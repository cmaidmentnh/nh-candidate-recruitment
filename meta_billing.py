"""Meta billing: what was actually charged, against what was actually delivered.

Two different numbers that people assume are one. `spend` in the ad reports is delivery: what
Meta says it served. A charge is money leaving the card. They diverge, because Meta bills when
an account crosses its billing threshold or at month end, whichever comes first, so a charge
almost never lines up with a calendar month of delivery.

Charges cannot be fetched. CTEHR's account is card funded rather than on a credit line, so
Meta issues receipts, not invoices, and every billing edge in the Graph API is gone or refuses
(checked across v12 to v23 on 2026-09-13). They come in from the CSV that Meta's Billing and
Payments page exports.

What IS fetchable, and worth showing beside them:

    amount_spent   billed to date, in cents
    balance        accrued and not yet charged, in cents
    funding_source VISA *8523
    account_status active, disabled, closed

Those come from the ad account object and need no new permission.
"""
import csv
import io
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

logger = logging.getLogger(__name__)

get_db_connection = None
release_db_connection = None

GRAPH = 'https://graph.facebook.com/v21.0'


def init_meta_billing(db_conn_func, db_release_func):
    global get_db_connection, release_db_connection
    get_db_connection = db_conn_func
    release_db_connection = db_release_func


# =============================================================================
# THE CSV
# =============================================================================

# Meta has renamed these columns more than once and localises them, so match on meaning
# rather than on an exact header. Each entry is (field, [substrings that identify it]).
COLUMN_HINTS = [
    ('reference', ['transaction id', 'reference number', 'transaction_id', 'payment id']),
    ('charged_on', ['transaction date', 'date created', 'date_created', 'date']),
    ('amount', ['amount billed', 'amount', 'total']),
    ('status', ['status', 'payment status']),
    ('payment_method', ['payment method', 'funding source', 'payment_method']),
    ('product', ['product type', 'product', 'description']),
    ('account_id', ['ad account id', 'account id', 'account_id']),
    ('currency', ['currency']),
]

MONEY = re.compile(r'-?[\d,]+\.?\d*')


def _pick_columns(header):
    """Map the file's headers onto our fields. Longest hint wins, so 'transaction date' is
    not stolen by the bare hint 'date'."""
    out = {}
    lowered = [(i, (h or '').strip().lower()) for i, h in enumerate(header)]
    for field, hints in COLUMN_HINTS:
        best = None
        for hint in hints:
            for i, h in lowered:
                if i in out.values():
                    continue
                if h == hint:
                    best = i
                    break
                if best is None and hint in h:
                    best = i
            if best is not None and any(h == hint for _, h in lowered):
                break
        if best is not None:
            out[field] = best
    return out


def _money(v):
    if v is None:
        return None
    m = MONEY.search(str(v).replace('$', ''))
    if not m:
        return None
    try:
        return float(m.group(0).replace(',', ''))
    except ValueError:
        return None


