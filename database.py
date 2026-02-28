import aiosqlite
from datetime import datetime, timedelta

DB_PATH = "tracker.db"

DEFAULT_CATEGORIES = ["work", "study", "sport", "other"]


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                UNIQUE(user_id, name)
            );

            CREATE TABLE IF NOT EXISTS time_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                task_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                stopped_at TEXT,
                duration_seconds INTEGER,
                FOREIGN KEY (category_id) REFERENCES categories(id)
            );
        """)
        await db.commit()


async def ensure_default_categories(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        for name in DEFAULT_CATEGORIES:
            await db.execute(
                "INSERT OR IGNORE INTO categories (user_id, name) VALUES (?, ?)",
                (user_id, name),
            )
        await db.commit()


async def get_categories(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name FROM categories WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def add_category(user_id: int, name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO categories (user_id, name) VALUES (?, ?)",
                (user_id, name.lower()),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def start_entry(user_id: int, category_id: int, task_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        await db.execute(
            "INSERT INTO time_entries (user_id, category_id, task_name, started_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, category_id, task_name, now),
        )
        await db.commit()


async def get_active_entry(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT te.id, te.task_name, te.started_at, c.name AS category "
            "FROM time_entries te "
            "JOIN categories c ON te.category_id = c.id "
            "WHERE te.user_id = ? AND te.stopped_at IS NULL "
            "ORDER BY te.id DESC LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def stop_active_entry(user_id: int) -> dict | None:
    entry = await get_active_entry(user_id)
    if not entry:
        return None

    now = datetime.now()
    started = datetime.fromisoformat(entry["started_at"])
    duration = int((now - started).total_seconds())

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE time_entries SET stopped_at = ?, duration_seconds = ? "
            "WHERE id = ?",
            (now.isoformat(), duration, entry["id"]),
        )
        await db.commit()

    entry["duration_seconds"] = duration
    return entry


async def get_stats(user_id: int, days: int = 7) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT c.name AS category, SUM(te.duration_seconds) AS total "
            "FROM time_entries te "
            "JOIN categories c ON te.category_id = c.id "
            "WHERE te.user_id = ? AND te.stopped_at IS NOT NULL "
            "AND te.started_at >= ? "
            "GROUP BY c.name ORDER BY total DESC",
            (user_id, since),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_history(user_id: int, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT te.task_name, c.name AS category, "
            "te.started_at, te.duration_seconds "
            "FROM time_entries te "
            "JOIN categories c ON te.category_id = c.id "
            "WHERE te.user_id = ? AND te.stopped_at IS NOT NULL "
            "ORDER BY te.id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
