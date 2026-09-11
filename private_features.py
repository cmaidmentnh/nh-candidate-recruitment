"""
Private Features Module
Handles secret primary tracking and speaker vote counting.
Access is controlled via private_feature_access table - only superadmin can grant access.
"""

from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime
import logging
import secrets
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

private_bp = Blueprint('private', __name__, url_prefix='/private')

# Will be set by init_private_features()
get_db_connection = None
release_db_connection = None
is_super_admin = None
upload_to_storage = None
SUPER_ADMIN_EMAIL = None


def init_private_features(db_conn_func, db_release_func, super_admin_func, super_admin_email,
                          storage_upload=None):
    """Initialize the module with database functions from main app."""
    global get_db_connection, release_db_connection, is_super_admin, SUPER_ADMIN_EMAIL
    global upload_to_storage
    get_db_connection = db_conn_func
    release_db_connection = db_release_func
    is_super_admin = super_admin_func
    SUPER_ADMIN_EMAIL = super_admin_email
    upload_to_storage = storage_upload


def has_feature_access(feature_slug):
    """Check if current user has access to a specific feature."""
    if not current_user.is_authenticated:
        return False

    # Superadmin always has access
    email = getattr(current_user, 'email', None)
    if email and email.lower() == SUPER_ADMIN_EMAIL.lower():
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
    email = getattr(current_user, 'email', None)
    if email and email.lower() == SUPER_ADMIN_EMAIL.lower():
        return ['secret_primaries', 'speaker_votes', 'campaign_plan', 'digest']

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
                             features=['secret_primaries', 'speaker_votes', 'campaign_plan', 'digest'])
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
    """Delete a primary target."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM secret_primary_targets WHERE id = %s", (target_id,))
        conn.commit()
        flash('Target removed.', 'success')
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


# =============================================================================
# CAMPAIGN BATTLE PLAN  (district-by-district strategy)
# =============================================================================
import re as _re

# posture buckets: (key, label, color)
# Sub-buckets (key, short label, color) that roll up to a main category (PLAN_GROUPS).
PLAN_BUCKETS = [
    ('lean_r',     'Lean R',     '#c6312d'),
    ('lean_d',     'Lean D',     '#1d4e89'),
    ('safe_r',     'Safe R',     '#1e7a3c'),
    ('safe_d',     'Safe D',     '#7a8aa3'),
    ('watch',      'Watch',      '#d97706'),
    ('unassigned', 'Unassigned', '#cdd4df'),
]
PLAN_GROUPS = [
    ('Spend',      ['lean_r', 'lean_d']),
    ('No Spend',   ['safe_r', 'safe_d']),
    ('Watch',      ['watch']),
    ('Unassigned', ['unassigned']),
]
PLAN_MAIN = {k: g for g, keys in PLAN_GROUPS for k in keys}
PLAN_BUCKET_KEYS = [b[0] for b in PLAN_BUCKETS]
PLAN_BUCKET_LABEL = {b[0]: b[1] for b in PLAN_BUCKETS}
PLAN_CHANNELS = ['Digital', 'Video', 'Mail', 'Doors', 'Phones', 'Text', 'Events']


def _dist_sortkey(code):
    m = _re.search(r'(\d+)\s*$', code or '')
    county = _re.sub(r'\s*\d+\s*$', '', code or '')
    return (county, int(m.group(1)) if m else 0)


# Named universes. The mask is the bitmask over SEGMENTS; these are the combinations
# anyone actually buys, so the row control is a plain dropdown rather than five toggles.
UNIVERSE_PRESETS = [
    (1,  'Reliable R only'),
    (2,  'R drop-off only'),
    (3,  'Modeled R (reliable + drop-off)'),
    (7,  'Modeled R + undeclared voters'),
    (11, 'Modeled R + undeclared drop-off'),
    (15, 'Modeled R + all undeclared'),
    (4,  'Undeclared voters only'),
    (12, 'All undeclared only'),
    (31, 'Everyone on the checklist'),
]

SEGMENTS = [
    (1,  'r_reliable',  'Reliable R',        'Modeled R who voted the 2024 general'),
    (2,  'r_dropoff',   'R drop-off',        'Modeled R who sat out 2024'),
    (4,  'und_voter',   'Undeclared voters', 'Undeclared, not modeled R, voted 2022 or 2024'),
    (8,  'und_dropoff', 'Undeclared drop-off', 'Undeclared, not modeled R, voted neither'),
    (16, 'dem',         'Democrats',         'Registered D or D primary voter'),
]


def _district_sort_key(code):
    """Belknap 1, Belknap 2, ... Belknap 10 — county alphabetical, number numeric."""
    parts = (code or '').rsplit(' ', 1)
    if len(parts) == 2 and parts[1].isdigit():
        return (parts[0], int(parts[1]))
    return (code or '', 0)


CREATIVE_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'pdf', 'mp4', 'mov'}
MAX_CREATIVE_BYTES = 40 * 1024 * 1024


def _afp_rank(rating):
    """AFP's own verdict wording, bucketed. Mirrors _afp_alignment in app.py exactly: AFP does
    not score the survey, so this only sorts the words Sarah Scott sends. Never re-scores."""
    t = (rating or '').strip().lower()
    if not t:
        return (None, '')
    if t.startswith('good'):
        return (-1, 'Aligned')
    if "can't endorse" in t or 'cannot endorse' in t:
        return (1, "Can't endorse")
    if t.startswith('bad'):
        return (0, 'Bad')
    if 'not great' in t:
        return (1, 'Not great')
    if 'not bad' in t:
        return (2, 'Not bad')
    return (3, 'Qualified')


def _tier_word(t):
    """How a tier reads in a sentence. 0 and NULL are both "no tier"."""
    return ('Tier %d' % t) if t else 'no tier'


def _qty_word(q):
    q = float(q or 0)
    return str(int(q)) if q == int(q) else ('%.1f' % q)


def _cost_of(qty_for_district, sizes, tactics):
    """What a district's chosen quantities cost. Mirrors cost() in spend_plan.html: mail bills
    against households, texts against cells, and an unpriced tactic counts units at zero."""
    total = 0.0
    for universe, byk in (qty_for_district or {}).items():
        sz = sizes.get(universe) or {'households': 0, 'cells': 0, 'voters': 0}
        for t in tactics:
            row = byk.get(t['key'])
            q = float(row['qty']) if row else 0.0
            if q <= 0:
                continue
            rate = row.get('rate') if row and row.get('rate') is not None else t['rate']
            if t['unit'] == 'per_household':
                total += q * (sz.get('households') or 0) * (rate or 0)
            elif t['unit'] == 'per_cell':
                total += q * (sz.get('cells') or 0) * (rate or 0)
            elif t['unit'] == 'dollars':
                total += q
            elif t['unit'] == 'per_unit':
                total += 0.0 if rate is None else q * rate
    return total


@private_bp.route('/spend-plan')
@require_feature_access('campaign_plan')
def spend_plan():
    """District-by-district spend planning: pick the universe, pick the tactics, pick how
    many of each, see what it costs. Every district is listed in district order."""
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT full_district_code, MAX(county_name), MAX(seat_count),
                   MAX(pvi), MAX(pvi_rating),
                   STRING_AGG(DISTINCT CASE WHEN ward IS NOT NULL AND ward <> 0
                              THEN town || ' Ward ' || ward ELSE town END, ', ') AS towns
            FROM districts WHERE full_district_code IS NOT NULL
            GROUP BY full_district_code
        """)
        districts = [{'code': r[0], 'county': r[1], 'seats': r[2], 'pvi': float(r[3]) if r[3] is not None else None,
                      'rating': r[4], 'towns': r[5]} for r in cur.fetchall()]

        cur.execute("SELECT district_code, mask, voters, households, cells FROM district_universe")
        universe = {(r[0], r[1]): {'voters': r[2], 'households': r[3], 'cells': r[4]} for r in cur.fetchall()}

        cur.execute("""SELECT tactic_key, label, unit, rate, qty_label, grp, sort_order
                       FROM spend_tactic WHERE active ORDER BY sort_order""")
        tactics = [{'key': r[0], 'label': r[1], 'unit': r[2],
                    'rate': float(r[3]) if r[3] is not None else None,
                    'qty_label': r[4], 'grp': r[5]} for r in cur.fetchall()]

        cur.execute("SELECT district_code, mask, include, notes, tier FROM district_spend")
        plan = {r[0]: {'mask': r[1], 'include': r[2], 'notes': r[3] or '', 'tier': r[4]}
                for r in cur.fetchall()}

        cur.execute("""SELECT r.district_code, r.kind, r.spans,
                              (SELECT string_agg(b.base, ', ' ORDER BY b.base)
                                 FROM district_floterial_base b WHERE b.floterial = r.district_code),
                              (SELECT string_agg(b.base, ', ' ORDER BY b.base)
                                 FROM district_floterial_base b
                                 JOIN district_spend s2 ON s2.district_code = b.base AND s2.tier IS NOT NULL
                                WHERE b.floterial = r.district_code),
                              (SELECT string_agg(f.floterial, ', ' ORDER BY f.floterial)
                                 FROM district_floterial_base f WHERE f.base = r.district_code)
                       FROM district_relation r""")
        relation = {r[0]: {'kind': r[1], 'spans': r[2], 'bases': r[3], 'bases_covered': r[4],
                           'carries': r[5]} for r in cur.fetchall()}

        cur.execute("SELECT district_code, uni, voters, households, cells FROM district_model_universe")
        model_uni = {}
        for dc, uni, v, hh, ce in cur.fetchall():
            model_uni.setdefault(dc, {})[uni] = {'voters': v, 'households': hh, 'cells': ce}

        # A floterial buys nothing itself: what it RECEIVES is decided by the tiers of the
        # bases it rides on, weighted by where its households sit. A tier on a floterial is
        # therefore close to decorative, and this is what makes that visible.
        cur.execute("""
            SELECT b.floterial, b.base,
                   COALESCE(bh.h, 0) AS households,
                   bs.tier,
                   COALESCE(bm.pieces, 0) AS pieces
              FROM district_floterial_base b
              LEFT JOIN (SELECT district_code, sum(households) h
                           FROM district_model_universe GROUP BY 1) bh ON bh.district_code = b.base
              LEFT JOIN district_spend bs ON bs.district_code = b.base AND bs.tier IS NOT NULL
              LEFT JOIN (SELECT district_code, max(qty) pieces
                           FROM district_spend_item WHERE tactic_key = 'mail' GROUP BY 1) bm
                     ON bm.district_code = b.base
             ORDER BY b.floterial, COALESCE(bh.h,0) DESC""")
        ride = {}
        for f, base, hh, btier, pieces in cur.fetchall():
            ride.setdefault(f, []).append({'base': base, 'households': int(hh or 0),
                                           'tier': btier, 'pieces': float(pieces or 0)})

        cur.execute("SELECT district_code, town, town_r, pct FROM district_top_r_town")
        topr = {r[0]: {'town': r[1], 'r': r[2], 'pct': r[3]} for r in cur.fetchall()}

        cur.execute("SELECT district_code, reg_r, reg_d, reg_u, reg_total FROM district_registration")
        reg = {r[0]: {'r': r[1], 'd': r[2], 'u': r[3], 'total': r[4]} for r in cur.fetchall()}

        # How the seat has actually behaved, not just how it models.
        cur.execute("""SELECT district_code, year, seats, r_seats, d_seats, r_votes, d_votes,
                              last_winner_votes, first_loser_votes
                       FROM district_past_results ORDER BY year DESC""")
        past = {}
        for dc, yr, seats, rs, ds, rv, dv, lw, fl in cur.fetchall():
            past.setdefault(dc, []).append({
                'year': yr, 'seats': seats, 'r_seats': rs, 'd_seats': ds,
                'r_votes': rv, 'd_votes': dv,
                'r_share': round(100.0 * rv / (rv + dv), 1) if (rv + dv) else None,
                'margin': (lw - fl) if (lw is not None and fl is not None) else None,
                'cands': []})

        cur.execute("""SELECT district_code, r_votes, d_votes, r_share, towns, towns_with_data
                       FROM district_2018_replay""")
        replay18 = {r[0]: {'r_votes': r[1], 'd_votes': r[2], 'r_share': float(r[3]),
                           'towns': r[4], 'towns_data': r[5]} for r in cur.fetchall()}

        cur.execute("""SELECT district_code, year, name, party, votes, won
                       FROM district_past_candidates ORDER BY district_code, year DESC, rank""")
        for dc, yr, nm, pty, vt, won in cur.fetchall():
            for blk in past.get(dc, []):
                if blk['year'] == yr:
                    blk['cands'].append({'name': nm, 'party': pty, 'votes': vt, 'won': won})
                    break

        cur.execute("SELECT district_code, universe, tactic_key, qty, rate_override FROM district_spend_item")
        qty_by_district = {}
        for dc, uni, tk, q, ro in cur.fetchall():
            qty_by_district.setdefault(dc, {}).setdefault(uni, {})[tk] = {
                'qty': float(q), 'rate': float(ro) if ro is not None else None}

        # Who is actually on the November ballot here, so a district is never planned blind.
        cur.execute("""SELECT district_code, party,
                              STRING_AGG(first_name || ' ' || last_name, ', ' ORDER BY last_name)
                       FROM filings
                       WHERE election_year = 2026 AND office = 'State Representative'
                         AND result <> 'lost'
                       GROUP BY district_code, party""")
        nominees = {}
        for dc, party, names in cur.fetchall():
            nominees.setdefault(dc, {})[party] = names

        # What the candidates themselves said they have and need. An unanswered question is
        # stored NULL and must never read as a No: only an explicit false is a need. Same rule
        # progress_checkin() applies in campaign_progress.py.
        cur.execute("""SELECT f.district_code,
                              c.first_name || ' ' || c.last_name,
                              p.intake_submitted_at,
                              p.signs_have, p.signs_count, p.lit_have, p.headshot_have,
                              p.walkbooks_have, p.fundraising_amount, p.cash_on_hand,
                              p.anticipated_raise, p.intake_notes,
                              COALESCE(NULLIF(c.website_url,''), NULLIF(c.external_campaign_url,'')),
                              NULLIF(c.donate_url,''), NULLIF(c.facebook_url,''),
                              (SELECT sv.rating FROM candidate_surveys sv
                                WHERE sv.candidate_id = f.candidate_id
                                  AND sv.survey_org = 'AFP' LIMIT 1),
                              EXISTS (SELECT 1 FROM candidate_surveys sv
                                       WHERE sv.candidate_id = f.candidate_id
                                         AND sv.survey_org = 'AFP'),
                              c.materials_optout, c.materials_optout_note
                       FROM filings f
                       JOIN candidates c ON c.candidate_id = f.candidate_id
                       LEFT JOIN candidate_campaign_progress p ON p.candidate_id = f.candidate_id
                       WHERE f.election_year = 2026 AND f.office = 'State Representative'
                         AND f.party = 'R' AND f.result <> 'lost'
                       ORDER BY f.district_code, c.last_name, c.first_name""")
        cands = {}
        for r in cur.fetchall():
            cands.setdefault(r[0], []).append({
                'name': r[1], 'answered': r[2].strftime('%b %-d') if r[2] else None,
                'signs': r[3], 'signs_count': r[4], 'lit': r[5], 'headshot': r[6],
                'walkbooks': r[7],
                'raised': float(r[8]) if r[8] is not None else None,
                'coh': float(r[9]) if r[9] is not None else None,
                'more': float(r[10]) if r[10] is not None else None,
                'note': r[11], 'website': r[12], 'donate': r[13], 'facebook': r[14],
                'afp': r[15], 'afp_done': r[16],
                'afp_label': _afp_rank(r[15])[1], 'afp_rank': _afp_rank(r[15])[0],
                'optout': r[17], 'optout_note': r[18]})

        # Every edit to this plan, so four people editing it can see each other's work. The
        # trigger stores a row as it was BEFORE the change, so each row's value is the state
        # it moved away from: pairing it with the next row (and the last with the live value)
        # turns that into a readable "was X, now Y".
        history = {}
        cur.execute("""SELECT district_code, tier, mask, include, notes, changed_by, changed_at
                       FROM district_spend_history WHERE op = 'update'
                       ORDER BY district_code, changed_at""")
        by_district = {}
        for dc, tier, mask, inc, notes, by, at in cur.fetchall():
            by_district.setdefault(dc, []).append(
                {'tier': tier, 'mask': mask, 'include': inc, 'notes': notes or '',
                 'by': by, 'at': at})
        for dc, seq in by_district.items():
            live = plan.get(dc, {})
            tail = {'tier': live.get('tier'), 'mask': live.get('mask'),
                    'include': live.get('include'), 'notes': live.get('notes', '')}
            for i, was in enumerate(seq):
                now = seq[i + 1] if i + 1 < len(seq) else tail
                ch = []
                if was['tier'] != now['tier']:
                    ch.append(['Tier', _tier_word(was['tier']), _tier_word(now['tier'])])
                if bool(was['include']) != bool(now['include']):
                    ch.append(['In the plan', 'no' if not was['include'] else 'yes',
                               'no' if not now['include'] else 'yes'])
                if was['mask'] != now['mask']:
                    ch.append(['Universe', str(was['mask']), str(now['mask'])])
                if (was['notes'] or '') != (now['notes'] or ''):
                    ch.append(['Notes', was['notes'] or 'empty', now['notes'] or 'empty'])
                if ch:
                    history.setdefault(dc, []).append(
                        {'ts': was['at'], 'by': was['by'] or '', 'changes': ch})

        tac_label = {t['key']: t['label'] for t in tactics}
        cur.execute("""SELECT district_code, universe, tactic_key, qty, changed_at
                       FROM district_spend_item_history WHERE op = 'update'
                       ORDER BY district_code, universe, tactic_key, changed_at""")
        by_item = {}
        for dc, uni_, tk, q, at in cur.fetchall():
            by_item.setdefault((dc, uni_, tk), []).append((float(q or 0), at))
        for (dc, uni_, tk), seq in by_item.items():
            cell = qty_by_district.get(dc, {}).get(uni_, {}).get(tk)
            live_q = float(cell['qty']) if cell else 0.0
            for i, (was_q, at) in enumerate(seq):
                now_q = seq[i + 1][0] if i + 1 < len(seq) else live_q
                if was_q == now_q:
                    continue
                history.setdefault(dc, []).append({
                    'ts': at, 'by': '',
                    'changes': [['%s, %s' % (tac_label.get(tk, tk), uni_),
                                 _qty_word(was_q), _qty_word(now_q)]]})

        for dc in history:
            history[dc].sort(key=lambda e: e['ts'], reverse=True)
            del history[dc][40:]
            for e in history[dc]:
                e['at'] = e.pop('ts').strftime('%b %-d, %-I:%M %p')

        pieces = _load_pieces(cur)

        cur.execute("SELECT amount FROM spend_budget WHERE key='program'")
        brow = cur.fetchone()
        budget = float(brow[0]) if brow else 600000.0

        for d in districts:
            code = d['code']
            p = plan.get(code, {})
            d['mask'] = p.get('mask', 3)
            d['include'] = p.get('include', False)
            d['notes'] = p.get('notes', '')
            d['tier'] = p.get('tier')
            d['reg'] = reg.get(code, {'r': 0, 'd': 0, 'u': 0, 'total': 0})
            d['past'] = past.get(code, [])
            d['topr'] = topr.get(code)
            d['model'] = model_uni.get(code, {})
            d['rel'] = relation.get(code)
            d['ride'] = ride.get(code, [])
            d['r2018'] = replay18.get(code)
            d['qty'] = qty_by_district.get(code, {})
            d['universe'] = universe.get((code, d['mask']), {'voters': 0, 'households': 0, 'cells': 0})
            d['all_universe'] = {m: universe.get((code, m), {'voters': 0, 'households': 0, 'cells': 0})
                                 for m in range(1, 32)}
            d['nominees'] = nominees.get(code, {})
            d['cands'] = cands.get(code, [])
            d['hist'] = history.get(code, [])

        districts.sort(key=lambda d: _district_sort_key(d['code']))
        return render_template('private/spend_plan.html',
                               districts=districts, tactics=tactics, segments=SEGMENTS,
                               presets=UNIVERSE_PRESETS,
                               preset_masks=[m for m, _ in UNIVERSE_PRESETS],
                               pieces=pieces, budget=budget)
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/spend-plan/export.csv')
@require_feature_access('campaign_plan')
def spend_plan_export():
    """The plan as a spreadsheet: one row per district per universe per tactic, plus a row per
    district carrying the tier and totals. Field tactics are unpriced, so their cost column is
    blank rather than zero - a blank says "not quoted", a zero says "free"."""
    import csv, io as _io
    from flask import Response
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT tactic_key, label, unit, rate, grp FROM spend_tactic
                       WHERE active ORDER BY sort_order""")
        tactics = [{'key': r[0], 'label': r[1], 'unit': r[2],
                    'rate': float(r[3]) if r[3] is not None else None, 'grp': r[4]}
                   for r in cur.fetchall()]
        tac = {t['key']: t for t in tactics}

        cur.execute("""SELECT full_district_code, MAX(county_name), MAX(seat_count),
                              MAX(pvi), MAX(pvi_rating)
                       FROM districts WHERE full_district_code IS NOT NULL GROUP BY 1""")
        meta = {r[0]: {'county': r[1], 'seats': r[2], 'pvi': r[3], 'rating': r[4]}
                for r in cur.fetchall()}

        cur.execute("SELECT district_code, tier, mask, include FROM district_spend")
        plan = {r[0]: {'tier': r[1], 'mask': r[2], 'include': r[3]} for r in cur.fetchall()}

        cur.execute("SELECT district_code, uni, voters, households, cells FROM district_model_universe")
        sizes = {}
        for dc, u, v, hh, ce in cur.fetchall():
            sizes.setdefault(dc, {})[u] = {'voters': v, 'households': hh, 'cells': ce}
        cur.execute("SELECT district_code, mask, voters, households, cells FROM district_universe")
        base_uni = {(r[0], r[1]): {'voters': r[2], 'households': r[3], 'cells': r[4]}
                    for r in cur.fetchall()}

        cur.execute("SELECT district_code, universe, tactic_key, qty, rate_override FROM district_spend_item")
        items = {}
        for dc, u, tk, q, ro in cur.fetchall():
            items.setdefault(dc, {}).setdefault(u, {})[tk] = {
                'qty': float(q), 'rate': float(ro) if ro is not None else None}

        cur.execute("SELECT district_code, kind FROM district_relation")
        kind = dict(cur.fetchall())

        cur.execute("""SELECT district_code,
                              STRING_AGG(first_name || ' ' || last_name, '; ' ORDER BY last_name)
                       FROM filings
                       WHERE election_year = 2026 AND office = 'State Representative'
                         AND party = 'R' AND result <> 'lost'
                       GROUP BY district_code""")
        nominees = dict(cur.fetchall())
    finally:
        cur.close(); release_db_connection(conn)

    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(['District', 'County', 'Seats', 'Floterial', 'Tier', 'PVI', 'Rating',
                'R nominees', 'Universe', 'Tactic', 'Quantity', 'Unit', 'Reach', 'Cost'])
    for code in sorted(plan, key=_district_sort_key):
        m = meta.get(code, {})
        p = plan[code]
        head = [code, m.get('county') or '', m.get('seats') or '',
                'yes' if kind.get(code) == 'floterial' else '',
                p['tier'] or '', m.get('pvi') if m.get('pvi') is not None else '',
                m.get('rating') or '', nominees.get(code, '')]
        rows = items.get(code, {})
        if not rows:
            w.writerow(head + ['', '', '', '', '', ''])
            continue
        for universe in sorted(rows):
            sz = (sizes.get(code, {}).get(universe)
                  or base_uni.get((code, p['mask'] or 3))
                  or {'households': 0, 'cells': 0, 'voters': 0})
            for tk, row in sorted(rows[universe].items()):
                t = tac.get(tk)
                if not t or row['qty'] <= 0:
                    continue
                rate = row['rate'] if row['rate'] is not None else t['rate']
                q = row['qty']
                if t['unit'] == 'per_household':
                    reach, cost = q * (sz['households'] or 0), q * (sz['households'] or 0) * (rate or 0)
                elif t['unit'] == 'per_cell':
                    reach, cost = q * (sz['cells'] or 0), q * (sz['cells'] or 0) * (rate or 0)
                elif t['unit'] == 'dollars':
                    reach, cost = '', q
                else:
                    reach, cost = q, (None if rate is None else q * rate)
                w.writerow(head + [universe, t['label'], _qty_word(q), t['unit'],
                                   int(reach) if reach != '' else '',
                                   '' if cost is None else round(cost, 2)])
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=spend_plan.csv'})


# =============================================================================
# PIECES - what we actually produced, as opposed to what we planned
# =============================================================================

PIECE_STATUS = ('draft', 'scheduled', 'delivered')
INVOICE_STATUS = ('unbilled', 'unpaid', 'paid')


def _piece_sizes(cur):
    """Universe sizes per district, both the modelled universes and the old single mask."""
    cur.execute("SELECT district_code, uni, voters, households, cells FROM district_model_universe")
    sizes = {}
    for dc, u, v, hh, ce in cur.fetchall():
        sizes.setdefault(dc, {})[u] = {'voters': v or 0, 'households': hh or 0, 'cells': ce or 0}
    cur.execute("""SELECT u.district_code, u.voters, u.households, u.cells
                     FROM district_universe u
                     JOIN district_spend s ON s.district_code = u.district_code
                                          AND s.mask = u.mask""")
    for dc, v, hh, ce in cur.fetchall():
        sizes.setdefault(dc, {})['base'] = {'voters': v or 0, 'households': hh or 0, 'cells': ce or 0}
    return sizes


def _piece_snapshot(cur, sizes, tactic, universe, codes):
    """What one drop of `tactic` to each of `codes` reaches and costs, right now.

    Snapshotted onto the row so a later change to the universe never rewrites history. A
    household-based or cell-based piece reaches the whole universe once; a digital flight and
    an unpriced field tactic carry the district's planned figure instead, because "one drop"
    is not a meaningful unit for either."""
    unit, rate = tactic['unit'], tactic['rate']
    planned = {}
    if unit in ('dollars', 'per_unit'):
        cur.execute("""SELECT district_code, qty FROM district_spend_item
                        WHERE tactic_key = %s AND universe = %s""",
                    (tactic['key'], universe))
        planned = {r[0]: float(r[1]) for r in cur.fetchall()}
    out = {}
    for code in codes:
        sz = (sizes.get(code, {}).get(universe)
              or sizes.get(code, {}).get('base')
              or {'households': 0, 'cells': 0})
        if unit == 'per_household':
            q = float(sz['households']); cost = q * float(rate or 0)
        elif unit == 'per_cell':
            q = float(sz['cells']); cost = q * float(rate or 0)
        elif unit == 'dollars':
            q = planned.get(code, 0.0); cost = q
        else:
            q = planned.get(code, 0.0)
            cost = None if rate is None else q * float(rate)
        out[code] = (q, cost)
    return out


def _load_pieces(cur):
    """Every piece with its districts, ready for both the editor and the district pane."""
    cur.execute("""SELECT p.id, p.name, p.tactic_key, p.universe, p.drop_date, p.status,
                          p.default_file_url, p.default_content_type, p.notes,
                          p.invoice_number, p.invoice_amount, p.invoice_status,
                          p.invoice_due, p.paid_at, p.created_by, t.label, t.unit, t.grp
                     FROM spend_piece p
                     JOIN spend_tactic t ON t.tactic_key = p.tactic_key
                    ORDER BY p.drop_date NULLS LAST, p.id""")
    pieces = []
    for r in cur.fetchall():
        pieces.append({
            'id': r[0], 'name': r[1], 'tactic_key': r[2], 'universe': r[3],
            'drop_date': r[4].isoformat() if r[4] else None, 'status': r[5],
            'file_url': r[6], 'content_type': r[7], 'notes': r[8] or '',
            'invoice_number': r[9] or '',
            'invoice_amount': float(r[10]) if r[10] is not None else None,
            'invoice_status': r[11],
            'invoice_due': r[12].isoformat() if r[12] else None,
            'paid_at': r[13].isoformat() if r[13] else None,
            'created_by': r[14] or '', 'tactic_label': r[15], 'unit': r[16], 'grp': r[17],
            'districts': [], 'planned': 0.0, 'reach': 0.0})
    by_id = {p['id']: p for p in pieces}
    cur.execute("""SELECT piece_id, district_code, file_url, content_type, quantity, planned_cost
                     FROM spend_piece_district ORDER BY district_code""")
    for pid, code, url, ct, q, cost in cur.fetchall():
        p = by_id.get(pid)
        if not p:
            continue
        p['districts'].append({'code': code, 'file_url': url, 'content_type': ct,
                               'quantity': float(q or 0),
                               'cost': float(cost) if cost is not None else None})
        p['planned'] += float(cost or 0)
        p['reach'] += float(q or 0)
    return pieces


@private_bp.route('/spend-plan/pieces')
@require_feature_access('campaign_plan')
def spend_pieces():
    """Every piece we are making, what it costs, where it goes and whether it is paid for."""
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT tactic_key, label, unit, rate, qty_label, grp
                         FROM spend_tactic WHERE active ORDER BY sort_order""")
        tactics = [{'key': r[0], 'label': r[1], 'unit': r[2],
                    'rate': float(r[3]) if r[3] is not None else None,
                    'qty_label': r[4], 'grp': r[5]} for r in cur.fetchall()]
        sizes = _piece_sizes(cur)

        cur.execute("""SELECT s.district_code, s.tier,
                              (SELECT MAX(d.county_name) FROM districts d
                                WHERE d.full_district_code = s.district_code),
                              (SELECT MAX(d.seat_count) FROM districts d
                                WHERE d.full_district_code = s.district_code),
                              r.kind
                         FROM district_spend s
                         LEFT JOIN district_relation r ON r.district_code = s.district_code
                        WHERE s.tier IS NOT NULL""")
        districts = [{'code': r[0], 'tier': r[1], 'county': r[2], 'seats': r[3] or 0,
                      'floterial': r[4] == 'floterial',
                      'uni': sizes.get(r[0], {})} for r in cur.fetchall()]
        districts.sort(key=lambda d: _district_sort_key(d['code']))

        # How many drops of each tactic the plan calls for, so a piece can be counted against it
        cur.execute("""SELECT district_code, universe, tactic_key, qty FROM district_spend_item""")
        planned = {}
        for dc, u, tk, q in cur.fetchall():
            planned.setdefault(dc, {}).setdefault(u, {})[tk] = float(q)

        cur.execute("SELECT amount FROM spend_budget WHERE key='program'")
        brow = cur.fetchone()
        return render_template('private/spend_pieces.html',
                               pieces=_load_pieces(cur), tactics=tactics,
                               districts=districts, planned=planned,
                               budget=float(brow[0]) if brow else 600000.0)
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/spend-plan/piece/save', methods=['POST'])
@require_feature_access('campaign_plan')
def spend_piece_save():
    """Create a piece, or change the fields actually sent. Never touches a field left out."""
    d = request.get_json(silent=True) or {}
    who = current_user.email if current_user.is_authenticated else 'admin'
    conn = get_db_connection(); cur = conn.cursor()
    try:
        pid = d.get('id')
        if not pid:
            name = (d.get('name') or '').strip()
            tk = (d.get('tactic_key') or '').strip()
            if not name or not tk:
                return jsonify({'ok': False, 'error': 'A piece needs a name and a tactic.'}), 400
            cur.execute("""INSERT INTO spend_piece (name, tactic_key, universe, drop_date,
                                                    notes, created_by, updated_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (name[:200], tk, (d.get('universe') or 'persuade')[:20],
                         d.get('drop_date') or None, d.get('notes') or None, who, who))
            pid = cur.fetchone()[0]
            conn.commit()
            return jsonify({'ok': True, 'id': pid})

        sets, vals = [], []
        for col in ('name', 'tactic_key', 'universe', 'drop_date', 'notes',
                    'invoice_number', 'invoice_amount', 'invoice_due', 'paid_at'):
            if col in d:
                sets.append(col + ' = %s')
                vals.append(d[col] if d[col] not in ('',) else None)
        if 'status' in d:
            if d['status'] not in PIECE_STATUS:
                return jsonify({'ok': False, 'error': 'bad status'}), 400
            sets.append('status = %s'); vals.append(d['status'])
        if 'invoice_status' in d:
            if d['invoice_status'] not in INVOICE_STATUS:
                return jsonify({'ok': False, 'error': 'bad invoice status'}), 400
            sets.append('invoice_status = %s'); vals.append(d['invoice_status'])
        if not sets:
            return jsonify({'ok': True, 'id': pid})
        sets.append('updated_by = %s'); vals.append(who)
        sets.append('updated_at = now()')
        vals.append(pid)
        cur.execute('UPDATE spend_piece SET ' + ', '.join(sets) + ' WHERE id = %s', vals)
        conn.commit()
        return jsonify({'ok': True, 'id': pid})
    except Exception as e:
        conn.rollback()
        logger.error('piece save failed: %s', e)
        return jsonify({'ok': False, 'error': 'Could not save.'}), 500
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/spend-plan/piece/districts', methods=['POST'])
@require_feature_access('campaign_plan')
def spend_piece_districts():
    """Set which districts a piece goes to.

    Districts already on the piece keep the quantity and cost they were snapshotted with;
    only newly added ones are priced at today's universe. A floterial cannot be added to a
    household or cell based piece at all: its candidate rides on the base district's piece,
    and billing it separately is the double-count this planner exists to avoid."""
    d = request.get_json(silent=True) or {}
    pid = d.get('id')
    codes = [c for c in (d.get('codes') or []) if c]
    if not pid:
        return jsonify({'ok': False, 'error': 'no piece'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT p.tactic_key, p.universe, t.unit, t.rate
                         FROM spend_piece p JOIN spend_tactic t ON t.tactic_key = p.tactic_key
                        WHERE p.id = %s""", (pid,))
        row = cur.fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'no such piece'}), 404
        tactic = {'key': row[0], 'unit': row[2],
                  'rate': float(row[3]) if row[3] is not None else None}
        universe = row[1]

        if tactic['unit'] in ('per_household', 'per_cell') and codes:
            cur.execute("""SELECT district_code FROM district_relation
                            WHERE kind = 'floterial' AND district_code = ANY(%s)""", (codes,))
            flo = [r[0] for r in cur.fetchall()]
            if flo:
                return jsonify({'ok': False, 'error':
                                'These are floterials and ride on their base district\'s '
                                'piece, so they cannot be bought here: ' + ', '.join(flo)}), 400

        cur.execute('SELECT district_code FROM spend_piece_district WHERE piece_id = %s', (pid,))
        have = {r[0] for r in cur.fetchall()}
        want = set(codes)
        gone, fresh = have - want, want - have

        if gone:
            cur.execute("""DELETE FROM spend_piece_district
                            WHERE piece_id = %s AND district_code = ANY(%s)""",
                        (pid, list(gone)))
        if fresh:
            snap = _piece_snapshot(cur, _piece_sizes(cur), tactic, universe, fresh)
            for code in sorted(fresh):
                q, cost = snap[code]
                cur.execute("""INSERT INTO spend_piece_district
                                 (piece_id, district_code, quantity, planned_cost)
                               VALUES (%s,%s,%s,%s)""", (pid, code, q, cost))
        conn.commit()
        cur.execute("""SELECT district_code, file_url, content_type, quantity, planned_cost
                         FROM spend_piece_district WHERE piece_id = %s ORDER BY district_code""",
                    (pid,))
        out = [{'code': r[0], 'file_url': r[1], 'content_type': r[2],
                'quantity': float(r[3] or 0),
                'cost': float(r[4]) if r[4] is not None else None} for r in cur.fetchall()]
        return jsonify({'ok': True, 'added': len(fresh), 'removed': len(gone),
                        'districts': out,
                        'planned': sum(x['cost'] or 0 for x in out),
                        'reach': sum(x['quantity'] for x in out)})
    except Exception as e:
        conn.rollback()
        logger.error('piece districts failed: %s', e)
        return jsonify({'ok': False, 'error': 'Could not save.'}), 500
    finally:
        cur.close(); release_db_connection(conn)


