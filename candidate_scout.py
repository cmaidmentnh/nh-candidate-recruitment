"""
Candidate Scout Module
Finds potential NH State Rep candidates by mining FEC donor data, voter file,
GenCourt testimony, and local news. Super admin only.
"""

import os
import json
import logging
import re
from functools import wraps
from datetime import datetime, date
from decimal import Decimal

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

scout_bp = Blueprint('scout', __name__, url_prefix='/scout')

# Module-level references set by init function
get_db_connection = None
release_db_connection = None
get_voter_db_connection = None
release_voter_db_connection = None
is_super_admin_func = None
SUPER_ADMIN_EMAIL = None


def init_candidate_scout(db_conn_func, db_release_func, voter_conn_func,
                         voter_release_func, super_admin_func, super_admin_email):
    """Initialize the module with database functions from main app."""
    global get_db_connection, release_db_connection, get_voter_db_connection
    global release_voter_db_connection, is_super_admin_func, SUPER_ADMIN_EMAIL
    get_db_connection = db_conn_func
    release_db_connection = db_release_func
    get_voter_db_connection = voter_conn_func
    release_voter_db_connection = voter_release_func
    is_super_admin_func = super_admin_func
    SUPER_ADMIN_EMAIL = super_admin_email


def scout_admin_required(f):
    """Decorator: super admin only access."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if current_user.email.lower() != SUPER_ADMIN_EMAIL.lower():
            flash('Access denied.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def recalc_composite_score(cur, prospect_id):
    """Recalculate composite score from all signals."""
    cur.execute("""
        UPDATE scout_prospects SET composite_score = COALESCE((
            SELECT SUM(signal_score) FROM scout_signals WHERE prospect_id = %s
        ), 0), updated_at = CURRENT_TIMESTAMP WHERE id = %s
    """, (prospect_id, prospect_id))


def lookup_district_for_city(cur, city):
    """Look up district_code from districts table by town/city name."""
    if not city:
        return None
    cur.execute("""
        SELECT full_district_code FROM districts
        WHERE UPPER(town) = UPPER(%s) LIMIT 1
    """, (city.strip(),))
    row = cur.fetchone()
    return row[0] if row else None


def match_voter_file(voter_cur, first_name, last_name, city=None):
    """Try to find a person in the statewide checklist. Returns dict or None."""
    if city:
        voter_cur.execute("""
            SELECT id_voter, nm_first, nm_last, cd_party, ad_str1, ad_city, ad_zip5, county, ward
            FROM statewidechecklist
            WHERE UPPER(nm_last) = UPPER(%s) AND UPPER(nm_first) = UPPER(%s)
              AND UPPER(ad_city) = UPPER(%s)
            LIMIT 1
        """, (last_name, first_name, city))
    else:
        voter_cur.execute("""
            SELECT id_voter, nm_first, nm_last, cd_party, ad_str1, ad_city, ad_zip5, county, ward
            FROM statewidechecklist
            WHERE UPPER(nm_last) = UPPER(%s) AND UPPER(nm_first) = UPPER(%s)
            LIMIT 1
        """, (last_name, first_name))
    row = voter_cur.fetchone()
    if not row:
        return None
    return {
        'voter_id': row[0], 'first_name': row[1], 'last_name': row[2],
        'party': row[3], 'address': row[4], 'city': row[5],
        'zip': row[6], 'county': row[7], 'ward': row[8]
    }


# =============================================================================
# DASHBOARD
# =============================================================================

@scout_bp.route('/')
@scout_admin_required
def dashboard():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Stats
        cur.execute("SELECT COUNT(*) FROM scout_district_targets WHERE empty_seats > 0")
        target_districts = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM scout_prospects WHERE review_status NOT IN ('dismissed', 'promoted')")
        total_prospects = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM scout_prospects WHERE review_status = 'contacted'")
        contacted = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM scout_prospects WHERE review_status = 'interested'")
        interested = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM scout_prospects WHERE review_status = 'promoted'")
        promoted = cur.fetchone()[0]

        # Top prospects by score
        cur.execute("""
            SELECT sp.id, sp.first_name, sp.last_name, sp.city, sp.district_code,
                   sp.composite_score, sp.review_status, sp.voter_party,
                   COUNT(ss.id) as signal_count
            FROM scout_prospects sp
            LEFT JOIN scout_signals ss ON ss.prospect_id = sp.id
            WHERE sp.review_status NOT IN ('dismissed', 'promoted')
            GROUP BY sp.id
            ORDER BY sp.composite_score DESC
            LIMIT 15
        """)
        top_prospects = cur.fetchall()

        # Priority districts (tier 1, top 12)
        cur.execute("""
            SELECT district_code, county_name, towns, seat_count, confirmed_count,
                   empty_seats, pvi, pvi_rating, prospect_count
            FROM scout_district_targets
            WHERE empty_seats > 0
            ORDER BY priority_tier, pvi DESC
            LIMIT 12
        """)
        priority_districts = cur.fetchall()

        # Recent scans
        cur.execute("""
            SELECT id, scan_type, status, prospects_found, prospects_new,
                   signals_added, started_at, completed_at
            FROM scout_scans ORDER BY started_at DESC LIMIT 5
        """)
        recent_scans = cur.fetchall()

        return render_template('scout/scout_dashboard.html',
            target_districts=target_districts,
            total_prospects=total_prospects,
            contacted=contacted,
            interested=interested,
            promoted=promoted,
            top_prospects=top_prospects,
            priority_districts=priority_districts,
            recent_scans=recent_scans
        )
    finally:
        cur.close()
        release_db_connection(conn)


# =============================================================================
# DISTRICTS
# =============================================================================

@scout_bp.route('/districts')
@scout_admin_required
def districts():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT district_code, county_name, towns, seat_count, confirmed_count,
                   empty_seats, pvi, pvi_rating, priority_tier, prospect_count, last_scanned_at
            FROM scout_district_targets
            WHERE empty_seats > 0
            ORDER BY priority_tier, pvi DESC
        """)
        rows = cur.fetchall()

        tiers = {1: [], 2: [], 3: []}
        for r in rows:
            tier = r[8]
            tiers.setdefault(tier, []).append(r)

        return render_template('scout/scout_districts.html', tiers=tiers, total=len(rows))
    finally:
        cur.close()
        release_db_connection(conn)


