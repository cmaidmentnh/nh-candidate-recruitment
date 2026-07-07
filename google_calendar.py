"""Google Calendar integration for candidate consult booking.

Talks to the Calendar REST API directly with `requests` (no google-api-python-client
dependency). Auth is a stored OAuth refresh token for the owner's calendar
(chris@maidmentnh.com), obtained once via the `gcloud auth application-default
login --scopes=...calendar` flow (see CONSULT_SETUP.md).

Env (portal .env):
  CAL_CLIENT_ID       OAuth client id
  CAL_CLIENT_SECRET   OAuth client secret
  CAL_REFRESH_TOKEN   refresh token for the owner
  CAL_CALENDAR_ID     calendar to read/write (default: primary)
  CAL_OWNER_EMAIL     owner's email (default: chris@maidmentnh.com)
"""
import os
import json
import time
import base64
import datetime as dt
from zoneinfo import ZoneInfo

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

CAL_SCOPE = "https://www.googleapis.com/auth/calendar"

TZ = ZoneInfo("America/New_York")
TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL_BASE = "https://www.googleapis.com/calendar/v3"

# Booking rules --------------------------------------------------------------
CONSULT_DAYS = {1, 2, 3}                    # Mon=0 -> Tue, Wed, Thu
WINDOWS = [(dt.time(10, 0), dt.time(15, 0)),  # 10:00a - 3:00p
           (dt.time(20, 0), dt.time(21, 0))]  # 8:00p - 9:00p
SLOT_STEP_MIN = 15                          # candidate start times land on :00/:15/:30/:45
LEAD_HOURS = 12                             # earliest a consult can be booked from now
HORIZON_DAYS = 21                           # how far out to offer slots
ALLOWED_DURATIONS = (15, 30)


def _cfg():
    return {
        # Service-account + domain-wide delegation (preferred on Workspace)
        "sa_keyfile": os.environ.get("CAL_SA_KEYFILE", ""),
        # OAuth refresh-token fallback
        "client_id": os.environ.get("CAL_CLIENT_ID", ""),
        "client_secret": os.environ.get("CAL_CLIENT_SECRET", ""),
        "refresh_token": os.environ.get("CAL_REFRESH_TOKEN", ""),
        "calendar_id": os.environ.get("CAL_CALENDAR_ID", "primary"),
        "owner_email": os.environ.get("CAL_OWNER_EMAIL", "chris@maidmentnh.com"),
    }


def is_configured():
    c = _cfg()
    return bool(c["sa_keyfile"] or (c["client_id"] and c["client_secret"] and c["refresh_token"]))


class CalendarError(RuntimeError):
    pass


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def _sa_access_token(keyfile, subject):
    """Domain-wide-delegation token: the service account impersonates `subject`."""
    try:
        with open(keyfile) as f:
            sa = json.load(f)
    except Exception as e:
        raise CalendarError(f"Could not read service-account key: {e}")
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iss": sa["client_email"], "scope": CAL_SCOPE,
              "aud": TOKEN_URL, "iat": now, "exp": now + 3600, "sub": subject}
    seg = _b64u(json.dumps(header).encode()) + b"." + _b64u(json.dumps(claims).encode())
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(seg, padding.PKCS1v15(), hashes.SHA256())
    assertion = (seg + b"." + _b64u(sig)).decode()
    r = requests.post(TOKEN_URL, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion}, timeout=15)
    if r.status_code != 200:
        raise CalendarError(f"Service-account token failed: {r.status_code} {r.text[:200]}")
    return r.json()["access_token"]


def _access_token():
    c = _cfg()
    if c["sa_keyfile"]:
        return _sa_access_token(c["sa_keyfile"], c["owner_email"])
    if c["client_id"] and c["client_secret"] and c["refresh_token"]:
        r = requests.post(TOKEN_URL, data={
            "client_id": c["client_id"], "client_secret": c["client_secret"],
            "refresh_token": c["refresh_token"], "grant_type": "refresh_token"}, timeout=15)
        if r.status_code != 200:
            raise CalendarError(f"Token refresh failed: {r.status_code} {r.text[:200]}")
        return r.json()["access_token"]
    raise CalendarError("Calendar not connected (set CAL_SA_KEYFILE or CAL_* refresh token).")


def _busy(time_min, time_max):
    """Return list of (start, end) tz-aware busy intervals from the owner's calendar."""
    c = _cfg()
    tok = _access_token()
    r = requests.post(f"{CAL_BASE}/freeBusy", headers={"Authorization": f"Bearer {tok}"},
                      json={"timeMin": time_min.isoformat(), "timeMax": time_max.isoformat(),
                            "timeZone": "America/New_York",
                            "items": [{"id": c["calendar_id"]}]}, timeout=20)
    if r.status_code != 200:
        raise CalendarError(f"freeBusy failed: {r.status_code} {r.text[:200]}")
    cal = r.json().get("calendars", {}).get(c["calendar_id"], {})
    out = []
    for b in cal.get("busy", []):
        out.append((dt.datetime.fromisoformat(b["start"]), dt.datetime.fromisoformat(b["end"])))
    return out


