import os
import re
import json
import csv
import secrets
from collections import defaultdict
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from psycopg2 import pool
import psycopg2
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
import logging
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import pyotp
import qrcode
import io
import base64

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Main database connection pool
db_pool = None

def get_db_pool():
    global db_pool
    if db_pool is None:
        DATABASE_URL = os.environ.get("DATABASE_URL")
        if DATABASE_URL:
            db_pool = psycopg2.pool.SimpleConnectionPool(1, 20, DATABASE_URL)
        else:
            db_pool = psycopg2.pool.SimpleConnectionPool(
                1, 20,
                host=os.environ.get("DB_HOST", "localhost"),
                port=os.environ.get("DB_PORT", "5432"),
                dbname=os.environ.get("DB_NAME", "nh_candidates"),
                user=os.environ.get("DB_USER", "postgres"),
                password=os.environ.get("DB_PASSWORD", ""),
                sslmode=os.environ.get("DB_SSLMODE", "require")
            )
    return db_pool

def get_db_connection():
    return get_db_pool().getconn()

def release_db_connection(conn):
    get_db_pool().putconn(conn)

# Voter database connection (external - for statewide checklist lookups)
VOTER_DATABASE_URL = os.environ.get("VOTER_DATABASE_URL")
voter_db_pool = None

def get_voter_db_pool():
    global voter_db_pool
    if voter_db_pool is None and VOTER_DATABASE_URL:
        try:
            voter_db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, VOTER_DATABASE_URL)
        except Exception as e:
            logger.error(f"Could not connect to voter database: {e}")
    return voter_db_pool

def get_voter_db_connection():
    pool = get_voter_db_pool()
    if pool:
        return pool.getconn()
    return None

def release_voter_db_connection(conn):
    pool = get_voter_db_pool()
    if pool and conn:
        pool.putconn(conn)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-to-a-secure-random-string")
app.permanent_session_lifetime = timedelta(hours=72)
app.config['SESSION_PERMANENT'] = True

# CSRF Protection
csrf = CSRFProtect(app)

# Rate Limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Flask-Login Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# AWS SES Configuration
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SES_SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "noreply@nhgop.org")
SES_SENDER_NAME = os.environ.get("SES_SENDER_NAME", "NH GOP Candidate Recruitment")
APP_URL = os.environ.get("APP_URL", "https://recruit.nhgop.org")

# Super Admin - full access to admin dashboard
SUPER_ADMIN_EMAIL = "chris@maidmentnh.com"

# Token serializer for secure links
token_serializer = URLSafeTimedSerializer(app.secret_key)

def get_ses_client():
    """Get AWS SES client."""
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        return None
    return boto3.client(
        "ses",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )

def send_email(to_email, subject, html_body, text_body=None):
    """Send an email via AWS SES."""
    ses = get_ses_client()
    if not ses:
        logger.error("SES not configured - missing AWS credentials")
        return False

    if text_body is None:
        text_body = "Please view this email in an HTML-compatible email client."

    try:
        ses.send_email(
            Source=f'"{SES_SENDER_NAME}" <{SES_SENDER_EMAIL}>',
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"}
                }
            }
        )
        logger.info(f"Email sent to {to_email}: {subject}")
        return True
    except ClientError as e:
        logger.error(f"SES error sending to {to_email}: {e}")
        return False

def generate_invite_token(user_type, user_id):
    """Generate a secure invite token for account setup."""
    return token_serializer.dumps({'type': user_type, 'id': user_id}, salt='invite-token')

def verify_invite_token(token, max_age=86400 * 7):
    """Verify an invite token (default 7 days expiry)."""
    try:
        data = token_serializer.loads(token, salt='invite-token', max_age=max_age)
        return data
    except SignatureExpired:
        return None
    except BadSignature:
        return None

def send_welcome_email(email, name, user_type, user_id):
    """Send welcome email with secure setup link."""
    token = generate_invite_token(user_type, user_id)
    setup_link = f"{APP_URL}/setup-account/{token}"

    subject = "Welcome to NH GOP Candidate Recruitment"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: #d91720; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 24px;">NH GOP Candidate Recruitment</h1>
        </div>

        <div style="background: #f8f9fa; padding: 30px; border: 1px solid #e9ecef; border-top: none; border-radius: 0 0 8px 8px;">
            <h2 style="color: #333; margin-top: 0;">Welcome, {name}!</h2>

            <p>You've been invited to join the NH GOP Candidate Recruitment system. Click the button below to set up your account and create your password.</p>

            <div style="text-align: center; margin: 30px 0;">
                <a href="{setup_link}" style="background: #d91720; color: white; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">
                    Set Up Your Account
                </a>
            </div>

            <p style="color: #666; font-size: 14px;">This link will expire in 7 days. If you didn't expect this email, you can safely ignore it.</p>

            <p style="color: #666; font-size: 14px;">If the button doesn't work, copy and paste this link into your browser:</p>
            <p style="color: #666; font-size: 12px; word-break: break-all;">{setup_link}</p>
        </div>

        <div style="text-align: center; padding: 20px; color: #999; font-size: 12px;">
            <p>NH GOP Candidate Recruitment System</p>
        </div>
    </body>
    </html>
    """

    text_body = f"""
Welcome to NH GOP Candidate Recruitment, {name}!

You've been invited to join the system. Please visit the following link to set up your account:

{setup_link}

This link will expire in 7 days.

If you didn't expect this email, you can safely ignore it.
    """

    return send_email(email, subject, html_body, text_body)

# S3-compatible storage (DigitalOcean Spaces)
S3_ENDPOINT = os.environ.get("S3_ENDPOINT")  # e.g., https://nyc3.digitaloceanspaces.com
S3_BUCKET = os.environ.get("S3_BUCKET", "candidate-uploads")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY")
S3_REGION = os.environ.get("S3_REGION", "nyc3")

def get_s3_client():
    """Get S3 client for DigitalOcean Spaces"""
    if not all([S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY]):
        return None
    return boto3.client(
        's3',
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version='s3v4'),
        region_name=S3_REGION
    )

def upload_file_to_storage(file_obj, destination_blob_name):
    """Upload file to DigitalOcean Spaces"""
    s3 = get_s3_client()
    if not s3:
        logger.warning("S3 storage not configured, skipping upload")
        return None
    
    try:
        s3.upload_fileobj(
            file_obj,
            S3_BUCKET,
            destination_blob_name,
            ExtraArgs={'ACL': 'public-read'}
        )
        return f"{S3_ENDPOINT}/{S3_BUCKET}/{destination_blob_name}"
    except Exception as e:
        logger.error(f"Error uploading to S3: {e}")
        return None

# User Models
class CandidateUser(UserMixin):
    def __init__(self, candidate_id, email, password_hash, first_name, last_name, password_changed, photo_url, 
                 address=None, city=None, zip=None, phone1=None, phone2=None, twitter_x=None, 
                 facebook=None, instagram=None, other=None, signal=None, email1=None, email2=None):
        self.id = f"c_{candidate_id}"
        self.candidate_id = candidate_id
        self.email = email
        self.password_hash = password_hash
        self.first_name = first_name
        self.last_name = last_name
        self.password_changed = password_changed
        self.photo_url = photo_url
        self.address = address
        self.city = city
        self.zip = zip
        self.phone1 = phone1
        self.phone2 = phone2
        self.twitter_x = twitter_x
        self.facebook = facebook
        self.instagram = instagram
        self.other = other
        self.signal = signal
        self.email1 = email1
        self.email2 = email2
        self.is_candidate = True

class AdminUser(UserMixin):
    def __init__(self, user_id, username, email, password_hash, role):
        self.id = f"u_{user_id}"
        self.user_id = user_id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.role = role
        self.is_candidate = False

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if user_id.startswith('c_'):
            candidate_id = int(user_id[2:])
            cur.execute("""
                SELECT candidate_id, email, password_hash, first_name, last_name, password_changed, photo_url,
                       address, city, zip, phone1, phone2, twitter_x, facebook, instagram, other, signal, email1, email2
                FROM candidates
                WHERE candidate_id = %s
            """, (candidate_id,))
            row = cur.fetchone()
            if row:
                return CandidateUser(*row)
        elif user_id.startswith('u_'):
            uid = int(user_id[2:])
            cur.execute("""
                SELECT user_id, username, email, password_hash, role
                FROM users
                WHERE user_id = %s
            """, (uid,))
            row = cur.fetchone()
            if row:
                return AdminUser(*row)
    finally:
        cur.close()
        release_db_connection(conn)
    return None

# Role-Based Access Control
def is_super_admin():
    """Check if current user is super admin."""
    if not current_user.is_authenticated:
        return False
    return current_user.email.lower() == SUPER_ADMIN_EMAIL.lower()

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in as admin.", "warning")
            return redirect(url_for('admin_login'))
        if not hasattr(current_user, 'role') or current_user.role != 'admin':
            flash("Admin access required.", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in.", "warning")
            return redirect(url_for('login'))
        if not is_super_admin():
            flash("Super admin access required.", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.context_processor
def inject_super_admin():
    """Make is_super_admin available in all templates."""
    return {'is_super_admin': is_super_admin()}

def candidate_restricted(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if hasattr(current_user, 'is_candidate') and current_user.is_candidate:
            allowed_routes = {'profile', 'logout', 'change_password'}
            if request.endpoint not in allowed_routes:
                flash("You are only authorized to access your profile.", "danger")
                return redirect(url_for('profile'))
        return f(*args, **kwargs)
    return decorated_function

# Helper Functions
def natural_district_sort_key(district_str):
    match = re.match(r"^(.*?)(\d+)$", district_str.strip())
    if match:
        alpha_part = match.group(1).strip()
        num_part = int(match.group(2))
        return (alpha_part, num_part)
    else:
        return (district_str, 0)

def override_county_for_cities(county_name, all_towns):
    towns_upper = [t.upper() for t in all_towns]
    for t in towns_upper:
        if t.startswith("CONCORD"): return "CONCORD"
        if t.startswith("MANCHESTER"): return "MANCHESTER"
        if t.startswith("NASHUA"): return "NASHUA"
    return county_name

def log_activity(action_type, description, candidate_id=None):
    """Log an activity to the activity_log table"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        user_email = current_user.email if current_user.is_authenticated else 'system'
        cur.execute("""
            INSERT INTO activity_log (action_type, description, candidate_id, user_email)
            VALUES (%s, %s, %s, %s)
        """, (action_type, description, candidate_id, user_email))
        conn.commit()
        cur.close()
        release_db_connection(conn)
    except Exception as e:
        logger.error(f"Error logging activity: {e}")

