"""
main.py  -  Day 21 Final: Research AI Agent (web server)
=========================================================
FastAPI app that serves the web UI and runs the agent.

Start locally (from this folder, virtual environment active):
    $env:OLLAMA_HOST = "http://localhost:11434"
    uvicorn main:app --reload
Then open http://localhost:8000   (Ctrl+C in the terminal to stop)

On a server: docker compose up -d --build   (see DEPLOY.md)

Endpoints:
    GET  /                     -> frontend/index.html (the web UI)
    GET  /static/...           -> frontend/app.js, style.css
    GET  /health               -> {"status": "healthy", "version": ...}   (is the server up?)
    POST /chat                 -> whole pipeline, one JSON answer at the end
    POST /export-excel         -> .xlsx of the tables behind a "/" answer
    GET  /research?topic=...   -> STREAMING: live progress events, then the report
                                  (this is what the web UI uses)
    GET  /company-data         -> what client data is loaded (counts + tables)

Two kinds of request:
    "Impact of solar energy"        -> web research (internet)
    "/ Total pledges by donor 2026" -> company research: ONLY the client's own
                                       documents and tables (data/company.db,
                                       loaded with ingest.py)

Protection for a shared server (all set by environment variables):
    One research at a time   a second request gets "busy, try again" (CPU model)
    RATE_LIMIT_PER_HOUR      research runs allowed per visitor (IP) per hour
    APP_PASSWORD             if set, the browser asks for a password first
    MAX_TOPIC_CHARS          longest topic accepted

Builds on Day 20 Deployment: FastAPI, lifespan, /health, run_in_threadpool,
rate limiting, timeouts.
"""

import asyncio
import base64
import json
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent import run_agent, research_agent, generate_report, wants_chart, MAX_STEPS
import company_db
import visits
from export_excel import build_workbook, MAX_ROWS as EXPORT_MAX_ROWS
from version import APP_VERSION
from schemas import ChatRequest, ChatResponse


# ---------------------------------------------------------------------
# Settings (environment variables; defaults suit local use)
# ---------------------------------------------------------------------

# Empty = no password (fine on your own PC). Set it on a public server.
APP_PASSWORD = os.getenv("APP_PASSWORD", "")

# How many research runs one visitor (IP address) may start per hour
RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "10"))

# Longest topic accepted (stops huge inputs filling the model's context)
MAX_TOPIC_CHARS = int(os.getenv("MAX_TOPIC_CHARS", "500"))


# ---------------------------------------------------------------------
# Shared-server guards
# ---------------------------------------------------------------------

# Only ONE research runs at a time: a CPU model can't serve two at once,
# and both would just get twice as slow.
research_lock = asyncio.Lock()

# ip -> times (seconds) of that visitor's recent runs, oldest first
_recent_runs = defaultdict(deque)


def rate_limited(ip):
    """True if this IP already started RATE_LIMIT_PER_HOUR runs in the last hour."""

    now = time.time()
    runs = _recent_runs[ip]

    # Forget runs older than one hour
    while runs and now - runs[0] > 3600:
        runs.popleft()

    if len(runs) >= RATE_LIMIT_PER_HOUR:
        return True

    runs.append(now)
    return False


def client_ip(request):
    return request.client.host if request.client else "unknown"


def parse_request(text):
    """
    "/ question" -> ("company", "question")    the client's own data
    "question"   -> ("web", "question")        internet research
    """

    text = text.strip()

    if text.startswith("/"):
        return "company", text.lstrip("/").strip()

    return "web", text


NO_COMPANY_DATA = (
    "No company data is loaded yet. Import the client's files first "
    "(python ingest.py data/inbox), then ask again with \"/\" in front."
)


def run_research_job(topic, progress_callback, mode="web"):
    """
    The whole web-UI job, run on ONE worker thread so the lock covers it all:
    research (plan + loop + verify), then the report from verified findings.
    Returns (report_text, charts, tables).
    """

    session = research_agent(topic, MAX_STEPS, progress_callback, mode)

    # Charts only work on the organization's own data ("/" questions)
    chart_note = ""
    if mode == "web" and wants_chart(topic):
        chart_note = (
            "Note: charts are only available for your own data. "
            "Start your question with / to get a chart, "
            "e.g. / Chart our donor churn rate by year\n\n"
        )

    # Charts made by create_chart (company mode only). Their numbers come
    # straight from the database, so they are sent to the page as they are.
    charts = session.charts

    # Data for "Download as Excel": each chart's query and each database query
    # the agent ran, re-run with a higher row limit (still read-only)
    tables = export_tables(session) if mode == "company" else []

    # research_agent only GATHERS findings.
    # Step 4 SYNTHESIZE: build the structured report from the verified ones.
    verified_notes = session.verified_notes()

    if not verified_notes:
        return chart_note + (
            "No verified findings were collected within "
            f"{MAX_STEPS} steps. Try again or raise MAX_STEPS."
        ), charts, tables

    progress_callback({
        "type": "step",
        "message": f"Writing report from {len(verified_notes)} verified finding(s)"
    })

    return chart_note + generate_report(topic, verified_notes), charts, tables


