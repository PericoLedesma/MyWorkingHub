#!/usr/bin/env python3
"""
MCP server — Diario Service.

Reads and writes today's agenda directly from smart_recorder.db; the app does
not need to be running.

Tools:
    get_diario      — full agenda for a date (default: today)
    add_item        — add a todo item to a section for today
    complete_item   — toggle an item's done state
    delete_item     — remove an item from today
    add_section     — add a new section to today's agenda
    delete_section  — remove a section (and all its items) from today

Register in claude_desktop_config.json:
    { "mcpServers": { "hub-diario": { "command": "...", "args": ["mcp/diario_server.py"] } } }
"""

import datetime
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

_DEFAULT_DATA_DIR = r"C:\Users\p.ledesma.jimenez\OneDrive - Accenture\Desktop\MyDB"
DATA_DIR = os.environ.get("SMART_RECORDER_DATA_DIR", _DEFAULT_DATA_DIR)
DB_PATH = os.path.join(DATA_DIR, "smart_recorder.db")

mcp = FastMCP("My Working Hub — Diario")

_DEFAULT_SECTIONS = [
    {"id": "s_naturgy",  "emoji": "⚡", "name": "Naturgy",            "items": []},
    {"id": "s_engie",    "emoji": "🔥", "name": "ENGIE",              "items": []},
    {"id": "s_vega",     "emoji": "🌌", "name": "Vega",               "items": []},
    {"id": "s_salvador", "emoji": "🏠", "name": "Salvador Madariaga", "items": []},
    {"id": "s_personal", "emoji": "🙋", "name": "Personal",           "items": []},
]


def _today() -> str:
    return datetime.date.today().isoformat()


def _new_id() -> str:
    return secrets.token_hex(4)[:7]


def _reject_future(date: str) -> str | None:
    """Return an error string if date is in the future, else None."""
    try:
        if datetime.date.fromisoformat(date) > datetime.date.today():
            return f"Date '{date}' is in the future. The agenda only supports today and past days."
    except ValueError:
        return f"Invalid date format '{date}'. Use YYYY-MM-DD."
    return None


def _load(date: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT state FROM diario WHERE date = ?", (date,)).fetchone()
    if row:
        return json.loads(row[0])
    return {"date": date, "sections": [dict(s) for s in _DEFAULT_SECTIONS]}


def _save(date: str, state: dict) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO diario (date, state) VALUES (?, ?)",
            (date, json.dumps(state)),
        )


@mcp.tool()
def get_diario(date: str = "") -> str:
    """Get the full daily agenda for a date (YYYY-MM-DD). Defaults to today."""
    d = date or _today()
    if err := _reject_future(d):
        return json.dumps({"error": err})
    state = _load(d)
    return json.dumps(state, ensure_ascii=False, indent=2)


@mcp.tool()
def add_item(section_id: str, text: str, priority: bool = False) -> str:
    """Add a todo item to a section in today's agenda.

    Args:
        section_id: The section's id field (e.g. 's_naturgy').
        text: The text of the new item.
        priority: Whether to mark the item as a priority.
    """
    d = _today()
    state = _load(d)
    sec = next((s for s in state["sections"] if s["id"] == section_id), None)
    if sec is None:
        return f"Section '{section_id}' not found. Available: {[s['id'] for s in state['sections']]}"
    item = {"id": _new_id(), "text": text, "done": False, "priority": priority}
    sec["items"].append(item)
    _save(d, state)
    return json.dumps({"ok": True, "item": item})


@mcp.tool()
def complete_item(section_id: str, item_id: str, done: bool = True) -> str:
    """Toggle the done state of an item in today's agenda.

    Args:
        section_id: The section's id field.
        item_id: The item's id field.
        done: True to mark done, False to unmark.
    """
    d = _today()
    state = _load(d)
    sec = next((s for s in state["sections"] if s["id"] == section_id), None)
    if sec is None:
        return f"Section '{section_id}' not found."
    item = next((i for i in sec["items"] if i["id"] == item_id), None)
    if item is None:
        return f"Item '{item_id}' not found in section '{section_id}'."
    item["done"] = done
    if done:
        item["priority"] = False
    _save(d, state)
    return json.dumps({"ok": True, "item": item})


@mcp.tool()
def delete_item(section_id: str, item_id: str) -> str:
    """Remove an item from today's agenda.

    Args:
        section_id: The section's id field.
        item_id: The item's id field to remove.
    """
    d = _today()
    state = _load(d)
    sec = next((s for s in state["sections"] if s["id"] == section_id), None)
    if sec is None:
        return f"Section '{section_id}' not found."
    before = len(sec["items"])
    sec["items"] = [i for i in sec["items"] if i["id"] != item_id]
    if len(sec["items"]) == before:
        return f"Item '{item_id}' not found in section '{section_id}'."
    _save(d, state)
    return json.dumps({"ok": True})


@mcp.tool()
def add_section(emoji: str, name: str) -> str:
    """Add a new section to today's agenda.

    Args:
        emoji: A single emoji character for the section.
        name: The display name of the section.
    """
    d = _today()
    state = _load(d)
    sec = {"id": "s_" + _new_id(), "emoji": emoji, "name": name, "items": []}
    state["sections"].append(sec)
    _save(d, state)
    return json.dumps({"ok": True, "section": sec})


@mcp.tool()
def delete_section(section_id: str) -> str:
    """Remove a section (and all its items) from today's agenda.

    Args:
        section_id: The section's id field.
    """
    d = _today()
    state = _load(d)
    before = len(state["sections"])
    state["sections"] = [s for s in state["sections"] if s["id"] != section_id]
    if len(state["sections"]) == before:
        return f"Section '{section_id}' not found."
    _save(d, state)
    return json.dumps({"ok": True})


if __name__ == "__main__":
    mcp.run()