@scout_bp.route('/districts/refresh', methods=['POST'])
@scout_admin_required
def refresh_districts():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            WITH district_summary AS (
                SELECT d.full_district_code, d.county_name,
                       STRING_AGG(DISTINCT d.town, ', ' ORDER BY d.town) as towns,
                       MAX(d.seat_count) as seats, MAX(d.pvi) as pvi, MAX(d.pvi_rating) as rating
                FROM districts d GROUP BY d.full_district_code, d.county_name
            ),
            confirmed AS (
                SELECT ces.district_code, COUNT(*) as cnt
                FROM candidate_election_status ces
                WHERE ces.election_year = 2026 AND ces.status = 'Confirmed'
                GROUP BY ces.district_code
            ),
            prospect_counts AS (
                SELECT district_code, COUNT(*) as cnt
                FROM scout_prospects
                WHERE review_status NOT IN ('dismissed', 'promoted')
                GROUP BY district_code
            )
            SELECT ds.full_district_code, ds.county_name, ds.towns,
                   ds.seats, COALESCE(cf.cnt, 0),
                   ds.seats - COALESCE(cf.cnt, 0),
                   ds.pvi, ds.rating, COALESCE(pc.cnt, 0)
            FROM district_summary ds
            LEFT JOIN confirmed cf ON ds.full_district_code = cf.district_code
            LEFT JOIN prospect_counts pc ON ds.full_district_code = pc.district_code
            WHERE ds.seats - COALESCE(cf.cnt, 0) > 0
        """)
        rows = cur.fetchall()

        # Clear and rebuild
        cur.execute("DELETE FROM scout_district_targets")
        for row in rows:
            district_code, county, towns, seats, confirmed, empty, pvi, rating, prospects = row
            pvi_float = float(pvi) if pvi else 0
            if pvi_float >= 5:
                tier = 1
            elif pvi_float >= 0:
                tier = 2
            else:
                tier = 3

            cur.execute("""
                INSERT INTO scout_district_targets
                    (district_code, county_name, towns, seat_count, confirmed_count,
                     empty_seats, pvi, pvi_rating, priority_tier, prospect_count, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """, (district_code, county, towns, seats, confirmed, empty, pvi, rating, tier, prospects))

        conn.commit()
        flash('District targets refreshed.', 'success')
    except Exception as e:
        conn.rollback()
        logger.error(f"Error refreshing districts: {e}")
        flash(f'Error: {e}', 'danger')
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for('scout.districts'))


@scout_bp.route('/district/<path:district_code>')
@scout_admin_required
def district_detail(district_code):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # District info
        cur.execute("""
            SELECT district_code, county_name, towns, seat_count, confirmed_count,
                   empty_seats, pvi, pvi_rating, priority_tier, prospect_count
            FROM scout_district_targets WHERE district_code = %s
        """, (district_code,))
        district = cur.fetchone()

        # Confirmed candidates
        cur.execute("""
            SELECT c.candidate_id, c.first_name, c.last_name, ces.status
            FROM candidate_election_status ces
            JOIN candidates c ON c.candidate_id = ces.candidate_id
            WHERE ces.district_code = %s AND ces.election_year = 2026
            ORDER BY ces.status, c.last_name
        """, (district_code,))
        candidates = cur.fetchall()

        # Prospects for this district
        cur.execute("""
            SELECT sp.id, sp.first_name, sp.last_name, sp.city, sp.composite_score,
                   sp.review_status, sp.voter_party, sp.occupation,
                   COUNT(ss.id) as signal_count
            FROM scout_prospects sp
            LEFT JOIN scout_signals ss ON ss.prospect_id = sp.id
            WHERE sp.district_code = %s
            GROUP BY sp.id
            ORDER BY sp.composite_score DESC
        """, (district_code,))
        prospects = cur.fetchall()

        return render_template('scout/scout_district_detail.html',
            district=district, candidates=candidates, prospects=prospects,
            district_code=district_code
        )
    finally:
        cur.close()
        release_db_connection(conn)


# =============================================================================
# PROSPECTS
# =============================================================================

@scout_bp.route('/prospects')
@scout_admin_required
def prospects():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        status_filter = request.args.get('status', '')
        district_filter = request.args.get('district', '')
        search = request.args.get('q', '')

        query = """
            SELECT sp.id, sp.first_name, sp.last_name, sp.city, sp.district_code,
                   sp.composite_score, sp.review_status, sp.voter_party, sp.priority,
                   COUNT(ss.id) as signal_count, sp.created_at
            FROM scout_prospects sp
            LEFT JOIN scout_signals ss ON ss.prospect_id = sp.id
            WHERE 1=1
        """
        params = []

        if status_filter:
            query += " AND sp.review_status = %s"
            params.append(status_filter)
        else:
            query += " AND sp.review_status NOT IN ('dismissed', 'promoted')"

        if district_filter:
            query += " AND sp.district_code = %s"
            params.append(district_filter)

        if search:
            query += " AND (UPPER(sp.first_name || ' ' || sp.last_name) LIKE UPPER(%s) OR UPPER(sp.city) LIKE UPPER(%s))"
            params.extend([f'%{search}%', f'%{search}%'])

        query += " GROUP BY sp.id ORDER BY sp.composite_score DESC"

        cur.execute(query, params)
        rows = cur.fetchall()

        # Status counts
        cur.execute("""
            SELECT review_status, COUNT(*) FROM scout_prospects GROUP BY review_status
        """)
        status_counts = dict(cur.fetchall())

        # District list for filter dropdown
        cur.execute("""
            SELECT DISTINCT district_code FROM scout_prospects
            WHERE district_code IS NOT NULL ORDER BY district_code
        """)
        district_list = [r[0] for r in cur.fetchall()]

        return render_template('scout/scout_prospects.html',
            prospects=rows, status_counts=status_counts,
            status_filter=status_filter, district_filter=district_filter,
            search=search, district_list=district_list
        )
    finally:
        cur.close()
        release_db_connection(conn)


@scout_bp.route('/prospect/<int:prospect_id>')
@scout_admin_required
def prospect_detail(prospect_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM scout_prospects WHERE id = %s", (prospect_id,))
        prospect = cur.fetchone()
        if not prospect:
            flash('Prospect not found.', 'danger')
            return redirect(url_for('scout.prospects'))

        # Column names for the prospect
        col_names = [desc[0] for desc in cur.description]
        prospect_dict = dict(zip(col_names, prospect))

        # Signals
        cur.execute("""
            SELECT id, source_type, signal_date, title, detail, url,
                   fec_committee, fec_amount, signal_score, created_at
            FROM scout_signals WHERE prospect_id = %s
            ORDER BY signal_date DESC NULLS LAST, created_at DESC
        """, (prospect_id,))
        signals = cur.fetchall()

        # Contact log
        cur.execute("""
            SELECT id, contacted_by, contact_date, contact_method, outcome, notes,
                   status_before, status_after
            FROM scout_contacts WHERE prospect_id = %s
            ORDER BY contact_date DESC
        """, (prospect_id,))
        contacts = cur.fetchall()

        # District info
        district_info = None
        if prospect_dict.get('district_code'):
            cur.execute("""
                SELECT district_code, county_name, towns, seat_count, confirmed_count,
                       empty_seats, pvi, pvi_rating
                FROM scout_district_targets WHERE district_code = %s
            """, (prospect_dict['district_code'],))
            district_info = cur.fetchone()

        # Available districts for promote dropdown
        cur.execute("""
            SELECT district_code, county_name, towns, empty_seats, pvi
            FROM scout_district_targets WHERE empty_seats > 0
            ORDER BY pvi DESC
        """)
        available_districts = cur.fetchall()

        return render_template('scout/scout_prospect_detail.html',
            p=prospect_dict, signals=signals, contacts=contacts,
            district_info=district_info, available_districts=available_districts
        )
    finally:
        cur.close()
        release_db_connection(conn)


@scout_bp.route('/prospect/<int:prospect_id>/update', methods=['POST'])
@scout_admin_required
def update_prospect(prospect_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        review_status = request.form.get('review_status')
        priority = request.form.get('priority')
        notes = request.form.get('notes')
        district_code = request.form.get('district_code')

        updates = []
        params = []
        if review_status:
            updates.append("review_status = %s")
            params.append(review_status)
        if priority:
            updates.append("priority = %s")
            params.append(priority)
        if notes is not None:
            updates.append("notes = %s")
            params.append(notes)
        if district_code:
            updates.append("district_code = %s")
            params.append(district_code)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(prospect_id)
            cur.execute(f"UPDATE scout_prospects SET {', '.join(updates)} WHERE id = %s", params)
            conn.commit()
            flash('Prospect updated.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {e}', 'danger')
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for('scout.prospect_detail', prospect_id=prospect_id))


@scout_bp.route('/prospect/<int:prospect_id>/contact', methods=['POST'])
@scout_admin_required
def log_contact(prospect_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        method = request.form.get('contact_method')
        outcome = request.form.get('outcome')
        notes = request.form.get('notes', '')
        new_status = request.form.get('new_status', '')

        # Get current status
        cur.execute("SELECT review_status FROM scout_prospects WHERE id = %s", (prospect_id,))
        row = cur.fetchone()
        old_status = row[0] if row else 'unknown'

        if new_status and new_status != old_status:
            cur.execute("UPDATE scout_prospects SET review_status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                       (new_status, prospect_id))
        elif not new_status:
            new_status = old_status

        cur.execute("""
            INSERT INTO scout_contacts (prospect_id, contacted_by, contact_method, outcome, notes,
                                        status_before, status_after)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (prospect_id, current_user.email, method, outcome, notes, old_status, new_status))
        conn.commit()
        flash('Contact logged.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {e}', 'danger')
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for('scout.prospect_detail', prospect_id=prospect_id))