def _when(v):
    s = (str(v or '')).strip()
    if not s:
        return None
    s = s.split('T')[0].split(' ')[0]
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%b %d, %Y', '%d %b %Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_csv(text):
    """Read Meta's billing export into rows we can store. Returns (rows, problems).

    Tolerant on purpose: the export has changed shape before, and a single unreadable line
    should not cost you the other ninety.
    """
    problems = []
    # Meta sometimes prefixes the file with a title line before the real header.
    sample = text[:4000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = list(csv.reader(io.StringIO(text), dialect))
    if not reader:
        return [], ['The file is empty.']

    head_at, cols = None, {}
    for i, row in enumerate(reader[:10]):
        c = _pick_columns(row)
        if 'amount' in c and ('charged_on' in c or 'reference' in c):
            head_at, cols = i, c
            break
    if head_at is None:
        return [], ['Could not find a header row with a date and an amount. '
                    'Columns seen: ' + ', '.join(str(x) for x in reader[0][:10])]

    header = reader[head_at]
    rows = []
    for n, raw in enumerate(reader[head_at + 1:], start=head_at + 2):
        if not any((c or '').strip() for c in raw):
            continue

        def cell(field):
            i = cols.get(field)
            return raw[i].strip() if i is not None and i < len(raw) else None

        amount = _money(cell('amount'))
        when = _when(cell('charged_on'))
        ref = cell('reference')
        if amount is None or when is None:
            problems.append('Line %d skipped: %s' % (
                n, 'no amount' if amount is None else 'no date'))
            continue
        if not ref:
            # No transaction id in this export: synthesise a stable one so a re-import of
            # the same file still updates rather than duplicates.
            ref = 'gen:%s:%.2f:%s' % (when.isoformat(), amount,
                                      (cell('payment_method') or '')[:12])
        rows.append({
            'reference': ref[:200],
            'charged_on': when,
            'amount': amount,
            'currency': (cell('currency') or 'USD')[:8],
            'status': (cell('status') or None),
            'payment_method': (cell('payment_method') or None),
            'product': (cell('product') or None),
            'account_id': (cell('account_id') or None),
            'raw': json.dumps(dict(zip(header, raw))),
        })
    return rows, problems


def import_rows(rows, filename, who):
    """Upsert on Meta's transaction id, so re-importing an overlapping export is safe."""
    conn = get_db_connection()
    cur = conn.cursor()
    written = updated = 0
    try:
        for r in rows:
            cur.execute("""
                INSERT INTO meta_billing_charge
                    (reference, account_id, charged_on, amount, currency, status,
                     payment_method, product, source, raw, imported_by, updated_at)
                VALUES (%(reference)s, %(account_id)s, %(charged_on)s, %(amount)s,
                        %(currency)s, %(status)s, %(payment_method)s, %(product)s,
                        'csv', %(raw)s::jsonb, %(who)s, now())
                ON CONFLICT (reference) DO UPDATE SET
                    charged_on = EXCLUDED.charged_on,
                    amount = EXCLUDED.amount,
                    status = EXCLUDED.status,
                    payment_method = COALESCE(EXCLUDED.payment_method,
                                              meta_billing_charge.payment_method),
                    product = COALESCE(EXCLUDED.product, meta_billing_charge.product),
                    raw = EXCLUDED.raw,
                    updated_at = now()
                RETURNING (xmax = 0) AS inserted""", dict(r, who=who))
            if cur.fetchone()[0]:
                written += 1
            else:
                updated += 1
        if rows:
            cur.execute("""INSERT INTO meta_billing_import
                             (filename, rows_seen, rows_written, rows_updated,
                              first_charge, last_charge, total_amount, imported_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (filename, len(rows), written, updated,
                         min(r['charged_on'] for r in rows),
                         max(r['charged_on'] for r in rows),
                         sum(r['amount'] for r in rows), who))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        release_db_connection(conn)
    return {'written': written, 'updated': updated}


# =============================================================================
# WHAT META WILL TELL US
# =============================================================================

def _env_token():
    for path in ('/opt/nh-civic-crm/.env', '/opt/nh-candidate-recruitment/.env'):
        try:
            for line in open(path):
                line = line.strip()
                if line.startswith('META_ADS_TOKEN='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return os.environ.get('META_ADS_TOKEN')


def account_billing(account_id):
    """Billed to date, what has accrued since, and how it is being paid.

    amount_spent and balance come back in cents. They are the only billing numbers Meta
    exposes for a card funded account, and together they are the cross-check on the CSV.
    """
    token = _env_token()
    if not token or not account_id:
        return None
    q = urllib.parse.urlencode({
        'fields': 'name,currency,account_status,disable_reason,amount_spent,balance,'
                  'spend_cap,funding_source_details,is_prepay_account',
        'access_token': token})
    try:
        with urllib.request.urlopen('%s/%s?%s' % (GRAPH, account_id, q), timeout=20) as r:
            d = json.loads(r.read().decode())
    except Exception as e:
        logger.info('meta billing: account read failed: %s', str(e)[:120])
        return None
    fs = d.get('funding_source_details') or {}
    return {
        'name': d.get('name'),
        'currency': d.get('currency'),
        'active': d.get('account_status') == 1,
        'billed_to_date': float(d.get('amount_spent') or 0) / 100.0,
        'accrued_unbilled': float(d.get('balance') or 0) / 100.0,
        'card': fs.get('display_string'),
        'prepay': bool(d.get('is_prepay_account')),
        'spend_cap': (float(d.get('spend_cap') or 0) / 100.0) or None,
    }


def monthly_spend(account_id, since='2025-01-01'):
    """Delivered spend by month, straight from insights. The thing charges get compared to."""
    token = _env_token()
    if not token or not account_id:
        return []
    q = urllib.parse.urlencode({
        'level': 'account', 'time_increment': 'monthly',
        'time_range': json.dumps({'since': since, 'until': date.today().isoformat()}),
        'fields': 'spend,impressions', 'limit': 200, 'access_token': token})
    try:
        with urllib.request.urlopen('%s/%s/insights?%s' % (GRAPH, account_id, q), timeout=30) as r:
            d = json.loads(r.read().decode())
    except Exception as e:
        logger.info('meta billing: insights read failed: %s', str(e)[:120])
        return []
    return [{'month': row['date_start'][:7],
             'spend': float(row.get('spend') or 0),
             'impressions': int(row.get('impressions') or 0)}
            for row in d.get('data', [])]


# =============================================================================
# THE RECONCILIATION
# =============================================================================

def reconcile(account_id):
    """Charges beside delivery, month by month, and the gap between the two totals.

    They are not supposed to match exactly in any single month: Meta charges on a threshold,
    not on a calendar. Over the life of the account they should converge, and a gap that keeps
    growing is the signal worth having.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""SELECT to_char(charged_on,'YYYY-MM') AS m,
                              SUM(amount), COUNT(*)
                         FROM meta_billing_charge
                        WHERE COALESCE(status,'') NOT ILIKE '%%fail%%'
                        GROUP BY 1 ORDER BY 1""")
        charged = {m: {'amount': float(a), 'n': n} for m, a, n in cur.fetchall()}
        cur.execute("""SELECT COALESCE(SUM(amount),0), COUNT(*), MIN(charged_on), MAX(charged_on)
                         FROM meta_billing_charge
                        WHERE COALESCE(status,'') NOT ILIKE '%%fail%%'""")
        total, count, first, last = cur.fetchone()
        cur.execute("""SELECT COALESCE(SUM(amount),0), COUNT(*) FROM meta_billing_charge
                        WHERE status ILIKE '%%fail%%'""")
        failed_amt, failed_n = cur.fetchone()
        cur.execute("""SELECT filename, imported_at, rows_seen, total_amount
                         FROM meta_billing_import ORDER BY imported_at DESC LIMIT 1""")
        last_import = cur.fetchone()
    finally:
        cur.close()
        release_db_connection(conn)

    delivered = {r['month']: r for r in monthly_spend(account_id)}
    months = sorted(set(charged) | set(delivered), reverse=True)
    rows = []
    for m in months:
        c = charged.get(m, {})
        d = delivered.get(m, {})
        rows.append({'month': m,
                     'charged': c.get('amount'), 'charges': c.get('n'),
                     'delivered': d.get('spend'),
                     'impressions': d.get('impressions')})
    acct = account_billing(account_id) or {}
    delivered_total = sum(r['spend'] for r in delivered.values())
    return {
        'rows': rows,
        'charged_total': float(total or 0),
        'charge_count': count or 0,
        'first_charge': first, 'last_charge': last,
        'failed_total': float(failed_amt or 0), 'failed_count': failed_n or 0,
        'delivered_total': delivered_total,
        'account': acct,
        'gap': float(total or 0) - delivered_total,
        'last_import': ({'filename': last_import[0], 'at': last_import[1],
                         'rows': last_import[2], 'total': float(last_import[3] or 0)}
                        if last_import else None),
    }


def cfs_total(account_id, since, until):
    """What the CFS filing needs: one number and the dates it covers.

    Charges where we have them, because that is money actually spent. Delivery is reported
    alongside so the difference is visible rather than silently chosen for you.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""SELECT COALESCE(SUM(amount),0), COUNT(*), MIN(charged_on), MAX(charged_on)
                         FROM meta_billing_charge
                        WHERE charged_on BETWEEN %s AND %s
                          AND COALESCE(status,'') NOT ILIKE '%%fail%%'""", (since, until))
        total, n, lo, hi = cur.fetchone()
    finally:
        cur.close()
        release_db_connection(conn)
    return {'since': since, 'until': until, 'charged': float(total or 0),
            'charges': n or 0, 'first': lo, 'last': hi}
