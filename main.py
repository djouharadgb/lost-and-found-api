"""
Run:  uvicorn main:app --reload
Docs: http://127.0.0.1:8000/docs
"""
import hashlib
import os
import secrets
import sqlite3
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

DB_PATH = os.getenv("DB_PATH", "lostfound.db")

app = FastAPI(
    title="Lost & Found API",
    description="Report lost/found items, search them, and mark them as recovered.",
)

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  
    return conn


def init_db() -> None:
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            created_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tokens (
            token   TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            type         TEXT NOT NULL CHECK (type IN ('lost','found')),
            title        TEXT NOT NULL,
            description  TEXT NOT NULL DEFAULT '',
            category     TEXT NOT NULL DEFAULT 'other',
            location     TEXT NOT NULL,
            date_event   TEXT NOT NULL,
            contact      TEXT NOT NULL,
            image_url    TEXT,
            status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','recovered')),
            user_id      INTEGER NOT NULL REFERENCES users(id),
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            recovered_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_items_type_status ON items(type, status);
        """
    )
    conn.commit()
    conn.close()


init_db()


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=6, max_length=100)


class ItemCreate(BaseModel):
    type: Literal["lost", "found"]
    title: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=1000)
    category: str = Field(default="other", max_length=30)
    location: str = Field(min_length=2, max_length=150)
    date_event: date  # day it was lost / found, format YYYY-MM-DD
    contact: str = Field(min_length=3, max_length=100)
    image_url: Optional[str] = Field(default=None, max_length=500)


class ItemUpdate(BaseModel):
    """All fields optional: PATCH only changes what you send to it"""

    title: Optional[str] = Field(default=None, min_length=2, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)
    category: Optional[str] = Field(default=None, max_length=30)
    location: Optional[str] = Field(default=None, min_length=2, max_length=150)
    date_event: Optional[date] = None
    contact: Optional[str] = Field(default=None, min_length=3, max_length=100)
    image_url: Optional[str] = Field(default=None, max_length=500)


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def current_user(
    authorization: Optional[str] = Header(default=None),
    db: sqlite3.Connection = Depends(get_db),
) -> sqlite3.Row:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header (use: Bearer <token>)")
    token = authorization[len("Bearer "):]
    user = db.execute(
        "SELECT u.* FROM tokens t JOIN users u ON u.id = t.user_id WHERE t.token = ?",
        (token,),
    ).fetchone()
    if not user:
        raise HTTPException(401, "Invalid token")
    return user


@app.post("/auth/register", status_code=201, tags=["auth"])
def register(body: Credentials, db: sqlite3.Connection = Depends(get_db)):
    salt = secrets.token_hex(8)
    try:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?,?,?,?)",
            (body.username, hash_password(body.password, salt), salt, now()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Username already taken")
    return {"id": cur.lastrowid, "username": body.username}


@app.post("/auth/login", tags=["auth"])
def login(body: Credentials, db: sqlite3.Connection = Depends(get_db)):
    user = db.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
    if not user or hash_password(body.password, user["salt"]) != user["password_hash"]:
        raise HTTPException(401, "Wrong username or password")
    token = secrets.token_hex(24)
    db.execute("INSERT INTO tokens (token, user_id) VALUES (?,?)", (token, user["id"]))
    db.commit()
    return {"token": token, "token_type": "Bearer"}


def get_item_or_404(db: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise HTTPException(404, "Item not found")
    return item


def get_own_item(db: sqlite3.Connection, item_id: int, user: sqlite3.Row) -> sqlite3.Row:
    item = get_item_or_404(db, item_id)
    if item["user_id"] != user["id"]:
        raise HTTPException(403, "You can only modify your own reports")
    return item


@app.post("/items", status_code=201, tags=["items"])
def create_item(
    body: ItemCreate,
    user=Depends(current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    ts = now()
    cur = db.execute(
        """INSERT INTO items (type,title,description,category,location,date_event,
                              contact,image_url,user_id,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (body.type, body.title, body.description, body.category.lower(), body.location,
         body.date_event.isoformat(), body.contact, body.image_url, user["id"], ts, ts),
    )
    db.commit()
    return dict(get_item_or_404(db, cur.lastrowid))


@app.get("/items", tags=["items"])
def list_items(
    type: Optional[Literal["lost", "found"]] = None,
    status: Optional[Literal["open", "recovered"]] = None,
    category: Optional[str] = None,
    location: Optional[str] = Query(default=None, description="partial match"),
    q: Optional[str] = Query(default=None, description="search in title/description/location"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    sort: Literal["newest", "oldest"] = "newest",
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
):
    where, params = [], []
    if type:
        where.append("type = ?"); params.append(type)
    if status:
        where.append("status = ?"); params.append(status)
    if category:
        where.append("category = ?"); params.append(category.lower())
    if location:
        where.append("location LIKE ?"); params.append(f"%{location}%")
    if q:
        where.append("(title LIKE ? OR description LIKE ? OR location LIKE ?)")
        params += [f"%{q}%"] * 3
    if date_from:
        where.append("date_event >= ?"); params.append(date_from.isoformat())
    if date_to:
        where.append("date_event <= ?"); params.append(date_to.isoformat())

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = "DESC" if sort == "newest" else "ASC"

    total = db.execute(f"SELECT COUNT(*) FROM items {clause}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT * FROM items {clause} ORDER BY created_at {order}, id {order} LIMIT ? OFFSET ?",
        params + [limit, (page - 1) * limit],
    ).fetchall()
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
        "items": [dict(r) for r in rows],
    }


@app.get("/items/mine", tags=["items"])
def my_items(user=Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM items WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/items/{item_id}", tags=["items"])
def get_item(item_id: int, db: sqlite3.Connection = Depends(get_db)):
    return dict(get_item_or_404(db, item_id))


@app.patch("/items/{item_id}", tags=["items"])
def update_item(
    item_id: int,
    body: ItemUpdate,
    user=Depends(current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    get_own_item(db, item_id, user)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update")
    if "date_event" in changes:
        changes["date_event"] = changes["date_event"].isoformat()
    if "category" in changes and changes["category"]:
        changes["category"] = changes["category"].lower()
    changes["updated_at"] = now()
    # keys come from the Pydantic model (never from raw user input) so this is safe
    sets = ", ".join(f"{k} = ?" for k in changes)
    db.execute(f"UPDATE items SET {sets} WHERE id = ?", [*changes.values(), item_id])
    db.commit()
    return dict(get_item_or_404(db, item_id))


@app.patch("/items/{item_id}/recover", tags=["items"])
def mark_recovered(
    item_id: int,
    user=Depends(current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    item = get_own_item(db, item_id, user)
    if item["status"] == "recovered":
        raise HTTPException(409, "Item is already marked as recovered")
    ts = now()
    db.execute(
        "UPDATE items SET status='recovered', recovered_at=?, updated_at=? WHERE id=?",
        (ts, ts, item_id),
    )
    db.commit()
    return dict(get_item_or_404(db, item_id))


@app.delete("/items/{item_id}", status_code=204, tags=["items"])
def delete_item(
    item_id: int,
    user=Depends(current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    get_own_item(db, item_id, user)
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
