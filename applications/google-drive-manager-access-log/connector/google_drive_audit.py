import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from ekaya import http

DEFAULT_EVENTS = "view,preview,download,access_item_content"
MAX_PAGES_PER_EVENT = 40
TOKEN_URL = "https://oauth2.googleapis.com/token"
REPORTS_URL = "https://admin.googleapis.com/admin/reports/v1/activity/users/{user}/applications/drive"

with open(os.environ["EKAYA_INPUT_FILE"], encoding="utf-8") as stream:
    request = json.load(stream)
if request["operation"] != "drive_access_events":
    raise ValueError("Expected drive_access_events")
params = request.get("params") or {}
limit = request["row_limit"]
if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
    raise ValueError("row_limit must be a positive integer")
deadline = time.monotonic() + request["timeout_s"] - 5


def remaining():
    left = deadline - time.monotonic()
    if left <= 1:
        raise ValueError("Ran out of time reading the audit log; narrow the date range.")
    return min(30, left)


def text(name):
    value = params.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def rfc3339(value, name):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})", value):
        raise ValueError(name + " must be an RFC 3339 time such as 2026-10-01T00:00:00Z")
    return value


events = [e.strip() for e in (text("events") or DEFAULT_EVENTS).split(",") if e.strip()]
if not events or len(events) > 10 or not all(re.fullmatch(r"[a-z_]{1,64}", e) for e in events):
    raise ValueError("events must be up to 10 audit event names such as view,download")
start = text("start_time") or (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
end = text("end_time")
rfc3339(start, "start_time")
if end:
    rfc3339(end, "end_time")
actor = text("actor_email")
if actor and not re.fullmatch(r"[^@\s/]+@[^@\s/]+", actor):
    raise ValueError("actor_email must be an email address")
exclude = (text("exclude_actor") or "").lower()
filters = []
doc_id = text("doc_id")
if doc_id:
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", doc_id):
        raise ValueError("doc_id is not a Drive file id")
    filters.append("doc_id==" + doc_id)
owner = text("owner")
if owner:
    if not re.fullmatch(r"[^,=<>]{1,128}", owner):
        raise ValueError("owner must be an email or a shared drive name")
    filters.append("owner==" + owner)

# Exchange the stored refresh token for a short-lived access token.
token_response = http.request(
    "POST",
    TOKEN_URL,
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    body=urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode("utf-8"),
    timeout=remaining(),
)
if token_response.status != 200:
    reason = ""
    try:
        reason = json.loads(token_response.body).get("error", "")
    except ValueError:
        pass
    raise ValueError("Google refused the stored refresh token (HTTP %d %s). The credentials need to be re-entered." % (token_response.status, reason))
access_token = json.loads(token_response.body)["access_token"]
headers = {"Authorization": "Bearer " + access_token, "x-goog-user-project": os.environ["GOOGLE_QUOTA_PROJECT"]}


def param_value(parameters, name):
    for item in parameters or []:
        if item.get("name") == name:
            if "value" in item:
                return item["value"]
            if "boolValue" in item:
                return item["boolValue"]
    return None


rows = []
for event_name in events:
    query = {"eventName": event_name, "maxResults": "1000", "startTime": start}
    if end:
        query["endTime"] = end
    if filters:
        query["filters"] = ",".join(filters)
    kept = 0
    token = None
    for page in range(MAX_PAGES_PER_EVENT):
        if token:
            query["pageToken"] = token
        response = http.request(
            "GET",
            REPORTS_URL.format(user=quote(actor or "all", safe="@")) + "?" + urlencode(query),
            headers=headers,
            timeout=remaining(),
        )
        if response.status != 200:
            message = ""
            try:
                message = json.loads(response.body).get("error", {}).get("message", "")
            except ValueError:
                pass
            raise ValueError("Google's audit log answered HTTP %d for %s: %s" % (response.status, event_name, message))
        document = json.loads(response.body)
        for item in document.get("items", []):
            who = item.get("actor") or {}
            email = who.get("email") or ""
            if exclude and email.lower() == exclude:
                continue
            kind = "person" if email else (str(who.get("key") or who.get("callerType") or "unknown").lower())
            app = (who.get("applicationInfo") or {}).get("applicationName") or ""
            for event in item.get("events", []):
                if event.get("name") != event_name:
                    continue
                p = event.get("parameters")
                rows.append({
                    "time": item["id"]["time"],
                    "actor_email": email,
                    "actor_kind": kind,
                    "app_name": app,
                    "event": event_name,
                    "doc_id": param_value(p, "doc_id") or "",
                    "doc_title": param_value(p, "doc_title") or "",
                    "doc_type": param_value(p, "doc_type") or "",
                    "owner": param_value(p, "owner") or "",
                    "visibility": param_value(p, "visibility") or "",
                })
                kept += 1
        token = document.get("nextPageToken")
        if not token or kept >= limit:
            break
    else:
        raise ValueError("The date range holds more %s events than one run can read. Narrow start_time and end_time." % event_name)

rows.sort(key=lambda r: r["time"], reverse=True)
with open(os.environ["EKAYA_OUTPUT_FILE"], "w", encoding="utf-8") as stream:
    for row in rows[:limit]:
        stream.write(json.dumps(row, allow_nan=False) + "\n")
