"""Bank reconciliation for the spend plan.

Chris copies the TD Bank account screen (Ctrl-A, Ctrl-C) each morning and pastes it into the
spend plan. This module turns that text into transactions, sorts each one into a spend
category, and reconciles what the bank says we paid each vendor against what the vendor has
billed or delivered.

Two rules make the numbers trustworthy:

* Posted rows are permanent and deduplicated on (date, description, amount, running balance).
  The running balance is what separates two identical $10.46 Cloudflare charges on one day.
* The pending list is a snapshot, so every import replaces all pending rows. A pending charge
  that later posts shows up again as a posted row with its own description, and the stale
  pending copy is already gone.
"""
import re
from datetime import date

# Spending before the general election program started (the primary) is not this plan's.
PROGRAM_START = date(2026, 9, 9)

MONEY_RE = re.compile(r'^-?\$[\d,]+\.\d\d$')
DATE_ONLY_RE = re.compile(r'^(\d{1,2})/(\d{1,2})/(\d{4})$')
DATE_KIND_RE = re.compile(r'^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\S.*)$')

# Category, then the patterns that put a transaction in it. First match wins, so the order
# matters where descriptions overlap (GOOGLE ADS before GOOGLE).
RULES = [
    ('income',     r'^\+|DEPOSIT|WINRED|STRIPE TRANSFER|ANEDOT|WIRE TRANSFER INCOMING|KALSHI'),
    ('mail',       r'NH REPUBLICAN STATE COMMITTEE|NHGOP'),
    ('meta',       r'FACEBK|FACEBOOK|META PLATFORMS'),
    ('stackadapt', r'STACKADAPT'),
    ('printing',   r'SPECTRUM MARKETI|SPECTRUM MARKETING'),
    ('texting',    r'REVT|TEXTING MANAGER'),
    ('google_ads', r'GOOGLE ADS'),
    ('consulting', r'1772 STRATEGIES'),
    ('fees',       r'WIRE TRANSFER FEE|SERVICE CHARGE|\bFEE\b'),
    ('operations', r'CLOUDFLARE|OPENAI|INTUIT|QBOOKS|GOOGLE WORKSPACE|X CORP|DECISION DESK|'
                   r'UPS STORE|ANTHROPIC|AMAZON WEB|AWS|GITHUB|ZOOM'),
]
CATEGORIES = ['mail', 'meta', 'stackadapt', 'printing', 'texting', 'google_ads', 'data', 'video',
              'consulting', 'operations', 'fees', 'other', 'income', 'transfer']
OVERHEAD = ('consulting', 'operations', 'fees')


def _amount(s):
    return float(s.replace('$', '').replace(',', ''))


def _date(m):
    return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))


def categorize(description, amount):
    if amount > 0:
        return 'income'
    up = description.upper()
    for cat, pat in RULES[1:]:
        if re.search(pat, up):
            return cat
    return None                              # unexplained: flagged until someone labels it


