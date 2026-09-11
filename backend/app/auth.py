import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from fastapi import HTTPException

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "data", "assist.db"),
)

DEMO_SPECIALIST_LOGIN = "support"
DEMO_SPECIALIST_PASSWORD = "akti2026"
TOKEN_TTL_DAYS = 30


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_auth():
    conn = _conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL,
        name TEXT,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TEXT,
        expires_at TEXT
    );
    """)
    try:
        conn.execute("ALTER TABLE tickets ADD COLUMN user_id INTEGER")
    except Exception:
        pass
    conn.commit()
    conn.close()


def create_user(role, name=None):
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO users (role, name, created_at) VALUES (?,?,?)",
        (role, name or ("Специалист поддержки" if role == "specialist"
                        else "Сотрудник"), _now()))
    conn.commit()
    uid = cur.lastrowid
    conn.close()
    return uid


def issue_token(user_id):
    token = secrets.token_hex(24)
    exp = (datetime.now() + timedelta(days=TOKEN_TTL_DAYS)
           ).strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) "
        "VALUES (?,?,?,?)", (token, user_id, _now(), exp))
    conn.commit()
    conn.close()
    return token


def login(role, login_name=None, password=None):
    role = (role or "").strip().lower()
    if role == "specialist":
        if ((login_name or "").strip() != DEMO_SPECIALIST_LOGIN
                or (password or "").strip() != DEMO_SPECIALIST_PASSWORD):
            raise HTTPException(status_code=401,
                                detail="Неверный логин или пароль специалиста")
        conn = _conn()
        row = conn.execute(
            "SELECT id FROM users WHERE role='specialist' "
            "ORDER BY id LIMIT 1").fetchone()
        conn.close()
        uid = row["id"] if row else create_user("specialist")
        return {"token": issue_token(uid), "role": "specialist"}
    if role == "user":
        return {"token": issue_token(create_user("user")), "role": "user"}
    raise HTTPException(status_code=400, detail="Неизвестная роль")


def extract_token(authorization):
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return authorization


def get_user_by_token(token):
    if not token:
        return None
    conn = _conn()
    row = conn.execute(
        "SELECT s.expires_at, u.id, u.role, u.name FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token=?",
        (token,)).fetchone()
    conn.close()
    if not row or row["expires_at"] < _now():
        return None
    return {"id": row["id"], "role": row["role"], "name": row["name"]}


def require_user(authorization):
    user = get_user_by_token(extract_token(authorization))
    if not user or user["role"] != "user":
        raise HTTPException(status_code=401,
                            detail="Требуется вход как пользователь")
    return user


def bind_ticket(ticket_id, user_id):
    conn = _conn()
    conn.execute("UPDATE tickets SET user_id=? WHERE id=?",
                 (user_id, ticket_id))
    conn.commit()
    conn.close()


def ticket_ids_of(user_id):
    conn = _conn()
    rows = conn.execute(
        "SELECT id FROM tickets WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    return {r["id"] for r in rows}