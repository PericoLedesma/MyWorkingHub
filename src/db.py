import datetime
import os
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.environ.get("SMART_RECORDER_DATA_DIR", _ROOT)
DB_PATH = os.path.join(_DATA_DIR, "smart_recorder.db")


def _txt_path(wav_path: str) -> str:
    """Return the legacy sidecar .txt path for a given .wav file."""
    return os.path.splitext(wav_path)[0] + ".txt"


_SCHEMA_VERSION = 1


def _init():
    """Create the recordings table and migrate any sidecar .txt files into the DB."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)
        """)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        db_version = row[0] if row else 0

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

        if db_version < 1:
            for col_def in (
                "ALTER TABLE recordings ADD COLUMN transcript TEXT",
                "ALTER TABLE recordings ADD COLUMN project TEXT",
                "ALTER TABLE recordings ADD COLUMN meeting TEXT",
            ):
                try:
                    conn.execute(col_def)
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_start_time ON recordings(start_time)"
            )
            if db_version == 0:
                conn.execute("INSERT INTO schema_version VALUES (?)", (_SCHEMA_VERSION,))
            else:
                conn.execute("UPDATE schema_version SET version = ?", (_SCHEMA_VERSION,))

        # Migrate any existing .txt sidecar files into the DB
        rows = conn.execute(
            "SELECT filepath FROM recordings WHERE transcript IS NULL"
        ).fetchall()
        for (fp,) in rows:
            txt = _txt_path(fp)
            if os.path.exists(txt):
                with open(txt, encoding="utf-8") as f:
                    text = f.read()
                conn.execute(
                    "UPDATE recordings SET transcript = ? WHERE filepath = ?",
                    (text, fp),
                )


_init()


def update_tag(filepath: str, field: str, value: str) -> None:
    """Update a single text tag column (project or meeting) for a recording."""
    if field not in ("project", "meeting"):
        raise ValueError(f"Unknown tag field: {field!r}")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            f"UPDATE recordings SET {field} = ? WHERE filepath = ?",
            (value or None, filepath),
        )


def save_transcript(filepath: str, text: str) -> None:
    """Update the transcript column for an existing recording row."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE recordings SET transcript = ? WHERE filepath = ?",
            (text, filepath),
        )


def get_transcript(filepath: str) -> str | None:
    """Return the transcript text for a recording, or None if not yet transcribed."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT transcript FROM recordings WHERE filepath = ?", (filepath,)
        ).fetchone()
    return row[0] if row else None


def save_metadata(filepath: str, source: str, start_time: datetime.datetime,
                  end_time: datetime.datetime, rate: int, channels: int) -> dict:
    """Insert a new recording row and return its metadata as a dict."""
    duration = (end_time - start_time).total_seconds()
    file_size = os.path.getsize(filepath)
    with sqlite3.connect(DB_PATH) as conn:
        # INSERT OR IGNORE so re-importing the same file is a no-op
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
        "has_transcript": False,
        "project": "",
        "meeting": "",
    }


def load_metadata(folder: str) -> list[dict]:
    """Load all recordings from DB, prune orphans, and pick up untracked WAV files."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT filepath, source, start_time, end_time, duration, sample_rate, channels, file_size, "
            "(transcript IS NOT NULL) as has_transcript, project, meeting "
            "FROM recordings ORDER BY start_time DESC"
        ).fetchall()

    orphans = []
    known = set()  # filepaths already in the DB, used to skip re-import below
    recs = []
    for row in rows:
        fp, source, st, et, dur, rate, channels, fsize, has_transcript, project, meeting = row
        if not os.path.exists(fp):
            # file was deleted outside the app; clean up the DB record
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
            "has_transcript": bool(has_transcript),
            "project": project or "",
            "meeting": meeting or "",
        })
        known.add(fp)

    if orphans:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany("DELETE FROM recordings WHERE filepath = ?", orphans)

    # pick up .wav files that exist on disk but have no DB row (e.g. copied manually)
    for f in sorted(os.listdir(folder)):
        if not f.endswith(".wav"):
            continue
        fp = os.path.join(folder, f)
        if fp in known:
            continue
        mtime = os.path.getmtime(fp)
        st = datetime.datetime.fromtimestamp(mtime)
        recs.append({**save_metadata(fp, "unknown", st, st, 0, 0), "has_transcript": False})

    recs.sort(key=lambda r: r["start_time"], reverse=True)
    return recs


def delete_metadata(filepath: str) -> None:
    """Remove the recording row for the given filepath."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM recordings WHERE filepath = ?", (filepath,))
