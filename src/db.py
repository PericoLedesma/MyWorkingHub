import datetime
import os
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_ROOT, "smart_recorder.db")


def _init():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recordings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath    TEXT    NOT NULL UNIQUE,
                source      TEXT    NOT NULL,
                start_time  TEXT    NOT NULL,
                end_time    TEXT    NOT NULL,
                duration    REAL    NOT NULL,
                sample_rate INTEGER NOT NULL,
                channels    INTEGER NOT NULL,
                file_size   INTEGER NOT NULL
            )
        """)


_init()


def save_metadata(filepath: str, source: str, start_time: datetime.datetime,
                  end_time: datetime.datetime, rate: int, channels: int) -> dict:
    duration = (end_time - start_time).total_seconds()
    file_size = os.path.getsize(filepath)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recordings "
            "(filepath, source, start_time, end_time, duration, sample_rate, channels, file_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (filepath, source, start_time.isoformat(), end_time.isoformat(),
             duration, rate, channels, file_size),
        )
    return {
        "filepath": filepath,
        "source": source,
        "start_time": start_time,
        "end_time": end_time,
        "duration": duration,
        "sample_rate": rate,
        "channels": channels,
        "file_size": file_size,
    }


def load_metadata(folder: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT filepath, source, start_time, end_time, duration, sample_rate, channels, file_size "
            "FROM recordings ORDER BY start_time DESC"
        ).fetchall()

    orphans = []
    known = set()
    recs = []
    for row in rows:
        fp, source, st, et, dur, rate, channels, fsize = row
        if not os.path.exists(fp):
            orphans.append((fp,))
            continue
        recs.append({
            "filepath": fp,
            "source": source,
            "start_time": datetime.datetime.fromisoformat(st),
            "end_time": datetime.datetime.fromisoformat(et),
            "duration": dur,
            "sample_rate": rate,
            "channels": channels,
            "file_size": fsize,
        })
        known.add(fp)

    if orphans:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany("DELETE FROM recordings WHERE filepath = ?", orphans)

    for f in sorted(os.listdir(folder)):
        if not f.endswith(".wav"):
            continue
        fp = os.path.join(folder, f)
        if fp in known:
            continue
        mtime = os.path.getmtime(fp)
        st = datetime.datetime.fromtimestamp(mtime)
        recs.append(save_metadata(fp, "unknown", st, st, 0, 0))

    recs.sort(key=lambda r: r["start_time"], reverse=True)
    return recs


def delete_metadata(filepath: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM recordings WHERE filepath = ?", (filepath,))