def get_data_and_dashboard():
    conn = get_db_connection()
    cur = conn.cursor()
    TOTAL_SEATS = 400
    TOTAL_DISTRICTS = 203

    try:
        cur.execute("""
            SELECT counter, county_name, district_code, ward, town, seat_count, full_district_code, pvi, pvi_rating
            FROM districts
            ORDER BY county_name, district_code, town;
        """)
        district_rows = cur.fetchall()

        cur.execute("""
            SELECT ces.district_code, ces.status, c.first_name, c.last_name, c.incumbent, c.candidate_id
            FROM candidate_election_status ces
            JOIN candidates c ON ces.candidate_id = c.candidate_id
            WHERE ces.election_year = 2026 AND c.party = 'R';
        """)
        cands_2026_rows = cur.fetchall()

        cur.execute("""
            SELECT ces.district_code, ces.status, c.first_name, c.last_name, c.incumbent, c.candidate_id
            FROM candidate_election_status ces
            JOIN candidates c ON ces.candidate_id = c.candidate_id
            WHERE ces.election_year = 2024 AND c.party = 'R';
        """)
        cands_2024_rows = cur.fetchall()
    except Exception as e:
        logger.error(e)
        flash("Error fetching data from database.", "danger")
        district_rows = []
        cands_2026_rows = []
        cands_2024_rows = []
    finally:
        cur.close()
        release_db_connection(conn)

    cand2026_by_dist = defaultdict(list)
    for dist_code, status, first, last, inc, cid in cands_2026_rows:
        cand2026_by_dist[dist_code].append({
            "name": f"{first} {last}",
            "status": status,
            "incumbent": inc,
            "candidate_id": cid
        })
    cand2024_by_dist = defaultdict(list)
    for dist_code, status, first, last, inc, cid in cands_2024_rows:
        cand2024_by_dist[dist_code].append({
            "name": f"{first} {last}",
            "status": status,
            "incumbent": inc,
            "candidate_id": cid
        })

    county_groups = defaultdict(dict)
    for row in district_rows:
        counter, county_name, district_code, ward, town, seat_count, full_district_code, pvi, pvi_rating = row
        if ward and ward != 0:
            display_town = f"{town} Ward {ward}"
        else:
            display_town = town
        if full_district_code not in county_groups[county_name]:
            county_groups[county_name][full_district_code] = {
                "district_code": district_code,
                "full_district_code": full_district_code,
                "seat_count": seat_count,
                "towns": [],
                "cand2026": [],
                "cand2024": [],
                "pvi": pvi,
                "pvi_rating": pvi_rating
            }
        county_groups[county_name][full_district_code]["towns"].append(display_town)
    
    for c_name, dist_dict in county_groups.items():
        for fdc, info in dist_dict.items():
            info["cand2026"] = cand2026_by_dist.get(fdc, [])
            info["cand2024"] = cand2024_by_dist.get(fdc, [])
    
    final_groups = defaultdict(dict)
    for old_county, dist_dict in county_groups.items():
        for fdc, info in dist_dict.items():
            real_county = override_county_for_cities(old_county, info["towns"])
            if fdc not in final_groups[real_county]:
                final_groups[real_county][fdc] = info
            else:
                final_groups[real_county][fdc]["towns"].extend(info["towns"])
    
    sorted_county_groups = {}
    for county, dist_dict in final_groups.items():
        sorted_items = sorted(dist_dict.items(), key=lambda x: natural_district_sort_key(x[0]))
        ordered = {}
        for (fdc, val) in sorted_items:
            ordered[fdc] = val
        sorted_county_groups[county] = ordered

    # Get count of 2026 candidates not matched to voter file
    conn2 = get_db_connection()
    cur2 = conn2.cursor()
    try:
        cur2.execute("""
            SELECT COUNT(DISTINCT c.candidate_id)
            FROM candidates c
            JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
            WHERE ces.election_year = 2026 
              AND c.voter_id IS NULL
              AND ces.status != 'Declined'
        """)
        unmatched_count = cur2.fetchone()[0]
    except:
        unmatched_count = 0
    finally:
        cur2.close()
        release_db_connection(conn2)

    dashboard = {
        "confirmed": {"total": 0, "districts": []},
        "empty_seats": {"total": 0, "districts": []},
        "empty_districts": {"total": 0, "districts": []},
        "potentials": {"total": 0, "districts": []},
        "incumbents_running": {"total": 0, "districts": []},
        "incumbents_not_running": {"total": 0, "districts": []},
        "incumbents_undecided": {"total": 0, "districts": []},
        "primaries": {"total": 0, "districts": []},
        "unmatched_voters": {"total": unmatched_count},
        "TOTAL_SEATS": TOTAL_SEATS,
        "TOTAL_DISTRICTS": TOTAL_DISTRICTS
    }

    # Build global set of all candidate IDs with 2026 records
    all_2026_candidate_ids = set()
    for county_name, dist_dict in sorted_county_groups.items():
        for fdc, info in dist_dict.items():
            for c in info["cand2026"]:
                all_2026_candidate_ids.add(c["candidate_id"])
                
    total_confirmed = 0
    for county_name, dist_dict in sorted_county_groups.items():
        for fdc, info in dist_dict.items():
            seat_count = info["seat_count"]
            c2026 = info["cand2026"]
            active_2026 = [c for c in c2026 if c["status"].upper() != "DECLINED"]
            confirmed_count = sum(1 for c in c2026 if c["status"].upper() == "CONFIRMED")
            total_confirmed += confirmed_count
            if confirmed_count > 0:
                dashboard["confirmed"]["districts"].append((county_name, fdc))
            empty_seats = max(0, seat_count - confirmed_count)
            dashboard["empty_seats"]["total"] += empty_seats
            if empty_seats > 0:
                dashboard["empty_seats"]["districts"].append((county_name, fdc))
            if len(active_2026) == 0:
                dashboard["empty_districts"]["total"] += 1
                dashboard["empty_districts"]["districts"].append((county_name, fdc))
            pot_count = sum(1 for c in c2026 if c["status"].upper() in ("CONSIDERING", "POTENTIAL"))
            if pot_count > 0:
                dashboard["potentials"]["total"] += pot_count
                dashboard["potentials"]["districts"].append((county_name, fdc))
            # Count individual incumbents running (confirmed only)
            for c in c2026:
                if c["incumbent"] and c["status"].upper() == "CONFIRMED":
                    dashboard["incumbents_running"]["total"] += 1
                    dashboard["incumbents_running"]["districts"].append((county_name, fdc))
            # Count individual incumbents who declined
            for c in c2026:
                if c["incumbent"] and c["status"].upper() == "DECLINED":
                    dashboard["incumbents_not_running"]["total"] += 1
                    dashboard["incumbents_not_running"]["districts"].append((county_name, fdc))
            # Count individual incumbents undecided (not confirmed, not declined in 2026)
            for c in c2026:
                if c["incumbent"] and c["status"].upper() not in ("CONFIRMED", "DECLINED"):
                    dashboard["incumbents_undecided"]["total"] += 1
                    dashboard["incumbents_undecided"]["districts"].append((county_name, fdc))
            # Also count 2024 incumbents who have NO 2026 record at all
            c2024 = info["cand2024"]
            for c in c2024:
                if c["incumbent"] and c["candidate_id"] not in all_2026_candidate_ids:
                    dashboard["incumbents_undecided"]["total"] += 1
                    dashboard["incumbents_undecided"]["districts"].append((county_name, fdc))
            
    dashboard["confirmed"]["total"] = total_confirmed

    county_stats = {}
    for county_name, dist_dict in sorted_county_groups.items():
        c_seats = 0
        c_2026 = 0
        c_2024 = 0
        c_2026_confirmed = 0
        for fdc, info in dist_dict.items():
            c_seats += info["seat_count"]
            c_2026 += len(info["cand2026"])
            c_2024 += len(info["cand2024"])
            c_2026_confirmed += sum(1 for c in info["cand2026"] if c["status"].upper() == "CONFIRMED")
        county_stats[county_name] = {
            "total_seats": c_seats,
            "c2026": c_2026,
            "c2024": c_2024,
            "c2026_confirmed": c_2026_confirmed
        }
    return sorted_county_groups, dashboard, county_stats


# ============== ROUTES ==============

@app.route('/')
@login_required
def index():
    search_query = request.args.get('search', '').strip()
    county_filter = request.args.get('county', '').strip()
    county_groups, dashboard, county_stats = get_data_and_dashboard()

    # Filter by county if specified
    if county_filter:
        filtered_groups = {k: v for k, v in county_groups.items() if k.upper() == county_filter.upper()}
        county_groups = filtered_groups

    if search_query:
        # Filter by search query
        filtered_groups = {}
        search_upper = search_query.upper()
        for county_name, dist_dict in county_groups.items():
            filtered_districts = {}
            for fdc, info in dist_dict.items():
                # Check if search matches district code
                if search_upper in fdc.upper():
                    filtered_districts[fdc] = info
                    continue
                # Check if search matches town
                if any(search_upper in town.upper() for town in info['towns']):
                    filtered_districts[fdc] = info
                    continue
                # Check if search matches any candidate name in 2026 or 2024
                match_2026 = any(search_upper in c['name'].upper() for c in info['cand2026'])
                match_2024 = any(search_upper in c['name'].upper() for c in info['cand2024'])
                if match_2026 or match_2024:
                    filtered_districts[fdc] = info
            if filtered_districts:
                filtered_groups[county_name] = filtered_districts
        county_groups = filtered_groups

    return render_template("index.html", county_groups=county_groups, dashboard=dashboard, county_stats=county_stats, max=max, search_query=search_query, county_filter=county_filter)

