"""
Cloud cost monitor: which Google Cloud VMs are running right now, and what they are costing.

Ross rents Windows GPU VMs in the Google Cloud project gcloudgpu to render After Effects. One
costs roughly $0.76 to $3.21 an hour, and one left on overnight is real money spent on nothing.
The page at /cost-monitor/ lists every VM in the project, how long each has been up in its
current run and what that run has cost so far. A check every ten minutes emails a warning when
a VM has been up too long, or when today's spend passes a line.

Two kinds of number, kept apart everywhere:

  1. ESTIMATES, worked out here from the live VM list: machine size, GPUs and Windows licence,
     times the list prices in RATES below. Every one is labelled "est.". A machine the table
     cannot price shows "rate unknown", never $0, and every total says how many it leaves out.
  2. ACTUAL spend, month to date, read from Google's billing export in BigQuery when
     GCP_BILLING_BQ_TABLE is set. That is the real bill, but it arrives hours late, which is
     why the estimates exist at all.

Read-only by default. The one thing this module can change is the Stop button, which does not
exist unless GCP_COST_ALLOW_STOP=1, and is offered only to cost admins, behind a confirm page.

Credentials are read at runtime from the environment and never written anywhere: this repo is
public. In order: GCP_ACCESS_TOKEN (a developer's `gcloud auth print-access-token`, good for an
hour, never for the server), GCP_COST_CREDENTIALS_JSON (a service-account key, as JSON or as
base64 of the JSON), the file GOOGLE_APPLICATION_CREDENTIALS names, and last the file that
`gcloud auth application-default login` writes. No Google library is needed: a service-account
key is signed here with `cryptography`, already a requirement. Only Workload Identity
Federation (credential type external_account) needs google-auth, and it says so if missing.

v1 reads Google Cloud only. Everything after the Google section works on plain dicts that carry
a 'source' field, and the tables are keyed by source, so a second provider is one more
snapshot function feeding the same page, check and tables.

Access is its own private feature ('cost_monitor'), plus the super admin and GCP_COST_ADMINS.
Setup and every setting: docs/cost-monitor.md.
"""
import base64
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from html import escape
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

import private_features

logger = logging.getLogger(__name__)

cost_bp = Blueprint('cost', __name__, url_prefix='/cost-monitor')

# Set by init_cost_monitor()
get_db_connection = None
release_db_connection = None
send_email = None

FEATURE = 'cost_monitor'
MIGRATION = '038_cost_monitor.sql'
DOCS_URL = 'https://github.com/cmaidmentnh/nh-candidate-recruitment/blob/main/docs/cost-monitor.md'
EXPORT_HELP_URL = 'https://cloud.google.com/billing/docs/how-to/export-data-bigquery-setup'

# "Today" is the day in New Hampshire, not the server's UTC day, which ends at 8pm here.
REPORT_TZ = ZoneInfo('America/New_York')
# Google closes its invoice months on Pacific time.
INVOICE_TZ = ZoneInfo('America/Los_Angeles')


class CostMonitorError(RuntimeError):
    """Something the page can print as it is: already worded for a person, no secrets in it."""


def init_cost_monitor(db_conn_func, db_release_func, email_func=None):
    """Wires the database and the app's email sender (app.send_email, which is AWS SES), and
    makes sure the tables exist. A failure here is logged, not fatal: the page says what is
    wrong when it is opened."""
    global get_db_connection, release_db_connection, send_email
    get_db_connection = db_conn_func
    release_db_connection = db_release_func
    send_email = email_func
    try:
        ensure_tables()
    except Exception as e:
        logger.error(f'[cost] could not create tables at start-up: {e}')


# =============================================================================
# SETTINGS (all environment; docs/cost-monitor.md lists them)
# =============================================================================

def _env(name, default=''):
    return (os.environ.get(name) or default).strip()


def project():
    return _env('GCP_COST_PROJECT')


def enabled():
    """Off, cleanly, until the project is named. Nothing is called and nothing is sent."""
    return bool(project())


def alert_hours():
    """GCP_COST_ALERT_HOURS, default 3. A list such as "3,8,24" warns again at each mark, so a
    VM left on all night is not down to the one email it got at 3 hours."""
    marks = set()
    for part in _env('GCP_COST_ALERT_HOURS', '3').split(','):
        try:
            v = float(part)
        except ValueError:
            continue
        if v > 0:
            marks.add(v)
    return sorted(marks) or [3.0]


def alert_usd():
    """GCP_COST_ALERT_USD: warn once a day when today's estimate passes it. Unset means off."""
    raw = _env('GCP_COST_ALERT_USD').replace('$', '').replace(',', '')
    try:
        v = float(raw) if raw else None
    except ValueError:
        return None
    return v if v and v > 0 else None


def allow_stop():
    return _env('GCP_COST_ALLOW_STOP').lower() in ('1', 'true', 'yes', 'on')


def cost_admins():
    """Who may stop a VM (when stopping is on) and who sees the page without a grant. The super
    admin always does. Default: Ross, who rents the VMs."""
    return {e.strip().lower() for e in _env('GCP_COST_ADMINS', 'berryrm0@gmail.com').split(',') if e.strip()}


def alert_recipients():
    raw = _env('GCP_COST_ALERT_TO')
    return [e.strip() for e in raw.split(',') if e.strip()] if raw else sorted(cost_admins())


def bq_table():
    return _env('GCP_BILLING_BQ_TABLE')


def app_url():
    return _env('APP_URL', 'https://nhcandidaterecruitment.com').rstrip('/')


# =============================================================================
# CREDENTIALS
# =============================================================================

SCOPE = 'https://www.googleapis.com/auth/cloud-platform'   # IAM roles do the limiting, not this
DEFAULT_TOKEN_URI = 'https://oauth2.googleapis.com/token'
TIMEOUT = 25

_token = {'value': None, 'expires': 0.0}
_token_lock = threading.Lock()


def _well_known_adc():
    """Where `gcloud auth application-default login` leaves its file."""
    if os.name == 'nt':
        base = os.environ.get('APPDATA')
        return os.path.join(base, 'gcloud', 'application_default_credentials.json') if base else None
    return os.path.expanduser('~/.config/gcloud/application_default_credentials.json')


def credentials_source():
    """Which credential this process would use, by NAME. Never any part of the value."""
    if _env('GCP_ACCESS_TOKEN'):
        return 'GCP_ACCESS_TOKEN (a one-hour developer token)'
    if _env('GCP_COST_CREDENTIALS_JSON'):
        return 'GCP_COST_CREDENTIALS_JSON'
    if _env('GOOGLE_APPLICATION_CREDENTIALS'):
        return 'GOOGLE_APPLICATION_CREDENTIALS'
    path = _well_known_adc()
    if path and os.path.exists(path):
        return 'gcloud application-default credentials'
    return None


def _json_or_base64(raw, name):
    """A key pasted into .env: the JSON itself, or base64 of it, which is easier to quote."""
    text = raw.strip()
    if not text.startswith('{'):
        try:
            text = base64.b64decode(text, validate=False).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            raise CostMonitorError(f'{name} is neither JSON nor base64 of JSON.')
    try:
        value = json.loads(text)
    except ValueError:
        raise CostMonitorError(f'{name} is not valid JSON.')
    if not isinstance(value, dict):
        raise CostMonitorError(f'{name} is not a credentials file.')
    return value


