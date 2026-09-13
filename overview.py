"""Where things stand: one page across the whole operation.

Everything else on the private side answers one question well. This answers the question you
actually ask at 11pm, which is "what is waiting on me and is anything on fire".

Two rules shaped it:

  * Only show a number you would act on. A count of rows in a table is not news. "31 walkbooks
    built" tells you nothing; "5 walkbook requests waiting" is a job.
  * A number that is stale or unresolved says so. filings.result drives every roster, so a
    district still sitting at 'pending' is invisible to the mail, the palm cards and the
    check-in chase. That is worth saying out loud rather than quietly excluding.

Read-only. Nothing here writes, so it can never be the thing that breaks a deadline.
"""
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

get_db_connection = None
release_db_connection = None

ELECTION_DAY = date(2026, 11, 3)
YEAR = 2026
OFFICE = 'State Representative'


def init_overview(db_conn_func, db_release_func):
    global get_db_connection, release_db_connection
    get_db_connection = db_conn_func
    release_db_connection = db_release_func


def _scalar(cur, sql, args=None, default=0):
    try:
        cur.execute(sql, args or ())
        r = cur.fetchone()
        return (r[0] if r and r[0] is not None else default)
    except Exception as e:
        logger.info('overview: query failed (%s): %s', str(e)[:80], sql.split('\n')[0][:60])
        return default


def _rows(cur, sql, args=None):
    try:
        cur.execute(sql, args or ())
        return cur.fetchall()
    except Exception as e:
        logger.info('overview: query failed (%s)', str(e)[:120])
        return []


# =============================================================================
# THE QUEUE
# =============================================================================

def _queue(cur):
    """Things sitting still that need a person. Ordered by how stuck they are, not by type.

    A request that has been 'processing' for hours is worse than one that arrived a minute ago:
    the first means a build died, the second means nobody has looked yet. They are listed
    separately for that reason.
    """
    q = []

    for status, label, tone in (('new', 'waiting to be approved', 'wait'),
                                ('processing', 'stuck part way through', 'bad'),
                                ('failed', 'failed', 'bad')):
        n = _scalar(cur, "SELECT count(*) FROM voterlist_requests WHERE status=%s", (status,))
        if n:
            oldest = _scalar(cur, """SELECT min(created_at) FROM voterlist_requests
                                      WHERE status=%s""", (status,), None)
            q.append({'n': n, 'what': 'voter list' + ('s' if n != 1 else ''),
                      'state': label, 'tone': tone, 'since': oldest,
                      'href': '/private/spend-plan', 'kind': 'voterlist'})

    for status, label, tone in (('new', 'waiting to be approved', 'wait'),):
        n = _scalar(cur, "SELECT count(*) FROM walkbook_requests WHERE status=%s", (status,))
        if n:
            oldest = _scalar(cur, """SELECT min(created_at) FROM walkbook_requests
                                      WHERE status=%s""", (status,), None)
            q.append({'n': n, 'what': 'walkbook request' + ('s' if n != 1 else ''),
                      'state': label, 'tone': tone, 'since': oldest,
                      'href': None, 'kind': 'walkbook'})

    n = _scalar(cur, """SELECT count(*) FROM consult_requests
                         WHERE status NOT IN ('approved','declined')""")
    if n:
        q.append({'n': n, 'what': 'consult request' + ('s' if n != 1 else ''),
                  'state': 'waiting on a yes or no', 'tone': 'wait',
                  'since': _scalar(cur, """SELECT min(created_at) FROM consult_requests
                                            WHERE status NOT IN ('approved','declined')""",
                                   None, None),
                  'href': None, 'kind': 'consult'})

    n = _scalar(cur, """SELECT count(*) FROM portal_registrations WHERE status='pending'""")
    if n:
        q.append({'n': n, 'what': 'portal registration' + ('s' if n != 1 else ''),
                  'state': 'waiting to be matched', 'tone': 'wait', 'since': None,
                  'href': None, 'kind': 'registration'})

    # A site submitted and still a draft is a candidate waiting on us before they can print
    # anything with their web address on it. reviewed_at is never stamped by the review flow,
    # so it cannot be the test: going by that alone counts every live site as unreviewed.
    n = _scalar(cur, """SELECT count(*) FROM ws_submissions
                         WHERE submitted_at IS NOT NULL AND status = 'draft'""")
    if n:
        q.append({'n': n, 'what': 'website' + ('s' if n != 1 else '') + ' submitted',
                  'state': 'still a draft, waiting on review', 'tone': 'wait',
                  'since': _scalar(cur, """SELECT min(submitted_at) FROM ws_submissions
                                            WHERE submitted_at IS NOT NULL
                                              AND status = 'draft'""", None, None),
                  'href': None, 'kind': 'website'})

    order = {'bad': 0, 'wait': 1}
    q.sort(key=lambda x: (order.get(x['tone'], 2), -x['n']))
    return q