def export_tables(session):
    """
    The tables behind a "/" answer, for the Download as Excel button:
    [{"title", "sql", "columns", "rows"}], one per distinct query.
    """
    jobs = [(chart["title"], chart["sql"]) for chart in session.charts]
    jobs += [(f"Query {i}", sql) for i, sql in enumerate(session.queries, start=1)]

    tables, seen = [], set()
    for title, sql in jobs:
        key = " ".join(sql.split()).lower()
        if not sql or key in seen:
            continue
        seen.add(key)
        try:
            result = company_db.run_select(sql, max_rows=EXPORT_MAX_ROWS)
        except Exception:
            continue
        if result["rows"]:
            tables.append({"title": title, "sql": sql,
                           "columns": result["columns"], "rows": result["rows"]})
    return tables


# ---------------------------------------------------------------------
# App
# ---------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code before "yield" runs once at startup, after "yield" once at shutdown.
    print("Starting Research AI application")
    print(f"Password protection: {'on' if APP_PASSWORD else 'off'}")
    print(f"Visitor log page (/admin/visits): {'on' if visits.ADMIN_PASSWORD else 'off - set ADMIN_PASSWORD'}")
    yield
    print("Shutting down Research AI application")


app = FastAPI(
    title="Research AI Agent",
    version=APP_VERSION,
    lifespan=lifespan,
)

# (No CORS middleware: the web UI is served from this same server, so
#  other websites don't need to call this API from their pages.)


@app.middleware("http")
async def no_stale_pages(request: Request, call_next):
    """
    Make browsers check for a newer page, script or stylesheet on every load
    (cheap: unchanged files come back as "304 Not Modified"). Without this,
    a browser can keep showing an old version after an update.
    """
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def password_check(request: Request, call_next):
    """
    Optional password (HTTP Basic auth). The browser shows its own login box;
    any user name works, the password must match APP_PASSWORD.
    /health stays open so Docker's healthcheck works.
    NOTE: over plain http:// the password is not encrypted - add HTTPS for real use.
    """

    # /admin pages check their own password (ADMIN_PASSWORD) in the route
    if APP_PASSWORD and request.url.path != "/health" and not request.url.path.startswith("/admin"):

        ok = False
        header = request.headers.get("authorization", "")

        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                _, _, password = decoded.partition(":")
                ok = secrets.compare_digest(password, APP_PASSWORD)
            except Exception:
                ok = False

        if not ok:
            # Someone typed a wrong password (not just the first, empty request)
            if header:
                visits.log_event(request, "login_failed", path=request.url.path)
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Research AI"'},
                content="Password required.",
            )

    return await call_next(request)


# Serve the frontend folder at /static (index.html loads /static/app.js etc.)
app.mount(
    "/static",
    StaticFiles(directory="frontend"),
    name="static",
)


@app.get("/")
async def home(request: Request):
    visits.log_event(request, "visit")
    # Put the app version into the page (see version.py)
    with open("frontend/index.html", encoding="utf-8") as f:
        page = f.read().replace("{{APP_VERSION}}", APP_VERSION)
    return Response(page, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.post("/export-excel")
async def export_excel(request: Request):
    """
    Download as Excel: the page posts {"question", "tables"} from the answer
    it received and gets back an .xlsx file (built in export_excel.py).
    """
    try:
        body = await request.json()
        question = str(body.get("question", ""))[:500]
        tables = body.get("tables") or []
        if not isinstance(tables, list) or not tables:
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="No table data to export.")

    data = await run_in_threadpool(build_workbook, question, tables)
    name = "research_export_" + time.strftime("%Y%m%d_%H%M") + ".xlsx"

    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _admin_login():
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Research AI admin"'},
        content="Admin password required.",
    )


