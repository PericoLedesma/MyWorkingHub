import datetime
import logging
import os
import sqlite3

log = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DATA_DIR = os.environ.get("SMART_RECORDER_DATA_DIR", _DEFAULT_DATA_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "smart_recorder.db")


def _txt_path(wav_path: str) -> str:
    """Return the legacy sidecar .txt path for a given .wav file."""
    return os.path.splitext(wav_path)[0] + ".txt"


_SCHEMA_VERSION = 2


def _init():
    """Create the recordings table and migrate any sidecar .txt files into the DB."""
    log.info("Initializing DB at %s", DB_PATH)
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
            log.info("Migrating schema to v1 (transcript/project/meeting columns)")
            for col_def in (
                "ALTER TABLE recordings ADD COLUMN transcript TEXT",
                "ALTER TABLE recordings ADD COLUMN project TEXT",
                "ALTER TABLE recordings ADD COLUMN meeting TEXT",
            ):
                try:
                    conn.execute(col_def)
                except sqlite3.OperationalError:
                    pass
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_start_time ON recordings(start_time)"
            )
            if db_version == 0:
                conn.execute("INSERT INTO schema_version VALUES (?)", (_SCHEMA_VERSION,))
            else:
                conn.execute("UPDATE schema_version SET version = ?", (_SCHEMA_VERSION,))

        if db_version < 2:
            log.info("Migrating schema to v2 (summary column)")
            try:
                conn.execute("ALTER TABLE recordings ADD COLUMN summary TEXT")
            except sqlite3.OperationalError:
                pass
            conn.execute("UPDATE schema_version SET version = ?", (_SCHEMA_VERSION,))

        # Migrate any existing .txt sidecar files into the DB
        rows = conn.execute(
            "SELECT filepath FROM recordings WHERE transcript IS NULL"
        ).fetchall()
        migrated = 0
        for (fp,) in rows:
            txt = _txt_path(fp)
            if os.path.exists(txt):
                with open(txt, encoding="utf-8") as f:
                    text = f.read()
                conn.execute(
                    "UPDATE recordings SET transcript = ? WHERE filepath = ?",
                    (text, fp),
                )
                migrated += 1
        if migrated:
            log.info("Migrated %d sidecar .txt transcript(s) into DB", migrated)


_init()


def update_tag(filepath: str, field: str, value: str) -> None:
    """Update a single text tag column (project or meeting) for a recording."""
    if field not in ("project", "meeting"):
        raise ValueError(f"Unknown tag field: {field!r}")
    log.info("update_tag  %s=%r  file=%s", field, value, os.path.basename(filepath))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            f"UPDATE recordings SET {field} = ? WHERE filepath = ?",
            (value or None, filepath),
        )


def save_transcript(filepath: str, text: str) -> None:
    """Update the transcript column for an existing recording row."""
    log.info("save_transcript  chars=%d  file=%s", len(text), os.path.basename(filepath))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE recordings SET transcript = ? WHERE filepath = ?",
            (text, filepath),
        )


def get_transcript(filepath: str) -> str | None:
    """Return the transcript text for a recording, or None if not yet transcribed."""
    log.debug("get_transcript  file=%s", os.path.basename(filepath))
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT transcript FROM recordings WHERE filepath = ?", (filepath,)
        ).fetchone()
    return row[0] if row else None


def save_summary(filepath: str, text: str) -> None:
    """Persist the generated summary for a recording."""
    log.info("save_summary  chars=%d  file=%s", len(text), os.path.basename(filepath))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE recordings SET summary = ? WHERE filepath = ?",
            (text, filepath),
        )


def get_summary(filepath: str) -> str | None:
    """Return the stored summary for a recording, or None if not yet generated."""
    log.debug("get_summary  file=%s", os.path.basename(filepath))
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT summary FROM recordings WHERE filepath = ?", (filepath,)
        ).fetchone()
    return row[0] if row else None


def save_metadata(filepath: str, source: str, start_time: datetime.datetime,
                  end_time: datetime.datetime, rate: int, channels: int) -> dict:
    """Insert a new recording row and return its metadata as a dict."""
    duration = (end_time - start_time).total_seconds()
    file_size = os.path.getsize(filepath)
    log.info(
        "save_metadata  source=%s  duration=%.1fs  file=%s",
        source, duration, os.path.basename(filepath),
    )
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
        "has_summary": False,
        "project": "",
        "meeting": "",
        "file_missing": False,
        "auto_imported": False,
    }


def update_filepath(old_path: str, new_path: str) -> None:
    """Update the filepath for an existing recording row (e.g. after the file is moved)."""
    log.info(
        "update_filepath  %s -> %s",
        os.path.basename(old_path), os.path.basename(new_path),
    )
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE recordings SET filepath = ? WHERE filepath = ?",
            (new_path, old_path),
        )


