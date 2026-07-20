import datetime
import logging
import os
import sqlite3

from src.db import DATA_DIR

log = logging.getLogger(__name__)

FOCUS_DB_PATH = os.path.join(DATA_DIR, "focus_time.db")


def _init():
    with sqlite3.connect(FOCUS_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS work_sessions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                date             TEXT NOT NULL,
                start_time       TEXT NOT NULL,
                end_time         TEXT,
                duration_seconds REAL
            )
        """)


_init()


def start_work_session() -> dict:
    now = datetime.datetime.now()
    date = now.date().isoformat()
    start_time = now.isoformat()
    with sqlite3.connect(FOCUS_DB_PATH) as conn:
        # Close any dangling open sessions before starting a new one
        open_sessions = conn.execute(
            "SELECT id, start_time FROM work_sessions WHERE end_time IS NULL"
        ).fetchall()
        for sid, sstart in open_sessions:
            start_dt = datetime.datetime.fromisoformat(sstart)
            duration = (now - start_dt).total_seconds()
            conn.execute(
                "UPDATE work_sessions SET end_time = ?, duration_seconds = ? WHERE id = ?",
                (now.isoformat(), duration, sid),
            )
            log.info("auto-closed dangling session id=%d  duration=%.1fs", sid, duration)

        cur = conn.execute(
            "INSERT INTO work_sessions (date, start_time) VALUES (?, ?)",
            (date, start_time),
        )
        session_id = cur.lastrowid
    log.info("start_work_session  id=%d  start=%s", session_id, start_time)
    return {"id": session_id, "start_time": start_time}


def end_work_session(session_id: int) -> None:
    now = datetime.datetime.now()
    with sqlite3.connect(FOCUS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT start_time FROM work_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return
        start = datetime.datetime.fromisoformat(row[0])
        duration = (now - start).total_seconds()
        conn.execute(
            "UPDATE work_sessions SET end_time = ?, duration_seconds = ? WHERE id = ?",
            (now.isoformat(), duration, session_id),
        )
    log.info("end_work_session  id=%d  duration=%.1fs", session_id, duration)


def list_work_sessions(date: str | None = None) -> list[dict]:
    with sqlite3.connect(FOCUS_DB_PATH) as conn:
        if date:
            rows = conn.execute(
                "SELECT id, date, start_time, end_time, duration_seconds "
                "FROM work_sessions WHERE date = ? ORDER BY start_time DESC",
                (date,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, date, start_time, end_time, duration_seconds "
                "FROM work_sessions ORDER BY start_time DESC"
            ).fetchall()
    return [
        {
            "id": r[0],
            "date": r[1],
            "start_time": r[2],
            "end_time": r[3],
            "duration_seconds": r[4],
        }
        for r in rows
    ]


def delete_work_session(session_id: int) -> None:
    log.info("delete_work_session  id=%d", session_id)
    with sqlite3.connect(FOCUS_DB_PATH) as conn:
        conn.execute("DELETE FROM work_sessions WHERE id = ?", (session_id,))


def delete_sessions_by_date(date: str) -> int:
    with sqlite3.connect(FOCUS_DB_PATH) as conn:
        cur = conn.execute("DELETE FROM work_sessions WHERE date = ?", (date,))
        count = cur.rowcount
    log.info("delete_sessions_by_date  date=%s  deleted=%d", date, count)
    return count
