"""
tools.py  -  Day 21 Final: Research AI Agent
=============================================
The "hands" of the agent. Every function the LLM is allowed to call lives here.

How the LLM sees these tools:
    agent.py passes these functions in `tools=[...]` to the model.
    The Ollama Python library reads each function's NAME, ARGUMENTS and
    DOCSTRING and turns them into a tool schema for the model.
    => The docstrings below are not just for humans: they are the
       instructions the model reads when deciding how to call a tool.

Tools exposed to the model (make_tools() builds the list):
    search_web         -> find candidate sources        (from Day 18)
    read_webpage       -> pull text from one source     (from Day 18)
    add_research_note  -> save a finding + its evidence
    get_research_notes -> look at what has been saved
    verify_finding     -> 2nd LLM call: does the evidence support the finding?

One ResearchSession per request:
    Each research run gets its OWN ResearchSession (its own notes list).
    make_tools(session) builds note/verify tools that only touch that session,
    so two people researching at the same time never mix their findings.

Builds on:
    Day 18 SearchWeb  - search_web / read_webpage, int(max_results), trimming page text
    Day 15 ProdReady  - idea of a separate "review" worker -> here: verify_finding
"""

import json

import requests
from bs4 import BeautifulSoup          # pip install beautifulsoup4  (HTML -> text)
from urllib.parse import urlparse, parse_qs

from llm import chat


# How many characters of a webpage to give back to the model.
# Every character goes into the conversation context and is re-read on
# every later step, so big pages make CPU inference crawl (Day 18 lesson).
MAX_PAGE_CHARS = 4000


# =====================================================================
# Web tools (no per-request state, so they are plain functions)
# =====================================================================

def search_web(query, max_results=5):
    """
    Search the web using DuckDuckGo.

    Args:
        query: Search query.
        max_results: Maximum number of results.

    Returns:
        A list of search results.
    """

    # Small models often send numbers as strings ("5") -> force int (Day 18 fix)
    max_results = int(max_results)

    # DuckDuckGo's plain-HTML endpoint: no API key needed
    response = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={
            "User-Agent": "Mozilla/5.0"   # some sites block requests without a browser UA
        },
        timeout=20
    )

    response.raise_for_status()   # turn HTTP 4xx/5xx into an exception

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    results = []

    # Each search hit is a <div class="result"> - take the first N
    for result in soup.select(
        ".result"
    )[:max_results]:

        title_element = result.select_one(
            ".result__title"
        )

        link_element = result.select_one(
            ".result__a"
        )

        snippet_element = result.select_one(
            ".result__snippet"
        )

        if not link_element:
            continue   # ads / malformed rows have no link

        results.append({
            "title": (
                title_element.get_text(
                    " ",
                    strip=True
                )
                if title_element
                else ""
            ),
            # NOTE: this is usually a DuckDuckGo redirect link
            # (//duckduckgo.com/l/?uddg=<real url>). read_webpage unwraps it.
            "url": link_element.get(
                "href",
                ""
            ),
            "snippet": (
                snippet_element.get_text(
                    " ",
                    strip=True
                )
                if snippet_element
                else ""
            )
        })

    return results


def read_webpage(url):
    """
    Download a webpage and extract its text.

    Args:
        url: Webpage URL.

    Returns:
        Extracted webpage text or an error message.
    """

    # DuckDuckGo links start with "//" (no scheme) -> add https:
    if url.startswith("//"):
        url = "https:" + url

    # Extract the real URL from the DuckDuckGo redirect URL.
    # The real address is in the "uddg" query parameter.
    parsed_url = urlparse(url)

    if "duckduckgo.com" in parsed_url.netloc:
        params = parse_qs(parsed_url.query)

        if "uddg" in params:
            url = params["uddg"][0]

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=20
        )

        response.raise_for_status()

    except requests.exceptions.RequestException as e:

        # Return the error as data instead of crashing, so the model
        # can read it and choose another source.
        return {
            "status": "error",
            "url": url,
            "error": str(e)
        }

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    # Remove non-content tags before extracting text
    for element in soup(
        ["script", "style", "noscript"]
    ):
        element.decompose()

    text = soup.get_text(
        "\n",
        strip=True
    )

    # Trim: keeps the context small so the next steps stay fast (Day 18 fix).
    return text[:MAX_PAGE_CHARS]