def load_metadata(folder: str) -> list[dict]:
    """Load all recordings from DB, mark missing files, and pick up untracked WAVs.

    When a DB row's filepath no longer exists but a file with the same basename
    is found in *folder*, the row is reconnected to that new path so transcripts,
    summaries, and tags are preserved across data-directory moves.

    Rows whose files are missing and cannot be reconnected are returned with
    ``"file_missing": True`` instead of being silently deleted.
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT filepath, source, start_time, end_time, duration, sample_rate, channels, file_size, "
            "(transcript IS NOT NULL) as has_transcript, project, meeting, "
            "(summary IS NOT NULL) as has_summary "
            "FROM recordings ORDER BY start_time DESC"
        ).fetchall()

    # Single folder walk — used for both reconnection and untracked-file detection
    folder_wavs: dict[str, str] = {}  # basename → full path
    for root, _, files in os.walk(folder):
        for f in files:
            if f.endswith(".wav"):
                folder_wavs[f] = os.path.join(root, f)

    log.debug("load_metadata  folder=%s  rows_in_db=%d", folder, len(rows))
    path_updates: list[tuple[str, str]] = []  # (new_fp, old_fp)
    known: set[str] = set()
    recs = []
    missing_count = 0
    for row in rows:
        fp, source, st, et, dur, rate, channels, fsize, has_transcript, project, meeting, has_summary = row
        file_missing = False
        if not os.path.exists(fp):
            basename = os.path.basename(fp)
            candidate = folder_wavs.get(basename)
            if candidate and candidate not in known:
                log.info("reconnect  %s -> %s", basename, candidate)
                path_updates.append((candidate, fp))
                fp = candidate
            else:
                file_missing = True
                missing_count += 1
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
            "has_summary": bool(has_summary),
            "project": project or "",
            "meeting": meeting or "",
            "file_missing": file_missing,
            "auto_imported": False,
        })
        if not file_missing:
            known.add(fp)

    if path_updates:
        log.info("Reconnected %d recording path(s) to new folder", len(path_updates))
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "UPDATE recordings SET filepath = ? WHERE filepath = ?", path_updates
            )

    if missing_count:
        log.warning(
            "%d recording(s) have missing audio files — metadata preserved", missing_count
        )

    # Import untracked WAV files using the single walk collected above
    untracked = 0
    for basename in sorted(folder_wavs):
        fp = folder_wavs[basename]
        if fp in known:
            continue
        mtime = os.path.getmtime(fp)
        st = datetime.datetime.fromtimestamp(mtime)
        rec = save_metadata(fp, "unknown", st, st, 0, 0)
        rec["file_missing"] = False
        rec["auto_imported"] = True
        recs.append(rec)
        known.add(fp)
        untracked += 1
    if untracked:
        log.info("Imported %d untracked WAV file(s) from folder tree", untracked)

    recs.sort(key=lambda r: r["start_time"], reverse=True)
    log.debug("load_metadata  returning %d recording(s)", len(recs))
    return recs


def search_transcripts(query: str) -> list[dict]:
    """Return recordings whose transcript contains query (case-insensitive), with context excerpt."""
    log.debug("search_transcripts  query=%r", query)
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT filepath, start_time, duration, project, meeting, transcript "
            "FROM recordings WHERE transcript LIKE ? "
            "ORDER BY start_time DESC LIMIT 20",
            (f"%{query}%",),
        ).fetchall()
    results = []
    for fp, st, dur, proj, meet, transcript in rows:
        idx = transcript.lower().find(query.lower())
        start = max(0, idx - 80)
        excerpt = transcript[start : start + 200] if idx >= 0 else transcript[:200]
        results.append({
            "filepath": fp,
            "start_time": st,
            "duration": dur,
            "project": proj or "",
            "meeting": meet or "",
            "excerpt": excerpt,
        })
    log.debug("search_transcripts  query=%r  hits=%d", query, len(results))
    return results


def delete_metadata(filepath: str) -> None:
    """Remove the recording row for the given filepath."""
    log.info("delete_metadata  file=%s", os.path.basename(filepath))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM recordings WHERE filepath = ?", (filepath,))


def clear_transcript_and_summary(filepath: str) -> None:
    """Erase only the transcript and summary columns, keeping the row (and audio file ref) intact."""
    log.info("clear_transcript_and_summary  file=%s", os.path.basename(filepath))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE recordings SET transcript = NULL, summary = NULL WHERE filepath = ?",
            (filepath,),
        )
