
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
import time
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
    2: "📚 Equality",
    3: "👧 Girl Child",
    4: "📖 Education",
    5: "🛡️ Children's Rights",
    6: "💛 Empathy",
    7: "🔒 Safety",
    8: "🏠 Family and Society",
    9: "⚖️ Decision Making",
    10: "🏆 BBBP Awareness"
}
COMMUNITY_ACTION_CHALLENGES = {
    1: {
        "title": "Share a message of respect",
        "description": "Share or discuss one practical way to show respect and dignity in everyday life."
    },
    2: {
        "title": "Encourage equal learning",
        "description": "Talk with someone about how girls and boys can have equal opportunities to learn."
    },
    3: {
        "title": "Challenge a stereotype",
        "description": "When you encounter a gender stereotype, respond respectfully with an example that challenges it."
    },
    4: {
        "title": "Share responsibilities",
        "description": "Take part in a household or group responsibility and encourage fair sharing."
    },
    5: {
        "title": "Support safe learning",
        "description": "Learn about a trusted local resource that supports safety and education for girls."
    },
    6: {
        "title": "Know your rights",
        "description": "Read or share information about a right or support resource from a reliable source."
    },
    7: {
        "title": "Make space for inclusion",
        "description": "Invite someone to participate and consider how an activity can be more inclusive."
    },
    8: {
        "title": "Encourage an aspiration",
        "description": "Encourage a girl or young woman to explore a subject, leadership role, or career that interests her."
    },
    9: {
        "title": "Practice respectful communication",
        "description": "Practice listening without interruption and responding respectfully in a conversation."
    },
    10: {
        "title": "Spread awareness thoughtfully",
        "description": "Share one reliable resource about equality, respect, or girls' education with your community."
    }
}

