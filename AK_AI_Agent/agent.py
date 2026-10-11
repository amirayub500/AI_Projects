"""
agent.py  -  Day 21 Final: Research AI Agent
=============================================
The "brain". Runs the plan -> research -> verify -> synthesize pipeline.

    1. PLAN        create_research_plan()   one LLM call, no tools
    2. RESEARCH    research_agent() loop    LLM picks tools, Python runs them
    3. VERIFY      verify_finding tool      (inside the loop, see tools.py)
    4. SYNTHESIZE  generate_report()        one LLM call over VERIFIED notes only

Who calls what:
    main.py  /research (web UI, streaming) -> research_agent() then generate_report()
    main.py  /chat     (plain JSON API)    -> run_agent() which does both

The tool-calling loop (the core agent pattern from Days 15-19):
    send messages + tool list to the model
      -> model replies with tool_calls?   YES: run each tool, append result, loop
                                          NO : it wants to stop -> check verified notes
    stop when: verified notes exist and the model stops,
               OR max_steps is reached, OR the run's time limit is reached

Each call to research_agent() creates its own ResearchSession (tools.py),
so findings from different requests never mix. It returns that session.

Builds on:
    Day 15 ProdReady   - manager/worker split, step limits
    Day 16 LangGraph   - plan first, then act
    Day 18 SearchWeb   - the search/read tools, capping steps
    Day 19 LangChain   - bind_tools / create_agent did this loop for you;
                         here it is written by hand with ollama.chat()
"""

import os
import time

# chat() calls Ollama with MODEL and a timeout (both set in llm.py)
from llm import chat

from tools import ResearchSession, make_tools
import company_db


# Hard cap on agent loop iterations.
# Each step = one model call + any tools it requests.
# The full chain (search -> read -> add note -> verify) needs ~4 tool calls,
# so 5 steps leaves one spare. The "steps left" reminder starts at step 4.
# If runs end with "No verified research findings", raise this number.
# main.py imports this. On the server it can be set in the .env file.
MAX_STEPS = int(os.getenv("MAX_STEPS", "5"))

# Total seconds one research run may take before it stops looking and
# writes the report from what it has. Protects a shared server from a run
# that never ends. 900 s = 15 minutes.
RESEARCH_TIMEOUT = float(os.getenv("RESEARCH_TIMEOUT", "900"))


def generate_report(
    user_request,
    verified_notes
):
    """
    Generate a research report from
    verified research findings.
    (Step 4: SYNTHESIZE - no tools, only the verified notes as input.)
    """

    # Turn the list of note dicts into one block of plain text for the prompt
    research_text = "\n\n".join(
        [
            (
                f"Source: {note['source']}\n"
                f"Finding: {note['finding']}\n"
                f"Evidence: {note['evidence']}"
            )
            for note in verified_notes
        ]
    )

    response = chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "/no_think "
                    "You are a professional research "
                    "report writer. "
                    "Write a clear, structured report "
                    "using only the supplied verified "
                    "research findings. "
                    "Do not invent facts or sources. "
                    "If the available research is "
                    "insufficient for a section, say so."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Research request:\n"
                    f"{user_request}\n\n"
                    f"Verified research findings:\n"
                    f"{research_text}\n\n"
                    "Create a report with the following "
                    "sections:\n"
                    "1. Executive Summary\n"
                    "2. Introduction\n"
                    "3. Key Findings\n"
                    "4. Analysis\n"
                    "5. Limitations\n"
                    "6. Conclusion\n"
                    "7. Sources\n\n"
                    "List the sources used in the "
                    "verified findings. "
                    "Do not create URLs that were not "
                    "provided in the research findings."
                )
            }
        ]
    )

    return response.message.content


