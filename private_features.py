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
    """List all secret primary campaigns."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT c.*,
                   COUNT(t.id) as target_count,
                   COUNT(CASE WHEN t.challenger_status = 'confirmed' THEN 1 END) as confirmed_count
            FROM secret_primary_campaigns c
            LEFT JOIN secret_primary_targets t ON c.id = t.campaign_id
            WHERE c.is_active = TRUE
            GROUP BY c.id
            ORDER BY c.created_at DESC
        """)
        campaigns = cur.fetchall()

        return render_template('private/primaries_list.html', campaigns=campaigns)
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

        # Get targets
        cur.execute("""
            SELECT * FROM secret_primary_targets
            WHERE campaign_id = %s
            ORDER BY priority, district_code
        """, (campaign_id,))
        targets = cur.fetchall()

        return render_template('private/primaries_view.html', campaign=campaign, targets=targets)
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/primaries/<int:campaign_id>/add-target', methods=['POST'])
@require_feature_access('secret_primaries')
def add_target(campaign_id):
    """Add a target to a campaign."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO secret_primary_targets
            (campaign_id, district_code, incumbent_name, incumbent_party, challenger_name,
             challenger_status, challenger_contact, notes, priority, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            campaign_id,
            request.form.get('district_code'),
            request.form.get('incumbent_name'),
            request.form.get('incumbent_party'),
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

    return redirect(url_for('private.view_campaign', campaign_id=campaign_id))


@private_bp.route('/primaries/target/<int:target_id>/update', methods=['POST'])
@require_feature_access('secret_primaries')
def update_target(target_id):
    """Update a target's status."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE secret_primary_targets
            SET challenger_name = %s, challenger_status = %s, challenger_contact = %s,
                notes = %s, priority = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING campaign_id
        """, (
            request.form.get('challenger_name'),
            request.form.get('challenger_status'),
            request.form.get('challenger_contact'),
            request.form.get('notes'),
            request.form.get('priority', 5),
            target_id
        ))
        result = cur.fetchone()
        conn.commit()
        flash('Target updated.', 'success')
        return redirect(url_for('private.view_campaign', campaign_id=result[0]))
    except Exception as e:
        conn.rollback()
        flash(f'Error updating target: {e}', 'error')
        return redirect(url_for('private.secret_primaries'))
    finally:
        cur.close()
        release_db_connection(conn)


# =============================================================================
# SPEAKER VOTE TRACKING
# =============================================================================

@private_bp.route('/speaker')
@require_feature_access('speaker_votes')
def speaker_votes():
    """Speaker vote tracking dashboard."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get all tracked legislators with vote counts
        cur.execute("""
            SELECT * FROM speaker_vote_tracking
            ORDER BY
                CASE commitment_status
                    WHEN 'committed' THEN 1
                    WHEN 'leaning_yes' THEN 2
                    WHEN 'unknown' THEN 3
                    WHEN 'leaning_no' THEN 4
                    WHEN 'opposed' THEN 5
                END,
                legislator_name
        """)
        legislators = cur.fetchall()

        # Get summary counts
        cur.execute("""
            SELECT commitment_status, COUNT(*)
            FROM speaker_vote_tracking
            GROUP BY commitment_status
        """)
        status_counts = dict(cur.fetchall())

        return render_template('private/speaker_dashboard.html',
                             legislators=legislators,
                             status_counts=status_counts)
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/speaker/add', methods=['POST'])
@require_feature_access('speaker_votes')
def add_speaker_vote():
    """Add or update a legislator's speaker vote status."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        employee_number = request.form.get('employee_number')

        cur.execute("""
            INSERT INTO speaker_vote_tracking
            (legislator_name, district_code, employee_number, commitment_status,
             contacted_by, notes, confidence_level, created_by, contacted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (employee_number) DO UPDATE SET
                commitment_status = EXCLUDED.commitment_status,
                notes = EXCLUDED.notes,
                confidence_level = EXCLUDED.confidence_level,
                last_contact_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
        """, (
            request.form.get('legislator_name'),
            request.form.get('district_code'),
            employee_number,
            request.form.get('commitment_status', 'unknown'),
            request.form.get('contacted_by', current_user.email),
            request.form.get('notes'),
            request.form.get('confidence_level', 5),
            current_user.email
        ))
        conn.commit()
        flash('Vote tracking updated.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error updating: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.speaker_votes'))


@private_bp.route('/speaker/<int:tracking_id>')
@require_feature_access('speaker_votes')
def speaker_vote_detail(tracking_id):
    """View details and contact history for a legislator."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get legislator
        cur.execute("SELECT * FROM speaker_vote_tracking WHERE id = %s", (tracking_id,))
        legislator = cur.fetchone()
        if not legislator:
            flash('Record not found.', 'error')
            return redirect(url_for('private.speaker_votes'))

        # Get contact history
        cur.execute("""
            SELECT * FROM speaker_vote_contacts
            WHERE tracking_id = %s
            ORDER BY contact_date DESC
        """, (tracking_id,))
        contacts = cur.fetchall()

        return render_template('private/speaker_detail.html',
                             legislator=legislator,
                             contacts=contacts)
    finally:
        cur.close()
        release_db_connection(conn)


@private_bp.route('/speaker/<int:tracking_id>/contact', methods=['POST'])
@require_feature_access('speaker_votes')
def log_speaker_contact(tracking_id):
    """Log a contact with a legislator about speaker vote."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get current status
        cur.execute("SELECT commitment_status FROM speaker_vote_tracking WHERE id = %s", (tracking_id,))
        current = cur.fetchone()
        status_before = current[0] if current else 'unknown'
        status_after = request.form.get('new_status', status_before)

        # Log the contact
        cur.execute("""
            INSERT INTO speaker_vote_contacts
            (tracking_id, contacted_by, contact_method, outcome, notes, status_before, status_after)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            tracking_id,
            request.form.get('contacted_by', current_user.email),
            request.form.get('contact_method'),
            request.form.get('outcome'),
            request.form.get('notes'),
            status_before,
            status_after
        ))

        # Update the main record
        cur.execute("""
            UPDATE speaker_vote_tracking
            SET commitment_status = %s,
                last_contact_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP,
                confidence_level = %s
            WHERE id = %s
        """, (status_after, request.form.get('confidence_level', 5), tracking_id))

        conn.commit()
        flash('Contact logged.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error logging contact: {e}', 'error')
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('private.speaker_vote_detail', tracking_id=tracking_id))


@private_bp.route('/speaker/import', methods=['GET', 'POST'])
@require_feature_access('speaker_votes')
def import_legislators():
    """Import House Republicans for speaker vote tracking."""
    if request.method == 'POST':
        # This would typically call the GC API to get all House Republicans
        # For now, we'll handle it manually or via a separate script
        flash('Import functionality coming soon.', 'info')
        return redirect(url_for('private.speaker_votes'))

    return render_template('private/speaker_import.html')
