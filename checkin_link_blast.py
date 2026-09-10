#!/usr/bin/env python3
"""One-click check-in links for nominees who have no password.

228 of the 326 R State Rep nominees have no password on their record, and not one of them has
completed the check in. The two emails we sent told them to log in; they had nothing to log in
with, and the link they could request delivered them to their profile hub rather than to the
form. This sends each of them a personal link that opens the check-in form already signed in.

The link is a portal_access token good for 7 days, pointing at /checkin, so there is no
password, no reset and no login screen. It is per-recipient, so it must go to the address on
that record and nowhere else.

Modes: list (preview, sends nothing), test (one to chris@), send.
Run from /opt/nh-candidate-recruitment so .env is found.
"""
import os
import re
import sys
import time


def load_env(path='/opt/nh-candidate-recruitment/.env'):
    for line in open(path):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
sys.path.insert(0, '/opt/nh-candidate-recruitment')
import boto3
import psycopg2
from botocore.exceptions import ClientError

MODE = sys.argv[1] if len(sys.argv) > 1 else 'list'
SOURCE = '"Committee to Elect House Republicans" <info@electhouserepublicans.com>'
REPLYTO = ['chris@electhouserepublicans.com']
SUBJECT = 'Your check in link - no password needed'
EXCLUDE_CIDS = {639, 1798}   # Sue DeLemus, Andy Dow: held back at Chris's direction

TEXT_TMPL = """Hi {first},

We have asked twice for your check in and have not heard back. That is our fault, not yours:
the form asked you to log in, and most candidates have never set a password.

So here is a link that just opens the form, already signed in. Nothing to remember, nothing
to reset.

{link}

It takes about two minutes and it is what tells us where to help you. It asks whether you
have signs, literature, a headshot, a website, a donation link and walk books, what you have
raised and hold, and what you expect to raise. Anything you are missing, we will work on
getting to you.

The link is yours alone and works for seven days. If it has expired by the time you get to
it, go to electhouserepublicans.com/checkin and put in your email, and it will send you a
fresh one.

Best,

Committee to Elect House Republicans"""

HTML_TMPL = """<html><body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222;line-height:1.55">
<p>Hi {first},</p>
<p>We have asked twice for your check in and have not heard back. That is our fault, not
yours: the form asked you to log in, and most candidates have never set a password.</p>
<p>So here is a link that just opens the form, already signed in. Nothing to remember,
nothing to reset.</p>
<p style="margin:26px 0">
  <a href="{link}" style="background:#c8102e;color:#fff;text-decoration:none;padding:14px 30px;border-radius:4px;display:inline-block;font-weight:bold;font-size:16px">Open My Check In</a>
</p>
<p>It takes about two minutes and it is what tells us where to help you. It asks whether you
have signs, literature, a headshot, a website, a donation link and walk books, what you have
raised and hold, and what you expect to raise. Anything you are missing, we will work on
getting to you.</p>
<p style="font-size:13px;color:#555">The link is yours alone and works for seven days. If it
has expired by the time you get to it, go to
<a href="https://electhouserepublicans.com/checkin" style="color:#c8102e">electhouserepublicans.com/checkin</a>
and put in your email, and it will send you a fresh one.</p>
<p>Best,<br>Committee to Elect House Republicans</p>
</body></html>"""


def greeting_name(first):
    parts = first.split()
    while len(parts) > 1 and re.fullmatch(r'[A-Z]\.?', parts[-1]):
        parts.pop()
    return ' '.join(parts)


conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute("""
    SELECT DISTINCT c.candidate_id, c.first_name, c.last_name,
           NULLIF(TRIM(COALESCE(c.email,'')),'')  AS email,
           NULLIF(TRIM(COALESCE(c.email1,'')),'') AS email1,
           NULLIF(TRIM(COALESCE(c.email2,'')),'') AS email2,
           c.dead_email, c.unsubscribed_email
    FROM filings f
    JOIN candidates c ON c.candidate_id = f.candidate_id
    WHERE f.election_year = 2026 AND f.party = 'R'
      AND f.office = 'State Representative'
      AND f.result <> 'lost'
      AND NOT EXISTS (SELECT 1 FROM candidate_campaign_progress p
                       WHERE p.candidate_id = c.candidate_id
                         AND p.intake_submitted_at IS NOT NULL)
    ORDER BY c.last_name, c.first_name
""")
rows = cur.fetchall()
conn.close()

# Tokens have to be minted inside the app so they are signed with the live secret.
os.chdir('/opt/nh-candidate-recruitment')
import app as _app                                    # noqa: E402
import candidate_portal as CP                         # noqa: E402

recips, missing, suppressed, excluded, seen = [], [], [], [], set()
for cid, first, last, *rest in rows:
    if cid in EXCLUDE_CIDS:
        excluded.append('{} {}'.format(first, last))
        continue
    emails = [e for e in rest[:3] if e]
    dead, unsub = rest[3], rest[4]
    pick = next((e for e in emails if not e.lower().endswith('gc.nh.gov')),
                emails[0] if emails else None)
    if not pick:
        missing.append('{} {}'.format(first, last))
        continue
    if (dead and pick.lower() in dead.lower()) or (unsub and pick.lower() in unsub.lower()):
        suppressed.append('{} {}'.format(first, last))
        continue
    if pick.lower() in seen:
        continue
    seen.add(pick.lower())
    recips.append({'cid': cid, 'first': greeting_name(first), 'last': last, 'email': pick})

print('not yet checked in: {}  sendable: {}  no email: {}  suppressed: {}  held back: {}'
      .format(len(rows), len(recips), len(missing), len(suppressed), len(excluded)))
if missing:
    print('  NO EMAIL ON FILE, will not receive this:')
    for m in missing:
        print('   ', m)

ses = boto3.client('ses', region_name='us-east-1')


def link_for(cid):
    with _app.app.app_context():
        tok = CP.make_token('portal_access', cid)
    return '{}/checkin?token={}'.format(CP.PORTAL_BASE, tok)


def send(to, first, cid):
    link = link_for(cid)
    return ses.send_email(
        Source=SOURCE, Destination={'ToAddresses': [to]}, ReplyToAddresses=REPLYTO,
        Message={'Subject': {'Data': SUBJECT, 'Charset': 'UTF-8'},
                 'Body': {'Text': {'Data': TEXT_TMPL.format(first=first, link=link), 'Charset': 'UTF-8'},
                          'Html': {'Data': HTML_TMPL.format(first=first, link=link), 'Charset': 'UTF-8'}}})


if MODE == 'list':
    for r in recips[:10]:
        print(' ', r['first'], r['last'], '->', r['email'])
    print('  ... and {} more'.format(max(0, len(recips) - 10)))
elif MODE == 'draft':
    r = recips[0]
    print('\n--- SUBJECT ---\n' + SUBJECT)
    print('\n--- TO (example) ---\n{} {} <{}>'.format(r['first'], r['last'], r['email']))
    print('\n--- TEXT ---\n' + TEXT_TMPL.format(first=r['first'],
                                                link=CP.PORTAL_BASE + '/checkin?token=<their own token>'))
elif MODE == 'test':
    # Chris's own link, so the whole path can be clicked before anyone else gets one
    r = send('chris@electhouserepublicans.com', 'Chris', recips[0]['cid'])
    print('TEST sent, MessageId=', r['MessageId'])
elif MODE == 'send':
    ok, failed = 0, []
    for r in recips:
        try:
            send(r['email'], r['first'], r['cid'])
            ok += 1
        except ClientError as e:
            failed.append((r['email'], str(e)))
        time.sleep(0.06)
    print('SENT ok={} fail={}'.format(ok, len(failed)))
    for a, e in failed:
        print('  FAIL:', a, e[:140])
