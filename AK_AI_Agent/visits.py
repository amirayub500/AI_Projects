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
    OWNER_IPS        your own IP addresses, comma separated, hidden as "You"   optional

The admin page groups the log into PEOPLE (one row per browser, via the ak_vid
cookie set on the first page load) and keeps bots, link previews, your own
visits and direct-IP traffic out of the main list. Opening the admin page
through the domain (https://agent.../admin/visits) also teaches it which
browser and IP are yours.
"""

import base64
import csv
import io
import ipaddress
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from urllib.parse import unquote

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


LOG_PATH = os.path.join("data", "visits.jsonl")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
LOG_TIMEZONE = os.getenv("LOG_TIMEZONE", "America/New_York")
OWNER_IPS = {ip.strip() for ip in os.getenv("OWNER_IPS", "").split(",") if ip.strip()}
OWNER_PATH = os.path.join("data", "owner.json")

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
    """
    The visitor's real IP address.

    The site sits behind Cloudflare, so the app itself only sees Cloudflare's
    hop (an internal Docker address such as 172.18.0.1). Cloudflare puts the
    visitor's address in the CF-Connecting-IP header. We trust that header ONLY
    when the request came from an internal address - someone hitting the server
    directly from the internet can't fake it.
    """
    peer = request.client.host if request.client else ""
    try:
        internal = ipaddress.ip_address(peer).is_private
    except ValueError:
        internal = False
    if internal:
        for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
            value = request.headers.get(header, "")
            if value:
                return value.split(",")[0].strip()[:64]
    return peer or "unknown"


def _cookie(request, name, limit=80):
    """A value the welcome box (frontend/visitor-hello.js) saved in a cookie."""
    try:
        return unquote(request.cookies.get(name, "")).strip()[:limit]
    except Exception:
        return ""


def log_event(request, kind, **fields):
    """
    Append one event. kind = visit | login_failed | question | intro | intro_skipped.
    Never raises: logging must not break the app.

    name / org  - what the visitor typed in the welcome box (saved in a cookie)
    ref         - the tag from a personal link, e.g. ?ref=sarah
    """
    try:
        user, _ = basic_auth_user(request)
        ref = (request.query_params.get("ref", "") or _cookie(request, "ak_ref")).strip()[:60]
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": kind,
            "ip": client_ip(request),
            "user": user,
            "name": _cookie(request, "ak_name"),
            "org": _cookie(request, "ak_org", 120),
            "ref": ref,
            "vid": _cookie(request, "ak_vid", 40),
            "host": request.headers.get("host", "")[:80],
            "browser": request.headers.get("user-agent", "")[:200],
        }
        # Values passed in (e.g. the name sent with the welcome box) win
        record.update({k: v for k, v in fields.items() if v not in (None, "")})
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
    return bool(password) and secrets.compare_digest(password, ADMIN_PASSWORD)


def new_visitor_id():
    return secrets.token_hex(8)


# ---------------------------------------------------------------------
# "That's me": the admin's own browser and IP are hidden from the people list
# ---------------------------------------------------------------------

def _load_owner():
    try:
        with open(OWNER_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("vids", [])), set(data.get("ips", []))
    except Exception:
        return set(), set()


def remember_owner(request):
    """Called when the admin page is opened: remember this browser and IP as yours."""
    try:
        vids, ips = _load_owner()
        vid = _cookie(request, "ak_vid", 40)
        ip = client_ip(request)
        try:
            real_ip = not ipaddress.ip_address(ip).is_private
        except ValueError:
            real_ip = False
        changed = False
        if vid and vid not in vids:
            vids.add(vid); changed = True
        if real_ip and ip not in ips:
            ips.add(ip); changed = True
        if changed:
            os.makedirs(os.path.dirname(OWNER_PATH), exist_ok=True)
            with _lock, open(OWNER_PATH, "w", encoding="utf-8") as f:
                json.dump({"vids": sorted(vids), "ips": sorted(ips)}, f)
    except Exception as exc:
        print(f"Owner note error: {exc}")


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


# Bots, crawlers, scanners and link previews. Real browsers always say
# "Gecko" or "WebKit" in their user agent; anything without them is a tool.
_BOT_WORDS = re.compile(
    r"bot|crawl|spider|slurp|preview|facebookexternalhit|embedly|whatsapp|telegram|"
    r"linkedin|skype|discord|slack|python|curl|wget|go-http|headless|monitor|uptime|"
    r"scan|httpclient|java/|okhttp|libwww|zgrab|nmap|masscan|censys|expanse|genome",
    re.I)


def _is_bot(agent):
    a = (agent or "").lower()
    if not a or _BOT_WORDS.search(a):
        return True
    return not ("gecko" in a or "webkit" in a)


def _is_direct(event):
    """Opened through the raw server IP (or logged before the real-IP fix)."""
    host = (event.get("host") or "").split(":")[0]
    if host and re.fullmatch(r"[\d.]+", host):
        return True
    try:
        return ipaddress.ip_address(event.get("ip", "")).is_private
    except ValueError:
        return False


def _person_key(e):
    return e.get("vid") or f"{e.get('ip', '')}|{e.get('browser', '')}"


def group_people(events):
    """
    events (newest first) -> (people, hidden) where people is a list of dicts,
    newest activity first, and hidden counts what was filtered out.
    """
    owner_vids, owner_ips = _load_owner()
    owner_ips |= OWNER_IPS

    groups = {}
    for e in reversed(events):  # oldest first, so "first seen" is easy
        groups.setdefault(_person_key(e), []).append(e)

    people = []
    hidden = {"you": 0, "bots": 0, "direct": 0}
    for key, evs in groups.items():
        last = evs[-1]
        if any(e.get("vid") in owner_vids or e.get("ip") in owner_ips for e in evs):
            hidden["you"] += len(evs)
            continue
        if _is_bot(last.get("browser", "")):
            hidden["bots"] += len(evs)
            continue
        if all(_is_direct(e) for e in evs):
            hidden["direct"] += len(evs)
            continue
        name = next((e["name"] for e in reversed(evs) if e.get("name")), "")
        org = next((e["org"] for e in reversed(evs) if e.get("org")), "")
        ref = next((e["ref"] for e in reversed(evs) if e.get("ref")), "")
        people.append({
            "name": name, "org": org, "ref": ref,
            "first": evs[0].get("time", ""), "last": last.get("time", ""),
            "visits": sum(1 for e in evs if e.get("kind") == "visit"),
            "questions": [e for e in evs if e.get("kind") == "question"],
            "ip": next((e["ip"] for e in reversed(evs) if not _is_direct(e)), last.get("ip", "")),
            "device": _browser(last.get("browser", "")),
        })
    people.sort(key=lambda p: p["last"], reverse=True)
    return people, hidden


_STYLE = """
body{font-family:system-ui,sans-serif;margin:24px;color:#1f2933;background:#fafafa}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:26px 0 10px}
.sub{color:#6b7280;margin-bottom:20px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 16px;min-width:140px}
.card b{display:block;font-size:24px} .card span{color:#6b7280;font-size:13px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:10px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #eef0f2;vertical-align:top;font-size:14px}
th{font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.03em}
.t{white-space:nowrap;color:#374151} .m{color:#6b7280;font-size:12px}
.q{margin:2px 0} .k{font-size:12px;padding:2px 8px;border-radius:999px;background:#eef2ff;color:#3730a3}
tr.question .k{background:#ecfdf5;color:#065f46} tr.intro .k{background:#fff7ed;color:#9a3412}
tr.login_failed .k{background:#fef2f2;color:#991b1b}
.r{display:inline-block;margin-top:3px;font-size:11px;padding:1px 7px;border-radius:999px;background:#f3f4f6;color:#374151}
.none{color:#9ca3af;font-style:italic}
details{margin-top:18px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 14px}
summary{cursor:pointer;font-weight:600}
a{color:#2563eb}
"""


def _page(title, sub, body):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{_STYLE}</style></head><body>
<h1>{title}</h1><div class="sub">{sub}</div>
{body}
</body></html>"""


def _ip_link(ip):
    ip = _esc(ip)
    return f"<a href='https://ipinfo.io/{ip}' target='_blank' rel='noopener'>{ip}</a>"


def admin_page(view="people"):
    events = read_events(limit=20000)
    if view == "events":
        return _events_page(events)

    people, hidden = group_people(events)
    named = [p for p in people if p["name"]]
    unnamed = [p for p in people if not p["name"]]
    asked = sum(len(p["questions"]) for p in people)

    def person_row(p):
        who = (f"<b>{_esc(p['name'])}</b>" if p["name"]
               else "<span class='none'>No name given</span>")
        if p["org"]:
            who += f"<div class='m'>{_esc(p['org'])}</div>"
        if p["ref"]:
            who += f"<div><span class='r'>link: {_esc(p['ref'])}</span></div>"
        qs = "".join(
            f"<div class='q'>{_esc(q.get('question', ''))}"
            f" <span class='m'>· {'own data' if q.get('mode') == 'company' else 'web'}"
            f" · {_esc(q.get('outcome', ''))} · {_local(q.get('time', ''))}</span></div>"
            for q in p["questions"]
        ) or "<span class='m'>No questions</span>"
        return (f"<tr><td>{who}</td>"
                f"<td class='t'>{_local(p['last'])}<div class='m'>first: {_local(p['first'])}</div></td>"
                f"<td class='t'>{p['visits']}</td><td>{qs}</td>"
                f"<td class='m'>{_ip_link(p['ip'])}<br>{_esc(p['device'])}</td></tr>")

    head = ("<tr><th>Person</th><th>Last seen</th><th>Visits</th>"
            "<th>Questions asked</th><th>Location / device</th></tr>")
    named_rows = "".join(person_row(p) for p in named) or \
        "<tr><td colspan='5' class='m'>Nobody has entered a name yet.</td></tr>"
    unnamed_rows = "".join(person_row(p) for p in unnamed)

    body = f"""
<div class="cards">
 <div class="card"><b>{len(named)}</b><span>people who gave their name</span></div>
 <div class="card"><b>{asked}</b><span>questions asked</span></div>
 <div class="card"><b>{len(unnamed)}</b><span>left without a name</span></div>
 <div class="card"><b>{sum(hidden.values())}</b><span>visits filtered out</span></div>
</div>
<h2>People</h2>
<table>{head}{named_rows}</table>
<details><summary>Visited but left without giving a name ({len(unnamed)})</summary>
<p class="m">Real browsers that saw the welcome box and closed the page, or tools that look like browsers.</p>
<table>{head}{unnamed_rows}</table></details>
<details><summary>Filtered out ({sum(hidden.values())} visits)</summary>
<p class="m">You: {hidden['you']} · Bots, crawlers and link previews: {hidden['bots']} ·
Direct-IP or older entries without a real address: {hidden['direct']}</p>
<p class="m">To hide your own visits, open this page once at
<b>https://agent.akdatasolutions.com/admin/visits</b> from each of your devices,
or set OWNER_IPS in the server's .env.</p></details>
"""
    sub = (f"Real visitors, newest first · times in {LOG_TIMEZONE} · "
           f"<a href='?view=events'>Every event</a> · <a href='/admin/visits.csv'>Download CSV</a>")
    return _page("Visitor log", sub, body)


def _events_page(events):
    labels = {"visit": "Opened site", "question": "Asked", "login_failed": "Wrong password",
              "intro": "Said hello", "intro_skipped": "Skipped welcome box"}
    rows = []
    for e in events[:500]:
        kind = e.get("kind", "")
        detail = ""
        if kind == "question":
            mode = "own data" if e.get("mode") == "company" else "web"
            detail = (f"<div class='q'>{_esc(e.get('question', ''))}</div>"
                      f"<div class='m'>{mode} · {_esc(e.get('outcome', ''))}</div>")
        who = f"<b>{_esc(e.get('name') or e.get('user') or '—')}</b>"
        if e.get("org"):
            who += f"<div class='m'>{_esc(e['org'])}</div>"
        tag = " · bot" if _is_bot(e.get("browser", "")) else (" · direct IP" if _is_direct(e) else "")
        rows.append(
            f"<tr class='{kind}'><td class='t'>{_local(e.get('time', ''))}</td><td>{who}</td>"
            f"<td><span class='k'>{labels.get(kind, kind)}</span>{detail}</td>"
            f"<td class='m'>{_ip_link(e.get('ip', ''))}<br>{_esc(_browser(e.get('browser', '')))}{tag}</td></tr>")
    body = ("<table><tr><th>When</th><th>Who</th><th>What they did</th><th>IP / device</th></tr>"
            + ("".join(rows) or "<tr><td colspan='4' class='m'>No visits logged yet.</td></tr>")
            + "</table>")
    sub = f"Every event, newest first (last 500) · <a href='/admin/visits'>Back to people</a>"
    return _page("Visitor log: every event", sub, body)


def admin_csv():
    out = io.StringIO()
    cols = ["time", "kind", "name", "org", "ref", "user", "ip", "mode", "question",
            "outcome", "seconds", "vid", "host", "browser"]
    writer = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for e in read_events(limit=100000):
        writer.writerow({**e, "time": _local(e.get("time", ""))})
    return out.getvalue()