def _credential_info():
    raw = _env('GCP_COST_CREDENTIALS_JSON')
    if raw:
        return _json_or_base64(raw, 'GCP_COST_CREDENTIALS_JSON')
    named = _env('GOOGLE_APPLICATION_CREDENTIALS')
    path = named or _well_known_adc()
    if path and os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                return _json_or_base64(f.read(), 'The credentials file')
        except OSError as e:
            raise CostMonitorError(f'The credentials file could not be read ({e.__class__.__name__}).')
    if named:
        raise CostMonitorError('GOOGLE_APPLICATION_CREDENTIALS names a file that does not exist.')
    raise CostMonitorError('No Google credentials. Set GCP_COST_CREDENTIALS_JSON or '
                           'GOOGLE_APPLICATION_CREDENTIALS on the server (docs/cost-monitor.md).')


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _post_token(uri, form):
    try:
        r = _http('POST', uri, data=form)
    except requests.RequestException as e:
        raise CostMonitorError(f'Could not reach Google to sign in ({e.__class__.__name__}).')
    try:
        body = r.json() or {}
    except ValueError:
        body = {}
    if r.status_code != 200 or not body.get('access_token'):
        why = str(body.get('error_description') or body.get('error') or f'HTTP {r.status_code}').rstrip('.')
        raise CostMonitorError(f'Google refused the credentials: {why}.')
    return body['access_token'], float(body.get('expires_in') or 3600)


def _service_account_token(info):
    """OAuth's JWT-bearer grant, by hand: a claim set signed RS256 with the key's private key,
    traded at Google's token endpoint for an hour-long access token."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    try:
        key = serialization.load_pem_private_key(info['private_key'].encode('utf-8'), password=None)
        email = info['client_email']
    except (KeyError, ValueError, TypeError, AttributeError):
        raise CostMonitorError('The service-account key is incomplete, or its private_key cannot be read.')
    token_uri = info.get('token_uri') or DEFAULT_TOKEN_URI
    now = int(time.time())
    header = {'alg': 'RS256', 'typ': 'JWT'}
    if info.get('private_key_id'):
        header['kid'] = info['private_key_id']
    claims = {'iss': email, 'scope': SCOPE, 'aud': token_uri, 'iat': now, 'exp': now + 3600}
    signing_input = '.'.join(_b64url(json.dumps(p, separators=(',', ':')).encode('utf-8')) for p in (header, claims))
    signature = key.sign(signing_input.encode('ascii'), padding.PKCS1v15(), hashes.SHA256())
    return _post_token(token_uri, {'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                                   'assertion': signing_input + '.' + _b64url(signature)})


def _google_auth_token(info, kind):
    """Workload Identity Federation (type external_account) and anything else unusual. That
    needs the google-auth package, which nothing else in this app needs, so it is not a
    requirement until someone chooses federation."""
    try:
        import google.auth
        from google.auth.transport.requests import Request
    except ImportError:
        raise CostMonitorError(f'Credentials of type "{kind}" (Workload Identity Federation) need the '
                               'google-auth package. Add google-auth to requirements.txt.')
    try:
        creds, _ = google.auth.load_credentials_from_dict(info, scopes=[SCOPE])
        creds.refresh(Request())
    except Exception as e:
        raise CostMonitorError(f'Google refused the {kind} credentials: {str(e)[:200]}')
    lifetime = 3600.0
    expiry = getattr(creds, 'expiry', None)
    if expiry:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        lifetime = max(60.0, (expiry - datetime.now(timezone.utc)).total_seconds())
    return creds.token, lifetime


def _fetch_token(info):
    kind = info.get('type')
    if kind == 'service_account':
        return _service_account_token(info)
    if kind == 'authorized_user':
        # What `gcloud auth application-default login` writes. For a laptop, not the server.
        return _post_token(info.get('token_uri') or DEFAULT_TOKEN_URI, {
            'grant_type': 'refresh_token', 'client_id': info.get('client_id'),
            'client_secret': info.get('client_secret'), 'refresh_token': info.get('refresh_token')})
    return _google_auth_token(info, kind or 'unknown')


def _access_token():
    dev = _env('GCP_ACCESS_TOKEN')
    if dev:
        return dev
    with _token_lock:
        if _token['value'] and time.time() < _token['expires'] - 300:
            return _token['value']
        value, lifetime = _fetch_token(_credential_info())
        _token.update(value=value, expires=time.time() + lifetime)
        return value


def clear_caches():
    """Forget the token, the VM list, machine sizes and the BigQuery total. For tests, and for
    anyone changing credentials in a running shell."""
    _token.update(value=None, expires=0.0)
    _snapshot_cache.update(at=0.0, value=None)
    _actual_cache.update(at=0.0, key=None, value=None)
    _shapes.clear()


# =============================================================================
# TALKING TO GOOGLE
# =============================================================================

def _http(method, url, **kw):
    """The one place a request leaves this module. Tests replace it."""
    return requests.request(method, url, timeout=TIMEOUT, **kw)


def _explain(r):
    try:
        err = (r.json() or {}).get('error')
        msg = err.get('message') if isinstance(err, dict) else err
    except (ValueError, AttributeError):
        msg = None
    msg = str(msg or r.text or '').strip()[:300].rstrip('.')
    if r.status_code == 401:
        if _env('GCP_ACCESS_TOKEN'):
            return 'Google did not accept GCP_ACCESS_TOKEN (401). It lasts an hour: print a fresh one.'
        return f'Google did not accept the credentials (401): {msg}.'
    if r.status_code == 403:
        return (f'Permission denied (403): {msg}. The service account needs the roles listed in '
                'docs/cost-monitor.md.')
    return f'HTTP {r.status_code}: {msg}.'


def _call(method, url, params=None, json_body=None):
    token = _access_token()
    try:
        r = _http(method, url, params=params, json=json_body, headers={'Authorization': 'Bearer ' + token})
    except requests.RequestException as e:
        raise CostMonitorError(f'Google did not answer ({e.__class__.__name__}).')
    if r.status_code >= 400:
        raise CostMonitorError(_explain(r))
    try:
        return r.json()
    except ValueError:
        raise CostMonitorError(f'Google answered with something that is not JSON (HTTP {r.status_code}).')


# =============================================================================
# RATES
# =============================================================================

# List prices in US dollars, on demand, before any discount, credit or tax. Read from Google's
# own price list (Cloud Billing Catalog API, service 6F81-5844-456A "Compute Engine") on
# 2026-09-23. The SKU id sits beside each number so it can be looked up again. The "running in
# Americas" SKUs cover us-central1, us-east1 and us-west1 at one price, so those are the only
# regions priced: a VM anywhere else says "rate unknown" rather than borrow a wrong number.
#
# The two machines this was built for, worked through (they match Google's pricing page):
#   n2-standard-8, Windows:  8 vCPU x 0.031611 + 32 GB x 0.004237 = 0.3885
#                            + Windows 8 x 0.046 = 0.368                       -> about $0.76/h
#   g2-standard-32, Windows: 32 vCPU x 0.024988212 + 128 GB x 0.002927448 = 1.1743
#                            + 1 L4 GPU 0.5600 + Windows 32 x 0.046 = 1.472    -> about $3.21/h
#
# Change any of it without a deploy: GCP_COST_RATES_JSON is laid over this, section by section,
# for example {"families": {"c4": {"vcpu": 0.0, "gb": 0.0}}, "gpus": {"nvidia-a100-80gb": 0.0}}.
RATES_READ_ON = '2026-09-23'
RATES = {
    'regions': ['us-east1', 'us-central1', 'us-west1'],
    # Per vCPU-hour and per GB-of-RAM-hour. The size of each machine type (vCPUs, RAM, and any
    # GPU that comes with it) is read from Google, so every predefined size in a family is
    # covered. Custom and shared-core types are priced differently and show "rate unknown".
    'families': {
        'n2': {'vcpu': 0.031611, 'gb': 0.004237},           # BB77-5FDA-69D9, 5B01-D157-A097
        'n1': {'vcpu': 0.031611, 'gb': 0.004237},           # 2E27-4F75-95CD, 6C71-E844-38BC
        'n2d': {'vcpu': 0.027502, 'gb': 0.003686},          # A03E-E620-7389, 5535-6D2D-4B50
        'e2': {'vcpu': 0.02181159, 'gb': 0.00292353},       # CF4E-A0C7-E3BF, F449-33EC-A5EF
        'c3': {'vcpu': 0.03465, 'gb': 0.003938},            # E4AE-E326-889C, 071C-145A-16E0
        'g2': {'vcpu': 0.024988212, 'gb': 0.002927448},     # D18E-3563-415F, F0A4-E3D3-33BD
    },
    # Per GPU-hour. Google bills a GPU as its own line whether it comes with the machine type
    # (the L4 in a G2) or is attached to one (a T4 on an N1), so it is added in both cases.
    'gpus': {
        'nvidia-l4': 0.560040239,        # A88A-5A60-E821
        'nvidia-tesla-t4': 0.35,         # 88B8-C3ED-03F0
        'nvidia-tesla-p4': 0.60,         # A152-C7DF-4872
        'nvidia-tesla-p100': 1.46,       # 7929-334B-6348
        'nvidia-tesla-v100': 2.48,       # DC0B-9926-40C5
    },
    # Windows Server licence per vCPU-hour, on top of the machine. "Licensing Fee for Windows
    # Server 2022 Datacenter Edition on VM"; 2016, 2019 and 2025 are the same $0.046. Google
    # charges nothing more for the RAM or the GPUs of a Windows VM.
    'windows_per_vcpu': 0.046,
    # A whole machine type at a fixed $/hour, Linux, e.g. copied off Google's pricing page. Wins
    # over the family rates. For G2 and A2 types that published price already includes the GPU
    # the type comes with, so only GPUs attached on top of it are added.
    'machine_types': {},
}


def _num(v):
    """A non-negative number, or None. Money from the database arrives as Decimal."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def rates_problem():
    raw = _env('GCP_COST_RATES_JSON')
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return 'GCP_COST_RATES_JSON is not valid JSON, so the built-in rates are in use.'
    if not isinstance(value, dict):
        return 'GCP_COST_RATES_JSON must be a JSON object, so the built-in rates are in use.'
    return None


