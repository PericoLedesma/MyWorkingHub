#!/usr/bin/env python3
"""
MCP server — Focus Time Service.

Reads directly from focus_time.db; the app does not need to be running.

Tools:
    get_focus_stats   — daily totals (hours + session count) for the last 30 days
    get_work_sessions — individual session rows with start/end times

Register in claude_desktop_config.json:
    { "mcpServers": { "hub-focus": { "command": "...", "args": ["mcp/focus_server.py"] } } }
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

_DEFAULT_DATA_DIR = r"C:\Users\p.ledesma.jimenez\OneDrive - Accenture\Desktop\MyDB"
DATA_DIR = os.environ.get("SMART_RECORDER_DATA_DIR", _DEFAULT_DATA_DIR)
FOCUS_DB_PATH = os.path.join(DATA_DIR, "focus_time.db")

mcp = FastMCP("My Working Hub — Focus Time")


def _conn():
    conn = sqlite3.connect(FOCUS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def get_focus_stats(date_from: str = "", date_to: str = "") -> str:
    """Get daily focus/work session statistics. Optionally filter by date range (YYYY-MM-DD)."""
    query = (
        "SELECT date, SUM(duration_seconds) as total_seconds, COUNT(*) as session_count "
        "FROM work_sessions WHERE end_time IS NOT NULL"
    )
    params: list = []
    if date_from:
        query += " AND date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND date <= ?"
        params.append(date_to)
    query += " GROUP BY date ORDER BY date DESC LIMIT 30"

    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return "No focus time data found for the given date range."

    days = [
        {
            "date": r["date"],
            "hours": round(r["total_seconds"] / 3600, 2),
            "sessions": r["session_count"],
        }
        for r in rows
    ]
    total_hours = sum(d["hours"] for d in days)
    return json.dumps({"days": days, "total_hours": round(total_hours, 2)}, indent=2)


@mcp.tool()
def get_work_sessions(date: str = "") -> str:
    """Get individual work session rows with start time, end time, and duration. Optionally filter by date (YYYY-MM-DD)."""
    query = (
        "SELECT id, date, start_time, end_time, duration_seconds "
        "FROM work_sessions WHERE end_time IS NOT NULL"
    )
    params: list = []
    if date:
        query += " AND date = ?"
        params.append(date)
    query += " ORDER BY start_time DESC LIMIT 100"

    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return "No work sessions found" + (f" for {date}." if date else ".")

    return json.dumps([
        {
            "id": r["id"],
            "date": r["date"],
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "duration_min": round(r["duration_seconds"] / 60, 1),
        }
        for r in rows
    ], indent=2)


if __name__ == "__main__":
    mcp.run()
