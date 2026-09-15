"""StackAdapt CTV delivery, read back into the tracker.

The plan holds CTV as dollars against a district and nothing read what those dollars actually
bought. This is the CTV half of the Meta pipeline sitting beside it, deliberately the same
shape: a daily insights table, a campaign-to-district link, and an account row carrying the
sync state so a stale pull is visible rather than silent.

The number worth having is ecpm. The plan costs CTV at a $45 CPM; these buys bid $110, which
is a ceiling and not a price, so nobody knows what the inventory clears at until it runs. That
is why every day is stored with its own ecpm rather than only a running total.

Two traps, both already paid for once:

  * The account matters. 44108 / advertiser 145826 is the live one with WRITE. The
    STACKADAPT_API_KEY in nh-civic-crm's env is account 35111, read-only, and it answers
    happily with the wrong advertiser's data rather than failing.
  * campaignDelivery returns a UNION. It is either a CampaignDeliveryOutcome or a Progress,
    so every query needs the inline fragment or it fails on "cannot query field on type".
    MoneyValue is a scalar, not an object; asking for `cost { amount }` is rejected.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

logger = logging.getLogger(__name__)

get_db_connection = None
release_db_connection = None

API = 'https://api.stackadapt.com/graphql'
ACCOUNT = '44108'
ADVERTISER = '145826'

# How far back to re-read on every sync. StackAdapt restates recent days as impressions
# settle, so a few days are refetched and upserted rather than trusted once.
LOOKBACK_DAYS = 7


def init_stackadapt(db_conn_func, db_release_func):
    global get_db_connection, release_db_connection
    get_db_connection = db_conn_func
    release_db_connection = db_release_func


def _token():
    """The CTV token. Env first, then the key file it was downloaded to.

    Only the 44108 key is accepted. STACKADAPT_API_KEY is deliberately not consulted: it is
    the old read-only account and using it silently returns another advertiser's numbers.
    """
    tok = (os.environ.get('STACKADAPT_CTV_TOKEN') or '').strip()
    if tok:
        return tok
    for path in ('/opt/nh-candidate-recruitment/.env', '/opt/nh-civic-crm/.env'):
        try:
            for line in open(path):
                if line.startswith('STACKADAPT_CTV_TOKEN='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    for path in (os.path.expanduser('~/Desktop/api-key-299852.txt'),
                 '/opt/nh-candidate-recruitment/stackadapt-key.json'):
        try:
            return json.load(open(path))['token']
        except Exception:
            continue
    return None


class StackAdaptError(RuntimeError):
    pass


def gql(query, **variables):
    """One GraphQL call, retrying transport failures.

    StackAdapt resets the connection partway through a long read often enough that treating it
    as a real failure would make the sync look broken when it is not.
    """
    tok = _token()
    if not tok:
        raise StackAdaptError('no StackAdapt CTV token available')
    body = json.dumps({'query': query, 'variables': variables}).encode()
    last = None
    for attempt in range(5):
        req = urllib.request.Request(API, data=body, headers={
            'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'})
        try:
            payload = json.load(urllib.request.urlopen(req, timeout=90))
            break
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < 4:
                last = 'HTTP %s' % e.code
                time.sleep(3 * (attempt + 1))
                continue
            raise StackAdaptError('HTTP %s: %s' % (e.code, e.read().decode()[:400]))
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last = str(e)[:120]
            if attempt == 4:
                raise StackAdaptError('transport: %s' % last)
            time.sleep(2 * (attempt + 1))
    else:
        raise StackAdaptError('gave up: %s' % last)
    if payload.get('errors'):
        raise StackAdaptError(json.dumps(payload['errors'])[:500])
    return payload['data']


# =============================================================================
# READ
# =============================================================================

CAMPAIGNS_Q = """
query($f: CampaignFilters) {
  campaigns(filterBy: $f, first: 100) {
    nodes { id name campaignStatus { state status } campaignGroup { id name } }
  }
}"""

# campaignDelivery is a union: CampaignDeliveryOutcome or Progress. MoneyValue is a scalar.
DELIVERY_Q = """
query($f: CampaignFilters, $d: DateRangeInput) {
  campaignDelivery(filterBy: $f, date: $d, granularity: DAILY, dataType: TABLE) {
    ... on CampaignDeliveryOutcome {
      records(first: 100) {
        pageInfo { hasNextPage endCursor }
        nodes {
          campaign { id name }
          granularity { time }
          metrics {
            cost impressionsBigint clicksBigint ecpm ecpc ctr
            videoStartsBigint videoCompletionsBigint frequency
          }
        }
      }
    }
  }
}"""


def linked_campaigns(cur):
    cur.execute("""SELECT campaign_id, district_code, candidate_name, group_id
                     FROM stackadapt_campaign_district ORDER BY district_code""")
    return [{'campaign_id': r[0], 'district_code': r[1], 'candidate': r[2], 'group_id': r[3]}
            for r in cur.fetchall()]


def _num(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sync(days=LOOKBACK_DAYS):
    """Pull the last `days` of daily delivery for every linked campaign and upsert it.

    Returns a summary rather than raising, so a caller running on a timer can log and carry on.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    started = date.today()
    written = 0
    try:
        camps = linked_campaigns(cur)
        if not camps:
            return {'ok': True, 'campaigns': 0, 'rows': 0, 'note': 'no campaigns linked yet'}
        ids = [c['campaign_id'] for c in camps]

        cur.execute("""UPDATE stackadapt_accounts SET last_attempt_at = now()
                        WHERE account_id = %s""", (ACCOUNT,))
        conn.commit()

        # Status first: a campaign stuck in creative review reports no delivery, and knowing
        # that is the difference between "not spending yet" and "something is broken".
        status = {}
        try:
            for n in gql(CAMPAIGNS_Q, f={'ids': ids})['campaigns']['nodes']:
                cs = n.get('campaignStatus') or {}
                status[str(n['id'])] = (n.get('name'), cs.get('state'), cs.get('status'),
                                        str((n.get('campaignGroup') or {}).get('id') or ''))
        except StackAdaptError as e:
            logger.info('stackadapt: status read failed: %s', str(e)[:160])

        since = (started - timedelta(days=days)).isoformat()
        data = gql(DELIVERY_Q, f={'ids': ids},
                   d={'from': since, 'to': started.isoformat()})
        outcome = data.get('campaignDelivery') or {}
        nodes = ((outcome.get('records') or {}).get('nodes')) or []

        for n in nodes:
            cid = str((n.get('campaign') or {}).get('id') or '')
            when = ((n.get('granularity') or {}).get('time') or '')[:10]
            if not cid or not when:
                continue
            m = n.get('metrics') or {}
            nm, state, stat, gid = status.get(cid, (None, None, None, None))
            cur.execute("""
                INSERT INTO stackadapt_insights
                    (account_id, advertiser_id, level, campaign_id, campaign_name,
                     campaign_state, campaign_status, group_id, date,
                     cost, impressions, clicks, ecpm, ecpc, ctr,
                     video_starts, video_completes, frequency, fetched_at)
                VALUES (%s,%s,'campaign',%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                ON CONFLICT (campaign_id, level, date) DO UPDATE SET
                    campaign_name = EXCLUDED.campaign_name,
                    campaign_state = EXCLUDED.campaign_state,
                    campaign_status = EXCLUDED.campaign_status,
                    cost = EXCLUDED.cost, impressions = EXCLUDED.impressions,
                    clicks = EXCLUDED.clicks, ecpm = EXCLUDED.ecpm, ecpc = EXCLUDED.ecpc,
                    ctr = EXCLUDED.ctr, video_starts = EXCLUDED.video_starts,
                    video_completes = EXCLUDED.video_completes,
                    frequency = EXCLUDED.frequency, fetched_at = now()""",
                (ACCOUNT, ADVERTISER, cid, nm or (n.get('campaign') or {}).get('name'),
                 state, stat, gid or None, when,
                 _num(m.get('cost')) or 0,
                 int(_num(m.get('impressionsBigint')) or 0),
                 int(_num(m.get('clicksBigint')) or 0),
                 _num(m.get('ecpm')), _num(m.get('ecpc')), _num(m.get('ctr')),
                 int(_num(m.get('videoStartsBigint')) or 0),
                 int(_num(m.get('videoCompletionsBigint')) or 0),
                 _num(m.get('frequency'))))
            written += 1

        cur.execute("""UPDATE stackadapt_accounts
                          SET last_synced_at = now(), last_sync_error = NULL,
                              campaigns_seen = %s, rows_written = %s
                        WHERE account_id = %s""", (len(camps), written, ACCOUNT))
        conn.commit()
        return {'ok': True, 'campaigns': len(camps), 'rows': written,
                'pending_review': sum(1 for v in status.values() if v[1] == 'PENDING')}
    except Exception as e:
        conn.rollback()
        try:
            cur.execute("""UPDATE stackadapt_accounts
                              SET last_sync_error = %s WHERE account_id = %s""",
                        (str(e)[:900], ACCOUNT))
            conn.commit()
        except Exception:
            conn.rollback()
        logger.warning('stackadapt sync failed: %s', str(e)[:200])
        return {'ok': False, 'error': str(e)[:300]}
    finally:
        cur.close()
        release_db_connection(conn)


