"""
Private Features Module
Handles secret primary tracking and speaker vote counting.
Access is controlled via private_feature_access table - only superadmin can grant access.
"""

from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime

private_bp = Blueprint('private', __name__, url_prefix='/private')

# Will be set by init_private_features()
get_db_connection = None
release_db_connection = None
is_super_admin = None
SUPER_ADMIN_EMAIL = None


def init_private_features(db_conn_func, db_release_func, super_admin_func, super_admin_email):
    """Initialize the module with database functions from main app."""
    global get_db_connection, release_db_connection, is_super_admin, SUPER_ADMIN_EMAIL
    get_db_connection = db_conn_func
    release_db_connection = db_release_func
    is_super_admin = super_admin_func
    SUPER_ADMIN_EMAIL = super_admin_email


def has_feature_access(feature_slug):
    """Check if current user has access to a specific feature."""
    if not current_user.is_authenticated:
        return False

    # Superadmin always has access
    if current_user.email.lower() == SUPER_ADMIN_EMAIL.lower():
        return True

    # Check if user is an admin with access
    if not hasattr(current_user, 'user_id'):
        return False

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 1 FROM private_feature_access
            WHERE user_id = %s AND feature_slug = %s
        """, (current_user.user_id, feature_slug))
        return cur.fetchone() is not None
    finally:
        cur.close()
        release_db_connection(conn)


def get_user_private_features():
    """Get list of private features current user has access to."""
    if not current_user.is_authenticated:
        return []

    # Superadmin has access to all
    if current_user.email.lower() == SUPER_ADMIN_EMAIL.lower():
        return ['secret_primaries', 'speaker_votes']

    if not hasattr(current_user, 'user_id'):
        return []

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT feature_slug FROM private_feature_access
            WHERE user_id = %s
        """, (current_user.user_id,))
        return [row[0] for row in cur.fetchall()]
    finally:
        cur.close()
        release_db_connection(conn)