# =============================================================================
# THE BALLOT
# =============================================================================

def _ballot(cur):
    """Who is on the November ballot, and where the roster is not yet trustworthy."""
    res = dict(_rows(cur, """SELECT result, count(*) FROM filings
                              WHERE election_year=%s AND office=%s AND party='R'
                              GROUP BY 1""", (YEAR, OFFICE)))
    won = res.get('won', 0)
    lost = res.get('lost', 0)
    pending = res.get('pending', 0)
    nominees = won + pending          # anything not beaten is on the ballot

    seats = _scalar(cur, """SELECT COALESCE(sum(seat_count), 0) FROM (
                              SELECT DISTINCT full_district_code, seat_count FROM districts
                            ) d""")

    # Districts where we have fewer R names than there are seats to fill: every one of those
    # is a seat conceded before a vote is cast.
    short = _rows(cur, """
        WITH seats AS (
          SELECT DISTINCT full_district_code AS code, seat_count FROM districts
        ), r AS (
          SELECT district_code AS code, count(*) AS n FROM filings
           WHERE election_year=%s AND office=%s AND party='R' AND result <> 'lost'
           GROUP BY 1
        )
        SELECT s.code, s.seat_count, COALESCE(r.n, 0)
          FROM seats s LEFT JOIN r ON r.code = s.code
         WHERE COALESCE(r.n, 0) < s.seat_count
         ORDER BY (s.seat_count - COALESCE(r.n, 0)) DESC, s.code
    """, (YEAR, OFFICE))

    dshort = _scalar(cur, """
        WITH seats AS (
          SELECT DISTINCT full_district_code AS code, seat_count FROM districts
        ), d AS (
          SELECT district_code AS code, count(*) AS n FROM filings
           WHERE election_year=%s AND office=%s AND party='D' AND result <> 'lost'
           GROUP BY 1
        )
        SELECT count(*) FROM seats s LEFT JOIN d ON d.code = s.code
         WHERE COALESCE(d.n, 0) < s.seat_count
    """, (YEAR, OFFICE))

    return {'nominees': nominees, 'won': won, 'lost': lost, 'pending': pending,
            'seats': seats,
            'r_short_districts': len(short),
            'r_short_seats': sum(s - n for _, s, n in short),
            'd_short_districts': dshort,
            'no_email': _scalar(cur, """
                SELECT count(*) FROM filings f JOIN candidates c ON c.candidate_id=f.candidate_id
                 WHERE f.election_year=%s AND f.office=%s AND f.party='R' AND f.result <> 'lost'
                   AND COALESCE(NULLIF(TRIM(c.email),''), NULLIF(TRIM(c.email1),''),
                                NULLIF(TRIM(c.email2),'')) IS NULL""", (YEAR, OFFICE))}


# =============================================================================
# THE CANDIDATES
# =============================================================================

def _candidates(cur):
    """What the nominees have told us they need, and how many have told us anything."""
    base = """FROM filings f JOIN candidates c ON c.candidate_id = f.candidate_id
               LEFT JOIN candidate_campaign_progress p ON p.candidate_id = c.candidate_id
              WHERE f.election_year=%s AND f.office=%s AND f.party='R' AND f.result <> 'lost'"""
    args = (YEAR, OFFICE)

    total = _scalar(cur, "SELECT count(DISTINCT c.candidate_id) " + base, args)
    done = _scalar(cur, "SELECT count(DISTINCT c.candidate_id) " + base +
                   " AND p.intake_submitted_at IS NOT NULL", args)

    def needs(col):
        return _scalar(cur, "SELECT count(DISTINCT c.candidate_id) " + base +
                       " AND p.%s IS FALSE" % col, args)

    return {
        'total': total, 'done': done,
        'pct': round(done * 100.0 / total) if total else 0,
        'needs_signs': needs('signs_have'),
        'needs_lit': needs('lit_have'),
        'needs_headshot': needs('headshot_have'),
        'needs_walkbooks': needs('walkbooks_have'),
        'never_logged_in': _scalar(cur, "SELECT count(DISTINCT c.candidate_id) " + base +
                                   " AND c.last_login IS NULL", args),
        'no_password': _scalar(cur, "SELECT count(DISTINCT c.candidate_id) " + base +
                               " AND c.password_hash IS NULL", args),
        'raised': _scalar(cur, "SELECT COALESCE(sum(p.fundraising_amount),0) " + base, args),
        'cash': _scalar(cur, "SELECT COALESCE(sum(p.cash_on_hand),0) " + base, args),
    }