def rates():
    """RATES with GCP_COST_RATES_JSON laid over it, one section at a time."""
    out = json.loads(json.dumps(RATES))
    if not _env('GCP_COST_RATES_JSON') or rates_problem():
        return out
    for k, v in json.loads(_env('GCP_COST_RATES_JSON')).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def gpu_label(kind):
    """nvidia-tesla-t4 -> T4, nvidia-l4 -> L4."""
    return kind.replace('nvidia-', '').replace('tesla-', '').upper()


def gpus_label(gpus):
    return ', '.join(f'{n}x {gpu_label(k)}' for k, n in sorted(gpus.items()) if n) or None


def estimate(machine_type, region, shape, attached_gpus=None, windows=False, table=None):
    """What one VM costs an hour while it runs: machine, plus GPUs, plus the Windows licence.

    Returns {'rate': $/h or None, 'parts': [(label, $/h)], 'unknown': why, or None}. One part
    that cannot be priced makes the whole rate unknown: a total missing its GPU would be a
    confident wrong number, which is worse than no number."""
    t = table or rates()
    shape = shape or {}
    attached = attached_gpus or {}
    bundled = dict(shape.get('gpus') or {})

    def unknown(why):
        return {'rate': None, 'parts': [], 'unknown': why}

    if region not in (t.get('regions') or []):
        return unknown(f'no prices for {region or "this region"}')
    parts = []
    fixed = _num((t.get('machine_types') or {}).get(machine_type))
    if fixed is not None:
        parts.append((machine_type, fixed))
        # The published price of a G2 already has its own L4 in it.
        gpus = {g: n - bundled.get(g, 0) for g, n in attached.items() if n > bundled.get(g, 0)}
    else:
        family = machine_type.split('-', 1)[0]
        rate = (t.get('families') or {}).get(family)
        if 'custom' in machine_type:
            return unknown('custom machine types are priced differently')
        if shape.get('shared_cpu'):
            return unknown('shared-core machine types are priced differently')
        if not isinstance(rate, dict):
            return unknown(f'no rate for {family} machines')
        per_vcpu, per_gb = _num(rate.get('vcpu')), _num(rate.get('gb'))
        if per_vcpu is None or per_gb is None:
            return unknown(f'the {family} rate is not a number')
        if not shape.get('vcpus') or not shape.get('memory_gb'):
            return unknown('machine size not known')
        parts.append((f'{shape["vcpus"]} vCPU, {shape["memory_gb"]:g} GB',
                      shape['vcpus'] * per_vcpu + shape['memory_gb'] * per_gb))
        # A G2's L4 may be listed both on the machine type and on the VM: count it once.
        gpus = dict(bundled)
        for g, n in attached.items():
            gpus[g] = max(gpus.get(g, 0), n)
    for g, n in sorted(gpus.items()):
        if n <= 0:
            continue
        per_gpu = _num((t.get('gpus') or {}).get(g))
        if per_gpu is None:
            return unknown(f'no rate for {g} GPUs')
        parts.append((f'{n}x {gpu_label(g)} GPU', n * per_gpu))
    if windows:
        per_win = _num(t.get('windows_per_vcpu'))
        if not shape.get('vcpus') or per_win is None or shape.get('shared_cpu'):
            return unknown('the Windows licence cannot be priced')
        parts.append((f'Windows licence, {shape["vcpus"]} vCPU', shape['vcpus'] * per_win))
    return {'rate': round(sum(p for _, p in parts), 4),
            'parts': [(label, round(p, 4)) for label, p in parts], 'unknown': None}


# =============================================================================
# THE VMs
# =============================================================================

COMPUTE = 'https://compute.googleapis.com/compute/v1'

# RUNNING is what Google bills. The other three last a minute or two (REPAIRING longer, and
# rarely) and are counted as well: this page errs toward saying a VM costs money, never away.
BILLED = ('RUNNING', 'STOPPING', 'SUSPENDING', 'REPAIRING')
# On its way up: not billed yet, but it will be in a minute, so it goes in the banner.
STARTING = ('PROVISIONING', 'STAGING')

_TS = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?$')


def _parse_ts(s):
    """Google's RFC 3339, "2026-09-23T07:47:54.658-07:00", as an aware UTC datetime. Done by
    hand because datetime.fromisoformat only learned the Z form in Python 3.11."""
    m = _TS.match((s or '').strip())
    if not m:
        return None
    base, frac, tz = m.groups()
    frac = (frac or '.0')[1:7].ljust(6, '0')
    tz = '+00:00' if tz in (None, 'Z') else tz
    try:
        return datetime.fromisoformat(f'{base}.{frac}{tz}').astimezone(timezone.utc)
    except ValueError:
        return None


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _last(url):
    return (url or '').rstrip('/').rsplit('/', 1)[-1]