def research_agent(
    user_request,
    max_steps=MAX_STEPS, progress_callback=None,
    mode="web"
):
    """
    Run the autonomous research agent.

    The agent must collect and verify research
    findings before it is allowed to finish.
    It stops after max_steps loop iterations or RESEARCH_TIMEOUT seconds.

    progress_callback: optional function. main.py passes one that pushes
    each event dict onto a queue, which is streamed to the browser.

    mode: "web"     -> research the internet (search_web, read_webpage)
          "company" -> research the client's own data only
                       (search_documents, query_database); chosen when the
                       request starts with "/" (see main.py)

    Returns the ResearchSession: session.verified_notes() for the report,
    session.final_message for the model's last words.
    """

    def emit(message):
        # Send a progress event to the web UI (if anyone is listening).
        # Each dict has a "type": plan | step | tool | verification | tool_complete
        if progress_callback:
            progress_callback(message)

    # This request's own notes + the tools bound to them
    session = ResearchSession()
    tools = make_tools(session, mode)

    # Name -> Python function. When the model asks for a tool by name,
    # we look it up here and call it. Unknown names are reported back as errors.
    available_tools = {
        tool.__name__: tool
        for tool in tools
    }

    # Wall-clock deadline for the whole run
    deadline = time.monotonic() + RESEARCH_TIMEOUT

    # ---------------- 1. PLAN ----------------
    plan = create_research_plan(
        user_request,
        mode
    )

    print("\n" + "=" * 60)
    print("RESEARCH PLAN")
    print("=" * 60)
    print(plan)

    emit({
        "type": "plan",
        "message": "Research plan created",
        "plan": plan
    })

    # ---------------- 2. RESEARCH ----------------
    # The conversation the model sees. It grows every step:
    # model replies and tool results are appended to it.
    if mode == "company":
        system_prompt = company_system_prompt(max_steps)
    else:
        system_prompt = (
                "/no_think "
                "You are a research agent. "

                "Investigate the user's request using "
                "the available tools. "

                "Follow the research plan carefully. "

                f"You have only {max_steps} steps in total, "
                "so do not repeat searches. "

                "First search for relevant sources. "

                "After reading a useful source, "
                "extract important findings and call "
                "add_research_note to store them. "

                "After storing an important finding, "
                "use verify_finding to determine whether "
                "the evidence actually supports the finding. "

                "Do not finish the research with zero "
                "verified findings. "

                "Do not invent sources or evidence."
        )

    messages = [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": (
                f"Research request:\n{user_request}\n\n"
                f"Research plan:\n{plan}"
            )
        }
    ]

    for step in range(max_steps):

        # Out of time: stop researching, keep what was verified so far
        if time.monotonic() > deadline:

            print("\nTime limit reached.")

            emit({
                "type": "step",
                "step": step + 1,
                "message": (
                    f"Time limit reached ({int(RESEARCH_TIMEOUT)} s)."
                )
            })

            session.final_message = "Time limit reached."
            return session

        print(
            f"\n--- Agent Step {step + 1} of {max_steps} ---"
        )

        emit({
            "type": "step",
            "step": step + 1,
            "message": (
                f"Agent step {step + 1} of {max_steps}"
            )
        })

        # -------------------------------------------------
        # Budget nudge: near the end, tell the model to stop
        # searching and save/verify what it already has.
        # Without this, small models keep calling search_web.
        # -------------------------------------------------
        steps_left = max_steps - step

        if steps_left <= 2:
            messages.append({
                "role": "user",
                "content": (
                    f"You have {steps_left} step(s) left. "
                    "Do not search again. "
                    "Call add_research_note for your best "
                    "finding so far, then call verify_finding."
                )
            })

        # One model call. The model either answers in text
        # or returns tool_calls (which tools + which arguments).
        response = chat(
            messages=messages,
            tools=tools
        )

        # Keep the model's reply in the history so it remembers its own calls
        messages.append(
            response.message
        )

        # -------------------------------------------------
        # The LLM did not request a tool = it wants to finish.
        # Do NOT immediately accept this as completion.
        # First check whether verified findings exist.
        # -------------------------------------------------

        if not response.message.tool_calls:

            verified_notes = (
                session.verified_notes()
            )

            if verified_notes:

                print(
                    "\nResearch complete."
                )

                print(
                    "Verified findings collected:",
                    len(verified_notes)
                )

                session.final_message = response.message.content
                return session

            else:

                # Guardrail: refuse to finish with nothing verified.
                # Push it back into the loop (this still uses up a step).
                print(
                    "\nAgent attempted to finish "
                    "without verified findings."
                )

                print(
                    "Continuing research..."
                )

                messages.append({
                    "role": "user",
                    "content": (
                        "You attempted to finish without collecting "
                        "any verified research findings. "
                        "You MUST perform another research action now. "
                        "Do not provide a final answer. "
                        "Choose one of these actions:\n"
                        "1. Call search_web to find another source.\n"
                        "2. Call read_webpage to inspect a source.\n"
                        "3. Call add_research_note after extracting "
                        "a finding from a source.\n"
                        "4. Call verify_finding after storing a finding.\n"
                        "You must call a tool in your next response."
                    )
                })

                continue

        # -------------------------------------------------
        # Execute every tool requested by the model.
        # (One step can contain several tool calls.)
        # -------------------------------------------------

        for tool_call in (
            response.message.tool_calls
        ):

            tool_name = (
                tool_call.function.name
            )

            # Already a dict, e.g. {"query": "...", "max_results": 3}
            arguments = (
                tool_call.function.arguments
            )

            print(
                f"\nCalling tool: {tool_name}"
            )

            print(
                f"Arguments: {arguments}"
            )

            emit({
                "type": "tool",
                "tool": tool_name,
                "message": (
                    f"Using tool: {tool_name}"
                )
            })

            # ---------- 3. VERIFY (happens here, as a tool) ----------
            if tool_name == "verify_finding":

                emit({
                    "type": "verification",
                    "message": (
                        "Verifying research evidence"
                    )
                })

            tool_function = (
                available_tools.get(
                    tool_name
                )
            )

            if tool_function is None:

                # The model invented a tool name - tell it, don't crash
                result = (
                    f"Unknown tool: "
                    f"{tool_name}"
                )

            else:

                try:

                    # **arguments unpacks the dict into keyword arguments:
                    # search_web(query="...", max_results=3)
                    result = tool_function(
                        **arguments
                    )

                    emit({
                        "type": "tool_complete",
                        "tool": tool_name,
                        "message": (
                            f"Completed: {tool_name}"
                        )
                    })

                except Exception as e:

                    # Wrong arguments, network error, bad JSON...
                    # Send the error back to the model so it can adjust.
                    result = {
                        "status": "error",
                        "tool": tool_name,
                        "error": str(e)
                    }

                    print(
                        f"Tool error: {e}"
                    )

            # Tool result goes back into the conversation as role "tool"
            messages.append({
                "role": "tool",
                "content": str(result),
                "tool_name": tool_name
            })

    # Loop finished without the model choosing to stop.
    # The caller (main.py /research or run_agent) still checks for
    # verified notes and writes the report from whatever was collected.
    print(
        f"\nReached the step limit ({max_steps})."
    )

    emit({
        "type": "step",
        "step": max_steps,
        "message": (
            f"Step limit reached ({max_steps})."
        )
    })

    session.final_message = (
        "The agent reached its maximum "
        "number of steps before completing "
        "the research."
    )

    return session


