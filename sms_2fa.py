"""SMS second factor for admin accounts.

TOTP stays the default and the stronger option. SMS exists because it is the
factor people actually finish enrolling in, and an admin with SMS is better
than an admin with nothing when the thirty-day clock runs out.

Codes are six digits, stored only as a hash, good for ten minutes, five
attempts, and no more than one send a minute.
"""
import os
import re
import logging
import secrets
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_SECONDS = 60
SENDER_NAME = "CTEHR"


def normalize_phone(phone):
    """US number -> E.164. None if it is not a plausible US mobile."""
    if not phone:
        return None
    digits = re.sub(r'\D', '', str(phone))
    if len(digits) == 10:
        return f'+1{digits}'
    if len(digits) == 11 and digits.startswith('1'):
        return f'+{digits}'
    return None


def mask_phone(e164):
    """+16035551234 -> (***) ***-1234, for showing which number we will text."""
    if not e164:
        return ''
    d = re.sub(r'\D', '', e164)
    return f"(***) ***-{d[-4:]}" if len(d) >= 4 else ''


def _client():
    from twilio.rest import Client
    sid = os.environ.get('TWILIO_ACCOUNT_SID')
    key, sec = os.environ.get('TWILIO_API_KEY_SID'), os.environ.get('TWILIO_API_KEY_SECRET')
    if not sid:
        raise RuntimeError('TWILIO_ACCOUNT_SID not configured')
    if key and sec:
        return Client(key, sec, sid)
    return Client(sid, os.environ.get('TWILIO_AUTH_TOKEN'))


def send_sms(to_e164, body):
    """Send through the Messaging Service so the 10DLC registration applies."""
    msid = os.environ.get('TWILIO_MESSAGING_SERVICE_SID')
    kwargs = {'to': to_e164, 'body': body}
    if msid:
        kwargs['messaging_service_sid'] = msid
    else:
        kwargs['from_'] = os.environ.get('TWILIO_FROM_NUMBER')
    msg = _client().messages.create(**kwargs)
    return msg.sid


def generate_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def issue_code(cur, user_id, phone_e164):
    """Create a code, store its hash, text it. Returns (ok, message)."""
    cur.execute("SELECT sms_last_sent FROM users WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if row and row[0]:
        since = (datetime.utcnow() - row[0]).total_seconds()
        if since < RESEND_SECONDS:
            return False, f"A code was just sent. Wait {int(RESEND_SECONDS - since)} seconds."
    code = generate_code()
    try:
        send_sms(phone_e164, f"{SENDER_NAME}: your sign-in code is {code}. "
                             f"It expires in {CODE_TTL_MINUTES} minutes. "
                             f"If you did not ask for it, ignore this message.")
    except Exception:
        logger.exception("2FA SMS send failed for user %s", user_id)
        return False, "We could not send the text. Try again, or use an authenticator app."
    cur.execute("""UPDATE users
                      SET sms_code_hash = %s,
                          sms_code_expires = %s,
                          sms_code_attempts = 0,
                          sms_last_sent = NOW()
                    WHERE user_id = %s""",
                (generate_password_hash(code),
                 datetime.utcnow() + timedelta(minutes=CODE_TTL_MINUTES), user_id))
    return True, f"Code sent to {mask_phone(phone_e164)}."


def check_code(cur, user_id, submitted):
    """Verify a submitted code. Returns (ok, message). Burns the code on success."""
    submitted = re.sub(r'\D', '', submitted or '')
    cur.execute("""SELECT sms_code_hash, sms_code_expires, sms_code_attempts
                     FROM users WHERE user_id = %s""", (user_id,))
    row = cur.fetchone()
    if not row or not row[0]:
        return False, "No code outstanding. Ask for a new one."
    code_hash, expires, attempts = row
    if expires and datetime.utcnow() > expires:
        cur.execute("UPDATE users SET sms_code_hash = NULL WHERE user_id = %s", (user_id,))
        return False, "That code has expired. Ask for a new one."
    if attempts is not None and attempts >= MAX_ATTEMPTS:
        cur.execute("UPDATE users SET sms_code_hash = NULL WHERE user_id = %s", (user_id,))
        return False, "Too many attempts. Ask for a new code."
    if not check_password_hash(code_hash, submitted):
        cur.execute("""UPDATE users SET sms_code_attempts = COALESCE(sms_code_attempts,0) + 1
                        WHERE user_id = %s""", (user_id,))
        left = MAX_ATTEMPTS - (attempts or 0) - 1
        return False, f"That code is not right. {left} attempt{'' if left == 1 else 's'} left."
    cur.execute("""UPDATE users SET sms_code_hash = NULL, sms_code_expires = NULL,
                                    sms_code_attempts = 0
                    WHERE user_id = %s""", (user_id,))
    return True, "Verified."