def parse_td(text):
    """Parse a pasted TD Bank account screen. Returns (balances, rows)."""
    lines = [l.strip() for l in text.replace('\r', '').split('\n')]
    lines = [l for l in lines if l]

    def after(label):
        for i, l in enumerate(lines):
            if l.lower() == label.lower() and i + 1 < len(lines) and MONEY_RE.match(lines[i + 1]):
                return _amount(lines[i + 1])
        return None

    balances = {'available': after('Available Balance'),
                'beginning': after("Today's Beginning Balance"),
                'pending': after('Pending')}

    def section(start, stop):
        try:
            a = next(i for i, l in enumerate(lines) if l.upper().startswith(start))
        except StopIteration:
            return []
        b = next((i for i in range(a + 1, len(lines)) if any(lines[i].upper().startswith(s) for s in stop)),
                 len(lines))
        return lines[a + 1:b]

    rows = []
    # Pending: a date alone on a line, type lines, the description, the amount, "Pending".
    pend = section('PENDING TRANSACTIONS', ('ACCOUNT HISTORY',))
    i = 0
    while i < len(pend):
        m = DATE_ONLY_RE.match(pend[i])
        if not m:
            i += 1; continue
        j = i + 1
        block = []
        while j < len(pend) and not DATE_ONLY_RE.match(pend[j]):
            block.append(pend[j]); j += 1
        amt_idx = next((k for k, l in enumerate(block) if MONEY_RE.match(l)), None)
        if amt_idx is not None and amt_idx > 0:
            rows.append({'date': _date(m), 'pending': True,
                         'kind': ' / '.join(block[:amt_idx - 1]) or None,
                         'description': block[amt_idx - 1],
                         'amount': _amount(block[amt_idx]), 'balance': None})
        i = j

    # Posted: "date  TYPE" on one line, then sub-type and description lines, amount, balance.
    hist = section('ACCOUNT HISTORY', ('VIEW MORE TRANSACTIONS', 'INFO & RELATED'))
    i = 0
    while i < len(hist):
        m = DATE_KIND_RE.match(hist[i])
        if not m:
            i += 1; continue
        j = i + 1
        block = []
        while j < len(hist) and not DATE_KIND_RE.match(hist[j]):
            block.append(hist[j]); j += 1
        money = [k for k, l in enumerate(block) if MONEY_RE.match(l)]
        if money:
            text_lines = block[:money[0]]
            # The sub-type line ("VISA DDA PUR AP", "WIRE TRANSFER OUTGOING") names the channel;
            # the payee is the last text line. A bare "DEPOSIT" has only the one line.
            desc = text_lines[-1] if text_lines else m.group(4).strip()
            kind = m.group(4).strip() + (' / ' + ' / '.join(text_lines[:-1]) if len(text_lines) > 1 else '')
            if len(text_lines) == 1 and text_lines[0].upper() in ('DEPOSIT', 'WIRE TRANSFER FEE',
                                                                 'SERVICE CHARGE'):
                kind = m.group(4).strip()
            rows.append({'date': _date(m), 'pending': False, 'kind': kind,
                         'description': ' '.join(text_lines) if text_lines else desc,
                         'amount': _amount(block[money[0]]),
                         'balance': _amount(block[money[1]]) if len(money) > 1 else None})
        i = j
    for r in rows:
        r['category'] = categorize(r['description'], r['amount'])
        r['fingerprint'] = '|'.join([
            'P' if r['pending'] else 'H', r['date'].isoformat(), r['description'][:200],
            '%.2f' % r['amount'], '' if r['balance'] is None else '%.2f' % r['balance']])
    return balances, rows


def import_rows(cur, balances, rows, who):
    """Write a parsed paste. Returns how many posted rows were new. The caller commits."""
    cur.execute("DELETE FROM bank_txn WHERE pending")          # the pending list is a snapshot
    added = 0
    for r in rows:
        cur.execute("""INSERT INTO bank_txn (txn_date, pending, kind, description, amount, balance,
                                             category, fingerprint)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (fingerprint) DO NOTHING""",
                    (r['date'], r['pending'], r['kind'], r['description'], r['amount'],
                     r['balance'], r['category'], r['fingerprint']))
        if not r['pending']:
            added += cur.rowcount
    cur.execute("""INSERT INTO bank_snapshot (available, beginning, pending, captured_by)
                   VALUES (%s,%s,%s,%s)""",
                (balances['available'], balances['beginning'], balances['pending'], who))
    return added


