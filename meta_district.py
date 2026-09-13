"""Meta ad performance, per district.

Three jobs, all read-only against the Meta tables that meta_ads.py owns:

  mirror_creative()   copy ad images to our S3, because Meta's CDN URLs expire in ~4 days
  district_meta()     everything one district needs: totals, daily series, ads, pacing
  efficiency()        this district's CPM and cost per click against the others

Nothing here writes to meta_ads.py's tables. That file belongs to another author on a repo
that auto-deploys, so an edit there would be overwritten without warning.

The rule that matters: a district only ever shows spend that ran in it. A statewide issue
campaign has no row in meta_campaign_district and must stay out of every per-district figure.
"""
import hashlib
import io
import logging
import re
from datetime import date, datetime, timezone


def _now_utc():
    return datetime.now(timezone.utc)

logger = logging.getLogger(__name__)

get_db_connection = None
release_db_connection = None
upload_to_storage = None

# Election day. Pacing is meaningless without the date the money has to be spent by.
ELECTION_DAY = date(2026, 11, 3)

# Digital tactics are budgeted in dollars, so their qty IS the budget.
DIGITAL_TACTICS = ('meta', 'ctv', 'display')


def init_meta_district(db_conn_func, db_release_func, storage_upload):
    global get_db_connection, release_db_connection, upload_to_storage
    get_db_connection = db_conn_func
    release_db_connection = db_release_func
    upload_to_storage = storage_upload


# =============================================================================
# CREATIVE MIRROR
# =============================================================================

def mirror_creative(limit=200):
    """Copy any ad image we do not already hold into our own S3.

    Meta hands back a signed CDN URL with an oe= expiry about four days out, so a page read
    later shows broken images. Dedupes on sha256 because the ads in one campaign usually share
    a single image, and a re-sync must not re-upload what we already have.
    """
    if upload_to_storage is None:
        return {'ok': False, 'error': 'file storage is not configured'}
    import requests

    conn = get_db_connection(); cur = conn.cursor()
    done, skipped, failed = 0, 0, []
    try:
        cur.execute("""SELECT a.ad_id, COALESCE(a.image_url, a.thumbnail_url), a.video_id
                         FROM meta_ads a
                         LEFT JOIN meta_ad_creative c ON c.ad_id = a.ad_id
                        WHERE c.ad_id IS NULL
                          AND COALESCE(a.image_url, a.thumbnail_url) IS NOT NULL
                        LIMIT %s""", (limit,))
        todo = cur.fetchall()
        cur.execute("SELECT sha256, s3_url, content_type FROM meta_ad_creative WHERE sha256 IS NOT NULL")
        seen = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        for ad_id, url, video_id in todo:
            try:
                r = requests.get(url, timeout=20)
                r.raise_for_status()
                body = r.content
                digest = hashlib.sha256(body).hexdigest()
                ctype = r.headers.get('content-type', 'image/jpeg').split(';')[0]

                if digest in seen:                      # same picture, already uploaded
                    s3_url, ctype = seen[digest]
                else:
                    ext = 'png' if 'png' in ctype else ('gif' if 'gif' in ctype else 'jpg')
                    key = 'meta-creative/%s.%s' % (re.sub(r'[^A-Za-z0-9_-]', '', ad_id), ext)
                    # upload_file_to_storage reads .content_type off the object, and a bare
                    # BytesIO has none, which would serve the image as octet-stream.
                    buf = io.BytesIO(body)
                    buf.content_type = ctype
                    s3_url = upload_to_storage(buf, key)
                    if not s3_url:
                        failed.append((ad_id, 'upload returned nothing')); continue
                    seen[digest] = (s3_url, ctype)

                cur.execute("""INSERT INTO meta_ad_creative
                                 (ad_id, source_url, s3_url, content_type, bytes, sha256, is_video)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (ad_id) DO UPDATE
                                 SET s3_url = EXCLUDED.s3_url, sha256 = EXCLUDED.sha256,
                                     fetched_at = now(), error = NULL""",
                            (ad_id, url, s3_url, ctype, len(body), digest, bool(video_id)))
                done += 1
            except Exception as e:
                failed.append((ad_id, str(e)[:120]))
                cur.execute("""INSERT INTO meta_ad_creative (ad_id, source_url, s3_url, error)
                               VALUES (%s,%s,'',%s)
                               ON CONFLICT (ad_id) DO UPDATE SET error = EXCLUDED.error,
                                                                 fetched_at = now()""",
                            (ad_id, url, str(e)[:200]))
        conn.commit()
    finally:
        cur.close(); release_db_connection(conn)
    return {'ok': True, 'mirrored': done, 'skipped': skipped, 'failed': failed}


# =============================================================================
# ONE DISTRICT
# =============================================================================

def _ratios(spend, impressions, clicks):
    """Rates are derived from summed totals, never averaged. Averaging a rate is wrong, and
    meta_ads.py's own _ratios does the same thing for the same reason."""
    return {
        'cpm': (spend / impressions * 1000) if impressions else None,
        'cpc': (spend / clicks) if clicks else None,
        'ctr': (clicks / impressions * 100) if impressions else None,
    }


