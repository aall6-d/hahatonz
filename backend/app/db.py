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
    # WAL для быстрой конкурентной записи
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT, title TEXT, problem_text TEXT,
        status TEXT DEFAULT 'open', state TEXT DEFAULT 'new',
        scenario_id TEXT, question_index INTEGER DEFAULT 0,
        answers TEXT DEFAULT '[]', confidence REAL DEFAULT 0,
        alt_used INTEGER DEFAULT 0,
        retry_count INTEGER DEFAULT 0,
        pending_category TEXT,
        escalated_at TEXT,
        taken_by TEXT,
        rated_at TEXT,
        parent_id INTEGER,
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
    CREATE INDEX IF NOT EXISTS idx_messages_ticket_created
        ON messages(ticket_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_tickets_status_escalated
        ON tickets(status, escalated_at);
    """)
    # Миграция старых таблиц
    for ddl in (
        "ALTER TABLE tickets ADD COLUMN alt_used INTEGER DEFAULT 0",
        "ALTER TABLE tickets ADD COLUMN retry_count INTEGER DEFAULT 0",
        "ALTER TABLE tickets ADD COLUMN pending_category TEXT",
        "ALTER TABLE tickets ADD COLUMN escalated_at TEXT",
        "ALTER TABLE tickets ADD COLUMN taken_by TEXT",
        "ALTER TABLE tickets ADD COLUMN rated_at TEXT",
        "ALTER TABLE tickets ADD COLUMN parent_id INTEGER",
    ):
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.commit()
    conn.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


ALLOWED = {"category", "title", "problem_text", "status", "state",
           "scenario_id", "question_index", "answers", "confidence",
           "alt_used", "retry_count", "pending_category",
           "escalated_at", "taken_by", "rated_at", "parent_id"}


def create_ticket(problem_text, parent_id=None):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO tickets (problem_text, parent_id, created_at, updated_at) "
        "VALUES (?,?,?,?)",
        (problem_text, parent_id, now(), now()))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def get_ticket(ticket_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
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


def take_ticket_atomic(ticket_id, agent):
    """Атомарный take: только если status='escalated'."""
    conn = get_db()
    cur = conn.execute(
        "UPDATE tickets SET status='in_progress', taken_by=?, "
        "updated_at=? WHERE id=? AND status='escalated'",
        (agent, now(), ticket_id))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected > 0


def list_tickets():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, category, title, status, confidence, created_at "
        "FROM tickets ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def queue_tickets():
    """Очередь специалиста: escalated + in_progress, FIFO по escalated_at."""
    conn = get_db()
    rows = conn.execute(
        "SELECT t.id, t.category, t.confidence, t.status, t.escalated_at, "
        "t.taken_by, t.title, t.problem_text FROM tickets t "
        "WHERE t.status IN ('escalated','in_progress') "
        "ORDER BY COALESCE(t.escalated_at, t.created_at) ASC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # last_user_text — последнее сообщение пользователя
        last = conn.execute(
            "SELECT content FROM messages WHERE ticket_id=? AND role='user' "
            "ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone()
        d["last_user_text"] = last["content"] if last else (d["problem_text"] or "")
        # waited_minutes
        if d.get("escalated_at"):
            try:
                dt = datetime.strptime(d["escalated_at"], "%Y-%m-%d %H:%M:%S")
                d["waited_minutes"] = max(0, int((datetime.now() - dt).total_seconds() / 60))
            except Exception:
                d["waited_minutes"] = 0
        else:
            d["waited_minutes"] = 0
        out.append(d)
    conn.close()
    return out


def add_message(ticket_id, role, content):
    conn = get_db()
    conn.execute(
        "INSERT INTO messages (ticket_id, role, content, created_at) "
        "VALUES (?,?,?,?)", (ticket_id, role, content, now()))
    conn.commit()
    conn.close()


def get_messages(ticket_id):
    """Для API поллинга: id, role (assistant→ai), text, created_at."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, role, content as text, created_at FROM messages "
        "WHERE ticket_id=? ORDER BY id ASC", (ticket_id,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("role") == "assistant":
            d["role"] = "ai"
        out.append(d)
    return out


def add_answer(ticket_id, answer):
    t = get_ticket(ticket_id)
    answers = json.loads(t["answers"] or "[]")
    answers.append(answer)
    update_ticket(ticket_id, answers=json.dumps(answers, ensure_ascii=False))


def add_feedback(ticket_id, rating, comment):
    conn = get_db()
    conn.execute(
        "INSERT INTO feedback (ticket_id, rating, comment, created_at) "
        "VALUES (?,?,?,?)", (ticket_id, rating, comment, now()))
    conn.commit()
    conn.close()


def get_feedback(ticket_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT rating, comment, created_at FROM feedback "
        "WHERE ticket_id=?", (ticket_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ticket_already_rated(ticket_id):
    t = get_ticket(ticket_id)
    return bool(t and t.get("rated_at"))


def mark_rated(ticket_id):
    update_ticket(ticket_id, rated_at=now())


def support_stats(period_days):
    """Агрегация по периоду (1 день или 7 дней)."""
    conn = get_db()
    since = (datetime.now() - __import__("datetime").timedelta(days=period_days)
             ).strftime("%Y-%m-%d %H:%M:%S")
    total = conn.execute(
        "SELECT COUNT(*) as c FROM tickets WHERE created_at>=?",
        (since,)).fetchone()["c"]
    ai_res = conn.execute(
        "SELECT COUNT(*) as c FROM tickets WHERE created_at>=? AND status='resolved'",
        (since,)).fetchone()["c"]
    spec_res = conn.execute(
        "SELECT COUNT(*) as c FROM tickets WHERE created_at>=? "
        "AND status='resolved_by_specialist'", (since,)).fetchone()["c"]
    # avg_wait_minutes: для тикетов с escalated_at и resolved_by_specialist
    rows_wait = conn.execute(
        "SELECT escalated_at, updated_at FROM tickets "
        "WHERE status='resolved_by_specialist' AND escalated_at IS NOT NULL "
        "AND created_at>=?", (since,)).fetchall()
    waits = []
    for r in rows_wait:
        try:
            t1 = datetime.strptime(r["escalated_at"], "%Y-%m-%d %H:%M:%S")
            t2 = datetime.strptime(r["updated_at"], "%Y-%m-%d %H:%M:%S")
            waits.append((t2 - t1).total_seconds() / 60)
        except Exception:
            pass
    avg_wait = round(sum(waits) / len(waits), 1) if waits else 0.0
    # avg_rating
    avg_rating_row = conn.execute(
        "SELECT AVG(rating) as a FROM feedback WHERE created_at>=?",
        (since,)).fetchone()
    avg_rating = round(avg_rating_row["a"], 2) if avg_rating_row["a"] else None
    # top categories
    top_rows = conn.execute(
        "SELECT category, COUNT(*) as c FROM tickets "
        "WHERE created_at>=? AND category IS NOT NULL "
        "GROUP BY category ORDER BY c DESC LIMIT 8",
        (since,)).fetchall()
    top = []
    for r in top_rows:
        share = r["c"] / total if total else 0
        top.append({"category": r["category"] or "без категории",
                    "count": r["c"], "share": round(share, 3)})
    conn.close()
    return {
        "total": total,
        "ai_resolved": ai_res,
        "specialist_resolved": spec_res,
        "avg_wait_minutes": avg_wait,
        "avg_rating": avg_rating,
        "top": top,
    }