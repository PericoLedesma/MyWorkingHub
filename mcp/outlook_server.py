#!/usr/bin/env python3
"""
MCP server — Outlook Email Service.

Calls src.outlook directly via COM (pywin32). Does NOT require the app to be running,
but does require desktop Outlook installed and a COM-accessible MAPI profile.

Tools:
    get_emails        — recent inbox emails
    get_unread_emails — unread inbox emails

Register in claude_desktop_config.json:
    { "mcpServers": { "hub-outlook": { "command": "...", "args": ["mcp/outlook_server.py"] } } }
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("My Working Hub — Outlook")


@mcp.tool()
def get_emails(count: int = 20) -> str:
    """Get the most recent emails from the Outlook inbox. Returns up to `count` emails (default 20), newest first."""
    if count <= 0:
        return json.dumps({"ok": False, "error": "count must be a positive integer."})
    try:
        from src.outlook import get_emails as _get_emails
        emails = _get_emails(count=count, unread_only=False)
    except ImportError:
        return json.dumps({"ok": False, "error": "pywin32 not installed — required for Outlook COM access."})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

    return json.dumps({"ok": True, "emails": emails, "count": len(emails)}, indent=2)


@mcp.tool()
def get_unread_emails(count: int = 20) -> str:
    """Get unread emails from the Outlook inbox. Returns up to `count` unread emails (default 20), newest first."""
    if count <= 0:
        return json.dumps({"ok": False, "error": "count must be a positive integer."})
    try:
        from src.outlook import get_emails as _get_emails
        emails = _get_emails(count=count, unread_only=True)
    except ImportError:
        return json.dumps({"ok": False, "error": "pywin32 not installed — required for Outlook COM access."})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

    if not emails:
        return json.dumps({"ok": True, "emails": [], "message": "No unread emails."})

    return json.dumps({"ok": True, "emails": emails, "count": len(emails)}, indent=2)


if __name__ == "__main__":
    mcp.run()
