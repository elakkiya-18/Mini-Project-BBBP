
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from functools import wraps
from datetime import datetime, timedelta
from pathlib import Path
from certificate import create_certificate
import random
import hashlib
import hmac
import os
import secrets
import smtplib
import base64
import json
from email.message import EmailMessage
from urllib.parse import urlencode
from urllib.request import Request, urlopen
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "respect_quest.db"


def load_environment_file():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


load_environment_file()

app = Flask(__name__)
app.secret_key = "CHANGE_THIS_SECRET_KEY_IN_REAL_PROJECT"
if not os.environ.get("SMTP_USERNAME") or not os.environ.get("SMTP_PASSWORD"):
    print(
        "WARNING: Gmail OTP is disabled. Configure SMTP_USERNAME and "
        "SMTP_PASSWORD in .env, then restart Flask."
    )
LEVEL_THEMES = {
    1: "🌸 Respect and Dignity",
    2: "📚 Equality in Education",
    3: "💪 Breaking Gender Stereotypes",
    4: "🤝 Shared Responsibility",
    5: "🏫 Safe Schools for Girls",
    6: "❤️ Rights of Women and Children",
    7: "🌍 Inclusion and Equal Opportunities",
    8: "👩‍🔬 Girls in Leadership & STEM",
    9: "🛡️ Respectful Communication",
    10: "🏆 BBBP Champion Challenge"
}

