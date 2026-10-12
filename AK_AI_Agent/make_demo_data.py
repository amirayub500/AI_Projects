"""
make_demo_data.py  -  build a FICTIONAL demo dataset for the "/" company search
================================================================================
All people, organizations and amounts are made up for demonstration.

    python make_demo_data.py data/inbox       write the demo files
    python ingest.py data/inbox               then import them

Creates:
    Donor_Pledges.xlsx      pledge history Jan 2023 - Sep 2026 with columns
                            Donor ID, Donor Name, Company Name, Pledge Amount,
                            Date of Pledge, Record Updated, Updated By
                            (Donor Name = a person; Company Name = the organization,
                             empty for individual donors)
    8 documents             annual report, program overview, gala summary,
                            thank-you letters, board minutes (incl. donor churn),
                            donor profiles, scholarship fund, communications policy

Donors behave realistically: some give every year, some lapse, some come back,
and new donors join each year - so churn/retention can be analysed.
The same seed always produces the same data, so answers are repeatable.
"""

import os
import random
import sys
from datetime import date, datetime, timedelta


SEED = 7
FIRST_YEAR, LAST_YEAR = 2023, 2026
LAST_DATE = date(2026, 9, 30)          # data runs to the end of September 2026
STAFF_BY_YEAR = {
    2023: ["tbrown", "jsmith", "akhan"],
    2024: ["tbrown", "jsmith", "akhan", "mlee"],
    2025: ["jsmith", "akhan", "mlee", "rpatel"],
    2026: ["jsmith", "akhan", "mlee", "rpatel"],
}

# Named donors used in the documents: (id, person, company, type, gift range, first year)
NAMED = [
    ("D-1001", "Maria Lopez", "", "individual", (500, 1500), 2023),
    ("D-1002", "Helen Park", "Green Valley Foundation", "foundation", (10000, 30000), 2023),
    ("D-1003", "Robert Chen", "", "individual", (250, 1000), 2024),
    ("D-1004", "Amina Yusuf", "", "individual", (1000, 4000), 2023),
    ("D-1005", "David Morales", "Harbor Point Bank", "corporate", (5000, 15000), 2023),
    ("D-1006", "Daniel Okafor", "", "individual", (100, 600), 2023),
    ("D-1007", "Priya Raman", "", "individual", (500, 2500), 2024),
    ("D-1008", "Lisa Grant", "Summit Ridge Health", "corporate", (7500, 20000), 2024),
    ("D-1009", "Elena Petrova", "", "individual", (200, 900), 2023),
    ("D-1010", "Charles Whitfield", "The Whitfield Family Trust", "foundation", (15000, 50000), 2023),
    ("D-1011", "James O'Connor", "", "individual", (100, 500), 2023),
    ("D-1012", "Nina Shah", "Bluewater Tech Co.", "corporate", (3000, 12000), 2024),
    ("D-1013", "Sofia Martins", "", "individual", (250, 1200), 2023),
    ("D-1014", "Kenji Watanabe", "", "individual", (500, 3000), 2023),
    ("D-1015", "Tom Becker", "Oak & Ember Bakery", "corporate", (300, 1500), 2025),
    ("D-1016", "Grace Thompson", "", "individual", (50, 400), 2023),
    ("D-1017", "Frank Mills", "Northside Rotary Club", "community", (1000, 5000), 2023),
    ("D-1018", "Ahmed Hassan", "", "individual", (200, 1800), 2024),
    ("D-1019", "Rachel Adams", "Lakeshore Community Fund", "foundation", (5000, 25000), 2023),
    ("D-1020", "Olivia Brooks", "", "individual", (100, 800), 2025),
    ("D-1021", "Marcus Bell", "", "individual", (1000, 6000), 2023),
    ("D-1022", "Karen Liu", "Riverbend Credit Union", "corporate", (2500, 9000), 2024),
    ("D-1023", "Hannah Kim", "", "individual", (100, 700), 2025),
    ("D-1024", "Carlos Mendes", "", "individual", (300, 2000), 2024),
    ("D-1025", "Father Michael Reyes", "St. Agnes Parish", "community", (500, 3000), 2023),
]