# =============================================================================
# THE PROGRAM
# =============================================================================

def _program(cur):
    """What the plan commits us to, and what has actually been made."""
    budget = _scalar(cur, "SELECT amount FROM spend_budget WHERE key='program'", None, 0)

    # Cost of the plan as it stands. Quantity alone is not cost: a mail quantity is the number
    # of DROPS, so it has to be multiplied by the households in that universe and then by the
    # rate. Leaving the universe out understated the whole program by nearly half, reporting
    # $294,912 against the planner's $576,362 for the same rows.
    #
    # Which universe supplies the size depends on the row: 'base' uses the district's own mask
    # in district_universe, everything else uses the modelled gotv/persuade counts. This is the
    # same arithmetic as cost() in the spend plan front end.
    committed = _scalar(cur, """
        WITH sized AS (
          SELECT i.qty, t.unit, COALESCE(i.rate_override, t.rate, 0) AS rate,
                 CASE WHEN i.universe = 'base' THEN du.households ELSE mu.households END AS households,
                 CASE WHEN i.universe = 'base' THEN du.cells      ELSE mu.cells      END AS cells
            FROM district_spend_item i
            JOIN spend_tactic t  ON t.tactic_key = i.tactic_key
            JOIN district_spend s ON s.district_code = i.district_code
            LEFT JOIN district_universe du
                   ON du.district_code = i.district_code AND du.mask = s.mask
            LEFT JOIN district_model_universe mu
                   ON mu.district_code = i.district_code AND mu.uni = i.universe
           WHERE s.include
        )
        SELECT COALESCE(sum(
          CASE WHEN unit = 'dollars'       THEN qty
               WHEN unit = 'per_household' THEN qty * COALESCE(households, 0) * rate
               WHEN unit = 'per_cell'      THEN qty * COALESCE(cells, 0) * rate
               ELSE qty * rate END), 0) FROM sized""")

    pieces = dict(_rows(cur, "SELECT status, count(*) FROM spend_piece GROUP BY 1"))
    next_drop = _scalar(cur, """SELECT min(drop_date) FROM spend_piece
                                 WHERE drop_date >= CURRENT_DATE""", None, None)
    next_drop_pieces = _scalar(cur, """SELECT count(*) FROM spend_piece
                                        WHERE drop_date = (SELECT min(drop_date) FROM spend_piece
                                                            WHERE drop_date >= CURRENT_DATE)""")

    districts_in = _scalar(cur, "SELECT count(*) FROM district_spend WHERE include")
    # A district that is in the plan but has no piece made for it is a promise with nothing
    # behind it yet.
    no_pieces = _scalar(cur, """
        SELECT count(*) FROM district_spend s
         WHERE s.include
           AND NOT EXISTS (SELECT 1 FROM spend_piece_district pd
                            WHERE pd.district_code = s.district_code)""")

    unbilled = _scalar(cur, """SELECT count(*) FROM spend_piece
                                WHERE COALESCE(invoice_status,'unbilled') = 'unbilled'
                                  AND status <> 'draft'""")

    return {'budget': float(budget or 0), 'committed': float(committed or 0),
            'pieces_draft': pieces.get('draft', 0),
            'pieces_scheduled': pieces.get('scheduled', 0),
            'pieces_printed': pieces.get('printed', 0),
            'pieces_delivered': pieces.get('delivered', 0),
            'next_drop': next_drop, 'next_drop_pieces': next_drop_pieces,
            'districts': districts_in, 'no_pieces': no_pieces, 'unbilled': unbilled}


# =============================================================================
# DIGITAL
# =============================================================================