def _windows_licence(inst):
    """Windows images carry a licence from Google's windows-cloud project on the boot disk,
    e.g. .../projects/windows-cloud/global/licenses/windows-server-2022-dc."""
    for disk in inst.get('disks') or []:
        if not disk.get('boot'):
            continue
        for lic in disk.get('licenses') or []:
            if '/projects/windows-cloud/' in lic:
                return _last(lic)
    return None


def _os_label(licence):
    if not licence:
        return 'Linux'
    m = re.search(r'windows-server-(\d{4})', licence)
    return f'Windows Server {m.group(1)}' if m else 'Windows'


_shapes = {}


def _machine_shape(zone, machine_type):
    """vCPUs, RAM and any GPU a machine type comes with. The VM list gives only the type's
    name. Asked once per zone and type, then kept for the life of the process."""
    key = (zone, machine_type)
    if key not in _shapes:
        d = _call('GET', f'{COMPUTE}/projects/{project()}/zones/{zone}/machineTypes/{machine_type}')
        _shapes[key] = {
            'vcpus': int(d.get('guestCpus') or 0) or None,
            'memory_gb': round(float(d['memoryMb']) / 1024.0, 2) if d.get('memoryMb') else None,
            'shared_cpu': bool(d.get('isSharedCpu')),
            'gpus': {a['guestAcceleratorType']: int(a.get('guestAcceleratorCount') or 0)
                     for a in d.get('accelerators') or [] if a.get('guestAcceleratorType')},
        }
    return _shapes[key]


def _list_instances():
    """Every VM in the project, all zones, one aggregated list. returnPartialSuccess means one
    zone having a bad day costs that zone, named in 'unreachables', not the whole page."""
    found, unreachable, page = [], [], None
    for _ in range(20):   # 500 a page: twenty pages is 10,000 VMs, far past anything real
        params = {'maxResults': 500, 'returnPartialSuccess': 'true'}
        if page:
            params['pageToken'] = page
        d = _call('GET', f'{COMPUTE}/projects/{project()}/aggregated/instances', params=params)
        for block in (d.get('items') or {}).values():
            found.extend(block.get('instances') or [])
        unreachable.extend(d.get('unreachables') or [])
        page = d.get('nextPageToken')
        if not page:
            break
    return found, unreachable


def console_url(zone, name):
    return (f'https://console.cloud.google.com/compute/instancesDetail/zones/{zone}/instances/{name}'
            f'?project={project()}')


def _normalize(inst, now, table):
    """One VM as the page, the check and the tables all see it. Provider-neutral on purpose:
    a second cost source only has to produce these same keys."""
    zone = _last(inst.get('zone'))
    region = zone.rsplit('-', 1)[0] if zone else ''
    machine_type = _last(inst.get('machineType'))
    status = inst.get('status') or 'UNKNOWN'
    licence = _windows_licence(inst)
    sched = inst.get('scheduling') or {}

    shape, shape_error = None, None
    try:
        shape = _machine_shape(zone, machine_type)
    except CostMonitorError as e:
        shape_error = str(e)
    attached = {}
    for a in inst.get('guestAccelerators') or []:
        kind = _last(a.get('acceleratorType'))
        if kind:
            attached[kind] = attached.get(kind, 0) + int(a.get('acceleratorCount') or 0)
    gpus = dict((shape or {}).get('gpus') or {})
    for kind, n in attached.items():
        gpus[kind] = max(gpus.get(kind, 0), n)

    est = estimate(machine_type, region, shape, attached, bool(licence), table)
    if shape_error and est['rate'] is None:
        est['unknown'] = f'machine size not known ({shape_error})'

    started = _parse_ts(inst.get('lastStartTimestamp')) or _parse_ts(inst.get('creationTimestamp'))
    stopped = _parse_ts(inst.get('lastStopTimestamp'))
    if stopped and started and stopped < started:
        stopped = None   # a stop from before the latest start: this run has not ended
    billed = status in BILLED
    hours = None
    if billed and started:
        hours = max(0.0, (now - started).total_seconds() / 3600.0)
    elif not billed and status not in STARTING and started and stopped:
        hours = (stopped - started).total_seconds() / 3600.0   # the last run, for the record
    run_cost = round(est['rate'] * hours, 2) if est['rate'] is not None and hours is not None else None

    return {
        'source': 'gcp',
        'id': str(inst.get('id') or inst.get('name')),
        'name': inst.get('name') or '?',
        'zone': zone,
        'region': region,
        'status': status,
        'on': billed or status in STARTING,
        'billed': billed,
        'starting': status in STARTING,
        'machine_type': machine_type,
        'vcpus': (shape or {}).get('vcpus'),
        'memory_gb': (shape or {}).get('memory_gb'),
        'gpus': gpus,
        'gpu_label': gpus_label(gpus),
        'windows': bool(licence),
        'os_label': _os_label(licence),
        'spot': sched.get('provisioningModel') == 'SPOT' or bool(sched.get('preemptible')),
        'started_at': started,
        'stopped_at': stopped if not billed else None,
        'hours': hours,
        'rate': est['rate'],
        'rate_parts': est['parts'],
        'rate_unknown': est['unknown'],
        'run_cost': run_cost,
        'console_url': console_url(zone, inst.get('name') or ''),
    }


SNAPSHOT_TTL_S = 60
_snapshot_cache = {'at': 0.0, 'value': None}


def _failed(now, error, is_enabled=True):
    return {'ok': False, 'enabled': is_enabled, 'error': error, 'checked_at': now, 'project': project(),
            'resources': [], 'running': [], 'stopped': [], 'unreachable': [],
            'burn_rate': 0.0, 'burn_unknown': 0, 'run_cost': 0.0}


def gcp_snapshot(fresh=False, now=None):
    """Every VM in the project, priced. Read live, and kept for a minute so a reload does not
    ask Google again. Never raises: a failure comes back as {'ok': False, 'error': ...}."""
    now = now or datetime.now(timezone.utc)
    if not enabled():
        return _failed(now, None, is_enabled=False)
    cached = _snapshot_cache['value']
    if not fresh and cached is not None and time.time() - _snapshot_cache['at'] < SNAPSHOT_TTL_S:
        return cached
    try:
        raw, unreachable = _list_instances()
        table = rates()
        resources = [_normalize(i, now, table) for i in raw]
    except CostMonitorError as e:
        return _failed(now, str(e))
    except Exception as e:   # a bug in here must not take the page down with it
        logger.exception('[cost] reading Google Cloud failed')
        return _failed(now, f'Unexpected error: {e.__class__.__name__}: {str(e)[:200]}')

    resources.sort(key=lambda r: (not r['on'], -(r['rate'] or 0), r['name']))
    running = [r for r in resources if r['on']]
    snap = {
        'ok': True, 'enabled': True, 'error': None, 'checked_at': now, 'project': project(),
        'resources': resources, 'running': running, 'stopped': [r for r in resources if not r['on']],
        'unreachable': unreachable,
        'burn_rate': round(sum(r['rate'] for r in running if r['billed'] and r['rate'] is not None), 4),
        'burn_unknown': sum(1 for r in running if r['billed'] and r['rate'] is None),
        'run_cost': round(sum(r['run_cost'] for r in running if r['run_cost'] is not None), 2),
    }
    _snapshot_cache.update(at=time.time(), value=snap)
    return snap


