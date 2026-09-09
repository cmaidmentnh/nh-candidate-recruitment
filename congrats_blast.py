#!/usr/bin/env python3
"""Post-primary congratulations and general-election intake for R State Rep nominees.

Audience: filings 2026 / R / State Representative where result <> 'lost', i.e. everyone
on the November ballot, contested primary or not.

Modes: list (preview recipients), test (one to chris@), send (full blast).
Run from /opt/nh-candidate-recruitment so .env is found."""
import os, re, sys, time

def load_env(path='/opt/nh-candidate-recruitment/.env'):
    for line in open(path):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_env()
import boto3, psycopg2
from botocore.exceptions import ClientError

MODE = sys.argv[1] if len(sys.argv) > 1 else 'list'
SOURCE = '"Committee to Elect House Republicans" <info@electhouserepublicans.com>'
REPLYTO = ['chris@electhouserepublicans.com']
SUBJECT = 'Congratulations, and what we need from you for November'

FORM_URL = 'https://electhouserepublicans.com/checkin'

# Held back at Chris's direction 2026-09-09. Both Strafford 1.
EXCLUDE_CIDS = {639, 1798}   # Sue DeLemus, Andy Dow

QUESTIONS = [
    'Yard signs, and roughly how many',
    'Literature: a palm card or walk piece',
    'A professional headshot',
    'Your campaign website',
    'Your donation link',
    'Walk books for your district',
    'Money raised so far',
    'Cash on hand',
    'How much more you anticipate raising',
    'Your Facebook page',
]

def greeting_name(first):
    parts = first.split()
    while len(parts) > 1 and re.fullmatch(r'[A-Z]\.?', parts[-1]):
        parts.pop()
    return ' '.join(parts)

TEXT_TMPL = """Hi {first},

Congratulations on winning your primary. You are the Republican nominee in {district}.

The general election is the whole ballgame now, and CTEHR is building its plan around what you already have and what you still need. Please take two minutes to fill this out:

{form_url}

Log in with your email and it will remember what we already have on file, so most of it is a couple of clicks. It asks about:

{questions}

If anything is missing we will work on getting it to you. If it is easier, just reply to this email instead.

Best,

Committee to Elect House Republicans"""

HTML_TMPL = """<html><body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222;line-height:1.5">
<p>Hi {first},</p>
<p>Congratulations on winning your primary. You are the Republican nominee in <strong>{district}</strong>.</p>
<p>The general election is the whole ballgame now, and CTEHR is building its plan around what you already have and what you still need. Please take two minutes to fill this out:</p>
<p style="margin:24px 0">
  <a href="{form_url}" style="background:#c8102e;color:#fff;text-decoration:none;padding:12px 26px;border-radius:4px;display:inline-block;font-weight:bold">Complete My Check In</a>
</p>
<p>Log in with your email and it will remember what we already have on file, so most of it is a couple of clicks. It asks about:</p>
<ul style="padding-left:20px">
{questions}
</ul>
<p>If anything is missing we will work on getting it to you. If it is easier, just reply to this email instead.</p>
<p>Best,<br>Committee to Elect House Republicans</p>
</body></html>"""

def bodies(first, district):
    text_q = '\n'.join(f'{i}. {q}' for i, q in enumerate(QUESTIONS, 1))
    html_q = '\n'.join(f'<li>{q}</li>' for q in QUESTIONS)
    return (TEXT_TMPL.format(first=first, district=district, questions=text_q, form_url=FORM_URL),
            HTML_TMPL.format(first=first, district=district, questions=html_q, form_url=FORM_URL))

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute("""
    SELECT c.candidate_id, c.first_name, c.last_name, f.district_code,
           NULLIF(TRIM(COALESCE(c.email,'')),'')  AS email,
           NULLIF(TRIM(COALESCE(c.email1,'')),'') AS email1,
           NULLIF(TRIM(COALESCE(c.email2,'')),'') AS email2,
           c.dead_email, c.unsubscribed_email
    FROM filings f
    JOIN candidates c ON c.candidate_id = f.candidate_id
    WHERE f.election_year = 2026 AND f.party = 'R'
      AND f.office = 'State Representative'
      AND f.result <> 'lost'
    ORDER BY c.last_name, c.first_name
""")
rows = cur.fetchall()
conn.close()

recips, missing, suppressed, excluded, seen = [], [], [], [], set()
for cid, first, last, district, *rest in rows:
    if cid in EXCLUDE_CIDS:
        excluded.append(f'{first} {last} (cid {cid})')
        continue
    emails = [e for e in rest[:3] if e]
    dead, unsub = rest[3], rest[4]
    pick = next((e for e in emails if not e.lower().endswith('gc.nh.gov')),
                emails[0] if emails else None)
    if not pick:
        missing.append(f'{first} {last} (cid {cid})')
        continue
    if (dead and pick.lower() in dead.lower()) or (unsub and pick.lower() in unsub.lower()):
        suppressed.append(f'{first} {last}')
        continue
    if pick.lower() in seen:
        continue
    seen.add(pick.lower())
    recips.append({'cid': cid, 'first': greeting_name(first), 'last': last,
                   'district': district or 'your district', 'email': pick})

gov = [r for r in recips if r['email'].lower().endswith('gc.nh.gov')]
print(f'nominees still running: {len(rows)}  sendable: {len(recips)}  '
      f'no email: {len(missing)}  suppressed: {len(suppressed)}  '
      f'held back: {len(excluded)}  on gc.nh.gov: {len(gov)}')
for x in excluded:
    print('  HELD BACK:', x)

ses = boto3.client('ses', region_name='us-east-1')

def send(to, first, district):
    text, html = bodies(first, district)
    return ses.send_email(
        Source=SOURCE, Destination={'ToAddresses': [to]}, ReplyToAddresses=REPLYTO,
        Message={'Subject': {'Data': SUBJECT, 'Charset': 'UTF-8'},
                 'Body': {'Text': {'Data': text, 'Charset': 'UTF-8'},
                          'Html': {'Data': html, 'Charset': 'UTF-8'}}})

if MODE == 'list':
    for r in recips[:15]:
        print(' ', r['first'], r['last'], '|', r['district'], '->', r['email'])
    for m in missing:
        print('  MISSING:', m)
elif MODE == 'test':
    r = send('chris@electhouserepublicans.com', 'Chris', 'Hillsborough 13')
    print('TEST sent to chris@electhouserepublicans.com MessageId=', r['MessageId'])
elif MODE == 'send':
    ok, failed = 0, []
    for r in recips:
        try:
            send(r['email'], r['first'], r['district'])
            ok += 1
        except ClientError as e:
            failed.append((r['email'], str(e)))
        time.sleep(0.06)
    print(f'SENT ok={ok} fail={len(failed)}')
    for a, e in failed:
        print('  FAIL:', a, e[:140])
