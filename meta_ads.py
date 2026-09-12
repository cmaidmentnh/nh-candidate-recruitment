"""
Meta (Facebook / Instagram) ads: our own ad accounts, read from the Marketing API.

Ported from the Goffstown CRM (src/lib/meta.ts). One page at /meta shows every connected ad
account together: spend, impressions, people reached and clicks over 7/30/90 days, a daily
spend chart, a campaign table, and a gallery of the ads themselves with their pictures and
words. Active accounts are synced by cron; Sync on the page does one at once.

Two ways to connect an account:
  1. The shared key. One access token serves every account it can reach and the account row
     stores a marker instead of a token. It is looked for at runtime, in this order: the
     process environment (META_ADS_TOKEN, or the Goffstown name META_API_KEY), a sibling app's
     .env (META_TOKEN_ENV_FILE), and last a token pasted on /meta/settings and kept encrypted
     in meta_settings. Environment first, on purpose: the key in the server's .env is the
     permanent system-user token, and a pasted user token is a stand-in for when nobody who
     holds the token can reach the box. This repo is public: no token value may ever land in it.
  2. A token pasted for one account, encrypted the same way. It is never shown again.

Encryption is AES-256-GCM under ENCRYPTION_KEY, or under a key derived from the app's own
SECRET_KEY when ENCRYPTION_KEY is not set - the same secret the session cookie already trusts.

The tables are created at start-up if missing (migrations/031 and 032 are idempotent), and an
in-process thread runs the hourly sync under a Postgres advisory lock, so a fresh deploy needs
neither psql nor a crontab. The cron endpoints and CLI commands still work for hosts that
prefer them.

Access is a private feature ('meta_ads'), granted on Manage Access like the battle plan. The
settings page is narrower: the super admin plus META_SETTINGS_EDITORS.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from psycopg2.extras import RealDictCursor, execute_values

import private_features
from private_features import require_feature_access

logger = logging.getLogger(__name__)

meta_bp = Blueprint('meta', __name__, url_prefix='/meta')

# Set by init_meta_ads()
get_db_connection = None
release_db_connection = None
_fallback_key = None

FEATURE = 'meta_ads'

# Who may open /meta/settings and paste a token. The super admin always can.
SETTINGS_EDITORS = {e.strip().lower() for e in (os.environ.get('META_SETTINGS_EDITORS') or 'berryrm0@gmail.com').split(',') if e.strip()}

MIGRATIONS = ('031_meta_ads.sql', '032_meta_settings.sql')


def init_meta_ads(db_conn_func, db_release_func, secret_key=None):
    """Wires the database, derives the fallback encryption key from the app secret, and makes
    sure the tables exist. Table creation failing (no database yet, say) is logged, not fatal:
    the pages will say what is wrong when they are opened."""
    global get_db_connection, release_db_connection, _fallback_key
    get_db_connection = db_conn_func
    release_db_connection = db_release_func
    if secret_key:
        raw = secret_key if isinstance(secret_key, bytes) else str(secret_key).encode('utf-8')
        _fallback_key = hashlib.sha256(b'meta-settings:' + raw).digest()
    try:
        ensure_tables()
    except Exception as e:
        logger.error(f'[meta] could not create tables at start-up: {e}')


def ensure_tables():
    """Runs the Meta migrations. Every statement in them is IF NOT EXISTS, so this is safe on
    every start and is how a host without psql access gets its tables."""
    here = os.path.dirname(os.path.abspath(__file__))
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        for name in MIGRATIONS:
            with open(os.path.join(here, 'migrations', name), encoding='utf-8') as f:
                cur.execute(f.read())
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def meta_access_required(f):
    """The 'meta_ads' private feature, or a settings editor: whoever is trusted to paste the
    token is trusted to see what it fetches. The gate is a thin wrapper around the feature
    decorator so Manage Access keeps working the same as for every other private page."""
    from functools import wraps
    gated = require_feature_access(FEATURE)(f)

    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.is_authenticated and can_edit_settings():
            return f(*args, **kwargs)
        return gated(*args, **kwargs)
    return decorated


# =============================================================================
# DATES
# =============================================================================

GRAPH_VERSION = 'v21.0'
GRAPH = f'https://graph.facebook.com/{GRAPH_VERSION}'

# Meta stamps every insight row with the date in the ad account's own timezone, and these
# accounts are all New Hampshire, so Eastern. The server runs in UTC, where the day flips at
# 8pm Eastern - reading "today" off the server clock would drop the oldest day out of every
# window and add an empty future day between 8pm and midnight.
REPORT_TZ = ZoneInfo('America/New_York')


def report_today(now=None):
    """Today's date in the reporting timezone."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(REPORT_TZ).date()


def iso_days_ago(n, now=None):
    """N days back from today in the reporting timezone, as YYYY-MM-DD."""
    return (report_today(now) - timedelta(days=n)).isoformat()


def _report_window():
    """30 days ending TODAY.

    Meta's own "last_30d" preset stops at yesterday, so a campaign that starts this morning
    reads as "$0.00, not delivering" until tomorrow. An explicit range ending today is the
    only way to see the money going out right now.
    """
    return {'time_range': json.dumps({'since': iso_days_ago(29), 'until': report_today().isoformat()})}


# =============================================================================
# TOKENS AND ENCRYPTION
# =============================================================================

# Accounts read with the key on the server store this instead of a token.
SERVER_TOKEN_MARKER = 'env:server-key'

# Where the server key is looked for, in order. META_ADS_TOKEN is the name Chris uses on the
# server; META_API_KEY is the Goffstown name and stays as an alias.
SERVER_TOKEN_VARS = ('META_ADS_TOKEN', 'META_API_KEY')

# A sibling app's .env, read at runtime when neither variable is in this process's
# environment. The system-user token already lives there, and copying its value into this
# app's config would be one more place for it to leak from.
SHARED_ENV_FILE = (os.environ.get('META_TOKEN_ENV_FILE') or '/opt/nh-civic-crm/.env').strip()


def _read_env_file(path, key):
    """One KEY=VALUE from a dotenv-style file, or None. Never raises: a missing or unreadable
    file just means the key is not there."""
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                if k.strip() == key:
                    v = v.strip()
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                        v = v[1:-1]
                    return v or None
    except OSError:
        return None
    return None


SETTINGS_PAGE_SOURCE = 'the Meta settings page'


def server_token_source():
    """Where the shared key came from, so the page can say where to change it."""
    for name in SERVER_TOKEN_VARS:
        if (os.environ.get(name) or '').strip():
            return name
    if _read_env_file(SHARED_ENV_FILE, 'META_ADS_TOKEN'):
        return f'META_ADS_TOKEN in {SHARED_ENV_FILE}'
    if stored_setting('META_ADS_TOKEN'):
        return SETTINGS_PAGE_SOURCE
    return None


def server_token():
    for name in SERVER_TOKEN_VARS:
        t = (os.environ.get(name) or '').strip()
        if t:
            return t
    return _read_env_file(SHARED_ENV_FILE, 'META_ADS_TOKEN') or stored_setting('META_ADS_TOKEN')


def _token_shape_problem(t, name):
    """Why a value cannot be a Meta access token, or None when it looks fine.

    Real tokens start with "EAA". App IDs (all digits) and App Secrets (32 hex chars) get
    pasted by mistake and then fail at request time with an unhelpful "cannot parse access
    token", so the shape is checked up front.
    """
    if not t:
        return f'{name} is not set on this environment.'
    if re.fullmatch(r'\d+', t):
        return f'{name} looks like an App ID (all digits), not an access token.'
    if re.fullmatch(r'[a-fA-F0-9]{32}', t):
        return f'{name} looks like an App Secret, not an access token.'
    if not t.startswith('EAA'):
        return f'{name} does not look like a Meta access token. Real ones start with "EAA".'
    if len(t) <= 40:
        return f'{name} is too short to be a Meta access token.'
    return None


def server_token_problem():
    t = server_token()
    if not t:
        return ('No shared key is set. Paste one on the Meta settings page, or put META_ADS_TOKEN in this '
                f'environment or in {SHARED_ENV_FILE}.')
    return _token_shape_problem(t, server_token_source())


def has_server_token():
    return server_token_problem() is None


def _encryption_key():
    """32 bytes from ENCRYPTION_KEY (64 hex chars); otherwise the key derived from the app's
    SECRET_KEY at init; None only when neither exists."""
    raw = (os.environ.get('ENCRYPTION_KEY') or '').strip()
    if re.fullmatch(r'[0-9a-fA-F]{64}', raw):
        return bytes.fromhex(raw)
    return _fallback_key


def encryption_problem():
    if _encryption_key() is None:
        return 'Neither ENCRYPTION_KEY nor SECRET_KEY is set, so there is no key to encrypt a pasted token with.'
    return None


