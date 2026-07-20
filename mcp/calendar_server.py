#!/usr/bin/env python3
"""
MCP server — Calendar Service (Outlook).

Calls src.outlook directly via COM (pywin32). Does NOT require the app to be running,
but does require desktop Outlook installed and a COM-accessible MAPI profile.

Tools:
    get_calendar_events — upcoming Outlook meetings

Register in claude_desktop_config.json:
    { "mcpServers": { "hub-calendar": { "command": "...", "args": ["mcp/calendar_server.py"] } } }
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("My Working Hub — Calendar")


@mcp.tool()
def get_calendar_events(days: int = 1) -> str:
    """Get upcoming Outlook calendar events. days=1 returns today only; days=3 returns the next 3 days."""
    if days <= 0:
        return json.dumps({"ok": False, "error": "days must be a positive integer."})
    try:
        from src.outlook import get_events
        events = get_events(days_ahead=days)
    except ImportError:
        return json.dumps({"ok": False, "error": "pywin32 not installed — required for Outlook COM access."})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

    if not events:
        return json.dumps({"ok": True, "events": [], "message": f"No events in the next {days} day(s)."})

    return json.dumps({"ok": True, "events": events}, indent=2)


if __name__ == "__main__":
    mcp.run()
