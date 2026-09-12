"""
Ad monitor: every political ad in the race on Facebook and Instagram, ours and theirs, read
from Meta's public Ad Library.

Ported from the Goffstown CRM (src/lib/ad-library.ts). Nobody can read a rival's ad account.
What Meta does publish, for any ad declared political or issue-based, is the Ad Library: the
words, the dates, and spend and impressions as a RANGE, never an exact number. Pages are
watched on both sides - candidates we support as well as the NHDP and outside groups - so
the same public numbers answer "what are they spending?" and "are we keeping up?".

Two things about the source shape every line below.

  1. Only ads the advertiser declared political carry spend numbers in the US. A page that
     never ran the "authorised for political ads" steps is invisible here, and that is a
     real answer, not a bug.
  2. Spend and impressions come as buckets ("$100-$199"), never exact figures, and they are
     for the ad's whole life, not per day. Everything downstream keeps both ends of the
     bucket and says "estimated" out loud.

Access is the same private feature as /meta ('meta_ads').
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from psycopg2.extras import RealDictCursor, Json, execute_values

from private_features import require_feature_access
from meta_ads import (FEATURE, MetaApiError, _token_shape_problem, cron_authorized, iso_days_ago, meta_get_all_paged,
                      report_today)

logger = logging.getLogger(__name__)

admon_bp = Blueprint('admon', __name__, url_prefix='/ad-monitor')

get_db_connection = None
release_db_connection = None


def init_ad_monitor(db_conn_func, db_release_func):
    global get_db_connection, release_db_connection
    get_db_connection = db_conn_func
    release_db_connection = db_release_func


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


# Whose side a watched Page is on.
SIDES = ('oppose', 'support')
SIDE_LABELS = {'support': 'Our side', 'oppose': 'Opposition'}
SIDE_LABELS_ONE = {'support': 'We support', 'oppose': 'Opponent'}

# This race only. Without a floor the archive hands back seven years, including 2024.
CYCLE_START = (os.environ.get('META_AD_CYCLE_START') or '2026-01-01').strip()

AD_COUNTRY = 'US'
AD_TYPE = 'POLITICAL_AND_ISSUE_ADS'   # the only ad type with spend attached in the US

# Pages of 100 ads one sync will follow. A House race is a few dozen ads; the NHDP is more.
MAX_PAGES = 10

# Meta throttles the archive hard, and answers a throttled call with the same "(#10) does not
# have permission" sentence it uses for a real refusal. Two defences: a gap between calls so
# one sync never bursts, and retries for the calls that look transient.
CALL_GAP_S = 1.2
RETRIES = 3
TRANSIENT = re.compile(r'\(#10\)|does not have permission|not have the permission|rate limit|too many calls|'
                       r'\(#613\)|\(#4\)|\(#17\)|\(#32\)|\(#341\)|temporarily|please try again|did not answer|'
                       r'network error', re.I)

ARCHIVE_FIELDS = ','.join(['id', 'page_id', 'page_name', 'bylines', 'currency', 'ad_creation_time',
                           'ad_delivery_start_time', 'ad_delivery_stop_time', 'ad_creative_bodies',
                           'ad_creative_link_titles', 'ad_creative_link_captions', 'ad_snapshot_url', 'impressions',
                           'spend', 'publisher_platforms', 'estimated_audience_size', 'delivery_by_region',
                           'demographic_distribution'])

# Meta's bottom band, "<$100", has no floor and so no trustworthy midpoint.
SMALL_AD_CEILING = 99

# The length of Meta's own published window, so ours is the same shape as theirs.
ROLLING_DAYS = 7


# =============================================================================
# TOKEN
# =============================================================================

def ad_library_token():
    """The archive can use its own token, because reading it needs an ID-confirmed account
    and that is often a different person from whoever holds the ads token. Falls back to
    META_API_KEY so a single confirmed token can do both jobs."""
    own = (os.environ.get('META_AD_LIBRARY_TOKEN') or '').strip()
    if own:
        return own
    shared = (os.environ.get('META_API_KEY') or '').strip()
    return shared or None


def ad_library_token_source():
    if (os.environ.get('META_AD_LIBRARY_TOKEN') or '').strip():
        return 'META_AD_LIBRARY_TOKEN'
    if (os.environ.get('META_API_KEY') or '').strip():
        return 'META_API_KEY'
    return None


def ad_library_token_problem():
    t = ad_library_token()
    if not t:
        return 'No token is set. Add META_AD_LIBRARY_TOKEN (or META_API_KEY) on this environment.'
    return _token_shape_problem(t, ad_library_token_source())


def has_ad_library_token():
    return ad_library_token_problem() is None


def explain_archive_error(e):
    """The archive refuses everyone who has not confirmed their identity with Meta, and the
    error it gives back says nothing useful. Turn the known ones into instructions."""
    msg = str(e) or 'Unknown error'
    if re.search(r'does not have permission|not have the permission|\(#10\)|\(#200\)', msg, re.I):
        # Meta sends this same sentence when the archive is simply busy. It has already been
        # retried by the time we get here.
        return (f'{msg}. This is usually Meta throttling the archive rather than a real permission problem, and it '
                'clears on its own - the hourly sync will pick it up. Only if it keeps failing all day is it worth '
                'checking that the account behind META_AD_LIBRARY_TOKEN has finished ID confirmation at facebook.com/ID.')
    if re.search(r'cannot parse access token|invalid oauth access token|malformed', msg, re.I):
        return f'{msg}. That value is not a Meta access token. An App ID or App Secret will not work here.'
    if re.search(r'expired|session has been invalidated', msg, re.I):
        return f'{msg}. Generate a fresh token and update META_AD_LIBRARY_TOKEN.'
    if re.search(r'rate limit|too many calls|\(#613\)', msg, re.I):
        return f'{msg}. Meta is rate limiting the archive. The hourly sync will pick it up; there is no need to keep pressing Sync.'
    return msg


# =============================================================================
# TALKING TO THE ARCHIVE
# =============================================================================

def bounds(b):
    """One of Meta's buckets as (lower, upper). The very top bucket has no upper end, so the
    lower end stands in for it - an under-count, which is the safe way to be wrong about
    how much the other side is spending."""
    b = b or {}
    try:
        lower = float(b.get('lower_bound') or 0)
    except (TypeError, ValueError):
        lower = 0.0
    try:
        upper = float(b['upper_bound']) if b.get('upper_bound') not in (None, '') else lower
    except (TypeError, ValueError):
        upper = lower
    return lower, max(lower, upper)


def range_label(lower, upper, whole=True):
    """"$100 - $199", or "$100+" when Meta gave no upper end."""
    fmt = (lambda n: f'${n:,.0f}') if whole else (lambda n: f'${n:,.2f}')
    if lower == 0 and upper == 0:
        return fmt(0)   # "$0+" reads as "at least nothing", which is noise
    if upper <= lower:
        return f'{fmt(lower)}+'
    return f'{fmt(lower)} - {fmt(upper)}'


def _first_of(lst):
    for s in lst or []:
        if isinstance(s, str) and s.strip():
            return s.strip()
    return None


def _to_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None


def _safe_snapshot_url(raw):
    """Meta hands back ad_snapshot_url with OUR access token in its query string. Saving that
    as-is would put a working token in the database and print it in a link on the page."""
    if not raw:
        return None
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    try:
        u = urlsplit(raw)
        q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k != 'access_token']
        return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))
    except ValueError:
        return None


def ad_library_url(archive_id):
    """The public Ad Library page for one ad. Needs no token and works signed out."""
    return f'https://www.facebook.com/ads/library/?id={archive_id}'


def ad_library_page_url(page_id):
    from urllib.parse import urlencode
    return 'https://www.facebook.com/ads/library/?' + urlencode({'active_status': 'all', 'ad_type': 'political_and_issue_ads',
                                                                 'country': AD_COUNTRY, 'view_all_page_id': page_id})


def _archive_search(token, query, max_pages=MAX_PAGES):
    params = {'ad_reached_countries': json.dumps([AD_COUNTRY]), 'ad_type': AD_TYPE, 'ad_active_status': 'ALL',
              'ad_delivery_date_min': CYCLE_START, 'fields': ARCHIVE_FIELDS, 'limit': 100, **query}
    last = None
    for attempt in range(RETRIES):
        if attempt > 0:
            time.sleep(2 * attempt)   # 2s then 4s: long enough for a throttle to lift
        try:
            return meta_get_all_paged('ads_archive', token, params, max_pages)
        except MetaApiError as e:
            last = e
            # A real refusal - a dead token, a bad query - will not fix itself.
            if not TRANSIENT.search(str(e)):
                raise
    raise last


def find_pages(terms):
    """The Pages behind a name, so a page id never has to be typed by hand. The archive has no
    page search of its own, so it is searched for ads and the answers are grouped by Page."""
    problem = ad_library_token_problem()
    if problem:
        raise MetaApiError(problem)
    search = (terms or '').strip()
    if len(search) < 3:
        raise MetaApiError('Type at least three letters to search.')
    rows, _ = _archive_search(ad_library_token(), {'search_terms': search}, 5)
    by_page = {}
    for ad in rows:
        pid = ad.get('page_id')
        if not pid:
            continue
        cur = by_page.setdefault(pid, {'page_id': pid, 'page_name': ad.get('page_name') or pid, 'ads': 0,
                                       'spend_upper': 0.0, 'last_ran': None})
        cur['ads'] += 1
        cur['spend_upper'] += bounds(ad.get('spend'))[1]
        start = (ad.get('ad_delivery_start_time') or '')[:10] or None
        if start and (not cur['last_ran'] or start > cur['last_ran']):
            cur['last_ran'] = start
    return sorted(by_page.values(), key=lambda p: -p['ads'])[:25]


# =============================================================================
# SYNC
# =============================================================================

def _terms_of(raw):
    """Splits "Jane Doe, Doe for NH House" into its terms."""
    return [s.strip() for s in (raw or '').split(',') if len(s.strip()) >= 3][:5]


def _write_spend_estimates(cur, watch_id):
    """Turns each ad's impressions into a spend estimate.

    Meta publishes impressions in bands too, but those bands are tight relative to the spend
    ones, and impressions are what spend actually buys. So: work out what this page pays per
    thousand impressions from its OWN ads whose spend band has a real floor, then price its
    sub-$100 ads off that rate. The result is clamped into the band Meta published, so the
    estimate can never contradict the source. A page with no usable anchor of its own borrows
    the rate from every watched page together.

    Do NOT use the plain midpoint to headline a total: Meta's bottom band is "<$100", so the
    midpoint charges $49.50 to an ad that may have cost a dollar, and a page running dozens
    of small boosts then reads as thousands of dollars.
    """
    cur.execute("""
        SELECT sum((spend_lower + spend_upper) / 2) FILTER (WHERE spend_upper > %(c)s)
               / nullif(sum((impressions_lower + impressions_upper) / 2.0) FILTER (WHERE spend_upper > %(c)s), 0) * 1000
               AS cpm
        FROM ad_watch_ads""", {'c': SMALL_AD_CEILING})
    row = cur.fetchone()
    global_cpm = float(row[0]) if row and row[0] is not None else None
    if not global_cpm or global_cpm <= 0:
        # Nothing anywhere has a priced anchor yet, so there is no rate to reason from.
        cur.execute("UPDATE ad_watch_ads SET spend_estimate = (spend_lower + spend_upper) / 2 WHERE watch_id = %s", (watch_id,))
        return
    cur.execute("""
        WITH anchor AS (
            SELECT coalesce(
                     sum((spend_lower + spend_upper) / 2) FILTER (WHERE spend_upper > %(c)s)
                       / nullif(sum((impressions_lower + impressions_upper) / 2.0) FILTER (WHERE spend_upper > %(c)s), 0) * 1000,
                     %(g)s) AS cpm
            FROM ad_watch_ads WHERE watch_id = %(w)s)
        UPDATE ad_watch_ads a
           SET spend_estimate = least(a.spend_upper,
                 greatest(a.spend_lower, ((a.impressions_lower + a.impressions_upper) / 2.0) / 1000 * (SELECT cpm FROM anchor)))
         WHERE a.watch_id = %(w)s""", {'c': SMALL_AD_CEILING, 'g': global_cpm, 'w': watch_id})


def _set_error(watch_id, msg):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE ad_watch_pages SET last_sync_error = %s WHERE id = %s", ((msg or '')[:1000] or None, watch_id))
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)


def sync_watch(row):
    """Pulls one page's ads and writes today's running total.

    When a page id is known, ads found by search word are kept only if they came from that
    page. Without that filter an attack ad naming the candidate, paid for by somebody else
    entirely, would be added to their own spend.
    """
    base = {'watch_id': row['id'], 'name': row['name'], 'ads': 0, 'spend_lower': 0.0, 'spend_upper': 0.0, 'truncated': False}
    problem = ad_library_token_problem()
    if problem:
        _set_error(row['id'], problem)
        return {**base, 'ok': False, 'error': problem}
    token = ad_library_token()

    queries = []
    if row.get('page_id'):
        queries.append({'search_page_ids': json.dumps([row['page_id']])})
    for t in _terms_of(row.get('search_terms')):
        queries.append({'search_terms': t})
    if not queries:
        msg = 'This page has no Page id and no search words, so there is nothing to look up.'
        _set_error(row['id'], msg)
        return {**base, 'ok': False, 'error': msg}

    found = {}
    truncated = False
    try:
        for i, q in enumerate(queries):
            if i > 0:
                time.sleep(CALL_GAP_S)
            rows, trunc = _archive_search(token, q)
            truncated = truncated or trunc
            for ad in rows:
                if not ad.get('id'):
                    continue
                if row.get('page_id') and 'search_terms' in q and ad.get('page_id') != row['page_id']:
                    continue
                found[ad['id']] = ad
    except MetaApiError as e:
        error = explain_archive_error(e)
        _set_error(row['id'], error)
        return {**base, 'ok': False, 'error': error}

    now = datetime.now(timezone.utc)
    values = []
    for ad in found.values():
        sl, su = bounds(ad.get('spend'))
        il, iu = bounds(ad.get('impressions'))
        aud = bounds(ad['estimated_audience_size']) if ad.get('estimated_audience_size') else None
        values.append((row['id'], ad['id'], ad.get('page_id'), ad.get('page_name'), ad.get('bylines'),
                       ad.get('currency') or 'USD', round(sl, 2), round(su, 2), int(round(il)), int(round(iu)),
                       int(round(aud[0])) if aud else None, int(round(aud[1])) if aud else None,
                       _first_of(ad.get('ad_creative_bodies')), _first_of(ad.get('ad_creative_link_titles')),
                       _first_of(ad.get('ad_creative_link_captions')), _safe_snapshot_url(ad.get('ad_snapshot_url')),
                       Json(ad.get('publisher_platforms')) if ad.get('publisher_platforms') is not None else None,
                       Json(ad.get('delivery_by_region')) if ad.get('delivery_by_region') is not None else None,
                       Json(ad.get('demographic_distribution')) if ad.get('demographic_distribution') is not None else None,
                       _to_dt(ad.get('ad_creation_time')), _to_dt(ad.get('ad_delivery_start_time')),
                       _to_dt(ad.get('ad_delivery_stop_time')), now))

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        if values:
            execute_values(cur, """
                INSERT INTO ad_watch_ads (watch_id, archive_id, page_id, page_name, bylines, currency, spend_lower,
                    spend_upper, impressions_lower, impressions_upper, audience_lower, audience_upper, body_text,
                    link_title, link_caption, snapshot_url, platforms, regions, demographics, created_time,
                    start_time, stop_time, synced_at)
                VALUES %s
                ON CONFLICT (watch_id, archive_id) DO UPDATE SET
                    page_id = EXCLUDED.page_id, page_name = EXCLUDED.page_name, bylines = EXCLUDED.bylines,
                    currency = EXCLUDED.currency, spend_lower = EXCLUDED.spend_lower, spend_upper = EXCLUDED.spend_upper,
                    impressions_lower = EXCLUDED.impressions_lower, impressions_upper = EXCLUDED.impressions_upper,
                    audience_lower = EXCLUDED.audience_lower, audience_upper = EXCLUDED.audience_upper,
                    body_text = EXCLUDED.body_text, link_title = EXCLUDED.link_title, link_caption = EXCLUDED.link_caption,
                    snapshot_url = EXCLUDED.snapshot_url, platforms = EXCLUDED.platforms, regions = EXCLUDED.regions,
                    demographics = EXCLUDED.demographics, created_time = EXCLUDED.created_time,
                    start_time = EXCLUDED.start_time, stop_time = EXCLUDED.stop_time, synced_at = EXCLUDED.synced_at
                    -- first_seen_at is left alone on purpose: it records when WE first saw the ad.
            """, values, page_size=100)
        # Ads that no longer come back are dropped, because the search words may have been
        # narrowed and stale rows would keep inflating the total. Skipped when Meta hit the
        # page cap: "not on the pages we read" is not the same as "gone".
        if not truncated:
            if found:
                cur.execute("DELETE FROM ad_watch_ads WHERE watch_id = %s AND archive_id <> ALL(%s)", (row['id'], list(found)))
            else:
                cur.execute("DELETE FROM ad_watch_ads WHERE watch_id = %s", (row['id'],))

        _write_spend_estimates(cur, row['id'])

        cur.execute("""
            SELECT count(*), count(*) FILTER (WHERE stop_time IS NULL OR stop_time > now()),
                   coalesce(sum(spend_lower),0), coalesce(sum(spend_upper),0), coalesce(sum(spend_estimate),0),
                   coalesce(sum(impressions_lower),0), coalesce(sum(impressions_upper),0)
            FROM ad_watch_ads WHERE watch_id = %s""", (row['id'],))
        t = cur.fetchone()
        cur.execute("""
            INSERT INTO ad_watch_snapshots (watch_id, date, ads, active_ads, spend_lower, spend_upper, spend_estimate,
                                            impressions_lower, impressions_upper, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (watch_id, date) DO UPDATE SET
                ads = EXCLUDED.ads, active_ads = EXCLUDED.active_ads, spend_lower = EXCLUDED.spend_lower,
                spend_upper = EXCLUDED.spend_upper, spend_estimate = EXCLUDED.spend_estimate,
                impressions_lower = EXCLUDED.impressions_lower, impressions_upper = EXCLUDED.impressions_upper,
                fetched_at = EXCLUDED.fetched_at""",
                    (row['id'], report_today(now), t[0], t[1], t[2], t[3], t[4], t[5], t[6], now))
        cur.execute("UPDATE ad_watch_pages SET last_synced_at = %s, last_sync_error = NULL WHERE id = %s", (now, row['id']))
        conn.commit()
        cur.close()
        return {**base, 'ok': True, 'ads': t[0], 'spend_lower': float(t[2]), 'spend_upper': float(t[3]), 'truncated': truncated}
    except Exception as e:
        conn.rollback()
        logger.exception(f'[ad-monitor] sync failed for {row["name"]}')
        _set_error(row['id'], str(e))
        return {**base, 'ok': False, 'error': str(e)}
    finally:
        release_db_connection(conn)


def _watch_row(watch_id):
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute("SELECT * FROM ad_watch_pages WHERE id = %s", (watch_id,))
        return cur.fetchone()
    finally:
        release_db_connection(conn)


def sync_all_watches():
    """Every watched page, one at a time with a gap, never in parallel. Firing every page at
    Meta at once is exactly what trips the archive's rate limit."""
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute("SELECT * FROM ad_watch_pages WHERE active ORDER BY name")
        rows = cur.fetchall()
    finally:
        release_db_connection(conn)
    results = []
    for i, row in enumerate(rows):
        if i > 0:
            time.sleep(CALL_GAP_S)
        try:
            results.append(sync_watch(row))
        except Exception as e:
            results.append({'watch_id': row['id'], 'name': row['name'], 'ok': False, 'ads': 0, 'spend_lower': 0.0,
                            'spend_upper': 0.0, 'truncated': False, 'error': explain_archive_error(e)})
    return {'total': len(results), 'synced': sum(1 for r in results if r['ok']),
            'failed': sum(1 for r in results if not r['ok']), 'results': results}


# =============================================================================
# QUERIES
# =============================================================================

def _f(v):
    return float(v) if v is not None else None


def list_watches():
    """Every watched page with its archive totals rolled up."""
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute("""
            SELECT p.*,
                   count(a.id)::int AS ads,
                   count(a.id) FILTER (WHERE a.stop_time IS NULL OR a.stop_time > now())::int AS active_ads,
                   coalesce(sum(a.spend_lower),0) AS spend_lower, coalesce(sum(a.spend_upper),0) AS spend_upper,
                   coalesce(sum(a.spend_estimate),0) AS spend_estimate,
                   coalesce(sum(a.impressions_lower),0)::bigint AS impressions_lower,
                   coalesce(sum(a.impressions_upper),0)::bigint AS impressions_upper,
                   count(a.id) FILTER (WHERE a.spend_upper <= %s)::int AS small_ads,
                   max(a.start_time) AS last_ad_on, max(a.bylines) AS bylines
            FROM ad_watch_pages p LEFT JOIN ad_watch_ads a ON a.watch_id = p.id
            GROUP BY p.id ORDER BY coalesce(sum(a.spend_upper),0) DESC, p.name""", (SMALL_AD_CEILING,))
        rows = cur.fetchall()
    finally:
        release_db_connection(conn)
    for r in rows:
        for k in ('spend_lower', 'spend_upper', 'spend_estimate', 'meta_total_spend', 'meta_week_spend'):
            r[k] = _f(r[k])
        r['range_label'] = range_label(r['spend_lower'], r['spend_upper'])
        r['page_url'] = ad_library_page_url(r['page_id']) if r['page_id'] else None
    return rows


def list_watched_ads(watch_id=None, limit=120, running_only=True, side=None):
    """The ads themselves. Live ads first, then the biggest spenders, so what they are saying
    today sits at the top instead of under a finished ad that happened to cost more."""
    where = []
    params = []
    if watch_id:
        where.append('a.watch_id = %s')
        params.append(watch_id)
    if running_only:
        where.append('(a.stop_time IS NULL OR a.stop_time > now())')
    if side in SIDES:
        where.append('p.side = %s')
        params.append(side)
    params.append(limit)
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute(f"""
            SELECT a.*, p.name AS watch_name, p.side,
                   (a.stop_time IS NULL OR a.stop_time > now()) AS running
            FROM ad_watch_ads a JOIN ad_watch_pages p ON p.id = a.watch_id
            {('WHERE ' + ' AND '.join(where)) if where else ''}
            ORDER BY (a.stop_time IS NULL OR a.stop_time > now()) DESC, a.spend_upper DESC, a.start_time DESC NULLS LAST
            LIMIT %s""", params)
        rows = cur.fetchall()
    finally:
        release_db_connection(conn)
    for r in rows:
        for k in ('spend_lower', 'spend_upper', 'spend_estimate'):
            r[k] = _f(r[k])
        r['platforms'] = r['platforms'] if isinstance(r['platforms'], list) else []
        r['range_label'] = range_label(r['spend_lower'], r['spend_upper'])
        r['impressions_label'] = range_label(r['impressions_lower'], r['impressions_upper']).replace('$', '')
        r['url'] = ad_library_url(r['archive_id'])
    return rows


def list_meta_reports():
    """Every "last 7 days" figure ever recorded off Meta, newest first. Read once a DAY they
    are a rolling window that moves one day at a time, and the gap between two readings a day
    apart says how much heavier yesterday was than the day that fell out of the window."""
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute("""SELECT r.watch_id, p.name AS watch_name, r.read_on, r.total_spend, r.week_spend
                       FROM ad_watch_meta_reports r JOIN ad_watch_pages p ON p.id = r.watch_id
                       ORDER BY r.read_on DESC, p.name""")
        rows = cur.fetchall()
    finally:
        release_db_connection(conn)
    previous = {}
    out = []
    for r in reversed(rows):
        week = _f(r['week_spend'])
        prior = previous.get(r['watch_id'])
        out.append({**r, 'total_spend': _f(r['total_spend']), 'week_spend': week,
                    'week_change': (week - prior['week']) if week is not None and prior and prior['week'] is not None else None,
                    'days_since_previous': (r['read_on'] - prior['read_on']).days if prior else None})
        if week is not None:
            previous[r['watch_id']] = {'read_on': r['read_on'], 'week': week}
    out.reverse()
    return out


def get_trend(days=30):
    """The running total per page per day, for the chart, in three shapes:
        daily        - the running total to date (days before we started watching stay empty)
        daily_added  - the gap between one reading and the one before: what went out THAT day
        rolling      - Meta's own seven-day window, worked out for every day
    """
    now = datetime.now(timezone.utc)
    since = iso_days_ago(days - 1, now)
    # Six days of history behind the chart's left edge. The rolling week needs them, or its
    # first points would sum a part-week and draw a slump that never happened.
    since_ext = iso_days_ago(days - 1 + (ROLLING_DAYS - 1), now)
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute("SELECT id, name, side FROM ad_watch_pages ORDER BY name")
        people = cur.fetchall()
        cur.execute("""SELECT watch_id, date, spend_lower, spend_upper, spend_estimate FROM ad_watch_snapshots
                       WHERE date >= %s ORDER BY date""", (since_ext,))
        snaps = cur.fetchall()
    finally:
        release_db_connection(conn)

    dates_ext = [iso_days_ago(i, now) for i in range(days - 1 + (ROLLING_DAYS - 1), -1, -1)]
    dates = dates_ext[ROLLING_DAYS - 1:]
    by_date = {d: {'date': d} for d in dates}
    first_seen, last_seen, readings = {}, {}, {}
    first_day = None
    for s in snaps:
        # Snapshots taken before the estimate column existed hold 0, so the published floor
        # stands in rather than dropping the line to nothing.
        est = float(s['spend_estimate'] or 0)
        mid = est if est > 0 else float(s['spend_lower'] or 0)
        d = s['date'].isoformat()
        readings.setdefault(s['watch_id'], []).append((d, mid))
        if d < since:
            continue
        if d in by_date:
            by_date[d][str(s['watch_id'])] = mid
        first_seen.setdefault(s['watch_id'], mid)
        last_seen[s['watch_id']] = mid
        if not first_day or d < first_day:
            first_day = d

    # Day-on-day movement from the raw readings. The very first reading gets no bar (its
    # total is the whole backlog before we looked); a drop is clamped to zero (totals shrink
    # when an old ad ages out, and negative spend is not a thing).
    added_by_date = {d: {} for d in dates_ext}
    watched_span = {}
    for wid, lst in readings.items():
        for i in range(1, len(lst)):
            if lst[i][0] in added_by_date:
                added_by_date[lst[i][0]][str(wid)] = max(0.0, lst[i][1] - lst[i - 1][1])
        if len(lst) > 1:
            watched_span[wid] = (lst[1][0], lst[-1][0])
    daily_added = [{'date': d, **added_by_date.get(d, {})} for d in dates]

    # A point is drawn only where all seven of its days sit inside the watched stretch; a
    # part-week would read as a quiet spell when the truth is that nobody was looking.
    rolling = []
    for i, day in enumerate(dates):
        rec = {'date': day}
        frm = dates_ext[i]
        for wid, (span_from, span_to) in watched_span.items():
            if frm < span_from or day > span_to:
                continue
            rec[str(wid)] = sum(added_by_date[dates_ext[j]].get(str(wid), 0.0) for j in range(i, i + ROLLING_DAYS))
        rolling.append(rec)

    # A failed sync leaves a hole; carrying the last total forward keeps the line flat across
    # it instead of dropping to zero, which would look like they stopped advertising.
    carried = {}
    for d in dates:
        rec = by_date[d]
        for p in people:
            k = str(p['id'])
            if k in rec:
                carried[k] = rec[k]
            elif k in carried:
                rec[k] = carried[k]

    added = sorted([{'id': p['id'], 'name': p['name'], 'side': p['side'],
                     'spend': last_seen.get(p['id'], 0.0) - first_seen.get(p['id'], 0.0)} for p in people
                    if last_seen.get(p['id'], 0.0) - first_seen.get(p['id'], 0.0) > 0], key=lambda a: -a['spend'])
    return {'days': days, 'daily': list(by_date.values()), 'daily_added': daily_added, 'rolling': rolling,
            'series': [{'key': str(p['id']), 'name': p['name'], 'side': p['side']} for p in people],
            'added': added, 'first_day': first_day}


# =============================================================================
# ROUTES
# =============================================================================

RANGES = (7, 30, 90)
VIEWS = [
    {'key': 'total', 'label': 'Total', 'title': 'Estimated spend this cycle',
     'blurb': 'One reading a day, taken by this app. The line only starts where the watch started'},
    {'key': 'added', 'label': 'Per day', 'title': 'Spend added each day',
     'blurb': 'The gap between one daily reading and the one before it. The first day has no bar, because there is nothing to compare it to.'},
    {'key': 'rolling', 'label': 'Last 7 days', 'title': 'Rolling week, worked out every day',
     'blurb': 'The same seven-day window Meta publishes, but for every single day. Each point is the seven days ending that day, so a push shows the day it starts and fades over the week after it.'},
]


def _who():
    return getattr(current_user, 'email', None) if current_user.is_authenticated else None


def _sum(rows):
    t = {'pages': 0, 'ads': 0, 'active_ads': 0, 'small_ads': 0, 'spend_lower': 0.0, 'spend_upper': 0.0, 'spend_estimate': 0.0}
    for p in rows:
        t['pages'] += 1
        for k in ('ads', 'active_ads', 'small_ads', 'spend_lower', 'spend_upper', 'spend_estimate'):
            t[k] += p[k] or 0
    t['range_label'] = range_label(t['spend_lower'], t['spend_upper'])
    return t


@admon_bp.route('/')
@require_feature_access(FEATURE)
def page():
    try:
        days = int(request.args.get('days', 30))
    except ValueError:
        days = 30
    if days not in RANGES:
        days = 30
    show_all = request.args.get('show') == 'all'
    view = request.args.get('view') if request.args.get('view') in ('added', 'rolling') else 'total'
    side = request.args.get('side') if request.args.get('side') in SIDES else None

    people = list_watches()
    trend = get_trend(days)
    ads = list_watched_ads(running_only=not show_all, side=side)
    reports = list_meta_reports()
    totals = _sum(people)
    oppose = _sum([p for p in people if p['side'] == 'oppose'])
    support = _sum([p for p in people if p['side'] == 'support'])
    in_window = {s: sum(a['spend'] for a in trend['added'] if a['side'] == s) for s in SIDES}
    current_view = next(v for v in VIEWS if v['key'] == view)
    chart_data = trend['daily_added'] if view == 'added' else trend['rolling'] if view == 'rolling' else trend['daily']

    def href(**over):
        q = {'days': days, 'show': 'all' if show_all else 'running', 'view': view, 'side': side or 'both', **over}
        out = {}
        if q['days'] != 30:
            out['days'] = q['days']
        if q['show'] == 'all':
            out['show'] = 'all'
        if q['view'] != 'total':
            out['view'] = q['view']
        if q['side'] in SIDES:
            out['side'] = q['side']
        return url_for('admon.page', **out)

    return render_template('meta/ad_monitor.html', people=people, trend=trend, ads=ads, reports=reports,
                           totals=totals, oppose=oppose, support=support, in_window=in_window, days=days,
                           ranges=RANGES, views=VIEWS, view=view, current_view=current_view, chart_data=chart_data,
                           show_all=show_all, side=side, sides=SIDES, side_labels=SIDE_LABELS, href=href,
                           has_token=has_ad_library_token(), token_problem=ad_library_token_problem(),
                           token_source=ad_library_token_source(), cycle_start=CYCLE_START,
                           watched=sum(1 for p in people if p['active']))


@admon_bp.route('/find', methods=['POST'])
@require_feature_access(FEATURE)
def find():
    """Pages behind a name. JSON, for the Watch dialog."""
    data = request.get_json(silent=True) or {}
    try:
        pages = find_pages(data.get('terms') or '')
    except MetaApiError as e:
        return jsonify({'ok': False, 'error': explain_archive_error(e)}), 400
    return jsonify({'ok': True, 'pages': pages})


def _read_watch_form():
    f = request.form
    side = f.get('side') if f.get('side') in SIDES else 'oppose'

    def money(name):
        v = (f.get(name) or '').replace('$', '').replace(',', '').strip()
        if not v:
            return None
        try:
            return round(float(v), 2)
        except ValueError:
            return None

    page_id = re.sub(r'\D', '', f.get('page_id') or '') or None
    return {'name': (f.get('name') or '').strip()[:160], 'side': side, 'office': (f.get('office') or '').strip()[:120] or None,
            'page_id': page_id, 'page_name': (f.get('page_name') or '').strip()[:200] or None,
            'search_terms': (f.get('search_terms') or '').strip()[:500] or None,
            'notes': (f.get('notes') or '').strip()[:2000] or None,
            'meta_total_spend': money('meta_total_spend'), 'meta_week_spend': money('meta_week_spend')}


def _record_meta_figures(cur, watch_id, total, week):
    """Meta's own figures, typed in. Kept as a series (one row per day read) and cached on the
    page row for display. Nothing is written when both are blank."""
    if total is None and week is None:
        return
    today = report_today()
    cur.execute("""INSERT INTO ad_watch_meta_reports (watch_id, read_on, total_spend, week_spend)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (watch_id, read_on) DO UPDATE SET
                       total_spend = coalesce(EXCLUDED.total_spend, ad_watch_meta_reports.total_spend),
                       week_spend = coalesce(EXCLUDED.week_spend, ad_watch_meta_reports.week_spend)""",
                (watch_id, today, total, week))
    cur.execute("""UPDATE ad_watch_pages SET meta_total_spend = coalesce(%s, meta_total_spend),
                       meta_week_spend = coalesce(%s, meta_week_spend), meta_figures_on = %s WHERE id = %s""",
                (total, week, today, watch_id))


@admon_bp.route('/watch', methods=['POST'])
@require_feature_access(FEATURE)
def add_watch():
    d = _read_watch_form()
    if not d['name']:
        flash('Give the page a name.', 'danger')
        return redirect(url_for('admon.page'))
    if not d['page_id'] and not d['search_terms']:
        flash('Pick their Facebook Page, or give some words to search for.', 'danger')
        return redirect(url_for('admon.page'))
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        if d['page_id']:
            cur.execute("SELECT name FROM ad_watch_pages WHERE page_id = %s", (d['page_id'],))
            dup = cur.fetchone()
            if dup:
                flash(f'That Facebook Page is already watched as "{dup[0]}".', 'warning')
                return redirect(url_for('admon.page'))
        cur.execute("""INSERT INTO ad_watch_pages (name, side, office, page_id, page_name, search_terms, notes, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (d['name'], d['side'], d['office'], d['page_id'], d['page_name'], d['search_terms'], d['notes'], _who()))
        new_id = cur.fetchone()[0]
        _record_meta_figures(cur, new_id, d['meta_total_spend'], d['meta_week_spend'])
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)
    if has_ad_library_token():
        r = sync_watch(_watch_row(new_id))
        if r['ok']:
            flash(f'Now watching {d["name"]}: {r["ads"]} ad{"" if r["ads"] == 1 else "s"} found this cycle.', 'success')
        else:
            flash(f'{d["name"]} added, but the first read failed: {r.get("error")}', 'warning')
    else:
        flash(f'{d["name"]} added. Nothing will be pulled until a token is set.', 'success')
    return redirect(url_for('admon.page'))


@admon_bp.route('/watch/<int:watch_id>', methods=['POST'])
@require_feature_access(FEATURE)
def update_watch(watch_id):
    row = _watch_row(watch_id) or abort(404)
    d = _read_watch_form()
    if not d['name']:
        flash('Give the page a name.', 'danger')
        return redirect(url_for('admon.page'))
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        if d['page_id']:
            cur.execute("SELECT name FROM ad_watch_pages WHERE page_id = %s AND id <> %s", (d['page_id'], watch_id))
            dup = cur.fetchone()
            if dup:
                flash(f'That Facebook Page is already watched as "{dup[0]}".', 'warning')
                return redirect(url_for('admon.page'))
        cur.execute("""UPDATE ad_watch_pages SET name=%s, side=%s, office=%s, page_id=%s, page_name=%s, search_terms=%s,
                           notes=%s WHERE id=%s""",
                    (d['name'], d['side'], d['office'], d['page_id'], d['page_name'], d['search_terms'], d['notes'], watch_id))
        _record_meta_figures(cur, watch_id, d['meta_total_spend'], d['meta_week_spend'])
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)
    page_changed = (row['page_id'] or '') != (d['page_id'] or '') or (row['search_terms'] or '') != (d['search_terms'] or '')
    if page_changed and has_ad_library_token():
        r = sync_watch(_watch_row(watch_id))
        flash(f'{d["name"]} updated and read again: {r["ads"]} ads.' if r['ok'] else f'{d["name"]} updated, but the read failed: {r.get("error")}',
              'success' if r['ok'] else 'warning')
    else:
        flash(f'{d["name"]} updated.', 'success')
    return redirect(url_for('admon.page'))


@admon_bp.route('/watch/<int:watch_id>/active', methods=['POST'])
@require_feature_access(FEATURE)
def set_active(watch_id):
    active = request.form.get('active') == '1'
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE ad_watch_pages SET active = %s WHERE id = %s", (active, watch_id))
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)
    flash('Watching again.' if active else 'Paused. Their ads stay on file; nothing new is pulled.', 'success')
    return redirect(url_for('admon.page'))


@admon_bp.route('/watch/<int:watch_id>/delete', methods=['POST'])
@require_feature_access(FEATURE)
def delete_watch(watch_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM ad_watch_pages WHERE id = %s RETURNING name", (watch_id,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)
    flash(f'{row[0]} removed, with their collected ads and readings.' if row else 'Already gone.', 'success')
    return redirect(url_for('admon.page'))


@admon_bp.route('/watch/<int:watch_id>/sync', methods=['POST'])
@require_feature_access(FEATURE)
def sync_one(watch_id):
    row = _watch_row(watch_id) or abort(404)
    r = sync_watch(row)
    if r['ok']:
        flash(f'{row["name"]}: {r["ads"]} ads, Meta says {range_label(r["spend_lower"], r["spend_upper"])}.'
              + (' The archive had more pages than one sync reads, so this is a floor.' if r['truncated'] else ''), 'success')
    else:
        flash(f'{row["name"]} did not sync: {r.get("error")}', 'danger')
    return redirect(url_for('admon.page'))


@admon_bp.route('/sync-all', methods=['POST'])
@require_feature_access(FEATURE)
def sync_all():
    s = sync_all_watches()
    if s['failed']:
        flash(f'{s["synced"]} of {s["total"]} pages read. Failed: '
              + '; '.join(f'{r["name"]}: {r.get("error")}' for r in s['results'] if not r['ok']), 'warning')
    else:
        flash(f'All {s["total"]} watched pages read.', 'success')
    return redirect(url_for('admon.page'))


@admon_bp.route('/cron/sync', methods=['GET', 'POST'])
def cron_sync():
    """Hourly read of every watched page. Bearer CRON_SECRET; CSRF-exempt in app.py."""
    if not cron_authorized():
        return jsonify({'error': 'Unauthorized'}), 401
    started = datetime.now(timezone.utc)
    s = sync_all_watches()
    return jsonify({'ok': s['failed'] == 0, 'at': started.isoformat(),
                    'ms': int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                    'total': s['total'], 'synced': s['synced'], 'failed': s['failed'],
                    'results': [{'name': r['name'], 'ok': r['ok'], 'ads': r['ads'], 'truncated': r['truncated'],
                                 'error': r.get('error')} for r in s['results']]})


def register_cli(app):
    @app.cli.command('ad-monitor-sync')
    def ad_monitor_sync_cmd():
        """Read every watched page from the Ad Library. For cron: `flask ad-monitor-sync`."""
        import click
        s = sync_all_watches()
        for r in s['results']:
            click.echo(f'{"ok " if r["ok"] else "ERR"} {r["name"]}: {r["ads"]} ads, '
                       f'{range_label(r["spend_lower"], r["spend_upper"])}' + (f' - {r["error"]}' if r.get('error') else ''))
        click.echo(f'{s["synced"]} of {s["total"]} read, {s["failed"]} failed.')
