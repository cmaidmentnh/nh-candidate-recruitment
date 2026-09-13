#!/usr/bin/env python3
"""Chase the palm card photos Joe Sweeney needs before print.

Two groups, one email each, worded differently because the ask is different:
  MISSING  - nothing on file anywhere. They print as a silhouette if they do not send one.
  POOR     - we hold a photo but it will not survive an oversized card at print resolution.

Joe's cutoff is Tuesday 15 September. Anyone who misses it goes to print with what we have.

Modes: list (preview, sends nothing), draft, test, send.
Run from /opt/nh-candidate-recruitment.
"""
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
SEND_TO = 'josephfsweeney@gmail.com'
DEADLINE = 'Tuesday, September 15'

# nothing on file in the picture-day galleries or the CRM
MISSING = [
    ('Erlon Jones','Carroll 2'), ('Matthew Santonastaso','Cheshire 13'),
    ('Eric Bourgeois','Strafford 7'), ('Kevin M. Nugent Jr','Belknap 4'),
    ('Sam Hoehn','Coos 1'), ('Amy Norris','Coos 1'),
    ('Mark Evans','Coos 5'), ('Peter Morency','Coos 5'),
    ('Kevin Ballantine','Hillsborough 12'), ('Bill Boyd','Hillsborough 12'),
    ('Kevin Southwick','Hillsborough 12'), ('Travis Corcoran','Hillsborough 28'),
    ('William J. Bieschke','Hillsborough 43'), ('Dale Girard','Sullivan 6'),
    ('Emily Sandblade','Sullivan 6'), ('Alvin B. See','Merrimack 26'),
    ('Jonathan Verbicky','Strafford 19'), ('Cameron Thompson','Belknap 8'),
    ('Stephen Boyd','Merrimack 10'), ('Thomas C. Walsh','Merrimack 10'),
    ('Mike Bordes','Belknap 5'), ('Timothy Minor','Belknap 5'),
    ('Glen Aldrich','Belknap 6'), ('Priscilla M. Bean','Belknap 6'),
    ('Brittany Ping','Hillsborough 22'),
    ('Andrew Prout','Hillsborough 13'), ('Bob Wherry','Hillsborough 13'),
    ('Cathy Kenny','Hillsborough 13'), ('Jordan Ulery','Hillsborough 13'),
    ('Rich Lascelles','Hillsborough 14'), ('Raymond C. Peeples Jr','Hillsborough 14'),
    ('Jason Hodgdon','Hillsborough 41'), ('Joseph Skinner','Hillsborough 41'),
    ('Lino M. Avellani','Carroll 4'), ('Jennifer Rhodes','Cheshire 17'),
    ('Richard R. Brown','Carroll 3'), ('Karel A. Crawford','Carroll 3'),
    ('Seth King','Coos 4'), ('Shuvom Ghose','Hillsborough 24'),
    ('Diane Rogers','Hillsborough 1'), ('Charles E. McMahon','Rockingham 17'),
    ('Julius F. Soti','Rockingham 35'),
    ('George C. Grant','Sullivan 5'),
    ('Brian K. Chirichiello','Rockingham 13'), ('Charles Foote','Rockingham 13'),
    ('Jodi Nelson','Rockingham 13'), ('John Potucek','Rockingham 13'),
    ('Richard Tripp','Rockingham 13'),
]
# we hold something, but Joe says it will not hold up on the card
POOR = [
    ('Terry Roy','Rockingham 31'), ('James Spillane','Rockingham 2'),
    ('Denise DeDe-Poulin','Strafford 6'), ('Rick Devoid','Merrimack 1'),
    ('Keith Erf','Hillsborough 28'), ('Lori Korzen','Coos 7'),
    ('Colleen M. Harkey','Belknap 6'),
]

SUBJ = 'Your palm card photo, needed by Tuesday'

BODY = """Hi {first},

Your palm cards are at the printer this week and we do not have a photo of you that will work.

{problem}

We need one by {deadline}. After that the cards go to print as they are, and yours would be the only one on the card without a proper picture of you.

Send it straight to Joe Sweeney at {joe}, with your name and district in the subject line.

What works: head and shoulders, facing the camera, plain or simple background. A phone photo is completely fine. The one thing that does not work is a screenshot or a picture saved off Facebook, because those come out too small to print. Send the original file.

It takes two minutes and it is the difference between a card that looks like a campaign and one that does not.

Thank you,

Committee to Elect House Republicans"""

P_MISSING = ("We have no photograph of you on file at all. As things stand your space on the "
             "card prints as a grey silhouette.")
P_POOR    = ("The photo we hold of you will not survive being printed at this size. It is "
             "either too small, cropped too tight, or not a head and shoulders shot.")