@app.get("/admin/visits")
async def admin_visits(request: Request):
    """Visitor log: who opened the site and what they asked (visits.py)."""
    if not visits.ADMIN_PASSWORD:
        raise HTTPException(status_code=404, detail="Set ADMIN_PASSWORD to turn on this page.")
    if not visits.is_admin(request):
        return _admin_login()
    page = await run_in_threadpool(visits.admin_page)
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/admin/visits.csv")
async def admin_visits_csv(request: Request):
    if not visits.ADMIN_PASSWORD:
        raise HTTPException(status_code=404, detail="Set ADMIN_PASSWORD to turn on this page.")
    if not visits.is_admin(request):
        return _admin_login()
    data = await run_in_threadpool(visits.admin_csv)
    name = "visitor_log_" + time.strftime("%Y%m%d") + ".csv"
    return Response(data, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/health")
async def health():
    return {"status": "healthy", "version": APP_VERSION}


@app.get("/company-data")
async def company_data():
    """What client data is loaded (password-protected like the rest)."""

    if not company_db.has_data():
        return {"loaded": False, "documents": 0, "tables": 0}

    counts = company_db.document_summary()
    return {
        "loaded": True,
        **counts,
        "table_list": company_db.describe_tables().splitlines(),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request):
    """
    Non-streaming API: send {"message": "..."} and wait for the full report.
    Try it at http://localhost:8000/docs
    """

    mode, message = parse_request(request.message)

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    if mode == "company" and not company_db.has_data():
        raise HTTPException(status_code=400, detail=NO_COMPANY_DATA)

    if len(message) > MAX_TOPIC_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Message is longer than {MAX_TOPIC_CHARS} characters.",
        )

    if research_lock.locked():
        raise HTTPException(
            status_code=429,
            detail="Another research is running. Try again in a few minutes.",
        )

    if rate_limited(client_ip(http_request)):
        raise HTTPException(
            status_code=429,
            detail=f"Limit of {RATE_LIMIT_PER_HOUR} research runs per hour reached.",
        )

    async with research_lock:
        try:
            # run_agent is slow, blocking code (requests + ollama).
            # run_in_threadpool runs it on a worker thread so the server
            # stays responsive to other requests meanwhile.
            result = await run_in_threadpool(run_agent, message, mode)

            return ChatResponse(response=result)

        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))


@app.get("/research")
async def research(topic: str, request: Request):
    """
    Streaming endpoint used by the web UI.

    Uses Server-Sent Events (SSE): the response stays open and the server
    writes lines of the form   data: {...json...}\\n\\n   as things happen.
    frontend/app.js reads each event and adds a line to the progress list.

    How progress gets from the agent to the browser:
        research_agent (worker thread)
            -> emit(event) -> progress_callback(event)
            -> queue
            -> event_stream() reads queue -> yields "data: ..." -> browser

    A refusal (busy, rate limit, bad topic) is sent as a "complete" event,
    so the existing web UI shows it in the report area.
    """

    mode, topic = parse_request(topic)
    ip = client_ip(request)

    queue = asyncio.Queue()

    # The event loop that runs this request. Needed because the agent runs
    # on a DIFFERENT thread, and asyncio.Queue is not thread-safe.
    loop = asyncio.get_running_loop()

    def progress_callback(event):
        # Called from the worker thread -> hand the event to the event loop
        # safely instead of calling queue.put_nowait() directly.
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def sse(event):
        # Format one dict as a Server-Sent Event line
        return "data: " + json.dumps(event) + "\n\n"

    started = time.time()

    def log_question(outcome):
        visits.log_event(request, "question", mode=mode, question=topic[:500],
                         outcome=outcome, seconds=round(time.time() - started))

    def finish(text, charts=None, tables=None):
        # "charts" is a list of chart dicts (see tools.create_chart); the page draws them.
        # "tables" is the data behind the answer, for the Download as Excel button.
        return sse({"type": "complete", "report": text,
                    "charts": charts or [], "tables": tables or []})

    async def event_stream():

        # ---------- Checks before starting ----------
        if not topic:
            yield finish("Please enter a research topic.")
            return

        if mode == "company" and not company_db.has_data():
            log_question("refused: no company data")
            yield finish(NO_COMPANY_DATA)
            return

        if len(topic) > MAX_TOPIC_CHARS:
            log_question("refused: too long")
            yield finish(f"Please keep the topic under {MAX_TOPIC_CHARS} characters.")
            return

        if research_lock.locked():
            log_question("refused: busy")
            yield finish(
                "Another research is running right now. "
                "Please try again in a few minutes."
            )
            return

        if rate_limited(ip):
            log_question("refused: hourly limit")
            yield finish(
                f"You have reached the limit of {RATE_LIMIT_PER_HOUR} "
                "research runs per hour. Please try again later."
            )
            return

        # ---------- Run (one at a time) ----------
        # The lock is released when the BACKGROUND JOB ends, not when this
        # stream ends: if the visitor closes the tab, the job still finishes
        # on its thread, and nobody else can start one until it has.
        await research_lock.acquire()

        try:
            # Start the job in a background thread (it blocks while it works)
            task = asyncio.create_task(
                asyncio.to_thread(run_research_job, topic, progress_callback, mode)
            )
        except BaseException:
            research_lock.release()
            raise

        task.add_done_callback(lambda _: research_lock.release())

        # Forward events until the job is finished AND the queue is empty
        while True:

            if task.done() and queue.empty():
                break

            try:
                # Wait up to 0.5 s for an event, then loop to re-check task.done()
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
                yield sse(event)

            except asyncio.TimeoutError:
                continue

        charts, tables = [], []
        try:
            # Re-raises any exception from the job thread (e.g. Ollama not running)
            result, charts, tables = await task
            log_question("answered")

        except Exception as exc:
            # Show the problem in the UI instead of silently cutting the stream
            result = f"Error during research: {exc}"
            log_question(f"error: {str(exc)[:120]}")

        # Final event: the frontend shows "report" as the result, charts above it
        yield finish(result, charts, tables)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream"
    )
