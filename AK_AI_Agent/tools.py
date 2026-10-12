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

  Web mode (a normal request):
    search_web         -> find candidate sources        (from Day 18)
    read_webpage       -> pull text from one source     (from Day 18)

  Company mode (a request starting with "/"):
    search_documents   -> keyword search in the client's imported documents
    query_database     -> read-only SQL on the client's spreadsheet tables
                          (both read data/company.db via company_db.py)

  Both modes:
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
import re

import requests
from bs4 import BeautifulSoup          # pip install beautifulsoup4  (HTML -> text)
from urllib.parse import urlparse, parse_qs

import company_db
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
# Charts (company mode)
# =====================================================================

def build_chart(sql, chart_type="bar", title=""):
    """
    Run a SELECT and turn the rows into a chart dict for the web page.
    First column = labels; the next number columns (up to 3) = data series.
    Returns (chart, None) or (None, "error message").
    """
    chart_type = str(chart_type or "bar").strip().lower()
    if chart_type not in ("bar", "line", "pie"):
        chart_type = "bar"

    try:
        result = company_db.run_select(sql)
    except Exception as exc:
        return None, str(exc)

    columns, rows = result["columns"], result["rows"]
    if not rows or len(columns) < 2:
        return None, "The query must return rows with a label column and at least one number column."

    def is_number(value):
        return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))

    # Number columns = columns after the first whose values are all numbers (or empty)
    numeric = [i for i in range(1, len(columns))
               if all(is_number(row[i]) for row in rows)
               and any(row[i] is not None for row in rows)]
    if not numeric:
        return None, "No number columns found. Put the label first and numbers after it."

    # Pie charts show a single series; bar/line up to 3
    numeric = numeric[:1] if chart_type == "pie" else numeric[:3]

    # Drop rows with no numbers at all (e.g. the first year has no churn yet)
    rows = [row for row in rows if any(row[i] is not None for i in numeric)]

    # One point is not a chart (e.g. the model filtered to a single year)
    if len(rows) < 2:
        return None, (
            "Only one row has data, so there is nothing to compare. "
            "Remove filters such as WHERE year = ... so the query returns "
            "every year or item, then call create_chart again."
        )

    chart = {
        "type": chart_type,
        "title": str(title or "").strip()[:120] or "Chart",
        "labels": ["" if row[0] is None else str(row[0]) for row in rows],
        "datasets": [
            {"label": columns[i],
             "data": [None if row[i] is None else round(float(row[i]), 2) for row in rows]}
            for i in numeric
        ],
        "source": "Company database query",
        "sql": sql.strip(),
        "truncated": result["truncated"],
        # The rows behind the chart (also used for the model and the report)
        "columns": [columns[0]] + [columns[i] for i in numeric],
        "rows": [[row[0]] + [row[i] for i in numeric] for row in rows],
    }
    return chart, None


def ready_made_chart(user_request):
    """
    Common chart questions on donor data, answered with a fixed, tested query
    (no model needed, so the chart is the same every time).
    Returns (sql, chart_type, title) or None when the question is not covered
    or the donor views do not exist.
    """
    text = user_request.lower()

    try:
        views = {row[0] for row in company_db.run_select(
            "SELECT table_name FROM data_tables WHERE table_name LIKE 'v%'")["rows"]}
    except Exception:
        return None
    if "v_donor_retention" not in views:
        return None

    has = lambda *words: any(w in text for w in words)
    year = re.search(r"\b(20\d{2})\b", text)
    top = re.search(r"\btop\s+(\d{1,2})\b", text)
    limit = min(int(top.group(1)), 25) if top else 10
    pledges_view = "v_pledges" in views

    # Churn / retention over the years
    if has("churn", "retention", "retain", "lapse rate", "lapsed rate"):
        return ("SELECT year, churn_rate_pct, retention_rate_pct FROM v_donor_retention "
                "WHERE churn_rate_pct IS NOT NULL ORDER BY year",
                "line", "Donor churn and retention rate by year (%)")

    # Individual vs company giving
    if pledges_view and has("individual") and has("compan", "corporate", "business"):
        where = f"WHERE year = {year.group(1)} " if year else ""
        return ("SELECT CASE WHEN company_name IS NULL OR TRIM(company_name) = '' "
                "THEN 'Individuals' ELSE 'Companies' END AS donor_type, "
                f"ROUND(SUM(pledge_amount), 2) AS total_pledged FROM v_pledges {where}"
                "GROUP BY 1 ORDER BY 2 DESC",
                "pie", "Individual vs company pledges" + (f", {year.group(1)}" if year else " (all years)"))

    # Top companies
    if pledges_view and has("compan", "corporate", "business") and has("top", "largest", "biggest", "most"):
        where = f"AND year = {year.group(1)} " if year else ""
        return ("SELECT company_name, ROUND(SUM(pledge_amount), 2) AS total_pledged FROM v_pledges "
                f"WHERE company_name IS NOT NULL AND TRIM(company_name) <> '' {where}"
                f"GROUP BY company_name ORDER BY total_pledged DESC LIMIT {limit}",
                "bar", f"Top {limit} companies by total pledged" + (f", {year.group(1)}" if year else ""))

    # Top donors
    if has("donor", "giver", "supporter") and has("top", "largest", "biggest", "most"):
        if year and pledges_view:
            return ("SELECT donor_name, ROUND(SUM(pledge_amount), 2) AS total_pledged FROM v_pledges "
                    f"WHERE year = {year.group(1)} GROUP BY donor_id ORDER BY total_pledged DESC LIMIT {limit}",
                    "bar", f"Top {limit} donors by total pledged, {year.group(1)}")
        return (f"SELECT donor_name, total_pledged FROM v_donor_summary ORDER BY total_pledged DESC LIMIT {limit}",
                "bar", f"Top {limit} donors by total pledged (all years)")

    # Number of donors per year
    if has("donor") and has("number", "count", "how many", "active", "new"):
        return ("SELECT year, active_donors, new_or_returning_donors, lapsed_donors "
                "FROM v_donor_retention ORDER BY year",
                "bar", "Donors per year: active, new or returning, lapsed")

    # Pledges by month (for one year)
    if pledges_view and has("month") and has("pledge", "giving", "gift", "donation", "amount", "total"):
        y = year.group(1) if year else None
        where = f"WHERE year = {y} " if y else ""
        return ("SELECT substr(date_of_pledge, 1, 7) AS month, ROUND(SUM(pledge_amount), 2) AS total_pledged "
                f"FROM v_pledges {where}GROUP BY 1 ORDER BY 1",
                "line", "Pledges by month" + (f", {y}" if y else ""))

    # Pledge totals per year
    if has("pledge", "giving", "gift", "donation", "amount", "total", "revenue", "raised"):
        return ("SELECT year, total_pledged FROM v_donor_retention ORDER BY year",
                "bar", "Total pledged per year")

    return None


