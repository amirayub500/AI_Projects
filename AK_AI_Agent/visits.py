"""
visits.py  -  who used the site, and what they asked
====================================================
Every page visit, failed login and research question is written as one
line of JSON to data/visits.jsonl. The data/ folder is outside the Docker
image, so the log survives rebuilds and restarts (and never goes to GitHub).

You read it at  /admin/visits  (a table, newest first) or download it as
/admin/visits.csv. Both need ADMIN_PASSWORD; if it is empty the pages are off.

    ADMIN_PASSWORD   password for /admin pages (any user name)   default: off
    LOG_TIMEZONE     time zone the admin page shows times in      default America/New_York
"""

import base64
import csv
import io
import json
import os
import threading
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


LOG_PATH = os.path.join("data", "visits.jsonl")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
LOG_TIMEZONE = os.getenv("LOG_TIMEZONE", "America/New_York")

_lock = threading.Lock()


def basic_auth_user(request):
    """(user name, password) from the browser's login box, or ("", "")."""
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return "", ""
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except Exception:
        return "", ""
    user, _, password = decoded.partition(":")
    return user.strip()[:80], password


def client_ip(request):
    return request.client.host if request.client else "unknown"


def log_event(request, kind, **fields):
    """
    Append one event. kind = visit | login_failed | question.
    Never raises: logging must not break the app.
    """
    try:
        user, _ = basic_auth_user(request)
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": kind,
            "ip": client_ip(request),
            "user": user,
            "browser": request.headers.get("user-agent", "")[:200],
            **fields,
        }
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with _lock, open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"Visit log error: {exc}")


def read_events(limit=2000):
    """Newest first."""
    if not os.path.exists(LOG_PATH):
        return []
    with _lock, open(LOG_PATH, encoding="utf-8") as f:
        lines = f.readlines()
    events = []
    for line in reversed(lines[-limit:]):
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def is_admin(request):
    if not ADMIN_PASSWORD:
        return False
    _, password = basic_auth_user(request)
    import secrets
    return bool(password) and secrets.compare_digest(password, ADMIN_PASSWORD)


# ---------------------------------------------------------------------
# Admin page
# ---------------------------------------------------------------------

def _local(iso):
    try:
        t = datetime.fromisoformat(iso)
        if ZoneInfo:
            t = t.astimezone(ZoneInfo(LOG_TIMEZONE))
        return t.strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return iso


def _browser(agent):
    a = agent.lower()
    device = "Phone" if ("iphone" in a or "android" in a and "mobile" in a) else "Computer"
    for name in ("Edg", "Chrome", "Firefox", "Safari"):
        if name.lower() in a:
            return f"{device}, {'Edge' if name == 'Edg' else name}"
    return device if agent else ""


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def admin_page():
    events = read_events()
    questions = [e for e in events if e.get("kind") == "question"]
    visits = [e for e in events if e.get("kind") == "visit"]
    people = {e.get("user") or e.get("ip") for e in visits + questions}
    failed = [e for e in events if e.get("kind") == "login_failed"]

    labels = {"visit": "Opened site", "question": "Asked", "login_failed": "Wrong password"}

    rows = []
    for e in events[:500]:
        kind = e.get("kind", "")
        detail = ""
        if kind == "question":
            mode = "own data" if e.get("mode") == "company" else "web"
            secs = e.get("seconds")
            extra = f" · {secs}s" if secs is not None else ""
            detail = (f"<div class='q'>{_esc(e.get('question', ''))}</div>"
                      f"<div class='m'>{mode} · {_esc(e.get('outcome', ''))}{extra}</div>")
        ip = _esc(e.get("ip", ""))
        rows.append(
            f"<tr class='{kind}'><td class='t'>{_local(e.get('time', ''))}</td>"
            f"<td><b>{_esc(e.get('user') or '—')}</b></td>"
            f"<td><span class='k'>{labels.get(kind, kind)}</span>{detail}</td>"
            f"<td class='m'><a href='https://ipinfo.io/{ip}' target='_blank' rel='noopener'>{ip}</a>"
            f"<br>{_esc(_browser(e.get('browser', '')))}</td></tr>"
        )

    body = "\n".join(rows) or "<tr><td colspan='4' class='m'>No visits logged yet.</td></tr>"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Visitor log</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;color:#1f2933;background:#fafafa}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#6b7280;margin-bottom:20px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px;min-width:130px}}
.card b{{display:block;font-size:24px}} .card span{{color:#6b7280;font-size:13px}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:10px}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid #eef0f2;vertical-align:top;font-size:14px}}
th{{font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.03em}}
.t{{white-space:nowrap;color:#374151}} .m{{color:#6b7280;font-size:12px}}
.q{{margin-top:4px}} .k{{font-size:12px;padding:2px 8px;border-radius:999px;background:#eef2ff;color:#3730a3}}
tr.question .k{{background:#ecfdf5;color:#065f46}} tr.login_failed .k{{background:#fef2f2;color:#991b1b}}
a{{color:#2563eb}}
</style></head><body>
<h1>Visitor log</h1>
<div class="sub">Newest first · times in {LOG_TIMEZONE} · <a href="/admin/visits.csv">Download CSV</a></div>
<div class="cards">
 <div class="card"><b>{len(people)}</b><span>people / devices</span></div>
 <div class="card"><b>{len(visits)}</b><span>site visits</span></div>
 <div class="card"><b>{len(questions)}</b><span>questions asked</span></div>
 <div class="card"><b>{len(failed)}</b><span>wrong passwords</span></div>
</div>
<table><tr><th>When</th><th>Name typed at login</th><th>What they did</th><th>IP / device</th></tr>
{body}
</table>
<p class="m">Click an IP to see its city and network (e.g. a company or university).</p>
</body></html>"""


def admin_csv():
    out = io.StringIO()
    cols = ["time", "kind", "user", "ip", "mode", "question", "outcome", "seconds", "browser"]
    writer = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for e in read_events(limit=100000):
        writer.writerow({**e, "time": _local(e.get("time", ""))})
    return out.getvalue()