def _store_art(f):
    """Validate and upload one artwork file. Returns (url, error)."""
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in CREATIVE_EXT:
        return None, '.%s is not an allowed file type' % ext
    f.seek(0, 2); size = f.tell(); f.seek(0)
    if size > MAX_CREATIVE_BYTES:
        return None, 'over 40MB'
    if upload_to_storage is None:
        return None, 'file storage is not configured'
    key = 'spend_piece/%s_%s' % (secrets.token_hex(4), secure_filename(f.filename))
    url = upload_to_storage(f, key)
    return (url, None) if url else (None, 'upload failed')


def _match_district(filename, codes):
    """Which district a per-district artwork file belongs to.

    Works on the shapes people actually name files: "Rockingham 4.pdf", "Rockingham-04.pdf",
    "rock4_final.pdf", "hills21.jpg". The rule is a number preceded by letters that start the
    county's name, so "mail_piece_2.pdf" matches nothing rather than landing in a district
    whose number happens to be 2. Returns None when two districts fit equally well, so an
    ambiguous file is handed back rather than filed in the wrong place."""
    stem = filename.rsplit('.', 1)[0].lower()
    # every (letters, number) pair in the name, e.g. "rockingham_14_v2" -> rockingham/14, v/2
    pairs = [(m.group(1), int(m.group(2)))
             for m in _re.finditer(r'([a-z]+)[^a-z0-9]*0*([0-9]+)', stem)]
    if not pairs:
        return None
    hits = []
    for code in codes:
        parts = code.rsplit(' ', 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        county, n = _re.sub(r'[^a-z]', '', parts[0].lower()), int(parts[1])
        for word, num in pairs:
            # at least three letters, and they must begin the county name
            if num == n and len(word) >= 3 and county.startswith(word):
                hits.append((len(word), code))
    if not hits:
        return None
    hits.sort(reverse=True)
    best = {c for ln, c in hits if ln == hits[0][0]}
    return hits[0][1] if len(best) == 1 else None


@private_bp.route('/spend-plan/piece/art', methods=['POST'])
@require_feature_access('campaign_plan')
def spend_piece_art():
    """Upload artwork. One file with no district becomes the piece's shared proof; files sent
    with district=<code>, or a batch matched by filename, become that district's own version.
    A file that matches nothing, or matches two districts, is handed back unfiled."""
    pid = request.form.get('id')
    if not pid:
        return jsonify({'ok': False, 'error': 'no piece'}), 400
    files = request.files.getlist('file')
    if not files or not files[0].filename:
        return jsonify({'ok': False, 'error': 'No file.'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('SELECT district_code FROM spend_piece_district WHERE piece_id = %s', (pid,))
        codes = [r[0] for r in cur.fetchall()]
        target = request.form.get('district')
        shared = request.form.get('shared') == '1'
        placed, unmatched = [], []

        for f in files:
            url, err = _store_art(f)
            if err:
                unmatched.append({'name': f.filename, 'why': err})
                continue
            if shared:
                cur.execute("""UPDATE spend_piece
                                  SET default_file_url = %s, default_content_type = %s,
                                      updated_at = now()
                                WHERE id = %s""", (url, f.content_type, pid))
                placed.append({'name': f.filename, 'code': None, 'url': url})
                continue
            code = target or _match_district(f.filename, codes)
            if not code:
                unmatched.append({'name': f.filename, 'url': url,
                                  'why': 'no district in this piece matches that filename'})
                continue
            cur.execute("""UPDATE spend_piece_district
                              SET file_url = %s, content_type = %s
                            WHERE piece_id = %s AND district_code = %s""",
                        (url, f.content_type, pid, code))
            if cur.rowcount:
                placed.append({'name': f.filename, 'code': code, 'url': url})
            else:
                unmatched.append({'name': f.filename, 'url': url,
                                  'why': code + ' is not on this piece'})
        conn.commit()
        return jsonify({'ok': True, 'placed': placed, 'unmatched': unmatched,
                        'codes': codes})
    except Exception as e:
        conn.rollback()
        logger.error('piece art failed: %s', e)
        return jsonify({'ok': False, 'error': 'Could not save.'}), 500
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/spend-plan/piece/place', methods=['POST'])
@require_feature_access('campaign_plan')
def spend_piece_place():
    """File an already-uploaded artwork file into the district the user picked for it.

    Only ever accepts a URL this app wrote: the file is in storage under spend_piece/, so
    there is nothing to re-upload, but nothing else can be pointed at either."""
    d = request.get_json(silent=True) or {}
    url, code, pid = (d.get('url') or ''), (d.get('district') or ''), d.get('id')
    if not (pid and code and url.startswith('https://') and '/spend_piece/' in url):
        return jsonify({'ok': False, 'error': 'bad request'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""UPDATE spend_piece_district SET file_url = %s
                        WHERE piece_id = %s AND district_code = %s""", (url, pid, code))
        if not cur.rowcount:
            conn.rollback()
            return jsonify({'ok': False, 'error': code + ' is not on this piece'}), 400
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        conn.rollback()
        logger.error('piece place failed: %s', e)
        return jsonify({'ok': False, 'error': 'Could not save.'}), 500
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/spend-plan/piece/delete', methods=['POST'])
@require_feature_access('campaign_plan')
def spend_piece_delete():
    """Delete a piece. Its districts go with it, and both are kept in the history tables."""
    d = request.get_json(silent=True) or {}
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE spend_piece SET updated_by = %s WHERE id = %s",
                    (current_user.email if current_user.is_authenticated else 'admin', d.get('id')))
        cur.execute('DELETE FROM spend_piece WHERE id = %s', (d.get('id'),))
        conn.commit()
        return jsonify({'ok': True, 'deleted': cur.rowcount})
    except Exception as e:
        conn.rollback()
        logger.error('piece delete failed: %s', e)
        return jsonify({'ok': False, 'error': 'Could not delete.'}), 500
    finally:
        cur.close(); release_db_connection(conn)


# =============================================================================
# SCENARIOS - named versions of the whole plan
# =============================================================================

def _plan_totals(cur):
    """What the live plan costs, and how many districts and seats it covers."""
    cur.execute("""SELECT tactic_key, unit, rate FROM spend_tactic WHERE active""")
    tactics = [{'key': r[0], 'unit': r[1], 'rate': float(r[2]) if r[2] is not None else None}
               for r in cur.fetchall()]
    sizes = _piece_sizes(cur)
    cur.execute("SELECT district_code, universe, tactic_key, qty, rate_override FROM district_spend_item")
    qty = {}
    for dc, u, tk, q, ro in cur.fetchall():
        qty.setdefault(dc, {}).setdefault(u, {})[tk] = {
            'qty': float(q), 'rate': float(ro) if ro is not None else None}
    total = 0.0
    for dc, byu in qty.items():
        total += _cost_of(byu, sizes.get(dc, {}), tactics)
    cur.execute("""SELECT count(*), COALESCE(SUM((SELECT MAX(d.seat_count) FROM districts d
                                   WHERE d.full_district_code = s.district_code)), 0)
                     FROM district_spend s WHERE s.tier IS NOT NULL""")
    n, seats = cur.fetchone()
    return round(total, 2), n, int(seats or 0)


def _take_scenario(cur, name, note, who, auto=False):
    """Copy the live plan into a new scenario. Returns its id."""
    total, n, seats = _plan_totals(cur)
    cur.execute("""INSERT INTO spend_scenario (name, note, total, districts, seats, auto, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (name[:120], note or None, total, n, seats, auto, who))
    sid = cur.fetchone()[0]
    cur.execute("""INSERT INTO spend_scenario_district
                     (scenario_id, district_code, mask, include, notes, tier)
                   SELECT %s, district_code, mask, include, notes, tier FROM district_spend""", (sid,))
    cur.execute("""INSERT INTO spend_scenario_item
                     (scenario_id, district_code, universe, tactic_key, qty, rate_override)
                   SELECT %s, district_code, universe, tactic_key, qty, rate_override
                     FROM district_spend_item""", (sid,))
    return sid


@private_bp.route('/spend-plan/versions')
@require_feature_access('campaign_plan')
def spend_versions():
    """Saved versions of the plan, and what changed between one of them and the plan now."""
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT id, name, note, total, districts, seats, auto, created_by, created_at,
                              (SELECT count(*) FROM spend_scenario_district d WHERE d.scenario_id = s.id)
                         FROM spend_scenario s ORDER BY created_at DESC""")
        versions = [{'id': r[0], 'name': r[1], 'note': r[2] or '',
                     'total': float(r[3]) if r[3] is not None else 0.0,
                     'districts': r[4] or 0, 'seats': r[5] or 0, 'auto': r[6],
                     'by': r[7] or '', 'at': r[8].strftime('%b %-d, %-I:%M %p'),
                     'rows': r[9]} for r in cur.fetchall()]

        total, n, seats = _plan_totals(cur)
        live = {'total': total, 'districts': n, 'seats': seats}

        # What is different between the version being looked at and the plan right now
        cmp_id = request.args.get('compare', type=int)
        diff = None
        if cmp_id:
            cur.execute("""SELECT district_code, tier, include FROM spend_scenario_district
                            WHERE scenario_id = %s""", (cmp_id,))
            was = {r[0]: {'tier': r[1], 'include': r[2]} for r in cur.fetchall()}
            cur.execute("SELECT district_code, tier, include FROM district_spend")
            now = {r[0]: {'tier': r[1], 'include': r[2]} for r in cur.fetchall()}

            cur.execute("""SELECT district_code, universe, tactic_key, qty
                             FROM spend_scenario_item WHERE scenario_id = %s""", (cmp_id,))
            was_q = {(r[0], r[1], r[2]): float(r[3]) for r in cur.fetchall()}
            cur.execute("SELECT district_code, universe, tactic_key, qty FROM district_spend_item")
            now_q = {(r[0], r[1], r[2]): float(r[3]) for r in cur.fetchall()}

            touched = {}
            for code in set(was) | set(now):
                w, nw = was.get(code), now.get(code)
                if not w or not nw or w['tier'] != nw['tier'] or bool(w['include']) != bool(nw['include']):
                    touched[code] = {'was_tier': w['tier'] if w else None,
                                     'now_tier': nw['tier'] if nw else None,
                                     'was_in': bool(w['include']) if w else False,
                                     'now_in': bool(nw['include']) if nw else False,
                                     'qty': []}
            for key in set(was_q) | set(now_q):
                a, b = was_q.get(key, 0.0), now_q.get(key, 0.0)
                if a == b:
                    continue
                code = key[0]
                touched.setdefault(code, {'was_tier': (was.get(code) or {}).get('tier'),
                                          'now_tier': (now.get(code) or {}).get('tier'),
                                          'was_in': bool((was.get(code) or {}).get('include')),
                                          'now_in': bool((now.get(code) or {}).get('include')),
                                          'qty': []})['qty'].append(
                    {'universe': key[1], 'tactic': key[2],
                     'was': _qty_word(a), 'now': _qty_word(b)})

            rows = []
            for code in sorted(touched, key=_district_sort_key):
                t = touched[code]
                t['qty'].sort(key=lambda x: (x['universe'], x['tactic']))
                t['code'] = code
                rows.append(t)
            cur.execute("SELECT name, total, created_at FROM spend_scenario WHERE id = %s", (cmp_id,))
            meta = cur.fetchone()
            diff = {'id': cmp_id, 'name': meta[0] if meta else '',
                    'total': float(meta[1]) if meta and meta[1] is not None else 0.0,
                    'at': meta[2].strftime('%b %-d, %-I:%M %p') if meta else '',
                    'rows': rows}
        return render_template('private/spend_versions.html',
                               versions=versions, live=live, diff=diff)
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/spend-plan/version/save', methods=['POST'])
@require_feature_access('campaign_plan')
def spend_version_save():
    """Freeze the plan as it stands under a name."""
    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Give the version a name.'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        sid = _take_scenario(cur, name, d.get('note'),
                             current_user.email if current_user.is_authenticated else 'admin')
        conn.commit()
        return jsonify({'ok': True, 'id': sid})
    except Exception as e:
        conn.rollback()
        logger.error('version save failed: %s', e)
        return jsonify({'ok': False, 'error': 'Could not save.'}), 500
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/spend-plan/version/restore', methods=['POST'])
@require_feature_access('campaign_plan')
def spend_version_restore():
    """Put a saved version back as the live plan.

    The plan as it stands is copied to a scenario of its own first, so this is reversible even
    if it was a mistake. Both the delete and the insert run through the history triggers, so
    the previous values are recoverable a second way too."""
    d = request.get_json(silent=True) or {}
    sid = d.get('id')
    if not sid:
        return jsonify({'ok': False, 'error': 'no version'}), 400
    who = current_user.email if current_user.is_authenticated else 'admin'
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('SELECT name FROM spend_scenario WHERE id = %s', (sid,))
        row = cur.fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'no such version'}), 404
        backup = _take_scenario(cur, 'Before restoring "%s"' % row[0][:80], None, who, auto=True)

        cur.execute('UPDATE district_spend SET updated_by = %s', (who,))
        cur.execute('DELETE FROM district_spend_item')
        cur.execute('DELETE FROM district_spend')
        cur.execute("""INSERT INTO district_spend (district_code, mask, include, notes, tier,
                                                   updated_by, updated_at)
                       SELECT district_code, mask, include, notes, tier, %s, now()
                         FROM spend_scenario_district WHERE scenario_id = %s""", (who, sid))
        cur.execute("""INSERT INTO district_spend_item
                         (district_code, universe, tactic_key, qty, rate_override)
                       SELECT district_code, universe, tactic_key, qty, rate_override
                         FROM spend_scenario_item WHERE scenario_id = %s""", (sid,))
        conn.commit()
        return jsonify({'ok': True, 'backup': backup})
    except Exception as e:
        conn.rollback()
        logger.error('version restore failed: %s', e)
        return jsonify({'ok': False, 'error': 'Could not restore. Nothing was changed.'}), 500
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/spend-plan/version/delete', methods=['POST'])
@require_feature_access('campaign_plan')
def spend_version_delete():
    d = request.get_json(silent=True) or {}
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('DELETE FROM spend_scenario WHERE id = %s', (d.get('id'),))
        conn.commit()
        return jsonify({'ok': True, 'deleted': cur.rowcount})
    except Exception as e:
        conn.rollback()
        logger.error('version delete failed: %s', e)
        return jsonify({'ok': False, 'error': 'Could not delete.'}), 500
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/spend-plan/save', methods=['POST'])
@require_feature_access('campaign_plan')
def spend_plan_save():
    """Inline save of one district: its universe mask, include flag, notes and quantities."""
    data = request.get_json(silent=True) or {}
    code = (data.get('district_code') or '').strip()
    if not code:
        return jsonify({'ok': False, 'error': 'district_code required'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        if any(k in data for k in ('mask', 'include', 'notes', 'tier')):
            # A field is only written when the caller actually sent it. The previous version
            # coerced a missing `include` to false in the VALUES clause, so COALESCE could
            # never see NULL and every tier-only save silently switched the district OFF.
            # Same trap for mask. Pass a "was it sent" flag per field and branch on it.
            cur.execute("""
                INSERT INTO district_spend (district_code, mask, include, notes, tier, updated_by, updated_at)
                VALUES (%s, COALESCE(%s,3), COALESCE(%s,false), %s, %s, %s, now())
                ON CONFLICT (district_code) DO UPDATE SET
                    mask    = CASE WHEN %s THEN EXCLUDED.mask    ELSE district_spend.mask    END,
                    include = CASE WHEN %s THEN EXCLUDED.include ELSE district_spend.include END,
                    notes   = CASE WHEN %s THEN EXCLUDED.notes   ELSE district_spend.notes   END,
                    tier    = CASE WHEN %s THEN EXCLUDED.tier    ELSE district_spend.tier    END,
                    updated_by = EXCLUDED.updated_by, updated_at = now()
            """, (code, data.get('mask'), data.get('include'), data.get('notes'),
                  (data.get('tier') or None),
                  (current_user.email if current_user.is_authenticated else 'admin'),
                  'mask' in data, 'include' in data, 'notes' in data, 'tier' in data))
        universe = (data.get('universe') or 'base').strip()[:20]
        for tk, qty in (data.get('items') or {}).items():
            try:
                q = float(qty)
            except (TypeError, ValueError):
                continue
            if q <= 0:
                cur.execute("DELETE FROM district_spend_item WHERE district_code=%s "
                            "AND universe=%s AND tactic_key=%s", (code, universe, tk))
            else:
                cur.execute("""INSERT INTO district_spend_item
                                   (district_code, universe, tactic_key, qty)
                               VALUES (%s,%s,%s,%s)
                               ON CONFLICT (district_code, universe, tactic_key)
                               DO UPDATE SET qty = EXCLUDED.qty""", (code, universe, tk, q))
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        conn.rollback()
        logger.error(f'spend plan save failed for {code}: {e}')
        return jsonify({'ok': False, 'error': 'Save failed.'}), 500
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/spend-plan/rate', methods=['POST'])
@require_feature_access('campaign_plan')
def spend_plan_rate():
    """Set a tactic's unit cost. The field tactics ship without a rate because nobody has
    quoted them yet; this is how they get one."""
    data = request.get_json(silent=True) or {}
    key = (data.get('tactic_key') or '').strip()
    raw = data.get('rate')
    conn = get_db_connection(); cur = conn.cursor()
    try:
        rate = None if raw in (None, '') else float(raw)
        cur.execute("UPDATE spend_tactic SET rate=%s WHERE tactic_key=%s", (rate, key))
        if cur.rowcount == 0:
            return jsonify({'ok': False, 'error': 'Unknown tactic.'}), 404
        conn.commit()
        return jsonify({'ok': True, 'rate': rate})
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Rate must be a number.'}), 400
    finally:
        cur.close(); release_db_connection(conn)


@private_bp.route('/campaign-plan')
@require_feature_access('campaign_plan')
def campaign_plan():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT d.full_district_code, d.county_name, d.seats, d.pvi, d.pvi_rating, d.towns,
                   p.bucket, p.channels, p.priority, p.notes, p.spend
            FROM (
                SELECT full_district_code,
                       MAX(county_name) AS county_name,
                       MAX(seat_count)  AS seats,
                       MAX(pvi)         AS pvi,
                       MAX(pvi_rating)  AS pvi_rating,
                       STRING_AGG(DISTINCT CASE WHEN ward IS NOT NULL AND ward <> 0
                                   THEN town || ' Ward ' || ward ELSE town END, ', ') AS towns
                FROM districts GROUP BY full_district_code
            ) d
            LEFT JOIN district_plan p ON p.district_code = d.full_district_code
        """)
        rows = cur.fetchall()

        # 2026 State Rep filers per district by party (counts + names for the matchup)
        cur.execute("""
            SELECT district_code, party, COUNT(*),
                   STRING_AGG(first_name || ' ' || last_name, ', ' ORDER BY last_name) AS names
            FROM filings
            WHERE election_year = 2026 AND office = 'State Representative'
            GROUP BY district_code, party
        """)
        filers = {}
        for dc, party, n, names in cur.fetchall():
            filers.setdefault(dc, {})
            filers[dc][party] = {'n': n, 'names': names or ''}

        districts = []
        for code, county, seats, pvi, rating, towns, bucket, channels, priority, notes, spend in rows:
            f = filers.get(code, {})
            r, d, ind = f.get('R', {}), f.get('D', {}), f.get('I', {})
            districts.append({
                'code': code, 'county': county or '', 'seats': seats or 1,
                'pvi': float(pvi) if pvi is not None else None, 'rating': rating or '',
                'towns': towns or '',
                'bucket': bucket or 'unassigned', 'channels': channels or [],
                'priority': priority, 'notes': notes or '', 'spend': spend or '',
                'r_filers': r.get('n', 0), 'd_filers': d.get('n', 0),
                'r_names': r.get('names', ''), 'd_names': d.get('names', ''), 'i_names': ind.get('names', ''),
            })
        districts.sort(key=lambda r: _dist_sortkey(r['code']))

        # summary: districts + seats per bucket, district count per channel, no-R count
        summary = {b: {'districts': 0, 'seats': 0} for b in PLAN_BUCKET_KEYS}
        chan_counts = {c: 0 for c in PLAN_CHANNELS}
        no_r = 0
        for d in districts:
            b = d['bucket'] if d['bucket'] in summary else 'unassigned'
            summary[b]['districts'] += 1
            summary[b]['seats'] += d['seats']
            for c in d['channels']:
                if c in chan_counts:
                    chan_counts[c] += 1
            if d['r_filers'] == 0:
                no_r += 1

        # one dashcard per sub-bucket, ordered by likelihood of an R win
        DASH_ORDER = ['safe_r', 'lean_r', 'watch', 'lean_d', 'safe_d', 'unassigned']
        BUCKET_COLOR = {b[0]: b[2] for b in PLAN_BUCKETS}
        dashcards = [{'key': k, 'label': PLAN_BUCKET_LABEL[k], 'color': BUCKET_COLOR[k],
                      'main': PLAN_MAIN.get(k, ''),
                      'districts': summary[k]['districts'], 'seats': summary[k]['seats']}
                     for k in DASH_ORDER if not (k == 'unassigned' and summary[k]['districts'] == 0)]

        # persisted Project-240 seat projections per bucket (seeded from 2024 actuals)
        cur.execute("SELECT bucket, seats_won FROM plan_projection")
        projection = {b: (s if s is not None else 0) for b, s in cur.fetchall()}
        # 2024 actual R seats won, aggregated by each district's CURRENT bucket
        cur.execute("""SELECT p.bucket, COALESCE(SUM(t.r_seats), 0)
                       FROM district_plan p LEFT JOIN district_2024 t ON t.district_code = p.district_code
                       GROUP BY p.bucket""")
        twentyfour = {b: int(s) for b, s in cur.fetchall()}
        twentyfour_total = sum(twentyfour.values())

        counties = sorted({d['county'] for d in districts if d['county']})
        return render_template('private/campaign_plan.html',
                               districts=districts, buckets=PLAN_BUCKETS, plan_groups=PLAN_GROUPS,
                               bucket_label=PLAN_BUCKET_LABEL, channels=PLAN_CHANNELS,
                               summary=summary, dashcards=dashcards, projection=projection,
                               twentyfour=twentyfour, twentyfour_total=twentyfour_total,
                               chan_counts=chan_counts, no_r=no_r,
                               counties=counties, total=len(districts))
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/campaign-plan/save', methods=['POST'])
@require_feature_access('campaign_plan')
def campaign_plan_save():
    data = request.get_json(silent=True) or {}
    code = data.get('district_code')
    field = data.get('field')
    value = data.get('value')
    if not code or field not in ('bucket', 'channels', 'priority', 'notes', 'spend'):
        return jsonify(ok=False, error='bad request'), 400
    if field == 'bucket' and value not in PLAN_BUCKET_KEYS:
        return jsonify(ok=False, error='bad bucket'), 400
    if field == 'channels':
        value = [c for c in (value or []) if c in PLAN_CHANNELS]
    if field == 'priority':
        value = int(value) if value in (1, 2, 3, '1', '2', '3') else None
    if field == 'spend':
        value = value if value in ('spend', 'no') else None

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # `field` is whitelisted above, so it's safe to inline as a column name.
        sql = ("INSERT INTO district_plan (district_code, " + field + ", updated_by, updated_at) "
               "VALUES (%s, %s, %s, NOW()) "
               "ON CONFLICT (district_code) DO UPDATE SET " + field + " = EXCLUDED." + field + ", "
               "updated_by = EXCLUDED.updated_by, updated_at = NOW()")
        cur.execute(sql, (code, value, getattr(current_user, 'email', 'admin')))
        conn.commit()
        return jsonify(ok=True)
    except Exception as e:
        conn.rollback()
        return jsonify(ok=False, error=str(e)), 500
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/campaign-plan/projection', methods=['POST'])
@require_feature_access('campaign_plan')
def campaign_plan_projection():
    data = request.get_json(silent=True) or {}
    bucket = data.get('bucket')
    if bucket not in PLAN_BUCKET_KEYS:
        return jsonify(ok=False, error='bad bucket'), 400
    try:
        seats = max(0, int(data.get('seats')))
    except (TypeError, ValueError):
        return jsonify(ok=False, error='bad seats'), 400
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO plan_projection (bucket, seats_won, updated_by, updated_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (bucket) DO UPDATE SET seats_won = EXCLUDED.seats_won,
                           updated_by = EXCLUDED.updated_by, updated_at = NOW()""",
                    (bucket, seats, getattr(current_user, 'email', 'admin')))
        conn.commit()
        return jsonify(ok=True)
    except Exception as e:
        conn.rollback()
        return jsonify(ok=False, error=str(e)), 500
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/campaign-plan/export.csv')
@require_feature_access('campaign_plan')
def campaign_plan_export():
    import csv, io
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT d.full_district_code, MAX(d.county_name), MAX(d.seat_count),
                   MAX(d.pvi), MAX(d.pvi_rating),
                   p.bucket, p.channels, p.priority, p.notes
            FROM districts d
            LEFT JOIN district_plan p ON p.district_code = d.full_district_code
            GROUP BY d.full_district_code, p.bucket, p.channels, p.priority, p.notes
        """)
        rows = cur.fetchall()
    finally:
        cur.close()
        release_db_connection(conn)
    rows.sort(key=lambda r: _dist_sortkey(r[0]))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['District', 'County', 'Seats', 'PVI', 'PVI Rating', 'Bucket', 'Channels', 'Priority', 'Notes'])
    for code, county, seats, pvi, rating, bucket, channels, priority, notes in rows:
        w.writerow([code, county, seats, pvi, rating,
                    PLAN_BUCKET_LABEL.get(bucket or 'unassigned', bucket),
                    ', '.join(channels or []), priority or '', (notes or '').replace('\n', ' ')])
    from flask import Response
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=battle_plan.csv'})