@scout_bp.route('/prospect/<int:prospect_id>/promote', methods=['POST'])
@scout_admin_required
def promote_prospect(prospect_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM scout_prospects WHERE id = %s", (prospect_id,))
        row = cur.fetchone()
        if not row:
            flash('Prospect not found.', 'danger')
            return redirect(url_for('scout.prospects'))

        col_names = [desc[0] for desc in cur.description]
        p = dict(zip(col_names, row))

        district_code = request.form.get('district_code', p.get('district_code', ''))
        status = request.form.get('status', 'New Recruit')

        if not district_code:
            flash('District is required to promote.', 'warning')
            return redirect(url_for('scout.prospect_detail', prospect_id=prospect_id))

        # Check if candidate already exists
        cur.execute("""
            SELECT candidate_id FROM candidates
            WHERE UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s)
        """, (p['first_name'], p['last_name']))
        existing = cur.fetchone()

        if existing:
            candidate_id = existing[0]
        else:
            cur.execute("""
                INSERT INTO candidates (first_name, last_name, party, address, city, zip,
                                        voter_id, created_by)
                VALUES (%s, %s, 'R', %s, %s, %s, %s, %s)
                RETURNING candidate_id
            """, (p['first_name'], p['last_name'], p.get('address', ''),
                  p.get('city', ''), p.get('zip', ''), p.get('voter_id'),
                  current_user.email))
            candidate_id = cur.fetchone()[0]

        # Check for existing election status
        cur.execute("""
            SELECT 1 FROM candidate_election_status
            WHERE candidate_id = %s AND election_year = 2026 AND district_code = %s
        """, (candidate_id, district_code))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO candidate_election_status
                    (candidate_id, election_year, status, is_running, added_by, district_code)
                VALUES (%s, 2026, %s, TRUE, %s, %s)
            """, (candidate_id, status, current_user.email, district_code))

        # Mark prospect as promoted
        cur.execute("""
            UPDATE scout_prospects
            SET review_status = 'promoted', promoted_candidate_id = %s,
                promoted_at = CURRENT_TIMESTAMP, promoted_by = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (candidate_id, current_user.email, prospect_id))

        conn.commit()
        flash(f'Promoted {p["first_name"]} {p["last_name"]} to candidate in {district_code}.', 'success')
    except Exception as e:
        conn.rollback()
        logger.error(f"Error promoting prospect: {e}")
        flash(f'Error: {e}', 'danger')
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for('scout.prospect_detail', prospect_id=prospect_id))