FIRST_NAMES = ["Aisha", "Ben", "Chloe", "Diego", "Emma", "Farid", "Gina", "Hiro", "Isla", "Jon",
               "Kara", "Liam", "Mei", "Noah", "Omar", "Paula", "Quinn", "Rosa", "Sam", "Tara",
               "Uma", "Victor", "Wendy", "Xavier", "Yara", "Zane", "Leah", "Ivan", "Nora", "Theo"]
LAST_NAMES = ["Alvarez", "Baker", "Costa", "Dubois", "Evans", "Fischer", "Garcia", "Hughes",
              "Iqbal", "Jensen", "Kowalski", "Larsen", "Murphy", "Novak", "Ortiz", "Patel",
              "Quinlan", "Rossi", "Silva", "Turner", "Usman", "Vargas", "Walsh", "Young", "Zhou"]
EXTRA_COMPANIES = ["Maple Street Dental", "Crescent Logistics", "Brightpath Insurance",
                   "Granite Peak Builders", "Silverline Pharmacy", "Cedar Hill Law Group",
                   "Pioneer Auto Group", "Harborview Hotel", "Evergreen Grocers", "Atlas Engineering"]

# Pledges already used in earlier tests and documents - kept identical
FIXED = [
    ("D-1001", 1200.00, date(2026, 3, 15), datetime(2026, 4, 1, 9, 12), "jsmith"),
    ("D-1002", 25000.00, date(2026, 1, 20), datetime(2026, 2, 2, 14, 30), "akhan"),
    ("D-1003", 500.00, date(2026, 5, 5), datetime(2026, 5, 6, 8, 0), "jsmith"),
    ("D-1004", 2750.50, date(2026, 6, 30), datetime(2026, 7, 1, 10, 45), "akhan"),
    ("D-1001", 800.00, date(2026, 8, 12), datetime(2026, 8, 13, 16, 20), "jsmith"),
]

# Story rules the documents rely on
ALWAYS_ACTIVE = {"D-1001", "D-1002", "D-1003", "D-1004", "D-1005", "D-1007", "D-1008",
                 "D-1012", "D-1015", "D-1016", "D-1017", "D-1021", "D-1022"}
LAPSED_IN_2026 = {"D-1010"}            # Whitfield Trust: gave 2023-2025, waiting on an impact report

PROGRAMS = ["Riverside Literacy Program", "Youth Scholarship Fund",
            "Community Food Pantry", "Senior Tech Mentoring"]


def build_donors(rng):
    donors = [dict(id=i, person=p, company=c, kind=k, range=r, start=s) for i, p, c, k, r, s in NAMED]
    used = {d["person"] for d in donors}
    companies = list(EXTRA_COMPANIES)
    next_id = 1026

    # New donors join every year (more in later years as outreach grows)
    for year, count in [(2023, 12), (2024, 12), (2025, 13), (2026, 10)]:
        for _ in range(count):
            while True:
                person = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
                if person not in used:
                    used.add(person)
                    break
            if companies and rng.random() < 0.25:
                company, kind, rng_range = companies.pop(0), "corporate", (1000, 8000)
            else:
                company, kind, rng_range = "", "individual", rng.choice([(50, 400), (100, 900), (250, 2000)])
            donors.append(dict(id=f"D-{next_id}", person=person, company=company,
                               kind=kind, range=rng_range, start=year))
            next_id += 1

    return donors