def gcp_stop(zone, name):
    """The one write this module can make, the same as pressing Stop in the console. Reached
    only from the confirm page, behind GCP_COST_ALLOW_STOP=1 and a cost admin."""
    if not allow_stop():
        raise CostMonitorError('Stopping VMs from here is turned off (GCP_COST_ALLOW_STOP).')
    return _call('POST', f'{COMPUTE}/projects/{project()}/zones/{zone}/instances/{name}/stop')


# =============================================================================
# ACTUAL SPEND (the billing export in BigQuery)
# =============================================================================

BIGQUERY = 'https://bigquery.googleapis.com/bigquery/v2'
# A ceiling on what one read may scan. The export for one small account is megabytes; 2 GB of
# BigQuery scanning is about a cent. Past it the query fails rather than bills.
BQ_MAX_BYTES = 2 * 1024 ** 3
ACTUAL_TTL_S = 30 * 60
_actual_cache = {'at': 0.0, 'key': None, 'value': None}

# project.dataset.table, and nothing else: the name goes into the SQL, so it is checked first.
TABLE_RE = re.compile(r'^([a-z0-9][a-z0-9.:-]*[a-z0-9])\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)$')

# Google's own recipe for the standard export: cost, plus credits (which are negative).
SPEND_SQL = """
SELECT service.description AS service,
       DATE(usage_start_time, 'America/New_York') AS day,
       SUM(cost) AS cost,
       SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0)) AS credits
  FROM `{table}`
 WHERE invoice.month = @month
   AND project.id = @project
 GROUP BY service, day
 ORDER BY day, service
"""


def _bq_query(job_project, sql, params):
    body = {'query': sql, 'useLegacySql': False, 'parameterMode': 'NAMED', 'timeoutMs': 20000,
            'maxResults': 5000, 'maximumBytesBilled': str(BQ_MAX_BYTES),
            'queryParameters': [{'name': k, 'parameterType': {'type': 'STRING'},
                                 'parameterValue': {'value': v}} for k, v in params.items()]}
    d = _call('POST', f'{BIGQUERY}/projects/{job_project}/queries', json_body=body)
    polls = 0
    while not d.get('jobComplete'):
        ref = d.get('jobReference') or {}
        if polls >= 3 or not ref.get('jobId'):
            raise CostMonitorError('BigQuery did not finish in time. Try again in a minute.')
        polls += 1
        d = _call('GET', f'{BIGQUERY}/projects/{job_project}/queries/{ref["jobId"]}',
                  params={'timeoutMs': 10000, 'maxResults': 5000, 'location': ref.get('location')})
    fields = [f.get('name') for f in ((d.get('schema') or {}).get('fields') or [])]
    return [dict(zip(fields, [c.get('v') for c in (row.get('f') or [])])) for row in d.get('rows') or []]


def actual_spend(fresh=False, now=None):
    """Month-to-date spend from the billing export, for this project: the real bill, some
    hours behind. {'configured': False} when GCP_BILLING_BQ_TABLE is not set."""
    table = bq_table()
    if not table or not enabled():
        return {'configured': False}
    now = now or datetime.now(timezone.utc)
    m = TABLE_RE.match(table)
    if not m:
        return {'configured': True, 'ok': False, 'table': table,
                'error': 'GCP_BILLING_BQ_TABLE should look like project.dataset.table.'}
    month = now.astimezone(INVOICE_TZ).strftime('%Y%m')
    key = (table, month, project())
    cached = _actual_cache['value']
    if not fresh and cached and _actual_cache['key'] == key and time.time() - _actual_cache['at'] < ACTUAL_TTL_S:
        return cached
    try:
        rows = _bq_query(m.group(1), SPEND_SQL.replace('{table}', table), {'month': month, 'project': project()})
    except CostMonitorError as e:
        return {'configured': True, 'ok': False, 'table': table, 'error': str(e)}

    by_service, by_day = {}, {}
    for r in rows:
        cost, credits = float(r.get('cost') or 0), float(r.get('credits') or 0)
        for bucket, label in ((by_service, r.get('service') or 'Other'), (by_day, r.get('day') or '?')):
            acc = bucket.setdefault(label, [0.0, 0.0])
            acc[0] += cost
            acc[1] += credits

    def listed(bucket):
        return [{'label': k, 'cost': round(c, 2), 'credits': round(cr, 2), 'net': round(c + cr, 2)}
                for k, (c, cr) in bucket.items()]

    services = sorted(listed(by_service), key=lambda x: (-x['net'], x['label']))
    days = sorted(listed(by_day), key=lambda x: x['label'], reverse=True)
    value = {'configured': True, 'ok': True, 'table': table, 'month': f'{month[:4]}-{month[4:]}',
             'checked_at': now, 'by_service': services, 'by_day': days,
             'cost': round(sum(x['cost'] for x in services), 2),
             'credits': round(sum(x['credits'] for x in services), 2),
             'net': round(sum(x['net'] for x in services), 2)}
    _actual_cache.update(at=time.time(), key=key, value=value)
    return value


# =============================================================================
# WHAT THE CHECK REMEMBERS
# =============================================================================

@contextmanager
def _cursor(dict_rows=False):
    if get_db_connection is None:
        raise CostMonitorError('The database is not wired up.')
    conn = get_db_connection()
    try:
        if dict_rows:
            from psycopg2.extras import RealDictCursor
            cur = conn.cursor(cursor_factory=RealDictCursor)
        else:
            cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()   # never hand the pool a connection stuck in a failed transaction
            raise
        finally:
            cur.close()
    finally:
        release_db_connection(conn)


def ensure_tables():
    """Runs migrations/038. Everything in it is IF NOT EXISTS, so it is safe on every start."""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, 'migrations', MIGRATION), encoding='utf-8') as f:
        sql = f.read()
    with _cursor() as cur:
        cur.execute(sql)


def record_runs(resources, now):
    """Remembers each run the check sees: enough to price today and to send each warning once.
    The live list stays the truth about what is on now."""
    rows = [r for r in resources if r['started_at'] and (r['billed'] or r['stopped_at'])]
    if not rows:
        return 0
    with _cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO cost_monitor_runs
                    (source, resource_id, started_at, name, location, machine_type, gpus, windows,
                     rate_per_hour, stopped_at, last_seen_at, last_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source, resource_id, started_at) DO UPDATE SET
                    name = EXCLUDED.name, location = EXCLUDED.location,
                    machine_type = EXCLUDED.machine_type, gpus = EXCLUDED.gpus,
                    windows = EXCLUDED.windows, rate_per_hour = EXCLUDED.rate_per_hour,
                    stopped_at = COALESCE(EXCLUDED.stopped_at, cost_monitor_runs.stopped_at),
                    last_seen_at = EXCLUDED.last_seen_at, last_status = EXCLUDED.last_status""",
                        (r['source'], r['id'], r['started_at'], r['name'], r['zone'], r['machine_type'],
                         r['gpu_label'], r['windows'], r['rate'], r['stopped_at'], now, r['status']))
    return len(rows)


def runs_since(since):
    with _cursor(dict_rows=True) as cur:
        cur.execute("""SELECT source, resource_id, started_at, name, rate_per_hour, stopped_at, last_seen_at
                         FROM cost_monitor_runs
                        WHERE last_seen_at >= %s OR stopped_at >= %s""", (since, since))
        return cur.fetchall()


def check_history(limit=10):
    """The last check's state row and the warnings most recently sent, for the page."""
    with _cursor(dict_rows=True) as cur:
        cur.execute("SELECT * FROM cost_monitor_state WHERE id = 1")
        state = cur.fetchone()
        cur.execute("""SELECT kind, resource_name, message, sent_to, sent_at
                         FROM cost_monitor_alerts ORDER BY sent_at DESC, id DESC LIMIT %s""", (limit,))
        return state, cur.fetchall()