# =============================================================================
# THE TIMER
# =============================================================================

# Same pattern as the Meta sync next door: one daemon thread per gunicorn worker, and the work
# itself serialised through a Postgres advisory lock so whichever worker wakes first does the
# pull and the rest go back to sleep. A different lock number, or the two syncs would block
# each other for no reason.
AUTO_SYNC_LOCK = 0x53544143_4B414450          # "STAC" "KADP"
AUTO_SYNC_EVERY_S = 15 * 60
AUTO_SYNC_STALE_MIN = 55
_auto_thread = None


def auto_sync_enabled():
    return (os.environ.get('STACKADAPT_AUTO_SYNC') or '1').strip() \
        not in ('0', 'false', 'no', 'off')


def start_auto_sync():
    import threading
    global _auto_thread
    if not auto_sync_enabled() or _auto_thread is not None:
        return
    _auto_thread = threading.Thread(target=_loop, name='stackadapt-auto-sync', daemon=True)
    _auto_thread.start()


def _loop():
    time.sleep(120)   # let the app start, and stagger against the Meta sync
    while True:
        try:
            auto_sync_once()
        except Exception:
            logger.exception('stackadapt auto sync failed')
        time.sleep(AUTO_SYNC_EVERY_S)


def auto_sync_once():
    """Pull if nothing has been attempted in the last 55 minutes and the lock is free."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (AUTO_SYNC_LOCK,))
        if not cur.fetchone()[0]:
            return {'lock': False}
        try:
            cur.execute("""SELECT count(*) FROM stackadapt_accounts
                            WHERE active AND (last_attempt_at IS NULL
                              OR last_attempt_at < now() - make_interval(mins => %s))""",
                        (AUTO_SYNC_STALE_MIN,))
            due = cur.fetchone()[0]
            cur.close()
            release_db_connection(conn)
            conn = None
            if not due:
                return {'lock': True, 'due': 0}
            return {'lock': True, 'due': due, 'result': sync()}
        finally:
            if conn is not None:
                c2 = conn.cursor()
                c2.execute("SELECT pg_advisory_unlock(%s)", (AUTO_SYNC_LOCK,))
                c2.close()
    finally:
        if conn is not None:
            release_db_connection(conn)


# =============================================================================
# PLANNED AGAINST ACTUAL
# =============================================================================

def by_district():
    """CTV per district: what was planned, what has been spent, and the CPM gap.

    The plan prices CTV at spend_tactic.rate, a $45 CPM. Planned impressions are derived from
    that rate, so the comparison against the real ecpm is the honest one: at $45 the money buys
    roughly two and a half times what it buys at the $110 bid ceiling, and the truth is
    somewhere between and only measurable once it runs.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT rate FROM spend_tactic WHERE tactic_key = 'ctv'")
        row = cur.fetchone()
        plan_cpm = float(row[0]) if row and row[0] else None

        cur.execute("""
            SELECT l.district_code, l.candidate_name, l.campaign_id,
                   MAX(i.campaign_state), MAX(i.campaign_status),
                   COALESCE(SUM(i.cost), 0), COALESCE(SUM(i.impressions), 0),
                   COALESCE(SUM(i.clicks), 0),
                   COALESCE(SUM(i.video_completes), 0),
                   MIN(i.date), MAX(i.date),
                   COALESCE((SELECT SUM(si.qty) FROM district_spend_item si
                              JOIN district_spend s ON s.district_code = si.district_code
                             WHERE si.district_code = l.district_code
                               AND si.tactic_key = 'ctv' AND s.include), 0)
              FROM stackadapt_campaign_district l
              LEFT JOIN stackadapt_insights i ON i.campaign_id = l.campaign_id
             GROUP BY l.district_code, l.candidate_name, l.campaign_id
             ORDER BY l.district_code""")
        rows = []
        for (code, cand, cid, state, stat, cost, impr, clicks, done,
             first, last, planned) in cur.fetchall():
            cost = float(cost or 0)
            impr = int(impr or 0)
            planned = float(planned or 0)
            actual_cpm = (cost / impr * 1000) if impr else None
            rows.append({
                'district': code, 'candidate': cand, 'campaign_id': cid,
                'state': state, 'status': stat,
                'planned': planned, 'spent': cost,
                'impressions': impr, 'clicks': clicks, 'completes': int(done or 0),
                'first_day': first, 'last_day': last,
                'plan_cpm': plan_cpm, 'actual_cpm': actual_cpm,
                # What the planned dollars buy at each price. The gap is the point.
                'impressions_at_plan': (planned / plan_cpm * 1000) if plan_cpm else None,
                'impressions_at_actual': (planned / actual_cpm * 1000) if actual_cpm else None,
            })
        return {'plan_cpm': plan_cpm, 'rows': rows,
                'planned_total': sum(r['planned'] for r in rows),
                'spent_total': sum(r['spent'] for r in rows),
                'impressions_total': sum(r['impressions'] for r in rows)}
    finally:
        cur.close()
        release_db_connection(conn)