def district_meta(codes):
    """Everything the Ads and Numbers tabs need, keyed by district code.

    `codes` is every district on the plan. A district with no linked campaign gets no entry at
    all rather than a row of zeroes, so the UI can tell "nothing running" from "running badly".
    """
    if not codes:
        return {}
    conn = get_db_connection(); cur = conn.cursor()
    out = {}
    try:
        # Totals and the daily series in one pass. level='campaign' only: the account level
        # would drag in statewide spend.
        cur.execute("""SELECT d.district_code, i.date, SUM(i.spend), SUM(i.impressions),
                              SUM(i.clicks), SUM(i.link_clicks)
                         FROM meta_campaign_district d
                         JOIN meta_insights i ON i.campaign_id = d.campaign_id
                                             AND i.level = 'campaign'
                        WHERE d.district_code = ANY(%s)
                        GROUP BY 1, 2 ORDER BY 1, 2""", (list(codes),))
        for code, day, sp, im, cl, lc in cur.fetchall():
            e = out.setdefault(code, {'days': [], 'spend': 0.0, 'impressions': 0,
                                      'clicks': 0, 'link_clicks': 0, 'ads': [],
                                      'campaigns': []})
            e['days'].append({'date': day.isoformat(), 'spend': float(sp or 0),
                              'impressions': int(im or 0), 'clicks': int(cl or 0)})
            e['spend'] += float(sp or 0)
            e['impressions'] += int(im or 0)
            e['clicks'] += int(cl or 0)
            e['link_clicks'] += int(lc or 0)

        if not out:
            return out

        live = list(out.keys())

        # The campaigns themselves, with their flight dates from the ad sets where we have them.
        cur.execute("""SELECT d.district_code, i.campaign_id, MAX(i.campaign_name),
                              MAX(i.campaign_status), MIN(i.date), MAX(i.date),
                              MIN(s.start_time), MAX(s.end_time),
                              MAX(s.campaign_lifetime_budget), MAX(s.campaign_daily_budget)
                         FROM meta_campaign_district d
                         JOIN meta_insights i ON i.campaign_id = d.campaign_id
                                             AND i.level = 'campaign'
                         LEFT JOIN meta_ad_sets s ON s.campaign_id = d.campaign_id
                        WHERE d.district_code = ANY(%s)
                        GROUP BY 1, 2""", (live,))
        for code, cid, cname, cstatus, lo, hi, start, end, lifebud, daybud in cur.fetchall():
            out[code]['campaigns'].append({
                'id': cid, 'name': cname, 'status': cstatus,
                'first_day': lo.isoformat() if lo else None,
                'last_day': hi.isoformat() if hi else None,
                'starts': start.date().isoformat() if start else None,
                'ends': end.date().isoformat() if end else None,
                'lifetime_budget': float(lifebud) if lifebud else None,
                'daily_budget': float(daybud) if daybud else None})

        # The flights (ad sets) behind this district's spend. A ramped buy splits its budget
        # across several, most of which have not started yet — so a projection built on the
        # running flight's daily rate alone is meaningless.
        cur.execute("""SELECT d.district_code, s.adset_id, s.name, s.lifetime_budget,
                              s.daily_budget, s.start_time, s.end_time,
                              COALESCE(SUM(a.spend), 0)
                         FROM meta_campaign_district d
                         JOIN meta_ad_sets s ON s.campaign_id = d.campaign_id
                         LEFT JOIN meta_ads a ON a.adset_id = s.adset_id
                        WHERE d.district_code = ANY(%s)
                        GROUP BY d.district_code, s.adset_id, s.name, s.lifetime_budget,
                                 s.daily_budget, s.start_time, s.end_time
                        ORDER BY s.start_time""", (live,))
        now = _now_utc()
        for r in cur.fetchall():
            st, en = r[5], r[6]
            state = ('scheduled' if st and st > now
                     else 'ended' if en and en < now
                     else 'running')
            out[r[0]].setdefault('flights', []).append(dict(
                adset_id=r[1], name=r[2],
                budget=float(r[3] or 0), daily_budget=float(r[4] or 0),
                start=st.isoformat() if st else None,
                end=en.isoformat() if en else None,
                spend=float(r[7] or 0), state=state))

        # The ads, with our own copy of the picture. meta_ads.spend is a 30 DAY rollup, not
        # lifetime - worth remembering before anyone reconciles it against the daily series.
        cur.execute("""SELECT d.district_code, a.ad_id, a.name, a.effective_status,
                              a.campaign_id, a.campaign_name, a.title, a.body, a.link_url,
                              a.spend, a.impressions, a.clicks, a.link_clicks,
                              a.first_delivered_on, a.last_delivered_on, a.delivery_days,
                              a.recent_spend, c.s3_url, c.is_video, a.video_id,
                              s.start_time, s.end_time, s.name
                         FROM meta_campaign_district d
                         JOIN meta_ads a ON a.campaign_id = d.campaign_id
                         LEFT JOIN meta_ad_creative c ON c.ad_id = a.ad_id
                                                     AND COALESCE(c.error,'') = ''
                         LEFT JOIN meta_ad_sets s ON s.adset_id = a.adset_id
                        WHERE d.district_code = ANY(%s)
                        ORDER BY a.spend DESC, a.name""", (live,))
        for r in cur.fetchall():
            spend = float(r[9] or 0); impr = int(r[10] or 0); clicks = int(r[11] or 0)
            out[r[0]]['ads'].append(dict(
                ad_id=r[1], name=r[2], status=r[3], campaign_id=r[4], campaign_name=r[5],
                title=r[6], body=r[7], link=r[8],
                spend=spend, impressions=impr, clicks=clicks, link_clicks=int(r[12] or 0),
                first=r[13].isoformat() if r[13] else None,
                last=r[14].isoformat() if r[14] else None,
                delivery_days=r[15], recent_spend=float(r[16] or 0),
                image=r[17], is_video=bool(r[18] or r[19]),
                flight_start=r[20].isoformat() if r[20] else None,
                flight_end=r[21].isoformat() if r[21] else None,
                flight_name=r[22],
                # An ad set that has not started yet has delivered nothing for a good
                # reason. Calling that "not delivering" makes every ramped buy look broken.
                scheduled=bool(r[20] and r[20] > _now_utc()),
                delivering=(impr > 0), **_ratios(spend, impr, clicks)))

        for code, e in out.items():
            e.update(_ratios(e['spend'], e['impressions'], e['clicks']))
            # Scheduled flights are not dead ads; they simply have not begun.
            e['dead_ads'] = sum(1 for a in e['ads']
                                if not a['delivering'] and not a.get('scheduled'))
            e['scheduled_ads'] = sum(1 for a in e['ads'] if a.get('scheduled'))
    finally:
        cur.close(); release_db_connection(conn)
    return out


