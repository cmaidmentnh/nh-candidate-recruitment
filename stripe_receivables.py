"""CTEHR Stripe money, kept on the tracker as expected income without anyone typing it in.

Gifts land in Stripe days before they reach TD, so for a while the bank balance does not hold
them and the surplus reads low. This keeps two kinds of spend_receivable row in step with
Stripe, run from cron:

  * ONE "held at Stripe" row (created_by 'stripe-sync-held'): available + pending balance,
    which is already net of fees, plus bank (ACH) payments still processing. Those are not in
    the balance until they settle, and they are the ones that got missed by hand (McMahon,
    Dow, Higginbottom, 25 Oct). ACH net is estimated at 0.8% capped at $5. The row is
    rewritten every run and never matched to a deposit: when Stripe pays out, the money moves
    out of the balance and into a payout row below.
  * ONE row per payout (created_by 'stripe-sync', label carries the po_ id) that has left
    Stripe but no TD import shows yet. Exact amount, so bank_recon.match_receivables settles
    it against the STRIPE TRANSFER deposit on the next TD paste.

Run: venv/bin/python stripe_receivables.py [--dry]
"""
import os
import sys
import time
from datetime import date

import psycopg2
import requests

HELD = 'stripe-sync-held'
PAYOUT = 'stripe-sync'
LOOKBACK_DAYS = 21


def _env(name):
    v = os.environ.get(name)
    if v:
        return v
    with open('/opt/nh-candidate-recruitment/.env') as f:
        for line in f:
            if line.startswith(name + '='):
                return line.split('=', 1)[1].strip().strip('"\'')
    raise SystemExit('%s not set' % name)


def _stripe_key():
    # The CTEHR Stripe key lives in the CRM's settings table (org 1), not in an env file.
    with psycopg2.connect(_env('CRM_DATABASE_URL')) as c, c.cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE org_id=1 AND key='stripe_secret_key'")
        return cur.fetchone()[0]


def _get(key, path, **params):
    r = requests.get('https://api.stripe.com/v1/' + path, params=params, auth=(key, ''), timeout=30)
    r.raise_for_status()
    return r.json()


def _all(key, path, **params):
    params.setdefault('limit', 100)
    while True:
        page = _get(key, path, **params)
        yield from page['data']
        if not page.get('has_more'):
            return
        params['starting_after'] = page['data'][-1]['id']


def _ach_net(cents):
    return (cents - min(round(cents * 0.008), 500)) / 100


def main(dry=False):
    key = _stripe_key()
    since = int(time.time()) - LOOKBACK_DAYS * 86400

    bal = _get(key, 'balance')
    held = sum(b['amount'] for b in bal['available'] + bal['pending'] if b['currency'] == 'usd') / 100
    processing = [p for p in _all(key, 'payment_intents', **{'created[gte]': since})
                  if p['status'] == 'processing']
    proc_net = sum(_ach_net(p['amount']) for p in processing)
    payouts = [p for p in _all(key, 'payouts', **{'created[gte]': since})
               if p['status'] in ('pending', 'in_transit', 'paid')]

    total = round(held + proc_net, 2)
    label = 'Stripe, not yet paid out: balance $%s' % '{:,.2f}'.format(held)
    if processing:
        names = ', '.join(sorted({(p.get('description') or '').split(' - ')[0].strip().title()
                                  for p in processing}))
        label += ' + %d bank payments processing (%s)' % (len(processing), names)
    label = label[:157] + '...' if len(label) > 160 else label

    conn = psycopg2.connect(_env('DATABASE_URL'))
    cur = conn.cursor()
    log = []

    cur.execute("SELECT id, amount, active FROM spend_receivable WHERE created_by=%s", (HELD,))
    row = cur.fetchone()
    if row is None:
        cur.execute("""INSERT INTO spend_receivable (label, amount, approx, created_by, match_note)
                       VALUES (%s, %s, %s, %s, 'kept in step with Stripe by stripe_receivables.py')""",
                    (label, total, bool(processing), HELD))
        log.append('held row created: $%.2f' % total)
    else:
        if not row[2]:
            log.append('held row is switched off on the tracker; left alone')
        elif abs(float(row[1]) - total) >= 0.005:
            log.append('held row $%.2f -> $%.2f' % (float(row[1]), total))
        cur.execute("UPDATE spend_receivable SET label=%s, amount=%s, approx=%s WHERE id=%s",
                    (label, total, bool(processing), row[0]))

    for p in payouts:
        amt = p['amount'] / 100
        arrive = date.fromtimestamp(p['arrival_date'])
        cur.execute("SELECT 1 FROM spend_receivable WHERE label LIKE %s", ('%' + p['id'] + '%',))
        if cur.fetchone():
            continue
        # Already in an imported TD deposit (or a pending line of one): the bank balance holds it.
        cur.execute("""SELECT 1 FROM bank_txn WHERE description ILIKE '%%STRIPE%%'
                         AND abs(amount - %s) < 0.005 AND txn_date >= %s::date - 3""", (amt, arrive))
        if cur.fetchone():
            continue
        cur.execute("""INSERT INTO spend_receivable (label, amount, expected_on, approx, created_by, match_note)
                       VALUES (%s, %s, %s, false, %s, 'added by stripe_receivables.py')""",
                    ('Stripe payout $%s arriving %s (%s)' % ('{:,.2f}'.format(amt), arrive.strftime('%-m/%-d'), p['id']),
                     amt, arrive, PAYOUT))
        log.append('payout %s $%.2f arriving %s added' % (p['id'], amt, arrive))

    if dry:
        conn.rollback()
        log.insert(0, 'DRY RUN, nothing saved. held $%.2f + ACH processing $%.2f = $%.2f; %d recent payouts'
                   % (held, proc_net, total, len(payouts)))
    else:
        conn.commit()
    conn.close()
    for line in log:
        print('%s %s' % (time.strftime('%Y-%m-%d %H:%M'), line))


if __name__ == '__main__':
    main(dry='--dry' in sys.argv)