def _digital(cur):
    """Meta, told the way the district pane tells it: planned, booked, spent are three
    different numbers and conflating them invents a crisis."""
    planned = _scalar(cur, """
        SELECT COALESCE(sum(i.qty), 0) FROM district_spend_item i
          JOIN district_spend s ON s.district_code = i.district_code
         WHERE s.include AND i.tactic_key = 'meta'""")
    offmeta = _scalar(cur, """
        SELECT COALESCE(sum(i.qty), 0) FROM district_spend_item i
          JOIN district_spend s ON s.district_code = i.district_code
         WHERE s.include AND i.tactic_key IN ('ctv','display')""")

    booked = _scalar(cur, """
        SELECT COALESCE(sum(a.lifetime_budget), 0)
          FROM meta_ad_sets a
         WHERE a.campaign_id IN (SELECT campaign_id FROM meta_campaign_district)""")
    spent = _scalar(cur, """
        SELECT COALESCE(sum(i.spend), 0) FROM meta_insights i
         WHERE i.level='campaign'
           AND i.campaign_id IN (SELECT campaign_id FROM meta_campaign_district)""")

    live = _scalar(cur, """SELECT count(*) FROM meta_ad_sets
                            WHERE campaign_id IN (SELECT campaign_id FROM meta_campaign_district)
                              AND start_time <= now() AND (end_time IS NULL OR end_time >= now())""")
    scheduled = _scalar(cur, """SELECT count(*) FROM meta_ad_sets
                                 WHERE campaign_id IN (SELECT campaign_id FROM meta_campaign_district)
                                   AND start_time > now()""")
    districts = _scalar(cur, "SELECT count(DISTINCT district_code) FROM meta_campaign_district")

    # Ads that are live and have shown to nobody. Scheduled flights are excluded: they have
    # not started, which is not the same as failing.
    dead = _scalar(cur, """
        SELECT count(*) FROM meta_ads a
          JOIN meta_campaign_district d ON d.campaign_id = a.campaign_id
          LEFT JOIN meta_ad_sets s ON s.adset_id = a.adset_id
         WHERE COALESCE(a.impressions, 0) = 0
           AND (s.start_time IS NULL OR s.start_time <= now())""")

    synced = _scalar(cur, """SELECT max(last_synced_at) FROM meta_ad_accounts WHERE active""",
                     None, None)
    sync_error = _scalar(cur, """SELECT last_sync_error FROM meta_ad_accounts
                                  WHERE active AND last_sync_error IS NOT NULL LIMIT 1""",
                         None, None)

    return {'planned': float(planned or 0), 'offmeta': float(offmeta or 0),
            'booked': float(booked or 0), 'spent': float(spent or 0),
            'live': live, 'scheduled': scheduled, 'districts': districts, 'dead': dead,
            'synced': synced, 'sync_error': sync_error}


# =============================================================================
# IS THE PLAN ACTUALLY HAPPENING
# =============================================================================

def _delivery(cur):
    """Per tactic: what the plan commits, and whether anything is actually running.

    The honest part is the third column. Mail and palm cards become pieces, and Meta becomes a
    linked campaign, so for those "nothing yet" is a real finding. Texts, streaming and display
    are bought outside this system and leave no record in it, so they are reported as untracked
    rather than as zero. A zero would read as "nobody bought it", which is a claim the data
    cannot support.
    """
    # Cost the plan the way the planner does: quantity times the size of ITS universe times
    # the rate. 'base' rows size off the district mask, gotv and persuade off the model.
    rows = _rows(cur, """
      WITH sized AS (
        SELECT i.tactic_key, i.district_code, i.qty, t.unit, t.label, t.sort_order,
               COALESCE(i.rate_override, t.rate, 0) AS rate,
               CASE WHEN i.universe = 'base' THEN du.households ELSE mu.households END AS hh,
               CASE WHEN i.universe = 'base' THEN du.cells      ELSE mu.cells      END AS cells
          FROM district_spend_item i
          JOIN district_spend s   ON s.district_code = i.district_code
          JOIN spend_tactic t     ON t.tactic_key = i.tactic_key
          LEFT JOIN district_universe du
                 ON du.district_code = i.district_code AND du.mask = s.mask
          LEFT JOIN district_model_universe mu
                 ON mu.district_code = i.district_code AND mu.uni = i.universe
         WHERE s.include AND i.qty > 0)
      SELECT tactic_key, label, count(DISTINCT district_code),
             SUM(CASE WHEN unit = 'dollars'       THEN qty
                      WHEN unit = 'per_household' THEN qty * COALESCE(hh, 0) * rate
                      WHEN unit = 'per_cell'      THEN qty * COALESCE(cells, 0) * rate
                      ELSE qty * rate END),
             MIN(sort_order)
        FROM sized GROUP BY 1, 2 ORDER BY 5""")

    # What is demonstrably under way, per tactic.
    made = dict(_rows(cur, """SELECT p.tactic_key, count(DISTINCT pd.district_code)
                                FROM spend_piece p
                                JOIN spend_piece_district pd ON pd.piece_id = p.id
                               GROUP BY 1"""))
    meta_live = _scalar(cur, """SELECT count(DISTINCT d.district_code)
                                  FROM meta_campaign_district d
                                  JOIN meta_ads a ON a.campaign_id = d.campaign_id
                                 WHERE COALESCE(a.impressions, 0) > 0""")
    meta_linked = _scalar(cur, "SELECT count(DISTINCT district_code) FROM meta_campaign_district")

    # Tactics that leave no trace in this database. Naming them is the point.
    ELSEWHERE = {
        'mms': 'sent through RevT, not recorded here',
        'ctv': 'bought outside this system',
        'display': 'bought outside this system',
        'palm_cards': 'tracked on the palm card sheet',
    }

    out = []
    for key, label, districts, dollars, _ in rows:
        if key == 'meta':
            live, note = meta_live, ('%d linked, %d delivering' % (meta_linked, meta_live))
        elif key in ELSEWHERE:
            live, note = None, ELSEWHERE[key]
        else:
            live, note = made.get(key, 0), None
        out.append({'key': key, 'label': label,
                    'districts': districts, 'dollars': float(dollars or 0),
                    'live': live, 'note': note,
                    'gap': (districts - live) if live is not None else None})

    untracked = sum(r['dollars'] for r in out if r['live'] is None and r['key'] != 'palm_cards')
    return {'rows': out, 'untracked': untracked,
            'pieces': dict(_rows(cur, "SELECT status, count(*) FROM spend_piece GROUP BY 1"))}


