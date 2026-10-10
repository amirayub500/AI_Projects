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
    GET  /health               -> {"status": "healthy"}   (is the server up?)
    POST /chat                 -> whole pipeline, one JSON answer at the end
    GET  /research?topic=...   -> STREAMING: live progress events, then the report
                                  (this is what the web UI uses)

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
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent import run_agent, research_agent, generate_report, MAX_STEPS
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


def run_research_job(topic, progress_callback):
    """
    The whole web-UI job, run on ONE worker thread so the lock covers it all:
    research (plan + loop + verify), then the report from verified findings.
    """

    session = research_agent(topic, MAX_STEPS, progress_callback)

    # research_agent only GATHERS findings.
    # Step 4 SYNTHESIZE: build the structured report from the verified ones.
    verified_notes = session.verified_notes()

    if not verified_notes:
        return (
            "No verified findings were collected within "
            f"{MAX_STEPS} steps. Try again or raise MAX_STEPS."
        )

    progress_callback({
        "type": "step",
        "message": f"Writing report from {len(verified_notes)} verified finding(s)"
    })

    return generate_report(topic, verified_notes)


# ---------------------------------------------------------------------
# App
# ---------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code before "yield" runs once at startup, after "yield" once at shutdown.
    print("Starting Research AI application")
    print(f"Password protection: {'on' if APP_PASSWORD else 'off'}")
    yield
    print("Shutting down Research AI application")


app = FastAPI(
    title="Research AI Agent",
    version="1.1.0",
    lifespan=lifespan,
)

# (No CORS middleware: the web UI is served from this same server, so
#  other websites don't need to call this API from their pages.)


@app.middleware("http")
async def password_check(request: Request, call_next):
    """
    Optional password (HTTP Basic auth). The browser shows its own login box;
    any user name works, the password must match APP_PASSWORD.
    /health stays open so Docker's healthcheck works.
    NOTE: over plain http:// the password is not encrypted - add HTTPS for real use.
    """

    if APP_PASSWORD and request.url.path != "/health":

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
async def home():
    return FileResponse("frontend/index.html")


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request):
    """
    Non-streaming API: send {"message": "..."} and wait for the full report.
    Try it at http://localhost:8000/docs
    """

    message = request.message.strip()

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

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
            result = await run_in_threadpool(run_agent, message)

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

    topic = topic.strip()
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

    def finish(text):
        return sse({"type": "complete", "report": text})

    async def event_stream():

        # ---------- Checks before starting ----------
        if not topic:
            yield finish("Please enter a research topic.")
            return

        if len(topic) > MAX_TOPIC_CHARS:
            yield finish(f"Please keep the topic under {MAX_TOPIC_CHARS} characters.")
            return

        if research_lock.locked():
            yield finish(
                "Another research is running right now. "
                "Please try again in a few minutes."
            )
            return

        if rate_limited(ip):
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
                asyncio.to_thread(run_research_job, topic, progress_callback)
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

        try:
            # Re-raises any exception from the job thread (e.g. Ollama not running)
            result = await task

        except Exception as exc:
            # Show the problem in the UI instead of silently cutting the stream
            result = f"Error during research: {exc}"

        # Final event: the frontend shows "report" as the result
        yield finish(result)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream"
    )