def pacing(entry, budget, today=None):
    """Is this district going to spend its digital budget?

    A ramped buy splits its money across flights, and most of them have not started.
    Extrapolating the running flight's daily rate to election day therefore predicts a
    huge underspend that is not real. So three separate figures:

      budget    what the plan says the district should spend
      booked    what is actually committed inside Meta, across every flight
      projected booked money that should land, plus anything already spent outside a flight

    and the on-pace verdict is judged against the RUNNING flight's own window, not the
    whole campaign. Unbooked money is a planning gap, not an underspend.
    """
    if not entry or not budget:
        return None
    today = today or date.today()
    days = entry.get('days') or []
    if not days:
        return None
    first = datetime.fromisoformat(days[0]['date']).date()
    elapsed = max((today - first).days + 1, 1)
    left = max((ELECTION_DAY - today).days, 0)
    spent = entry['spend']
    per_day = spent / elapsed

    flights = entry.get('flights') or []
    booked = sum(f['budget'] for f in flights)
    running = [f for f in flights if f['state'] == 'running']
    scheduled = [f for f in flights if f['state'] == 'scheduled']

    # Money inside Meta should land: lifetime budgets pace themselves over their window.
    # Anything spent outside a flight we know about still counts.
    projected = booked if booked else spent + per_day * left
    projected = max(projected, spent)

    # Pace the running flight against its own window, which is the only rate that means
    # anything today.
    cur_need = cur_rate = None
    verdict = 'on track'
    if running:
        f = running[0]
        f_end = datetime.fromisoformat(f['end']).date() if f.get('end') else ELECTION_DAY
        f_start = datetime.fromisoformat(f['start']).date() if f.get('start') else first
        f_elapsed = max((today - f_start).days + 1, 1)
        f_left = max((f_end - today).days, 0)
        cur_rate = f['spend'] / f_elapsed
        if f_left:
            cur_need = max(f['budget'] - f['spend'], 0) / f_left
            if cur_need and abs(cur_rate - cur_need) > 0.25 * max(cur_need, 1):
                verdict = 'underspending' if cur_rate < cur_need else 'overspending'
    elif not scheduled and booked and spent < booked * 0.9:
        verdict = 'underspending'

    return {
        'budget': budget, 'spent': spent, 'booked': booked,
        'unbooked': max(budget - booked, 0),
        'elapsed_days': elapsed, 'days_left': left,
        'per_day': per_day, 'needed_per_day': (max(budget - spent, 0) / left) if left else None,
        'projected': projected,
        'pct_spent': (spent / budget * 100) if budget else None,
        'pct_projected': (projected / budget * 100) if budget else None,
        'flights_total': len(flights),
        'flights_running': len(running), 'flights_scheduled': len(scheduled),
        'current_rate': cur_rate, 'current_needed': cur_need,
        'current_name': running[0]['name'] if running else None,
        'current_end': running[0]['end'] if running else None,
        'verdict': verdict,
    }


def efficiency(all_meta):
    """Median CPM and cost per click across every district with real delivery, so one
    district's numbers can be read as dear or cheap rather than as a bare figure."""
    cpms = sorted(e['cpm'] for e in all_meta.values() if e.get('cpm'))
    cpcs = sorted(e['cpc'] for e in all_meta.values() if e.get('cpc'))

    def med(xs):
        if not xs:
            return None
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    return {'cpm': med(cpms), 'cpc': med(cpcs), 'districts': len(cpms)}
