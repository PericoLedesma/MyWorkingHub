import datetime
import logging
import os
import sqlite3

log = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = r"C:\Users\p.ledesma.jimenez\OneDrive - Accenture\Desktop\MyDB"
DATA_DIR = os.environ.get("SMART_RECORDER_DATA_DIR", _DEFAULT_DATA_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "smart_recorder.db")


def _txt_path(wav_path: str) -> str:
    """Return the legacy sidecar .txt path for a given .wav file."""
    return os.path.splitext(wav_path)[0] + ".txt"


_SCHEMA_VERSION = 4


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

        if db_version < 3:
            log.info("Migrating schema to v3 (transcript_x column)")
            try:
                conn.execute("ALTER TABLE recordings ADD COLUMN transcript_x TEXT")
            except sqlite3.OperationalError:
                pass
            conn.execute("UPDATE schema_version SET version = ?", (_SCHEMA_VERSION,))

        if db_version < 4:
            log.info("Migrating schema to v4 (language column)")
            try:
                conn.execute("ALTER TABLE recordings ADD COLUMN language TEXT")
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


def update_tag(recording_id: int, field: str, value: str) -> None:
    """Update a single text tag column (project or meeting) for a recording."""
    if field not in ("project", "meeting"):
        raise ValueError(f"Unknown tag field: {field!r}")
    log.info("update_tag  %s=%r  id=%d", field, value, recording_id)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            f"UPDATE recordings SET {field} = ? WHERE id = ?",
            (value or None, recording_id),
        )


def save_transcript(recording_id: int, text: str) -> None:
    """Update the transcript column for an existing recording row."""
    log.info("save_transcript  chars=%d  id=%d", len(text), recording_id)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE recordings SET transcript = ? WHERE id = ?",
            (text, recording_id),
        )


def get_transcript(recording_id: int) -> str | None:
    """Return the transcript text for a recording, or None if not yet transcribed."""
    log.debug("get_transcript  id=%d", recording_id)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT transcript FROM recordings WHERE id = ?", (recording_id,)
        ).fetchone()
    return row[0] if row else None


def save_summary(recording_id: int, text: str) -> None:
    """Persist the generated summary for a recording."""
    log.info("save_summary  chars=%d  id=%d", len(text), recording_id)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE recordings SET summary = ? WHERE id = ?",
            (text, recording_id),
        )


def save_language(recording_id: int, language: str) -> None:
    """Persist the detected language for a recording."""
    log.info("save_language  language=%s  id=%d", language, recording_id)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE recordings SET language = ? WHERE id = ?",
            (language, recording_id),
        )


def get_language(recording_id: int) -> str | None:
    """Return the detected language for a recording, or None if not yet set."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT language FROM recordings WHERE id = ?", (recording_id,)
        ).fetchone()
    return row[0] if row else None


def save_transcript_x(recording_id: int, text: str) -> None:
    """Persist the WhisperX diarized transcript for a recording."""
    log.info("save_transcript_x  chars=%d  id=%d", len(text), recording_id)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE recordings SET transcript_x = ? WHERE id = ?",
            (text, recording_id),
        )


def get_transcript_x(recording_id: int) -> str | None:
    """Return the WhisperX diarized transcript, or None if not yet generated."""
    log.debug("get_transcript_x  id=%d", recording_id)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT transcript_x FROM recordings WHERE id = ?", (recording_id,)
        ).fetchone()
    return row[0] if row else None


def get_summary(recording_id: int) -> str | None:
    """Return the stored summary for a recording, or None if not yet generated."""
    log.debug("get_summary  id=%d", recording_id)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT summary FROM recordings WHERE id = ?", (recording_id,)
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
        cur = conn.execute(
            "INSERT OR IGNORE INTO recordings "
            "(filepath, source, start_time, end_time, duration, sample_rate, channels, file_size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (filepath, source, start_time.isoformat(), end_time.isoformat(),
             duration, rate, channels, file_size),
        )
        if cur.lastrowid:
            row_id = cur.lastrowid
        else:
            row_id = conn.execute(
                "SELECT id FROM recordings WHERE filepath = ?", (filepath,)
            ).fetchone()[0]
    return {
        "id": row_id,
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
        "has_transcript_x": False,
        "project": "",
        "meeting": "",
        "language": "",
        "file_missing": False,
        "auto_imported": False,
    }


def update_filepath(recording_id: int, new_path: str) -> None:
    """Update the filepath for an existing recording row (e.g. after the file is moved)."""
    log.info("update_filepath  id=%d -> %s", recording_id, os.path.basename(new_path))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE recordings SET filepath = ? WHERE id = ?",
            (new_path, recording_id),
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
            "SELECT id, filepath, source, start_time, end_time, duration, sample_rate, channels, file_size, "
            "(transcript IS NOT NULL) as has_transcript, project, meeting, "
            "(summary IS NOT NULL) as has_summary, "
            "(transcript_x IS NOT NULL) as has_transcript_x, "
            "language "
            "FROM recordings ORDER BY start_time DESC"
        ).fetchall()

    # Single folder walk — used for both reconnection and untracked-file detection
    folder_wavs: dict[str, str] = {}  # basename → full path
    for root, _, files in os.walk(folder):
        for f in files:
            if f.endswith(".wav"):
                folder_wavs[f] = os.path.join(root, f)

    log.debug("load_metadata  folder=%s  rows_in_db=%d", folder, len(rows))
    path_updates: list[tuple[str, int]] = []  # (new_fp, row_id)
    known: set[str] = set()
    recs = []
    missing_count = 0
    for row in rows:
        row_id, fp, source, st, et, dur, rate, channels, fsize, has_transcript, project, meeting, has_summary, has_transcript_x, language = row
        file_missing = False
        if not os.path.exists(fp):
            basename = os.path.basename(fp)
            candidate = folder_wavs.get(basename)
            if candidate and candidate not in known:
                log.info("reconnect  %s -> %s", basename, candidate)
                path_updates.append((candidate, row_id))
                fp = candidate
            else:
                file_missing = True
                missing_count += 1
        recs.append({
            "id": row_id,
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
            "has_transcript_x": bool(has_transcript_x),
            "project": project or "",
            "meeting": meeting or "",
            "language": language or "",
            "file_missing": file_missing,
            "auto_imported": False,
        })
        if not file_missing:
            known.add(fp)

    if path_updates:
        log.info("Reconnected %d recording path(s) to new folder", len(path_updates))
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "UPDATE recordings SET filepath = ? WHERE id = ?", path_updates
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


def delete_metadata(recording_id: int) -> None:
    """Remove the recording row for the given id."""
    log.info("delete_metadata  id=%d", recording_id)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
