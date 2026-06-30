"""
SQLite storage for the Seymour bot.

Tables
------
users(discord_id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE)
items(id INTEGER PK, discord_id INTEGER, item_name TEXT, hex_color TEXT)
    UNIQUE(discord_id, item_name, hex_color) -- re-importing the same file
    won't create duplicate rows; importing a second/different file merges in.
"""
import sqlite3
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

DB_PATH = Path(__file__).parent / "seymour.db"


class Item(NamedTuple):
    item_name: str
    hex_color: str
    discord_id: int
    username: str


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


"""
SQLite storage for the Seymour bot.

Tables
------
users(discord_id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE)
items(id INTEGER PK, discord_id INTEGER, item_name TEXT, hex_color TEXT)
    No uniqueness constraint on (item_name, hex_color) -- every line from
    every import is kept as its own row, duplicates included on purpose.
"""
import sqlite3
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

DB_PATH = Path(__file__).parent / "seymour.db"


class Item(NamedTuple):
    item_name: str
    hex_color: str
    discord_id: int
    username: str


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_drop_unique_constraint(conn: sqlite3.Connection) -> None:
    """
    Older DBs created the items table with
    UNIQUE(discord_id, item_name, hex_color), which silently dropped
    duplicate lines on import. Detect that and rebuild the table without
    the constraint, preserving all existing rows.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchone()
    if not row or not row[0] or "UNIQUE(discord_id" not in row[0]:
        return  # already migrated, or table doesn't exist yet

    conn.executescript(
        """
        ALTER TABLE items RENAME TO items_old;

        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            hex_color TEXT NOT NULL,
            FOREIGN KEY(discord_id) REFERENCES users(discord_id)
        );

        INSERT INTO items (id, discord_id, item_name, hex_color)
            SELECT id, discord_id, item_name, hex_color FROM items_old;

        DROP TABLE items_old;
        """
    )
    conn.commit()


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                discord_id INTEGER PRIMARY KEY,
                username TEXT UNIQUE COLLATE NOCASE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                hex_color TEXT NOT NULL,
                FOREIGN KEY(discord_id) REFERENCES users(discord_id)
            )
            """
        )
        _migrate_drop_unique_constraint(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_hex ON items(hex_color)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_discord_id ON items(discord_id)")
        conn.commit()
    finally:
        conn.close()


def upsert_user(discord_id: int, username: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO users (discord_id, username) VALUES (?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET username = excluded.username
            """,
            (discord_id, username),
        )
        conn.commit()
    finally:
        conn.close()


def bulk_insert_items(discord_id: int, rows: Iterable[tuple[str, str]]) -> int:
    """
    Insert (item_name, hex_color) rows for a user. Every row is kept,
    including exact duplicates. Returns the number of rows inserted.
    """
    conn = _connect()
    try:
        rows = list(rows)
        conn.executemany(
            "INSERT INTO items (discord_id, item_name, hex_color) VALUES (?, ?, ?)",
            [(discord_id, name, hex_color) for name, hex_color in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def clear_user_items(discord_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM items WHERE discord_id = ?", (discord_id,))
        conn.commit()
    finally:
        conn.close()


def get_all_items() -> list[Item]:
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT items.item_name, items.hex_color, items.discord_id, users.username
            FROM items
            JOIN users ON users.discord_id = items.discord_id
            """
        )
        return [Item(*row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_items_for_user(discord_id: int) -> list[Item]:
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT items.item_name, items.hex_color, items.discord_id, users.username
            FROM items
            JOIN users ON users.discord_id = items.discord_id
            WHERE items.discord_id = ?
            """,
            (discord_id,),
        )
        return [Item(*row) for row in cur.fetchall()]
    finally:
        conn.close()


def find_user_by_username(username: str) -> Optional[tuple[int, str]]:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT discord_id, username FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else None
    finally:
        conn.close()


def all_usernames(prefix: str = "") -> list[str]:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT username FROM users WHERE username LIKE ? COLLATE NOCASE ORDER BY username",
            (f"{prefix}%",),
        )
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def item_count_for_user(discord_id: int) -> int:
    conn = _connect()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM items WHERE discord_id = ?", (discord_id,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def total_item_count() -> int:
    conn = _connect()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM items")
        return cur.fetchone()[0]
    finally:
        conn.close()


def item_counts_by_user() -> list[tuple[str, int]]:
    """Returns [(username, item_count), ...] for every registered user, highest first."""
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT users.username, COUNT(items.id) AS cnt
            FROM users
            LEFT JOIN items ON items.discord_id = users.discord_id
            GROUP BY users.discord_id
            ORDER BY cnt DESC, users.username COLLATE NOCASE ASC
            """
        )
        return [(row[0], row[1]) for row in cur.fetchall()]
    finally:
        conn.close()