"""
App version - change it HERE only.

The number shows on the web page (top right and footer) and in GET /health,
and it is added to the CSS/JS links so browsers load fresh files after
every new version (no more Ctrl+F5).

How to number:  MAJOR.MINOR.PATCH
    PATCH (1.4.0 -> 1.4.1): small fix, text or color change
    MINOR (1.4.1 -> 1.5.0): new feature
    MAJOR (1.5.0 -> 2.0.0): big redesign or breaking change

History
    1.0.0  Course project: plan -> research -> verify -> report, web UI
    1.1.0  Step limit, docs, Docker + AWS deployment
    1.2.0  Company data: import files into a database, "/" questions
    1.3.0  AK Data Solutions theme, logo, demo banner, policy page,
           donor demo data with churn views, sample questions
    1.4.0  Charts for "/" questions, coral buttons, testing-phase note
    1.4.1  Charts need at least 2 data points; report headings and bold
           shown properly instead of ** and ###
    1.5.0  Ready-made charts for common donor questions (same chart every
           time, much faster); v_pledges view; browsers always load the
           newest page; note when a chart is asked without "/"
    1.6.0  Download as Excel for "/" answers (data behind charts and queries)
    1.7.0  Download as PDF for every answer (print layout with charts)
    1.8.0  UI refresh: animated hero, process flow, tabbed sample questions
    1.9.0  Hosted model option (LLM_PROVIDER=anthropic|openai) - runs take
           seconds instead of minutes; Ollama is now optional
    1.10.0 Visitor log: who opened the site, the name typed at login, every
           question and its result, at /admin/visits (ADMIN_PASSWORD)
"""

APP_VERSION = "1.10.0"