def _day_start(now):
    local = now.astimezone(REPORT_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _overlap_hours(start, end, lo, hi):
    a, b = max(start, lo), min(end, hi)
    return max(0.0, (b - a).total_seconds() / 3600.0)


def estimate_today(resources, now, stored_runs=()):
    """Estimated spend so far today (the day in New Hampshire): every run that overlapped
    today, at its rate, for the part of it that fell today.

    The live list shows only each VM's latest run, so runs the check stored earlier fill in a
    VM that ran this morning and has since been restarted or deleted. A stored run that is no
    longer live and never showed a stop is counted to the last time it was seen, which can be
    one check (ten minutes) short: better that than inventing hours."""
    day_start = _day_start(now)
    runs = {}
    for row in stored_runs:
        end = row['stopped_at'] or row['last_seen_at']
        runs[(row['source'], row['resource_id'], row['started_at'])] = (
            row['name'], _num(row['rate_per_hour']), row['started_at'], end)
    for r in resources:
        if not r['started_at']:
            continue
        if r['billed']:
            end = now
        elif r['stopped_at']:
            end = r['stopped_at']
        else:
            continue
        runs[(r['source'], r['id'], r['started_at'])] = (r['name'], r['rate'], r['started_at'], end)
    total, unknown = 0.0, set()
    for name, rate, start, end in runs.values():
        if _overlap_hours(start, end, day_start, now) <= 0:
            continue
        if rate is None:
            unknown.add(name)
            continue
        total += rate * _overlap_hours(start, end, day_start, now)
    return {'date': day_start.astimezone(REPORT_TZ).date(), 'total': round(total, 2), 'unknown': sorted(unknown)}


def _save_state(now, ok=False, error=None, running=None, est_today=None, alert_error=None):
    try:
        with _cursor() as cur:
            cur.execute("""
                INSERT INTO cost_monitor_state
                    (id, last_check_at, last_ok_at, last_error, running_count, est_today, last_alert_error)
                VALUES (1, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    last_check_at = EXCLUDED.last_check_at,
                    last_ok_at = COALESCE(EXCLUDED.last_ok_at, cost_monitor_state.last_ok_at),
                    last_error = EXCLUDED.last_error,
                    running_count = COALESCE(EXCLUDED.running_count, cost_monitor_state.running_count),
                    est_today = COALESCE(EXCLUDED.est_today, cost_monitor_state.est_today),
                    last_alert_error = EXCLUDED.last_alert_error""",
                        (now, now if ok else None, error, running, est_today, alert_error))
    except Exception as e:
        logger.warning('[cost] could not save the check state: %s', str(e)[:200])


# =============================================================================
# THE WARNINGS
# =============================================================================

def usd(v):
    """$1,234.56. None is 'rate unknown', never $0. Credits are negative: -$1.23."""
    if v is None:
        return 'rate unknown'
    return f'-${-v:,.2f}' if v < 0 else f'${v:,.2f}'


def duration_label(hours):
    if hours is None:
        return '-'
    if hours < 1:
        return f'{int(round(hours * 60))} min'
    if hours < 48:
        return f'{hours:.1f} h'
    return f'{hours / 24:.1f} days'


def when_label(dt, now):
    """'10:47 AM' today, 'Sep 22, 10:47 PM' before today. Eastern."""
    if not dt:
        return '-'
    local = dt.astimezone(REPORT_TZ)
    clock = local.strftime('%I:%M %p').lstrip('0')
    if local.date() == now.astimezone(REPORT_TZ).date():
        return clock
    return f'{local.strftime("%b")} {local.day}, {clock}'


def ago_label(dt, now):
    if not dt:
        return 'never'
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 90:
        return 'just now'
    if secs < 5400:
        return f'{round(secs / 60)} min ago'
    if secs < 172800:
        return f'{round(secs / 3600)} hours ago'
    return f'{round(secs / 86400)} days ago'


def _key(*parts):
    return ':'.join(str(p) for p in parts)


def due_alerts(snap, today):
    """Everything past a line right now, sent or not. Each item carries every key it settles,
    so a VM already past two marks the first time the check sees it gets one email, not two."""
    items = []
    marks = alert_hours()
    for r in snap['running']:
        if r['hours'] is None or not r['started_at']:
            continue
        crossed = [h for h in marks if r['hours'] >= h]
        if not crossed:
            continue
        top = max(crossed)
        what = r['machine_type'] + (f', {r["gpu_label"]}' if r['gpu_label'] else '') + f', {r["os_label"]}'
        cost = (f'est. {usd(r["rate"])}/h, est. {usd(r["run_cost"])} so far' if r['rate'] is not None
                else f'rate unknown ({r["rate_unknown"]})')
        items.append({
            'kind': 'hours', 'resource': r, 'threshold': top,
            'keys': [_key(r['source'], r['id'], _iso(r['started_at']), 'hours', f'{h:g}') for h in crossed],
            'line': f'{r["name"]} has been running {duration_label(r["hours"])} (past {top:g} h): {what}; {cost}.',
        })
    line = alert_usd()
    if line is not None and today['total'] >= line:
        items.append({
            'kind': 'usd', 'resource': None, 'threshold': line,
            'keys': [_key('day', today['date'].isoformat(), 'usd', f'{line:g}')],
            'line': f'Estimated Google Cloud spend today is {usd(today["total"])}, past the {usd(line)} line.',
        })
    return items


def _alert_bodies(items, snap, today, now):
    page = f'{app_url()}/cost-monitor/'
    console = f'https://console.cloud.google.com/compute/instances?project={project()}'
    running = [f'{r["name"]} - {r["machine_type"]}{", " + r["gpu_label"] if r["gpu_label"] else ""}, '
               f'{r["os_label"]} - since {when_label(r["started_at"], now)} ET - {duration_label(r["hours"])} - '
               + (f'est. {usd(r["rate"])}/h, est. {usd(r["run_cost"])} so far' if r['rate'] is not None
                  else 'rate unknown')
               for r in snap['running']]
    unknown = f' (plus {len(today["unknown"])} VM(s) with rate unknown)' if today['unknown'] else ''
    rules = f'GCP_COST_ALERT_HOURS={",".join(f"{h:g}" for h in alert_hours())}'
    if alert_usd() is not None:
        rules += f', GCP_COST_ALERT_USD={alert_usd():g}'
    text = '\n'.join(
        ['Cost warning from the NH Candidate Tracker.', '']
        + [f'- {it["line"]}' for it in items]
        + ['', f'Running now in {project()}:']
        + [f'  {line}' for line in running or ['nothing']]
        + ['', f'Estimated spend today: {usd(today["total"])}{unknown}.',
           'Every figure is an estimate from list prices; the Google bill is the final word.', '',
           f'See it: {page}', f'Stop a VM in the Google Cloud console: {console}', '',
           f'One email per VM run for each threshold ({rules}).'])
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1c2733;font-size:15px;">'
        '<h2 style="margin:0 0 10px;color:#b4262d;font-size:18px;">Google Cloud cost warning</h2>'
        + ''.join(f'<p style="margin:0 0 8px;"><b>{escape(it["line"])}</b></p>' for it in items)
        + f'<p style="margin:14px 0 4px;color:#6b7684;">Running now in {escape(project())}:</p><ul style="margin:0 0 12px;">'
        + ''.join(f'<li>{escape(line)}</li>' for line in running or ['nothing'])
        + f'</ul><p>Estimated spend today: <b>{escape(usd(today["total"]))}</b>{escape(unknown)}. '
        'Every figure is an estimate from list prices; the Google bill is the final word.</p>'
        f'<p><a href="{escape(page)}">Open Cloud Costs</a> &middot; '
        f'<a href="{escape(console)}">Stop a VM in the Google Cloud console</a></p>'
        f'<p style="color:#6b7684;font-size:12px;">One email per VM run for each threshold ({escape(rules)}).</p></div>')
    return text, html


