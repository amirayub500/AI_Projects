"""
main.py  -  Day 21 Final: Research AI Agent (web server)
=========================================================
FastAPI app that serves the web UI and runs the agent.

Start (from the Day_21_Final folder, shared root .venv active):
    $env:OLLAMA_HOST = "http://localhost:11434"
    $env:OLLAMA_BASE_URL = "http://localhost:11434"
    uvicorn main:app --reload
Then open http://localhost:8000   (Ctrl+C in the terminal to stop)

Endpoints:
    GET  /                     -> frontend/index.html (the web UI)
    GET  /static/...           -> frontend/app.js, style.css
    GET  /health               -> {"status": "healthy"}   (is the server up?)
    POST /chat                 -> whole pipeline, one JSON answer at the end
    GET  /research?topic=...   -> STREAMING: live progress events, then the report
                                  (this is what the web UI uses)

Builds on Day 20 Deployment: FastAPI, lifespan, /health, run_in_threadpool.
(Day 20 also had /ready, rate limiting and a timeout - not used here.)
"""

from contextlib import asynccontextmanager

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent import run_agent, research_agent, generate_report, MAX_STEPS
from tools import get_verified_research_notes
from schemas import ChatRequest, ChatResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code before "yield" runs once at startup, after "yield" once at shutdown.
    # (Day 20 built the agent here; this agent needs no setup.)
    print("Starting Research AI application")
    yield
    print("Shutting down Research AI application")


app = FastAPI(
    title="Research AI Agent",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: lets a page from another origin call this API.
# "*" = allow everyone - fine for local learning, too open for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
async def chat(request: ChatRequest):
    """
    Non-streaming API: send {"message": "..."} and wait for the full report.
    Try it at http://localhost:8000/docs
    """

    if not request.message.strip():
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty."
        )

    try:
        # run_agent is slow, blocking code (requests + ollama).
        # run_in_threadpool runs it on a worker thread so the server
        # stays responsive to other requests meanwhile.
        result = await run_in_threadpool(run_agent, request.message)

        return ChatResponse(
            response=result
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )


@app.get("/research")
async def research(topic: str):
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
    """

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

    async def event_stream():

        # Start the agent in a background thread (it blocks while it works).
        # Arguments: research_agent(topic, max_steps, progress_callback)
        task = asyncio.create_task(
            asyncio.to_thread(
                research_agent,
                topic,
                MAX_STEPS,          # step cap - set once in agent.py
                progress_callback
            )
        )

        # Forward events until the agent is finished AND the queue is empty
        while True:

            if task.done() and queue.empty():
                break

            try:

                # Wait up to 0.5 s for an event, then loop to re-check task.done()
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=0.5
                )

                yield sse(event)

            except asyncio.TimeoutError:

                continue

        try:

            # Re-raises any exception from the agent thread (e.g. Ollama not running)
            await task

            # research_agent only GATHERS findings.
            # Step 4 SYNTHESIZE: build the structured report from the verified ones.
            verified_notes = get_verified_research_notes()

            if verified_notes:

                yield sse({
                    "type": "step",
                    "message": (
                        f"Writing report from "
                        f"{len(verified_notes)} verified finding(s)"
                    )
                })

                result = await asyncio.to_thread(
                    generate_report,
                    topic,
                    verified_notes
                )

            else:

                result = (
                    "No verified findings were collected within "
                    f"{MAX_STEPS} steps. Try again or raise MAX_STEPS in agent.py."
                )

        except Exception as exc:

            # Show the problem in the UI instead of silently cutting the stream
            result = f"Error during research: {exc}"

        # Final event: the frontend shows "report" as the result
        yield sse({
            "type": "complete",
            "report": result
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream"
    )