def company_system_prompt(max_steps):
    """
    Instructions for company mode ("/" requests): use ONLY the client's data.
    The table list is read from the database each time, so newly imported
    spreadsheets are known to the model without code changes.
    """

    counts = company_db.document_summary()

    return (
        "/no_think "
        "You are a research agent for an organization. "
        "Answer ONLY from the organization's own data, using these tools: "
        "search_documents (letters, minutes, reports, policies) and "
        "query_database (spreadsheet tables, read-only SQLite SELECT). "
        "Do not use outside knowledge and do not invent facts.\n\n"

        f"Loaded: {counts['documents']} document(s) and {counts['tables']} table(s).\n"
        "Tables (name, rows, source, columns):\n"
        f"{company_db.describe_tables()}\n\n"

        "Tips: use query_database for amounts, counts, totals and dates "
        "(amounts are numbers; dates are 'YYYY-MM-DD' text, so use BETWEEN or "
        "comparisons). Use search_documents for names, topics and context. "
        "For a person or organization, check both.\n\n"

        f"You have only {max_steps} steps in total. "
        "After you find something relevant, call add_research_note with "
        "source = the file and page from search_documents, or the table name "
        "and _source_file/_row from query_database; "
        "evidence = the exact passage or the result rows. "
        "Then call verify_finding with the same source, finding and evidence. "
        "Do not finish with zero verified findings."
    )


def create_research_plan(
    user_request,
    mode="web"
):
    """
    Create a concise research plan.
    (Step 1: PLAN - one LLM call, no tools. The plan text is
     pasted into the research agent's first message.)
    """

    where = (
        "The research will use only the organization's own documents and "
        "data tables, not the internet. "
        if mode == "company" else ""
    )

    response = chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "/no_think "
                    "You are a research planning assistant. "

                    "Create a concise and ordered research "
                    "plan for the user's research request. "

                    "The plan should identify the main "
                    "research objectives that need to be "
                    "investigated. "

                    + where +
                    "Return at most 3 numbered research steps."
                )
            },
            {
                "role": "user",
                "content": user_request
            }
        ]
    )

    return response.message.content


def run_agent(request, mode="web"):
    """
    Whole pipeline in one blocking call (used by POST /chat).
    research -> check verified notes -> generate report.
    """

    session = research_agent(
        request,
        max_steps=MAX_STEPS,
        mode=mode
    )

    verified_notes = (
        session.verified_notes()
    )

    if not verified_notes:
        print(
            "\nNo verified research findings "
            "were collected."
        )
        # main.py turns this into an HTTP 500 with this message
        raise Exception(
            "No verified research findings were collected "
            f"within {MAX_STEPS} steps. Try raising MAX_STEPS in agent.py."
        )

    print(
        "\nVerified findings:",
        len(verified_notes)
    )

    report = generate_report(
        user_request=request,
        verified_notes=verified_notes
    )

    print("\n")
    print("=" * 60)
    print("FINAL RESEARCH REPORT")
    print("=" * 60)
    print("\n")

    print(report)
    return report