def require_feature_access(feature_slug):
    """Decorator to require access to a specific private feature."""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not has_feature_access(feature_slug):
                flash('You do not have access to this feature.', 'error')
                return redirect(url_for('admin_dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# =============================================================================
# ACCESS MANAGEMENT (Superadmin only)
# =============================================================================

@private_bp.route('/access')
@login_required
def manage_access():
    """Manage who has access to private features (superadmin only)."""
    if not is_super_admin():
        flash('Access denied.', 'error')
        return redirect(url_for('admin_dashboard'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get all users
        cur.execute("SELECT user_id, username, email, role FROM users ORDER BY username")
        users = cur.fetchall()

        # Get all access grants
        cur.execute("""
            SELECT pfa.id, pfa.user_id, pfa.feature_slug, pfa.granted_at, pfa.notes, u.username, u.email
            FROM private_feature_access pfa
            JOIN users u ON pfa.user_id = u.user_id
            ORDER BY pfa.feature_slug, u.username
        """)
        access_grants = cur.fetchall()

        return render_template('private/manage_access.html',
                             users=users,
                             access_grants=access_grants,
                             features=['secret_primaries', 'speaker_votes'])
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/access/grant', methods=['POST'])
@login_required
def grant_access():
    """Grant a user access to a feature."""
    if not is_super_admin():
        return jsonify({'error': 'Access denied'}), 403

    user_id = request.form.get('user_id')
    feature_slug = request.form.get('feature_slug')
    notes = request.form.get('notes', '')

    if not user_id or not feature_slug:
        flash('User and feature are required.', 'error')
        return redirect(url_for('private.manage_access'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO private_feature_access (user_id, feature_slug, granted_by, notes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, feature_slug) DO NOTHING
        """, (user_id, feature_slug, current_user.email, notes))
        conn.commit()
        flash('Access granted successfully.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error granting access: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.manage_access'))


@private_bp.route('/access/revoke/<int:access_id>', methods=['POST'])
@login_required
def revoke_access(access_id):
    """Revoke a user's access to a feature."""
    if not is_super_admin():
        return jsonify({'error': 'Access denied'}), 403

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM private_feature_access WHERE id = %s", (access_id,))
        conn.commit()
        flash('Access revoked.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error revoking access: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.manage_access'))


# =============================================================================
# SECRET PRIMARY TRACKING
# =============================================================================

@private_bp.route('/primaries')
@require_feature_access('secret_primaries')
def secret_primaries():
    """List all primary targets (flat list, no campaigns)."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get all targets with incumbent info
        cur.execute("""
            SELECT t.id, t.campaign_id, t.district_code, t.incumbent_candidate_id,
                   t.challenger_name, t.challenger_status, t.challenger_contact,
                   t.notes, t.priority, t.created_by, t.created_at, t.updated_at,
                   c.first_name as incumbent_first,
                   c.last_name as incumbent_last,
                   c.party as incumbent_party,
                   c.email as incumbent_email,
                   c.phone1 as incumbent_phone,
                   t.assigned_caller,
                   (SELECT COUNT(*) FROM secret_primary_contacts WHERE target_id = t.id) as contact_count
            FROM secret_primary_targets t
            LEFT JOIN candidates c ON t.incumbent_candidate_id = c.candidate_id
            ORDER BY t.priority, t.district_code
        """)
        targets = cur.fetchall()

        # Get challengers for all targets
        target_ids = [t[0] for t in targets]
        challengers_by_target = {}
        if target_ids:
            cur.execute("""
                SELECT id, target_id, first_name, last_name, email, phone,
                       status, notes, voter_id, is_public, created_at
                FROM secret_primary_challengers
                WHERE target_id = ANY(%s)
                ORDER BY created_at
            """, (target_ids,))
            for ch in cur.fetchall():
                target_id = ch[1]
                if target_id not in challengers_by_target:
                    challengers_by_target[target_id] = []
                challengers_by_target[target_id].append(ch)

        # Get IDs of candidates already in targets
        target_candidate_ids = [t[3] for t in targets if t[3]]

        # Get "opposed" legislators from speaker vote tracking who aren't already targets
        cur.execute("""
            SELECT c.candidate_id, c.first_name, c.last_name, c.email, c.phone1,
                   COALESCE(ces2026.district_code, ces2024.district_code) as district_code,
                   svt.commitment_status, svt.confidence_level, svt.notes
            FROM candidates c
            JOIN candidate_election_status ces2024 ON c.candidate_id = ces2024.candidate_id
                AND ces2024.election_year = 2024 AND ces2024.status = 'Ran'
            LEFT JOIN candidate_election_status ces2026 ON c.candidate_id = ces2026.candidate_id AND ces2026.election_year = 2026
            JOIN speaker_vote_tracking svt ON c.candidate_id = svt.candidate_id
            WHERE c.party = 'R'
              AND c.incumbent = TRUE
              AND (ces2026.status IS NULL OR ces2026.status != 'Declined')
              AND svt.commitment_status = 'opposed'
            ORDER BY c.last_name
        """)
        opposed_legislators = [leg for leg in cur.fetchall() if leg[0] not in target_candidate_ids]

        # Get all R incumbents for the add target dropdown (same logic as speaker votes)
        cur.execute("""
            SELECT c.candidate_id, c.first_name, c.last_name, c.party,
                   COALESCE(ces2026.district_code, ces2024.district_code) as district_code,
                   COALESCE(ces2026.status, ces2024.status) as status
            FROM candidates c
            JOIN candidate_election_status ces2024 ON c.candidate_id = ces2024.candidate_id
                AND ces2024.election_year = 2024 AND ces2024.status = 'Ran'
            LEFT JOIN candidate_election_status ces2026 ON c.candidate_id = ces2026.candidate_id AND ces2026.election_year = 2026
            WHERE c.incumbent = TRUE
              AND c.party = 'R'
              AND (ces2026.status IS NULL OR ces2026.status != 'Declined')
            ORDER BY COALESCE(ces2026.district_code, ces2024.district_code), c.last_name
        """)
        incumbents = cur.fetchall()

        # Get users for caller assignment
        cur.execute("""
            SELECT u.user_id, u.username, u.email
            FROM users u
            JOIN private_feature_access pfa ON u.user_id = pfa.user_id
            WHERE pfa.feature_slug = 'secret_primaries'
            ORDER BY u.username
        """)
        callers = cur.fetchall()

        return render_template('private/primaries.html',
                             targets=targets,
                             incumbents=incumbents,
                             callers=callers,
                             opposed_legislators=opposed_legislators,
                             challengers_by_target=challengers_by_target)
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/primaries/new', methods=['GET', 'POST'])
@require_feature_access('secret_primaries')
def new_campaign():
    """Create a new secret primary campaign."""
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description', '')
        target_year = request.form.get('target_year', 2026)

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO secret_primary_campaigns (name, description, target_year, created_by)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (name, description, target_year, current_user.email))
            campaign_id = cur.fetchone()[0]
            conn.commit()
            flash('Campaign created successfully.', 'success')
            return redirect(url_for('private.view_campaign', campaign_id=campaign_id))
        except Exception as e:
            conn.rollback()
            flash(f'Error creating campaign: {e}', 'error')
        finally:
            cur.close()
            release_db_connection(conn)

    return render_template('private/primaries_new.html')


@private_bp.route('/primaries/<int:campaign_id>')
@require_feature_access('secret_primaries')
def view_campaign(campaign_id):
    """View a secret primary campaign and its targets."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get campaign
        cur.execute("SELECT * FROM secret_primary_campaigns WHERE id = %s", (campaign_id,))
        campaign = cur.fetchone()
        if not campaign:
            flash('Campaign not found.', 'error')
            return redirect(url_for('private.secret_primaries'))

        # Get targets with incumbent info from candidates table
        cur.execute("""
            SELECT t.id, t.campaign_id, t.district_code, t.incumbent_candidate_id,
                   t.challenger_name, t.challenger_status, t.challenger_contact,
                   t.notes, t.priority, t.created_by, t.created_at, t.updated_at,
                   c.first_name as incumbent_first,
                   c.last_name as incumbent_last,
                   c.party as incumbent_party,
                   c.email as incumbent_email,
                   c.phone1 as incumbent_phone,
                   t.assigned_caller,
                   (SELECT COUNT(*) FROM secret_primary_contacts WHERE target_id = t.id) as contact_count
            FROM secret_primary_targets t
            LEFT JOIN candidates c ON t.incumbent_candidate_id = c.candidate_id
            WHERE t.campaign_id = %s
            ORDER BY t.priority, t.district_code
        """, (campaign_id,))
        targets = cur.fetchall()

        # Get all R incumbents for the add target dropdown (same logic as speaker votes)
        cur.execute("""
            SELECT c.candidate_id, c.first_name, c.last_name, c.party,
                   COALESCE(ces2026.district_code, ces2024.district_code) as district_code,
                   COALESCE(ces2026.status, ces2024.status) as status
            FROM candidates c
            JOIN candidate_election_status ces2024 ON c.candidate_id = ces2024.candidate_id
                AND ces2024.election_year = 2024 AND ces2024.status = 'Ran'
            LEFT JOIN candidate_election_status ces2026 ON c.candidate_id = ces2026.candidate_id AND ces2026.election_year = 2026
            WHERE c.incumbent = TRUE
              AND c.party = 'R'
              AND (ces2026.status IS NULL OR ces2026.status != 'Declined')
            ORDER BY COALESCE(ces2026.district_code, ces2024.district_code), c.last_name
        """)
        incumbents = cur.fetchall()

        # Get users who have access to this feature for caller assignment
        cur.execute("""
            SELECT u.user_id, u.username, u.email
            FROM users u
            JOIN private_feature_access pfa ON u.user_id = pfa.user_id
            WHERE pfa.feature_slug = 'secret_primaries'
            ORDER BY u.username
        """)
        callers = cur.fetchall()

        return render_template('private/primaries_view.html',
                             campaign=campaign,
                             targets=targets,
                             incumbents=incumbents,
                             callers=callers)
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/primaries/add-from-opposed', methods=['POST'])
@require_feature_access('secret_primaries')
def add_target_from_opposed():
    """Add a primary target from an opposed speaker vote legislator."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        candidate_id = request.form.get('candidate_id')

        # Get candidate info (with 2024 fallback for district)
        cur.execute("""
            SELECT c.candidate_id, c.first_name, c.last_name, c.party,
                   COALESCE(ces2026.district_code, ces2024.district_code) as district_code
            FROM candidates c
            LEFT JOIN candidate_election_status ces2026 ON c.candidate_id = ces2026.candidate_id AND ces2026.election_year = 2026
            LEFT JOIN candidate_election_status ces2024 ON c.candidate_id = ces2024.candidate_id AND ces2024.election_year = 2024
            WHERE c.candidate_id = %s
        """, (candidate_id,))
        candidate = cur.fetchone()

        if not candidate:
            flash('Candidate not found.', 'error')
            return redirect(url_for('private.secret_primaries'))

        # Check if already a target
        cur.execute("SELECT 1 FROM secret_primary_targets WHERE incumbent_candidate_id = %s", (candidate_id,))
        if cur.fetchone():
            flash('Already a primary target.', 'warning')
            return redirect(url_for('private.secret_primaries'))

        cur.execute("""
            INSERT INTO secret_primary_targets
            (district_code, incumbent_candidate_id, challenger_status, priority, created_by)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            candidate[4],  # district_code
            candidate[0],  # candidate_id
            'recruiting',
            5,
            current_user.email
        ))
        conn.commit()
        flash(f'Added {candidate[1]} {candidate[2]} as primary target.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error adding target: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.secret_primaries'))


@private_bp.route('/primaries/add', methods=['POST'])
@require_feature_access('secret_primaries')
def add_target():
    """Add a primary target."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        incumbent_candidate_id = request.form.get('incumbent_candidate_id')

        # Get incumbent info (with 2024 fallback for district)
        cur.execute("""
            SELECT c.candidate_id, c.first_name, c.last_name, c.party,
                   COALESCE(ces2026.district_code, ces2024.district_code) as district_code
            FROM candidates c
            LEFT JOIN candidate_election_status ces2026 ON c.candidate_id = ces2026.candidate_id AND ces2026.election_year = 2026
            LEFT JOIN candidate_election_status ces2024 ON c.candidate_id = ces2024.candidate_id AND ces2024.election_year = 2024
            WHERE c.candidate_id = %s
        """, (incumbent_candidate_id,))
        incumbent = cur.fetchone()

        if not incumbent:
            flash('Incumbent not found.', 'error')
            return redirect(url_for('private.secret_primaries'))

        cur.execute("""
            INSERT INTO secret_primary_targets
            (district_code, incumbent_candidate_id, challenger_name,
             challenger_status, challenger_contact, notes, priority, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            incumbent[4],  # district_code
            incumbent[0],  # candidate_id
            request.form.get('challenger_name'),
            request.form.get('challenger_status', 'recruiting'),
            request.form.get('challenger_contact'),
            request.form.get('notes'),
            request.form.get('priority', 5),
            current_user.email
        ))
        conn.commit()
        flash('Target added.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error adding target: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.secret_primaries'))