# ---------------- DATABASE ----------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def install_scenario_question_bank(conn):
    bank_path = BASE_DIR / "data" / "ar_scenario_questions.json"
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    version = bank.get("version")
    levels = bank.get("levels")
    if not version or not isinstance(levels, list) or len(levels) != 10:
        raise ValueError("The AR scenario question bank must contain 10 levels.")

    questions_to_add = []
    for expected_level, level_data in enumerate(levels, start=1):
        questions = level_data.get("questions")
        if level_data.get("level") != expected_level or not isinstance(questions, list) or len(questions) != 10:
            raise ValueError(f"Question bank level {expected_level} must contain 10 questions.")
        for expected_number, item in enumerate(questions, start=1):
            options = item.get("options", {})
            correct = item.get("correct_answer")
            if (
                item.get("number") != expected_number
                or not item.get("question")
                or set(options) != set("abcd")
                or correct not in {"A", "B", "C", "D"}
            ):
                raise ValueError(
                    f"Invalid question data at level {expected_level}, question {expected_number}."
                )
            questions_to_add.append((expected_level, level_data["title"], item))

    installed = conn.execute(
        "SELECT 1 FROM question_bank_migrations WHERE version=?",
        (version,)
    ).fetchone()
    if installed:
        return

    conn.execute("UPDATE questions SET is_active=0")
    for level, title, item in questions_to_add:
        options = item["options"]
        correct_answer = item["correct_answer"]
        best_response = options[correct_answer.lower()]
        learning_message = (
            f"The most helpful response is “{best_response}”. "
            f"It reflects {title.lower()}."
        )
        conn.execute("""
            INSERT INTO questions (
                level, is_active, question, option_a, option_b, option_c, option_d,
                correct_answer, option_a_rating, option_b_rating, option_c_rating,
                option_d_rating, explanation, learning_objective, difficulty,
                positive_message, learning_message
            )
            VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scenario', ?, ?)
        """, (
            level,
            item["question"],
            options["a"],
            options["b"],
            options["c"],
            options["d"],
            correct_answer,
            *(5 if option.upper() == correct_answer else 1 for option in "abcd"),
            learning_message,
            title,
            "Good choice. You selected the most helpful response.",
            learning_message
        ))
    conn.execute(
        "INSERT INTO question_bank_migrations(version, installed_at) VALUES (?, ?)",
        (version, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )


def fetch_questions_by_ids(conn, question_ids):
    if not question_ids:
        return []
    ids = [int(question_id) for question_id in question_ids]
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM questions WHERE id IN ({placeholders})",
        ids
    ).fetchall()
    questions_by_id = {row["id"]: row for row in rows}
    return [
        questions_by_id[question_id]
        for question_id in ids
        if question_id in questions_by_id
    ]


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
    is_active INTEGER NOT NULL DEFAULT 1,
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

    CREATE TABLE IF NOT EXISTS community_action_checkins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        level INTEGER NOT NULL,
        reflection TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(user_id, level),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS question_bank_migrations (
        version TEXT PRIMARY KEY,
        installed_at TEXT NOT NULL
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

    CREATE TABLE IF NOT EXISTS multiplayer_rooms (
        code TEXT PRIMARY KEY,
        host_user_id INTEGER NOT NULL,
        level INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'waiting',
        question_index INTEGER NOT NULL DEFAULT 0,
        question_started_at REAL,
        question_ids TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(host_user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS multiplayer_players (
        room_code TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        score INTEGER NOT NULL DEFAULT 0,
        joined_at TEXT NOT NULL,
        PRIMARY KEY(room_code, user_id),
        FOREIGN KEY(room_code) REFERENCES multiplayer_rooms(code),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS multiplayer_answers (
        room_code TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        question_index INTEGER NOT NULL,
        selected_answer TEXT NOT NULL,
        is_correct INTEGER NOT NULL,
        points INTEGER NOT NULL,
        answered_at REAL NOT NULL,
        PRIMARY KEY(room_code, user_id, question_index),
        FOREIGN KEY(room_code, user_id)
            REFERENCES multiplayer_players(room_code, user_id)
    );
    """)

    conn.execute("""
        UPDATE multiplayer_answers
        SET points=CASE WHEN is_correct=1 THEN ? ELSE 0 END
        WHERE points != CASE WHEN is_correct=1 THEN ? ELSE 0 END
    """, (MULTIPLAYER_POINTS_PER_CORRECT, MULTIPLAYER_POINTS_PER_CORRECT))
    conn.execute("""
        UPDATE multiplayer_players
        SET score=COALESCE((
            SELECT SUM(a.points)
            FROM multiplayer_answers a
            WHERE a.room_code=multiplayer_players.room_code
              AND a.user_id=multiplayer_players.user_id
        ), 0)
        WHERE score != COALESCE((
            SELECT SUM(a.points)
            FROM multiplayer_answers a
            WHERE a.room_code=multiplayer_players.room_code
              AND a.user_id=multiplayer_players.user_id
        ), 0)
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
    if "is_active" not in question_columns:
        conn.execute(
            "ALTER TABLE questions ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
        question_columns.add("is_active")
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

    install_scenario_question_bank(conn)

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


MULTIPLAYER_QUESTION_COUNT = 10
MULTIPLAYER_PLAYER_LIMIT = 4
MULTIPLAYER_QUESTION_SECONDS = 30
MULTIPLAYER_POINTS_PER_CORRECT = 10
SOLO_QUESTION_SECONDS = 30


def multiplayer_player_in_room(conn, room_code, user_id):
    return conn.execute(
        "SELECT 1 FROM multiplayer_players WHERE room_code=? AND user_id=?",
        (room_code, user_id)
    ).fetchone()


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
        return {"title": "Gold Tier", "icon": "🥇", "class": "gold"}
    if score >= 51:
        return {"title": "Silver Tier", "icon": "🥈", "class": "silver"}
    return {"title": "Bronze Tier", "icon": "🥉", "class": "bronze"}


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
    action_checkins = conn.execute("""
        SELECT level, reflection, created_at
        FROM community_action_checkins
        WHERE user_id=?
        ORDER BY created_at DESC
    """, (uid,)).fetchall()
    conn.close()

    result_map = {r["level"]: dict(r) for r in results}
    resume_levels = {
        row["level"] for row in saved_levels
        if row["level"] not in completed
    }
    for result in result_map.values():
        result["badge"] = score_badge(result["score"])
    completed_results = [
        result for result in result_map.values() if result["completed"]
    ]
    impact_stats = {
        "levels_completed": len(completed),
        "score_earned": sum(result["score"] for result in completed_results),
        "gold_badges": sum(
            result["badge"]["class"] == "gold" for result in completed_results
        )
    }
    action_checkin_map = {row["level"]: dict(row) for row in action_checkins}
    action_challenges = [
        {
            "level": level,
            **challenge,
            "checkin": action_checkin_map.get(level)
        }
        for level, challenge in COMMUNITY_ACTION_CHALLENGES.items()
        if level in completed
    ]
    leaderboard_url = url_for("leaderboard", _external=True)
    impact_share_text = (
        f"I've completed {impact_stats['levels_completed']} of 10 Respect Quest "
        f"levels, earned {impact_stats['score_earned']} points, and received "
        f"{impact_stats['gold_badges']} Gold badges while learning about "
        f"respect, equality, and dignity. Join the journey: {leaderboard_url}"
    )

    return render_template(
        "dashboard.html",
        completed=completed,
        progress=progress,
        result_map=result_map,
        resume_levels=resume_levels,
        current_user_phone=current_user["phone"] if current_user else "",
        impact_stats=impact_stats,
        action_challenges=action_challenges,
        action_checkins_count=len(action_checkins),
        impact_share_text=impact_share_text,
        leaderboard_url=leaderboard_url
    )


@app.route("/community-actions/<int:level>/complete", methods=["POST"])
@login_required
def complete_community_action(level):
    if session.get("is_admin"):
        flash("Community action challenges are for player accounts.", "warning")
        return redirect(url_for("admin_dashboard"))
    if level not in COMMUNITY_ACTION_CHALLENGES:
        flash("Choose a valid community action challenge.", "danger")
        return redirect(url_for("dashboard"))
    if request.form.get("completed") != "yes":
        flash("Confirm that you completed the action before recording it.", "warning")
        return redirect(url_for("dashboard"))

    reflection = request.form.get("reflection", "").strip()
    if len(reflection) > 500:
        flash("Keep your reflection to 500 characters or fewer.", "danger")
        return redirect(url_for("dashboard"))

    uid = session["user_id"]
    conn = get_db()
    level_result = conn.execute(
        "SELECT 1 FROM level_results WHERE user_id=? AND level=? AND completed=1",
        (uid, level)
    ).fetchone()
    if not level_result:
        conn.close()
        flash("Complete this quiz level before recording its action challenge.", "warning")
        return redirect(url_for("dashboard"))

    try:
        conn.execute("""
            INSERT INTO community_action_checkins(user_id, level, reflection, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            uid,
            level,
            reflection,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        flash("You have already recorded the action for this level.", "info")
        return redirect(url_for("dashboard"))
    conn.close()
    flash(
        "Thanks for recording your self-reported community action. "
        "It is not independently verified.",
        "success"
    )
    return redirect(url_for("dashboard"))


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


@app.route("/virtual-ar")
@login_required
def virtual_ar_home():
    completed = completed_levels(session["user_id"])
    level = next(
        (
            candidate for candidate in range(1, 11)
            if candidate not in completed
            and (candidate == 1 or candidate - 1 in completed)
        ),
        None
    )
    if level is None:
        return redirect(url_for("levels"))
    return redirect(url_for("virtual_ar", level=level))


@app.route("/leaderboard")
def leaderboard():
    conn = get_db()
    players = conn.execute("""
        SELECT u.id AS user_id,
               u.username,
               COUNT(DISTINCT lr.level) AS levels_completed,
               COALESCE(SUM(lr.score), 0) AS total_score
        FROM users u
        JOIN level_results lr ON lr.user_id=u.id AND lr.completed=1
        WHERE u.is_admin=0
        GROUP BY u.id, u.username
        ORDER BY total_score DESC, levels_completed DESC, u.username COLLATE NOCASE
        LIMIT 10
    """).fetchall()
    conn.close()
    return render_template(
        "leaderboard.html",
        players=players,
        current_user_id=session.get("user_id")
    )


@app.route("/community-leaderboard")
def community_leaderboard():
    conn = get_db()
    players = conn.execute("""
        SELECT u.id AS user_id,
               u.username,
               COUNT(DISTINCT lr.level) AS levels_completed,
               COALESCE(SUM(lr.score), 0) AS total_score,
               SUM(CASE WHEN lr.score >= 90 THEN 1 ELSE 0 END) AS gold_badges
        FROM users u
        JOIN level_results lr ON lr.user_id=u.id AND lr.completed=1
        WHERE u.is_admin=0
        GROUP BY u.id, u.username
        ORDER BY levels_completed DESC, total_score DESC, u.username COLLATE NOCASE
    """).fetchall()
    action_summary = conn.execute("""
        SELECT COUNT(*) AS checkins,
               COUNT(DISTINCT user_id) AS participants
        FROM community_action_checkins
    """).fetchone()
    conn.close()

    community_stats = {
        "players": len(players),
        "levels_completed": sum(player["levels_completed"] for player in players),
        "gold_badges": sum(player["gold_badges"] for player in players),
        "action_checkins": action_summary["checkins"],
        "action_participants": action_summary["participants"]
    }
    return render_template(
        "community_leaderboard.html",
        players=players,
        community_stats=community_stats,
        current_user_id=session.get("user_id")
    )


# ---------------- MULTIPLAYER CHALLENGE ----------------
@app.route("/multiplayer")
@login_required
def multiplayer():
    return render_template("multiplayer.html")


@app.route("/multiplayer/create", methods=["POST"])
@login_required
def multiplayer_create():
    try:
        level = int(request.form.get("level", ""))
    except ValueError:
        flash("Choose a valid level.", "danger")
        return redirect(url_for("multiplayer"))
    if not 1 <= level <= 10:
        flash("Choose a valid level.", "danger")
        return redirect(url_for("multiplayer"))

    conn = get_db()
    questions = conn.execute(
        "SELECT id FROM questions WHERE level=? AND is_active=1 ORDER BY RANDOM() LIMIT ?",
        (level, MULTIPLAYER_QUESTION_COUNT)
    ).fetchall()
    if len(questions) < MULTIPLAYER_QUESTION_COUNT:
        conn.close()
        flash("This level needs 10 questions before it can be played.", "warning")
        return redirect(url_for("multiplayer"))

    conn.execute("BEGIN IMMEDIATE")
    code = None
    for _ in range(5):
        candidate = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        if not conn.execute(
            "SELECT 1 FROM multiplayer_rooms WHERE code=?", (candidate,)
        ).fetchone():
            code = candidate
            break
    if code is None:
        conn.rollback()
        conn.close()
        app.logger.error("Could not generate a unique multiplayer room code.")
        flash("Could not create a room right now. Please try again.", "danger")
        return redirect(url_for("multiplayer"))

    conn.execute("""
        INSERT INTO multiplayer_rooms
        (code, host_user_id, level, question_ids, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (
        code,
        session["user_id"],
        level,
        json.dumps([question["id"] for question in questions]),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.execute("""
        INSERT INTO multiplayer_players (room_code, user_id, joined_at)
        VALUES (?, ?, ?)
    """, (code, session["user_id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return redirect(url_for("multiplayer_room", code=code))


@app.route("/multiplayer/join", methods=["POST"])
@login_required
def multiplayer_join():
    code = request.form.get("code", "").strip().upper()
    if len(code) != 6 or not code.isalnum():
        flash("Enter a valid six-character room code.", "danger")
        return redirect(url_for("multiplayer"))

    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    room = conn.execute(
        "SELECT status FROM multiplayer_rooms WHERE code=?", (code,)
    ).fetchone()
    if not room:
        conn.rollback()
        conn.close()
        flash("That room code was not found.", "danger")
        return redirect(url_for("multiplayer"))
    if room["status"] != "waiting":
        conn.rollback()
        conn.close()
        flash("That match has already started.", "warning")
        return redirect(url_for("multiplayer"))

    existing = multiplayer_player_in_room(conn, code, session["user_id"])
    player_count = conn.execute(
        "SELECT COUNT(*) AS count FROM multiplayer_players WHERE room_code=?",
        (code,)
    ).fetchone()["count"]
    if not existing and player_count >= MULTIPLAYER_PLAYER_LIMIT:
        conn.rollback()
        conn.close()
        flash("That room is full.", "warning")
        return redirect(url_for("multiplayer"))
    if not existing:
        conn.execute("""
            INSERT INTO multiplayer_players (room_code, user_id, joined_at)
            VALUES (?, ?, ?)
        """, (code, session["user_id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    conn.close()
    return redirect(url_for("multiplayer_room", code=code))


@app.route("/multiplayer/<code>")
@login_required
def multiplayer_room(code):
    code = code.upper()
    conn = get_db()
    room = conn.execute(
        "SELECT code, host_user_id, level, status FROM multiplayer_rooms WHERE code=?",
        (code,)
    ).fetchone()
    is_player = multiplayer_player_in_room(conn, code, session["user_id"])
    conn.close()
    if not room or not is_player:
        flash("Join a multiplayer room before opening it.", "warning")
        return redirect(url_for("multiplayer"))
    return render_template(
        "multiplayer.html",
        room=dict(room),
        is_host=room["host_user_id"] == session["user_id"],
        player_limit=MULTIPLAYER_PLAYER_LIMIT,
        current_user_id=session["user_id"]
    )


@app.route("/multiplayer/<code>/start", methods=["POST"])
@login_required
def multiplayer_start(code):
    code = code.upper()
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    room = conn.execute(
        "SELECT host_user_id, status FROM multiplayer_rooms WHERE code=?",
        (code,)
    ).fetchone()
    if not room or room["host_user_id"] != session["user_id"]:
        conn.rollback()
        conn.close()
        return {"error": "Only the room host can start the match."}, 403
    if room["status"] != "waiting":
        conn.rollback()
        conn.close()
        return {"error": "This match has already started."}, 409
    player_count = conn.execute(
        "SELECT COUNT(*) AS count FROM multiplayer_players WHERE room_code=?",
        (code,)
    ).fetchone()["count"]
    if player_count < 2:
        conn.rollback()
        conn.close()
        return {"error": "At least two players are needed to start."}, 400
    conn.execute("""
        UPDATE multiplayer_rooms
        SET status='active', question_index=0, question_started_at=?
        WHERE code=?
    """, (time.time(), code))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.route("/multiplayer/<code>/state", methods=["POST"])
@login_required
def multiplayer_state(code):
    code = code.upper()
    uid = session["user_id"]
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    room = conn.execute(
        "SELECT * FROM multiplayer_rooms WHERE code=?", (code,)
    ).fetchone()
    if not room or not multiplayer_player_in_room(conn, code, uid):
        conn.rollback()
        conn.close()
        return {"error": "Multiplayer room not found."}, 404

    question_ids = json.loads(room["question_ids"])
    index = room["question_index"]
    if room["status"] == "active":
        player_count = conn.execute(
            "SELECT COUNT(*) AS count FROM multiplayer_players WHERE room_code=?",
            (code,)
        ).fetchone()["count"]
        answered_count = conn.execute("""
            SELECT COUNT(*) AS count FROM multiplayer_answers
            WHERE room_code=? AND question_index=?
        """, (code, index)).fetchone()["count"]
        expired = time.time() - room["question_started_at"] >= MULTIPLAYER_QUESTION_SECONDS
        if answered_count >= player_count or expired:
            next_index = index + 1
            if next_index >= len(question_ids):
                conn.execute(
                    "UPDATE multiplayer_rooms SET status='completed' WHERE code=?",
                    (code,)
                )
            else:
                conn.execute("""
                    UPDATE multiplayer_rooms
                    SET question_index=?, question_started_at=?
                    WHERE code=?
                """, (next_index, time.time(), code))
            room = conn.execute(
                "SELECT * FROM multiplayer_rooms WHERE code=?", (code,)
            ).fetchone()
            index = room["question_index"]

    players = conn.execute("""
        SELECT p.user_id, u.username, p.score
        FROM multiplayer_players p
        JOIN users u ON u.id=p.user_id
        WHERE p.room_code=?
        ORDER BY p.score DESC, p.joined_at, u.username
    """, (code,)).fetchall()
    payload = {
        "current_user_id": uid,
        "room": {
            "code": room["code"],
            "level": room["level"],
            "status": room["status"],
            "question_index": index,
            "question_count": len(question_ids),
            "time_remaining": None,
            "host_user_id": room["host_user_id"],
            "is_host": uid == room["host_user_id"],
        },
        "players": [
            {
                "user_id": player["user_id"],
                "username": player["username"],
                "score": player["score"],
                "answered": False,
                "is_host": player["user_id"] == room["host_user_id"],
            }
            for player in players
        ],
        "question": None,
        "own_answer": None,
        "reveal": None,
    }

    if room["status"] == "active":
        question_row = conn.execute(
            "SELECT * FROM questions WHERE id=?", (question_ids[index],)
        ).fetchone()
        if not question_row:
            conn.rollback()
            conn.close()
            app.logger.error(
                "Multiplayer room %s references missing question %s",
                code, question_ids[index]
            )
            return {"error": "A question in this match is no longer available."}, 500
        payload["question"] = {
            "number": index + 1,
            "prompt": question_row["question"],
            "options": [
                {"key": key, "text": question_row[f"option_{key.lower()}"]}
                for key in "ABCD"
            ],
        }
        payload["room"]["time_remaining"] = max(
            0,
            MULTIPLAYER_QUESTION_SECONDS - int(time.time() - room["question_started_at"])
        )
        current_answers = conn.execute("""
            SELECT user_id, selected_answer, is_correct, points
            FROM multiplayer_answers
            WHERE room_code=? AND question_index=?
        """, (code, index)).fetchall()
        answers_by_user = {answer["user_id"]: answer for answer in current_answers}
        for player in payload["players"]:
            player["answered"] = player["user_id"] in answers_by_user
        answer = answers_by_user.get(uid)
        if answer:
            payload["own_answer"] = {
                "selected_answer": answer["selected_answer"],
                "is_correct": bool(answer["is_correct"]),
                "points": answer["points"],
            }

    if index > 0:
        previous_answer = conn.execute("""
            SELECT a.selected_answer, a.is_correct, a.points, q.correct_answer
            FROM multiplayer_answers a
            JOIN questions q ON q.id=?
            WHERE a.room_code=? AND a.user_id=? AND a.question_index=?
        """, (question_ids[index - 1], code, uid, index - 1)).fetchone()
        if previous_answer:
            payload["reveal"] = {
                "selected_answer": previous_answer["selected_answer"],
                "correct_answer": previous_answer["correct_answer"],
                "is_correct": bool(previous_answer["is_correct"]),
                "points": previous_answer["points"],
            }

    conn.commit()
    conn.close()
    return payload


@app.route("/multiplayer/<code>/answer", methods=["POST"])
@login_required
def multiplayer_answer(code):
    code = code.upper()
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return {"error": "Choose a valid answer."}, 400
    selected = payload.get("answer")
    if selected not in {"A", "B", "C", "D"}:
        return {"error": "Choose a valid answer."}, 400

    uid = session["user_id"]
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    room = conn.execute(
        "SELECT * FROM multiplayer_rooms WHERE code=?", (code,)
    ).fetchone()
    if not room or not multiplayer_player_in_room(conn, code, uid):
        conn.rollback()
        conn.close()
        return {"error": "Multiplayer room not found."}, 404
    if room["status"] != "active":
        conn.rollback()
        conn.close()
        return {"error": "This match is not accepting answers."}, 409
    if time.time() - room["question_started_at"] >= MULTIPLAYER_QUESTION_SECONDS:
        conn.rollback()
        conn.close()
        return {"error": "Time is up for this question."}, 409

    index = room["question_index"]
    existing = conn.execute("""
        SELECT 1 FROM multiplayer_answers
        WHERE room_code=? AND user_id=? AND question_index=?
    """, (code, uid, index)).fetchone()
    if existing:
        conn.rollback()
        conn.close()
        return {"error": "You already answered this question."}, 409

    question_ids = json.loads(room["question_ids"])
    question = conn.execute(
        "SELECT correct_answer FROM questions WHERE id=?",
        (question_ids[index],)
    ).fetchone()
    if not question:
        conn.rollback()
        conn.close()
        app.logger.error("Question %s missing in multiplayer room %s", question_ids[index], code)
        return {"error": "The question is no longer available."}, 500
    is_correct = selected == question["correct_answer"]
    answered_at = time.time()
    points = MULTIPLAYER_POINTS_PER_CORRECT if is_correct else 0
    conn.execute("""
        INSERT INTO multiplayer_answers
        (room_code, user_id, question_index, selected_answer,
         is_correct, points, answered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (code, uid, index, selected, int(is_correct), points, answered_at))
    conn.execute(
        "UPDATE multiplayer_players SET score=score+? WHERE room_code=? AND user_id=?",
        (points, code, uid)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "is_correct": is_correct, "points": points}


# ---------------- QUIZ ----------------
@app.route("/quiz/<int:level>")
@app.route("/virtual-ar/<int:level>", endpoint="virtual_ar")
@login_required
def quiz(level):

    if level < 1 or level > 10:
        flash("Invalid level.", "danger")
        return redirect(url_for("dashboard"))

    uid = session["user_id"]
    completed = completed_levels(uid)

    if level in completed:
        return redirect(url_for("result", level=level))

    # Unlock next level only after previous level is completed
    if level > 1 and (level - 1) not in completed:
        flash(f"Complete Level {level-1} first.", "warning")
        return redirect(url_for("levels"))

    conn = get_db()
    saved = conn.execute(
        "SELECT state_json FROM quiz_progress WHERE user_id=? AND level=?",
        (uid, level)
    ).fetchone()
    if saved:
        state = json.loads(saved["state_json"])
        quiz_questions = state["questions"]
        questions = fetch_questions_by_ids(
            conn, [question["id"] for question in quiz_questions]
        )
        if len(questions) != len(quiz_questions):
            conn.close()
            app.logger.error(
                "Saved quiz state for user %s level %s references missing questions.",
                uid, level
            )
            flash("Your saved quiz could not be resumed. Please contact support.", "danger")
            return redirect(url_for("dashboard"))
        answer_map = state["answer_map"]
        resume_index = state.get("current_index", 0)
        timed_out_questions = set(state.get("timed_out", []))
        question_started_at = state.setdefault("question_started_at", {})
        current_question_id = str(quiz_questions[resume_index]["id"])
        if current_question_id not in question_started_at:
            question_started_at[current_question_id] = time.time()
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
            question["timed_out"] = str(question["id"]) in timed_out_questions
            question["started_at"] = question_started_at.get(str(question["id"]))
        conn.execute(
            "UPDATE quiz_progress SET state_json=? WHERE user_id=? AND level=?",
            (json.dumps(state), uid, level)
        )
        conn.commit()
    else:
        questions = conn.execute("""
            SELECT * FROM questions
            WHERE level=? AND is_active=1
            ORDER BY id
            LIMIT 10
        """, (level,)).fetchall()
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
                "selected_answer": "",
                "timed_out": False,
                "started_at": None
            })
        question_started_at = {str(quiz_questions[0]["id"]): time.time()}
        quiz_questions[0]["started_at"] = question_started_at[
            str(quiz_questions[0]["id"])
        ]
        state = {"questions": quiz_questions, "answer_map": answer_map,
                 "answers": {}, "current_index": 0, "timed_out": [],
                 "question_started_at": question_started_at}
        conn.execute("""
            INSERT INTO quiz_progress(user_id, level, state_json, updated_at)
            VALUES (?, ?, ?, ?)
        """, (uid, level, json.dumps(state), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        resume_index = 0

    conn.close()

    return render_template(
        "virtual_ar.html" if request.endpoint == "virtual_ar" else "quiz.html",
        questions=quiz_questions,
        level=level,
        theme=LEVEL_THEMES[level],
        resume_index=resume_index,
        has_resume=bool(saved and resume_index > 0),
        question_seconds=SOLO_QUESTION_SECONDS
    )


@app.route("/quiz/<int:level>/progress", methods=["POST"])
@login_required
def save_quiz_progress(level):
    if level < 1 or level > 10:
        return {"error": "Invalid level."}, 400
    if level in completed_levels(session["user_id"]):
        return {"error": "This level is already completed."}, 409
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
        if key in valid_ids and value in {"", "A", "B", "C", "D"}
    }
    timed_out = payload.get("timed_out", [])
    if not isinstance(timed_out, list):
        conn.close()
        return {"error": "Invalid timed-out question data."}, 400
    state["timed_out"] = [
        key for key in timed_out if isinstance(key, str) and key in valid_ids
    ]
    state["current_index"] = current_index
    question_started_at = state.setdefault("question_started_at", {})
    current_question_id = str(state["questions"][current_index]["id"])
    question_started_at.setdefault(current_question_id, time.time())
    for question in state["questions"]:
        question["selected_answer"] = state["answers"].get(str(question["id"]), "")
        question["timed_out"] = str(question["id"]) in state["timed_out"]
        question["started_at"] = question_started_at.get(str(question["id"]))
    conn.execute("""
        UPDATE quiz_progress SET state_json=?, updated_at=?
        WHERE user_id=? AND level=?
    """, (json.dumps(state), datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          session["user_id"], level))
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "question_started_at": question_started_at[current_question_id],
    }



@app.route("/submit/<int:level>", methods=["POST"])
@login_required
def submit_level(level):
    uid = session["user_id"]

    conn = get_db()
    completed = conn.execute(
        "SELECT 1 FROM level_results WHERE user_id=? AND level=? AND completed=1",
        (uid, level)
    ).fetchone()
    if completed:
        conn.close()
        return redirect(url_for("result", level=level))

    # Prevent duplicate scoring when a user refreshes/submits again.
    existing = conn.execute("""
        SELECT COUNT(*) AS n FROM answers
        WHERE user_id=? AND level=?
    """, (uid, level)).fetchone()["n"]

    if existing >= 10:
        conn.close()
        return redirect(url_for("result", level=level))

    progress = conn.execute(
        "SELECT state_json FROM quiz_progress WHERE user_id=? AND level=?",
        (uid, level)
    ).fetchone()
    progress_state = json.loads(progress["state_json"]) if progress else None
    if progress_state:
        questions = fetch_questions_by_ids(
            conn,
            [question["id"] for question in progress_state["questions"]]
        )
    else:
        questions = conn.execute("""
            SELECT * FROM questions
            WHERE level=? AND is_active=1
            ORDER BY id
            LIMIT 10
        """, (level,)).fetchall()
    if len(questions) < 10:
        conn.close()
        flash("This level does not have 10 questions yet.", "danger")
        return redirect(url_for("levels"))

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
        timed_out = (
            progress_state
            and str(q["id"]) in progress_state.get("timed_out", [])
        )
        if not selected and not timed_out:
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
        ) if selected else 0
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

    return redirect(url_for("result", level=level))


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
        SELECT q.*, a.selected_answer, a.is_correct, a.rating,
               a.display_correct_answer
        FROM answers a
        JOIN questions q ON q.id=a.question_id
        WHERE a.user_id=? AND a.level=?
        ORDER BY a.id
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
        badge=score_badge(row["score"]),
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