def match_payables(cur):
    """Settle unpaid bills against bank payments, and keep settled ones pointed at the row
    that paid them. Returns a list of plain-English results for the page.

    A bill matches a payment when, in this order of preference:
      1. its payee words appear in exactly one unclaimed payment, or
      2. exactly one unclaimed payment is for exactly its amount, or
      3. it is marked approximate and exactly one unclaimed, unlabeled-or-same-category
         payment is within 15% of it.
    Two or more candidates is never guessed at: it is reported for a person to decide.
    Payments are only considered from three days before the bill was entered onward."""
    out = []
    cur.execute("SELECT bank_fp FROM spend_payable WHERE bank_fp IS NOT NULL")
    claimed = {r[0] for r in cur.fetchall()}

    # A bill settled by a pending charge loses its link when that charge posts under a new
    # description. Find the posted row and move the link (and the category) onto it.
    cur.execute("""SELECT id, category, amount, paid_at, bank_fp FROM spend_payable
                    WHERE paid AND bank_fp LIKE 'P|%%'""")
    for pid, cat, amt, paid_at, fp in cur.fetchall():
        cur.execute("SELECT 1 FROM bank_txn WHERE fingerprint=%s", (fp,))
        if cur.fetchone():
            continue
        cur.execute("""SELECT fingerprint, id FROM bank_txn
                        WHERE NOT pending AND amount = %s AND txn_date BETWEEN %s - 3 AND %s + 10
                          AND (category IS NULL OR category = %s)""",
                    (-float(amt), paid_at, paid_at, cat))
        c = [r for r in cur.fetchall() if r[0] not in claimed]
        if len(c) == 1:
            cur.execute("UPDATE spend_payable SET bank_fp=%s WHERE id=%s", (c[0][0], pid))
            cur.execute("UPDATE bank_txn SET category=%s, category_set_by='matched' WHERE id=%s", (cat, c[0][1]))
            claimed.add(c[0][0])

    cur.execute("""SELECT id, label, category, amount, approx, match_hint, created_at
                     FROM spend_payable WHERE NOT paid ORDER BY created_at""")
    for pid, label, cat, amt, approx, hint, created in cur.fetchall():
        amt = float(amt)
        cur.execute("""SELECT id, fingerprint, txn_date, description, amount, category, pending
                         FROM bank_txn WHERE amount < 0 AND txn_date >= %s::date - 3
                          AND (category IS NULL OR category = %s OR category = 'other')""",
                    (created, cat))
        cands = [r for r in cur.fetchall() if r[1] not in claimed]
        pick, how = None, ''
        if hint:
            words = [w for w in re.split(r'[\s,]+', hint.upper()) if len(w) > 2]
            hinted = [r for r in cands if any(w in r[3].upper() for w in words)]
            if len(hinted) == 1:
                pick, how = hinted[0], 'payee'
            elif len(hinted) > 1:
                out.append('%s: %d payments mention %s; which one?' % (label, len(hinted), hint))
                continue
        if not pick:
            exact = [r for r in cands if abs(-float(r[4]) - amt) < 0.005]
            if len(exact) == 1:
                pick, how = exact[0], 'exact amount'
            elif len(exact) > 1:
                out.append('%s: %d payments of exactly $%s; which one?' % (label, len(exact), '{:,.2f}'.format(amt)))
                continue
        if not pick and approx:
            near = [r for r in cands if abs(-float(r[4]) - amt) <= 0.15 * amt]
            if len(near) == 1:
                pick, how = near[0], 'close amount'
            elif len(near) > 1:
                out.append('%s: %d payments near $%s; which one?' % (label, len(near), '{:,.0f}'.format(amt)))
                continue
        if not pick:
            continue
        tid, fp, tdate, desc, tamt, tcat, pend = pick
        paid = -float(tamt)
        note = 'matched by %s to %s %s $%s' % (how, tdate.isoformat(), desc[:60], '{:,.2f}'.format(paid))
        if abs(paid - amt) >= 0.005:
            note += ' (bill said $%s)' % '{:,.2f}'.format(amt)
        cur.execute("""UPDATE spend_payable SET paid=true, paid_at=%s, bank_fp=%s, match_note=%s, amount=%s
                        WHERE id=%s""", (tdate, fp, note, paid, pid))
        cur.execute("UPDATE bank_txn SET category=%s, category_set_by='matched' WHERE id=%s", (cat, tid))
        claimed.add(fp)
        out.append('%s: paid. %s' % (label, note))
    return out


def check_balances(balances, rows):
    """The paste has to add up before it is trusted: pending rows sum to the pending total,
    and each posted balance is the next older balance plus this row's amount."""
    problems = []
    pend = round(sum(r['amount'] for r in rows if r['pending']), 2)
    if balances.get('pending') is not None and abs(pend - balances['pending']) > 0.005:
        problems.append('pending rows add to %.2f, the bank says %.2f' % (pend, balances['pending']))
    posted = [r for r in rows if not r['pending'] and r['balance'] is not None]
    for newer, older in zip(posted, posted[1:]):
        if abs(round(older['balance'] + newer['amount'], 2) - newer['balance']) > 0.005:
            problems.append('running balance breaks at %s %s' % (newer['date'], newer['description'][:40]))
    if balances.get('available') is not None and balances.get('beginning') is not None \
            and balances.get('pending') is not None:
        if abs(round(balances['beginning'] + balances['pending'], 2) - balances['available']) > 0.005:
            problems.append('beginning + pending does not equal available')
    return problems