@scout_bp.route('/prospect/<int:prospect_id>/dismiss', methods=['POST'])
@scout_admin_required
def dismiss_prospect(prospect_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE scout_prospects SET review_status = 'dismissed', updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (prospect_id,))
        conn.commit()
        flash('Prospect dismissed.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {e}', 'danger')
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for('scout.prospects'))


@scout_bp.route('/prospect/add', methods=['POST'])
@scout_admin_required
def add_prospect():
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    city = request.form.get('city', '').strip()
    notes = request.form.get('notes', '').strip()

    if not first_name or not last_name:
        flash('First and last name required.', 'warning')
        return redirect(url_for('scout.prospects'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Check for existing
        cur.execute("""
            SELECT id FROM scout_prospects
            WHERE UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s)
              AND UPPER(COALESCE(city,'')) = UPPER(COALESCE(%s,''))
        """, (first_name, last_name, city or ''))
        if cur.fetchone():
            flash('Prospect already exists.', 'warning')
            return redirect(url_for('scout.prospects'))

        # Try voter file match
        voter_data = None
        voter_conn = get_voter_db_connection()
        if voter_conn:
            try:
                voter_cur = voter_conn.cursor()
                voter_data = match_voter_file(voter_cur, first_name, last_name, city if city else None)
            finally:
                voter_cur.close()
                release_voter_db_connection(voter_conn)

        # Detect district
        district_code = lookup_district_for_city(cur, city) if city else None
        if voter_data and not district_code:
            district_code = lookup_district_for_city(cur, voter_data.get('city'))

        cur.execute("""
            INSERT INTO scout_prospects
                (first_name, last_name, city, notes, voter_id, voter_party,
                 address, zip, county, district_code, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            first_name, last_name, city or (voter_data or {}).get('city', ''),
            notes,
            (voter_data or {}).get('voter_id'),
            (voter_data or {}).get('party'),
            (voter_data or {}).get('address', ''),
            (voter_data or {}).get('zip', ''),
            (voter_data or {}).get('county', ''),
            district_code,
            current_user.email
        ))
        new_id = cur.fetchone()[0]

        # Add manual signal
        if notes:
            cur.execute("""
                INSERT INTO scout_signals (prospect_id, source_type, title, detail, signal_score, signal_date)
                VALUES (%s, 'manual', 'Manually added', %s, 5, CURRENT_DATE)
            """, (new_id, notes))
            recalc_composite_score(cur, new_id)

        conn.commit()
        flash(f'Added {first_name} {last_name}.', 'success')
        return redirect(url_for('scout.prospect_detail', prospect_id=new_id))
    except Exception as e:
        conn.rollback()
        logger.error(f"Error adding prospect: {e}")
        flash(f'Error: {e}', 'danger')
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for('scout.prospects'))


# =============================================================================
# FEC SCAN
# =============================================================================

FEC_API_BASE = 'https://api.open.fec.gov/v1'

# Republican committees/PACs to score highly
GOP_COMMITTEES = {
    'C00075820': ('Republican National Committee', 15),
    'C00580100': ('Donald J. Trump for President', 12),
    'C00694455': ('Trump Save America JFC', 12),
    'C00003418': ('National Republican Congressional Committee', 12),
    'C00027466': ('National Republican Senatorial Committee', 12),
}


def score_fec_donation(committee_name, committee_id, amount):
    """Calculate signal score for an FEC donation."""
    score = 0
    name_upper = (committee_name or '').upper()

    # Check known committees
    if committee_id in GOP_COMMITTEES:
        score = GOP_COMMITTEES[committee_id][1]
    elif 'REPUBLICAN' in name_upper or 'GOP' in name_upper:
        score = 10
    elif any(kw in name_upper for kw in ['CONSERVATIVE', 'LIBERTY', 'FREEDOM', 'PATRIOT', 'MAGA']):
        score = 8
    else:
        # Any political donor is somewhat interesting
        score = 3

    # Bonus for large donations
    if amount and amount >= 1000:
        score += 5
    elif amount and amount >= 500:
        score += 3

    return min(score, 25)  # cap at 25


@scout_bp.route('/scan/fec', methods=['GET', 'POST'])
@scout_admin_required
def scan_fec():
    if request.method == 'GET':
        return render_template('scout/scout_scan_fec.html')

    import requests as http_requests

    api_key = os.environ.get('FEC_API_KEY', 'DEMO_KEY')
    min_amount = int(request.form.get('min_amount', 200))
    cycle = request.form.get('cycle', '2024')
    max_pages = int(request.form.get('max_pages', 20))

    conn = get_db_connection()
    cur = conn.cursor()

    # Create scan record
    cur.execute("""
        INSERT INTO scout_scans (scan_type, status, parameters, run_by)
        VALUES ('fec', 'running', %s, %s) RETURNING id
    """, (json.dumps({'min_amount': min_amount, 'cycle': cycle, 'max_pages': max_pages}),
          current_user.email))
    scan_id = cur.fetchone()[0]
    conn.commit()

    prospects_found = 0
    prospects_new = 0
    signals_added = 0

    try:
        last_index = None
        last_date = None

        for page_num in range(max_pages):
            params = {
                'api_key': api_key,
                'contributor_state': 'NH',
                'two_year_transaction_period': int(cycle),
                'min_amount': min_amount,
                'sort': '-contribution_receipt_date',
                'per_page': 100,
                'is_individual': 'true',
            }
            if last_index:
                params['last_index'] = last_index
                params['last_contribution_receipt_date'] = last_date

            resp = http_requests.get(f'{FEC_API_BASE}/schedules/schedule_a/', params=params, timeout=30)
            if resp.status_code != 200:
                raise Exception(f"FEC API returned {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            results = data.get('results', [])
            if not results:
                break

            # Get voter DB connection for matching
            voter_conn = get_voter_db_connection()
            voter_cur = voter_conn.cursor() if voter_conn else None

            try:
                for donor in results:
                    name = (donor.get('contributor_name') or '').strip()
                    if not name or ',' not in name:
                        continue

                    # FEC stores names as "LAST, FIRST"
                    parts = name.split(',', 1)
                    last_name = parts[0].strip().title()
                    first_name = parts[1].strip().title() if len(parts) > 1 else ''
                    if not first_name or not last_name:
                        continue

                    city = (donor.get('contributor_city') or '').strip().title()
                    state = (donor.get('contributor_state') or '').strip()
                    if state != 'NH':
                        continue

                    zip_code = (donor.get('contributor_zip') or '')[:5]
                    amount = donor.get('contribution_receipt_amount', 0)
                    committee_name = donor.get('committee', {}).get('name', '')
                    committee_id = donor.get('committee_id', '')
                    receipt_date = donor.get('contribution_receipt_date', '')
                    occupation = (donor.get('contributor_occupation') or '').strip().title()
                    employer = (donor.get('contributor_employer') or '').strip().title()

                    prospects_found += 1

                    # Check if prospect exists
                    cur.execute("""
                        SELECT id FROM scout_prospects
                        WHERE UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s)
                          AND UPPER(COALESCE(city, '')) = UPPER(COALESCE(%s, ''))
                    """, (first_name, last_name, city))
                    existing = cur.fetchone()

                    if existing:
                        prospect_id = existing[0]
                    else:
                        # Try voter file match
                        voter_data = None
                        if voter_cur:
                            voter_data = match_voter_file(voter_cur, first_name, last_name, city)

                        # Only create prospects for R or UND voters (or unknown)
                        if voter_data and voter_data['party'] and voter_data['party'].upper() == 'DEM':
                            continue

                        district_code = lookup_district_for_city(cur, city)

                        cur.execute("""
                            INSERT INTO scout_prospects
                                (first_name, last_name, city, zip, county, occupation, employer,
                                 voter_id, voter_party, address, district_code, created_by)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'fec_scan')
                            ON CONFLICT (first_name, last_name, city) DO NOTHING
                            RETURNING id
                        """, (
                            first_name, last_name, city, zip_code,
                            (voter_data or {}).get('county', ''),
                            occupation, employer,
                            (voter_data or {}).get('voter_id'),
                            (voter_data or {}).get('party'),
                            (voter_data or {}).get('address', ''),
                            district_code
                        ))
                        result = cur.fetchone()
                        if result:
                            prospect_id = result[0]
                            prospects_new += 1
                        else:
                            # ON CONFLICT hit - fetch existing
                            cur.execute("""
                                SELECT id FROM scout_prospects
                                WHERE UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s)
                                  AND UPPER(COALESCE(city,'')) = UPPER(COALESCE(%s,''))
                            """, (first_name, last_name, city))
                            prospect_id = cur.fetchone()[0]

                    # Add signal
                    sig_score = score_fec_donation(committee_name, committee_id, amount)
                    cur.execute("""
                        INSERT INTO scout_signals
                            (prospect_id, source_type, signal_date, title, detail, fec_committee,
                             fec_amount, signal_score)
                        VALUES (%s, 'fec_donation', %s, %s, %s, %s, %s, %s)
                    """, (
                        prospect_id,
                        receipt_date if receipt_date else None,
                        f"${amount:,.0f} to {committee_name[:60]}",
                        f"Donated ${amount:,.2f} to {committee_name} ({committee_id}). Occupation: {occupation}, Employer: {employer}",
                        committee_name, amount, sig_score
                    ))
                    signals_added += 1
                    recalc_composite_score(cur, prospect_id)

                conn.commit()
            finally:
                if voter_cur:
                    voter_cur.close()
                if voter_conn:
                    release_voter_db_connection(voter_conn)

            # Pagination
            pagination = data.get('pagination', {})
            last_index = pagination.get('last_indexes', {}).get('last_index')
            last_date = pagination.get('last_indexes', {}).get('last_contribution_receipt_date')
            if not last_index or len(results) < 100:
                break

        # Update scan record
        cur.execute("""
            UPDATE scout_scans SET status = 'completed', prospects_found = %s, prospects_new = %s,
                   signals_added = %s, completed_at = CURRENT_TIMESTAMP WHERE id = %s
        """, (prospects_found, prospects_new, signals_added, scan_id))
        conn.commit()

        flash(f'FEC scan complete: {prospects_found} donors found, {prospects_new} new prospects, {signals_added} signals added.', 'success')

    except Exception as e:
        conn.rollback()
        cur.execute("""
            UPDATE scout_scans SET status = 'failed', error_message = %s, completed_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (str(e)[:500], scan_id))
        conn.commit()
        logger.error(f"FEC scan error: {e}")
        flash(f'FEC scan error: {e}', 'danger')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('scout.scans'))


