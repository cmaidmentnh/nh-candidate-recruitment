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