def _subject(items, today):
    if len(items) == 1 and items[0]['kind'] == 'hours':
        r = items[0]['resource']
        tail = f' (est. {usd(r["run_cost"])} so far)' if r['run_cost'] is not None else ''
        return f'Cloud cost: {r["name"]} has been on {duration_label(r["hours"])}{tail}'
    if len(items) == 1:
        return f'Cloud cost: est. {usd(today["total"])} today'
    return f'Cloud cost: {len(items)} warnings, est. {usd(today["total"])} today'


def _email(items, snap, today, now):
    if send_email is None:
        return False, 'No email sender is wired up (init_cost_monitor).'
    to = alert_recipients()
    if not to:
        return False, 'Nobody to send to: set GCP_COST_ALERT_TO.'
    subject = _subject(items, today)
    text, html = _alert_bodies(items, snap, today, now)
    sent = []
    for addr in to:
        try:
            if send_email(addr, subject, html, text):
                sent.append(addr)
        except Exception as e:
            logger.warning('[cost] alert email to one recipient failed: %s', e.__class__.__name__)
    if not sent:
        return False, ('The warning email could not be sent (check the AWS SES settings). '
                       'It will be tried again on the next check.')
    return True, None


def _send_due(items, snap, today, now):
    """Claim, then send, then keep the claim only if the email went.

    The row goes in (and is committed) before the email, so two workers can never both send
    the same warning. If the email fails the rows come out again, so the next check retries:
    a warning that never arrived was not sent."""
    to = ', '.join(alert_recipients())
    fresh = []
    with _cursor() as cur:
        for it in items:
            r = it['resource'] or {}
            mine = []
            for k in it['keys']:
                cur.execute("""
                    INSERT INTO cost_monitor_alerts
                        (alert_key, kind, source, resource_id, resource_name, run_started_at, message, sent_to)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (alert_key) DO NOTHING RETURNING id""",
                            (k, it['kind'], r.get('source'), r.get('id'), r.get('name'), r.get('started_at'),
                             it['line'], to))
                if cur.fetchone():
                    mine.append(k)
            if mine:
                fresh.append((it, mine))
    if not fresh:
        return [], None
    ok, err = _email([it for it, _ in fresh], snap, today, now)
    if not ok:
        with _cursor() as cur:
            cur.execute("DELETE FROM cost_monitor_alerts WHERE alert_key = ANY(%s)",
                        ([k for _, keys in fresh for k in keys],))
        return [], err
    return [it['line'] for it, _ in fresh], None


def run_check(now=None):
    """Read the VMs, remember the runs, send whatever warning is due. The timer, the cron URL
    and `flask cost-monitor-check` all run this. Returns a summary; never raises."""
    now = now or datetime.now(timezone.utc)
    out = {'enabled': enabled(), 'ok': False, 'checked_at': now.isoformat(), 'running': [],
           'est_today': None, 'sent': [], 'error': None, 'alert_error': None}
    if not out['enabled']:
        out['error'] = 'GCP_COST_PROJECT is not set, so there is nothing to check.'
        return out
    snap = gcp_snapshot(fresh=True, now=now)
    if not snap['ok']:
        out['error'] = snap['error']
        _save_state(now, error=snap['error'])
        return out
    out['running'] = [{'name': r['name'], 'status': r['status'], 'hours': round(r['hours'], 2) if r['hours'] else None,
                       'est_rate': r['rate'], 'est_run_cost': r['run_cost']} for r in snap['running']]
    try:
        record_runs(snap['resources'], now)
        stored = runs_since(_day_start(now))
    except Exception as e:
        # Without the tables there is no knowing what was already sent, and the same warning
        # every ten minutes is worse than none. Say so, and send nothing.
        out['error'] = (f'Database: {str(e)[:200].strip().rstrip(".")}. No warning was sent, because '
                        'without the database the same one would repeat on every check.')
        logger.warning('[cost] %s', out['error'])
        _save_state(now, error=out['error'], running=len(snap['running']))
        return out
    today = estimate_today(snap['resources'], now, stored)
    out['est_today'] = today['total']
    items = due_alerts(snap, today)
    if items:
        try:
            out['sent'], out['alert_error'] = _send_due(items, snap, today, now)
        except Exception as e:
            out['alert_error'] = f'Could not record the warning: {str(e)[:200]}'
            logger.warning('[cost] %s', out['alert_error'])
    out['ok'] = True
    _save_state(now, ok=True, running=len(snap['running']), est_today=today['total'],
                alert_error=out['alert_error'])
    return out


# =============================================================================
# THE TIMER
# =============================================================================

# Same pattern as the Meta and StackAdapt syncs: one daemon thread per gunicorn worker, and the
# work itself behind a Postgres advisory lock, so whichever worker wakes first does the check
# and the rest go back to sleep. Its own lock number, so it never waits on a sync.
AUTO_CHECK_LOCK = 0x434F5354_43484B31   # "COST" "CHK1"
AUTO_CHECK_EVERY_S = 10 * 60
AUTO_CHECK_STALE_MIN = 9
_auto_thread = None


def auto_check_enabled():
    return enabled() and _env('GCP_COST_AUTO_CHECK', '1').lower() not in ('0', 'false', 'no', 'off')


def start_auto_check():
    """Starts the ten-minute check in this process. Does nothing until GCP_COST_PROJECT is set,
    and GCP_COST_AUTO_CHECK=0 turns it off for a host that uses the cron URL instead."""
    global _auto_thread
    if not auto_check_enabled() or _auto_thread is not None:
        return
    _auto_thread = threading.Thread(target=_auto_loop, name='cost-monitor-check', daemon=True)
    _auto_thread.start()


def _auto_loop():
    time.sleep(150)   # let the app start, and stay out of step with the Meta and StackAdapt syncs
    while True:
        try:
            auto_check_once()
        except Exception:
            logger.exception('[cost] auto check failed')
        time.sleep(AUTO_CHECK_EVERY_S)


