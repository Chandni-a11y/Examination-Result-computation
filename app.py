"""Small college examination system prototype for a local project submission."""

import os
import secrets
import sqlite3
import csv
import io
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, Response, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


ROOT = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("EXAM_DATABASE_PATH", str(ROOT / "examination.db")))
DEPLOYMENT_MODE = os.environ.get("EXAM_DEPLOYMENT_MODE", "local").lower()
if DEPLOYMENT_MODE == "public" and not os.environ.get("EXAM_ADMIN_PASSWORD"):
    raise RuntimeError("Set EXAM_ADMIN_PASSWORD before running in public mode.")
PASS_MARK = 40  # Demonstration rule only; confirm the official rule before use.
MARK_LIMITS = {"internal": 20, "practical": 20, "theory": 60}
ROLES = ("admin", "faculty", "exam_branch", "student")

app = Flask(__name__)
if DEPLOYMENT_MODE == "public" and not os.environ.get("EXAM_APP_SECRET"):
    raise RuntimeError("Set EXAM_APP_SECRET before running in public mode.")
app.secret_key = os.environ.get("EXAM_APP_SECRET", "local-demo-secret-change-before-use")


def connect_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','faculty','exam_branch','student')),
                student_code TEXT
            );
            CREATE TABLE IF NOT EXISTS students (
                student_code TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                course TEXT NOT NULL,
                semester TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                credits REAL NOT NULL DEFAULT 0,
                semester TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS exams (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                academic_year TEXT NOT NULL,
                semester TEXT NOT NULL DEFAULT '',
                exam_date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Scheduled'
            );
            CREATE TABLE IF NOT EXISTS marks (
                id INTEGER PRIMARY KEY,
                student_code TEXT NOT NULL REFERENCES students(student_code),
                subject_id INTEGER NOT NULL REFERENCES subjects(id),
                exam_id INTEGER NOT NULL REFERENCES exams(id),
                internal REAL NOT NULL,
                practical REAL NOT NULL,
                theory REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'Submitted'
                    CHECK(status IN ('Submitted','Verified','Published')),
                total REAL,
                outcome TEXT,
                published_at TEXT,
                UNIQUE(student_code, subject_id, exam_id)
            );
            CREATE TABLE IF NOT EXISTS correction_requests (
                id INTEGER PRIMARY KEY,
                student_code TEXT NOT NULL REFERENCES students(student_code),
                subject_id INTEGER NOT NULL REFERENCES subjects(id),
                exam_id INTEGER NOT NULL REFERENCES exams(id),
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                staff_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS technical_issues (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                resolution TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        exam_columns = {row["name"] for row in db.execute("PRAGMA table_info(exams)")}
        for column, definition in (("semester", "TEXT NOT NULL DEFAULT ''"),
                                   ("exam_date", "TEXT NOT NULL DEFAULT ''"),
                                   ("status", "TEXT NOT NULL DEFAULT 'Scheduled'")):
            if column not in exam_columns:
                db.execute(f"ALTER TABLE exams ADD COLUMN {column} {definition}")
        admin_username = os.environ.get("EXAM_ADMIN_USERNAME", "admin")
        admin_password_hash = generate_password_hash(
            os.environ.get("EXAM_ADMIN_PASSWORD", "admin123")
        )
        db.execute(
            """INSERT INTO users(username,password_hash,role) VALUES(?,?,?)
            ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash
            WHERE users.role='admin'""",
            (admin_username, admin_password_hash, "admin"),
        )


def record_activity(action, details=""):
    g.db.execute("""INSERT INTO activity_logs(username,role,action,details,created_at)
        VALUES(?,?,?,?,?)""", (session.get("username", "system"), session.get("role", "system"),
                                 action, details, datetime.now().isoformat(timespec="seconds")))


@app.before_request
def open_db():
    g.db = connect_db()


@app.teardown_request
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Form expired. Please go back, refresh, and try again.")


def login_required(*roles):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                if roles:
                    return redirect(url_for("login", role=roles[0]))
                return redirect(url_for("home"))
            if roles and session.get("role") not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate


@app.get("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("role_select.html")


@app.route("/login/<role>", methods=["GET", "POST"])
def login(role):
    if role not in ROLES:
        abort(404)
    if request.method == "POST":
        user = g.db.execute(
            "SELECT * FROM users WHERE username = ?", (request.form.get("username", "").strip(),)
        ).fetchone()
        if (user and user["role"] == role and
                check_password_hash(user["password_hash"], request.form.get("password", ""))):
            session.clear()
            session.update(user_id=user["id"], username=user["username"], role=user["role"],
                           student_code=user["student_code"])
            flash("Signed in.", "success")
            return redirect(url_for("dashboard"))
        flash("That username and password do not match the selected portal.", "error")
    return render_template("login.html", role=role)


@app.get("/dashboard")
@login_required()
def dashboard():
    role = session["role"]
    endpoint = {"admin": "admin_page", "faculty": "faculty_page",
                "exam_branch": "exam_page", "student": "student_page"}[role]
    return redirect(url_for(endpoint))


@app.get("/admin")
@login_required("admin")
def admin_page():
    stats = {
        "students": g.db.execute("SELECT COUNT(*) FROM students").fetchone()[0],
        "subjects": g.db.execute("SELECT COUNT(*) FROM subjects").fetchone()[0],
        "accounts": g.db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "issues": g.db.execute("SELECT COUNT(*) FROM technical_issues WHERE status='Open'").fetchone()[0],
    }
    return render_template(
        "admin.html",
        students=g.db.execute("SELECT * FROM students ORDER BY student_code").fetchall(),
        subjects=g.db.execute("SELECT * FROM subjects ORDER BY code").fetchall(),
        exams=g.db.execute("SELECT * FROM exams ORDER BY id DESC").fetchall(),
        users=g.db.execute("SELECT id,username,role,student_code FROM users ORDER BY username").fetchall(),
        stats=stats,
        activity=g.db.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT 12").fetchall(),
        issues=g.db.execute("SELECT * FROM technical_issues ORDER BY id DESC LIMIT 30").fetchall(),
    )


@app.post("/admin/issue")
@login_required("admin")
def add_issue():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    if not title or not description:
        flash("Enter an issue title and description.", "error")
    else:
        now = datetime.now().isoformat(timespec="seconds")
        g.db.execute("INSERT INTO technical_issues(title,description,created_at,updated_at) VALUES(?,?,?,?)",
                     (title, description, now, now))
        record_activity("Technical issue recorded", title)
        g.db.commit()
        flash("Technical issue recorded.", "success")
    return redirect(url_for("admin_page") + "#issues")


@app.post("/admin/issue/<int:issue_id>/resolve")
@login_required("admin")
def resolve_issue(issue_id):
    note = request.form.get("resolution", "").strip()
    if not note:
        flash("Add a short resolution note before closing an issue.", "error")
    else:
        g.db.execute("UPDATE technical_issues SET resolution=?,status='Resolved',updated_at=? WHERE id=? AND status='Open'",
                     (note, datetime.now().isoformat(timespec="seconds"), issue_id))
        record_activity("Technical issue resolved", f"Issue {issue_id}")
        g.db.commit()
        flash("Issue updated if it was still open.", "success")
    return redirect(url_for("admin_page") + "#issues")


@app.post("/admin/student")
@login_required("admin")
def add_student():
    try:
        g.db.execute("INSERT INTO students VALUES(?,?,?,?)", (
            request.form["student_code"].strip(), request.form["full_name"].strip(),
            request.form["course"].strip(), request.form["semester"].strip()))
        record_activity("Student record added", request.form["student_code"].strip())
        g.db.commit()
        flash("Student added.", "success")
    except sqlite3.IntegrityError:
        flash("Could not add student. Check the ID and required fields.", "error")
    return redirect(url_for("admin_page"))


@app.post("/admin/subject")
@login_required("admin")
def add_subject():
    try:
        g.db.execute("INSERT INTO subjects(code,name,credits,semester) VALUES(?,?,?,?)", (
            request.form["code"].strip(), request.form["name"].strip(),
            float(request.form["credits"]), request.form["semester"].strip()))
        record_activity("Subject added", request.form["code"].strip())
        g.db.commit()
        flash("Subject added.", "success")
    except (ValueError, sqlite3.IntegrityError):
        flash("Could not add subject. Check the code, credits, and required fields.", "error")
    return redirect(url_for("admin_page"))


@app.post("/exam/schedule")
@login_required("exam_branch")
def add_exam():
    g.db.execute("INSERT INTO exams(name,academic_year,semester,exam_date,status) VALUES(?,?,?,?,?)", (
        request.form["name"].strip(), request.form["academic_year"].strip(),
        request.form.get("semester", "").strip(), request.form.get("exam_date", ""), "Scheduled"))
    record_activity("Exam scheduled", request.form["name"].strip())
    g.db.commit()
    flash("Exam added.", "success")
    return redirect(url_for("exam_page") + "#schedule")


@app.post("/admin/user")
@login_required("admin")
def add_user():
    username = request.form["username"].strip()
    password = request.form["password"]
    role = request.form["role"]
    student_code = request.form.get("student_code", "").strip() or None
    if role not in ROLES or len(password) < 6:
        flash("Choose a valid role and a password of at least 6 characters.", "error")
    elif role == "student" and not g.db.execute(
        "SELECT 1 FROM students WHERE student_code=?", (student_code,)
    ).fetchone():
        flash("Add the student record before creating a student login.", "error")
    else:
        try:
            g.db.execute("INSERT INTO users(username,password_hash,role,student_code) VALUES(?,?,?,?)",
                         (username, generate_password_hash(password), role, student_code))
            record_activity("User account created", f"{username} · {role}")
            g.db.commit()
            flash("User account added.", "success")
        except sqlite3.IntegrityError:
            flash("That username already exists.", "error")
    return redirect(url_for("admin_page"))


@app.post("/admin/user/<int:user_id>/delete")
@login_required("admin")
def delete_user(user_id):
    target = g.db.execute("SELECT id,username,role FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        flash("That user account was not found.", "error")
    elif target["id"] == session["user_id"]:
        flash("You cannot delete the account you are currently using.", "error")
    elif target["role"] == "admin" and g.db.execute(
        "SELECT COUNT(*) FROM users WHERE role='admin'"
    ).fetchone()[0] <= 1:
        flash("The last administrator account cannot be deleted.", "error")
    else:
        g.db.execute("DELETE FROM users WHERE id=?", (user_id,))
        record_activity("User login deleted", f"{target['username']} · {target['role']}")
        g.db.commit()
        flash(f"Login account '{target['username']}' deleted. Academic records remain.", "success")
    return redirect(url_for("admin_page"))


@app.get("/faculty")
@login_required("faculty")
def faculty_page():
    return render_template("faculty.html",
        students=g.db.execute("SELECT * FROM students ORDER BY student_code").fetchall(),
        subjects=g.db.execute("SELECT * FROM subjects ORDER BY code").fetchall(),
        exams=g.db.execute("SELECT * FROM exams ORDER BY id DESC").fetchall(),
        marks=g.db.execute("""SELECT m.*,s.full_name,sub.code,sub.name,e.name AS exam_name
            FROM marks m JOIN students s ON s.student_code=m.student_code
            JOIN subjects sub ON sub.id=m.subject_id JOIN exams e ON e.id=m.exam_id
            ORDER BY m.id DESC LIMIT 100""").fetchall())


@app.get("/faculty/report.csv")
@login_required("faculty")
def faculty_report():
    rows = g.db.execute("""SELECT m.student_code,s.full_name,sub.code,e.name,m.internal,m.practical,m.theory,m.status
        FROM marks m JOIN students s ON s.student_code=m.student_code
        JOIN subjects sub ON sub.id=m.subject_id JOIN exams e ON e.id=m.exam_id ORDER BY e.id DESC,m.id DESC""").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student ID", "Student", "Subject", "Exam", "Internal", "Practical", "Theory", "Status"])
    writer.writerows([tuple(row) for row in rows])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=faculty_marks_report.csv"})


@app.post("/faculty/marks")
@login_required("faculty")
def save_marks():
    try:
        values = {key: float(request.form[key]) for key in MARK_LIMITS}
        if any(value < 0 or value > MARK_LIMITS[key] for key, value in values.items()):
            raise ValueError
        existing = g.db.execute(
            "SELECT id,status FROM marks WHERE student_code=? AND subject_id=? AND exam_id=?",
            (request.form["student_code"], request.form["subject_id"], request.form["exam_id"]),
        ).fetchone()
        if existing and existing["status"] == "Published":
            flash("Published marks cannot be changed. Use a correction request.", "error")
        else:
            g.db.execute("""INSERT INTO marks(student_code,subject_id,exam_id,internal,practical,theory,status)
                VALUES(?,?,?,?,?,?, 'Submitted') ON CONFLICT(student_code,subject_id,exam_id)
                DO UPDATE SET internal=excluded.internal,practical=excluded.practical,
                theory=excluded.theory,status='Submitted',total=NULL,outcome=NULL,published_at=NULL
                WHERE marks.status != 'Published'""",
                (request.form["student_code"], request.form["subject_id"], request.form["exam_id"],
                 values["internal"], values["practical"], values["theory"]))
            record_activity("Marks submitted", f"Student {request.form['student_code']}")
            g.db.commit()
            flash("Marks submitted for examination-branch review.", "success")
    except (ValueError, KeyError, sqlite3.IntegrityError):
        flash("Marks must be numeric and within the example ranges: internal 0–20, practical 0–20, theory 0–60.", "error")
    return redirect(url_for("faculty_page"))


@app.get("/exam")
@login_required("exam_branch")
def exam_page():
    marks = g.db.execute("""SELECT m.*,s.full_name,sub.code,sub.name,e.name AS exam_name
        FROM marks m JOIN students s ON s.student_code=m.student_code
        JOIN subjects sub ON sub.id=m.subject_id JOIN exams e ON e.id=m.exam_id
        ORDER BY e.id DESC,m.id DESC""").fetchall()
    requests = g.db.execute("""SELECT c.*,s.full_name,sub.code,e.name AS exam_name
        FROM correction_requests c JOIN students s ON s.student_code=c.student_code
        JOIN subjects sub ON sub.id=c.subject_id JOIN exams e ON e.id=c.exam_id
        ORDER BY c.id DESC""").fetchall()
    exams = g.db.execute("SELECT * FROM exams ORDER BY id DESC").fetchall()
    counts = {"exams": len(exams), "submitted": sum(1 for m in marks if m["status"] == "Submitted"),
              "verified": sum(1 for m in marks if m["status"] == "Verified"),
              "pending": sum(1 for c in requests if c["status"] == "Pending")}
    return render_template("exam.html", marks=marks, requests=requests, exams=exams, counts=counts)


@app.get("/exam/report.csv")
@login_required("exam_branch")
def exam_report():
    rows = g.db.execute("""SELECT m.student_code,s.full_name,sub.code,e.name,m.internal,m.practical,m.theory,
        m.total,m.outcome,m.status FROM marks m JOIN students s ON s.student_code=m.student_code
        JOIN subjects sub ON sub.id=m.subject_id JOIN exams e ON e.id=m.exam_id ORDER BY e.id DESC,m.id DESC""").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student ID", "Student", "Subject", "Exam", "Internal", "Practical", "Theory", "Total", "Outcome", "Status"])
    writer.writerows([tuple(row) for row in rows])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=examination_results_report.csv"})


@app.post("/exam/verify/<int:mark_id>")
@login_required("exam_branch")
def verify_marks(mark_id):
    g.db.execute("UPDATE marks SET status='Verified' WHERE id=? AND status='Submitted'", (mark_id,))
    record_activity("Marks verified", f"Entry {mark_id}")
    g.db.commit()
    flash("Mark entry verified if it was awaiting review.", "success")
    return redirect(url_for("exam_page"))


@app.post("/exam/publish")
@login_required("exam_branch")
def publish_results():
    exam_id = request.form["exam_id"]
    rows = g.db.execute("SELECT * FROM marks WHERE exam_id=? AND status='Verified'", (exam_id,)).fetchall()
    if not rows:
        flash("There are no verified marks to publish for that exam.", "error")
    else:
        for row in rows:
            total = row["internal"] + row["practical"] + row["theory"]
            outcome = "Pass" if total >= PASS_MARK else "Fail"
            g.db.execute("UPDATE marks SET status='Published',total=?,outcome=?,published_at=? WHERE id=?",
                         (total, outcome, datetime.now().isoformat(timespec="seconds"), row["id"]))
        record_activity("Results published", f"Exam {exam_id}: {len(rows)} results")
        g.db.commit()
        flash(f"Published {len(rows)} result(s).", "success")
    return redirect(url_for("exam_page"))


@app.get("/student")
@login_required("student")
def student_page():
    code = session.get("student_code")
    if not code:
        abort(403, "This student login is not linked to a student record.")
    results = g.db.execute("""SELECT m.*,sub.code,sub.name,e.name AS exam_name,e.academic_year
        FROM marks m JOIN subjects sub ON sub.id=m.subject_id JOIN exams e ON e.id=m.exam_id
        WHERE m.student_code=? AND m.status='Published' ORDER BY e.id DESC,sub.code""", (code,)).fetchall()
    requests = g.db.execute("""SELECT c.*,sub.code FROM correction_requests c
        JOIN subjects sub ON sub.id=c.subject_id WHERE c.student_code=? ORDER BY c.id DESC""", (code,)).fetchall()
    return render_template("student.html", results=results, requests=requests,
        subjects=g.db.execute("""SELECT DISTINCT sub.* FROM subjects sub JOIN marks m ON m.subject_id=sub.id
        JOIN exams e ON e.id=m.exam_id WHERE m.student_code=? AND m.status='Published'""", (code,)).fetchall(),
        exams=g.db.execute("""SELECT DISTINCT e.* FROM exams e JOIN marks m ON m.exam_id=e.id
        WHERE m.student_code=? AND m.status='Published'""", (code,)).fetchall())


@app.get("/student/results.csv")
@login_required("student")
def student_results_download():
    code = session.get("student_code")
    if not code:
        abort(403, "This student login is not linked to a student record.")
    rows = g.db.execute("""SELECT e.academic_year,e.name,sub.code,sub.name,m.internal,m.practical,m.theory,m.total,m.outcome
        FROM marks m JOIN subjects sub ON sub.id=m.subject_id JOIN exams e ON e.id=m.exam_id
        WHERE m.student_code=? AND m.status='Published' ORDER BY e.id DESC,sub.code""", (code,)).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Academic year", "Exam", "Subject code", "Subject", "Internal", "Practical", "Theory", "Total", "Outcome"])
    writer.writerows([tuple(row) for row in rows])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=my_published_results.csv"})


@app.post("/student/correction")
@login_required("student")
def request_correction():
    code = session.get("student_code")
    reason = request.form.get("reason", "").strip()
    exists = g.db.execute("""SELECT 1 FROM marks WHERE student_code=? AND subject_id=? AND exam_id=?
        AND status='Published'""", (code, request.form.get("subject_id"), request.form.get("exam_id"))).fetchone()
    if not reason or not exists:
        flash("Choose one of your published results and enter a reason.", "error")
    else:
        g.db.execute("""INSERT INTO correction_requests(student_code,subject_id,exam_id,reason,created_at)
            VALUES(?,?,?,?,?)""", (code, request.form["subject_id"], request.form["exam_id"], reason,
                                     datetime.now().isoformat(timespec="seconds")))
        record_activity("Correction request submitted", f"Student {code}")
        g.db.commit()
        flash("Correction request submitted.", "success")
    return redirect(url_for("student_page"))


@app.post("/exam/request/<int:request_id>")
@login_required("exam_branch")
def resolve_request(request_id):
    status = request.form.get("status")
    if status not in ("Resolved", "Rejected"):
        abort(400)
    g.db.execute("UPDATE correction_requests SET status=?,staff_note=? WHERE id=? AND status='Pending'",
                 (status, request.form.get("staff_note", "").strip(), request_id))
    record_activity("Correction request reviewed", f"Request {request_id}: {status}")
    g.db.commit()
    flash("Request updated if it was still pending.", "success")
    return redirect(url_for("exam_page"))


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
