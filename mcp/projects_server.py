#!/usr/bin/env python3
"""
MCP server for My Working Hub — Projects integration.

Registered in claude_desktop_config.json:
    { "mcpServers": { "smart-recorder-projects": { "command": "python", "args": ["mcp/projects_server.py"] } } }

Exposes tools:
    list_projects              — all projects with recording counts
    get_project                — project metadata + its recordings
    create_project             — create a new project
    update_project             — update project fields
    assign_recording_to_project — link a recording to a project
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src import db

from mcp.server.fastmcp import FastMCP

_DEFAULT_DATA_DIR = r"C:\Users\p.ledesma.jimenez\OneDrive - Accenture\Desktop\MyDB"
DATA_DIR = os.environ.get("SMART_RECORDER_DATA_DIR", _DEFAULT_DATA_DIR)
DB_PATH = os.path.join(DATA_DIR, "smart_recorder.db")

mcp = FastMCP("My Working Hub — Projects")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def list_projects() -> str:
    """List all projects with recording counts, total duration, and last recording date."""
    projects = db.list_projects()
    if not projects:
        return "No projects found."
    for p in projects:
        if p.get("total_duration"):
            p["total_duration_min"] = round(p["total_duration"] / 60, 1)
        del p["total_duration"]
    return json.dumps(projects, indent=2)


@mcp.tool()
def get_project(name: str) -> str:
    """Get project metadata and its recordings. Uses a case-insensitive partial name match."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, name, description, status, client, objective, phases, streams, color, created_at "
            "FROM projects WHERE name LIKE ? COLLATE NOCASE",
            (f"%{name}%",),
        ).fetchone()

    if not row:
        return f"No project found matching {name!r}."

    project = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "status": row["status"] or "",
        "client": row["client"] or "",
        "objective": row["objective"] or "",
        "phases": json.loads(row["phases"] or "[]"),
        "streams": json.loads(row["streams"] or "[]"),
        "color": row["color"] or "",
        "created_at": row["created_at"],
    }

    with _conn() as conn:
        recs = conn.execute(
            "SELECT id, start_time, duration, meeting, "
            "(transcript IS NOT NULL) as has_transcript "
            "FROM recordings WHERE project = ? ORDER BY start_time DESC",
            (row["name"],),
        ).fetchall()

    project["recordings"] = [
        {
            "id": r["id"],
            "start_time": r["start_time"],
            "duration_min": round(r["duration"] / 60, 1),
            "meeting": r["meeting"] or "",
            "has_transcript": bool(r["has_transcript"]),
        }
        for r in recs
    ]
    return json.dumps(project, indent=2)


@mcp.tool()
def create_project(
    name: str,
    description: str = "",
    status: str = "Active",
    client: str = "",
    objective: str = "",
    color: str = "",
) -> str:
    """Create a new project. Name must be unique. phases and streams default to empty lists."""
    try:
        result = db.create_project(
            name=name,
            description=description,
            status=status,
            client=client,
            objective=objective,
            color=color,
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def update_project(
    project_id: int,
    name: str = "",
    description: str = "",
    status: str = "",
    client: str = "",
    objective: str = "",
    color: str = "",
) -> str:
    """Update one or more fields of an existing project. Pass only the fields you want to change; empty string = skip."""
    fields = {
        "name": name,
        "description": description,
        "status": status,
        "client": client,
        "objective": objective,
        "color": color,
    }
    db.update_project(project_id, **{k: v for k, v in fields.items() if v})
    return json.dumps({"ok": True})


@mcp.tool()
def assign_recording_to_project(recording_id: int, project_name: str) -> str:
    """Assign a recording to a project by name. Pass project_name='' to unassign."""
    db.update_tag(recording_id, "project", project_name)
    return json.dumps({"ok": True})


if __name__ == "__main__":
    mcp.run()