def active_years(rng, donor):
    """Which years a donor gives: retention ~65-80%, some lapse and return."""
    keep = 0.80 if donor["kind"] in ("foundation", "corporate", "community") else 0.65
    years, active = [], True

    for year in range(donor["start"], LAST_YEAR + 1):
        if year == donor["start"]:
            active = True
        elif active:
            active = rng.random() < keep
        else:
            active = rng.random() < 0.15        # lapsed donors sometimes come back
        if donor["id"] in ALWAYS_ACTIVE:
            active = True
        if donor["id"] in LAPSED_IN_2026:
            active = year < 2026
        if active:
            years.append(year)

    return years


def build_pledges(rng, donors):
    fixed_years = {(i, d.year) for i, _, d, _, _ in FIXED}
    rows = []

    for donor in donors:
        low, high = donor["range"]
        step = 25 if high <= 1000 else (50 if high <= 5000 else 500)

        for year in active_years(rng, donor):
            if (donor["id"], year) in fixed_years:
                continue                          # this year's pledges come from FIXED
            months = 12 if year < 2026 else 9
            per_year = 1 if donor["kind"] != "individual" else rng.choice([1, 1, 1, 2, 2, 3])
            if donor["id"] == "D-1016":
                per_year = months                 # Grace Thompson gives monthly

            for n in range(per_year):
                if donor["id"] == "D-1016":
                    day = date(year, n + 1, 5)
                else:
                    day = date(year, 1, 1) + timedelta(days=rng.randrange(30 * months))
                day = min(day, LAST_DATE)
                amount = float(rng.randrange(low, high + step, step))
                updated = datetime.combine(day, datetime.min.time()) + timedelta(
                    days=rng.randrange(0, 15), hours=rng.randrange(8, 18),
                    minutes=rng.choice([0, 15, 30, 45]))
                rows.append((donor["id"], amount, day, updated, rng.choice(STAFF_BY_YEAR[year])))

    rows.extend(FIXED)
    info = {d["id"]: d for d in donors}
    full = [(i, info[i]["person"], info[i]["company"], a, d, u, w) for i, a, d, u, w in rows]
    full.sort(key=lambda r: (r[4], r[0]))
    return full


def retention(rows):
    """Same definition as the database view v_donor_retention."""
    by_year = {}
    for r in rows:
        by_year.setdefault(r[4].year, set()).add(r[0])
    out = {}
    for y in sorted(by_year):
        prev = by_year.get(y - 1)
        if prev:
            kept = len(by_year[y] & prev)
            out[y] = dict(active=len(by_year[y]), prior=len(prev), retained=kept,
                          lapsed=len(prev) - kept, churn=round(100 * (len(prev) - kept) / len(prev), 1))
        else:
            out[y] = dict(active=len(by_year[y]))
    return out


def write_excel(rows, path):
    import pandas as pd

    df = pd.DataFrame(
        [
            (i, p, c, f"${a:,.2f}", d.strftime("%m/%d/%Y"), u.strftime("%Y-%m-%d %H:%M"), w)
            for i, p, c, a, d, u, w in rows
        ],
        columns=["Donor ID", "Donor Name", "Company Name", "Pledge Amount",
                 "Date of Pledge", "Record Updated", "Updated By"],
    )
    df.to_excel(path, index=False, sheet_name="Pledges")


def write_pdf(path, title, lines):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    pdf = fitz.open()
    page, y = None, 0
    for i, line in enumerate([title, ""] + lines):
        if page is None or y > 760:
            page, y = pdf.new_page(), 72
        page.insert_text((72, y), line, fontsize=14 if i == 0 else 11)
        y += 24 if i == 0 else 16
    pdf.save(path)


def write_docx(path, title, paragraphs, table=None):
    import docx

    document = docx.Document()
    document.add_heading(title, level=1)
    for p in paragraphs:
        document.add_paragraph(p)
    if table:
        t = document.add_table(rows=len(table), cols=len(table[0]))
        t.style = "Table Grid"
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                t.cell(r, c).text = str(value)
    document.save(path)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")