@private_bp.route('/primaries/target/<int:target_id>/update', methods=['POST'])
@require_feature_access('secret_primaries')
def update_target(target_id):
    """Update a target's status."""
    conn = get_db_connection()
    cur = conn.cursor()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
              'application/json' in request.headers.get('Accept', '')

    try:
        cur.execute("""
            UPDATE secret_primary_targets
            SET challenger_name = %s, challenger_status = %s, challenger_contact = %s,
                notes = %s, priority = %s, assigned_caller = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (
            request.form.get('challenger_name'),
            request.form.get('challenger_status'),
            request.form.get('challenger_contact'),
            request.form.get('notes'),
            request.form.get('priority', 5),
            request.form.get('assigned_caller'),
            target_id
        ))
        conn.commit()

        if is_ajax:
            return jsonify({'success': True})

        flash('Target updated.', 'success')
    except Exception as e:
        conn.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash(f'Error updating target: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.secret_primaries'))


@private_bp.route('/primaries/target/<int:target_id>/contact', methods=['POST'])
@require_feature_access('secret_primaries')
def log_primary_contact(target_id):
    """Log a contact with an incumbent about primary challenge."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get current status
        cur.execute("""
            SELECT challenger_status, campaign_id FROM secret_primary_targets WHERE id = %s
        """, (target_id,))
        current = cur.fetchone()
        if not current:
            flash('Target not found.', 'error')
            return redirect(url_for('private.secret_primaries'))

        status_before = current[0] or 'recruiting'
        campaign_id = current[1]
        status_after = request.form.get('new_status', status_before)

        # Log the contact
        cur.execute("""
            INSERT INTO secret_primary_contacts
            (target_id, contacted_by, contact_method, outcome, notes, status_before, status_after)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            target_id,
            current_user.email,
            request.form.get('contact_method'),
            request.form.get('outcome'),
            request.form.get('notes'),
            status_before,
            status_after
        ))

        # Update the target status if changed
        if status_after != status_before:
            cur.execute("""
                UPDATE secret_primary_targets
                SET challenger_status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (status_after, target_id))

        conn.commit()
        flash('Contact logged.', 'success')
        return redirect(url_for('private.secret_primaries'))
    except Exception as e:
        conn.rollback()
        flash(f'Error logging contact: {e}', 'error')
        return redirect(url_for('private.secret_primaries'))
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/primaries/target/<int:target_id>/delete', methods=['POST'])
@require_feature_access('secret_primaries')
def delete_target(target_id):
    """Delete a target from a campaign."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            DELETE FROM secret_primary_targets WHERE id = %s RETURNING campaign_id
        """, (target_id,))
        result = cur.fetchone()
        conn.commit()
        flash('Target removed.', 'success')
        if result:
            return redirect(url_for('private.view_campaign', campaign_id=result[0]))
    except Exception as e:
        conn.rollback()
        flash(f'Error deleting target: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.secret_primaries'))


