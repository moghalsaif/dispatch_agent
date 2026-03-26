from __future__ import annotations
import sqlite3
import uuid
import time
from contextlib import contextmanager

DB_PATH = "caller_agent.db"


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_phone TEXT,
                product TEXT,
                quantity TEXT,
                status TEXT DEFAULT 'pending',
                batch_call_id TEXT,
                created_at REAL,
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS vendor_calls (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                vendor_name TEXT,
                vendor_phone TEXT,
                vendor_website TEXT,
                listed_price TEXT,
                conversation_id TEXT,
                status TEXT DEFAULT 'pending',
                price_quoted TEXT,
                lead_time TEXT,
                contact_name TEXT,
                can_fulfill INTEGER DEFAULT 1,
                notes TEXT,
                source TEXT DEFAULT 'db',
                created_at REAL,
                updated_at REAL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS vendors (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE,
                phone TEXT,
                website TEXT,
                supplies TEXT,
                min_order INTEGER DEFAULT 0,
                max_order INTEGER DEFAULT 999999,
                notes TEXT,
                active INTEGER DEFAULT 1,
                created_at REAL
            );
            CREATE TABLE IF NOT EXISTS telegram_users (
                phone TEXT PRIMARY KEY,
                chat_id TEXT UNIQUE,
                username TEXT,
                registered_at REAL
            );
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Sessions ────────────────────────────────────────────────────────────────

def create_session(user_phone: str, product: str, quantity: str) -> str:
    sid = str(uuid.uuid4())
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
            (sid, user_phone, product, quantity, "researching", None, now, now),
        )
    return sid


def set_batch_call_id(session_id: str, batch_call_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET batch_call_id=?, status='calling', updated_at=? WHERE id=?",
            (batch_call_id, time.time(), session_id),
        )


def get_session(session_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None


def all_sessions() -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def check_session_complete(session_id: str) -> bool:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM vendor_calls WHERE session_id=?", (session_id,)).fetchone()[0]
        done = conn.execute(
            "SELECT COUNT(*) FROM vendor_calls WHERE session_id=? AND status IN ('completed','failed','no_fulfill')",
            (session_id,),
        ).fetchone()[0]
        if total > 0 and total == done:
            conn.execute("UPDATE sessions SET status='done', updated_at=? WHERE id=?", (time.time(), session_id))
            return True
    return False


# ── Vendor Calls ─────────────────────────────────────────────────────────────

def create_vendor_call(session_id: str, vendor_name: str, vendor_phone: str,
                       vendor_website: str, listed_price: str, source: str = "db") -> str:
    vid = str(uuid.uuid4())
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO vendor_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, session_id, vendor_name, vendor_phone, vendor_website,
             listed_price, None, "pending", None, None, None, 1, None, source, now, now),
        )
    return vid


def update_vendor_call(conversation_id: str, **kwargs):
    kwargs["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [conversation_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE vendor_calls SET {sets} WHERE conversation_id=?", vals)


def update_vendor_call_by_id(vendor_call_id: str, **kwargs):
    kwargs["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [vendor_call_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE vendor_calls SET {sets} WHERE id=?", vals)


def get_vendor_calls(session_id: str) -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM vendor_calls WHERE session_id=?", (session_id,)).fetchall()
        return [dict(r) for r in rows]


def get_vendor_call_by_conv(conversation_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM vendor_calls WHERE conversation_id=?", (conversation_id,)).fetchone()
        return dict(row) if row else None


# ── Vendors (your known vendor directory) ───────────────────────────────────

def add_vendor(name: str, phone: str, website: str, supplies: str,
               min_order: int = 0, max_order: int = 999999, notes: str = "") -> str:
    vid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO vendors VALUES (?,?,?,?,?,?,?,?,?,?)",
            (vid, name, phone, website, supplies, min_order, max_order, notes, 1, time.time()),
        )
    return vid


def get_known_vendor(name: str) -> dict | None:
    """Case-insensitive lookup by name."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM vendors WHERE LOWER(name)=LOWER(?) AND active=1", (name,)
        ).fetchone()
        return dict(row) if row else None


def search_known_vendors(product: str, quantity: int) -> list:
    """Find vendors who supply this product and can handle the quantity."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM vendors WHERE active=1 AND min_order<=? AND max_order>=?",
            (quantity, quantity),
        ).fetchall()
    # Further filter by supplies keyword match
    results = []
    for r in rows:
        d = dict(r)
        supplies = (d.get("supplies") or "").lower()
        if any(kw in supplies for kw in product.lower().split()):
            results.append(d)
    return results


def list_vendors() -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM vendors ORDER BY name").fetchall()
        return [dict(r) for r in rows]


# ── Telegram users ───────────────────────────────────────────────────────────

def link_telegram(phone: str, chat_id: str, username: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO telegram_users VALUES (?,?,?,?)",
            (phone, str(chat_id), username, time.time()),
        )


def get_telegram_chat_id(phone: str) -> str | None:
    # Normalize phone: try both with and without country code variations
    with get_conn() as conn:
        row = conn.execute(
            "SELECT chat_id FROM telegram_users WHERE phone=? OR phone=?",
            (phone, phone.lstrip("+1")),
        ).fetchone()
        return row["chat_id"] if row else None