def _overlaps(s1, e1, intervals):
    for (s2, e2) in intervals:
        if s1 < e2 and s2 < e1:
            return True
    return False


def available_slots(duration_min, now=None, extra_busy=None):
    """Open start times (ISO 8601, tz-aware) for a consult of `duration_min`.

    `extra_busy` is a list of (start, end) tz-aware intervals to also exclude
    (e.g. already-pending consult requests held in the DB).
    """
    if duration_min not in ALLOWED_DURATIONS:
        raise ValueError("duration must be 15 or 30")
    now = now or dt.datetime.now(TZ)
    earliest = now + dt.timedelta(hours=LEAD_HOURS)
    horizon = now + dt.timedelta(days=HORIZON_DAYS)

    busy = _busy(now, horizon)
    if extra_busy:
        busy = busy + list(extra_busy)

    slots = []
    day = now.date()
    for _ in range(HORIZON_DAYS + 1):
        if day.weekday() in CONSULT_DAYS:
            for win_start, win_end in WINDOWS:
                cursor = dt.datetime.combine(day, win_start, TZ)
                window_end = dt.datetime.combine(day, win_end, TZ)
                while cursor + dt.timedelta(minutes=duration_min) <= window_end:
                    end = cursor + dt.timedelta(minutes=duration_min)
                    if cursor >= earliest and not _overlaps(cursor, end, busy):
                        slots.append(cursor.isoformat())
                    cursor += dt.timedelta(minutes=SLOT_STEP_MIN)
        day = day + dt.timedelta(days=1)
    return slots


def slot_is_open(start_iso, duration_min, extra_busy=None):
    """Re-check at approval time that a specific slot is still free and valid."""
    start = dt.datetime.fromisoformat(start_iso)
    if start.tzinfo is None:
        start = start.replace(tzinfo=TZ)
    return start.isoformat() in set(available_slots(duration_min, extra_busy=extra_busy))


def _extract_meet(ev):
    if ev.get("hangoutLink"):
        return ev["hangoutLink"]
    for ep in (ev.get("conferenceData") or {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            return ep.get("uri", "")
    return ""


def create_consult_event(start_iso, duration_min, candidate_name, candidate_email,
                         summary=None, description=""):
    """Create the calendar event with a Google Meet link and invite the candidate.

    Returns dict: {event_id, meet_link, html_link, start, end}.
    """
    c = _cfg()
    tok = _access_token()
    start = dt.datetime.fromisoformat(start_iso)
    if start.tzinfo is None:
        start = start.replace(tzinfo=TZ)
    end = start + dt.timedelta(minutes=duration_min)
    summary = summary or f"CTEHR consult — {candidate_name}"

    body = {
        "summary": summary,
        "description": description or f"{duration_min}-minute consult with {candidate_name}.",
        "start": {"dateTime": start.isoformat(), "timeZone": "America/New_York"},
        "end": {"dateTime": end.isoformat(), "timeZone": "America/New_York"},
        "attendees": [{"email": candidate_email, "displayName": candidate_name}],
        "reminders": {"useDefault": True},
        "conferenceData": {
            "createRequest": {
                "requestId": f"consult-{int(start.timestamp())}-{abs(hash(candidate_email)) % 100000}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    # sendUpdates=none: we do NOT let Google email its own guest invite, because a
    # service-account event's per-guest Meet link can be generated asynchronously and
    # diverge from the organizer's. We send our own .ics with the finalized link instead.
    r = requests.post(
        f"{CAL_BASE}/calendars/{c['calendar_id']}/events",
        headers={"Authorization": f"Bearer {tok}"},
        params={"conferenceDataVersion": 1, "sendUpdates": "none"},
        json=body, timeout=25)
    if r.status_code not in (200, 201):
        raise CalendarError(f"event insert failed: {r.status_code} {r.text[:300]}")
    ev = r.json()
    eid = ev.get("id")
    meet = _extract_meet(ev)
    status = ((ev.get("conferenceData") or {}).get("status") or {}).get("statusCode")
    # Conference creation is async — poll events.get until the Meet link is finalized
    # (status == "success"), so the link we store/email is the final, stable one.
    tries = 0
    while (not meet or status == "pending") and tries < 8:
        time.sleep(1.3)
        gr = requests.get(f"{CAL_BASE}/calendars/{c['calendar_id']}/events/{eid}",
                          headers={"Authorization": f"Bearer {tok}"},
                          params={"conferenceDataVersion": 1}, timeout=20)
        if gr.status_code == 200:
            ev = gr.json()
            meet = _extract_meet(ev)
            status = ((ev.get("conferenceData") or {}).get("status") or {}).get("statusCode")
        tries += 1
    return {"event_id": eid, "meet_link": meet, "conf_status": status,
            "html_link": ev.get("htmlLink", ""),
            "start": start.isoformat(), "end": end.isoformat()}
