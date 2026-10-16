#!/usr/bin/env python3
"""Final-two-weeks GOTV push: each R House nominee gets their district's list of strong
Republicans who vote in presidential years but skip midterms, with Jason's ask.

  python3 send_gotv.py --run runs/2026-10-16                       # dry run (counts only)
  python3 send_gotv.py --run runs/2026-10-16 --draft-to chris@maidmentnh.com
  python3 send_gotv.py --run runs/2026-10-16 --send                # live, resumable via log

Lists come from build_lists_remote.py (run on the voter-file server).
"""
import argparse, csv, json, os, sys, time, html as H
import boto3
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

ORG = "Committee to Elect House Republicans"
SRC = "info@electhouserepublicans.com"
SENDER = f'"Jason Osborne, CTEHR" <{SRC}>'


def copy(first, dist, n, both, phones):
    paras = [
        f"{first},",
        "We are two weeks out, and the pieces are on the board. The mail is going out, the ads are running, "
        "the door hangers are printed, and everything we can do from Concord is in motion. What is left is the "
        "one thing nobody can do for you: getting your voters to show up on November 3rd.",
        f"Attached is your list for {dist}. These are {n:,} strong Republicans in your district who vote for us in "
        f"presidential years and then sit out the midterms. {both:,} of them skipped both 2018 and 2022. They are "
        "not undecided. They are our voters, and the only question is whether they remember to vote this year. "
        "A knock on the door from the candidate is the surest way I know to make sure they do.",
        "Here is the ask. Find 100 of them and get them to vote. Knock their doors, introduce yourself, tell them "
        "the date, and ask them straight out whether they will be there. If they won't be home on the 3rd, help "
        "them get an absentee ballot. In a close district, 100 voters who would otherwise have stayed home is "
        "the margin.",
        f"The list is sorted with the people who skipped both midterms at the top, then by town and street so you "
        f"can walk it. {phones:,} of them have a phone number for the ones you can't catch at the door. Anyone "
        "who has already returned an absentee ballot has been taken off.",
        "Please keep the list secure and use it only for your campaign.",
    ]
    sign = ["Live Free or Die,", "Jason Osborne", "Chairman, Committee to Elect House Republicans"]
    text = "\n\n".join(paras) + "\n\n" + "\n".join(sign) + "\n"
    ph = "".join(f'<p style="margin:0 0 16px 0;">{H.escape(p)}</p>' for p in paras)
    sg = "<br>".join(H.escape(s) for s in sign)
    html = (f'<div style="font-family:Georgia,serif;font-size:16px;line-height:1.5;color:#111;max-width:620px;">'
            f'{ph}<p style="margin:0;">{sg}</p></div>')
    return html, text


def stats(fn):
    rows = list(csv.DictReader(open(fn)))
    return len(rows), sum(1 for r in rows if r['Priority'] == 'Skipped both midterms'), sum(1 for r in rows if r['Phone'])


def compose(c, rundir, subject_prefix=""):
    dist = c['district']
    fn = f"{rundir}/lists/{dist.replace(' ', '_')}_GOTV.csv"
    n, both, phones = stats(fn)
    html, text = copy(c['first'], dist, n, both, phones)
    m = MIMEMultipart('mixed')
    m['Subject'] = subject_prefix + f"Your 100 votes in {dist}"
    m['From'] = SENDER
    m['Reply-To'] = SRC
    alt = MIMEMultipart('alternative'); alt.attach(MIMEText(text, 'plain')); alt.attach(MIMEText(html, 'html')); m.attach(alt)
    att = MIMEApplication(open(fn, 'rb').read(), _subtype='csv')
    att.add_header('Content-Disposition', 'attachment', filename=os.path.basename(fn))
    m.attach(att)
    return m, n


def ses():
    return boto3.client('ses', region_name='us-east-1')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True); ap.add_argument('--send', action='store_true'); ap.add_argument('--draft-to')
    a = ap.parse_args()
    rundir = a.run if os.path.isabs(a.run) else os.path.join(os.path.dirname(os.path.abspath(__file__)), a.run)
    C = json.load(open(f"{rundir}/r_house_candidates.json"))
    plan, skip = [], {}
    for c in C:
        fn = f"{rundir}/lists/{c['district'].replace(' ', '_')}_GOTV.csv"
        why = None if os.path.exists(fn) and stats(fn)[0] else 'no list'
        why = why or (None if c['to'] else 'no email') or (None if not c['dead'] else 'dead email') or (None if not c['unsub'] else 'unsubscribed')
        if why: skip[why] = skip.get(why, 0) + 1
        else: plan.append(c)
    print(f"candidates {len(C)}  will send {len(plan)}  skipped {skip}")
    if a.draft_to:
        c = max(plan, key=lambda c: stats(f"{rundir}/lists/{c['district'].replace(' ', '_')}_GOTV.csv")[0])
        m, n = compose(c, rundir, "[DRAFT - not sent to candidates] ")
        m['To'] = a.draft_to
        r = ses().send_raw_email(Source=SRC, Destinations=[a.draft_to], RawMessage={"Data": m.as_string()})
        print("DRAFT SENT", c['first'], c['last'], c['district'], n, r['MessageId']); return
    if not a.send:
        return
    logf = f"{rundir}/gotv_blast_log.jsonl"
    done = {json.loads(l)['cid'] for l in open(logf)} if os.path.exists(logf) else set()
    client = ses()
    with open(logf, 'a') as lg:
        for c in plan:
            if c['cid'] in done: continue
            m, n = compose(c, rundir); m['To'] = c['to']
            try:
                r = client.send_raw_email(Source=SRC, Destinations=[c['to']], RawMessage={"Data": m.as_string()})
                lg.write(json.dumps({"cid": c['cid'], "to": c['to'], "district": c['district'], "rows": n, "id": r['MessageId']}) + "\n")
            except Exception as ex:
                print("FAIL", c['to'], ex)
            lg.flush(); time.sleep(0.12)
    print("done")


if __name__ == '__main__':
    main()