HTML = """<html><body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222;line-height:1.55">
<p>Hi {first},</p>
<p>Your palm cards are at the printer this week and we do not have a photo of you that will work.</p>
<p style="background:#fdf6f6;border-left:3px solid #c8102e;padding:9px 12px;margin:18px 0">{problem}</p>
<p><b>We need one by {deadline}.</b> After that the cards go to print as they are, and yours
would be the only one on the card without a proper picture of you.</p>
<p>Send it straight to Joe Sweeney at <a href="mailto:{joe}">{joe}</a>, with your name and
district in the subject line.</p>
<p><b>What works:</b> head and shoulders, facing the camera, plain or simple background. A phone
photo is completely fine. The one thing that does not work is a screenshot or a picture saved
off Facebook, because those come out too small to print. Send the original file.</p>
<p>It takes two minutes and it is the difference between a card that looks like a campaign and
one that does not.</p>
<p>Thank you,<br>Committee to Elect House Republicans</p>
</body></html>"""


def first_name(n):
    parts = n.split()
    out = parts[0]
    return out


conn = psycopg2.connect(os.environ['DATABASE_URL']); cur = conn.cursor()

def lookup(name, district):
    ln = name.split()[-1]
    if ln.lower() in ('jr','sr','ii','iii'):
        ln = name.split()[-2]
    cur.execute("""SELECT c.candidate_id, c.first_name, c.last_name,
                          NULLIF(TRIM(COALESCE(c.email,'')),''),
                          NULLIF(TRIM(COALESCE(c.email1,'')),''),
                          NULLIF(TRIM(COALESCE(c.email2,'')),''),
                          c.dead_email, c.unsubscribed_email
                     FROM candidates c
                     JOIN filings f ON f.candidate_id=c.candidate_id
                    WHERE f.election_year=2026 AND f.office='State Representative'
                      AND f.result<>'lost' AND lower(c.last_name)=lower(%s)
                      AND f.district_code=%s LIMIT 1""", (ln, district))
    return cur.fetchone()

rows, missing_email = [], []
for group, people, problem in (('missing', MISSING, P_MISSING), ('poor', POOR, P_POOR)):
    for name, dist in people:
        r = lookup(name, dist)
        if not r:
            missing_email.append((name, dist, 'no matching filing')); continue
        cid, fn, ln, e0, e1, e2, dead, unsub = r
        emails = [e for e in (e0, e1, e2) if e]
        pick = next((e for e in emails if not e.lower().endswith('gc.nh.gov')),
                    emails[0] if emails else None)
        if not pick:
            missing_email.append((name, dist, 'NO EMAIL ON FILE')); continue
        if (dead and pick.lower() in dead.lower()) or (unsub and pick.lower() in unsub.lower()):
            missing_email.append((name, dist, 'suppressed')); continue
        rows.append({'name': name, 'first': first_name(fn), 'dist': dist,
                     'email': pick, 'problem': problem})
conn.close()

print('sendable: %d   unreachable: %d' % (len(rows), len(missing_email)))
for m in missing_email:
    print('   CANNOT EMAIL: %-24s %-16s %s' % m)

ses = boto3.client('ses', region_name='us-east-1')

def send(to, r):
    kw = dict(first=r['first'], problem=r['problem'], deadline=DEADLINE, joe=SEND_TO)
    return ses.send_email(
        Source=SOURCE, Destination={'ToAddresses': [to]}, ReplyToAddresses=REPLYTO,
        Message={'Subject': {'Data': SUBJ, 'Charset': 'UTF-8'},
                 'Body': {'Text': {'Data': BODY.format(**kw), 'Charset': 'UTF-8'},
                          'Html': {'Data': HTML.format(**kw), 'Charset': 'UTF-8'}}})

if MODE == 'list':
    for r in rows:
        print('   %-24s %-16s %s' % (r['name'], r['dist'], r['email']))
elif MODE == 'draft':
    r = rows[0]
    print('\n--- SUBJECT ---\n' + SUBJ)
    print('\n--- NOTHING ON FILE ---\n' + BODY.format(first=r['first'], problem=P_MISSING,
                                                      deadline=DEADLINE, joe=SEND_TO))
    print('\n--- POOR PHOTO variant paragraph ---\n' + P_POOR)
elif MODE == 'test':
    send('chris@electhouserepublicans.com',
         {'first':'Chris','problem':P_MISSING,'dist':'test'})
    print('test sent')
elif MODE == 'send':
    ok, fail = 0, []
    for r in rows:
        try:
            send(r['email'], r); ok += 1
        except ClientError as e:
            fail.append((r['email'], str(e)[:120]))
        time.sleep(0.06)
    print('SENT ok=%d fail=%d' % (ok, len(fail)))
    for f in fail: print('   FAIL:', f)