# =============================================================================
# SPEAKER VOTE TRACKING
# =============================================================================

@private_bp.route('/speaker')
@require_feature_access('speaker_votes')
def speaker_votes():
    """Speaker vote tracking dashboard - pulls from confirmed R candidates."""
    conn = get_db_connection()
    cur = conn.cursor()

    # Get filter parameters
    caller_filter = request.args.get('caller', '')
    status_filter = request.args.get('status', '')

    try:
        # Build query with optional filters
        # R incumbents who ran in 2024, excluding 2026 Declined
        query = """
            SELECT c.candidate_id, c.first_name, c.last_name, c.email, c.phone1,
                   COALESCE(ces2026.district_code, ces2024.district_code) as district_code,
                   c.incumbent,
                   svt.id as tracking_id, svt.commitment_status, svt.confidence_level,
                   svt.notes, svt.last_contact_at, svt.assigned_caller
            FROM candidates c
            JOIN candidate_election_status ces2024 ON c.candidate_id = ces2024.candidate_id
                AND ces2024.election_year = 2024 AND ces2024.status = 'Ran'
            LEFT JOIN candidate_election_status ces2026 ON c.candidate_id = ces2026.candidate_id AND ces2026.election_year = 2026
            LEFT JOIN speaker_vote_tracking svt ON c.candidate_id = svt.candidate_id
            WHERE c.party = 'R'
              AND c.incumbent = TRUE
              AND (ces2026.status IS NULL OR ces2026.status != 'Declined')
        """
        params = []

        if caller_filter:
            if caller_filter == 'unassigned':
                query += " AND (svt.assigned_caller IS NULL OR svt.assigned_caller = '')"
            else:
                query += " AND svt.assigned_caller = %s"
                params.append(caller_filter)

        if status_filter:
            query += " AND COALESCE(svt.commitment_status, 'unknown') = %s"
            params.append(status_filter)

        query += """
            ORDER BY
                CASE COALESCE(svt.commitment_status, 'unknown')
                    WHEN 'unknown' THEN 1
                    WHEN 'leaning_yes' THEN 2
                    WHEN 'leaning_no' THEN 3
                    WHEN 'opposed' THEN 4
                    WHEN 'committed' THEN 5
                END,
                c.last_name
        """

        cur.execute(query, params)
        legislators = cur.fetchall()

        # Get summary counts (unfiltered for dashboard cards)
        cur.execute("""
            SELECT COALESCE(svt.commitment_status, 'unknown') as status, COUNT(*)
            FROM candidates c
            JOIN candidate_election_status ces2024 ON c.candidate_id = ces2024.candidate_id
                AND ces2024.election_year = 2024 AND ces2024.status = 'Ran'
            LEFT JOIN candidate_election_status ces2026 ON c.candidate_id = ces2026.candidate_id AND ces2026.election_year = 2026
            LEFT JOIN speaker_vote_tracking svt ON c.candidate_id = svt.candidate_id
            WHERE c.party = 'R'
              AND c.incumbent = TRUE
              AND (ces2026.status IS NULL OR ces2026.status != 'Declined')
            GROUP BY COALESCE(svt.commitment_status, 'unknown')
        """)
        status_counts = dict(cur.fetchall())

        # Get users who have access to this feature for caller assignment
        cur.execute("""
            SELECT u.user_id, u.username, u.email
            FROM users u
            JOIN private_feature_access pfa ON u.user_id = pfa.user_id
            WHERE pfa.feature_slug = 'speaker_votes'
            ORDER BY u.username
        """)
        callers = cur.fetchall()

        return render_template('private/speaker_dashboard.html',
                             legislators=legislators,
                             status_counts=status_counts,
                             callers=callers,
                             caller_filter=caller_filter,
                             status_filter=status_filter)
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/speaker/update/<int:candidate_id>', methods=['POST'])
@require_feature_access('speaker_votes')
def update_speaker_vote(candidate_id):
    """Update a candidate's speaker vote status."""
    conn = get_db_connection()
    cur = conn.cursor()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
              'application/json' in request.headers.get('Accept', '')

    try:
        commitment_status = request.form.get('commitment_status', 'unknown')
        confidence_level = request.form.get('confidence_level', 5)
        notes = request.form.get('notes')

        assigned_caller = request.form.get('assigned_caller')

        # Get candidate name and existing tracking data (use 2026 district if available, else 2024)
        cur.execute("""
            SELECT c.first_name, c.last_name,
                   COALESCE(ces2026.district_code, ces2024.district_code) as district_code,
                   svt.notes, svt.assigned_caller
            FROM candidates c
            LEFT JOIN candidate_election_status ces2026 ON c.candidate_id = ces2026.candidate_id AND ces2026.election_year = 2026
            LEFT JOIN candidate_election_status ces2024 ON c.candidate_id = ces2024.candidate_id AND ces2024.election_year = 2024
            LEFT JOIN speaker_vote_tracking svt ON c.candidate_id = svt.candidate_id
            WHERE c.candidate_id = %s
        """, (candidate_id,))
        candidate_info = cur.fetchone()

        if not candidate_info:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Candidate not found'}), 404
            flash('Candidate not found.', 'error')
            return redirect(url_for('private.speaker_votes'))

        legislator_name = f"{candidate_info[0]} {candidate_info[1]}"
        district_code = candidate_info[2]

        if notes is None or notes == '':
            notes = candidate_info[3] if candidate_info[3] else ''

        if assigned_caller is None:
            assigned_caller = candidate_info[4] if candidate_info[4] else None

        # Upsert the tracking record
        cur.execute("""
            INSERT INTO speaker_vote_tracking
            (candidate_id, legislator_name, district_code, commitment_status, confidence_level, notes, assigned_caller, created_by, last_contact_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (candidate_id) DO UPDATE SET
                commitment_status = EXCLUDED.commitment_status,
                confidence_level = EXCLUDED.confidence_level,
                notes = EXCLUDED.notes,
                assigned_caller = EXCLUDED.assigned_caller,
                last_contact_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
        """, (candidate_id, legislator_name, district_code, commitment_status, confidence_level, notes, assigned_caller, current_user.email))
        conn.commit()

        if is_ajax:
            return jsonify({'success': True, 'status': commitment_status, 'confidence': confidence_level})

        flash('Vote tracking updated.', 'success')
    except Exception as e:
        conn.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash(f'Error updating: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.speaker_votes'))


