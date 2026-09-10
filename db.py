"""
db.py — All Cloud SQL database logic lives here.

This file is the ONLY place that talks to the database. The agents
(in agent.py) call these functions as "tools" — they never write SQL
directly. This keeps things organized and easy to debug.
"""

import os
import datetime
from google.cloud.sql.connector import Connector
import sqlalchemy

# --- Connection setup -------------------------------------------------
INSTANCE_CONNECTION_NAME = os.environ["INSTANCE_CONNECTION_NAME"]
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ["DB_PASS"]
DB_NAME = os.environ.get("DB_NAME", "postgres")

connector = Connector()


def get_conn():
    return connector.connect(
        INSTANCE_CONNECTION_NAME,
        "pg8000",
        user=DB_USER,
        password=DB_PASS,
        db=DB_NAME,
    )


engine = sqlalchemy.create_engine("postgresql+pg8000://", creator=get_conn)


# --- User helpers -------------------------------------------------------

def get_or_create_user(telegram_chat_id: int) -> int:
    """Looks up a user by their Telegram chat ID, creating a new row if
    this is their first time messaging the bot. Returns the user_id."""
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text("SELECT user_id FROM users WHERE telegram_chat_id = :cid"),
            {"cid": telegram_chat_id},
        ).fetchone()
        if result:
            return result[0]

        result = conn.execute(
            sqlalchemy.text(
                "INSERT INTO users (telegram_chat_id) VALUES (:cid) RETURNING user_id"
            ),
            {"cid": telegram_chat_id},
        )
        conn.commit()
        return result.fetchone()[0]


def set_baby_info(user_id: int, baby_name: str = None, baby_dob: str = None, baby_gender: str = None):
    """Updates the baby's name, date of birth, and/or gender for a user,
    then reads the row back to CONFIRM the write actually landed.

    Returns a dict {"ok": bool, "baby_name":..., "baby_dob":..., "baby_gender":...}
    instead of nothing — this is what lets agent.py tell the difference
    between "actually saved" and "silently failed", so the agent can
    never falsely tell a parent their info was saved when it wasn't.
    """
    with engine.connect() as conn:
        if baby_name:
            conn.execute(
                sqlalchemy.text("UPDATE users SET baby_name = :name WHERE user_id = :uid"),
                {"name": baby_name, "uid": user_id},
            )
        if baby_dob:
            conn.execute(
                sqlalchemy.text("UPDATE users SET baby_dob = :dob WHERE user_id = :uid"),
                {"dob": baby_dob, "uid": user_id},
            )
        if baby_gender:
            conn.execute(
                sqlalchemy.text("UPDATE users SET baby_gender = :gender WHERE user_id = :uid"),
                {"gender": baby_gender, "uid": user_id},
            )
        conn.commit()

        # Verify: read the row back rather than assuming the writes above
        # succeeded. This is the actual fix for the "bot claimed it saved
        # but the row was empty" bug.
        row = conn.execute(
            sqlalchemy.text(
                "SELECT baby_name, baby_dob, baby_gender FROM users WHERE user_id = :uid"
            ),
            {"uid": user_id},
        ).fetchone()

    saved_name = row[0] if row else None
    saved_dob = str(row[1]) if row and row[1] else None
    saved_gender = row[2] if row else None

    ok = bool(saved_name and saved_dob)
    return {
        "ok": ok,
        "baby_name": saved_name,
        "baby_dob": saved_dob,
        "baby_gender": saved_gender,
    }


def has_baby_info(telegram_chat_id: int) -> bool:
    """Returns True if this parent has already told us their baby's DOB.
    Used to decide whether to run onboarding before anything else."""
    user_id = get_or_create_user(telegram_chat_id)
    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.text("SELECT baby_dob FROM users WHERE user_id = :uid"),
            {"uid": user_id},
        ).fetchone()
    return bool(row and row[0])


def get_baby_age_context(telegram_chat_id: int) -> str:
    """Returns a plain-English sentence describing the baby's age, so the
    Guidance Agent can give age-appropriate answers. Takes telegram_chat_id
    (like every other function here) and resolves it to the right user_id
    internally — this keeps every parent's data strictly scoped to them."""
    user_id = get_or_create_user(telegram_chat_id)
    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.text("SELECT baby_dob FROM users WHERE user_id = :uid"),
            {"uid": user_id},
        ).fetchone()
    if not row or not row[0]:
        return "The baby's date of birth is not on file yet."
    dob = row[0]
    age_days = (datetime.date.today() - dob).days
    months = age_days // 30
    return f"The baby is approximately {months} months old (born {dob})."


# --- Tracking Agent tool -------------------------------------------------

def log_entry(telegram_chat_id: int, category: str, details: str, logged_at: str, raw_message: str) -> str:
    """Writes one structured entry (feeding/sleep/diaper/milestone) to the
    entries table. This function is used directly as a Tracking Agent tool."""
    user_id = get_or_create_user(telegram_chat_id)
    with engine.connect() as conn:
        conn.execute(
            sqlalchemy.text(
                """INSERT INTO entries (user_id, category, details, logged_at, raw_message)
                   VALUES (:uid, :cat, :det, :logged_at, :raw)"""
            ),
            {
                "uid": user_id,
                "cat": category,
                "det": details,
                "logged_at": logged_at,
                "raw": raw_message,
            },
        )
        conn.commit()
    return f"Logged {category}: {details}"


def get_todays_entries(telegram_chat_id: int) -> str:
    """Returns a plain-text summary of today's logged entries for a parent —
    used for the 'show me today' dashboard-style request."""
    user_id = get_or_create_user(telegram_chat_id)
    with engine.connect() as conn:
        rows = conn.execute(
            sqlalchemy.text(
                """SELECT category, details, logged_at FROM entries
                   WHERE user_id = :uid AND logged_at::date = CURRENT_DATE
                   ORDER BY logged_at"""
            ),
            {"uid": user_id},
        ).fetchall()
    if not rows:
        return "No entries logged yet today."
    lines = [f"- {r[2].strftime('%I:%M %p')}: {r[0]} ({r[1]})" for r in rows]
    return "Today's entries:\n" + "\n".join(lines)


# --- Guidance Agent tool ---------------------------------------------------

def log_guidance_query(telegram_chat_id: int, question: str, answer: str):
    """Saves a parenting question + the agent's answer, for later review."""
    user_id = get_or_create_user(telegram_chat_id)
    with engine.connect() as conn:
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO guidance_queries (user_id, question, answer) VALUES (:uid, :q, :a)"
            ),
            {"uid": user_id, "q": question, "a": answer},
        )
        conn.commit()
