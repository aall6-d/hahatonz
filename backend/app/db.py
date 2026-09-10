import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "data", "assist.db"),
)


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        try:
        conn.execute("ALTER TABLE tickets ADD COLUMN alt_used INTEGER DEFAULT 0")
    except Exception:
        pass
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT, title TEXT, problem_text TEXT,
        status TEXT DEFAULT 'new', state TEXT DEFAULT 'new',
        scenario_id TEXT, question_index INTEGER DEFAULT 0,
        answers TEXT DEFAULT '[]', confidence REAL DEFAULT 0,
        created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER, role TEXT, content TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER, rating INTEGER, comment TEXT, created_at TEXT
    );
    """)
    conn.commit()
    conn.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


ALLOWED = {"category", "title", "problem_text", "status", "state",
           "scenario_id", "question_index", "answers", "confidence",
           "alt_used"}


def create_ticket(problem_text):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO tickets (problem_text, created_at, updated_at) VALUES (?,?,?)",
        (problem_text, now(), now()))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def get_ticket(ticket_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_ticket(ticket_id, **fields):
    fields = {k: v for k, v in fields.items() if k in ALLOWED}
    fields["updated_at"] = now()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = get_db()
    conn.execute(f"UPDATE tickets SET {sets} WHERE id=?",
                 (*fields.values(), ticket_id))
    conn.commit()
    conn.close()


def list_tickets():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, category, title, status, confidence, created_at "
        "FROM tickets ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_message(ticket_id, role, content):
    conn = get_db()
    conn.execute(
        "INSERT INTO messages (ticket_id, role, content, created_at) VALUES (?,?,?,?)",
        (ticket_id, role, content, now()))
    conn.commit()
    conn.close()


def get_messages(ticket_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content, created_at FROM messages "
        "WHERE ticket_id=? ORDER BY id", (ticket_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_answer(ticket_id, answer):
    t = get_ticket(ticket_id)
    answers = json.loads(t["answers"] or "[]")
    answers.append(answer)
    update_ticket(ticket_id, answers=json.dumps(answers, ensure_ascii=False))


def add_feedback(ticket_id, rating, comment):
    conn = get_db()
    conn.execute(
        "INSERT INTO feedback (ticket_id, rating, comment, created_at) VALUES (?,?,?,?)",
        (ticket_id, rating, comment, now()))
    conn.commit()
    conn.close()