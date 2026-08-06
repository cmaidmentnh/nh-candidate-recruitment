"""Two-factor and lockout for admin accounts.

Same design as the donation platform's twofa.py, narrowed to what admin accounts
need: an authenticator app and printed backup codes. No SMS here — the users
table holds no phone number, and admin accounts belong to staff at other
organisations whose mobile numbers we have no business collecting.

Backup codes matter more than they look. Without them, "I got a new phone"
becomes a support request that ends with someone switching the second factor
off, and the requirement quietly becomes optional.
"""

import base64
import datetime
import hashlib
import hmac
import io
import json
import re
import secrets

import pyotp

ISSUER = "NH Candidate Recruitment"
BACKUP_CODE_COUNT = 10
# Escalating but always self-releasing. Five wrong passwords is a person
# mistyping; twenty is not.
LOCKOUT_STEPS = [(5, 5), (8, 15), (12, 60), (20, 360)]


def _now():
    return datetime.datetime.utcnow()


def _hash(value):
    return hashlib.sha256(("recruit2fa:" + (value or "")).encode()).hexdigest()


# ── lockout ──────────────────────────────────────────────────────────────

def lockout_minutes(failures):
    minutes = 0
    for threshold, mins in LOCKOUT_STEPS:
        if failures >= threshold:
            minutes = mins
    return minutes


def is_locked(locked_until):
    if not locked_until:
        return False, 0
    remaining = (locked_until - _now()).total_seconds()
    if remaining <= 0:
        return False, 0
    return True, int(remaining // 60) + 1


def register_failure(cur, user_id):
    cur.execute("""UPDATE users SET failed_logins = failed_logins + 1,
                          last_failed_login = NOW()
                    WHERE user_id = %s RETURNING failed_logins""", (user_id,))
    row = cur.fetchone()
    failures = row[0] if row else 0
    minutes = lockout_minutes(failures)
    if minutes:
        cur.execute("""UPDATE users
                          SET locked_until = NOW() + (%s || ' minutes')::interval
                        WHERE user_id = %s""", (str(minutes), user_id))
    return failures, minutes


def clear_failures(cur, user_id):
    cur.execute("UPDATE users SET failed_logins = 0, locked_until = NULL "
                "WHERE user_id = %s", (user_id,))


# ── TOTP ─────────────────────────────────────────────────────────────────

def new_secret():
    return pyotp.random_base32()


def qr_data_uri(secret, email):
    """QR inline, so the secret never travels to a third-party chart service."""
    import qrcode
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def totp_ok(secret, code):
    if not secret or not code:
        return False
    # One step either side: phone clocks drift, and a code typed as it rolls
    # over should not be rejected.
    return pyotp.TOTP(secret).verify(re.sub(r"\D", "", code), valid_window=1)


# ── backup codes ─────────────────────────────────────────────────────────

def new_backup_codes():
    plain = ["%s-%s" % (secrets.token_hex(2), secrets.token_hex(2))
             for _ in range(BACKUP_CODE_COUNT)]
    return plain, json.dumps([_hash(c) for c in plain])


def use_backup_code(stored_json, code):
    try:
        hashes = json.loads(stored_json or "[]")
    except ValueError:
        return False, stored_json
    target = _hash((code or "").strip().lower())
    for h in hashes:
        if hmac.compare_digest(h, target):
            hashes.remove(h)
            return True, json.dumps(hashes)
    return False, stored_json


def backup_codes_left(stored_json):
    try:
        return len(json.loads(stored_json or "[]"))
    except ValueError:
        return 0
