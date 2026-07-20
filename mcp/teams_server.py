#!/usr/bin/env python3
"""
MCP server — Teams Service (Microsoft Graph via Windows WAM broker).

Auth is handled by src.teams using the Windows Web Account Manager — no device-code
URL, no browser. First call shows a native Windows account picker; subsequent calls
are silent (WAM caches the token at OS level).

Required env vars (set in claude_desktop_config.json):
    AZURE_CLIENT_ID   — App registration client ID
    AZURE_TENANT_ID   — Directory (tenant) ID

Tools:
    teams_whoami        — Confirm auth; returns name + email
    teams_list_chats    — List recent chats with last-message preview
    teams_get_messages  — Read messages from a specific chat
    teams_list_teams    — List joined Teams
    teams_list_channels — List channels in a Team
    teams_get_presence  — My current availability / activity status
    teams_send_message  — Send a message to a chat

Register in claude_desktop_config.json:
    { "mcpServers": { "hub-teams": { "command": "...", "args": ["mcp/teams_server.py"],
      "env": { "AZURE_CLIENT_ID": "...", "AZURE_TENANT_ID": "..." } } } }
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("My Working Hub — Teams")


@mcp.tool()
def teams_whoami() -> str:
    """Confirm Microsoft Graph authentication works. Returns the signed-in user's display name and email."""
    try:
        from src.teams import get_me
        return json.dumps({"ok": True, **get_me()}, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def teams_list_chats(limit: int = 20) -> str:
    """List recent Teams chats with topic, type, and last message preview. limit controls how many to return (max 50)."""
    try:
        from src.teams import list_chats
        return json.dumps({"ok": True, "chats": list_chats(limit=limit)}, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def teams_get_messages(chat_id: str, limit: int = 20) -> str:
    """Get messages from a Teams chat by chat ID. Use teams_list_chats first to find the ID."""
    try:
        from src.teams import get_chat_messages
        return json.dumps({"ok": True, "messages": get_chat_messages(chat_id, limit=limit)}, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def teams_list_teams() -> str:
    """List all Microsoft Teams that the signed-in user has joined."""
    try:
        from src.teams import list_teams
        return json.dumps({"ok": True, "teams": list_teams()}, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def teams_list_channels(team_id: str) -> str:
    """List channels in a specific Team. Use teams_list_teams first to find the team ID."""
    try:
        from src.teams import list_channels
        return json.dumps({"ok": True, "channels": list_channels(team_id)}, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def teams_get_presence() -> str:
    """Get the signed-in user's current Teams availability and activity status."""
    try:
        from src.teams import get_my_presence
        return json.dumps({"ok": True, **get_my_presence()}, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def teams_send_message(chat_id: str, text: str) -> str:
    """Send a plain-text message to a Teams chat. Use teams_list_chats to find the chat ID."""
    try:
        from src.teams import send_chat_message
        result = send_chat_message(chat_id, text)
        return json.dumps({"ok": True, **result}, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


if __name__ == "__main__":
    mcp.run()