@app.route('/filter')
@candidate_restricted
def filter_view():
    category = request.args.get("category", "").strip()
    county_groups, dashboard, county_stats = get_data_and_dashboard()
    
    # Special handling for unmatched_voters
    if category == 'unmatched_voters':
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT c.candidate_id, c.first_name, c.last_name, c.city, ces.district_code, ces.status
                FROM candidates c
                JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                WHERE ces.election_year = 2026 
                  AND c.voter_id IS NULL
                  AND ces.status != 'Declined'
                ORDER BY c.last_name, c.first_name
            """)
            unmatched = cur.fetchall()
        finally:
            cur.close()
            release_db_connection(conn)
        
        return render_template("unmatched_voters.html",
                              category=category,
                              unmatched=unmatched,
                              dashboard=dashboard)
        
    # Special handling for incumbents_undecided
    if category == 'incumbents_undecided':
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Get 2026 incumbents with status not confirmed/declined
            cur.execute("""
                SELECT c.candidate_id, c.first_name, c.last_name, ces.district_code, ces.status, 2026 as year
                FROM candidates c
                JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                WHERE ces.election_year = 2026 
                  AND c.incumbent = true
                  AND c.party = 'R'
                  AND ces.status NOT IN ('Confirmed', 'Declined')
                ORDER BY c.last_name, c.first_name
            """)
            undecided_2026 = cur.fetchall()
            
            # Get 2024 incumbents with NO 2026 record at all
            cur.execute("""
                SELECT c.candidate_id, c.first_name, c.last_name, ces.district_code, 'No 2026 Record' as status, 2024 as year
                FROM candidates c
                JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                WHERE ces.election_year = 2024 
                  AND c.incumbent = true
                  AND c.party = 'R'
                  AND NOT EXISTS (
                      SELECT 1 FROM candidate_election_status ces2 
                      WHERE ces2.candidate_id = c.candidate_id AND ces2.election_year = 2026
                  )
                ORDER BY c.last_name, c.first_name
            """)
            undecided_no_2026 = cur.fetchall()
            
            undecided = list(undecided_2026) + list(undecided_no_2026)
        finally:
            cur.close()
            release_db_connection(conn)
        
        return render_template("incumbents_undecided.html",
                              category=category,
                              undecided=undecided,
                              dashboard=dashboard)
    
    if category not in dashboard:
        flash("Invalid category filter.", "warning")
        return redirect(url_for("index"))
    if not isinstance(dashboard[category], dict):
        flash("This category is not filterable.", "warning")
        return redirect(url_for("index"))
    filter_set = set(dashboard[category]["districts"])
    filtered_groups = defaultdict(dict)
    for c_name, dist_dict in county_groups.items():
        for fdc, info in dist_dict.items():
            if (c_name, fdc) in filter_set:
                filtered_groups[c_name][fdc] = info
    return render_template("filter.html",
                          category=category,
                          county_groups=filtered_groups,
                          dashboard=dashboard,
                          county_stats=county_stats)

@app.route('/add_candidate_inline', methods=['POST'])
@candidate_restricted
@admin_required
def add_candidate_inline():
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    party = request.form.get("party", "").upper().strip()
    district_code = request.form.get("district_code", "").strip()
    election_year = request.form.get("election_year", "2026")
    status = request.form.get("status", "CONSIDERING").strip()
    if not (first_name and last_name and party and district_code and election_year and status):
        flash("All fields are required to add a candidate.", "warning")
        return redirect(url_for("index"))
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT candidate_id FROM candidates
            WHERE UPPER(first_name)=UPPER(%s) AND UPPER(last_name)=UPPER(%s);
        """, (first_name, last_name))
        existing = cur.fetchone()
        if existing:
            flash("Candidate already exists.", "warning")
            return redirect(url_for("index"))
        cur.execute("""
            INSERT INTO candidates (first_name, last_name, party, created_by)
            VALUES (%s, %s, %s, %s)
            RETURNING candidate_id;
        """, (first_name, last_name, party, current_user.email))
        candidate_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO candidate_election_status (candidate_id, election_year, status, is_running, added_by, district_code)
            VALUES (%s, %s, %s, %s, %s, %s);
        """, (candidate_id, int(election_year), status, True, current_user.email, district_code))
        conn.commit()
        log_activity('candidate_added', f"Added {first_name} {last_name} to {district_code} ({status})", candidate_id)
        flash(f"Added {first_name} {last_name} for {election_year} in {district_code}.", "success")
    except Exception as e:
        conn.rollback()
        logger.error(e)
        flash("Error adding candidate.", "danger")
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for("index"))

@app.route('/edit_candidate/<int:candidate_id>/<int:election_year>', methods=['GET', 'POST'])
@candidate_restricted
@admin_required
def edit_candidate(candidate_id, election_year):
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        first_name = request.form.get("first_name").strip()
        last_name = request.form.get("last_name").strip()
        party = request.form.get("party").upper().strip()
        status = request.form.get("status").strip()
        is_incumbent = request.form.get("incumbent") == "on"
        is_running = (status.upper() != "DECLINED")
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        zip_code = request.form.get("zip", "").strip()
        try:
            cur.execute("""
                UPDATE candidates
                SET first_name=%s, last_name=%s, party=%s, incumbent=%s, address=%s, city=%s, zip=%s
                WHERE candidate_id=%s;
            """, (first_name, last_name, party, is_incumbent, address, city, zip_code, candidate_id))
            cur.execute("""
                UPDATE candidate_election_status
                SET status=%s, is_running=%s
                WHERE candidate_id=%s AND election_year=%s;
            """, (status, is_running, candidate_id, election_year))
            conn.commit()
            log_activity('candidate_updated', f"Updated {first_name} {last_name}", candidate_id)
            flash("Candidate updated successfully.", "success")
        except Exception as e:
            conn.rollback()
            logger.error(e)
            flash("Error updating candidate.", "danger")
        finally:
            cur.close()
            release_db_connection(conn)
        return redirect(url_for("index"))
    else:
        try:
            cur.execute("""
                SELECT c.first_name, c.last_name, c.party, c.incumbent,
                       ces.status, ces.district_code, c.address, c.city, c.zip
                FROM candidates c
                LEFT JOIN candidate_election_status ces
                  ON c.candidate_id=ces.candidate_id AND ces.election_year=%s
                WHERE c.candidate_id=%s;
            """, (election_year, candidate_id))
            row = cur.fetchone()
            if not row:
                flash("Candidate not found.", "warning")
                return redirect(url_for("index"))
            first_name, last_name, party, incumbent, status, district_code, address, city, zip_code = row
            
            # Get voter info if voter_id exists
            voter_info = None
            cur.execute("SELECT voter_id FROM candidates WHERE candidate_id = %s", (candidate_id,))
            voter_id_row = cur.fetchone()
            voter_id = voter_id_row[0] if voter_id_row else None
            
            if voter_id:
                try:
                    voter_conn = get_voter_db_connection()
                    if voter_conn:
                        voter_cur = voter_conn.cursor()
                        try:
                            voter_cur.execute("""
                                SELECT id_voter, nm_first, nm_mid, nm_last, nm_suff, 
                                       ad_num, ad_str1, ad_city, ad_zip5, ward, county, cd_party
                                FROM statewidechecklist
                                WHERE id_voter = %s
                            """, (voter_id,))
                            v = voter_cur.fetchone()
                            if v:
                                voter_info = {
                                    'voter_id': v[0],
                                    'name': f"{v[1]} {v[2] or ''} {v[3]} {v[4] or ''}".strip(),
                                    'address': f"{v[5] or ''} {v[6] or ''}".strip(),
                                    'city': v[7],
                                    'zip': v[8],
                                    'ward': v[9],
                                    'county': v[10],
                                    'party': v[11]
                                }
                            else:
                                logger.warning(f"Voter ID {voter_id} not found in voter database")
                        finally:
                            voter_cur.close()
                            release_voter_db_connection(voter_conn)
                    else:
                        logger.warning(f"Could not connect to voter database for voter_id {voter_id}")
                except Exception as e:
                    logger.error(f"Error fetching voter info for voter_id {voter_id}: {e}")

            cur.execute("""
                SELECT comment_id, comment_text, added_by, added_at
                FROM comments
                WHERE candidate_id = %s
                ORDER BY added_at DESC
                LIMIT 5
            """, (candidate_id,))
            comments = cur.fetchall()

            # Get districts that include the candidate's city
            if city:
                cur.execute("""
                    SELECT DISTINCT full_district_code 
                    FROM districts 
                    WHERE UPPER(town) = UPPER(%s)
                    ORDER BY full_district_code
                """, (city,))
                districts = [row[0] for row in cur.fetchall()]
                
                # If no matches, fall back to all districts
                if not districts:
                    cur.execute("""
                        SELECT DISTINCT full_district_code 
                        FROM districts 
                        ORDER BY full_district_code
                    """)
                    districts = [row[0] for row in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT DISTINCT full_district_code 
                    FROM districts 
                    ORDER BY full_district_code
                """)
                districts = [row[0] for row in cur.fetchall()]

            # Get scores from candidate_scores table
            cur.execute("""
                SELECT score_type, score_year, score_value, letter_grade
                FROM candidate_scores
                WHERE candidate_id = %s
                ORDER BY score_year DESC, score_type
            """, (candidate_id,))
            scores = cur.fetchall()
            candidate_scores = [
                {'type': s[0], 'year': s[1], 'value': float(s[2]) if s[2] is not None else None, 'letter': s[3]} for s in scores
            ]

        except Exception as e:
            logger.error(e)
            flash("Error loading candidate data.", "danger")
            comments = []
            districts = []
            district_code = None
            address = None
            city = None
            zip_code = None
        finally:
            cur.close()
            release_db_connection(conn)
        return render_template("edit_candidate.html",
                                candidate_id=candidate_id,
                                election_year=election_year,
                                first_name=first_name,
                                last_name=last_name,
                                party=party,
                                incumbent=incumbent,
                                status=status,
                                district_code=district_code,
                                address=address,
                                city=city,
                                zip=zip_code,
                                districts=districts,
                                comments=comments,
                                voter_info=voter_info,
                                voter_id_exists=bool(voter_id),
                                scores=candidate_scores)

@app.route('/copy_candidate_to_2026/<int:candidate_id>', methods=['POST'])
@candidate_restricted
@admin_required
def copy_candidate_to_2026(candidate_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT status_id FROM candidate_election_status
            WHERE candidate_id = %s AND election_year = 2026
        """, (candidate_id,))
        if cur.fetchone():
            flash("Candidate already has a 2026 entry.", "warning")
            return redirect(url_for("index"))
        cur.execute("""
            SELECT district_code, status FROM candidate_election_status
            WHERE candidate_id = %s AND election_year = 2024
        """, (candidate_id,))
        row = cur.fetchone()
        if not row:
            flash("Candidate does not have a 2024 entry.", "warning")
            return redirect(url_for("index"))
        district_code = request.form.get("district_code", row[0]).strip()
        status = request.form.get("status", "New Recruit").strip()
        comment = request.form.get("comment", "").strip()
        cur.execute("""
            INSERT INTO candidate_election_status 
              (candidate_id, election_year, status, is_running, added_by, district_code)
            VALUES (%s, 2026, %s, TRUE, %s, %s)
        """, (candidate_id, status, current_user.email, district_code))
        if comment:
            cur.execute("""
                INSERT INTO comments (candidate_id, comment_text, added_by)
                VALUES (%s, %s, %s)
            """, (candidate_id, comment, current_user.email))
        conn.commit()
        log_activity('candidate_copied', f"Copied candidate to 2026 in {district_code} ({status})", candidate_id)
        flash("Candidate successfully copied to 2026.", "success")
    except Exception as e:
        conn.rollback()
        flash("Error copying candidate to 2026.", "danger")
        logger.error(e)
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for("index"))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email').strip()
        first_name = request.form.get('first_name').strip()
        last_name = request.form.get('last_name').strip()
        party = request.form.get('party').strip()
        password = request.form.get('password').strip()
        confirm_password = request.form.get('confirm_password').strip()
        token = request.form.get('token').strip()

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for('register'))

        if not token:
            flash("Registration token is required.", "danger")
            return redirect(url_for('register'))

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT token, used FROM registration_tokens
                WHERE token = %s AND NOT used
            """, (token,))
            token_row = cur.fetchone()
            if not token_row:
                flash("Invalid or used registration token.", "danger")
                return redirect(url_for('register'))

            cur.execute("SELECT candidate_id FROM candidates WHERE email ILIKE %s", (email,))
            if cur.fetchone():
                flash("Email already registered.", "warning")
                return redirect(url_for('register'))

            hashed_password = generate_password_hash(password)
            cur.execute("""
                INSERT INTO candidates (first_name, last_name, party, email, password_hash, password_changed, created_by)
                VALUES (%s, %s, %s, %s, %s, FALSE, %s)
                RETURNING candidate_id;
            """, (first_name, last_name, party, email, hashed_password, "self_register"))

            cur.execute("""
                UPDATE registration_tokens
                SET used = TRUE
                WHERE token = %s
            """, (token,))

            conn.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for('login'))
        except Exception as e:
            conn.rollback()
            logger.error(e)
            flash("Registration failed.", "danger")
        finally:
            cur.close()
            release_db_connection(conn)
    return render_template("register.html")

@app.route('/generate_token', methods=['POST'])
@admin_required
def generate_token():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        token_count = int(request.form.get('token_count', 1))
        tokens = []
        for _ in range(token_count):
            token = os.urandom(16).hex()
            cur.execute("""
                INSERT INTO registration_tokens (token, created_by)
                VALUES (%s, %s)
            """, (token, current_user.email))
            tokens.append(token)
        conn.commit()
        flash(f"Generated {token_count} new tokens: {', '.join(tokens)}", "success")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error generating tokens: {e}")
        flash("Error generating tokens.", "danger")
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def login():
    if request.method == 'POST':
        email = request.form.get('email').strip()
        password = request.form.get('password').strip()
        conn = get_db_connection()
        cur = conn.cursor()

        # Check candidates table first
        cur.execute("""
            SELECT candidate_id, email, password_hash, first_name, last_name, password_changed, photo_url,
                   COALESCE(totp_enabled, FALSE) as totp_enabled
            FROM candidates
            WHERE email = %s
        """, (email,))
        user_row = cur.fetchone()

        if user_row:
            candidate_id, email, password_hash, first_name, last_name, password_changed, photo_url, totp_enabled = user_row
            if password_hash and check_password_hash(password_hash, password):
                # Check if 2FA is enabled
                if totp_enabled:
                    session['pending_2fa_user'] = {'type': 'candidate', 'id': candidate_id}
                    cur.close()
                    release_db_connection(conn)
                    return redirect(url_for('verify_2fa'))

                user = CandidateUser(candidate_id, email, password_hash, first_name, last_name, password_changed, photo_url)
                login_user(user)
                cur.execute("UPDATE candidates SET last_login = NOW() WHERE candidate_id = %s", (candidate_id,))
                conn.commit()
                session.permanent = True
                flash("Logged in successfully.", "success")
                cur.close()
                release_db_connection(conn)
                if not password_changed:
                    flash("Please change your password on first login.", "warning")
                    return redirect(url_for('change_password'))
                next_page = request.args.get('next')
                return redirect(next_page if next_page else url_for('index'))

        # Check admin users table
        cur.execute("""
            SELECT user_id, username, email, password_hash, role,
                   COALESCE(totp_enabled, FALSE) as totp_enabled
            FROM users
            WHERE email = %s
        """, (email,))
        admin_row = cur.fetchone()
        cur.close()
        release_db_connection(conn)

        if admin_row and admin_row[3] and check_password_hash(admin_row[3], password):
            # Check if 2FA is enabled
            if admin_row[5]:  # totp_enabled
                session['pending_2fa_user'] = {'type': 'admin', 'id': admin_row[0]}
                return redirect(url_for('verify_2fa'))

            user = AdminUser(admin_row[0], admin_row[1], admin_row[2], admin_row[3], admin_row[4])
            login_user(user)
            conn2 = get_db_connection()
            cur2 = conn2.cursor()
            cur2.execute("UPDATE users SET last_login = NOW() WHERE user_id = %s", (admin_row[0],))
            conn2.commit()
            cur2.close()
            release_db_connection(conn2)
            session.permanent = True
            next_page = request.args.get('next')
            return redirect(next_page if next_page else url_for('index'))

        flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('pending_2fa_user', None)
    flash("Logged out.", "success")
    return redirect(url_for('login'))


# ============== PASSWORD RESET ==============

def generate_reset_token(user_type, user_id):
    """Generate a secure password reset token."""
    return token_serializer.dumps({'type': user_type, 'id': user_id, 'action': 'reset'}, salt='password-reset')

def verify_reset_token(token, max_age=3600):
    """Verify a password reset token (1 hour expiry)."""
    try:
        data = token_serializer.loads(token, salt='password-reset', max_age=max_age)
        if data.get('action') != 'reset':
            return None
        return data
    except Exception:
        return None