# ---------------- DATABASE ----------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT,
        password_hash TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS password_reset_otps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        otp_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        used INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS quiz_progress (
        user_id INTEGER NOT NULL,
        level INTEGER NOT NULL,
        state_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(user_id, level),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS level_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        level INTEGER NOT NULL,
        rating INTEGER,
        comment TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, level),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level INTEGER NOT NULL,
    question TEXT NOT NULL,

    option_a TEXT NOT NULL,
    option_b TEXT NOT NULL,
    option_c TEXT NOT NULL,
    option_d TEXT NOT NULL,

    correct_answer TEXT NOT NULL,
    option_a_rating INTEGER NOT NULL DEFAULT 1,
    option_b_rating INTEGER NOT NULL DEFAULT 1,
    option_c_rating INTEGER NOT NULL DEFAULT 1,
    option_d_rating INTEGER NOT NULL DEFAULT 1,

    explanation TEXT,
    learning_objective TEXT,
    difficulty TEXT,

    positive_message TEXT,
    learning_message TEXT
);

    CREATE TABLE IF NOT EXISTS answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        level INTEGER NOT NULL,
        question_id INTEGER NOT NULL,
        selected_answer TEXT NOT NULL,
        rating INTEGER NOT NULL DEFAULT 1,
        is_correct INTEGER NOT NULL,
        answered_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(question_id) REFERENCES questions(id)
    );

    CREATE TABLE IF NOT EXISTS level_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        level INTEGER NOT NULL,
        score INTEGER NOT NULL,
        completed INTEGER DEFAULT 0,
        completed_at TEXT,
        UNIQUE(user_id, level),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS certificates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        certificate_number TEXT UNIQUE NOT NULL,
        final_score INTEGER NOT NULL,
        percentage REAL NOT NULL,
        issued_date TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)

    user_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if "phone" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")

    answer_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(answers)").fetchall()
    }
    if "display_correct_answer" not in answer_columns:
        conn.execute(
            "ALTER TABLE answers ADD COLUMN display_correct_answer TEXT"
        )
    if "rating" not in answer_columns:
        conn.execute(
            "ALTER TABLE answers ADD COLUMN rating INTEGER NOT NULL DEFAULT 1"
        )
        conn.execute(
            "UPDATE answers SET rating=5 WHERE is_correct=1"
        )

    question_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(questions)").fetchall()
    }
    for option in "abcd":
        column = f"option_{option}_rating"
        if column not in question_columns:
            conn.execute(
                f"ALTER TABLE questions ADD COLUMN {column} INTEGER NOT NULL DEFAULT 1"
            )
    for option in "abcd":
        conn.execute(
            f"UPDATE questions SET option_{option}_rating=5 "
            f"WHERE correct_answer=?",
            (option.upper(),)
        )

    # Demo admin account
    admin = conn.execute(
        "SELECT id FROM users WHERE username = ?", ("admin",)
    ).fetchone()

    if not admin:
        conn.execute("""
            INSERT INTO users
            (username, email, password_hash, is_admin, created_at)
            VALUES (?, ?, ?, 1, ?)
        """, (
            "admin",
            "admin@respectquest.local",
            generate_password_hash("admin123"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

    conn.commit()
    conn.close()


# ---------------- HELPERS ----------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session or session.get("is_admin") != 1:
            flash("Admin access required.", "danger")
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def completed_levels(user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT level FROM level_results WHERE user_id=? AND completed=1",
        (user_id,)
    ).fetchall()
    conn.close()
    return {row["level"] for row in rows}


def total_progress(user_id):
    conn = get_db()
    answered = conn.execute(
        "SELECT COUNT(DISTINCT question_id) AS n FROM answers WHERE user_id=?",
        (user_id,)
    ).fetchone()["n"]
    conn.close()
    return min(100, round((answered / 100) * 100))


def score_badge(score):
    if score >= 90:
        return {"title": "Diamond Champion", "icon": "💎", "class": "diamond"}
    if score >= 75:
        return {"title": "Gold Achiever", "icon": "🏆", "class": "gold"}
    if score >= 60:
        return {"title": "Silver Star", "icon": "🌟", "class": "silver"}
    if score >= 40:
        return {"title": "Bronze Explorer", "icon": "🥉", "class": "bronze"}
    return {"title": "Brave Beginner", "icon": "🌱", "class": "beginner"}


def normalize_phone(phone):
    return "".join(phone.replace("-", "").replace("(", "").replace(")", "").split())


def send_recovery_email(recipient, otp):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    sender = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    if not sender or not password:
        raise RuntimeError(
            "Email recovery is not configured. Set SMTP_USERNAME and SMTP_PASSWORD."
        )

    message = EmailMessage()
    message["Subject"] = "Respect Quest password reset code"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"Your Respect Quest password reset code is {otp}. "
        "It expires in 10 minutes."
    )
    with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
        smtp.login(sender, password)
        smtp.send_message(message)


def send_recovery_sms(recipient, otp):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    if not account_sid or not auth_token or not from_number:
        raise RuntimeError(
            "Mobile recovery is not configured. Set TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER."
        )

    payload = urlencode({
        "From": from_number,
        "To": recipient,
        "Body": f"Your Respect Quest password reset code is {otp}. It expires in 10 minutes."
    }).encode()
    request = Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data=payload,
        method="POST"
    )
    auth = (f"{account_sid}:{auth_token}").encode()
    request.add_header(
        "Authorization",
        "Basic " + base64.b64encode(auth).decode()
    )
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(request, timeout=20) as response:
        if response.status >= 300:
            raise RuntimeError("The SMS provider rejected the recovery message.")


def create_password_reset(user_id, channel):
    otp = f"{secrets.randbelow(1000000):06d}"
    now = datetime.now()
    conn = get_db()
    conn.execute(
        "UPDATE password_reset_otps SET used=1 WHERE user_id=? AND used=0",
        (user_id,)
    )
    conn.execute("""
        INSERT INTO password_reset_otps
        (user_id, channel, otp_hash, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (
        user_id,
        channel,
        hashlib.sha256(otp.encode()).hexdigest(),
        (now + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
        now.strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()
    conn.close()
    return otp


# ---------------- HOME ----------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip()
        phone = normalize_phone(request.form.get("phone", ""))
        password = request.form["password"]

        if not username or not email or not phone or not password:
            flash("All fields are required.", "danger")
            return render_template("register.html")
        if not phone.startswith("+") or not phone[1:].isdigit():
            flash("Enter a valid mobile number in international format, such as +919876543210.", "danger")
            return render_template("register.html")

        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO users(username, email, phone, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                username,
                email,
                phone,
                generate_password_hash(password),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))
            conn.commit()
            flash("Registration successful. Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username or email already exists.", "danger")
        finally:
            conn.close()

    return render_template("register.html")


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["is_admin"] = user["is_admin"]

            if user["is_admin"]:
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        channel = request.form.get("channel", "")
        if channel not in {"email", "sms"} or not identifier:
            flash("Choose a recovery method and enter your username or email.", "danger")
            return render_template("forgot_password.html", identifier=identifier)

        conn = get_db()
        if channel == "sms":
            user = conn.execute(
                """SELECT * FROM users
                   WHERE username=? OR lower(email)=lower(?) OR phone=?""",
                (identifier, identifier.lower(), normalize_phone(identifier))
            ).fetchone()
        else:
            user = conn.execute(
                "SELECT * FROM users WHERE username=? OR lower(email)=lower(?)",
                (identifier, identifier.lower())
            ).fetchone()
        conn.close()

        if not user:
            flash("No account matches that username, email, or mobile number.", "danger")
            return render_template("forgot_password.html", identifier=identifier)
        if channel == "sms" and not user["phone"]:
            flash("This account has no mobile number. Login and add one from your dashboard, then try again.", "danger")
            return render_template("forgot_password.html", identifier=identifier)

        recipient = user["email"] if channel == "email" else user["phone"]
        otp = create_password_reset(user["id"], channel)
        try:
            if channel == "email":
                send_recovery_email(recipient, otp)
            else:
                send_recovery_sms(recipient, otp)
        except (OSError, ValueError, RuntimeError, smtplib.SMTPException) as error:
            conn = get_db()
            conn.execute(
                "UPDATE password_reset_otps SET used=1 WHERE user_id=? AND used=0",
                (user["id"],)
            )
            conn.commit()
            conn.close()
            app.logger.error("Password recovery delivery failed: %s", error)
            if isinstance(error, RuntimeError):
                flash(str(error), "danger")
            else:
                flash("We could not send the OTP. Please check the recovery service configuration.", "danger")
            return render_template("forgot_password.html", identifier=identifier)

        session["password_reset_user_id"] = user["id"]
        session["password_reset_channel"] = channel
        flash("OTP sent. It expires in 10 minutes.", "success")
        return redirect(url_for("verify_password_reset"))

    return render_template("forgot_password.html", identifier="")


@app.route("/forgot-password/verify", methods=["GET", "POST"])
def verify_password_reset():
    user_id = session.get("password_reset_user_id")
    channel = session.get("password_reset_channel")
    if not user_id or channel not in {"email", "sms"}:
        flash("Start the password reset process again.", "warning")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()
        password = request.form.get("password", "")
        if not otp.isdigit() or len(otp) != 6 or len(password) < 8:
            flash("Enter the 6-digit OTP and a password with at least 8 characters.", "danger")
            return render_template("verify_password_reset.html", channel=channel)

        conn = get_db()
        reset = conn.execute("""
            SELECT * FROM password_reset_otps
            WHERE user_id=? AND channel=? AND used=0
            ORDER BY id DESC LIMIT 1
        """, (user_id, channel)).fetchone()
        now = datetime.now()
        valid = reset and datetime.strptime(reset["expires_at"], "%Y-%m-%d %H:%M:%S") > now
        valid = valid and reset["attempts"] < 5 and hmac.compare_digest(
            reset["otp_hash"], hashlib.sha256(otp.encode()).hexdigest()
        )
        if not valid:
            if reset:
                conn.execute(
                    "UPDATE password_reset_otps SET attempts=attempts+1 WHERE id=?",
                    (reset["id"],)
                )
                conn.commit()
            conn.close()
            flash("That OTP is invalid or expired.", "danger")
            return render_template("verify_password_reset.html", channel=channel)

        conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (generate_password_hash(password), user_id)
        )
        conn.execute("UPDATE password_reset_otps SET used=1 WHERE id=?", (reset["id"],))
        conn.commit()
        conn.close()
        session.pop("password_reset_user_id", None)
        session.pop("password_reset_channel", None)
        flash("Password reset successfully. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("verify_password_reset.html", channel=channel)


@app.route("/account/recovery-phone", methods=["POST"])
@login_required
def update_recovery_phone():
    phone = normalize_phone(request.form.get("phone", ""))
    if not phone.startswith("+") or not phone[1:].isdigit():
        flash("Enter a valid mobile number in international format, such as +919876543210.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db()
    conn.execute("UPDATE users SET phone=? WHERE id=?", (phone, session["user_id"]))
    conn.commit()
    conn.close()
    flash("Recovery mobile number updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("index"))


# ---------------- USER DASHBOARD ----------------
@app.route("/dashboard")
@login_required
def dashboard():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    uid = session["user_id"]
    completed = completed_levels(uid)
    progress = total_progress(uid)

    conn = get_db()
    current_user = conn.execute(
        "SELECT phone FROM users WHERE id=?", (uid,)
    ).fetchone()
    results = conn.execute("""
        SELECT level, score, completed
        FROM level_results
        WHERE user_id=?
        ORDER BY level
    """, (uid,)).fetchall()
    saved_levels = conn.execute(
        "SELECT level FROM quiz_progress WHERE user_id=?",
        (uid,)
    ).fetchall()
    conn.close()

    result_map = {r["level"]: dict(r) for r in results}
    resume_levels = {
        row["level"] for row in saved_levels
        if row["level"] not in completed
    }
    for result in result_map.values():
        result["badge"] = score_badge(result["score"])

    return render_template(
        "dashboard.html",
        completed=completed,
        progress=progress,
        result_map=result_map,
        resume_levels=resume_levels,
        current_user_phone=current_user["phone"] if current_user else ""
    )


# ---------------- LEVELS ----------------
@app.route("/levels")
@login_required
def levels():
    uid = session["user_id"]
    completed = completed_levels(uid)

    return render_template(
        "levels.html",
        completed=completed
    )


# ---------------- QUIZ ----------------
@app.route("/quiz/<int:level>")
@login_required
def quiz(level):

    if level < 1 or level > 10:
        flash("Invalid level.", "danger")
        return redirect(url_for("dashboard"))

    uid = session["user_id"]
    completed = completed_levels(uid)

    # Unlock next level only after previous level is completed
    if level > 1 and (level - 1) not in completed:
        flash(f"Complete Level {level-1} first.", "warning")
        return redirect(url_for("levels"))

    conn = get_db()
    saved = conn.execute(
        "SELECT state_json FROM quiz_progress WHERE user_id=? AND level=?",
        (uid, level)
    ).fetchone()
    questions = conn.execute("""
        SELECT * FROM questions
        WHERE level=?
        ORDER BY id
        LIMIT 10
    """, (level,)).fetchall()

    if saved:
        state = json.loads(saved["state_json"])
        quiz_questions = state["questions"]
        answer_map = state["answer_map"]
        resume_index = state.get("current_index", 0)
        for question in quiz_questions:
            source_question = next(
                item for item in questions if item["id"] == question["id"]
            )
            rating_by_text = {
                source_question[f"option_{option}"]:
                    source_question[f"option_{option}_rating"]
                for option in "abcd"
            }
            for option in "abcd":
                question[f"option_{option}_rating"] = rating_by_text.get(
                    question[f"option_{option}"],
                    5 if option.upper() == question["correct_answer"] else 1
                )
        conn.execute(
            "UPDATE quiz_progress SET state_json=? WHERE user_id=? AND level=?",
            (json.dumps(state), uid, level)
        )
        conn.commit()
    else:
        quiz_questions = []
        answer_map = {}
        for q in questions:
            options = [
                {
                    "text": q[f"option_{option}"],
                    "rating": q[f"option_{option}_rating"],
                }
                for option in "abcd"
            ]
            correct_text = q[f"option_{q['correct_answer'].lower()}"]
            random.shuffle(options)
            correct_answer = "ABCD"[
                next(index for index, option in enumerate(options)
                     if option["text"] == correct_text)
            ]
            answer_map[str(q["id"])] = correct_answer
            quiz_questions.append({
                "id": q["id"], "question": q["question"],
                "option_a": options[0]["text"],
                "option_b": options[1]["text"],
                "option_c": options[2]["text"],
                "option_d": options[3]["text"],
                "option_a_rating": options[0]["rating"],
                "option_b_rating": options[1]["rating"],
                "option_c_rating": options[2]["rating"],
                "option_d_rating": options[3]["rating"],
                "correct_answer": correct_answer,
                "positive_message": q["positive_message"],
                "learning_message": q["learning_message"],
                "explanation": q["explanation"],
                "learning_objective": q["learning_objective"],
                "selected_answer": ""
            })
        state = {"questions": quiz_questions, "answer_map": answer_map,
                 "answers": {}, "current_index": 0}
        conn.execute("""
            INSERT INTO quiz_progress(user_id, level, state_json, updated_at)
            VALUES (?, ?, ?, ?)
        """, (uid, level, json.dumps(state), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        resume_index = 0

    conn.close()

    return render_template(
        "quiz.html",
        questions=quiz_questions,
        level=level,
        theme=LEVEL_THEMES[level],
        resume_index=resume_index,
        has_resume=bool(saved and resume_index > 0)
    )


@app.route("/quiz/<int:level>/progress", methods=["POST"])
@login_required
def save_quiz_progress(level):
    if level < 1 or level > 10:
        return {"error": "Invalid level."}, 400
    payload = request.get_json(silent=True) or {}
    try:
        current_index = max(0, min(9, int(payload["current_index"])))
        answers = payload.get("answers", {})
        if not isinstance(answers, dict):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return {"error": "Invalid progress data."}, 400

    conn = get_db()
    row = conn.execute(
        "SELECT state_json FROM quiz_progress WHERE user_id=? AND level=?",
        (session["user_id"], level)
    ).fetchone()
    if not row:
        conn.close()
        return {"error": "Quiz session not found."}, 404
    state = json.loads(row["state_json"])
    valid_ids = {str(question["id"]) for question in state["questions"]}
    state["answers"] = {
        key: value for key, value in answers.items()
        if key in valid_ids and value in {"A", "B", "C", "D"}
    }
    state["current_index"] = current_index
    for question in state["questions"]:
        question["selected_answer"] = state["answers"].get(str(question["id"]), "")
    conn.execute("""
        UPDATE quiz_progress SET state_json=?, updated_at=?
        WHERE user_id=? AND level=?
    """, (json.dumps(state), datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          session["user_id"], level))
    conn.commit()
    conn.close()
    return {"ok": True}



@app.route("/submit/<int:level>", methods=["POST"])
@login_required
def submit_level(level):
    uid = session["user_id"]

    conn = get_db()
    questions = conn.execute("""
        SELECT * FROM questions
        WHERE level=?
        ORDER BY id
        LIMIT 10
    """, (level,)).fetchall()

    if len(questions) < 10:
        conn.close()
        flash("This level does not have 10 questions yet.", "danger")
        return redirect(url_for("levels"))

    # Prevent duplicate scoring when a user refreshes/submits again.
    existing = conn.execute("""
        SELECT COUNT(*) AS n FROM answers
        WHERE user_id=? AND level=?
    """, (uid, level)).fetchone()["n"]

    if existing >= 10:
        conn.close()
        if level < 10:
            return redirect(url_for("quiz", level=level + 1))
        return redirect(url_for("dashboard"))

    progress = conn.execute(
        "SELECT state_json FROM quiz_progress WHERE user_id=? AND level=?",
        (uid, level)
    ).fetchone()
    progress_state = json.loads(progress["state_json"]) if progress else None
    answer_map = (
        progress_state["answer_map"]
        if progress_state else session.get("quiz_answers", {}).get(str(level), {})
    )
    question_rating_map = {
        str(question["id"]): {
            option: question.get(f"option_{option.lower()}_rating", 5 if option == question["correct_answer"] else 1)
            for option in "ABCD"
        }
        for question in progress_state.get("questions", [])
    } if progress_state else {}
    if len(answer_map) != len(questions):
        conn.close()
        flash("Please start the level again before submitting.", "warning")
        return redirect(url_for("quiz", level=level))

    submitted_answers = {}
    for q in questions:
        selected = request.form.get(f"q_{q['id']}", "").strip()
        if not selected:
            conn.close()
            flash("Please answer all 10 questions before submitting.", "warning")
            return redirect(url_for("quiz", level=level))
        submitted_answers[q["id"]] = selected

    score = 0

    for q in questions:
        selected = submitted_answers[q["id"]]

        correct = selected.upper() == answer_map.get(str(q["id"]))
        rating = question_rating_map.get(str(q["id"]), {}).get(
            selected.upper(), 5 if correct else 1
        )
        score += rating * 2

        conn.execute("""
            INSERT INTO answers
            (user_id, level, question_id, selected_answer, display_correct_answer,
             rating, is_correct, answered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            uid,
            level,
            q["id"],
            selected,
            answer_map.get(str(q["id"])),
            rating,
            1 if correct else 0,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

    conn.execute("""
        INSERT INTO level_results(user_id, level, score, completed, completed_at)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(user_id, level)
        DO UPDATE SET score=excluded.score,
                      completed=1,
                      completed_at=excluded.completed_at
    """, (
        uid,
        level,
        score,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.execute(
        "DELETE FROM quiz_progress WHERE user_id=? AND level=?",
        (uid, level)
    )
    conn.commit()
    conn.close()

    if level < 10:
        return redirect(url_for("quiz", level=level + 1))

    return redirect(url_for("dashboard"))


# ---------------- RESULT ----------------
@app.route("/result/<int:level>")
@login_required
def result(level):
    uid = session["user_id"]

    conn = get_db()
    row = conn.execute("""
        SELECT score, completed
        FROM level_results
        WHERE user_id=? AND level=?
    """, (uid, level)).fetchone()

    questions = conn.execute("""
SELECT
    q.*,
    a.selected_answer,
    a.is_correct,
    a.rating,
    a.display_correct_answer
FROM questions q
LEFT JOIN answers a
ON q.id = a.question_id
AND a.user_id = ?
WHERE q.level = ?
ORDER BY q.id
LIMIT 10
""", (uid, level)).fetchall()
    conn.close()

    questions = [
        dict(
            question,
            correct_answer=question["display_correct_answer"] or question["correct_answer"]
        )
        for question in questions
    ]

    if not row:
        flash("Complete the level first.", "warning")
        return redirect(url_for("levels"))

    return render_template(
        "result.html",
        level=level,
        score=row["score"],
        star_rating=min(5, (row["score"] + 10) // 20),
        questions=questions,
    )


# ---------------- CERTIFICATE ----------------
@app.route("/certificate")
@login_required
def certificate():
    uid = session["user_id"]

    conn = get_db()
    completed_count = conn.execute("""
        SELECT COUNT(*) AS n
        FROM level_results
        WHERE user_id=? AND completed=1
    """, (uid,)).fetchone()["n"]

    if completed_count < 10:
        conn.close()
        flash("Complete all 10 levels to receive your certificate.", "warning")
        return redirect(url_for("dashboard"))

    total_score = conn.execute("""
        SELECT COALESCE(SUM(score), 0) AS total
        FROM level_results
        WHERE user_id=? AND completed=1
    """, (uid,)).fetchone()["total"]

    existing = conn.execute(
        "SELECT * FROM certificates WHERE user_id=?", (uid,)
    ).fetchone()

    if existing:
        cert_number = existing["certificate_number"]
        final_score = existing["final_score"]
        percentage = existing["percentage"]
        issued_date = existing["issued_date"]
    else:
        cert_number = f"RQ-{datetime.now().strftime('%Y%m%d')}-{uid:04d}"
        final_score = total_score
        percentage = round((total_score / 1000) * 100, 2)
        issued_date = datetime.now().strftime("%d-%m-%Y")

        conn.execute("""
            INSERT INTO certificates
            (user_id, certificate_number, final_score, percentage, issued_date)
            VALUES (?, ?, ?, ?, ?)
        """, (uid, cert_number, final_score, percentage, issued_date))
        conn.commit()

    conn.close()

    pdf_path = create_certificate(
        username=session["username"],
        score=final_score,
        percentage=percentage,
        certificate_number=cert_number,
        issued_date=issued_date
    )

    return send_file(
        pdf_path,
        as_attachment=True,
        download_name="Respect_Quest_Certificate.pdf"
    )


# ---------------- ADMIN LOGIN ----------------
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND is_admin=1",
            (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["is_admin"] = 1
            return redirect(url_for("admin_dashboard"))

        flash("Invalid admin credentials.", "danger")

    return render_template("admin_login.html")


# ---------------- ADMIN DASHBOARD ----------------
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    conn = get_db()

    total_users = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE is_admin=0"
    ).fetchone()["n"]

    completed_games = conn.execute("""
        SELECT COUNT(*) AS n
        FROM (
            SELECT user_id
            FROM level_results
            WHERE completed=1
            GROUP BY user_id
            HAVING COUNT(DISTINCT level)=10
        )
    """).fetchone()["n"]

    total_certificates = conn.execute(
        "SELECT COUNT(*) AS n FROM certificates"
    ).fetchone()["n"]

    users = conn.execute("""
        SELECT
            u.id,
            u.username,
            u.email,
            u.created_at,
            COUNT(DISTINCT c.id) AS has_certificate,
            COUNT(DISTINCT CASE WHEN lr.completed=1 THEN lr.level END) AS completed_levels,
            COALESCE(SUM(CASE WHEN lr.completed=1 THEN lr.score ELSE 0 END),0) AS score
        FROM users u
        LEFT JOIN level_results lr ON u.id=lr.user_id
        LEFT JOIN certificates c ON u.id=c.user_id
        WHERE u.is_admin=0
        GROUP BY u.id
        ORDER BY u.id DESC
    """).fetchall()

    conn.close()

    return render_template(
        "admin_dashboard.html",
        total_users=total_users,
        completed_games=completed_games,
        total_certificates=total_certificates,
        users=users
    )


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    conn = get_db()
    user = conn.execute(
        "SELECT id, username, is_admin FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    if not user:
        conn.close()
        flash("User not found.", "danger")
        return redirect(url_for("admin_dashboard"))

    if user["is_admin"]:
        conn.close()
        flash("Admin accounts cannot be deleted from this page.", "danger")
        return redirect(url_for("admin_dashboard"))

    try:
        conn.execute("BEGIN")
        for table in (
            "answers",
            "level_results",
            "certificates",
            "password_reset_otps",
            "quiz_progress",
            "level_feedback",
        ):
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        app.logger.exception("Failed to delete user %s", user_id)
        flash("Unable to remove the user. Please try again.", "danger")
    else:
        flash(f"User '{user['username']}' was removed.", "success")
    finally:
        conn.close()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/user/<int:user_id>")
@admin_required
def admin_user(user_id):
    conn = get_db()

    user = conn.execute(
        "SELECT id, username, email, created_at FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    results = conn.execute("""
        SELECT level, score, completed, completed_at
        FROM level_results
        WHERE user_id=?
        ORDER BY level
    """, (user_id,)).fetchall()

    answers = conn.execute("""
        SELECT
            a.level,
            q.question,
            a.selected_answer,
            CASE
                WHEN a.is_correct=1 THEN a.selected_answer
                ELSE COALESCE(a.display_correct_answer, q.correct_answer)
            END AS correct_answer,
            a.is_correct,
            a.rating,
            a.answered_at
        FROM answers a
        JOIN questions q ON q.id=a.question_id
        WHERE a.user_id=?
        ORDER BY a.level, a.id
    """, (user_id,)).fetchall()

    certificate = conn.execute(
        "SELECT * FROM certificates WHERE user_id=?", (user_id,)
    ).fetchone()
    feedback = conn.execute("""
        SELECT level, rating, comment, created_at
        FROM level_feedback
        WHERE user_id=?
        ORDER BY level
    """, (user_id,)).fetchall()

    conn.close()

    return render_template(
        "admin_user.html",
        user=user,
        results=results,
        answers=answers,
        certificate=certificate,
        feedback=feedback
    )


@app.route("/admin/user/<int:user_id>/certificate")
@admin_required
def admin_user_certificate(user_id):
    conn = get_db()
    user = conn.execute(
        "SELECT id, username FROM users WHERE id=? AND is_admin=0",
        (user_id,)
    ).fetchone()
    certificate = conn.execute(
        "SELECT * FROM certificates WHERE user_id=?",
        (user_id,)
    ).fetchone()
    conn.close()

    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin_dashboard"))
    if not certificate:
        flash("This user does not have a certificate yet.", "warning")
        return redirect(url_for("admin_user", user_id=user_id))

    pdf_path = create_certificate(
        username=user["username"],
        score=certificate["final_score"],
        percentage=certificate["percentage"],
        certificate_number=certificate["certificate_number"],
        issued_date=certificate["issued_date"]
    )

    return send_file(
        pdf_path,
        as_attachment=request.args.get("download") == "1",
        download_name=f"{user['username']}_certificate.pdf"
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