def encryption_source():
    raw = (os.environ.get('ENCRYPTION_KEY') or '').strip()
    if re.fullmatch(r'[0-9a-fA-F]{64}', raw):
        return 'ENCRYPTION_KEY'
    return 'a key derived from SECRET_KEY' if _fallback_key else None


def encrypt(plain):
    """AES-256-GCM. Output: base64(iv).base64(tag).base64(ciphertext), same shape as Goffstown."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _encryption_key()
    if key is None:
        raise ValueError(encryption_problem())
    iv = secrets.token_bytes(12)
    sealed = AESGCM(key).encrypt(iv, plain.encode('utf-8'), None)
    ct, tag = sealed[:-16], sealed[-16:]
    return '.'.join(base64.b64encode(b).decode('ascii') for b in (iv, tag, ct))


def decrypt(blob):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _encryption_key()
    if key is None:
        raise ValueError(encryption_problem())
    parts = blob.split('.')
    if len(parts) != 3:
        raise ValueError('Stored value is not in the expected encrypted format.')
    iv, tag, ct = (base64.b64decode(p) for p in parts)
    if len(iv) != 12 or len(tag) != 16:
        raise ValueError('Stored value is not in the expected encrypted format.')
    return AESGCM(key).decrypt(iv, ct + tag, None).decode('utf-8')


def token_for(row):
    """The access token for an ad account row. Never log or return this to the client."""
    if row['access_token_enc'] == SERVER_TOKEN_MARKER:
        t = server_token()
        if not t:
            raise MetaApiError('This account uses the server key, but ' + (server_token_problem() or 'it is not usable.'))
        return t
    return decrypt(row['access_token_enc'])


# ---------- tokens pasted in the browser ----------

SETTING_KEYS = ('META_ADS_TOKEN', 'META_AD_LIBRARY_TOKEN')

# Decrypted values are cached per process for a minute, because server_token() is asked several
# times per page. Saving or clearing drops the cache in this worker; the others catch up within
# the minute, which is fine for a value that changes a few times a year.
SETTINGS_TTL = 60
_settings = {'at': 0.0, 'rows': {}}
_settings_lock = threading.Lock()


def _load_settings(force=False):
    with _settings_lock:
        if not force and time.time() - _settings['at'] < SETTINGS_TTL:
            return _settings['rows']
        rows = {}
        try:
            conn = get_db_connection()
            try:
                cur = _cursor(conn)
                cur.execute("SELECT key, value_enc, hint, updated_by, updated_at FROM meta_settings")
                for r in cur.fetchall():
                    try:
                        r['value'] = decrypt(r['value_enc'])
                    except Exception as e:
                        # A key rotated under us. The row stays so the page can say so; the
                        # value is simply unusable until it is pasted again.
                        logger.warning(f'[meta] cannot decrypt setting {r["key"]}: {e}')
                        r['value'] = None
                    rows[r['key']] = r
            finally:
                release_db_connection(conn)
        except Exception as e:
            # No table yet, or no database: the environment chain still works without us.
            logger.debug(f'[meta] settings not readable: {e}')
        _settings['rows'] = rows
        _settings['at'] = time.time()
        return rows


def stored_setting(key):
    """The pasted value for a key, or None. Never log or return this to the client."""
    row = _load_settings().get(key)
    return (row or {}).get('value') or None


def stored_setting_info(key):
    """Who pasted it and when, plus the last four characters, for the settings page. Safe to show."""
    row = _load_settings().get(key)
    if not row:
        return None
    return {'hint': row.get('hint'), 'updated_by': row.get('updated_by'), 'updated_at': row.get('updated_at'),
            'unreadable': row.get('value') is None}


def save_setting(key, value, who):
    if key not in SETTING_KEYS:
        raise ValueError('Unknown setting.')
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO meta_settings (key, value_enc, hint, updated_by, updated_at)
                       VALUES (%s, %s, %s, %s, now())
                       ON CONFLICT (key) DO UPDATE SET value_enc = EXCLUDED.value_enc, hint = EXCLUDED.hint,
                           updated_by = EXCLUDED.updated_by, updated_at = now()""",
                    (key, encrypt(value), value[-4:], who))
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)
    _load_settings(force=True)


def clear_setting(key):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM meta_settings WHERE key = %s", (key,))
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)
    _load_settings(force=True)


# =============================================================================
# GRAPH API CLIENT
# =============================================================================

REQUEST_TIMEOUT = 25


