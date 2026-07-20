#!/usr/bin/env python3
"""
MCP server — OneNote Service.

Calls src.onenote directly via a PowerShell COM bridge. Does NOT require the app
to be running, but does require desktop OneNote installed.

Tools:
    get_onenote_hierarchy    — list notebooks, sections, section groups, and pages
    get_page_content         — read the text content of a page
    create_page              — create a new page (in the first available section)
    create_page_in_section   — create a new page in a specific section
    create_section           — create a new section inside a notebook
    create_recording_page    — push a recording's notes to OneNote
    rename_page              — rename a page
    rename_section           — rename a section
    rename_notebook          — rename a notebook (display name)
    move_page                — move a page to a different section

Register in claude_desktop_config.json:
    { "mcpServers": { "hub-onenote": { "command": "...", "args": ["mcp/onenote_server.py"] } } }
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
DB_PATH = os.path.join(DATA_DIR, "smart_recorder.db")

mcp = FastMCP("My Working Hub — OneNote")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _onenote():
    from src import onenote
    return onenote


# ---------------------------------------------------------------------------
# Structure — read
# ---------------------------------------------------------------------------

@mcp.tool()
def get_onenote_hierarchy() -> str:
    """
    List all OneNote notebooks, sections, section groups, and pages.

    Returns a nested JSON structure:
      notebooks[]: { type, name, id, children[] }
        children can be:
          { type: 'section', name, id, color, pages[]: { type, name, id, modified } }
          { type: 'section_group', name, id, children[] }

    Use the returned IDs with other tools (get_page_content, rename_*, move_page, etc.).
    """
    try:
        result = _onenote().get_hierarchy()
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps(result, indent=2)


@mcp.tool()
def get_page_content(page_id: str) -> str:
    """
    Read the text content of a OneNote page.

    Args:
        page_id: The page ID from get_onenote_hierarchy.

    Returns { ok, page_id, title, content } where content is all text on the page
    joined by newlines.
    """
    try:
        result = _onenote().get_page_content(page_id)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Structure — modify
# ---------------------------------------------------------------------------

@mcp.tool()
def rename_page(page_id: str, new_name: str) -> str:
    """
    Rename a OneNote page.

    Args:
        page_id:  The page ID from get_onenote_hierarchy.
        new_name: The new title for the page.
    """
    try:
        result = _onenote().rename_page(page_id, new_name)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps(result, indent=2)


@mcp.tool()
def rename_section(section_id: str, new_name: str) -> str:
    """
    Rename a OneNote section.

    Args:
        section_id: The section ID from get_onenote_hierarchy.
        new_name:   The new name for the section.
    """
    try:
        result = _onenote().rename_section(section_id, new_name)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps(result, indent=2)


@mcp.tool()
def rename_notebook(notebook_id: str, new_name: str) -> str:
    """
    Rename a OneNote notebook (updates the display name only; does not rename the folder on disk).

    Args:
        notebook_id: The notebook ID from get_onenote_hierarchy.
        new_name:    The new display name.
    """
    try:
        result = _onenote().rename_notebook(notebook_id, new_name)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps(result, indent=2)


@mcp.tool()
def move_page(page_id: str, target_section_id: str) -> str:
    """
    Move a page to a different section.

    Args:
        page_id:           The page ID from get_onenote_hierarchy.
        target_section_id: The destination section ID from get_onenote_hierarchy.
    """
    try:
        result = _onenote().move_page(page_id, target_section_id)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps(result, indent=2)


@mcp.tool()
def create_section(notebook_id: str, section_name: str) -> str:
    """
    Create a new section inside a notebook.

    Requires a local or OneDrive-synced notebook (must have a file system path).
    Cloud-only notebooks without a local path will return an error.

    Args:
        notebook_id:  The notebook ID from get_onenote_hierarchy.
        section_name: Name for the new section (without .one extension).
    """
    try:
        result = _onenote().create_section(notebook_id, section_name)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Content — create
# ---------------------------------------------------------------------------

@mcp.tool()
def create_page(title: str, body: str) -> str:
    """
    Create a new OneNote page in the first available section.

    Args:
        title: The page title.
        body:  The page body text.
    """
    try:
        result = _onenote().create_page(title, body)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps(result, indent=2)


@mcp.tool()
def create_page_in_section(section_id: str, title: str, body: str) -> str:
    """
    Create a new OneNote page in a specific section.

    Args:
        section_id: The section ID from get_onenote_hierarchy.
        title:      The page title.
        body:       The page body text.
    """
    try:
        result = _onenote().create_page(title, body, section_id=section_id)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps(result, indent=2)


@mcp.tool()
def create_recording_page(recording_id: int) -> str:
    """Push a recording's notes to a new OneNote page. The page title is the meeting name + date."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, start_time, project, meeting, notes "
            "FROM recordings WHERE id = ?",
            (recording_id,),
        ).fetchone()

    if not row:
        return json.dumps({"ok": False, "error": f"No recording found with id {recording_id}."})

    notes = row["notes"] or ""
    if not notes:
        return json.dumps({"ok": False, "error": "Recording has no notes to export."})

    date_str = (row["start_time"] or "")[:10]
    meeting = row["meeting"] or row["project"] or f"Recording {recording_id}"
    title = f"{meeting} — {date_str}" if date_str else meeting
    body = f"NOTES\n{'='*40}\n{notes}"

    try:
        result = _onenote().create_page(title, body)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run()
