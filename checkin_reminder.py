#!/usr/bin/env python3
"""Reminder to R State Rep nominees who have not completed the check-in form.

Recipients are the same audience as congrats_blast.py, minus anyone with a completed
check-in and minus the two Chris held back. The opening says it is a second ask rather than
congratulating them again; everything below it is the same email they already got.

Modes: list (preview), test (one to chris@), send.
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
import boto3
import psycopg2
from botocore.exceptions import ClientError

MODE = sys.argv[1] if len(sys.argv) > 1 else 'list'
SOURCE = '"Committee to Elect House Republicans" <info@electhouserepublicans.com>'
REPLYTO = ['chris@electhouserepublicans.com']
SUBJECT = 'Still need your check in for November'
FORM_URL = 'https://electhouserepublicans.com/checkin'
EXCLUDE_CIDS = {639, 1798}   # Sue DeLemus, Andy Dow: held back at Chris's direction

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

TEXT_TMPL = """Hi {first},

We sent this a few days ago and have not heard back from you yet. It takes about two minutes, and it is what tells us where to help you.

{form_url}

Log in with your email and it will remember what we already have on file, so most of it is a couple of clicks. It asks about:

{questions}

If anything is missing we will work on getting it to you. If it is easier, just reply to this email instead.

Best,

Committee to Elect House Republicans"""

HTML_TMPL = """<html><body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222;line-height:1.5">
<p>Hi {first},</p>
<p>We sent this a few days ago and have not heard back from you yet. It takes about two minutes, and it is what tells us where to help you.</p>
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


def greeting_name(first):
    parts = first.split()
    while len(parts) > 1 and re.fullmatch(r'[A-Z]\.?', parts[-1]):
        parts.pop()
    return ' '.join(parts)


def bodies(first):
    text_q = '\n'.join('{}. {}'.format(i, q) for i, q in enumerate(QUESTIONS, 1))
    html_q = '\n'.join('<li>{}</li>'.format(q) for q in QUESTIONS)
    return (TEXT_TMPL.format(first=first, questions=text_q, form_url=FORM_URL),
            HTML_TMPL.format(first=first, questions=html_q, form_url=FORM_URL))


conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute("""
    SELECT c.candidate_id, c.first_name, c.last_name,
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
    recips.append({'first': greeting_name(first), 'last': last, 'email': pick})

print('not yet completed: {}  sendable: {}  no email: {}  suppressed: {}  held back: {}'
      .format(len(rows), len(recips), len(missing), len(suppressed), len(excluded)))

ses = boto3.client('ses', region_name='us-east-1')


def send(to, first):
    text, html = bodies(first)
    return ses.send_email(
        Source=SOURCE, Destination={'ToAddresses': [to]}, ReplyToAddresses=REPLYTO,
        Message={'Subject': {'Data': SUBJECT, 'Charset': 'UTF-8'},
                 'Body': {'Text': {'Data': text, 'Charset': 'UTF-8'},
                          'Html': {'Data': html, 'Charset': 'UTF-8'}}})


if MODE == 'list':
    for r in recips[:12]:
        print(' ', r['first'], r['last'], '->', r['email'])
elif MODE == 'test':
    r = send('chris@electhouserepublicans.com', 'Chris')
    print('TEST sent, MessageId=', r['MessageId'])
elif MODE == 'send':
    ok, failed = 0, []
    for r in recips:
        try:
            send(r['email'], r['first'])
            ok += 1
        except ClientError as e:
            failed.append((r['email'], str(e)))
        time.sleep(0.06)
    print('SENT ok={} fail={}'.format(ok, len(failed)))
    for a, e in failed:
        print('  FAIL:', a, e[:140])
