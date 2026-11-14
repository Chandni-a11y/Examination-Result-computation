# College Examination System (simple project)

A small local Flask + SQLite prototype for a college project. It supports four simple roles: Administrator, Faculty, Examination Branch, and Student.

## Run it

1. Install Python 3.10 or newer.
2. In this folder, run `python -m pip install -r requirements.txt`.
3. Run `python app.py`.
4. Open `http://127.0.0.1:5000` in a browser and choose the Administrator portal.
5. Sign in with the starter administrator account: **admin / admin123**. Add student and subject records and create role logins in the Admin portal. Schedule exams in the Examination Branch portal.

For a classroom demonstration, keep the app on your own computer. The starter login is only for this local demo.

## Public demo deployment (Render)

The `render.yaml` file prepares a small public demonstration deployment. Push this project to a **new public GitHub repository** (do not include `examination.db`), then create a Render Web Service from that repository using its Blueprint configuration. Render will ask you to enter `EXAM_ADMIN_PASSWORD`; choose a new password and keep it private. The deployed administrator username is `collegeadmin`.

The included Render plan uses temporary filesystem storage. Records entered into the hosted demo may be lost when the service restarts or redeploys. Use fictional demonstration records only. To retain real ongoing data, this project needs a persistent database and appropriate privacy/security review before public use.

The local database is ignored by Git. Do not remove that protection or upload a database containing real student or staff records.

## Included workflow

The four role portals have separate menus and pages. Admin manages students, subjects, accounts, and a basic technical issue log. Examination Branch schedules exams, verifies mark entries, publishes verified results, reviews correction requests, and downloads a CSV report. Faculty submits marks, reviews submission status, and downloads a CSV report. Students view, print, or download their own published results and track correction requests. The administrator overview also displays activity entries from key academic workflow actions.

The result screen shows total marks and Pass/Fail. The example uses internal 20, practical 20, theory 60, and total pass mark 40, because those values appear in the report as an illustrative test scheme. They are **not confirmed university rules**. The report does not define grade boundaries, SGPA, or CGPA formulas, so this prototype does not invent or calculate those values.

## Project files

- `app.py` — routes, simple role checks, SQLite schema, and result workflow
- `templates/` — HTML pages
- `static/style.css` — basic styling
- `examination.db` — created automatically the first time the app runs

This is a classroom prototype, not a production examination platform. It has no official grading policy, deployment setup, bulk upload, or full audit/version history.