# =============================================================================
# SITES, MONEY, FIELD
# =============================================================================

def _sites(cur):
    s = dict(_rows(cur, "SELECT status, count(*) FROM ws_submissions GROUP BY 1"))
    return {'live': s.get('live', 0) + s.get('custom_domain_live', 0),
            'custom_domain': s.get('custom_domain_live', 0),
            'draft': s.get('draft', 0),
            'donations': float(_scalar(cur, """SELECT COALESCE(sum(amount_cents),0)/100.0
                                                 FROM ws_donations
                                                WHERE donation_status='succeeded'
                                                  AND refunded_at IS NULL""") or 0),
            'donors': _scalar(cur, """SELECT count(*) FROM ws_donations
                                       WHERE donation_status='succeeded'
                                         AND refunded_at IS NULL"""),
            'donations_7d': float(_scalar(cur, """SELECT COALESCE(sum(amount_cents),0)/100.0
                                                    FROM ws_donations
                                                   WHERE donation_status='succeeded'
                                                     AND refunded_at IS NULL
                                                     AND created_at > now() - interval '7 days'""") or 0)}


def _field(cur):
    return {
        'walkbooks_built': _scalar(cur, "SELECT count(*) FROM walkbook_requests WHERE status='built'"),
        'walkbook_districts': _scalar(cur, """SELECT count(DISTINCT district_code)
                                                FROM walkbook_requests WHERE status='built'"""),
        'lists_sent': _scalar(cur, "SELECT count(*) FROM voterlist_requests WHERE status='fulfilled'"),
        'consults': _scalar(cur, """SELECT count(*) FROM consult_requests
                                     WHERE status='approved' AND requested_start >= now()"""),
    }


def _recent(cur, limit=12):
    """What has actually happened, newest first. Useful for picking a thread back up."""
    return _rows(cur, """SELECT created_at, action_type, description
                           FROM activity_log
                          WHERE created_at > now() - interval '3 days'
                          ORDER BY created_at DESC LIMIT %s""", (limit,))


# =============================================================================

def gather():
    """One connection, one pass. Every section degrades to zeroes rather than 500ing the
    page: a dashboard that will not load because one table moved is worse than useless."""
    conn = get_db_connection(); cur = conn.cursor()
    try:
        data = {
            'today': date.today(),
            'days_left': max((ELECTION_DAY - date.today()).days, 0),
            'queue': _queue(cur),
            'ballot': _ballot(cur),
            'cands': _candidates(cur),
            'program': _program(cur),
            'digital': _digital(cur),
            'sites': _sites(cur),
            'field': _field(cur),
            'delivery': _delivery(cur),
            'recent': _recent(cur),
        }
        conn.rollback()          # read-only: drop the transaction, hold nothing
        return data
    finally:
        cur.close(); release_db_connection(conn)
