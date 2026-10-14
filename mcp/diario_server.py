#!/usr/bin/env python3
"""
MCP server — Diario Service.

Reads and writes today's agenda directly from smart_recorder.db; the app does
not need to be running.

Tools:
    get_diario      — full agenda for a date (default: today)
    add_item        — add a todo item for today (optionally under a section)
    complete_item   — toggle an item's done state
    pin_item        — pin/unpin an item in Today's Focus panel
    set_item_notes  — set per-item notes text
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

# Hidden section holding items that don't belong to any project (matches app.js D_LOOSE_ID)
_LOOSE_ID = "s_loose"


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


def _carry_over(prev: dict, date: str) -> dict:
    """Seed a fresh day from the previous day's unfinished items.

    Kept in sync with carry_over_diario() in src/db.py.
    """
    sections = []
    for sec in prev.get("sections", []):
        items = [{**i, "done": False} for i in sec.get("items", []) if not i.get("done")]
        if items:
            sections.append({**sec, "items": items})
    state = {"date": date, "sections": sections}
    alive = {(s["id"], i["id"]) for s in sections for i in s["items"]}
    order = [e for e in prev.get("flatOrder", [])
             if (e.get("secId"), e.get("itemId")) in alive]
    if order:
        state["flatOrder"] = order
    if prev.get("flatMode"):
        state["flatMode"] = True
    return state


def _load(date: str) -> dict:
    """Load a day's agenda, inheriting the last day's unfinished items if it has no row."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT state FROM diario WHERE date = ?", (date,)).fetchone()
        if row:
            return json.loads(row[0])
        prev = conn.execute(
            "SELECT state FROM diario WHERE date < ? ORDER BY date DESC LIMIT 1", (date,)
        ).fetchone()
    if prev:
        return _carry_over(json.loads(prev[0]), date)
    return {"date": date, "sections": []}


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
def add_item(text: str, section_id: str = "", priority: bool = False, pinned: bool = False, notes: str = "") -> str:
    """Add a todo item to today's agenda, optionally under a project section.

    Args:
        text: The text of the new item.
        section_id: The section's id field (e.g. 's_naturgy'). Empty = item without a project
            (stored in the hidden 's_loose' section).
        priority: Whether to mark the item as a priority (star).
        pinned: Whether to pin the item to Today's Focus panel.
        notes: Optional per-item note text.
    """
    d = _today()
    state = _load(d)
    if not section_id or section_id == _LOOSE_ID:
        section_id = _LOOSE_ID
        if not any(s["id"] == _LOOSE_ID for s in state["sections"]):
            state["sections"].insert(0, {"id": _LOOSE_ID, "emoji": "", "name": "", "items": []})
    sec = next((s for s in state["sections"] if s["id"] == section_id), None)
    if sec is None:
        return (f"Section '{section_id}' not found. Available: "
                f"{[s['id'] for s in state['sections']]}. Use add_section to create one.")
    item = {"id": _new_id(), "text": text, "done": False, "priority": priority, "pinned": pinned}
    if notes:
        item["notes"] = notes
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
def pin_item(section_id: str, item_id: str, pinned: bool = True) -> str:
    """Pin or unpin an item in Today's Focus panel.

    Args:
        section_id: The section's id field.
        item_id: The item's id field.
        pinned: True to pin to Today's Focus, False to remove from focus.
    """
    d = _today()
    state = _load(d)
    sec = next((s for s in state["sections"] if s["id"] == section_id), None)
    if sec is None:
        return f"Section '{section_id}' not found."
    item = next((i for i in sec["items"] if i["id"] == item_id), None)
    if item is None:
        return f"Item '{item_id}' not found in section '{section_id}'."
    item["pinned"] = pinned
    _save(d, state)
    return json.dumps({"ok": True, "item": item})


@mcp.tool()
def set_item_notes(section_id: str, item_id: str, notes: str) -> str:
    """Set the per-item notes text on a diario item.

    Args:
        section_id: The section's id field.
        item_id: The item's id field.
        notes: The note text to save (empty string to clear).
    """
    d = _today()
    state = _load(d)
    sec = next((s for s in state["sections"] if s["id"] == section_id), None)
    if sec is None:
        return f"Section '{section_id}' not found."
    item = next((i for i in sec["items"] if i["id"] == item_id), None)
    if item is None:
        return f"Item '{item_id}' not found in section '{section_id}'."
    item["notes"] = notes
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