def send_password_reset_email(email, name, user_type, user_id):
    """Send password reset email."""
    token = generate_reset_token(user_type, user_id)
    reset_url = f"{APP_URL}/reset-password/{token}"

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #d91720;">Password Reset Request</h2>
        <p>Hi {name},</p>
        <p>We received a request to reset your password for the NH Candidate Recruitment system.</p>
        <p>Click the button below to reset your password:</p>
        <p style="margin: 30px 0;">
            <a href="{reset_url}" style="background-color: #d91720; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; display: inline-block;">Reset Password</a>
        </p>
        <p style="color: #666; font-size: 14px;">This link will expire in 1 hour.</p>
        <p style="color: #666; font-size: 14px;">If you didn't request this, you can safely ignore this email.</p>
        <hr style="border: 1px solid #eee; margin: 30px 0;">
        <p style="color: #999; font-size: 12px;">Committee to Elect House Republicans</p>
    </body>
    </html>
    """

    text_body = f"Hi {name},\n\nReset your password here: {reset_url}\n\nThis link expires in 1 hour."

    return send_email(email, "Password Reset Request", html_body, text_body)

@app.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("3 per minute")
def forgot_password():
    """Handle forgot password requests."""
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()

        if not email:
            flash("Please enter your email address.", "danger")
            return render_template('forgot_password.html')

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            # Check candidates table
            cur.execute("SELECT candidate_id, first_name FROM candidates WHERE LOWER(email) = %s", (email,))
            candidate = cur.fetchone()

            if candidate:
                send_password_reset_email(email, candidate[1], 'candidate', candidate[0])
                flash("If an account exists with that email, you'll receive a password reset link shortly.", "success")
                return redirect(url_for('login'))

            # Check users table
            cur.execute("SELECT user_id, username FROM users WHERE LOWER(email) = %s", (email,))
            user = cur.fetchone()

            if user:
                send_password_reset_email(email, user[1], 'admin', user[0])

            # Always show success to prevent email enumeration
            flash("If an account exists with that email, you'll receive a password reset link shortly.", "success")
            return redirect(url_for('login'))

        finally:
            cur.close()
            release_db_connection(conn)

    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
@csrf.exempt
def reset_password(token):
    """Handle password reset from email link."""
    data = verify_reset_token(token)
    if not data:
        flash("This password reset link has expired or is invalid. Please request a new one.", "danger")
        return redirect(url_for('forgot_password'))

    user_type = data['type']
    user_id = data['id']

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if user_type == 'candidate':
            cur.execute("SELECT email, first_name FROM candidates WHERE candidate_id = %s", (user_id,))
        else:
            cur.execute("SELECT email, username FROM users WHERE user_id = %s", (user_id,))

        row = cur.fetchone()
        if not row:
            flash("Account not found.", "danger")
            return redirect(url_for('login'))

        user_email, user_name = row

        if request.method == 'POST':
            password = request.form.get('password', '').strip()
            confirm_password = request.form.get('confirm_password', '').strip()

            if len(password) < 8:
                flash("Password must be at least 8 characters.", "danger")
                return render_template('reset_password.html', token=token, email=user_email)

            if password != confirm_password:
                flash("Passwords do not match.", "danger")
                return render_template('reset_password.html', token=token, email=user_email)

            hashed_password = generate_password_hash(password)

            if user_type == 'candidate':
                cur.execute("UPDATE candidates SET password_hash = %s, password_changed = TRUE WHERE candidate_id = %s",
                           (hashed_password, user_id))
            else:
                cur.execute("UPDATE users SET password_hash = %s WHERE user_id = %s",
                           (hashed_password, user_id))

            conn.commit()
            flash("Password reset successfully! You can now log in.", "success")
            return redirect(url_for('login'))

        return render_template('reset_password.html', token=token, email=user_email)

    finally:
        cur.close()
        release_db_connection(conn)


# ============== ACCOUNT SETUP (from email invite) ==============

@app.route('/setup-account/<token>', methods=['GET', 'POST'])
@csrf.exempt  # Token itself provides security
def setup_account(token):
    """Handle initial account setup from email invite."""
    data = verify_invite_token(token)
    if not data:
        flash("This setup link has expired or is invalid. Please request a new invite.", "danger")
        return redirect(url_for('login'))

    user_type = data['type']
    user_id = data['id']

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if user_type == 'candidate':
            cur.execute("""
                SELECT candidate_id, email, first_name, last_name, password_hash
                FROM candidates WHERE candidate_id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                flash("User not found.", "danger")
                return redirect(url_for('login'))
            user_email = row[1]
            user_name = f"{row[2]} {row[3]}"
            has_password = row[4] is not None
        else:
            cur.execute("""
                SELECT user_id, email, username, password_hash
                FROM users WHERE user_id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                flash("User not found.", "danger")
                return redirect(url_for('login'))
            user_email = row[1]
            user_name = row[2]
            has_password = row[3] is not None

        if request.method == 'POST':
            password = request.form.get('password', '').strip()
            confirm_password = request.form.get('confirm_password', '').strip()
            enable_2fa = request.form.get('enable_2fa') == 'on'

            if len(password) < 8:
                flash("Password must be at least 8 characters.", "danger")
                return render_template('setup_account.html', user_name=user_name, user_email=user_email, token=token)

            if password != confirm_password:
                flash("Passwords do not match.", "danger")
                return render_template('setup_account.html', user_name=user_name, user_email=user_email, token=token)

            hashed_password = generate_password_hash(password)

            if user_type == 'candidate':
                cur.execute("""
                    UPDATE candidates
                    SET password_hash = %s, password_changed = TRUE
                    WHERE candidate_id = %s
                """, (hashed_password, user_id))
            else:
                cur.execute("""
                    UPDATE users
                    SET password_hash = %s
                    WHERE user_id = %s
                """, (hashed_password, user_id))

            conn.commit()

            if enable_2fa:
                # Store pending 2FA setup in session
                session['pending_2fa_setup'] = {
                    'type': user_type,
                    'id': user_id
                }
                flash("Password set successfully! Now let's set up two-factor authentication.", "success")
                return redirect(url_for('setup_2fa'))

            flash("Account set up successfully! You can now log in.", "success")
            return redirect(url_for('login'))

        return render_template('setup_account.html', user_name=user_name, user_email=user_email, token=token, has_password=has_password)

    finally:
        cur.close()
        release_db_connection(conn)


@app.route('/setup-2fa', methods=['GET', 'POST'])
def setup_2fa():
    """Set up two-factor authentication."""
    pending_setup = session.get('pending_2fa_setup')

    # Also allow logged-in users to set up 2FA
    if not pending_setup and current_user.is_authenticated:
        if current_user.is_candidate:
            pending_setup = {'type': 'candidate', 'id': current_user.candidate_id}
        else:
            pending_setup = {'type': 'admin', 'id': current_user.user_id}

    if not pending_setup:
        flash("Please log in first.", "warning")
        return redirect(url_for('login'))

    user_type = pending_setup['type']
    user_id = pending_setup['id']

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if user_type == 'candidate':
            cur.execute("SELECT email, first_name FROM candidates WHERE candidate_id = %s", (user_id,))
            row = cur.fetchone()
            email = row[0]
            display_name = row[1]
        else:
            cur.execute("SELECT email, username FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            email = row[0]
            display_name = row[1]

        if request.method == 'POST':
            totp_secret = request.form.get('totp_secret')
            verification_code = request.form.get('verification_code', '').strip()

            # Verify the code
            totp = pyotp.TOTP(totp_secret)
            if not totp.verify(verification_code):
                flash("Invalid verification code. Please try again.", "danger")
                # Regenerate QR code with same secret
                provisioning_uri = totp.provisioning_uri(name=email, issuer_name="NH GOP Recruit")
                qr = qrcode.make(provisioning_uri)
                buffer = io.BytesIO()
                qr.save(buffer, format='PNG')
                qr_base64 = base64.b64encode(buffer.getvalue()).decode()
                return render_template('setup_2fa.html', qr_code=qr_base64, totp_secret=totp_secret, email=email)

            # Save the secret
            if user_type == 'candidate':
                cur.execute("""
                    UPDATE candidates
                    SET totp_secret = %s, totp_enabled = TRUE
                    WHERE candidate_id = %s
                """, (totp_secret, user_id))
            else:
                cur.execute("""
                    UPDATE users
                    SET totp_secret = %s, totp_enabled = TRUE
                    WHERE user_id = %s
                """, (totp_secret, user_id))

            conn.commit()
            session.pop('pending_2fa_setup', None)

            flash("Two-factor authentication enabled successfully!", "success")
            return redirect(url_for('login'))

        # Generate new TOTP secret and QR code
        totp_secret = pyotp.random_base32()
        totp = pyotp.TOTP(totp_secret)
        provisioning_uri = totp.provisioning_uri(name=email, issuer_name="NH GOP Recruit")

        qr = qrcode.make(provisioning_uri)
        buffer = io.BytesIO()
        qr.save(buffer, format='PNG')
        qr_base64 = base64.b64encode(buffer.getvalue()).decode()

        return render_template('setup_2fa.html', qr_code=qr_base64, totp_secret=totp_secret, email=email)

    finally:
        cur.close()
        release_db_connection(conn)


@app.route('/verify-2fa', methods=['GET', 'POST'])
def verify_2fa():
    """Verify 2FA code during login."""
    pending_2fa = session.get('pending_2fa_user')
    if not pending_2fa:
        return redirect(url_for('login'))

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        user_type = pending_2fa['type']
        user_id = pending_2fa['id']

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            if user_type == 'candidate':
                cur.execute("SELECT totp_secret FROM candidates WHERE candidate_id = %s", (user_id,))
            else:
                cur.execute("SELECT totp_secret FROM users WHERE user_id = %s", (user_id,))

            row = cur.fetchone()
            if not row or not row[0]:
                flash("2FA not configured properly.", "danger")
                session.pop('pending_2fa_user', None)
                return redirect(url_for('login'))

            totp = pyotp.TOTP(row[0])
            if totp.verify(code):
                # 2FA verified - complete login
                session.pop('pending_2fa_user', None)

                if user_type == 'candidate':
                    cur.execute("""
                        SELECT candidate_id, email, password_hash, first_name, last_name, password_changed, photo_url
                        FROM candidates WHERE candidate_id = %s
                    """, (user_id,))
                    user_row = cur.fetchone()
                    user = CandidateUser(user_row[0], user_row[1], user_row[2], user_row[3], user_row[4], user_row[5], user_row[6])
                    login_user(user)
                    cur.execute("UPDATE candidates SET last_login = NOW() WHERE candidate_id = %s", (user_id,))
                else:
                    cur.execute("SELECT user_id, username, email, password_hash, role FROM users WHERE user_id = %s", (user_id,))
                    user_row = cur.fetchone()
                    user = AdminUser(*user_row)
                    login_user(user)
                    cur.execute("UPDATE users SET last_login = NOW() WHERE user_id = %s", (user_id,))

                conn.commit()
                session.permanent = True
                flash("Logged in successfully.", "success")
                return redirect(url_for('index'))
            else:
                flash("Invalid verification code.", "danger")

        finally:
            cur.close()
            release_db_connection(conn)

    return render_template('verify_2fa.html')


@app.route('/disable-2fa', methods=['POST'])
@login_required
def disable_2fa():
    """Disable 2FA for current user."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if current_user.is_candidate:
            cur.execute("""
                UPDATE candidates
                SET totp_secret = NULL, totp_enabled = FALSE
                WHERE candidate_id = %s
            """, (current_user.candidate_id,))
        else:
            cur.execute("""
                UPDATE users
                SET totp_secret = NULL, totp_enabled = FALSE
                WHERE user_id = %s
            """, (current_user.user_id,))

        conn.commit()
        flash("Two-factor authentication has been disabled.", "success")
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('profile') if current_user.is_candidate else url_for('admin_dashboard'))