class MetaApiError(Exception):
    def __init__(self, message, code=None, subcode=None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode


def _read_json(res):
    try:
        body = res.json() if res.text else None
    except ValueError:
        raise MetaApiError(f'Meta returned a non-JSON response (HTTP {res.status_code}).')
    err = (body or {}).get('error') if isinstance(body, dict) else None
    if not res.ok or err:
        err = err or {}
        # Meta's `message` is frequently a bare "Permissions error". The sentence a person can
        # act on, link and all, sits in error_user_title/error_user_msg instead.
        base = err.get('message') or f'Meta request failed (HTTP {res.status_code}).'
        detail = ': '.join(p for p in (err.get('error_user_title'), err.get('error_user_msg')) if p)
        raise MetaApiError(f'{detail} ({base})' if detail else base, err.get('code'), err.get('error_subcode'))
    return body


def _fetch(url, params=None):
    """GET with the token in the query string. Transport errors are replaced with a fixed
    sentence: the URL carries access_token=..., and these messages end up on the page."""
    try:
        return requests.get(url, params=params, headers={'Accept': 'application/json'}, timeout=REQUEST_TIMEOUT)
    except requests.Timeout:
        raise MetaApiError(f'Meta did not answer within {REQUEST_TIMEOUT} seconds.')
    except requests.RequestException:
        raise MetaApiError('Could not reach Meta (network error).')


def meta_get(path, token, params=None):
    p = {k: v for k, v in (params or {}).items() if v is not None}
    p['access_token'] = token
    return _read_json(_fetch(f"{GRAPH}/{path.lstrip('/')}", p))


def meta_get_all_paged(path, token, params=None, max_pages=50):
    """Follows paging.next until done. `truncated` says the page cap was hit, so the caller
    must not treat "missing" as "deleted"."""
    out = []
    page = meta_get(path, token, params) or {}
    out.extend(page.get('data') or [])
    n = 1
    while (page.get('paging') or {}).get('next') and n < max_pages:
        page = _read_json(_fetch(page['paging']['next'])) or {}
        out.extend(page.get('data') or [])
        n += 1
    return out, bool((page.get('paging') or {}).get('next'))


def meta_get_all(path, token, params=None, max_pages=50):
    return meta_get_all_paged(path, token, params, max_pages)[0]


def normalize_account_id(raw):
    """Accepts "act_123", "123", or " act_123 " and returns "act_123"."""
    s = (raw or '').strip().lower()
    digits = s[4:] if s.startswith('act_') else s
    if not re.fullmatch(r'\d{3,25}', digits):
        raise ValueError('Ad account id must be numbers only, like 1234567890 or act_1234567890.')
    return f'act_{digits}'


def discover_ad_accounts(token):
    """The ad accounts a token can reach, so ids never have to be typed by hand."""
    rows = meta_get_all('me/adaccounts', token, {'fields': 'id,name,currency,account_status', 'limit': 100}, 5)
    return [{'id': r.get('id'), 'name': r.get('name') or r.get('id'),
             'currency': r.get('currency'), 'status': r.get('account_status')} for r in rows if r.get('id')]


ACCOUNT_STATUS = {1: 'Active', 2: 'Disabled', 3: 'Unsettled', 7: 'Pending risk review',
                  8: 'Pending settlement', 9: 'In grace period', 100: 'Pending closure', 101: 'Closed'}


def test_account(account_id, token):
    return meta_get(normalize_account_id(account_id), token, {'fields': 'name,currency,account_status,amount_spent'})


def debug_token(token):
    """What a token actually is. User tokens from the Graph API Explorer last about an hour,
    extended ones about 60 days, system-user tokens can be permanent - so the only honest
    way to warn before a sync starts failing is to read the expiry back.

    Returns {'ok', 'valid', 'expires_at', 'scopes'}. ok=False means Meta would not answer,
    and expires_at=None then means "not known", NOT "never expires".
    """
    try:
        res = meta_get('debug_token', token, {'input_token': token})
        d = (res or {}).get('data') or {}
        exp = d.get('expires_at')
        return {'ok': True, 'valid': d.get('is_valid', True), 'scopes': d.get('scopes') or [],
                'expires_at': datetime.fromtimestamp(exp, tz=timezone.utc) if exp and exp > 0 else None}
    except MetaApiError:
        return {'ok': False, 'valid': True, 'scopes': [], 'expires_at': None}


def format_token_expiry(dt):
    """Eastern, with the clock time: "Sep 6, 2026, 3:00 PM EDT"."""
    local = dt.astimezone(REPORT_TZ)
    clock = local.strftime('%I:%M %p').lstrip('0')
    return f'{local.strftime("%b")} {local.day}, {local.year}, {clock} {local.tzname()}'


def describe_token_expiry(dt, now=None):
    """One sentence about a token's remaining life. Anything under two days is said in hours
    with the exact moment, because "expires Sep 6" on a token dying at 3pm reads like a whole
    day of headroom."""
    if not dt:
        return 'It does not expire.'
    now = now or datetime.now(timezone.utc)
    stamp = format_token_expiry(dt)
    secs = (dt - now).total_seconds()
    if secs <= 0:
        return f'It has already expired ({stamp}) - paste a new one.'
    if secs < 3600:
        return f'It expires in under an hour ({stamp}).'
    hours = int(secs // 3600)
    if hours < 48:
        return f'It expires in {hours} hour{"" if hours == 1 else "s"} ({stamp}).'
    days = hours // 24
    return f'It expires in {days} days ({stamp}).'


# =============================================================================
# SYNC
# =============================================================================

CAMPAIGN_FIELDS = 'id,name,status,effective_status,objective,daily_budget,lifetime_budget,budget_remaining'
INSIGHT_FIELDS = 'spend,impressions,reach,clicks,inline_link_clicks,cpm,cpc,ctr'
ADSET_FIELDS = ('id,name,status,effective_status,campaign_id,daily_budget,lifetime_budget,budget_remaining,'
                'start_time,end_time,optimization_goal,billing_event,bid_strategy')
# thumbnail_url comes back as a 64px square unless a size is asked for on the creative field
# itself; for a video ad that poster frame is the only still there is, so ask for a big one.
AD_FIELDS = ('id,name,status,effective_status,created_time,campaign_id,campaign{name},adset_id,adset{id,name},'
             'creative.thumbnail_width(1200).thumbnail_height(1200)'
             '{id,name,thumbnail_url,image_url,video_id,title,body,object_story_spec,asset_feed_spec}')
AD_INSIGHT_FIELDS = 'ad_id,spend,impressions,reach,clicks,inline_link_clicks,ctr,cpc,cpm,frequency'

# The windows the page can show. Reach is asked for once per window, per sync.
REACH_WINDOWS = (7, 30, 90)


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _to_money(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _to_rate(v):
    if v in (None, ''):
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _to_budget(v):
    """Meta reports budgets in cents while insights come in whole dollars. Absent stays None so
    "no budget set here" (campaign budget optimisation) differs from "budget of zero"."""
    if v in (None, ''):
        return None
    try:
        return round(float(v) / 100, 2)
    except (TypeError, ValueError):
        return None


def _to_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None


def _first_text(*values):
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _is_gone(a):
    """Deleting or archiving an ad in Ads Manager does not take it out of the API straight away."""
    s = (a.get('effective_status') or a.get('status') or '').strip().upper()
    return s in ('DELETED', 'ARCHIVED')


def read_creative(creative):
    """The picture, headline, body and link out of a creative. Meta puts those in different
    places depending on how the ad was built, so every field is optional. A strange shape
    gives nulls rather than raising, because one odd ad must not stop a sync."""
    empty = {'creative_id': None, 'thumbnail_url': None, 'image_url': None, 'video_id': None,
             'title': None, 'body': None, 'link_url': None}
    if not isinstance(creative, dict):
        return empty
    try:
        spec = creative.get('object_story_spec') or {}
        link = spec.get('link_data') or {}
        video = spec.get('video_data') or {}
        photo = spec.get('photo_data') or {}
        child = (link.get('child_attachments') or [{}])[0] or {}
        feed = creative.get('asset_feed_spec') or {}
        f_img = (feed.get('images') or [{}])[0] or {}
        f_vid = (feed.get('videos') or [{}])[0] or {}
        f_title = (feed.get('titles') or [{}])[0] or {}
        f_body = (feed.get('bodies') or [{}])[0] or {}
        f_desc = (feed.get('descriptions') or [{}])[0] or {}
        f_link = (feed.get('link_urls') or [{}])[0] or {}
        cta = ((video.get('call_to_action') or {}).get('value') or {})
        return {
            'creative_id': _first_text(creative.get('id')),
            'thumbnail_url': _first_text(creative.get('thumbnail_url'), f_vid.get('thumbnail_url')),
            'image_url': _first_text(creative.get('image_url'), link.get('image_url'), link.get('picture'),
                                     video.get('image_url'), photo.get('image_url'), photo.get('url'),
                                     child.get('picture'), f_img.get('url')),
            'video_id': _first_text(creative.get('video_id'), video.get('video_id'), f_vid.get('video_id')),
            'title': _first_text(creative.get('title'), video.get('title'), link.get('name'), child.get('name'),
                                 photo.get('caption'), f_title.get('text')),
            'body': _first_text(creative.get('body'), link.get('message'), video.get('message'),
                                link.get('description'), video.get('link_description'), child.get('description'),
                                f_body.get('text'), f_desc.get('text')),
            'link_url': _first_text(creative.get('link_url'), link.get('link'), cta.get('link'), child.get('link'),
                                    f_link.get('website_url'), f_link.get('display_url')),
        }
    except Exception:
        return empty


def _sync_ads(conn, row, token):
    """Every ad with its creative, the last 30 days of results per ad, and the budgets on its
    ad set. Ads Meta no longer returns are removed so the gallery never shows something gone."""
    act = row['account_id']
    ads, truncated = meta_get_all_paged(f'{act}/ads', token, {'fields': AD_FIELDS, 'limit': 200})
    # No time_increment: one rolled-up row per ad.
    stats = meta_get_all(f'{act}/insights', token, {'level': 'ad', 'fields': AD_INSIGHT_FIELDS, 'limit': 500,
                                                   **_report_window()})
    # The same window by day. Buys the last day each ad delivered, and whether it delivered
    # lately - Meta still calls an ad ACTIVE once its schedule or budget has run out.
    recent = meta_get_all(f'{act}/insights', token, {'level': 'ad', 'time_increment': 1,
                                                    'fields': AD_INSIGHT_FIELDS, 'limit': 500, **_report_window()})
    adsets = meta_get_all(f'{act}/adsets', token, {'fields': ADSET_FIELDS, 'limit': 200})
    campaigns = meta_get_all(f'{act}/campaigns', token, {'fields': CAMPAIGN_FIELDS, 'limit': 200})

    stats_by_ad = {s['ad_id']: s for s in stats if s.get('ad_id')}
    recent_cutoff = iso_days_ago(2)
    delivery = {}
    for s in recent:
        ad_id, day = s.get('ad_id'), s.get('date_start')
        if not ad_id or not day:
            continue
        imp = _to_int(s.get('impressions'))
        d = delivery.setdefault(ad_id, {'first': None, 'last': None, 'days': 0, 'spend': 0.0, 'imp': 0})
        if imp > 0:  # a zero-impression day is a day the ad did not run
            d['first'] = day if not d['first'] or day < d['first'] else d['first']
            d['last'] = day if not d['last'] or day > d['last'] else d['last']
            d['days'] += 1
        if day >= recent_cutoff:
            d['spend'] += _to_money(s.get('spend'))
            d['imp'] += imp

    campaign_by_id = {c['id']: c for c in campaigns if c.get('id')}
    now = datetime.now(timezone.utc)
    cur = conn.cursor()

    seen_sets = set()
    set_rows = []
    for s in adsets:
        if not s.get('id') or s['id'] in seen_sets:
            continue
        seen_sets.add(s['id'])
        c = campaign_by_id.get(s.get('campaign_id') or '', {})
        set_rows.append((row['id'], s['id'], _first_text(s.get('name')) or s['id'], _first_text(s.get('status')),
                         _first_text(s.get('effective_status')), _first_text(s.get('campaign_id')),
                         _to_budget(s.get('daily_budget')), _to_budget(s.get('lifetime_budget')),
                         _to_budget(s.get('budget_remaining')), _to_budget(c.get('daily_budget')),
                         _to_budget(c.get('lifetime_budget')), _to_budget(c.get('budget_remaining')),
                         _first_text(c.get('objective')), _to_dt(s.get('start_time')), _to_dt(s.get('end_time')),
                         _first_text(s.get('optimization_goal')), _first_text(s.get('billing_event')),
                         _first_text(s.get('bid_strategy')), now))
    if set_rows:
        execute_values(cur, """
            INSERT INTO meta_ad_sets (ad_account_id, adset_id, name, status, effective_status, campaign_id,
                daily_budget, lifetime_budget, budget_remaining, campaign_daily_budget, campaign_lifetime_budget,
                campaign_budget_remaining, campaign_objective, start_time, end_time, optimization_goal,
                billing_event, bid_strategy, synced_at)
            VALUES %s
            ON CONFLICT (ad_account_id, adset_id) DO UPDATE SET
                name = EXCLUDED.name, status = EXCLUDED.status, effective_status = EXCLUDED.effective_status,
                campaign_id = EXCLUDED.campaign_id, daily_budget = EXCLUDED.daily_budget,
                lifetime_budget = EXCLUDED.lifetime_budget, budget_remaining = EXCLUDED.budget_remaining,
                campaign_daily_budget = EXCLUDED.campaign_daily_budget,
                campaign_lifetime_budget = EXCLUDED.campaign_lifetime_budget,
                campaign_budget_remaining = EXCLUDED.campaign_budget_remaining,
                campaign_objective = EXCLUDED.campaign_objective, start_time = EXCLUDED.start_time,
                end_time = EXCLUDED.end_time, optimization_goal = EXCLUDED.optimization_goal,
                billing_event = EXCLUDED.billing_event, bid_strategy = EXCLUDED.bid_strategy,
                synced_at = EXCLUDED.synced_at
        """, set_rows, page_size=100)
        cur.execute("DELETE FROM meta_ad_sets WHERE ad_account_id = %s AND adset_id <> ALL(%s)",
                    (row['id'], list(seen_sets)))

    seen = set()
    ad_rows = []
    for a in ads:
        if not a.get('id') or a['id'] in seen or _is_gone(a):  # gone ads stay out of `seen` so the prune takes them
            continue
        seen.add(a['id'])
        c = read_creative(a.get('creative'))
        m = stats_by_ad.get(a['id'], {})
        d = delivery.get(a['id'], {})
        ad_rows.append((row['id'], a['id'], _first_text(a.get('name')) or a['id'], _first_text(a.get('status')),
                        _first_text(a.get('effective_status')), _first_text(a.get('campaign_id')),
                        _first_text((a.get('campaign') or {}).get('name')),
                        _first_text(a.get('adset_id'), (a.get('adset') or {}).get('id')),
                        _first_text((a.get('adset') or {}).get('name')),
                        c['creative_id'], c['thumbnail_url'], c['image_url'], c['video_id'], c['title'], c['body'],
                        c['link_url'], _to_money(m.get('spend')), _to_int(m.get('impressions')),
                        _to_int(m.get('reach')), _to_int(m.get('clicks')), _to_int(m.get('inline_link_clicks')),
                        round(d.get('spend', 0.0), 2), d.get('imp', 0), d.get('first'), d.get('last'),
                        d.get('days', 0), _to_rate(m.get('cpm')), _to_rate(m.get('cpc')), _to_rate(m.get('ctr')),
                        _to_rate(m.get('frequency')), _to_dt(a.get('created_time')), now))
    if ad_rows:
        execute_values(cur, """
            INSERT INTO meta_ads (ad_account_id, ad_id, name, status, effective_status, campaign_id, campaign_name,
                adset_id, adset_name, creative_id, thumbnail_url, image_url, video_id, title, body, link_url,
                spend, impressions, reach, clicks, link_clicks, recent_spend, recent_impressions,
                first_delivered_on, last_delivered_on, delivery_days, cpm, cpc, ctr, frequency, ad_created_at,
                synced_at)
            VALUES %s
            ON CONFLICT (ad_account_id, ad_id) DO UPDATE SET
                name = EXCLUDED.name, status = EXCLUDED.status, effective_status = EXCLUDED.effective_status,
                campaign_id = EXCLUDED.campaign_id, campaign_name = EXCLUDED.campaign_name,
                adset_id = EXCLUDED.adset_id, adset_name = EXCLUDED.adset_name, creative_id = EXCLUDED.creative_id,
                thumbnail_url = EXCLUDED.thumbnail_url, image_url = EXCLUDED.image_url,
                video_id = EXCLUDED.video_id, title = EXCLUDED.title, body = EXCLUDED.body,
                link_url = EXCLUDED.link_url, spend = EXCLUDED.spend, impressions = EXCLUDED.impressions,
                reach = EXCLUDED.reach, clicks = EXCLUDED.clicks, link_clicks = EXCLUDED.link_clicks,
                recent_spend = EXCLUDED.recent_spend, recent_impressions = EXCLUDED.recent_impressions,
                first_delivered_on = EXCLUDED.first_delivered_on, last_delivered_on = EXCLUDED.last_delivered_on,
                delivery_days = EXCLUDED.delivery_days, cpm = EXCLUDED.cpm, cpc = EXCLUDED.cpc,
                ctr = EXCLUDED.ctr, frequency = EXCLUDED.frequency, ad_created_at = EXCLUDED.ad_created_at,
                synced_at = EXCLUDED.synced_at
        """, ad_rows, page_size=100)

    # Drop ads no longer in the account, but only when the listing was complete: a truncated
    # listing means "not fetched", not "deleted at Meta".
    if not truncated:
        if seen:
            cur.execute("DELETE FROM meta_ads WHERE ad_account_id = %s AND ad_id <> ALL(%s)", (row['id'], list(seen)))
        else:
            cur.execute("DELETE FROM meta_ads WHERE ad_account_id = %s", (row['id'],))
    # Deleted and archived ads always go, however long the listing was.
    cur.execute("""DELETE FROM meta_ads WHERE ad_account_id = %s
                   AND upper(coalesce(effective_status, status, '')) IN ('DELETED', 'ARCHIVED')""", (row['id'],))
    cur.close()
    return len(ad_rows)


def _sync_reach(conn, row, token):
    """People reached in each window, account-wide and per campaign, straight from Meta. One
    request per level carries all three windows (time_ranges); the window each row belongs
    to is read back off its start date."""
    now = datetime.now(timezone.utc)
    until = report_today(now).isoformat()
    since_for = {iso_days_ago(d - 1, now): d for d in REACH_WINDOWS}
    time_ranges = json.dumps([{'since': s, 'until': until} for s in since_for])
    act = row['account_id']
    account = meta_get_all(f'{act}/insights', token, {'level': 'account', 'time_ranges': time_ranges,
                                                     'fields': 'reach,impressions', 'limit': 100})
    campaigns = meta_get_all(f'{act}/insights', token, {'level': 'campaign', 'time_ranges': time_ranges,
                                                       'fields': 'campaign_id,reach,impressions', 'limit': 500})
    by_key = {}
    for level, rows in (('account', account), ('campaign', campaigns)):
        for r in rows:
            days = since_for.get(r.get('date_start'))
            if not days:
                continue
            cid = (r.get('campaign_id') or '') if level == 'campaign' else ''
            if level == 'campaign' and not cid:
                continue
            by_key[(level, cid, days)] = (row['id'], level, cid, days, _to_int(r.get('reach')),
                                          _to_int(r.get('impressions')), now)
    if by_key:
        cur = conn.cursor()
        execute_values(cur, """
            INSERT INTO meta_reach (ad_account_id, level, campaign_id, days, reach, impressions, synced_at)
            VALUES %s
            ON CONFLICT (ad_account_id, level, campaign_id, days) DO UPDATE SET
                reach = EXCLUDED.reach, impressions = EXCLUDED.impressions, synced_at = EXCLUDED.synced_at
        """, list(by_key.values()), page_size=200)
        cur.close()
    return len(by_key)


def sync_account(row):
    """Pulls 30 days of account and campaign results, then the ads and the reach. The numbers
    are the point; ads and reach are a bonus that only record a warning when they fail.

    Returns {'id', 'account_id', 'name', 'ok', 'rows', 'ads', 'warning', 'error'}.
    """
    base = {'id': row['id'], 'account_id': row['account_id'], 'name': row['name']}
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE meta_ad_accounts SET last_attempt_at = now() WHERE id = %s", (row['id'],))
        conn.commit()
        cur.close()
        try:
            token = token_for(row)
            act = row['account_id']
            account_rows = meta_get_all(f'{act}/insights', token, {'level': 'account', 'time_increment': 1,
                                                                  'fields': INSIGHT_FIELDS, 'limit': 100,
                                                                  **_report_window()})
            campaign_rows = meta_get_all(f'{act}/insights', token, {'level': 'campaign', 'time_increment': 1,
                                                                   'fields': f'campaign_id,campaign_name,{INSIGHT_FIELDS}',
                                                                   'limit': 500, **_report_window()})
            campaigns = meta_get_all(f'{act}/campaigns', token, {'fields': CAMPAIGN_FIELDS, 'limit': 200})
        except (MetaApiError, ValueError) as e:
            cur = conn.cursor()
            cur.execute("UPDATE meta_ad_accounts SET last_sync_error = %s WHERE id = %s", (str(e)[:1000], row['id']))
            conn.commit()
            cur.close()
            return {**base, 'ok': False, 'rows': 0, 'error': str(e)}

        status_by_id = {c['id']: (c.get('effective_status') or c.get('status') or '') for c in campaigns if c.get('id')}
        now = datetime.now(timezone.utc)
        # Keyed like the unique index: Meta's cursor paging can hand back the same (campaign,
        # day) twice, and Postgres refuses an ON CONFLICT DO UPDATE that touches one row twice
        # in a single statement. Last row wins, exactly like the upsert would.
        by_key = {}

        def keep(level, cid, cname, r):
            if not r.get('date_start'):
                return
            by_key[(level, cid, r['date_start'])] = (
                row['id'], level, cid, cname, (status_by_id.get(cid) or None) if level == 'campaign' else None,
                r['date_start'], _to_money(r.get('spend')), _to_int(r.get('impressions')), _to_int(r.get('reach')),
                _to_int(r.get('clicks')), _to_int(r.get('inline_link_clicks')), _to_rate(r.get('cpm')),
                _to_rate(r.get('cpc')), _to_rate(r.get('ctr')), now)

        for r in account_rows:
            keep('account', '', None, r)
        for r in campaign_rows:
            if r.get('campaign_id'):
                keep('campaign', r['campaign_id'], r.get('campaign_name'), r)

        cur = conn.cursor()
        if by_key:
            execute_values(cur, """
                INSERT INTO meta_insights (ad_account_id, level, campaign_id, campaign_name, campaign_status, date,
                    spend, impressions, reach, clicks, link_clicks, cpm, cpc, ctr, fetched_at)
                VALUES %s
                ON CONFLICT (ad_account_id, level, campaign_id, date) DO UPDATE SET
                    campaign_name = EXCLUDED.campaign_name, campaign_status = EXCLUDED.campaign_status,
                    spend = EXCLUDED.spend, impressions = EXCLUDED.impressions, reach = EXCLUDED.reach,
                    clicks = EXCLUDED.clicks, link_clicks = EXCLUDED.link_clicks, cpm = EXCLUDED.cpm,
                    cpc = EXCLUDED.cpc, ctr = EXCLUDED.ctr, fetched_at = EXCLUDED.fetched_at
            """, list(by_key.values()), page_size=200)
        # Campaign statuses on rows outside the synced window (a 90-day view still shows them).
        ids_by_status = {}
        for cid, status in status_by_id.items():
            if status:
                ids_by_status.setdefault(status, []).append(cid)
        for status, ids in ids_by_status.items():
            cur.execute("UPDATE meta_insights SET campaign_status = %s WHERE ad_account_id = %s AND campaign_id = ANY(%s)",
                        (status, row['id'], ids))
        conn.commit()
        cur.close()

        ads = 0
        warning = None
        try:
            ads = _sync_ads(conn, row, token)
            conn.commit()
        except Exception as e:
            conn.rollback()
            warning = f'Numbers synced, but the ads did not: {e}'
            logger.warning(f'[meta] ads sync failed for {act}: {e}')
        try:
            _sync_reach(conn, row, token)
            conn.commit()
        except Exception as e:
            conn.rollback()
            why = f'people reached did not sync: {e}'
            warning = f'{warning} Also, {why}' if warning else f'Numbers synced, but {why}'
            logger.warning(f'[meta] reach sync failed for {act}: {why}')

        # A half-done sync must not look like a clean one: the numbers are real so
        # last_synced_at moves, but the warning stays on the row.
        cur = conn.cursor()
        cur.execute("UPDATE meta_ad_accounts SET last_synced_at = now(), last_sync_error = %s WHERE id = %s",
                    ((warning or '')[:1000] or None, row['id']))
        conn.commit()
        cur.close()
        return {**base, 'ok': True, 'rows': len(by_key), 'ads': ads, 'warning': warning}
    except Exception as e:
        conn.rollback()
        logger.exception(f'[meta] sync failed for {row["account_id"]}')
        cur = conn.cursor()
        cur.execute("UPDATE meta_ad_accounts SET last_sync_error = %s WHERE id = %s", (str(e)[:1000], row['id']))
        conn.commit()
        cur.close()
        return {**base, 'ok': False, 'rows': 0, 'error': str(e)}
    finally:
        release_db_connection(conn)


def _account_row(account_id):
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute("SELECT * FROM meta_ad_accounts WHERE id = %s", (account_id,))
        return cur.fetchone()
    finally:
        release_db_connection(conn)


def sync_all_active():
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute("SELECT * FROM meta_ad_accounts WHERE active ORDER BY name")
        rows = cur.fetchall()
    finally:
        release_db_connection(conn)
    results = [sync_account(r) for r in rows]
    return {'total': len(rows), 'synced': sum(1 for r in results if r['ok']),
            'failed': sum(1 for r in results if not r['ok']), 'results': results}


# =============================================================================
# QUERIES
# =============================================================================

def _f(v):
    return float(v) if v is not None else None


def list_accounts():
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute("""SELECT id, name, account_id, label, currency, active, token_expires_at, last_synced_at,
                              last_sync_error, created_at,
                              (access_token_enc = %s) AS uses_server_key
                       FROM meta_ad_accounts ORDER BY name""", (SERVER_TOKEN_MARKER,))
        rows = cur.fetchall()
    finally:
        release_db_connection(conn)
    now = datetime.now(timezone.utc)
    for r in rows:
        exp = r['token_expires_at']
        r['token_hours_left'] = int((exp - now).total_seconds() // 3600) if exp else None
    return rows


FALLBACK_COLORS = ['#d91720', '#1e3557', '#16a34a', '#d97706', '#7c3aed', '#0891b2', '#65a30d', '#db2777']


def _ratios(spend, impressions, clicks):
    return {'cpm': spend / impressions * 1000 if impressions > 0 else None,
            'cpc': spend / clicks if clicks > 0 else None,
            'ctr': clicks / impressions * 100 if impressions > 0 else None}


def get_overview(days):
    """Everything the top of the page needs for one window: totals, per account, a daily
    pivot for the chart, and the campaign table."""
    now = datetime.now(timezone.utc)
    since = iso_days_ago(days - 1, now)
    accounts = list_accounts()
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute("""SELECT ad_account_id, coalesce(sum(spend),0) AS spend, coalesce(sum(impressions),0) AS impressions,
                              coalesce(sum(reach),0) AS reach, coalesce(sum(clicks),0) AS clicks,
                              coalesce(sum(link_clicks),0) AS link_clicks
                       FROM meta_insights WHERE level = 'account' AND date >= %s GROUP BY ad_account_id""", (since,))
        agg_by_id = {r['ad_account_id']: r for r in cur.fetchall()}
        cur.execute("""SELECT date, ad_account_id, spend FROM meta_insights
                       WHERE level = 'account' AND date >= %s ORDER BY date""", (since,))
        daily_rows = cur.fetchall()
        cur.execute("""SELECT ad_account_id, campaign_id, max(campaign_name) AS campaign_name,
                              max(campaign_status) AS status, coalesce(sum(spend),0) AS spend,
                              coalesce(sum(impressions),0) AS impressions, coalesce(sum(reach),0) AS daily_reach,
                              coalesce(sum(clicks),0) AS clicks
                       FROM meta_insights WHERE level = 'campaign' AND date >= %s
                       GROUP BY ad_account_id, campaign_id ORDER BY sum(spend) DESC""", (since,))
        campaign_rows = cur.fetchall()
        # People counted once across the whole window. Only the synced windows exist here;
        # any other `days` falls back to the daily figures added up.
        true_reach = {}
        if days in REACH_WINDOWS:
            cur.execute("SELECT ad_account_id, level, campaign_id, reach FROM meta_reach WHERE days = %s", (days,))
            true_reach = {(r['level'], r['ad_account_id'], r['campaign_id']): r['reach'] for r in cur.fetchall()}
    finally:
        release_db_connection(conn)

    totals = {'spend': 0.0, 'impressions': 0, 'reach': 0, 'clicks': 0, 'link_clicks': 0}
    per_account = []
    for i, a in enumerate(accounts):
        agg = agg_by_id.get(a['id'], {})
        spend, imp, clicks = _to_money(agg.get('spend')), _to_int(agg.get('impressions')), _to_int(agg.get('clicks'))
        totals['spend'] += spend
        totals['impressions'] += imp
        totals['reach'] += true_reach.get(('account', a['id'], ''), _to_int(agg.get('reach')))
        totals['clicks'] += clicks
        totals['link_clicks'] += _to_int(agg.get('link_clicks'))
        per_account.append({'id': a['id'], 'name': a['name'], 'label': a['label'], 'active': a['active'],
                            'color': FALLBACK_COLORS[i % len(FALLBACK_COLORS)],
                            'spend': spend, 'impressions': imp, 'clicks': clicks})

    dates = [iso_days_ago(i, now) for i in range(days - 1, -1, -1)]
    by_date = {d: {'date': d, **{str(a['id']): 0.0 for a in accounts}} for d in dates}
    for r in daily_rows:
        rec = by_date.get(r['date'].isoformat())
        if rec is not None:
            rec[str(r['ad_account_id'])] = rec.get(str(r['ad_account_id']), 0.0) + _to_money(r['spend'])

    name_by_id = {a['id']: a['name'] for a in accounts}
    campaigns = []
    for r in campaign_rows:
        spend, imp, clicks = _to_money(r['spend']), _to_int(r['impressions']), _to_int(r['clicks'])
        reach = true_reach.get(('campaign', r['ad_account_id'], r['campaign_id']), _to_int(r['daily_reach']))
        campaigns.append({'account_id': r['ad_account_id'], 'account_name': name_by_id.get(r['ad_account_id'], 'Unknown'),
                          'campaign_id': r['campaign_id'], 'campaign_name': r['campaign_name'] or r['campaign_id'],
                          'status': r['status'], 'spend': spend, 'impressions': imp, 'reach': reach,
                          'frequency': imp / reach if reach > 0 else None, 'clicks': clicks,
                          **_ratios(spend, imp, clicks)})
    camp_totals = {'spend': sum(c['spend'] for c in campaigns), 'impressions': sum(c['impressions'] for c in campaigns),
                   'clicks': sum(c['clicks'] for c in campaigns)}
    camp_totals.update(_ratios(**camp_totals))

    return {'days': days, 'since': since, 'totals': {**totals, **_ratios(totals['spend'], totals['impressions'], totals['clicks'])},
            'per_account': per_account, 'daily': list(by_date.values()),
            'series': [{'key': str(a['id']), 'name': a['name'], 'color': a['color']} for a in per_account],
            'campaigns': campaigns, 'campaign_totals': camp_totals}


def list_ads(account_id=None, limit=200):
    """Every synced ad with its creative. Live ads first, in three tiers, because "is this
    running?" is the question the gallery is opened to answer:
        0 - switched on AND delivering in the last three days
        1 - switched on but not delivering: schedule finished, or budget ran out
        2 - paused, archived, everything else
    Within a tier, most recently delivered first."""
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute(f"""
            SELECT a.*, acc.account_id AS meta_account_id, acc.name AS account_name, acc.label AS account_label,
                   coalesce(s.daily_budget, s.campaign_daily_budget) AS daily_budget,
                   coalesce(s.lifetime_budget, s.campaign_lifetime_budget) AS lifetime_budget,
                   coalesce(s.budget_remaining, s.campaign_budget_remaining) AS budget_remaining,
                   s.end_time AS adset_end_time, s.campaign_objective
            FROM meta_ads a
            JOIN meta_ad_accounts acc ON acc.id = a.ad_account_id
            LEFT JOIN meta_ad_sets s ON s.ad_account_id = a.ad_account_id AND s.adset_id = a.adset_id
            {'WHERE a.ad_account_id = %s' if account_id else ''}
            ORDER BY CASE WHEN upper(coalesce(a.effective_status, a.status, '')) = 'ACTIVE' AND a.recent_impressions > 0 THEN 0
                          WHEN upper(coalesce(a.effective_status, a.status, '')) = 'ACTIVE' THEN 1 ELSE 2 END,
                     a.last_delivered_on DESC NULLS LAST, a.ad_created_at DESC, a.name
            LIMIT %s""", ((account_id, limit) if account_id else (limit,)))
        rows = cur.fetchall()
    finally:
        release_db_connection(conn)
    for r in rows:
        for k in ('spend', 'recent_spend', 'cpm', 'cpc', 'ctr', 'frequency', 'daily_budget', 'lifetime_budget', 'budget_remaining'):
            r[k] = _f(r[k])
        status = (r['effective_status'] or r['status'] or '').upper()
        r['delivering'] = status == 'ACTIVE' and (r['recent_impressions'] or 0) > 0
        r['status_label'] = ad_status_label(status, r['delivering'])
        r['ads_manager_url'] = ads_manager_url(r['meta_account_id'], r['ad_id'])
    return rows


# Meta keeps adding delivery states, so nothing is assumed: known ones get plain English,
# anything new is tidied up and shown as it comes.
STATUS_LABELS = {'ACTIVE': 'Active', 'PAUSED': 'Paused', 'ADSET_PAUSED': 'Ad set paused',
                 'CAMPAIGN_PAUSED': 'Campaign paused', 'ARCHIVED': 'Archived', 'DELETED': 'Deleted',
                 'PENDING_REVIEW': 'In review', 'PENDING_BILLING_INFO': 'Needs billing info',
                 'PREAPPROVED': 'Pre-approved', 'DISAPPROVED': 'Not approved', 'IN_PROCESS': 'Starting up',
                 'WITH_ISSUES': 'Has a problem'}


def ad_status_label(status, delivering):
    s = (status or '').strip().upper()
    if not s:
        return {'label': 'Unknown', 'kind': 'muted'}
    label = STATUS_LABELS.get(s) or (s[0] + s[1:].lower().replace('_', ' '))
    if s == 'ACTIVE':
        # Meta leaves an ad on ACTIVE once its schedule ends or its budget runs out; three days
        # with no impressions is the honest answer.
        return {'label': label, 'kind': 'live'} if delivering else {'label': 'Not delivering', 'kind': 'warn'}
    if 'PAUSED' in s:
        return {'label': label, 'kind': 'paused'}
    return {'label': label, 'kind': 'muted'}


def ads_manager_url(meta_account_id, ad_id):
    """Straight to one ad in Ads Manager, on the screen with the preview. Ads Manager wants
    the bare account number, not the stored "act_" form."""
    act = re.sub(r'^act_', '', (meta_account_id or '').strip(), flags=re.I)
    if not act or not (ad_id or '').strip():
        return None
    return f'https://adsmanager.facebook.com/adsmanager/manage/ads/edit?act={act}&selected_ad_ids={ad_id.strip()}'


def get_spend_summary(days=7):
    """Small summary for a dashboard tile: accounts connected and spend over the last N days."""
    conn = get_db_connection()
    try:
        cur = _cursor(conn)
        cur.execute("SELECT count(*) AS n FROM meta_ad_accounts")
        n = cur.fetchone()['n']
        if not n:
            return {'accounts': 0, 'spend': 0.0}
        cur.execute("SELECT coalesce(sum(spend),0) AS spend FROM meta_insights WHERE level='account' AND date >= %s",
                    (iso_days_ago(days - 1),))
        return {'accounts': n, 'spend': _to_money(cur.fetchone()['spend'])}
    finally:
        release_db_connection(conn)


# =============================================================================
# TEMPLATE FILTERS (shared with the ad monitor page)
# =============================================================================

@meta_bp.app_template_filter('money')
def money_filter(v):
    """Whole dollars: $1,234."""
    return f'${_to_money(v):,.0f}' if v is not None else '-'


@meta_bp.app_template_filter('money_exact')
def money_exact_filter(v):
    return f'${_to_money(v):,.2f}' if v is not None else '-'


@meta_bp.app_template_filter('num')
def num_filter(v):
    return f'{_to_int(v):,}' if v is not None else '-'


@meta_bp.app_template_filter('pct')
def pct_filter(v):
    return f'{float(v):.2f}%' if v is not None else '-'


@meta_bp.app_template_filter('ago')
def ago_filter(dt):
    if not dt:
        return 'never'
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return 'just now'
    if secs < 3600:
        return f'{secs // 60} min ago'
    if secs < 86400:
        h = secs // 3600
        return f'{h} hour{"" if h == 1 else "s"} ago'
    d = secs // 86400
    return f'{d} day{"" if d == 1 else "s"} ago'


@meta_bp.app_template_filter('fmt_date')
def fmt_date_filter(v, with_year=False):
    if not v:
        return '-'
    if isinstance(v, str):
        try:
            v = date.fromisoformat(v[:10])
        except ValueError:
            return v
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        v = v.astimezone(REPORT_TZ).date()
    s = f'{v.strftime("%b")} {v.day}'
    return f'{s}, {v.year}' if with_year else s


@meta_bp.app_template_filter('token_expiry')
def token_expiry_filter(dt):
    return format_token_expiry(dt) if dt else ''


# =============================================================================
# ROUTES
# =============================================================================

RANGES = (7, 30, 90)


def _who():
    return getattr(current_user, 'email', None) if current_user.is_authenticated else None


@meta_bp.route('/')
@meta_access_required
def page():
    try:
        days = int(request.args.get('days', 30))
    except ValueError:
        days = 30
    if days not in RANGES:
        days = 30
    accounts = list_accounts()
    overview = get_overview(days)
    ads = list_ads()
    live_ads = sum(1 for a in ads if a['delivering'])
    return render_template('meta/meta.html', accounts=accounts, overview=overview, ads=ads, live_ads=live_ads,
                           days=days, ranges=RANGES, has_server_key=has_server_token(),
                           server_key_problem=server_token_problem(), server_key_source=server_token_source(),
                           encryption_problem=encryption_problem(),
                           graph_version=GRAPH_VERSION, account_status=ACCOUNT_STATUS,
                           now_utc=datetime.now(timezone.utc), can_edit_settings=can_edit_settings())


@meta_bp.route('/accounts/discover', methods=['POST'])
@meta_access_required
def discover():
    """The ad accounts the server key can reach. JSON, for the Add account dialog."""
    if not has_server_token():
        return jsonify({'ok': False, 'error': server_token_problem()}), 400
    try:
        return jsonify({'ok': True, 'accounts': discover_ad_accounts(server_token())})
    except MetaApiError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@meta_bp.route('/accounts/test', methods=['POST'])
@meta_access_required
def test():
    """Tries a token against an account before anything is saved. JSON."""
    data = request.get_json(silent=True) or {}
    use_server = bool(data.get('use_server_key'))
    token = server_token() if use_server else (data.get('token') or '').strip()
    if use_server and not has_server_token():
        return jsonify({'ok': False, 'error': server_token_problem()}), 400
    problem = None if use_server else _token_shape_problem(token, 'That token')
    if problem:
        return jsonify({'ok': False, 'error': problem}), 400
    try:
        info = test_account(data.get('account_id') or '', token)
    except (MetaApiError, ValueError) as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'name': info.get('name'), 'currency': info.get('currency'),
                    'status': ACCOUNT_STATUS.get(info.get('account_status'), str(info.get('account_status') or '-')),
                    'amount_spent': _to_money(info.get('amount_spent')) / 100})


@meta_bp.route('/accounts', methods=['POST'])
@meta_access_required
def add_account():
    use_server = request.form.get('use_server_key') == '1'
    name = (request.form.get('name') or '').strip()[:160]
    label = (request.form.get('label') or '').strip()[:160] or None
    token = server_token() if use_server else (request.form.get('token') or '').strip()
    try:
        act = normalize_account_id(request.form.get('account_id') or '')
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('meta.page'))
    if use_server and not has_server_token():
        flash(server_token_problem(), 'danger')
        return redirect(url_for('meta.page'))
    if not use_server:
        problem = _token_shape_problem(token, 'That token') or encryption_problem()
        if problem:
            flash(problem, 'danger')
            return redirect(url_for('meta.page'))
    try:
        info = test_account(act, token)
    except MetaApiError as e:
        flash(f'Meta would not accept that: {e}', 'danger')
        return redirect(url_for('meta.page'))
    name = name or info.get('name') or act
    dbg = debug_token(token)
    if dbg['ok'] and not dbg['valid']:
        flash('Meta says that token is not valid.', 'danger')
        return redirect(url_for('meta.page'))
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO meta_ad_accounts (name, account_id, access_token_enc, label, currency,
                                                     token_expires_at, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (account_id) DO UPDATE SET
                           name = EXCLUDED.name, access_token_enc = EXCLUDED.access_token_enc,
                           label = EXCLUDED.label, currency = EXCLUDED.currency,
                           token_expires_at = EXCLUDED.token_expires_at, active = true, last_sync_error = NULL
                       RETURNING id""",
                    (name, act, SERVER_TOKEN_MARKER if use_server else encrypt(token), label,
                     info.get('currency') or 'USD', dbg['expires_at'], _who()))
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)
    result = sync_account(_account_row(new_id))
    if result['ok']:
        flash(f'{name} connected and synced. {describe_token_expiry(dbg["expires_at"]) if dbg["ok"] else ""}', 'success')
        if result.get('warning'):
            flash(result['warning'], 'warning')
    else:
        flash(f'{name} was saved, but the first sync failed: {result.get("error")}', 'warning')
    return redirect(url_for('meta.page'))


@meta_bp.route('/accounts/<int:account_id>/token', methods=['POST'])
@meta_access_required
def update_token(account_id):
    row = _account_row(account_id) or abort(404)
    use_server = request.form.get('use_server_key') == '1'
    token = server_token() if use_server else (request.form.get('token') or '').strip()
    problem = (server_token_problem() if use_server else (_token_shape_problem(token, 'That token') or encryption_problem()))
    if problem:
        flash(problem, 'danger')
        return redirect(url_for('meta.page'))
    try:
        test_account(row['account_id'], token)
    except MetaApiError as e:
        flash(f'Meta would not accept that token: {e}', 'danger')
        return redirect(url_for('meta.page'))
    dbg = debug_token(token)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # expires_at is only trusted when Meta answered; otherwise the old value stays.
        cur.execute("""UPDATE meta_ad_accounts SET access_token_enc = %s, last_sync_error = NULL,
                           token_expires_at = CASE WHEN %s THEN %s ELSE token_expires_at END
                       WHERE id = %s""",
                    (SERVER_TOKEN_MARKER if use_server else encrypt(token), dbg['ok'], dbg['expires_at'], account_id))
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)
    result = sync_account(_account_row(account_id))
    flash(f'Token updated for {row["name"]}. ' + (describe_token_expiry(dbg['expires_at']) if dbg['ok'] else ''),
          'success' if result['ok'] else 'warning')
    if not result['ok']:
        flash(f'The sync after it failed: {result.get("error")}', 'warning')
    return redirect(url_for('meta.page'))


@meta_bp.route('/accounts/<int:account_id>/active', methods=['POST'])
@meta_access_required
def set_active(account_id):
    active = request.form.get('active') == '1'
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE meta_ad_accounts SET active = %s WHERE id = %s", (active, account_id))
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)
    flash('Account resumed. It will sync on the next run.' if active else 'Account paused. It will not sync until resumed.', 'success')
    return redirect(url_for('meta.page'))


@meta_bp.route('/accounts/<int:account_id>/delete', methods=['POST'])
@meta_access_required
def delete_account(account_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM meta_ad_accounts WHERE id = %s RETURNING name", (account_id,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
    finally:
        release_db_connection(conn)
    flash(f'{row[0]} removed, with its synced results.' if row else 'That account was already gone.', 'success')
    return redirect(url_for('meta.page'))


@meta_bp.route('/accounts/<int:account_id>/sync', methods=['POST'])
@meta_access_required
def sync_one(account_id):
    row = _account_row(account_id) or abort(404)
    r = sync_account(row)
    if r['ok']:
        flash(f'{row["name"]} synced: {r["rows"]} daily rows, {r.get("ads", 0)} ads.', 'success')
        if r.get('warning'):
            flash(r['warning'], 'warning')
    else:
        flash(f'{row["name"]} did not sync: {r.get("error")}', 'danger')
    return redirect(url_for('meta.page'))


@meta_bp.route('/sync-all', methods=['POST'])
@meta_access_required
def sync_all():
    s = sync_all_active()
    if s['failed']:
        flash(f'{s["synced"]} of {s["total"]} accounts synced. Failed: '
              + '; '.join(f'{r["name"]}: {r.get("error")}' for r in s['results'] if not r['ok']), 'warning')
    else:
        flash(f'All {s["total"]} active accounts synced.', 'success')
    return redirect(url_for('meta.page'))


# ---------- settings page ----------

def can_edit_settings():
    """The super admin, plus the named editors. Deliberately narrower than the feature itself:
    this page is where a token gets pasted."""
    if not current_user.is_authenticated:
        return False
    try:
        if private_features.is_super_admin and private_features.is_super_admin():
            return True
    except Exception:
        pass
    email = (getattr(current_user, 'email', None) or '').lower()
    return bool(email) and email in SETTINGS_EDITORS


def settings_editor_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in.', 'warning')
            return redirect(url_for('login'))
        if not can_edit_settings():
            flash('You do not have access to that page.', 'danger')
            return redirect(url_for('meta.page'))
        return f(*args, **kwargs)
    return decorated


@meta_bp.app_context_processor
def inject_meta_settings_access():
    return {'can_edit_meta_settings': can_edit_settings()}


def _library_token_state():
    """What the Ad Library will use right now. Imported lazily: ad_monitor imports this module."""
    from ad_monitor import ad_library_token, ad_library_token_problem, ad_library_token_source
    return ad_library_token(), ad_library_token_source(), ad_library_token_problem()


def check_token(token, for_library=False):
    """One live look at a token so the page can say what it is and what it can reach. Nothing
    here writes anything. Returns plain values for the template."""
    out = {'valid': None, 'expires_at': None, 'expiry': None, 'scopes': [], 'user': None, 'accounts': None,
           'archive': None, 'error': None}
    if not token:
        return out
    dbg = debug_token(token)
    if dbg['ok']:
        out['valid'] = dbg['valid']
        out['expires_at'] = dbg['expires_at']
        out['expiry'] = describe_token_expiry(dbg['expires_at'])
        out['scopes'] = dbg['scopes']
    try:
        me = meta_get('me', token, {'fields': 'id,name'})
        out['user'] = me.get('name') or me.get('id')
    except MetaApiError as e:
        out['error'] = str(e)
        return out
    if for_library:
        from ad_monitor import CYCLE_START
        try:
            rows, _ = meta_get_all_paged('ads_archive', token, {
                'ad_reached_countries': json.dumps(['US']), 'ad_type': 'POLITICAL_AND_ISSUE_ADS',
                'ad_active_status': 'ALL', 'ad_delivery_date_min': CYCLE_START, 'search_terms': 'New Hampshire',
                'fields': 'id', 'limit': 5}, 1)
            out['archive'] = len(rows)
        except MetaApiError as e:
            out['error'] = f'The Ad Library refused it: {e}'
    else:
        try:
            out['accounts'] = discover_ad_accounts(token)
        except MetaApiError as e:
            out['error'] = f'It works, but cannot list ad accounts: {e}'
    return out


@meta_bp.route('/settings')
@settings_editor_required
def settings():
    ads_token = server_token()
    lib_token, lib_source, lib_problem = _library_token_state()
    ads = {'source': server_token_source(), 'problem': server_token_problem(), 'stored': stored_setting_info('META_ADS_TOKEN'),
           'check': check_token(ads_token) if ads_token and not server_token_problem() else None}
    lib = {'source': lib_source, 'problem': lib_problem, 'stored': stored_setting_info('META_AD_LIBRARY_TOKEN'),
           'check': check_token(lib_token, for_library=True) if lib_token and not lib_problem else None,
           'same_as_ads': bool(lib_token) and lib_token == ads_token}
    return render_template('meta/settings.html', ads=ads, lib=lib, encryption_problem=encryption_problem(),
                           encryption_source=encryption_source(), settings_page_source=SETTINGS_PAGE_SOURCE,
                           editors=sorted(SETTINGS_EDITORS), auto_sync=auto_sync_enabled())


@meta_bp.route('/settings', methods=['POST'])
@settings_editor_required
def settings_save():
    key = request.form.get('key')
    if key not in SETTING_KEYS:
        abort(400)
    label = 'Ad Library token' if key == 'META_AD_LIBRARY_TOKEN' else 'shared key'
    if request.form.get('clear') == '1':
        clear_setting(key)
        flash(f'The pasted {label} was removed. ' + ('The environment still provides one.' if
              (key == 'META_ADS_TOKEN' and server_token()) or (key != 'META_ADS_TOKEN' and _library_token_state()[0])
              else 'Nothing provides one now.'), 'success')
        return redirect(url_for('meta.settings'))
    token = (request.form.get('token') or '').strip()
    problem = _token_shape_problem(token, 'That') or encryption_problem()
    if problem:
        flash(problem, 'danger')
        return redirect(url_for('meta.settings'))
    check = check_token(token, for_library=(key == 'META_AD_LIBRARY_TOKEN'))
    if check['valid'] is False:
        flash('Meta says that token is not valid. Nothing was saved.', 'danger')
        return redirect(url_for('meta.settings'))
    if check['error'] and check['user'] is None:
        flash(f'Meta would not accept that token: {check["error"]} Nothing was saved.', 'danger')
        return redirect(url_for('meta.settings'))
    save_setting(key, token, _who())
    who = f' It belongs to {check["user"]}.' if check['user'] else ''
    flash(f'{label[0].upper()}{label[1:]} saved and encrypted.{who} {check["expiry"] or ""}', 'success')
    if check['error']:
        flash(check['error'], 'warning')
    # An env value still wins; say so rather than let a paste look like it did nothing.
    src = _library_token_state()[1] if key == 'META_AD_LIBRARY_TOKEN' else server_token_source()
    if src and src != SETTINGS_PAGE_SOURCE:
        flash(f'Note: {src} is set on the server and is used first. The pasted token is the fallback.', 'warning')
    return redirect(url_for('meta.settings'))


# ---------- in-process hourly sync ----------

# Any fixed 64-bit number works; it only has to be the same in every worker.
AUTO_SYNC_LOCK = 0x4D455441_53594E43   # "META" "SYNC"
AUTO_SYNC_EVERY_S = 10 * 60
AUTO_SYNC_STALE_MIN = 55
_auto_sync_thread = None


def auto_sync_enabled():
    return (os.environ.get('META_AUTO_SYNC') or '1').strip() not in ('0', 'false', 'no', 'off')


def start_auto_sync():
    """Starts one daemon thread per process. Gunicorn runs several workers, so the work itself
    is serialised through a Postgres advisory lock: whichever worker wakes first does the hour's
    sync, the rest see nothing due and go back to sleep."""
    global _auto_sync_thread
    if not auto_sync_enabled() or _auto_sync_thread is not None:
        return
    _auto_sync_thread = threading.Thread(target=_auto_sync_loop, name='meta-auto-sync', daemon=True)
    _auto_sync_thread.start()


def _auto_sync_loop():
    time.sleep(90)   # let the app finish starting, and stagger workers a little
    while True:
        try:
            auto_sync_once()
        except Exception:
            logger.exception('[meta] auto sync failed')
        time.sleep(AUTO_SYNC_EVERY_S)


def auto_sync_once():
    """Syncs whatever has not been attempted in the last 55 minutes, if the lock is free.
    Returns what it did, for tests and the CLI."""
    done = {'lock': False, 'accounts': None, 'watches': None}
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (AUTO_SYNC_LOCK,))
        if not cur.fetchone()[0]:
            return done
        done['lock'] = True
        try:
            cur.execute("""SELECT count(*) FROM meta_ad_accounts WHERE active
                           AND (last_attempt_at IS NULL OR last_attempt_at < now() - make_interval(mins => %s))""",
                        (AUTO_SYNC_STALE_MIN,))
            due_accounts = cur.fetchone()[0]
            cur.execute("""SELECT count(*) FROM ad_watch_pages WHERE active
                           AND (last_attempt_at IS NULL OR last_attempt_at < now() - make_interval(mins => %s))""",
                        (AUTO_SYNC_STALE_MIN,))
            due_watches = cur.fetchone()[0]
            conn.commit()
            if due_accounts and has_server_token():
                done['accounts'] = sync_all_active()
            if due_watches:
                from ad_monitor import has_ad_library_token, sync_all_watches
                if has_ad_library_token():
                    done['watches'] = sync_all_watches()
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (AUTO_SYNC_LOCK,))
            conn.commit()
            cur.close()
    finally:
        release_db_connection(conn)
    return done


# ---------- cron ----------

def cron_authorized():
    """`Authorization: Bearer <CRON_SECRET>`, compared in constant time. No secret configured
    means no way to authenticate: stay shut rather than open."""
    secret = (os.environ.get('CRON_SECRET') or '').strip()
    if not secret:
        return False
    header = request.headers.get('Authorization', '')
    token = header[7:].strip() if header.lower().startswith('bearer ') else ''
    return bool(token) and hmac.compare_digest(token.encode(), secret.encode())


@meta_bp.route('/cron/sync', methods=['GET', 'POST'])
def cron_sync():
    """Hourly sync of every active account. Called by cron with the bearer secret; exempt
    from CSRF in app.py because there is no session."""
    if not cron_authorized():
        return jsonify({'error': 'Unauthorized'}), 401
    started = datetime.now(timezone.utc)
    s = sync_all_active()
    return jsonify({'ok': s['failed'] == 0, 'at': started.isoformat(),
                    'ms': int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                    'total': s['total'], 'synced': s['synced'], 'failed': s['failed'],
                    'results': [{'account_id': r['account_id'], 'name': r['name'], 'ok': r['ok'],
                                 'rows': r['rows'], 'error': r.get('error')} for r in s['results']]})


def register_cli(app):
    @app.cli.command('meta-sync')
    def meta_sync_cmd():
        """Sync every active Meta ad account. For cron: `flask meta-sync`."""
        import click
        s = sync_all_active()
        for r in s['results']:
            click.echo(f'{"ok " if r["ok"] else "ERR"} {r["name"]} ({r["account_id"]}): '
                       f'{r["rows"]} rows, {r.get("ads", 0)} ads' + (f' - {r["error"]}' if r.get('error') else '')
                       + (f' - {r["warning"]}' if r.get('warning') else ''))
        click.echo(f'{s["synced"]} of {s["total"]} synced, {s["failed"]} failed.')
