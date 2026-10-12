"""
company_db.py  -  the client's own data, in one SQLite database
================================================================
Everything about storing and searching company data lives in this file,
so it can later be swapped for PostgreSQL + pgvector or SQL Server
without touching the agent.

What is stored (file: data/company.db, or COMPANY_DB):

    documents     one row per imported file
                  doc_id (hash of the file), file_name, path, file_type,
                  pages, chars, status (ok | needs_ocr | error), note, ingested_at

    chunks        full-text search index (SQLite FTS5) over document text
                  text + doc_id, file_name, page (a label: "page 3" for PDFs,
                  "part 2" for Word/text files), chunk_index
                  -> keyword search ranked by BM25 relevance

    data_tables   catalog of spreadsheet tables
                  table_name, source_file, sheet, rows, columns

    t_<file>_<sheet>   one SQL table per Excel sheet / CSV file,
                       plus _source_file and _row columns for citations

Used by:
    ingest.py  -> writes (import documents and spreadsheets)
    tools.py   -> reads  (search_documents, query_database tools)
"""

import os
import re
import sqlite3
import time
from contextlib import closing


# Where the database file lives. data/ is never uploaded to GitHub.
DB_PATH = os.getenv("COMPANY_DB", os.path.join("data", "company.db"))

# Limits that keep answers small enough for the model's context
MAX_SEARCH_RESULTS = 8
MAX_PASSAGE_CHARS = 900
MAX_SQL_ROWS = 50
SQL_TIMEOUT_SECONDS = 10


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    file_name   TEXT NOT NULL,
    path        TEXT,
    file_type   TEXT,
    pages       INTEGER,
    chars       INTEGER,
    status      TEXT,
    note        TEXT,
    ingested_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    text,
    doc_id      UNINDEXED,
    file_name   UNINDEXED,
    page        UNINDEXED,
    chunk_index UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS data_tables (
    table_name  TEXT PRIMARY KEY,
    source_file TEXT,
    sheet       TEXT,
    rows        INTEGER,
    columns     TEXT
);
"""


# =====================================================================
# Connections
# =====================================================================

def connect_write(path=None):
    """Read-write connection (used by ingest.py). Creates the file and tables."""
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def connect_read(path=None):
    """
    Read-only connection (used by the agent's tools).
    mode=ro means even a bad SQL statement from the model cannot change data.
    """
    path = path or DB_PATH
    uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def has_data(path=None):
    """True if the company database exists and holds documents or tables."""
    path = path or DB_PATH

    if not os.path.exists(path):
        return False

    try:
        with closing(connect_read(path)) as conn:
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            tables = conn.execute("SELECT COUNT(*) FROM data_tables").fetchone()[0]
            return (docs + tables) > 0
    except sqlite3.Error:
        return False


# =====================================================================
# Writing (ingest.py)
# =====================================================================

def document_exists(conn, doc_id):
    """True if a file with this content was already imported."""
    row = conn.execute(
        "SELECT 1 FROM documents WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    return row is not None


def delete_document(conn, doc_id):
    """Remove a document and its chunks (used when re-importing)."""
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))


def split_text(text, size=1000, overlap=150):
    """
    Split one page of text into overlapping chunks of about `size` characters,
    breaking at whitespace so words are not cut in half.
    """
    text = re.sub(r"[ \t]+", " ", text).strip()

    if len(text) <= size:
        return [text] if text else []

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + size, len(text))

        if end < len(text):
            space = text.rfind(" ", start + size // 2, end)
            if space != -1:
                end = space

        chunks.append(text[start:end].strip())

        if end >= len(text):
            break

        start = max(end - overlap, start + 1)

    return [c for c in chunks if c]


def add_document(conn, doc_id, file_name, path, file_type, pages_text, status, note=""):
    """
    Store one document: a row in `documents` + its text chunks in `chunks`.
    pages_text = list of (label, text), e.g. ("page 3", "...") or ("part 1", "...").
    """
    total_chars = 0

    for label, text in pages_text:
        for index, chunk in enumerate(split_text(text)):
            conn.execute(
                "INSERT INTO chunks (text, doc_id, file_name, page, chunk_index) "
                "VALUES (?, ?, ?, ?, ?)",
                (chunk, doc_id, file_name, label, index),
            )
            total_chars += len(chunk)

    conn.execute(
        "INSERT OR REPLACE INTO documents "
        "(doc_id, file_name, path, file_type, pages, chars, status, note, ingested_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            doc_id, file_name, path, file_type, len(pages_text), total_chars,
            status, note, time.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )


def table_name_for(file_name, sheet=None):
    """Make a safe SQL table name like t_sales_2025_q1 from a file + sheet name."""
    base = os.path.splitext(file_name)[0]
    if sheet:
        base += "_" + sheet

    name = re.sub(r"[^0-9a-zA-Z]+", "_", base).strip("_").lower()
    return ("t_" + name)[:60]


def clean_column_name(name, used):
    """Safe, unique column names (spreadsheets often have odd or repeated headers)."""
    col = re.sub(r"[^0-9a-zA-Z]+", "_", str(name)).strip("_").lower() or "col"

    if col[0].isdigit():
        col = "c_" + col

    base, n = col, 2
    while col in used:
        col = f"{base}_{n}"
        n += 1

    used.add(col)
    return col


_MONEY = re.compile(r"^\s*\(?-?\s*[$€£]?\s*-?[\d,]+(\.\d+)?\s*\)?\s*$")
_DATE_WORDS = ("date", "updated", "created", "modified", "time")


def normalize_column(name, series):
    """
    Clean one spreadsheet column before it goes into SQL:
      - text that looks like money/numbers ("$1,200.00", "(50)") -> numbers
      - columns named like dates (date_of_pledge, record_updated) -> 'YYYY-MM-DD'
        (or 'YYYY-MM-DD HH:MM:SS' when times are present), which sort correctly in SQL
    Anything that doesn't convert cleanly is left exactly as it was.
    """
    import pandas as pd

    values = series.dropna()

    if values.empty:
        return series

    # Dates (by column name, or already datetime from Excel)
    if pd.api.types.is_datetime64_any_dtype(series) or any(w in name for w in _DATE_WORDS):
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_datetime64_any_dtype(series):
            return series    # e.g. a numeric "updated_count" column
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")    # "could not infer format" is expected
            parsed = pd.to_datetime(series, errors="coerce")
        if parsed.notna().sum() >= 0.8 * len(values):
            has_time = (parsed.dropna().dt.normalize() != parsed.dropna()).any()
            fmt = "%Y-%m-%d %H:%M:%S" if has_time else "%Y-%m-%d"
            return parsed.dt.strftime(fmt).where(parsed.notna(), series)
        return series

    # Money / numbers stored as text
    if series.dtype == object or pd.api.types.is_string_dtype(series):
        text = values.astype(str)
        if text.map(lambda v: bool(_MONEY.match(v))).mean() >= 0.9:
            cleaned = series.astype(str).str.replace(r"[$€£,\s]", "", regex=True)
            cleaned = cleaned.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
            numbers = pd.to_numeric(cleaned, errors="coerce")
            return numbers.where(series.notna(), None)

    return series


def add_table(conn, df, file_name, sheet=None):
    """
    Store one spreadsheet sheet (a pandas DataFrame) as a SQL table and
    register it in data_tables. Returns the table name.
    """
    df = df.dropna(how="all").dropna(axis=1, how="all").copy()

    used = set()
    df.columns = [clean_column_name(c, used) for c in df.columns]

    # Make amounts numeric and dates sortable, so SUM / AVG / date ranges work
    for col in df.columns:
        df[col] = normalize_column(col, df[col])

    # Citation columns: which file and which spreadsheet row each record came from
    df.insert(0, "_source_file", file_name if not sheet else f"{file_name} [{sheet}]")
    df.insert(1, "_row", range(2, len(df) + 2))    # row 1 = header row

    name = table_name_for(file_name, sheet)
    df.to_sql(name, conn, if_exists="replace", index=False)

    conn.execute(
        "INSERT OR REPLACE INTO data_tables (table_name, source_file, sheet, rows, columns) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, file_name, sheet or "", len(df), ", ".join(df.columns)),
    )

    return name


# =====================================================================
# Donor analytics views (churn / retention)
# =====================================================================

PLEDGE_COLUMNS = {"donor_id", "pledge_amount", "date_of_pledge"}


def refresh_donor_views(conn):
    """
    If any imported table looks like donor pledges (donor_id, pledge_amount,
    date_of_pledge), (re)create two ready-made SQL views the agent can query:

      v_donor_retention  one row per year: active donors, retained from the year
                         before, new or returning, lapsed, churn_rate_pct,
                         retention_rate_pct, total_pledged, year_complete
      v_donor_summary    one row per donor: name, company, first/last year,
                         years with pledges, total pledged, active or lapsed
      v_pledges          every pledge from all pledge tables in one place, with a
                         year column (used for charts by month, year, company...)

    Churn for year Y = donors who pledged in Y-1 but not in Y, divided by
    donors who pledged in Y-1. Views compute live, so they always match the data.
    Returns the list of views created.
    """

    for view in ("v_donor_retention", "v_donor_summary", "v_pledges"):
        conn.execute(f"DROP VIEW IF EXISTS {view}")
    conn.execute("DELETE FROM data_tables WHERE table_name LIKE 'v\\_%' ESCAPE '\\'")

    tables = []
    for name, columns in conn.execute(
        "SELECT table_name, columns FROM data_tables WHERE table_name NOT LIKE 'v\\_%' ESCAPE '\\'"
    ):
        cols = {c.strip() for c in columns.split(",")}
        if PLEDGE_COLUMNS <= cols:
            tables.append((name, cols))

    if not tables:
        return []

    def pick(col, cols):
        return col if col in cols else f"NULL AS {col}"

    union = " UNION ALL ".join(
        f"SELECT donor_id, {pick('donor_name', cols)}, {pick('company_name', cols)}, "
        f"pledge_amount, date_of_pledge FROM {name}"
        for name, cols in tables
    )

    conn.execute(f"""
        CREATE VIEW v_donor_retention AS
        WITH p AS ({union}),
        g AS (
            SELECT DISTINCT donor_id, CAST(substr(date_of_pledge, 1, 4) AS INTEGER) AS yr
            FROM p WHERE date_of_pledge IS NOT NULL
        ),
        y AS (
            SELECT g.yr, COUNT(*) AS donors,
                   (SELECT ROUND(SUM(pledge_amount), 2) FROM p
                     WHERE CAST(substr(date_of_pledge, 1, 4) AS INTEGER) = g.yr) AS total
            FROM g GROUP BY g.yr
        ),
        r AS (
            SELECT cur.yr, COUNT(*) AS retained
            FROM g cur JOIN g prev ON prev.donor_id = cur.donor_id AND prev.yr = cur.yr - 1
            GROUP BY cur.yr
        )
        SELECT
            y.yr AS year,
            CASE WHEN y.yr < CAST(strftime('%Y', 'now') AS INTEGER) THEN 1 ELSE 0 END AS year_complete,
            y.donors AS active_donors,
            prev.donors AS prior_year_donors,
            COALESCE(r.retained, 0) AS retained_donors,
            y.donors - COALESCE(r.retained, 0) AS new_or_returning_donors,
            prev.donors - COALESCE(r.retained, 0) AS lapsed_donors,
            ROUND(100.0 * (prev.donors - COALESCE(r.retained, 0)) / prev.donors, 1) AS churn_rate_pct,
            ROUND(100.0 * COALESCE(r.retained, 0) / prev.donors, 1) AS retention_rate_pct,
            y.total AS total_pledged
        FROM y
        LEFT JOIN y AS prev ON prev.yr = y.yr - 1
        LEFT JOIN r ON r.yr = y.yr
        ORDER BY y.yr
    """)

    conn.execute(f"""
        CREATE VIEW v_donor_summary AS
        WITH p AS ({union}),
        latest AS (SELECT MAX(CAST(substr(date_of_pledge, 1, 4) AS INTEGER)) AS yr FROM p)
        SELECT
            donor_id,
            MAX(donor_name) AS donor_name,
            MAX(company_name) AS company_name,
            MIN(CAST(substr(date_of_pledge, 1, 4) AS INTEGER)) AS first_year,
            MAX(CAST(substr(date_of_pledge, 1, 4) AS INTEGER)) AS last_year,
            COUNT(DISTINCT substr(date_of_pledge, 1, 4)) AS years_with_pledges,
            COUNT(*) AS pledges,
            ROUND(SUM(pledge_amount), 2) AS total_pledged,
            MAX(date_of_pledge) AS last_pledge_date,
            CASE WHEN MAX(CAST(substr(date_of_pledge, 1, 4) AS INTEGER)) = (SELECT yr FROM latest)
                 THEN 'active' ELSE 'lapsed' END AS status
        FROM p
        GROUP BY donor_id
        ORDER BY total_pledged DESC
    """)

    conn.execute(f"""
        CREATE VIEW v_pledges AS
        WITH p AS ({union})
        SELECT donor_id, donor_name, company_name, pledge_amount, date_of_pledge,
               CAST(substr(date_of_pledge, 1, 4) AS INTEGER) AS year
        FROM p
        WHERE date_of_pledge IS NOT NULL
    """)

    source = ", ".join(n for n, _ in tables)
    for view in ("v_donor_retention", "v_donor_summary", "v_pledges"):
        cursor = conn.execute(f"SELECT * FROM {view}")
        columns = ", ".join(d[0] for d in cursor.description)
        rows = len(cursor.fetchall())
        conn.execute(
            "INSERT OR REPLACE INTO data_tables (table_name, source_file, sheet, rows, columns) "
            "VALUES (?, ?, '', ?, ?)",
            (view, source, rows, columns),
        )

    return ["v_donor_retention", "v_donor_summary", "v_pledges"]


# =====================================================================
# Reading (agent tools)
# =====================================================================

def _fts_query(text):
    """
    Turn a natural-language question into a safe FTS5 query:
    words joined with OR (any word may match; BM25 ranks the best first).
    Quoting each word stops FTS5 syntax errors from punctuation.
    """
    words = re.findall(r"[0-9A-Za-zÀ-ɏ]{2,}", text.lower())
    words = [w for w in dict.fromkeys(words) if w not in _STOPWORDS]
    return " OR ".join(f'"{w}"' for w in words[:20])


_STOPWORDS = {
    "the", "and", "for", "are", "was", "what", "which", "who", "how", "does",
    "did", "our", "your", "with", "from", "this", "that", "about", "into",
    "is", "of", "to", "in", "on", "a", "an", "do", "be", "by", "or", "as",
    "at", "it", "we", "us", "me", "my", "any", "all", "can", "tell", "show",
}


def search(query, max_results=5, path=None):
    """
    Keyword search over all document chunks, best matches first.
    Returns a list of {file_name, page, doc_id, text}.
    """
    max_results = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
    fts = _fts_query(query)

    if not fts:
        return []

    with closing(connect_read(path)) as conn:
        rows = conn.execute(
            "SELECT file_name, page, doc_id, text FROM chunks "
            "WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?",
            (fts, max_results),
        ).fetchall()

    return [
        {
            "source": f"{file_name}, {page}",
            "doc_id": doc_id,
            "text": text[:MAX_PASSAGE_CHARS],
        }
        for file_name, page, doc_id, text in rows
    ]


def describe_tables(path=None):
    """Short text description of the spreadsheet tables, for the model's prompt."""
    with closing(connect_read(path)) as conn:
        rows = conn.execute(
            "SELECT table_name, source_file, sheet, rows, columns FROM data_tables "
            "ORDER BY table_name"
        ).fetchall()

    if not rows:
        return "No spreadsheet tables are loaded."

    lines = []
    for name, source, sheet, n_rows, columns in rows:
        if name.startswith("v_"):
            origin = "calculated view over " + source
        else:
            origin = "from " + source + (f" [{sheet}]" if sheet else "")
        lines.append(f"- {name} ({n_rows} rows, {origin}): {columns}")

    return "\n".join(lines)


def document_summary(path=None):
    """Counts of what is loaded, for the prompt and for status checks."""
    with closing(connect_read(path)) as conn:
        docs = conn.execute("SELECT COUNT(*) FROM documents WHERE status != 'error'").fetchone()[0]
        tables = conn.execute("SELECT COUNT(*) FROM data_tables").fetchone()[0]
    return {"documents": docs, "tables": tables}


_READ_ONLY_SQL = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def run_select(sql, path=None, max_rows=None):
    """
    Run ONE read-only SELECT statement and return {columns, rows, truncated}.
    Protections: read-only connection, SELECT/WITH only, single statement,
    row cap, and a time limit so a heavy query cannot hang the agent.
    """
    sql = sql.strip().rstrip(";").strip()

    if not _READ_ONLY_SQL.match(sql):
        raise ValueError("Only SELECT queries are allowed.")

    if ";" in sql:
        raise ValueError("Only one SQL statement is allowed.")

    with closing(connect_read(path)) as conn:
        deadline = time.monotonic() + SQL_TIMEOUT_SECONDS

        # Returning non-zero from the progress handler aborts the query
        conn.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, 10_000
        )

        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        limit = max_rows or MAX_SQL_ROWS
        rows = cursor.fetchmany(limit + 1)

    return {
        "columns": columns,
        "rows": [list(r) for r in rows[:limit]],
        "truncated": len(rows) > limit,
    }