@app.route('/resend-invite/<user_type>/<int:user_id>', methods=['POST'])
@admin_required
def resend_invite(user_type, user_id):
    """Resend the welcome/invite email to a user."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if user_type == 'candidate':
            cur.execute("SELECT email, first_name, last_name FROM candidates WHERE candidate_id = %s", (user_id,))
            row = cur.fetchone()
            if row:
                email, first_name, last_name = row
                name = f"{first_name} {last_name}"
                if send_welcome_email(email, name, 'candidate', user_id):
                    flash(f"Invite resent to {email}.", "success")
                else:
                    flash(f"Failed to send invite to {email}.", "danger")
        else:
            cur.execute("SELECT email, username FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if row:
                email, username = row
                if send_welcome_email(email, username, 'admin', user_id):
                    flash(f"Invite resent to {email}.", "success")
                else:
                    flash(f"Failed to send invite to {email}.", "danger")
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('admin_dashboard'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if not current_user.is_candidate:
        flash("Admin users don't have profiles.", "warning")
        return redirect(url_for('admin_dashboard'))
    
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip()
        address = request.form.get('address', '').strip()
        city = request.form.get('city', '').strip()
        zip_code = request.form.get('zip', '').strip()
        phone1 = request.form.get('phone1', '').strip()
        phone2 = request.form.get('phone2', '').strip()
        twitter_x = request.form.get('twitter_x', '').strip()
        facebook = request.form.get('facebook', '').strip()
        instagram = request.form.get('instagram', '').strip()
        other = request.form.get('other', '').strip()
        signal = request.form.get('signal', '').strip()
        email1 = request.form.get('email1', '').strip()
        email2 = request.form.get('email2', '').strip()
        password = request.form.get('password')

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            photo_url = current_user.photo_url
            if 'photo' in request.files:
                photo = request.files['photo']
                if photo.filename != '':
                    filename = secure_filename(photo.filename)
                    destination = f"candidate_photos/{current_user.candidate_id}/{filename}"
                    uploaded_url = upload_file_to_storage(photo, destination)
                    if uploaded_url:
                        photo_url = uploaded_url
            
            if password and password.strip():
                new_password_hash = generate_password_hash(password.strip())
                cur.execute("""
                    UPDATE candidates
                    SET first_name = %s, last_name = %s, email = %s, address = %s, city = %s, zip = %s,
                        phone1 = %s, phone2 = %s, twitter_x = %s, facebook = %s, instagram = %s,
                        other = %s, signal = %s, email1 = %s, email2 = %s, password_hash = %s, photo_url = %s
                    WHERE candidate_id = %s
                """, (first_name, last_name, email, address, city, zip_code, phone1, phone2, twitter_x,
                      facebook, instagram, other, signal, email1, email2, new_password_hash, photo_url, 
                      current_user.candidate_id))
            else:
                cur.execute("""
                    UPDATE candidates
                    SET first_name = %s, last_name = %s, email = %s, address = %s, city = %s, zip = %s,
                        phone1 = %s, phone2 = %s, twitter_x = %s, facebook = %s, instagram = %s,
                        other = %s, signal = %s, email1 = %s, email2 = %s, photo_url = %s
                    WHERE candidate_id = %s
                """, (first_name, last_name, email, address, city, zip_code, phone1, phone2, twitter_x,
                      facebook, instagram, other, signal, email1, email2, photo_url, current_user.candidate_id))
            conn.commit()
            flash("Profile updated.", "success")
        except Exception as e:
            conn.rollback()
            flash("Error updating profile.", "danger")
            logger.error(f"Error updating profile: {e}")
        finally:
            cur.close()
            release_db_connection(conn)
        return redirect(url_for('profile'))
    return render_template("profile.html", user=current_user)

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        new_password = request.form.get('new_password').strip()
        confirm_password = request.form.get('confirm_password').strip()
        if new_password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for('change_password'))
        new_hash = generate_password_hash(new_password)
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            if current_user.is_candidate:
                cur.execute("""
                    UPDATE candidates
                    SET password_hash = %s, password_changed = TRUE
                    WHERE candidate_id = %s
                """, (new_hash, current_user.candidate_id))
            else:
                cur.execute("""
                    UPDATE users
                    SET password_hash = %s
                    WHERE user_id = %s
                """, (new_hash, current_user.user_id))
            conn.commit()
            flash("Password updated successfully.", "success")
        except Exception as e:
            conn.rollback()
            flash("Error updating password.", "danger")
            logger.error(e)
        finally:
            cur.close()
            release_db_connection(conn)
        return redirect(url_for('profile') if current_user.is_candidate else url_for('admin_dashboard'))
    return render_template("change_password.html")

@app.route('/comments/<int:candidate_id>', methods=['GET', 'POST'])
@candidate_restricted
@admin_required
def comments(candidate_id):
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        comment_text = request.form.get('comment_text').strip()
        try:
            cur.execute("""
                INSERT INTO comments (candidate_id, comment_text, added_by)
                VALUES (%s, %s, %s)
            """, (candidate_id, comment_text, current_user.email))
            conn.commit()
            flash("Comment added.", "success")
        except Exception as e:
            conn.rollback()
            flash("Error adding comment.", "danger")
    cur.execute("""
        SELECT comment_id, comment_text, added_by, added_at
        FROM comments
        WHERE candidate_id = %s
        ORDER BY added_at DESC
    """, (candidate_id,))
    comments_list = cur.fetchall()
    cur.execute("SELECT first_name, last_name FROM candidates WHERE candidate_id = %s", (candidate_id,))
    candidate = cur.fetchone()
    cur.close()
    release_db_connection(conn)
    return render_template('comments.html', comments=comments_list, candidate=candidate, candidate_id=candidate_id)

@app.route('/history/<int:candidate_id>')
@candidate_restricted
def history(candidate_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT history_id, first_name, last_name, incumbent, address, city, zip, phone1, phone2,
               twitter_x, facebook, instagram, other, signal, email1, email2, change_comment, changed_by, changed_at
        FROM candidate_history
        WHERE candidate_id = %s
        ORDER BY changed_at DESC
    """, (candidate_id,))
    history_list = cur.fetchall()
    cur.execute("SELECT first_name, last_name FROM candidates WHERE candidate_id = %s", (candidate_id,))
    candidate = cur.fetchone()
    cur.close()
    release_db_connection(conn)
    return render_template('history.html', history=history_list, candidate=candidate)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        email = request.form.get('email').strip()
        password = request.form.get('password').strip()
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT user_id, username, email, password_hash, role
            FROM users
            WHERE email = %s
        """, (email,))
        user_row = cur.fetchone()
        cur.close()
        release_db_connection(conn)
        if user_row and check_password_hash(user_row[3], password):
            user = AdminUser(*user_row)
            login_user(user)
            session.permanent = True
            flash("Admin logged in successfully.", "success")
            return redirect(url_for('admin_dashboard'))
        flash("Invalid email or password.", "danger")
    return render_template("admin_login.html")

@app.route('/admin/dashboard')
@super_admin_required
def admin_dashboard():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT candidate_id, first_name, last_name, email, party, last_login
            FROM candidates
            WHERE email IS NOT NULL AND password_hash IS NOT NULL
        """)
        candidate_users = cur.fetchall()

        cur.execute("""
            SELECT user_id, username, email, role, last_login
            FROM users
            ORDER BY user_id ASC
        """)
        admins = cur.fetchall()

        cur.execute("""
            SELECT token, created_at, used, created_by
            FROM registration_tokens
        """)
        tokens = cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        flash("Error loading user data.", "danger")
        candidate_users = []
        admins = []
        tokens = []
    finally:
        cur.close()
        release_db_connection(conn)
    return render_template('admin_dashboard.html', candidate_users=candidate_users, admins=admins, tokens=tokens, timedelta=timedelta)

@app.route('/add_user', methods=['GET', 'POST'])
@admin_required
def add_user():
    if request.method == 'POST':
        user_type = request.form.get('user_type')
        email = request.form.get('email').strip()
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        party = request.form.get('party', '').strip()
        role = request.form.get('role', 'admin').strip()

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Check if email already exists
            cur.execute("SELECT 1 FROM candidates WHERE email ILIKE %s", (email,))
            if cur.fetchone():
                flash("Email already exists in candidates.", "danger")
                return redirect(url_for('add_user'))
            cur.execute("SELECT 1 FROM users WHERE email ILIKE %s", (email,))
            if cur.fetchone():
                flash("Email already exists in admin users.", "danger")
                return redirect(url_for('add_user'))

            if user_type == 'candidate':
                if not (first_name and last_name and party):
                    flash("First name, last name, and party are required for candidates.", "danger")
                    return redirect(url_for('add_user'))
                # Create candidate without password - they'll set it via email link
                cur.execute("""
                    INSERT INTO candidates (first_name, last_name, party, email, password_hash, password_changed, created_by)
                    VALUES (%s, %s, %s, %s, NULL, FALSE, %s)
                    RETURNING candidate_id
                """, (first_name, last_name, party, email, current_user.email))
                user_id = cur.fetchone()[0]
                conn.commit()

                # Send welcome email
                name = f"{first_name} {last_name}"
                if send_welcome_email(email, name, 'candidate', user_id):
                    flash(f"Candidate added and welcome email sent to {email}.", "success")
                else:
                    flash(f"Candidate added but failed to send welcome email to {email}.", "warning")
            else:
                if not role:
                    flash("Role is required for admins.", "danger")
                    return redirect(url_for('add_user'))
                # Create admin without password - they'll set it via email link
                username = email.split('@')[0]
                cur.execute("""
                    INSERT INTO users (username, email, password_hash, role, created_at)
                    VALUES (%s, %s, NULL, %s, CURRENT_TIMESTAMP)
                    RETURNING user_id
                """, (username, email, role))
                user_id = cur.fetchone()[0]
                conn.commit()

                # Send welcome email
                if send_welcome_email(email, username, 'admin', user_id):
                    flash(f"Admin added and welcome email sent to {email}.", "success")
                else:
                    flash(f"Admin added but failed to send welcome email to {email}.", "warning")
        except Exception as e:
            conn.rollback()
            logger.error(f"Error adding user: {e}")
            flash("Error adding user.", "danger")
        finally:
            cur.close()
            release_db_connection(conn)
        return redirect(url_for('admin_dashboard'))
    return render_template('add_user.html')