def label(row_or_id, info):
    d = info[row_or_id]
    return f"{d['company']} ({d['id']})" if d["company"] else f"{d['person']} ({d['id']})"


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("data", "inbox")
    os.makedirs(out, exist_ok=True)
    rng = random.Random(SEED)

    donors = build_donors(rng)
    info = {d["id"]: d for d in donors}
    rows = build_pledges(rng, donors)
    write_excel(rows, os.path.join(out, "Donor_Pledges.xlsx"))

    money = lambda v: f"${v:,.2f}"
    ret = retention(rows)

    def year_total(y):
        return sum(r[3] for r in rows if r[4].year == y)

    def top(y, n=5):
        t = {}
        for r in rows:
            if r[4].year == y:
                t[r[0]] = t.get(r[0], 0) + r[3]
        return sorted(t.items(), key=lambda kv: kv[1], reverse=True)[:n]

    # ---- Annual report 2025
    r25 = ret[2025]
    write_pdf(os.path.join(out, "Annual_Report_2025.pdf"), "Annual Report 2025 - Community Futures Network", [
        f"In 2025 we received {sum(1 for r in rows if r[4].year == 2025)} pledges from "
        f"{r25['active']} donors, totalling {money(year_total(2025))}.",
        f"Donor retention: {r25['retained']} of {r25['prior']} donors from 2024 gave again in 2025 "
        f"({100 - r25['churn']:.1f}% retention, {r25['churn']}% churn).",
        "Programs: " + ", ".join(PROGRAMS) + ".",
        "",
        "Top supporters in 2025:",
        *[f"  {i}. {label(d, info)}: {money(v)}" for i, (d, v) in enumerate(top(2025), start=1)],
        "",
        "The Riverside Literacy Program served 412 children across six schools.",
        "The Community Food Pantry distributed 38,000 meals with 120 volunteers.",
        "Senior Tech Mentoring trained 95 older adults in video calls and online banking.",
    ])

    # ---- Riverside Literacy Program
    write_pdf(os.path.join(out, "Riverside_Literacy_Program.pdf"), "Riverside Literacy Program - Funding Overview", [
        "The Riverside Literacy Program provides after-school reading tutors for grades 1 to 5.",
        "Lead funder: Green Valley Foundation (D-1002, contact Helen Park), which pledged $25,000",
        "in January 2026 for two years of tutoring. Additional support comes from Harbor Point Bank",
        "(D-1005, contact David Morales) and the Northside Rotary Club (D-1017), whose members",
        "also volunteer as reading buddies.",
        "Goal for 2026: 500 children, 40 trained tutors and a summer reading camp.",
        "Program contact: Lena Ortiz, Literacy Program Manager.",
    ])

    # ---- Fall Gala 2026
    gala = [r for r in rows if r[4].year == 2026 and r[4].month == 9]
    write_docx(os.path.join(out, "Fall_Gala_2026_Summary.docx"), "Fall Gala 2026 - Summary", [
        "The Fall Gala took place on September 19, 2026 at the Lakeshore Pavilion with 240 guests.",
        "Robert Chen (D-1003) chaired the gala committee. Gold sponsor: Summit Ridge Health (D-1008). "
        "Silver sponsors: Bluewater Tech Co. (D-1012) and Riverbend Credit Union (D-1022). "
        "Desserts were donated by Oak & Ember Bakery (D-1015).",
        "Funds raised support the Youth Scholarship Fund and the Community Food Pantry.",
        f"Pledges recorded in September 2026: {len(gala)}, totalling {money(sum(r[3] for r in gala))}.",
    ], table=[["Donor", "Company", "Pledge", "Date"]] +
              [[p, c or "-", money(a), d.isoformat()] for _, p, c, a, d, _, _ in gala])

    # ---- Thank-you letters (amounts match the table)
    letters = [r for r in rows if r[4].year == 2026][:10]
    write_docx(os.path.join(out, "Thank_You_Letters_2026.docx"), "Thank-You Letters 2026", [
        f"Dear {p}{' of ' + c if c else ''}, thank you for your generous pledge of {money(a)} on "
        f"{d.strftime('%B %d, %Y')}. Your gift (donor {i}) helps our community programs grow. "
        f"With gratitude, the Development Team."
        for i, p, c, a, d, _, _ in letters
    ])

    # ---- Board minutes September 2026 (includes churn)
    r26 = ret[2026]
    lapsed_25 = sorted({r[0] for r in rows if r[4].year == 2025} - {r[0] for r in rows if r[4].year == 2026})
    write_docx(os.path.join(out, "Board_Minutes_Sep_2026.docx"), "Board Meeting Minutes - September 2026", [
        "Present: board chair Diane Foster, treasurer Paul Nguyen, five directors, staff jsmith and akhan.",
        f"The treasurer reported {money(year_total(2026))} pledged so far in 2026 from {r26['active']} donors.",
        f"Donor churn: in 2025, {r25['churn']}% of 2024 donors did not give again. "
        f"So far in 2026, {r26['lapsed']} of {r26['prior']} donors from 2025 have not yet renewed "
        f"({r26['churn']}% year-to-date; the year is not finished).",
        f"The board asked staff member rpatel to contact the {len(lapsed_25)} donors from 2025 who have not "
        "renewed, starting with the largest, and to report back in November.",
        "The Whitfield Family Trust (D-1010, contact Charles Whitfield) asked for an impact report "
        "before renewing; it gave every year from 2023 to 2025.",
        "The board approved a matching-gift campaign with Harbor Point Bank (D-1005) for December.",
        "Next meeting: November 12, 2026.",
    ])

    # ---- Donor profiles
    lines = []
    for d in donors[:25]:
        mine = [r for r in rows if r[0] == d["id"]]
        if not mine:
            continue
        yrs = sorted({r[4].year for r in mine})
        who = f"{d['person']}" + (f", {d['company']}" if d["company"] else "")
        lines.append(f"{who} ({d['id']}) - {d['kind']} donor since {yrs[0]}. Gave in "
                     f"{', '.join(map(str, yrs))}; {len(mine)} pledge(s) totalling {money(sum(r[3] for r in mine))}.")
    write_text(os.path.join(out, "Donor_Profiles.txt"), "Donor Profiles (internal notes)\n\n" + "\n".join(lines) + """

Harbor Point Bank matches employee gifts one-to-one up to $5,000 per employee.
Summit Ridge Health prefers to fund health and senior programs.
Grace Thompson (D-1016) gives monthly and prefers email updates only.
Kenji Watanabe (D-1014) asked to stay anonymous in printed materials.
""")

    write_text(os.path.join(out, "Youth_Scholarship_Fund.txt"), """
Youth Scholarship Fund

The fund awards scholarships of $1,000 to $2,500 to local high-school graduates.
Maria Lopez (D-1001) asked that all her gifts support the youth scholarship fund.
Amina Yusuf (D-1004) and Priya Raman (D-1007) also designate their gifts to scholarships.
In 2026 the committee awarded 14 scholarships. Applications open each February.
Committee chair: Marcus Bell (D-1021).
""")

    write_text(os.path.join(out, "Donor_Communications_Policy.txt"), """
Donor Communications Policy

Every pledge receives a thank-you letter within 7 days of the Date of Pledge.
Pledges over $10,000 require a signed gift agreement and a call from the executive director.
Donors who have not given for 12 months are contacted by phone or letter (lapsed donor outreach).
Donor records may only be updated by development staff; each change is logged in
Record Updated and Updated By.
Donors may ask to be anonymous; their names are then removed from printed reports.
""")

    print(f"Demo data written to {out}: {len(rows)} pledges from {len({r[0] for r in rows})} donors + 8 documents")
    for y, v in ret.items():
        extra = f", churn {v['churn']}% ({v['lapsed']} of {v['prior']} lapsed)" if "churn" in v else ""
        print(f"  {y}: {v['active']} active donors, {money(year_total(y))}{extra}")


if __name__ == "__main__":
    main()
