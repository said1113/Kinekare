"""
storage.py
----------
Kullanıcı verilerini SQLite'ta kalıcı olarak saklar (data/users.db).

Tablolar:
  profiles     → kullanıcının /kesfet seçimleri ve son önerileri
  chat_memory  → kullanıcı başına son 10 soru-cevap

Dosya ilk çalıştırmada otomatik oluşturulur. Ek kütüphane gerekmez
(sqlite3 Python ile birlikte gelir).
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "users.db")
MEMORY_SIZE = 10


@contextmanager
def _connect():
    # Her işlem kendi kısa ömürlü bağlantısını açar; böylece iş parçacığı sorunu çıkmaz.
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def init_db() -> None:
    """Tabloları yoksa oluşturur. Bot açılırken bir kez çağrılır."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id         INTEGER PRIMARY KEY,
                tags            TEXT NOT NULL DEFAULT '[]',
                styles          TEXT NOT NULL DEFAULT '[]',
                value_choices   TEXT NOT NULL DEFAULT '[]',
                recommendations TEXT NOT NULL DEFAULT '[]',
                updated_at      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_memory (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                question   TEXT NOT NULL,
                answer     TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_user ON chat_memory(user_id, id);
        """)


# --------------------------------------------------------------------------- #
# Profil
# --------------------------------------------------------------------------- #
def save_profile(user_id: int, tags: list[str], styles: list[str],
                 values: list[str]) -> None:
    """Menü seçimlerini kaydeder (varsa günceller). Son öneriler korunur."""
    with _connect() as conn:
        conn.execute("""
            INSERT INTO profiles (user_id, tags, styles, value_choices, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                tags = excluded.tags,
                styles = excluded.styles,
                value_choices = excluded.value_choices,
                updated_at = excluded.updated_at
        """, (user_id,
              json.dumps(tags, ensure_ascii=False),
              json.dumps(styles, ensure_ascii=False),
              json.dumps(values, ensure_ascii=False),
              _now()))


def save_recommendations(user_id: int, names: list[str]) -> None:
    """AI'ın son önerdiği meslek adlarını kaydeder."""
    with _connect() as conn:
        conn.execute(
            "UPDATE profiles SET recommendations = ?, updated_at = ? WHERE user_id = ?",
            (json.dumps(names, ensure_ascii=False), _now(), user_id))


def get_profile(user_id: int) -> dict | None:
    """Profili döndürür: {tags, styles, values, recommendations, updated_at} ya da None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return {
        "tags": json.loads(row["tags"]),
        "styles": json.loads(row["styles"]),
        "values": json.loads(row["value_choices"]),
        "recommendations": json.loads(row["recommendations"]),
        "updated_at": row["updated_at"],
    }


# --------------------------------------------------------------------------- #
# Sohbet hafızası
# --------------------------------------------------------------------------- #
def add_memory(user_id: int, question: str, answer: str) -> None:
    """Soru-cevabı ekler; kullanıcının en yeni MEMORY_SIZE kaydı dışındakileri siler."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chat_memory (user_id, question, answer, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, question, answer, _now()))
        conn.execute("""
            DELETE FROM chat_memory
            WHERE user_id = ? AND id NOT IN (
                SELECT id FROM chat_memory WHERE user_id = ?
                ORDER BY id DESC LIMIT ?
            )
        """, (user_id, user_id, MEMORY_SIZE))


def get_memory(user_id: int) -> list[tuple[str, str]]:
    """Son MEMORY_SIZE soru-cevabı eskiden yeniye sıralı döndürür."""
    with _connect() as conn:
        rows = conn.execute("""
            SELECT question, answer FROM (
                SELECT id, question, answer FROM chat_memory
                WHERE user_id = ? ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
        """, (user_id, MEMORY_SIZE)).fetchall()
    return [(r["question"], r["answer"]) for r in rows]


def memory_count(user_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM chat_memory WHERE user_id = ?", (user_id,)).fetchone()
    return row["n"]


# --------------------------------------------------------------------------- #
# Silme
# --------------------------------------------------------------------------- #
def delete_user(user_id: int) -> None:
    """Kullanıcının profilini ve sohbet hafızasını kalıcı olarak siler."""
    with _connect() as conn:
        conn.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM chat_memory WHERE user_id = ?", (user_id,))