@app.route('/edit_user/<user_type>/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def edit_user(user_type, user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        email = request.form.get('email').strip()
        password = request.form.get('password', '').strip()
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        party = request.form.get('party', '').strip()
        role = request.form.get('role', '').strip()

        try:
            if user_type == 'candidate':
                if password:
                    hashed_password = generate_password_hash(password)
                    cur.execute("""
                        UPDATE candidates
                        SET email = %s, password_hash = %s, first_name = %s, last_name = %s, party = %s
                        WHERE candidate_id = %s
                    """, (email, hashed_password, first_name, last_name, party, user_id))
                else:
                    cur.execute("""
                        UPDATE candidates
                        SET email = %s, first_name = %s, last_name = %s, party = %s
                        WHERE candidate_id = %s
                    """, (email, first_name, last_name, party, user_id))
                flash("Candidate updated successfully.", "success")
            else:
                if password:
                    hashed_password = generate_password_hash(password)
                    cur.execute("""
                        UPDATE users
                        SET email = %s, password_hash = %s, username = %s, role = %s
                        WHERE user_id = %s
                    """, (email, hashed_password, email.split('@')[0], role, user_id))
                else:
                    cur.execute("""
                        UPDATE users
                        SET email = %s, username = %s, role = %s
                        WHERE user_id = %s
                    """, (email, email.split('@')[0], role, user_id))
                flash("Admin updated successfully.", "success")
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Error editing user: {e}")
            flash("Error editing user.", "danger")
        finally:
            cur.close()
            release_db_connection(conn)
        return redirect(url_for('admin_dashboard'))
    else:
        try:
            if user_type == 'candidate':
                cur.execute("""
                    SELECT candidate_id, first_name, last_name, email, party
                    FROM candidates
                    WHERE candidate_id = %s
                """, (user_id,))
                user = cur.fetchone()
                if not user:
                    flash("Candidate not found.", "warning")
                    return redirect(url_for('admin_dashboard'))
            else:
                cur.execute("""
                    SELECT user_id, username, email, role
                    FROM users
                    WHERE user_id = %s
                """, (user_id,))
                user = cur.fetchone()
                if not user:
                    flash("Admin not found.", "warning")
                    return redirect(url_for('admin_dashboard'))
        except Exception as e:
            logger.error(f"Error loading user: {e}")
            flash("Error loading user data.", "danger")
            user = None
        finally:
            cur.close()
            release_db_connection(conn)
        return render_template('edit_user.html', user_type=user_type, user=user)

@app.route('/delete_user/<user_type>/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_type, user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if user_type == 'candidate':
            cur.execute("""
                UPDATE candidates
                SET password_hash = NULL, password_changed = FALSE
                WHERE candidate_id = %s
            """, (user_id,))
            flash("Candidate user's login credentials removed successfully.", "success")
        else:
            cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
            flash("Admin deleted successfully.", "success")
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting user: {e}")
        flash("Error deleting user.", "danger")
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/delete_users_bulk', methods=['POST'])
@admin_required
def delete_users_bulk():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        candidate_ids = request.form.getlist('candidate_ids')
        if not candidate_ids:
            flash("No candidate users selected for deletion.", "warning")
        else:
            for candidate_id in candidate_ids:
                cur.execute("""
                    UPDATE candidates
                    SET password_hash = NULL, password_changed = FALSE
                    WHERE candidate_id = %s
                """, (candidate_id,))
            flash(f"Removed login credentials for {len(candidate_ids)} candidate users successfully.", "success")
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting users: {e}")
        flash("Error deleting users.", "danger")
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update_candidates', methods=['GET', 'POST'])
@admin_required
def update_candidates():
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        if 'file' not in request.files:
            flash("No file part", "danger")
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            flash("No selected file", "danger")
            return redirect(request.url)
        if file and file.filename.endswith('.csv'):
            candidates_data = []
            content = file.read().decode('utf-8')
            reader = csv.DictReader(content.splitlines(), fieldnames=['submission_time', 'first_name', 'last_name', 'email', 'phone', 'address', 'city', 'zip', 'photo_url', 'local_issues', 'other_info', 'flags'])
            next(reader, None)
            for row in reader:
                row = {k: (v.strip() if isinstance(v, str) else v)[:50] if k in ['first_name', 'last_name', 'city'] else (v.strip() if isinstance(v, str) else v)[:20] if k in ['phone'] else (v.strip() if isinstance(v, str) else v)[:100] if k in ['address'] else (v.strip() if isinstance(v, str) else v)[:10] if k in ['zip'] else (v.strip() if isinstance(v, str) else v)[:255] if k in ['email'] else v for k, v in row.items()}
                candidates_data.append(row)
            updated_count = 0
            try:
                for candidate in candidates_data:
                    first_name = candidate.get('first_name', '')[:50]
                    last_name = candidate.get('last_name', '')[:50]
                    email = candidate.get('email', '').lower()[:255]
                    address = candidate.get('address', '')[:100]
                    city = candidate.get('city', '')[:50]
                    zip_code = candidate.get('zip', '')[:10]
                    phone = candidate.get('phone', '')[:20]
                    photo_url = candidate.get('photo_url', '')

                    cur.execute("""
                        SELECT candidate_id, first_name, last_name, email, address, city, zip, phone1, photo_url, other_info
                        FROM candidates
                        WHERE LOWER(email) = %s OR (UPPER(first_name) = UPPER(%s) AND UPPER(last_name) = UPPER(%s))
                    """, (email, first_name, last_name))
                    existing = cur.fetchone()

                    if existing:
                        candidate_id = existing[0]
                        existing_address = existing[4]
                        existing_city = existing[5]
                        existing_zip = existing[6]
                        existing_phone1 = existing[7]
                        existing_photo_url = existing[8]
                        existing_other_info = existing[9]

                        new_address = address or existing_address
                        new_city = city or existing_city
                        new_zip = zip_code or existing_zip
                        new_phone1 = phone or existing_phone1
                        new_photo_url = photo_url or existing_photo_url

                        local_issues = candidate.get('local_issues', '')
                        flags = candidate.get('flags', '')
                        other_info_text = candidate.get('other_info', '')
                        new_other_info = ""
                        if local_issues:
                            new_other_info += f"Local Issues: {local_issues}\n"
                        if flags:
                            new_other_info += f"Flags: {flags}\n"
                        if other_info_text:
                            new_other_info += f"Other Info: {other_info_text}\n"
                        if existing_other_info and not new_other_info:
                            new_other_info = existing_other_info
                        elif existing_other_info:
                            new_other_info = f"{existing_other_info}\n{new_other_info}".strip()

                        cur.execute("""
                            UPDATE candidates
                            SET first_name = %s, last_name = %s, email = %s, address = %s, city = %s, zip = %s, phone1 = %s, photo_url = %s, other_info = %s
                            WHERE candidate_id = %s
                        """, (first_name, last_name, email, new_address, new_city, new_zip, new_phone1, new_photo_url, new_other_info, candidate_id))
                        updated_count += 1

                conn.commit()
                flash(f"Updated {updated_count} candidate records.", "success")
            except Exception as e:
                conn.rollback()
                logger.error(f"Error updating candidates: {e}")
                flash("Error updating candidates.", "danger")
            finally:
                cur.close()
                release_db_connection(conn)
        else:
            flash("Please upload a CSV file", "danger")
    return render_template('admin_dashboard.html')

@app.route('/admin/export/<export_type>')
@super_admin_required
def export_csv(export_type):
    """Export data to CSV"""
    import io
    import csv
    from flask import Response
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        include_voter = False
        voter_id_index = None
        
        if export_type == 'all_candidates':
            cur.execute("""
                SELECT DISTINCT c.candidate_id, c.first_name, c.last_name, c.party, 
                       c.email, c.email1, c.email2,
                       c.address, c.city, c.zip, c.phone1, c.phone2, 
                       c.incumbent, c.twitter_x, c.facebook, c.instagram, c.signal,
                       c.voter_id, ces.district_code, ces.status
                FROM candidates c
                JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                WHERE ces.election_year = 2026 AND c.party = 'R'
                ORDER BY ces.district_code, c.last_name
            """)
            headers = ['ID', 'First Name', 'Last Name', 'Party', 'Email', 'Email1', 'Email2', 'Address', 'City', 'ZIP', 'Phone1', 'Phone2', 'Incumbent', 'Twitter/X', 'Facebook', 'Instagram', 'Signal', 'Voter ID', 'District', 'Status', 'Voter Name', 'Voter Address', 'Voter City', 'Voter ZIP', 'Voter Ward', 'Voter County', 'Voter Party']
            filename = 'all_candidates_2026.csv'
            include_voter = True
            voter_id_index = 17
            
        elif export_type == 'confirmed':
            cur.execute("""
                SELECT DISTINCT c.candidate_id, c.first_name, c.last_name, c.party,
                       c.email, c.email1, c.email2,
                       c.address, c.city, c.zip, c.phone1, c.phone2,
                       c.incumbent, c.twitter_x, c.facebook, c.instagram, c.signal,
                       c.voter_id, ces.district_code
                FROM candidates c
                JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                WHERE ces.election_year = 2026 AND ces.status = 'Confirmed' AND c.party = 'R'
                ORDER BY ces.district_code, c.last_name
            """)
            headers = ['ID', 'First Name', 'Last Name', 'Party', 'Email', 'Email1', 'Email2', 'Address', 'City', 'ZIP', 'Phone1', 'Phone2', 'Incumbent', 'Twitter/X', 'Facebook', 'Instagram', 'Signal', 'Voter ID', 'District', 'Voter Name', 'Voter Address', 'Voter City', 'Voter ZIP', 'Voter Ward', 'Voter County', 'Voter Party']
            filename = 'confirmed_candidates_2026.csv'
            include_voter = True
            voter_id_index = 17
            
        elif export_type == 'potentials':
            cur.execute("""
                SELECT DISTINCT c.candidate_id, c.first_name, c.last_name, c.party,
                       c.email, c.email1, c.email2,
                       c.address, c.city, c.zip, c.phone1, c.phone2,
                       c.twitter_x, c.facebook, c.instagram, c.signal,
                       c.voter_id, ces.district_code, ces.status
                FROM candidates c
                JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                WHERE ces.election_year = 2026 AND ces.status IN ('Potential', 'Considering') AND c.party = 'R'
                ORDER BY ces.district_code, c.last_name
            """)
            headers = ['ID', 'First Name', 'Last Name', 'Party', 'Email', 'Email1', 'Email2', 'Address', 'City', 'ZIP', 'Phone1', 'Phone2', 'Twitter/X', 'Facebook', 'Instagram', 'Signal', 'Voter ID', 'District', 'Status', 'Voter Name', 'Voter Address', 'Voter City', 'Voter ZIP', 'Voter Ward', 'Voter County', 'Voter Party']
            filename = 'potential_candidates_2026.csv'
            include_voter = True
            voter_id_index = 16
            
        elif export_type == 'empty_districts':
            cur.execute("""
                SELECT DISTINCT d.full_district_code, d.county_name, d.seat_count, 
                       STRING_AGG(DISTINCT d.town, ', ') as towns
                FROM districts d
                WHERE d.full_district_code NOT IN (
                    SELECT ces.district_code 
                    FROM candidate_election_status ces
                    JOIN candidates c ON ces.candidate_id = c.candidate_id
                    WHERE ces.election_year = 2026 AND ces.status != 'Declined' AND c.party = 'R'
                )
                GROUP BY d.full_district_code, d.county_name, d.seat_count
                ORDER BY d.county_name, d.full_district_code
            """)
            headers = ['District', 'County', 'Seats', 'Towns']
            filename = 'empty_districts_2026.csv'
            
        elif export_type == 'by_county':
            county = request.args.get('county', '')
            cur.execute("""
                SELECT DISTINCT c.candidate_id, c.first_name, c.last_name, c.party,
                       c.email, c.email1, c.email2,
                       c.address, c.city, c.zip, c.phone1, c.phone2,
                       c.incumbent, c.twitter_x, c.facebook, c.instagram, c.signal,
                       c.voter_id, ces.district_code, ces.status
                FROM candidates c
                JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                JOIN districts d ON ces.district_code = d.full_district_code
                WHERE ces.election_year = 2026 AND d.county_name ILIKE %s AND c.party = 'R'
                ORDER BY ces.district_code, c.last_name
            """, (county,))
            headers = ['ID', 'First Name', 'Last Name', 'Party', 'Email', 'Email1', 'Email2', 'Address', 'City', 'ZIP', 'Phone1', 'Phone2', 'Incumbent', 'Twitter/X', 'Facebook', 'Instagram', 'Signal', 'Voter ID', 'District', 'Status', 'Voter Name', 'Voter Address', 'Voter City', 'Voter ZIP', 'Voter Ward', 'Voter County', 'Voter Party']
            filename = f'{county.lower()}_candidates_2026.csv'
            include_voter = True
            voter_id_index = 17
            
        elif export_type == 'contact_list':
            cur.execute("""
                SELECT DISTINCT c.first_name, c.last_name, c.email, c.email1, c.email2,
                       c.phone1, c.phone2, c.city,
                       ces.district_code, ces.status
                FROM candidates c
                JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
                WHERE ces.election_year = 2026 AND ces.status != 'Declined' AND c.party = 'R'
                    AND (c.email IS NOT NULL OR c.phone1 IS NOT NULL)
                ORDER BY c.last_name
            """)
            headers = ['First Name', 'Last Name', 'Email', 'Email1', 'Email2', 'Phone1', 'Phone2', 'City', 'District', 'Status']
            filename = 'contact_list_2026.csv'
            
        else:
            flash("Invalid export type.", "danger")
            return redirect(url_for('admin_dashboard'))
        
        rows = cur.fetchall()
        
        # Add voter info if needed
        if include_voter and voter_id_index is not None:
            voter_conn = get_voter_db_connection()
            enhanced_rows = []
            
            for row in rows:
                row_list = list(row)
                voter_id = row_list[voter_id_index] if len(row_list) > voter_id_index else None
                
                voter_name = ''
                voter_address = ''
                voter_city = ''
                voter_zip = ''
                voter_ward = ''
                voter_county = ''
                voter_party = ''
                
                if voter_id and voter_conn:
                    try:
                        voter_cur = voter_conn.cursor()
                        voter_cur.execute("""
                            SELECT nm_first, nm_mid, nm_last, nm_suff, 
                                   ad_num, ad_str1, ad_city, ad_zip5, ward, county, cd_party
                            FROM statewidechecklist WHERE id_voter = %s
                        """, (voter_id,))
                        v = voter_cur.fetchone()
                        voter_cur.close()
                        if v:
                            voter_name = f"{v[0] or ''} {v[1] or ''} {v[2] or ''} {v[3] or ''}".strip()
                            voter_address = f"{v[4] or ''} {v[5] or ''}".strip()
                            voter_city = v[6] or ''
                            voter_zip = v[7] or ''
                            voter_ward = v[8] or ''
                            voter_county = v[9] or ''
                            voter_party = v[10] or ''
                    except Exception as e:
                        logger.error(f"Error fetching voter {voter_id}: {e}")
                
                row_list.extend([voter_name, voter_address, voter_city, voter_zip, voter_ward, voter_county, voter_party])
                enhanced_rows.append(row_list)
            
            if voter_conn:
                release_voter_db_connection(voter_conn)
            rows = enhanced_rows
        
        # Create CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
        
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
        
    except Exception as e:
        logger.error(f"Error exporting CSV: {e}")
        flash("Error exporting data.", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
        cur.close()
        release_db_connection(conn)

@app.route('/profile/<int:candidate_id>')
@admin_required
def candidate_profile(candidate_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT candidate_id, first_name, last_name, email, address, city, zip, phone1, photo_url, other_info
            FROM candidates
            WHERE candidate_id = %s
        """, (candidate_id,))
        candidate = cur.fetchone()
        if candidate:
            columns = ['candidate_id', 'first_name', 'last_name', 'email', 'address', 'city', 'zip', 'phone1', 'photo_url', 'other_info']
            candidate = dict(zip(columns, candidate))
            return render_template('candidate_profile.html', candidate=candidate)
        else:
            flash("Candidate not found.", "danger")
            return redirect(url_for('admin_dashboard'))
    finally:
        cur.close()
        release_db_connection(conn)

@app.route('/api/candidate/<int:candidate_id>')
@login_required
@csrf.exempt
def get_candidate_data(candidate_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT candidate_id, first_name, last_name, email, address, city, zip, phone1, photo_url, other_info
            FROM candidates
            WHERE candidate_id = %s
        """, (candidate_id,))
        candidate = cur.fetchone()
        if candidate:
            columns = ['candidate_id', 'first_name', 'last_name', 'email', 'address', 'city', 'zip', 'phone1', 'photo_url', 'other_info']
            candidate_dict = dict(zip(columns, candidate))
            
            # Get recent comments
            cur.execute("""
                SELECT comment_text, added_by, added_at
                FROM comments
                WHERE candidate_id = %s
                ORDER BY added_at DESC
                LIMIT 5
            """, (candidate_id,))
            comments = cur.fetchall()
            candidate_dict['comments'] = [
                {'text': c[0], 'by': c[1], 'at': str(c[2])} for c in comments
            ]
            
            # Get scores from candidate_scores table
            cur.execute("""
                SELECT score_type, score_year, score_value, letter_grade
                FROM candidate_scores
                WHERE candidate_id = %s
                ORDER BY score_year DESC, score_type
            """, (candidate_id,))
            scores = cur.fetchall()
            candidate_dict['scores'] = [
                {'type': s[0], 'year': s[1], 'value': float(s[2]) if s[2] is not None else None, 'letter': s[3]} for s in scores
            ]
            
            return jsonify(candidate_dict)
        else:
            return jsonify({'error': 'Candidate not found'}), 404
    finally:
        cur.close()
        release_db_connection(conn)
        
@app.route('/api/unmatch_voter/<int:candidate_id>', methods=['POST'])
@login_required
@csrf.exempt
def unmatch_voter(candidate_id):
    """Clear voter_id from candidate"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE candidates
            SET voter_id = NULL
            WHERE candidate_id = %s
        """, (candidate_id,))
        conn.commit()
        log_activity('voter_unmatched', f"Unmatched from voter file", candidate_id)
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        logger.error(f"Error unmatching voter: {e}")
        return jsonify({'error': 'Failed to unmatch'}), 500
    finally:
        cur.close()
        release_db_connection(conn)
        
@app.route('/activity')
@login_required
@admin_required
def activity_log_page():
    """View activity log"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM activity_log")
        total = cur.fetchone()[0]
        
        cur.execute("""
            SELECT log_id, action_type, description, candidate_id, user_email, created_at
            FROM activity_log
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (per_page, offset))
        activities = cur.fetchall()
        
        total_pages = (total + per_page - 1) // per_page
        
    except Exception as e:
        logger.error(f"Error fetching activity log: {e}")
        activities = []
        total_pages = 1
    finally:
        cur.close()
        release_db_connection(conn)
    
    return render_template('activity_log.html', 
                          activities=activities, 
                          page=page, 
                          total_pages=total_pages,
                          timedelta=timedelta)

@app.route('/match_candidates')
@admin_required
def match_candidates():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        logger.info("Starting match_candidates process")
        cur.execute("SET statement_timeout = '60s'")

        cur.execute("""
            SELECT candidate_id, first_name, last_name, party
            FROM candidates
        """)
        candidates = {f"{row[1]} {row[2]}".upper(): {'id': row[0], 'party': row[3]} for row in cur.fetchall()}
        logger.info(f"Loaded {len(candidates)} candidates")

        offset = 0
        limit = 10000
        matches = []
        while True:
            cur.execute("""
                SELECT DISTINCT nm_first, nm_last, cd_party, ad_str1, ad_city, ad_zip5
                FROM statewidechecklist
                WHERE nm_first IS NOT NULL AND nm_last IS NOT NULL
                LIMIT %s OFFSET %s
            """, (limit, offset))
            batch = cur.fetchall()
            if not batch:
                break
            logger.info(f"Processing batch starting at offset {offset}")
            for entry in batch:
                checklist_name = f"{entry[0]} {entry[1]}".upper().strip()
                checklist_party = entry[2].upper() if entry[2] else None
                for candidate_name, candidate_data in candidates.items():
                    if checklist_name in candidate_name or candidate_name in checklist_name:
                        candidate_id = candidate_data['id']
                        candidate_party = candidate_data['party'].upper() if candidate_data['party'] else ''
                        if not checklist_party or checklist_party == candidate_party:
                            matches.append({
                                'candidate_id': candidate_id,
                                'checklist_name': f"{entry[0]} {entry[1]}",
                                'checklist_party': entry[2],
                                'checklist_address': f"{entry[3]}, {entry[4]}, {entry[5]}",
                                'candidate_party': candidate_party
                            })
            offset += limit
        logger.info(f"Found {len(matches)} potential matches")

        return render_template('match_candidates.html', matches=matches)
    except psycopg2.Error as e:
        logger.error(f"Database operation timed out or failed: {e}")
        flash("Operation timed out or failed. Try again or contact support.", "danger")
        return redirect(url_for('admin_dashboard'))
    except Exception as e:
        logger.error(f"Error in match_candidates: {e}")
        flash("Error processing matches. Check logs for details.", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
        cur.close()
        release_db_connection(conn)

@app.route('/confirm_match/<int:candidate_id>', methods=['POST'])
@admin_required
def confirm_match(candidate_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT nm_first, nm_last, cd_party, ad_str1, ad_city, ad_zip5
            FROM statewidechecklist
            WHERE nm_first || ' ' || nm_last IN (
                SELECT first_name || ' ' || last_name FROM candidates WHERE candidate_id = %s
            )
            LIMIT 1
        """, (candidate_id,))
        checklist_data = cur.fetchone()
        if checklist_data:
            first_name, last_name, party, address, city, zip_code = checklist_data
            cur.execute("""
                UPDATE candidates
                SET first_name = %s, last_name = %s, party = %s, address = %s, city = %s, zip = %s
                WHERE candidate_id = %s
            """, (first_name, last_name, party, address, city, zip_code, candidate_id))
            conn.commit()
            flash("Candidate updated with statewide checklist data.", "success")
        else:
            flash("No matching checklist data found.", "warning")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error confirming match: {e}")
        flash("Error confirming match.", "danger")
    finally:
        cur.close()
        release_db_connection(conn)
    return redirect(url_for('match_candidates'))


# ============== VOTER DATABASE API ENDPOINTS ==============

@app.route('/api/lookup_voter/<int:candidate_id>')
@login_required
@csrf.exempt
def lookup_voter(candidate_id):
    """Look up a candidate in the voter file by name"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get candidate info
        cur.execute("""
            SELECT first_name, last_name, address, city
            FROM candidates
            WHERE candidate_id = %s
        """, (candidate_id,))
        candidate = cur.fetchone()
        if not candidate:
            return jsonify({'error': 'Candidate not found'}), 404
        
        first_name, last_name, address, city = candidate
    finally:
        cur.close()
        release_db_connection(conn)
    
    # Parse first name - split off middle name if present
    first_parts = first_name.split() if first_name else []
    search_first = first_parts[0] if first_parts else ''
    search_middle = first_parts[1] if len(first_parts) > 1 else None
    
    # Parse last name - split off suffix if present
    suffixes = ['JR', 'SR', 'II', 'III', 'IV', 'V']
    last_parts = last_name.split() if last_name else []
    search_suffix = None
    search_last = last_name
    
    if len(last_parts) > 1 and last_parts[-1].upper().replace('.', '') in suffixes:
        search_suffix = last_parts[-1].upper().replace('.', '')
        search_last = ' '.join(last_parts[:-1])
    
    # Look up in voter database
    voter_conn = get_voter_db_connection()
    if not voter_conn:
        return jsonify({'error': 'Voter database not available'}), 503
    
    voter_cur = voter_conn.cursor()
    try:
        # Build list of name variations to check (Joe -> Joseph, Bill -> William, etc.)
        name_variants = {
            'JOE': ['JOSEPH'], 'JOSEPH': ['JOE'],
            'BILL': ['WILLIAM'], 'WILLIAM': ['BILL', 'WILL', 'WILLY'],
            'BOB': ['ROBERT'], 'ROBERT': ['BOB', 'ROB', 'BOBBY'],
            'TOM': ['THOMAS'], 'THOMAS': ['TOM', 'TOMMY'],
            'MIKE': ['MICHAEL'], 'MICHAEL': ['MIKE', 'MIKEY'],
            'JIM': ['JAMES'], 'JAMES': ['JIM', 'JIMMY', 'JAMIE'],
            'DICK': ['RICHARD'], 'RICHARD': ['DICK', 'RICK', 'RICH'],
            'TONY': ['ANTHONY'], 'ANTHONY': ['TONY'],
            'DAN': ['DANIEL'], 'DANIEL': ['DAN', 'DANNY'],
            'STEVE': ['STEVEN', 'STEPHEN'], 'STEVEN': ['STEVE'], 'STEPHEN': ['STEVE'],
            'CHRIS': ['CHRISTOPHER'], 'CHRISTOPHER': ['CHRIS'],
            'MATT': ['MATTHEW'], 'MATTHEW': ['MATT'],
            'NICK': ['NICHOLAS'], 'NICHOLAS': ['NICK'],
            'BETH': ['ELIZABETH'], 'ELIZABETH': ['BETH', 'LIZ', 'LIZZY'],
            'LIZ': ['ELIZABETH'],
            'KATE': ['KATHERINE', 'CATHERINE'], 'KATHERINE': ['KATE', 'KATHY', 'KATIE'],
            'CATHERINE': ['KATE', 'KATHY', 'CATHY'],
            'PAT': ['PATRICIA', 'PATRICK'], 'PATRICIA': ['PAT', 'PATTY'], 'PATRICK': ['PAT'],
            'ED': ['EDWARD', 'EDWIN'], 'EDWARD': ['ED', 'EDDIE', 'TED'],
            'DAVE': ['DAVID'], 'DAVID': ['DAVE'],
            'ALEX': ['ALEXANDER', 'ALEXANDRA'], 'ALEXANDER': ['ALEX'], 'ALEXANDRA': ['ALEX'],
            'SAM': ['SAMUEL', 'SAMANTHA'], 'SAMUEL': ['SAM'], 'SAMANTHA': ['SAM'],
            'CHARLIE': ['CHARLES'], 'CHARLES': ['CHARLIE', 'CHUCK'],
            'JERRY': ['GERALD', 'JEROME'], 'GERALD': ['JERRY'], 'JEROME': ['JERRY'],
            'LARRY': ['LAWRENCE'], 'LAWRENCE': ['LARRY'],
            'JENNY': ['JENNIFER'], 'JENNIFER': ['JENNY', 'JEN'],
            'MAGGIE': ['MARGARET'], 'MARGARET': ['MAGGIE', 'PEGGY', 'MEG'],
            'DEBBIE': ['DEBORAH'], 'DEBORAH': ['DEBBIE', 'DEB'],
            'SUE': ['SUSAN', 'SUZANNE'], 'SUSAN': ['SUE', 'SUSIE'], 'SUZANNE': ['SUE'],
            'VICKY': ['VICTORIA'], 'VICTORIA': ['VICKY', 'TORI'],
            'DOUG': ['DOUGLAS'], 'DOUGLAS': ['DOUG'],
            'GREG': ['GREGORY'], 'GREGORY': ['GREG'],
            'JEFF': ['JEFFREY', 'GEOFFREY'], 'JEFFREY': ['JEFF'], 'GEOFFREY': ['JEFF'],
            'RON': ['RONALD'], 'RONALD': ['RON', 'RONNIE'],
            'DON': ['DONALD'], 'DONALD': ['DON', 'DONNIE'],
            'KEN': ['KENNETH'], 'KENNETH': ['KEN', 'KENNY'],
            'FRED': ['FREDERICK'], 'FREDERICK': ['FRED', 'FREDDY'],
            'WALT': ['WALTER'], 'WALTER': ['WALT', 'WALLY'],
            'ANDY': ['ANDREW'], 'ANDREW': ['ANDY', 'DREW'],
            'PETE': ['PETER'], 'PETER': ['PETE'],
            'HANK': ['HENRY'], 'HENRY': ['HANK'],
            'JACK': ['JOHN', 'JACKSON'], 'JOHN': ['JACK', 'JOHNNY', 'JON'],
            'TED': ['THEODORE', 'EDWARD'], 'THEODORE': ['TED', 'TEDDY'],
            'RAY': ['RAYMOND'], 'RAYMOND': ['RAY'],
            'PHIL': ['PHILIP', 'PHILLIP'], 'PHILIP': ['PHIL'], 'PHILLIP': ['PHIL'],
            'MARTY': ['MARTIN'], 'MARTIN': ['MARTY'],
            'VINNY': ['VINCENT'], 'VINCENT': ['VINNY', 'VINCE'],
            'SANDY': ['SANDRA', 'ALEXANDER'], 'SANDRA': ['SANDY'],
            'BECKY': ['REBECCA'], 'REBECCA': ['BECKY', 'BECCA'],
            'CATHY': ['CATHERINE'], 'CINDY': ['CYNTHIA'], 'CYNTHIA': ['CINDY'],
            'NANCY': ['ANN', 'ANNE'], 'ANN': ['NANCY', 'ANNIE'], 'ANNE': ['NANCY', 'ANNIE'],
            'BARB': ['BARBARA'], 'BARBARA': ['BARB', 'BARBIE'],
            'RICK': ['RICHARD', 'FREDERICK'], 'ROB': ['ROBERT'],
            'BEN': ['BENJAMIN'], 'BENJAMIN': ['BEN', 'BENNY'],
            'JON': ['JONATHAN', 'JOHN'], 'JONATHAN': ['JON'],
            'NATE': ['NATHAN', 'NATHANIEL'], 'NATHAN': ['NATE'], 'NATHANIEL': ['NATE'],
            'ZACH': ['ZACHARY'], 'ZACHARY': ['ZACH', 'ZACK'],
            'JOSH': ['JOSHUA'], 'JOSHUA': ['JOSH'],
            'WILL': ['WILLIAM'], 'WILLY': ['WILLIAM'],
        }
        
        # Get variants for search
        search_first_upper = search_first.upper() if search_first else ''
        first_names_to_check = [search_first_upper]
        if search_first_upper in name_variants:
            first_names_to_check.extend(name_variants[search_first_upper])
        
        # Query with prioritized matching:
        # 0 = exact first name or variant match
        # 1 = first 3 letters match
        # 2 = first 2 letters match  
        # 3 = first letter matches
        # 4 = middle name matches
        voter_cur.execute("""
            SELECT id_voter, nm_first, nm_mid, nm_last, nm_suff, ad_num, ad_str1, ad_city, ad_zip5, ward, county, cd_party
            FROM statewidechecklist
            WHERE UPPER(nm_last) = UPPER(%s)
            ORDER BY 
                CASE 
                    WHEN UPPER(nm_first) = ANY(%s) THEN 0
                    WHEN UPPER(SUBSTRING(nm_first, 1, 3)) = UPPER(SUBSTRING(%s, 1, 3)) THEN 1
                    WHEN UPPER(SUBSTRING(nm_first, 1, 2)) = UPPER(SUBSTRING(%s, 1, 2)) THEN 2
                    WHEN UPPER(SUBSTRING(nm_first, 1, 1)) = UPPER(SUBSTRING(%s, 1, 1)) THEN 3
                    WHEN UPPER(nm_mid) = ANY(%s) OR UPPER(SUBSTRING(nm_mid, 1, 3)) = UPPER(SUBSTRING(%s, 1, 3)) THEN 4
                    ELSE 5
                END,
                nm_first
            LIMIT 500
        """, (search_last, first_names_to_check, search_first, search_first, search_first, first_names_to_check, search_first))
        
        voters = voter_cur.fetchall()
        results = []
        for v in voters:
            # Build display name with middle and suffix
            display_name = v[1]  # nm_first
            if v[2]:  # nm_mid
                display_name += f" {v[2]}"
            display_name += f" {v[3]}"  # nm_last
            if v[4]:  # nm_suff
                display_name += f" {v[4]}"
            
            results.append({
                'id_voter': v[0],
                'name': display_name,
                'address': f"{v[5] or ''} {v[6] or ''}".strip(),
                'city': v[7],
                'zip': v[8],
                'ward': v[9],
                'county': v[10],
                'party': v[11]
            })
        
        return jsonify({'voters': results})
    finally:
        voter_cur.close()
        release_voter_db_connection(voter_conn)

@app.route('/api/lookup_district', methods=['POST'])
@login_required  
@csrf.exempt
def lookup_district():
    """Look up district based on town and ward"""
    data = request.get_json()
    city = data.get('city', '').strip()
    ward = data.get('ward', '0').strip()
    
    if not city:
        return jsonify({'error': 'City is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Convert ward to int, default to 0
        try:
            ward_int = int(ward) if ward else 0
        except:
            ward_int = 0
        
        # Look up ALL districts for this town (base + floterial)
        cur.execute("""
            SELECT full_district_code, county_name, seat_count
            FROM districts
            WHERE UPPER(town) = UPPER(%s) AND (ward = %s OR ward = 0)
            ORDER BY full_district_code
        """, (city, ward_int))
        
        results = cur.fetchall()
        
        # If no match with ward, try without ward filter
        if not results:
            cur.execute("""
                SELECT full_district_code, county_name, seat_count
                FROM districts
                WHERE UPPER(town) = UPPER(%s)
                ORDER BY full_district_code
            """, (city,))
            results = cur.fetchall()
        
        # If still no match, try partial match
        if not results:
            cur.execute("""
                SELECT full_district_code, county_name, seat_count
                FROM districts
                WHERE UPPER(town) LIKE '%%' || UPPER(%s) || '%%'
                ORDER BY full_district_code
            """, (city,))
            results = cur.fetchall()
        
        if results:
            districts = []
            for r in results:
                districts.append({
                    'district': r[0],
                    'county': r[1],
                    'seats': r[2]
                })
            return jsonify({'districts': districts})
        else:
            return jsonify({'error': f'No district found for {city}'}), 404
    finally:
        cur.close()
        release_db_connection(conn)
        
@app.route('/api/search_candidates')
@login_required
@csrf.exempt
def search_candidates():
    """Search existing candidates by name"""
    first_name = request.args.get('first_name', '').strip()
    last_name = request.args.get('last_name', '').strip()
    
    if not last_name:
        return jsonify({'candidates': []})
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT c.candidate_id, c.first_name, c.last_name, c.party, c.city,
                   STRING_AGG(DISTINCT ces.district_code, ', ') as districts,
                   STRING_AGG(DISTINCT ces.election_year::text, ', ') as years
            FROM candidates c
            LEFT JOIN candidate_election_status ces ON c.candidate_id = ces.candidate_id
            WHERE UPPER(c.last_name) = UPPER(%s)
               OR UPPER(c.last_name) LIKE UPPER(%s) || '%%'
               OR UPPER(%s) LIKE UPPER(c.last_name) || '%%'
            GROUP BY c.candidate_id, c.first_name, c.last_name, c.party, c.city
            ORDER BY 
                CASE WHEN UPPER(c.first_name) = UPPER(%s) THEN 0
                     WHEN UPPER(c.first_name) LIKE UPPER(%s) || '%%' THEN 1
                     ELSE 2
                END,
                c.last_name, c.first_name
            LIMIT 20
        """, (last_name, last_name, last_name, first_name, first_name))
        
        candidates = cur.fetchall()
        results = []
        for c in candidates:
            results.append({
                'candidate_id': c[0],
                'first_name': c[1],
                'last_name': c[2],
                'party': c[3],
                'city': c[4],
                'districts': c[5] or 'None',
                'years': c[6] or 'None'
            })
        
        return jsonify({'candidates': results})
    finally:
        cur.close()
        release_db_connection(conn)

@app.route('/api/update_candidate_district/<int:candidate_id>', methods=['POST'])
@login_required
@csrf.exempt
def update_candidate_district(candidate_id):
    """Update a candidate's district"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    new_district = data.get('district', '').strip() if data.get('district') else ''
    election_year = data.get('election_year', 2026)
    
    if not new_district:
        return jsonify({'error': 'District is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Verify district exists
        cur.execute("""
            SELECT full_district_code FROM districts 
            WHERE full_district_code = %s
            LIMIT 1
        """, (new_district,))
        if not cur.fetchone():
            return jsonify({'error': 'Invalid district'}), 400
        
        # Update the candidate's district
        cur.execute("""
            UPDATE candidate_election_status
            SET district_code = %s
            WHERE candidate_id = %s AND election_year = %s
        """, (new_district, candidate_id, election_year))
        
        conn.commit()
        return jsonify({'success': True, 'district': new_district})
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating district: {e}")
        return jsonify({'error': 'Failed to update district'}), 500
    finally:
        cur.close()
        release_db_connection(conn)

@app.route('/api/districts')
@login_required
@csrf.exempt
def get_districts():
    """Get all districts for dropdown"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT DISTINCT full_district_code 
            FROM districts 
            ORDER BY full_district_code
        """)
        districts = [row[0] for row in cur.fetchall()]
        return jsonify({'districts': districts})
    finally:
        cur.close()
        release_db_connection(conn)


@app.route('/api/sync_from_voter/<int:candidate_id>', methods=['POST'])
@login_required
@csrf.exempt
def sync_from_voter(candidate_id):
    """Sync candidate info from voter file"""
    data = request.get_json()
    voter_id = data.get('voter_id')
    
    if not voter_id:
        return jsonify({'error': 'Voter ID required'}), 400
    
    # Get voter info
    voter_conn = get_voter_db_connection()
    if not voter_conn:
        return jsonify({'error': 'Voter database not available'}), 503
    
    voter_cur = voter_conn.cursor()
    try:
        voter_cur.execute("""
            SELECT nm_first, nm_last, ad_num, ad_str1, ad_city, ad_zip5, ward, county, cd_party
            FROM statewidechecklist
            WHERE id_voter = %s
        """, (voter_id,))
        voter = voter_cur.fetchone()
        
        if not voter:
            return jsonify({'error': 'Voter not found'}), 404
        
        nm_first, nm_last, ad_num, ad_str1, ad_city, ad_zip5, ward, county, cd_party = voter
        address = f"{ad_num or ''} {ad_str1 or ''}".strip()
        
    finally:
        voter_cur.close()
        release_voter_db_connection(voter_conn)
    
    # Update candidate
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE candidates
            SET address = %s, city = %s, zip = %s, voter_id = %s
            WHERE candidate_id = %s
        """, (address, ad_city, ad_zip5, voter_id, candidate_id))
        conn.commit()
        log_activity('voter_matched', f"Matched to voter ID {voter_id}", candidate_id)
        
        return jsonify({
            'success': True,
            'address': address,
            'city': ad_city,
            'zip': ad_zip5,
            'ward': ward,
            'county': county,
            'voter_id': voter_id,
            'party': cd_party
        })
    except Exception as e:
        conn.rollback()
        logger.error(f"Error syncing from voter: {e}")
        return jsonify({'error': 'Failed to sync'}), 500
    finally:
        cur.close()
        release_db_connection(conn)


# Health check endpoint
@app.route('/health')
@csrf.exempt
def health():
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=os.getenv("DEBUG", "false").lower() == "true")