#!/usr/bin/env python3
"""
Sync the website builder into `candidates`, so the recruitment side stops
reporting "no website" for people who have had a live site for months.

Everything already lives in one database. The problem is that the builder
writes to ws_submissions and nothing ever copies it back, so the two halves
disagree. `candidates` is the source of truth every other tool reads
(directory, digest, palm cards, whip tool) - this makes it true.

Only ever FILLS BLANKS. It will not overwrite a value a human already put in
candidates, so running it twice is safe and it can be put on a cron.

    python3 sync_builder_to_candidates.py --dry-run
    python3 sync_builder_to_candidates.py
"""
import os
import sys
import psycopg2

DSN = dict(host='127.0.0.1', dbname='candidate_recruitment',
           user='postgres', password=os.environ.get('PGPASSWORD', 'postgres123'))

# builder column  ->  candidates column
FIELDS = [
    ('site',   'external_campaign_url'),
    ('donate', 'donate_url'),
    ('fb',     'facebook_url'),
    ('tw',     'twitter_url'),
    ('ig',     'instagram_url'),
    ('yt',     'youtube_url'),
    ('tiktok', 'tiktok_url'),
    ('bio',    'bio'),
    ('slogan', 'campaign_slogan'),
]

SRC = """
SELECT wc.recruitment_candidate_id AS cid,
       MAX(CASE WHEN s.status='custom_domain_live' AND s.custom_domain <> ''
                     THEN 'https://' || s.custom_domain
                WHEN s.status='live' AND s.website_slug <> ''
                     THEN 'https://' || s.website_slug || '.winthehouse.gop' END) AS site,
       MAX(NULLIF(TRIM(s.donation_url),''))   AS donate,
       MAX(NULLIF(TRIM(s.facebook_url),''))   AS fb,
       MAX(NULLIF(TRIM(s.twitter_url),''))    AS tw,
       MAX(NULLIF(TRIM(s.instagram_url),''))  AS ig,
       MAX(NULLIF(TRIM(s.youtube_url),''))    AS yt,
       MAX(NULLIF(TRIM(s.tiktok_url),''))     AS tiktok,
       MAX(NULLIF(TRIM(s.bio),''))            AS bio,
       MAX(NULLIF(TRIM(s.campaign_slogan),'')) AS slogan
FROM ws_submissions s
JOIN ws_candidates wc ON wc.id = s.candidate_id
WHERE wc.recruitment_candidate_id IS NOT NULL
GROUP BY 1
"""


def main():
    dry = '--dry-run' in sys.argv
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    cur.execute(SRC)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    filled = {c: 0 for _s, c in FIELDS}
    touched = 0
    for r in rows:
        sets, vals = [], []
        for src, dest in FIELDS:
            if r.get(src):
                # COALESCE(NULLIF(...)) means: only write where the existing
                # value is null or an empty string. Never clobber a human edit.
                sets.append(f"{dest} = COALESCE(NULLIF(TRIM({dest}), ''), %s)")
                vals.append(r[src])
        if not sets:
            continue
        where = " OR ".join(
            f"COALESCE(TRIM({d}),'') = ''" for s, d in FIELDS if r.get(s))
        sql = (f"UPDATE candidates SET {', '.join(sets)} "
               f"WHERE candidate_id = %s AND ({where})")
        if dry:
            cur.execute(
                f"SELECT {', '.join(d for s, d in FIELDS if r.get(s))} "
                f"FROM candidates WHERE candidate_id=%s", (r['cid'],))
            cur_vals = cur.fetchone() or ()
            for (s, d), existing in zip([f for f in FIELDS if r.get(f[0])], cur_vals):
                if not (existing or '').strip():
                    filled[d] += 1
            touched += 1
        else:
            cur.execute(sql, vals + [r['cid']])
            if cur.rowcount:
                touched += 1

    if dry:
        print("DRY RUN — would fill blanks:")
        for _s, d in FIELDS:
            if filled[d]:
                print(f"   {d:<24} {filled[d]}")
        print(f"   candidates touched: {touched}")
        conn.rollback()
    else:
        conn.commit()
        print(f"synced; {touched} candidate rows updated")
    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