# =============================================================================
# VOTER FILE SCAN
# =============================================================================

@scout_bp.route('/scan/voter', methods=['GET', 'POST'])
@scout_admin_required
def scan_voter():
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'GET':
        # Get target districts for selection
        cur.execute("""
            SELECT district_code, county_name, towns, empty_seats, pvi
            FROM scout_district_targets WHERE empty_seats > 0
            ORDER BY pvi DESC
        """)
        targets = cur.fetchall()
        cur.close()
        release_db_connection(conn)
        return render_template('scout/scout_scan_voter.html', targets=targets)

    # POST - run scan
    selected_districts = request.form.getlist('districts')
    if not selected_districts:
        flash('Select at least one district.', 'warning')
        cur.close()
        release_db_connection(conn)
        return redirect(url_for('scout.scan_voter'))

    # Create scan record
    cur.execute("""
        INSERT INTO scout_scans (scan_type, status, parameters, run_by)
        VALUES ('voter_profile', 'running', %s, %s) RETURNING id
    """, (json.dumps({'districts': selected_districts}), current_user.email))
    scan_id = cur.fetchone()[0]
    conn.commit()

    prospects_found = 0
    prospects_new = 0
    signals_added = 0

    try:
        voter_conn = get_voter_db_connection()
        if not voter_conn:
            raise Exception("Voter database not available")
        voter_cur = voter_conn.cursor()

        for district_code in selected_districts:
            # Get towns in this district
            cur.execute("""
                SELECT DISTINCT town FROM districts WHERE full_district_code = %s
            """, (district_code,))
            towns = [r[0].strip() for r in cur.fetchall()]

            for town in towns:
                # Find registered Republicans in this town
                voter_cur.execute("""
                    SELECT id_voter, nm_first, nm_last, cd_party, ad_str1, ad_city, ad_zip5, county
                    FROM statewidechecklist
                    WHERE UPPER(ad_city) = UPPER(%s) AND cd_party = 'REP'
                    ORDER BY nm_last, nm_first
                """, (town,))

                for voter in voter_cur.fetchall():
                    voter_id, first, last, party, addr, vcity, vzip, county = voter
                    if not first or not last:
                        continue

                    first = first.strip().title()
                    last = last.strip().title()
                    vcity = (vcity or town).strip().title()

                    prospects_found += 1

                    # Check if already a prospect
                    cur.execute("""
                        SELECT id, voter_id FROM scout_prospects
                        WHERE UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s)
                          AND UPPER(COALESCE(city,'')) = UPPER(COALESCE(%s,''))
                    """, (first, last, vcity))
                    existing = cur.fetchone()

                    if existing:
                        prospect_id = existing[0]
                        # Enrich with voter data if missing
                        if not existing[1]:
                            cur.execute("""
                                UPDATE scout_prospects SET voter_id = %s, voter_party = %s,
                                       address = %s, zip = %s, county = %s,
                                       district_code = COALESCE(district_code, %s),
                                       updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                            """, (voter_id, party, addr, vzip, county, district_code, prospect_id))
                    else:
                        # Only create new prospects for voters who already have signals
                        # (bare voter records are too low-signal)
                        # Check if they're an FEC donor we haven't matched yet
                        cur.execute("""
                            SELECT id FROM scout_prospects
                            WHERE UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s)
                              AND voter_id IS NULL
                        """, (first, last))
                        unmatched = cur.fetchone()
                        if unmatched:
                            cur.execute("""
                                UPDATE scout_prospects SET voter_id = %s, voter_party = %s,
                                       address = %s, city = %s, zip = %s, county = %s,
                                       district_code = %s, updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                            """, (voter_id, party, addr, vcity, vzip, county, district_code, unmatched[0]))
                            prospect_id = unmatched[0]

                            # Add voter signal
                            cur.execute("""
                                INSERT INTO scout_signals
                                    (prospect_id, source_type, title, signal_score, signal_date)
                                VALUES (%s, 'voter_profile', %s, 10, CURRENT_DATE)
                            """, (prospect_id, f"Registered Republican in {vcity}"))
                            signals_added += 1
                            recalc_composite_score(cur, prospect_id)
                            prospects_new += 1

                conn.commit()

        voter_cur.close()
        release_voter_db_connection(voter_conn)

        cur.execute("""
            UPDATE scout_scans SET status = 'completed', prospects_found = %s, prospects_new = %s,
                   signals_added = %s, completed_at = CURRENT_TIMESTAMP WHERE id = %s
        """, (prospects_found, prospects_new, signals_added, scan_id))
        conn.commit()
        flash(f'Voter scan complete: {prospects_found} voters checked, {prospects_new} enriched, {signals_added} signals added.', 'success')

    except Exception as e:
        conn.rollback()
        cur.execute("""
            UPDATE scout_scans SET status = 'failed', error_message = %s, completed_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (str(e)[:500], scan_id))
        conn.commit()
        logger.error(f"Voter scan error: {e}")
        flash(f'Voter scan error: {e}', 'danger')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('scout.scans'))


# =============================================================================
# NEWS RSS SCAN
# =============================================================================

NH_RSS_FEEDS = [
    ('Union Leader', 'https://www.unionleader.com/search/?f=rss&t=article&l=50&s=start_time&sd=desc'),
    ('Concord Monitor', 'https://www.concordmonitor.com/arcio/rss/'),
    ('Laconia Daily Sun', 'https://www.laconiadailysun.com/search/?f=rss&t=article&l=50&s=start_time&sd=desc'),
    ('Foster\'s Daily Democrat', 'https://www.fosters.com/arcio/rss/'),
    ('Keene Sentinel', 'https://www.sentinelsource.com/search/?f=rss&t=article&l=50&s=start_time&sd=desc'),
]


@scout_bp.route('/scan/news', methods=['GET', 'POST'])
@scout_admin_required
def scan_news():
    if request.method == 'GET':
        return render_template('scout/scout_scan_news.html', feeds=NH_RSS_FEEDS)

    import feedparser

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO scout_scans (scan_type, status, parameters, run_by)
        VALUES ('news_rss', 'running', %s, %s) RETURNING id
    """, (json.dumps({'feeds': [f[0] for f in NH_RSS_FEEDS]}), current_user.email))
    scan_id = cur.fetchone()[0]
    conn.commit()

    prospects_found = 0
    prospects_new = 0
    signals_added = 0

    try:
        voter_conn = get_voter_db_connection()
        voter_cur = voter_conn.cursor() if voter_conn else None

        for feed_name, feed_url in NH_RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
            except Exception as e:
                logger.warning(f"Failed to parse {feed_name}: {e}")
                continue

            for entry in feed.get('entries', []):
                title = entry.get('title', '')
                summary = entry.get('summary', '')
                link = entry.get('link', '')
                content = f"{title} {summary}".lower()

                # Look for letters to the editor or opinion
                if not any(kw in content for kw in ['letter', 'opinion', 'editorial', 'to the editor']):
                    continue

                # Try to extract author - common format "By FIRSTNAME LASTNAME" or author field
                author = entry.get('author', '')
                if not author:
                    # Try to extract from title like "Letter: Title here - John Smith, Concord"
                    match = re.search(r'[-\u2014]\s*([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+),\s*([A-Za-z\s]+)$', title)
                    if match:
                        author = match.group(1)
                        city = match.group(2).strip()
                    else:
                        continue
                else:
                    city = ''

                # Parse author name
                author = author.strip()
                if not author or ' ' not in author:
                    continue
                name_parts = author.split()
                first_name = name_parts[0].title()
                last_name = name_parts[-1].title()

                # Try voter file match
                if voter_cur:
                    voter_data = match_voter_file(voter_cur, first_name, last_name, city if city else None)
                    if voter_data:
                        if voter_data['party'] and voter_data['party'].upper() == 'DEM':
                            continue
                        city = city or voter_data.get('city', '')

                prospects_found += 1

                # Check existing
                cur.execute("""
                    SELECT id FROM scout_prospects
                    WHERE UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s)
                      AND UPPER(COALESCE(city,'')) = UPPER(COALESCE(%s,''))
                """, (first_name, last_name, city))
                existing = cur.fetchone()

                if existing:
                    prospect_id = existing[0]
                else:
                    district_code = lookup_district_for_city(cur, city) if city else None
                    cur.execute("""
                        INSERT INTO scout_prospects
                            (first_name, last_name, city, voter_id, voter_party, district_code,
                             address, zip, county, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'news_scan')
                        ON CONFLICT (first_name, last_name, city) DO NOTHING
                        RETURNING id
                    """, (
                        first_name, last_name, city,
                        (voter_data or {}).get('voter_id') if voter_cur else None,
                        (voter_data or {}).get('party') if voter_cur else None,
                        district_code,
                        (voter_data or {}).get('address', '') if voter_cur else '',
                        (voter_data or {}).get('zip', '') if voter_cur else '',
                        (voter_data or {}).get('county', '') if voter_cur else ''
                    ))
                    result = cur.fetchone()
                    if result:
                        prospect_id = result[0]
                        prospects_new += 1
                    else:
                        cur.execute("""
                            SELECT id FROM scout_prospects
                            WHERE UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s)
                              AND UPPER(COALESCE(city,'')) = UPPER(COALESCE(%s,''))
                        """, (first_name, last_name, city))
                        prospect_id = cur.fetchone()[0]

                # Add signal
                pub_date = entry.get('published', '')
                cur.execute("""
                    INSERT INTO scout_signals
                        (prospect_id, source_type, signal_date, title, detail, url, signal_score)
                    VALUES (%s, 'news_lte', %s, %s, %s, %s, 8)
                """, (
                    prospect_id,
                    pub_date[:10] if pub_date else None,
                    f"Letter to Editor in {feed_name}",
                    f"Title: {title[:200]}",
                    link
                ))
                signals_added += 1
                recalc_composite_score(cur, prospect_id)

            conn.commit()

        if voter_cur:
            voter_cur.close()
        if voter_conn:
            release_voter_db_connection(voter_conn)

        cur.execute("""
            UPDATE scout_scans SET status = 'completed', prospects_found = %s, prospects_new = %s,
                   signals_added = %s, completed_at = CURRENT_TIMESTAMP WHERE id = %s
        """, (prospects_found, prospects_new, signals_added, scan_id))
        conn.commit()
        flash(f'News scan complete: {prospects_found} LTEs found, {prospects_new} new prospects, {signals_added} signals added.', 'success')

    except Exception as e:
        conn.rollback()
        cur.execute("""
            UPDATE scout_scans SET status = 'failed', error_message = %s, completed_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (str(e)[:500], scan_id))
        conn.commit()
        logger.error(f"News scan error: {e}")
        flash(f'News scan error: {e}', 'danger')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('scout.scans'))


# =============================================================================
# SCAN HISTORY
# =============================================================================

@scout_bp.route('/scans')
@scout_admin_required
def scans():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, scan_type, status, parameters, prospects_found, prospects_new,
                   signals_added, error_message, started_at, completed_at, run_by
            FROM scout_scans ORDER BY started_at DESC LIMIT 50
        """)
        rows = cur.fetchall()
        return render_template('scout/scout_scans.html', scans=rows)
    finally:
        cur.close()
        release_db_connection(conn)


# =============================================================================
# EXPORT
# =============================================================================

@scout_bp.route('/export')
@scout_admin_required
def export_prospects():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT sp.first_name, sp.last_name, sp.city, sp.county, sp.zip,
                   sp.district_code, sp.voter_party, sp.occupation, sp.employer,
                   sp.composite_score, sp.review_status, sp.priority,
                   sp.email, sp.phone, sp.notes,
                   COUNT(ss.id) as signal_count
            FROM scout_prospects sp
            LEFT JOIN scout_signals ss ON ss.prospect_id = sp.id
            WHERE sp.review_status NOT IN ('dismissed')
            GROUP BY sp.id
            ORDER BY sp.composite_score DESC
        """)
        rows = cur.fetchall()

        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['First Name', 'Last Name', 'City', 'County', 'Zip',
                        'District', 'Party', 'Occupation', 'Employer',
                        'Score', 'Status', 'Priority', 'Email', 'Phone', 'Notes', 'Signals'])
        for row in rows:
            writer.writerow(row)

        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=scout_prospects_{date.today()}.csv'}
        )
    finally:
        cur.close()
        release_db_connection(conn)