# =====================================================================
# Per-request state
# =====================================================================

class ResearchSession:
    """
    Everything ONE research request collects.
    agent.py creates a new one for every request.
    """

    def __init__(self):
        # Each note: {"source", "finding", "evidence", "verified": bool}
        self.notes = []

        # The model's last message (set by agent.py when it finishes)
        self.final_message = ""

    def verified_notes(self):
        """Only the notes verify_finding approved (used for the report)."""
        return [
            note.copy()
            for note in self.notes
            if note.get("verified", False)
        ]

    def mark_verified(self, source, finding):
        """Mark the matching note as verified. Returns "verified" or "not_found"."""

        # 1st try: exact match on source AND finding
        for note in self.notes:
            if note["source"] == source and note["finding"] == finding:
                note["verified"] = True
                return "verified"

        # 2nd try: the model often re-types the source slightly differently
        # (e.g. drops "https://"), so fall back to matching the finding text only.
        wanted = finding.strip().lower()

        for note in self.notes:
            if note["finding"].strip().lower() == wanted:
                note["verified"] = True
                return "verified"

        return "not_found"


def make_tools(session):
    """
    Build the list of tools the model may call, bound to ONE session.

    The note/verify functions are defined inside this function, so each
    one "remembers" its own session (a closure). Their names and docstrings
    are what the model sees, exactly as before.
    """

    def add_research_note(
        source,
        finding,
        evidence
    ):
        """
        Store an important research finding.
    IMPORTANT:
        This tool accepts exactly three arguments:

        source:
            The URL or name of the source.

        finding:
            The specific claim or conclusion
            extracted from the source.

        evidence:
            The text or information from the source
            that supports the finding.

        Do not use title or summary.

        Returns:
            Confirmation message.
        """
        # (The docstring above is deliberately strict: small models tended to
        #  invent arguments like "title" or "summary", which made the call fail.)

        # Saved as NOT verified. Only verify_finding can flip it to True.
        session.notes.append({
            "source": source,
            "finding": finding,
            "evidence": evidence,
            "verified": False
        })

        return {
            "status": "stored",
            "finding": finding
        }

    def get_research_notes():
        """
        Return all research notes collected so far.
        """

        # .copy() so the caller can't accidentally edit the stored notes
        return [
            note.copy()
            for note in session.notes
        ]

    def verify_finding(
        source,
        finding,
        evidence
    ):
        """
        Check whether evidence supports a finding.
        """

        # A separate LLM call acting as a "reviewer" (like Day 15's review worker).
        # format="json" forces Ollama to return valid JSON we can parse.
        # /no_think stops qwen3 from writing a long reasoning block first (faster).
        response = chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "/no_think "
                        "You are an evidence verification assistant. "
                        "Determine whether the supplied evidence "
                        "supports the supplied finding. "
                        "Return JSON with exactly two fields: "
                        "supported and explanation. "
                        "supported must be true or false."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Source:\n{source}\n\n"
                        f"Finding:\n{finding}\n\n"
                        f"Evidence:\n{evidence}"
                    )
                }
            ],
            format="json"
        )

        result = json.loads(
            response.message.content
        )

        # Only a "supported": true verdict marks the stored note as verified.
        # .get() so a missing key counts as "not supported" instead of crashing.
        if result.get("supported") is True:

            # Tell the model whether a stored note was actually found.
            # "not_found" means it must call add_research_note first.
            result["note_status"] = session.mark_verified(
                source,
                finding
            )

        return result

    return [
        search_web,
        read_webpage,
        add_research_note,
        get_research_notes,
        verify_finding
    ]