@private_bp.route('/speaker/<int:candidate_id>')
@require_feature_access('speaker_votes')
def speaker_vote_detail(candidate_id):
    """View details and contact history for a candidate."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get candidate with tracking info
        cur.execute("""
            SELECT c.candidate_id, c.first_name, c.last_name, c.email, c.phone1,
                   c.address, c.city, c.zip,
                   ces.district_code, c.incumbent,
                   svt.id as tracking_id, svt.commitment_status, svt.confidence_level,
                   svt.notes, svt.last_contact_at
            FROM candidates c
            JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
            LEFT JOIN speaker_vote_tracking svt ON c.candidate_id = svt.candidate_id
            WHERE c.candidate_id = %s AND ces.election_year = 2026
        """, (candidate_id,))
        candidate = cur.fetchone()

        if not candidate:
            flash('Candidate not found.', 'error')
            return redirect(url_for('private.speaker_votes'))

        # Get contact history
        cur.execute("""
            SELECT * FROM speaker_vote_contacts
            WHERE candidate_id = %s
            ORDER BY contact_date DESC
        """, (candidate_id,))
        contacts = cur.fetchall()

        return render_template('private/speaker_detail.html',
                             candidate=candidate,
                             contacts=contacts)
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/speaker/<int:candidate_id>/contact', methods=['POST'])
@require_feature_access('speaker_votes')
def log_speaker_contact(candidate_id):
    """Log a contact with a candidate about speaker vote."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get current status
        cur.execute("""
            SELECT commitment_status FROM speaker_vote_tracking WHERE candidate_id = %s
        """, (candidate_id,))
        current = cur.fetchone()
        status_before = current[0] if current else 'unknown'
        status_after = request.form.get('new_status', status_before)
        confidence_level = request.form.get('confidence_level', 5)

        # Ensure tracking record exists
        cur.execute("""
            INSERT INTO speaker_vote_tracking (candidate_id, commitment_status, confidence_level, created_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (candidate_id) DO NOTHING
        """, (candidate_id, 'unknown', 5, current_user.email))

        # Log the contact
        cur.execute("""
            INSERT INTO speaker_vote_contacts
            (candidate_id, contacted_by, contact_method, outcome, notes, status_before, status_after)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            candidate_id,
            request.form.get('contacted_by', current_user.email),
            request.form.get('contact_method'),
            request.form.get('outcome'),
            request.form.get('notes'),
            status_before,
            status_after
        ))

        # Update the tracking record
        cur.execute("""
            UPDATE speaker_vote_tracking
            SET commitment_status = %s,
                last_contact_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP,
                confidence_level = %s
            WHERE candidate_id = %s
        """, (status_after, confidence_level, candidate_id))

        conn.commit()
        flash('Contact logged.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error logging contact: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.speaker_vote_detail', candidate_id=candidate_id))


# =============================================================================
# CHALLENGER MANAGEMENT
# =============================================================================

@private_bp.route('/primaries/target/<int:target_id>/challenger/add', methods=['POST'])
@require_feature_access('secret_primaries')
def add_challenger(target_id):
    """Add a challenger to a primary target."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO secret_primary_challengers
            (target_id, first_name, last_name, email, phone, status, notes, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            target_id,
            request.form.get('first_name'),
            request.form.get('last_name'),
            request.form.get('email'),
            request.form.get('phone'),
            request.form.get('status', 'potential'),
            request.form.get('notes'),
            current_user.email
        ))
        challenger_id = cur.fetchone()[0]
        conn.commit()
        flash('Challenger added.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error adding challenger: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.secret_primaries'))


