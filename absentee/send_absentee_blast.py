#!/usr/bin/env python3
"""Send each R House candidate their own district's Republican-ballot absentee list.

  python3 send_absentee_blast.py --run runs/2026-08-13 --asof "13 August"                 # dry run
  python3 send_absentee_blast.py --run runs/2026-08-13 --asof "13 August" --draft-to chris@maidmentnh.com
  python3 send_absentee_blast.py --run runs/2026-08-13 --asof "13 August" --send          # live

The log (absentee_blast_log.jsonl in the run dir) makes the live send resumable:
already-logged candidates are skipped, so a crash mid-run can just be rerun.
"""
import argparse, csv, json, os, sys, time

import boto3
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from absentee_email import build

ORG = "Committee to Elect House Republicans"
SRC = "info@electhouserepublicans.com"
SENDER = f'"{ORG}" <{SRC}>'


def compose(c, rundir, asof):
    dist = c['district']
    fn = f"{rundir}/absentee_by_district/{dist.replace(' ', '_')}_REP_absentee.csv"
    rows = list(csv.DictReader(open(fn)))
    counts = (sum(1 for r in rows if r['Bucket'] == 'BALLOT IN HAND'),
              sum(1 for r in rows if r['Bucket'] == 'REQUESTED, NOT YET MAILED'),
              sum(1 for r in rows if r['Bucket'] == 'ALREADY VOTED'))
    towns = sorted({r['Town'].title() for r in rows})
    phones = sum(1 for r in rows if r['Phone'])
    base = os.path.basename(fn)
    html, text = build(c['first'], dist, counts, towns, phones, len(rows), base, asof)

    m = MIMEMultipart('mixed')
    m['Subject'] = f"Who already has a primary ballot in {dist}"
    m['From'] = SENDER
    m['Reply-To'] = SRC
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(text, 'plain'))
    alt.attach(MIMEText(html, 'html'))
    m.attach(alt)
    att = MIMEApplication(open(fn, 'rb').read(), _subtype='csv')
    att.add_header('Content-Disposition', 'attachment', filename=base)
    m.attach(att)
    return m, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True, help='run dir from run_pipeline.py')
    ap.add_argument('--asof', required=True, help='human date for the email copy, e.g. "13 August"')
    ap.add_argument('--send', action='store_true', help='actually send to the candidates')
    ap.add_argument('--draft-to', help='send ONE sample email (largest district) to this address, '
                                       'subject-prefixed [DRAFT - not sent to candidates]')
    a = ap.parse_args()
    rundir = a.run if os.path.isabs(a.run) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), a.run)
    plan = json.load(open(f'{rundir}/send_plan.json'))
    log_path = f'{rundir}/absentee_blast_log.jsonl'

    # verify every attachment exists and is non-empty before sending anything
    bad = []
    for c in plan:
        fn = f"{rundir}/absentee_by_district/{c['district'].replace(' ', '_')}_REP_absentee.csv"
        if not os.path.exists(fn):
            bad.append((c['district'], 'missing file'))
        elif not list(csv.DictReader(open(fn))):
            bad.append((c['district'], 'empty file'))
    if bad:
        sys.exit(f"ABORT - attachment problems: {bad[:5]}")
    print(f"verified {len({c['district'] for c in plan})} district files")

    ses = boto3.client('ses', region_name='us-east-1')

    if a.draft_to:
        by_size = []
        for c in plan:
            m, n = compose(c, rundir, a.asof)
            by_size.append((n, c, m))
        n, c, m = max(by_size, key=lambda x: x[0])
        del m['Subject']
        m['Subject'] = f"[DRAFT - not sent to candidates] Who already has a primary ballot in {c['district']}"
        del m['To']
        m['To'] = a.draft_to
        r = ses.send_raw_email(Source=SRC, Destinations=[a.draft_to],
                               RawMessage={'Data': m.as_string()})
        print(f"draft sent to {a.draft_to}: sample is {c['district']} "
              f"({c['first']} {c['last']}, {n} voters), SES {r['MessageId']}")
        return

    done = set()
    if os.path.exists(log_path):
        for line in open(log_path):
            try:
                done.add(json.loads(line)['cid'])
            except Exception:
                pass

    sent = fail = skip = 0
    for c in plan:
        if c['cid'] in done:
            skip += 1; continue
        m, n = compose(c, rundir, a.asof)
        m['To'] = c['to']
        if not a.send:
            print(f"  [DRY] {c['district']:18s} {c['first']} {c['last']:22s} -> {c['to']:38s} {n:3d} voters")
            sent += 1
            continue
        try:
            r = ses.send_raw_email(Source=SRC, Destinations=[c['to']],
                                   RawMessage={'Data': m.as_string()})
            sent += 1
            with open(log_path, 'a') as f:
                f.write(json.dumps({'cid': c['cid'], 'district': c['district'], 'to': c['to'],
                                    'name': f"{c['first']} {c['last']}", 'voters': n,
                                    'id': r['MessageId']}) + "\n")
            if sent % 25 == 0:
                print(f"   ... {sent} sent")
        except Exception as e:
            fail += 1
            print(f"  FAIL {c['district']} {c['to']}: {type(e).__name__} {e}")
        time.sleep(0.15)
    print(f"\n{'SENT' if a.send else 'DRY RUN'}: {sent}   failed {fail}   already done {skip}")


if __name__ == '__main__':
    main()