def auto_check_once():
    """Checks if no check has run in the last nine minutes and the lock is free."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (AUTO_CHECK_LOCK,))
        if not cur.fetchone()[0]:
            conn.commit()
            return {'lock': False}
        try:
            cur.execute("""SELECT last_check_at IS NULL OR last_check_at < now() - make_interval(mins => %s)
                             FROM cost_monitor_state WHERE id = 1""", (AUTO_CHECK_STALE_MIN,))
            row = cur.fetchone()
            conn.commit()
            if row is not None and not row[0]:
                return {'lock': True, 'due': False}
            return {'lock': True, 'due': True, 'result': run_check()}
        finally:
            conn.rollback()
            cur.execute("SELECT pg_advisory_unlock(%s)", (AUTO_CHECK_LOCK,))
            conn.commit()
            cur.close()
    finally:
        release_db_connection(conn)


# =============================================================================
# ACCESS
# =============================================================================

def is_cost_admin():
    """The super admin, plus GCP_COST_ADMINS. Needs no database, so it is cheap on every page."""
    if not current_user.is_authenticated:
        return False
    try:
        if private_features.is_super_admin and private_features.is_super_admin():
            return True
    except Exception:
        pass
    email = (getattr(current_user, 'email', None) or '').lower()
    return bool(email) and email in cost_admins()


def can_view_costs():
    """A cost admin, or anyone granted 'cost_monitor' on Manage Access."""
    if is_cost_admin():
        return True
    if not current_user.is_authenticated:
        return False
    try:
        return bool(private_features.has_feature_access(FEATURE))
    except Exception:
        return False


def can_stop():
    return allow_stop() and is_cost_admin()


def cost_access_required(f):
    """Same shape as meta_access_required: log in first, then the grant."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if can_view_costs():
            return f(*args, **kwargs)
        if not current_user.is_authenticated:
            flash('Please log in.', 'warning')
            return redirect(url_for('login'))
        flash('You do not have access to that page.', 'danger')
        return redirect(url_for('index'))
    return decorated


@cost_bp.app_context_processor
def inject_cost_monitor_access():
    # No database here: this runs on every page of the app. A user holding the grant is
    # recognised in base.html through user_private_features, which is loaded already.
    return {'cost_monitor_admin': is_cost_admin()}


# =============================================================================
# ROUTES
# =============================================================================

NAME_RE = re.compile(r'^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$')   # Google's rule for VM and zone names


def _who():
    return getattr(current_user, 'email', None) if current_user.is_authenticated else None


@cost_bp.route('/')
@cost_access_required
def page():
    fresh = request.args.get('fresh') == '1'
    now = datetime.now(timezone.utc)
    snap = gcp_snapshot(fresh=fresh, now=now)
    stored, state, alerts, history_error = [], None, [], None
    if enabled():
        try:
            stored = runs_since(_day_start(now))
            state, alerts = check_history()
        except Exception as e:
            history_error = str(e)[:200]
    today = estimate_today(snap['resources'], now, stored) if snap['ok'] else None
    return render_template(
        'cost/monitor.html', snap=snap, today=today, actual=actual_spend(fresh=fresh, now=now),
        state=state, alerts=alerts, history_error=history_error, configured=enabled(),
        project=project(), can_stop=can_stop(), allow_stop=allow_stop(),
        marks=', '.join(f'{h:g}' for h in alert_hours()), alert_usd=alert_usd(), recipients=alert_recipients(),
        creds=credentials_source(), rates_problem=rates_problem(), rates_read_on=RATES_READ_ON,
        priced_regions=rates().get('regions') or [], auto_check=auto_check_enabled(),
        docs_url=DOCS_URL, export_help_url=EXPORT_HELP_URL, now=now,
        usd=usd, dur=duration_label, when=lambda dt: when_label(dt, now), ago=lambda dt: ago_label(dt, now))


def _find(zone, name):
    snap = gcp_snapshot(fresh=True)
    if not snap['ok']:
        raise CostMonitorError(snap['error'] or 'Google Cloud could not be read.')
    return next((r for r in snap['resources'] if r['zone'] == zone and r['name'] == name), None)


@cost_bp.route('/stop', methods=['GET'])
@cost_access_required
def stop_confirm():
    """The confirm step. Changes nothing: it re-reads the VM and says what stopping it means."""
    if not can_stop():
        abort(403)
    zone, name = request.args.get('zone', ''), request.args.get('name', '')
    if not (NAME_RE.match(zone) and NAME_RE.match(name)):
        abort(400)
    try:
        r = _find(zone, name)
    except CostMonitorError as e:
        flash(f'Could not read the VM: {e}', 'danger')
        return redirect(url_for('cost.page'))
    if not r:
        flash(f'{name} is not in {project()} any more.', 'warning')
        return redirect(url_for('cost.page'))
    now = datetime.now(timezone.utc)
    return render_template('cost/stop.html', r=r, project=project(), usd=usd, dur=duration_label,
                           when=lambda dt: when_label(dt, now))


@cost_bp.route('/stop', methods=['POST'])
@cost_access_required
def stop():
    if not can_stop():
        abort(403)
    f = request.form
    zone, name, rid = f.get('zone', ''), f.get('name', ''), f.get('id', '')
    if f.get('confirm') != 'stop' or not (NAME_RE.match(zone) and NAME_RE.match(name)):
        abort(400)
    try:
        r = _find(zone, name)
    except CostMonitorError as e:
        flash(f'Nothing was stopped: {e}', 'danger')
        return redirect(url_for('cost.page'))
    # The id must match the VM the confirm page showed: a name can be deleted and reused.
    if not r or r['id'] != rid:
        flash(f'{name} is not the VM that was confirmed any more. Nothing was stopped.', 'warning')
        return redirect(url_for('cost.page'))
    if not r['billed']:
        flash(f'{name} is not running ({r["status"]}). Nothing was stopped.', 'info')
        return redirect(url_for('cost.page'))
    try:
        gcp_stop(zone, name)
    except CostMonitorError as e:
        flash(f'Google would not stop {name}: {e}', 'danger')
        return redirect(url_for('cost.page'))
    logger.warning('[cost] %s asked Google to stop %s (%s) in %s/%s', _who(), name, rid, project(), zone)
    _snapshot_cache.update(at=0.0, value=None)
    flash(f'Stop requested for {name}. It takes a minute or two; Check now shows it as TERMINATED once it is off.',
          'success')
    return redirect(url_for('cost.page'))


@cost_bp.route('/cron/check', methods=['GET', 'POST'])
def cron_check():
    """The same check as the timer, for a host that prefers cron. Bearer CRON_SECRET;
    CSRF-exempt in app.py because there is no session."""
    from meta_ads import cron_authorized
    if not cron_authorized():
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(run_check())


def register_cli(app):
    @app.cli.command('cost-monitor-check')
    def cost_monitor_check_cmd():
        """Read the Google Cloud VMs and send any cost warning that is due. For cron:
        `flask cost-monitor-check`."""
        import click
        s = run_check()
        if not s['enabled'] or s['error']:
            click.echo(f'ERR {s["error"]}')
        for r in s['running']:
            click.echo(f'on  {r["name"]} ({r["status"]}): {duration_label(r["hours"])}, '
                       f'est. {usd(r["est_rate"])}/h, est. {usd(r["est_run_cost"])} this run')
        if s['ok']:
            click.echo(f'{len(s["running"])} running, est. {usd(s["est_today"])} today, '
                       f'{len(s["sent"])} warning(s) sent.' + (f' {s["alert_error"]}' if s['alert_error'] else ''))