def add_chart_to_session(session, chart):
    """Keep the chart, plus a VERIFIED note (its numbers come straight from the database)."""
    session.charts.append(chart)
    session.notes.append({
        "source": "Company database (chart: " + chart["title"] + ")",
        "finding": "Chart data for " + chart["title"],
        "evidence": json.dumps({"columns": chart["columns"], "rows": chart["rows"]}),
        "verified": True,
    })



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

        # Charts made by create_chart (company mode). Each one:
        # {"type", "title", "labels", "datasets": [{"label", "data"}], "source", "sql"}
        self.charts = []

        # SQL of query_database calls that returned rows (chart safety net)
        self.queries = []

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


# =====================================================================
# Company-data tools (read the client's database; no per-request state)
# =====================================================================

def search_documents(query, max_results=5):
    """
    Search the organization's own documents (letters, minutes, reports, policies).

    Args:
        query: Words to look for, e.g. a name, topic or program.
        max_results: Maximum number of passages to return (1-8).

    Returns:
        Matching passages, each with its source (file name and page) and text.
    """

    results = company_db.search(query, max_results)

    if not results:
        return {
            "status": "no_matches",
            "hint": "Try other words, a shorter query, or query_database for figures."
        }

    return results


def query_database(sql):
    """
    Run ONE read-only SQL SELECT on the organization's data tables (SQLite).
    The table names and columns are listed in your instructions.
    Each row has _source_file and _row columns to cite where it came from.

    Args:
        sql: A single SELECT statement, e.g.
             SELECT donor_name, SUM(pledge_amount) FROM t_pledges GROUP BY donor_name

    Returns:
        The column names and up to 50 result rows.
    """

    try:
        return company_db.run_select(sql)
    except Exception as exc:
        # Return the error so the model can correct its SQL on the next step
        return {"status": "error", "error": str(exc)}


def make_tools(session, mode="web"):
    """
    Build the list of tools the model may call, bound to ONE session.

    The note/verify functions are defined inside this function, so each
    one "remembers" its own session (a closure). Their names and docstrings
    are what the model sees, exactly as before.

    mode="web"     -> search_web + read_webpage
    mode="company" -> search_documents + query_database + create_chart (the client's data only)
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


    def create_chart(sql, chart_type="bar", title=""):
        """
        Draw a chart for the user from the organization's database.

        Write ONE SELECT whose FIRST column is the labels (year, donor name...)
        and the next 1 to 3 columns are numbers (one line or bar set each).
        The chart is drawn from the real query results.

        Args:
            sql: A single SELECT, e.g.
                 SELECT year, churn_rate_pct FROM v_donor_retention WHERE churn_rate_pct IS NOT NULL
            chart_type: "bar" (compare items), "line" (trend over time) or "pie" (parts of a whole).
            title: A short chart title.

        Returns:
            Confirmation with the rows the chart shows, or an error to fix.
        """
        chart, error = build_chart(sql, chart_type, title)
        if error:
            return {"status": "error", "error": error}

        add_chart_to_session(session, chart)

        return {
            "status": "chart_created",
            "chart_type": chart["type"],
            "title": chart["title"],
            "columns": chart["columns"],
            "rows": chart["rows"],
            "next": "The chart is shown to the user. Briefly explain what it shows, then finish.",
        }

    note_tools = [
        add_research_note,
        get_research_notes,
        verify_finding
    ]

    if mode == "company":
        return [search_documents, query_database, create_chart] + note_tools

    return [search_web, read_webpage] + note_tools