@private_bp.route('/primaries/challenger/<int:challenger_id>/update', methods=['POST'])
@require_feature_access('secret_primaries')
def update_challenger(challenger_id):
    """Update a challenger."""
    conn = get_db_connection()
    cur = conn.cursor()
    is_ajax = 'application/json' in request.headers.get('Accept', '')

    try:
        cur.execute("""
            UPDATE secret_primary_challengers
            SET first_name = %s, last_name = %s, email = %s, phone = %s,
                status = %s, notes = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (
            request.form.get('first_name'),
            request.form.get('last_name'),
            request.form.get('email'),
            request.form.get('phone'),
            request.form.get('status'),
            request.form.get('notes'),
            challenger_id
        ))
        conn.commit()

        if is_ajax:
            return jsonify({'success': True})
        flash('Challenger updated.', 'success')
    except Exception as e:
        conn.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash(f'Error updating challenger: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.secret_primaries'))


@private_bp.route('/primaries/challenger/<int:challenger_id>/delete', methods=['POST'])
@require_feature_access('secret_primaries')
def delete_challenger(challenger_id):
    """Delete a challenger."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM secret_primary_challengers WHERE id = %s", (challenger_id,))
        conn.commit()
        flash('Challenger removed.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error removing challenger: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.secret_primaries'))


