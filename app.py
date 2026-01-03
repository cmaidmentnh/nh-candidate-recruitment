import os
import re
import json
import csv
from collections import defaultdict
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from psycopg2 import pool
import psycopg2
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import timedelta
import boto3
from botocore.client import Config
import logging

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-to-a-secure-random-string")
app.permanent_session_lifetime = timedelta(hours=72)
app.config['SESSION_PERMANENT'] = True

# Flask-Login Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

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
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not hasattr(current_user, 'role') or current_user.role != 'admin':
            flash("Admin access required.", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

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

def get_data_and_dashboard():
    conn = get_db_connection()
    cur = conn.cursor()
    TOTAL_SEATS = 400
    TOTAL_DISTRICTS = 203

    try:
        cur.execute("""
            SELECT counter, county_name, district_code, ward, town, seat_count, full_district_code
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
        counter, county_name, district_code, ward, town, seat_count, full_district_code = row
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
                "cand2024": []
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

    dashboard = {
        "confirmed": {"total": 0, "districts": []},
        "empty_seats": {"total": 0, "districts": []},
        "empty_districts": {"total": 0, "districts": []},
        "potentials": {"total": 0, "districts": []},
        "incumbents_running": {"total": 0, "districts": []},
        "incumbents_not_running": {"total": 0, "districts": []},
        "primaries": {"total": 0, "districts": []},
        "TOTAL_SEATS": TOTAL_SEATS,
        "TOTAL_DISTRICTS": TOTAL_DISTRICTS
    }

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
            if any(c["incumbent"] and c["status"].upper() != "DECLINED" for c in c2026):
                dashboard["incumbents_running"]["total"] += 1
                dashboard["incumbents_running"]["districts"].append((county_name, fdc))
            inc_cands = [c for c in c2026 if c["incumbent"]]
            if inc_cands and not any(c["incumbent"] and c["status"].upper() != "DECLINED" for c in inc_cands):
                dashboard["incumbents_not_running"]["total"] += 1
                dashboard["incumbents_not_running"]["districts"].append((county_name, fdc))
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

# Replace the index() function (around line 368-372) with this:

@app.route('/')
@login_required
def index():
    search_query = request.args.get('search', '').strip()
    county_groups, dashboard, county_stats = get_data_and_dashboard()
    
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
    
    return render_template("index.html", county_groups=county_groups, dashboard=dashboard, county_stats=county_stats, max=max, search_query=search_query)

@app.route('/filter')
@candidate_restricted
def filter_view():
    category = request.args.get("category", "").strip()
    county_groups, dashboard, county_stats = get_data_and_dashboard()
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
        try:
            cur.execute("""
                UPDATE candidates
                SET first_name=%s, last_name=%s, party=%s, incumbent=%s
                WHERE candidate_id=%s;
            """, (first_name, last_name, party, is_incumbent, candidate_id))
            cur.execute("""
                UPDATE candidate_election_status
                SET status=%s, is_running=%s
                WHERE candidate_id=%s AND election_year=%s;
            """, (status, is_running, candidate_id, election_year))
            conn.commit()
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
                       ces.status, ces.district_code
                FROM candidates c
                LEFT JOIN candidate_election_status ces
                  ON c.candidate_id=ces.candidate_id AND ces.election_year=%s
                WHERE c.candidate_id=%s;
            """, (election_year, candidate_id))
            row = cur.fetchone()
            if not row:
                flash("Candidate not found.", "warning")
                return redirect(url_for("index"))
            first_name, last_name, party, incumbent, status, district_code = row

            cur.execute("""
                SELECT comment_id, comment_text, added_by, added_at
                FROM comments
                WHERE candidate_id = %s
                ORDER BY added_at DESC
                LIMIT 5
            """, (candidate_id,))
            comments = cur.fetchall()

            # Get all districts for dropdown
            cur.execute("""
                SELECT DISTINCT full_district_code 
                FROM districts 
                ORDER BY full_district_code
            """)
            districts = [row[0] for row in cur.fetchall()]

        except Exception as e:
            logger.error(e)
            flash("Error loading candidate data.", "danger")
            comments = []
            districts = []
            district_code = None
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
                              districts=districts,
                              comments=comments)

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
def login():
    if request.method == 'POST':
        email = request.form.get('email').strip()
        password = request.form.get('password').strip()
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Check candidates table first
        cur.execute("""
            SELECT candidate_id, email, password_hash, first_name, last_name, password_changed, photo_url
            FROM candidates
            WHERE email = %s
        """, (email,))
        user_row = cur.fetchone()
        
        if user_row:
            candidate_id, email, password_hash, first_name, last_name, password_changed, photo_url = user_row
            if password_hash and check_password_hash(password_hash, password):
                user = CandidateUser(candidate_id, email, password_hash, first_name, last_name, password_changed, photo_url)
                login_user(user)
                session.permanent = True
                flash("Logged in successfully.", "success")
                cur.close()
                release_db_connection(conn)
                if not password_changed:
                    flash("Please change your password on first login.", "warning")
                    return redirect(url_for('change_password'))
                return redirect(url_for('index'))
        
        # Check admin users table
        cur.execute("""
            SELECT user_id, username, email, password_hash, role
            FROM users
            WHERE email = %s
        """, (email,))
        admin_row = cur.fetchone()
        cur.close()
        release_db_connection(conn)
        
        if admin_row and check_password_hash(admin_row[3], password):
            user = AdminUser(*admin_row)
            login_user(user)
            session.permanent = True
            flash("Logged in successfully.", "success")
            return redirect(url_for('index'))
        
        flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for('login'))

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
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT candidate_id, first_name, last_name, email, party
            FROM candidates
            WHERE email IS NOT NULL AND password_hash IS NOT NULL
        """)
        candidate_users = cur.fetchall()

        cur.execute("""
            SELECT user_id, username, email, role
            FROM users
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
    return render_template('admin_dashboard.html', candidate_users=candidate_users, admins=admins, tokens=tokens)

@app.route('/add_user', methods=['GET', 'POST'])
@admin_required
def add_user():
    if request.method == 'POST':
        user_type = request.form.get('user_type')
        email = request.form.get('email').strip()
        password = request.form.get('password').strip()
        confirm_password = request.form.get('confirm_password').strip()
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        party = request.form.get('party', '').strip()
        role = request.form.get('role', 'user').strip()

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for('add_user'))

        hashed_password = generate_password_hash(password)
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            if user_type == 'candidate':
                if not (first_name and last_name and party):
                    flash("First name, last name, and party are required for candidates.", "danger")
                    return redirect(url_for('add_user'))
                cur.execute("""
                    INSERT INTO candidates (first_name, last_name, party, email, password_hash, password_changed, created_by)
                    VALUES (%s, %s, %s, %s, %s, FALSE, %s)
                    RETURNING candidate_id
                """, (first_name, last_name, party, email, hashed_password, current_user.email))
                flash("Candidate added successfully.", "success")
            else:
                if not role:
                    flash("Role is required for admins.", "danger")
                    return redirect(url_for('add_user'))
                cur.execute("""
                    INSERT INTO users (username, email, password_hash, role, created_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    RETURNING user_id
                """, (email.split('@')[0], email, hashed_password, role))
                flash("Admin added successfully.", "success")
            conn.commit()
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
            
            return jsonify(candidate_dict)
        else:
            return jsonify({'error': 'Candidate not found'}), 404
    finally:
        cur.close()
        release_db_connection(conn)

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

# Health check endpoint
@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=os.getenv("DEBUG", "false").lower() == "true")