@private_bp.route('/primaries/challenger/<int:challenger_id>/make-public', methods=['POST'])
@require_feature_access('secret_primaries')
def make_challenger_public(challenger_id):
    """Make a challenger public (visible to regular users)."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE secret_primary_challengers
            SET is_public = TRUE, made_public_at = CURRENT_TIMESTAMP, made_public_by = %s
            WHERE id = %s
        """, (current_user.email, challenger_id))
        conn.commit()
        flash('Challenger is now public.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.secret_primaries'))


@private_bp.route('/primaries/voter-search')
@require_feature_access('secret_primaries')
def search_voter_file():
    """Search voter file for potential challengers."""
    import requests
    import os

    first_name = request.args.get('first_name', '')
    last_name = request.args.get('last_name', '')
    city = request.args.get('city', '')

    if not last_name:
        return jsonify({'error': 'Last name required', 'results': []})

    try:
        # Call the voter file API on the secondary server
        api_key = os.environ.get('VOTER_FILE_API_KEY', '')
        params = {'last_name': last_name, 'api_key': api_key}
        if first_name:
            params['first_name'] = first_name
        if city:
            params['city'] = city

        resp = requests.get('http://138.197.36.143:5050/api/search', params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return jsonify({'results': data.get('voters', [])[:20]})  # Limit to 20
        else:
            return jsonify({'error': 'Voter file search failed: ' + resp.text, 'results': []})
    except Exception as e:
        return jsonify({'error': str(e), 'results': []})


@private_bp.route('/primaries/challenger/<int:challenger_id>/link-voter', methods=['POST'])
@require_feature_access('secret_primaries')
def link_challenger_to_voter(challenger_id):
    """Link a challenger to a voter file record."""
    import json

    conn = get_db_connection()
    cur = conn.cursor()
    is_ajax = 'application/json' in request.headers.get('Accept', '')

    try:
        voter_id = request.form.get('voter_id')
        voter_data = request.form.get('voter_data')  # JSON string

        cur.execute("""
            UPDATE secret_primary_challengers
            SET voter_id = %s, voter_data = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (voter_id, voter_data, challenger_id))

        # Also update contact info from voter data if provided
        if voter_data:
            vd = json.loads(voter_data)
            cur.execute("""
                UPDATE secret_primary_challengers
                SET address = COALESCE(address, %s),
                    city = COALESCE(city, %s),
                    zip = COALESCE(zip, %s)
                WHERE id = %s AND (address IS NULL OR address = '')
            """, (
                vd.get('address', ''),
                vd.get('city', ''),
                vd.get('zip', ''),
                challenger_id
            ))

        conn.commit()

        if is_ajax:
            return jsonify({'success': True})
        flash('Voter linked.', 'success')
    except Exception as e:
        conn.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash(f'Error: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.secret_primaries'))


# Context processor to make access check available in all templates
@private_bp.app_context_processor
def inject_private_features():
    """Make private feature access available in templates."""
    return {
        'user_private_features': get_user_private_features() if current_user.is_authenticated else []
    }
