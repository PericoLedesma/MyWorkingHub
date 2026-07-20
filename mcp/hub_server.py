#!/usr/bin/env python3
"""
MCP server — Hub Meta Service.

Exposes information about My Working Hub itself: README, dev context,
available MCP services, and project structure. No app needs to be running.

Tools:
    get_readme          — return the full README.md content
    update_readme       — overwrite README.md with new content
    get_dev_context     — return CLAUDE.md (dev context / architecture notes)
    list_mcp_services   — list every registered MCP server and its tools

Register in claude_desktop_config.json:
    { "mcpServers": { "hub-meta": { "command": "...", "args": ["mcp/hub_server.py"] } } }
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).parent.parent
README_PATH = REPO_ROOT / "README.md"
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"

mcp = FastMCP("My Working Hub — Meta")


@mcp.tool()
def get_readme() -> str:
    """Return the full contents of README.md."""
    if not README_PATH.exists():
        return "README.md not found."
    return README_PATH.read_text(encoding="utf-8")


@mcp.tool()
def update_readme(content: str) -> str:
    """
    Overwrite README.md with new content.

    Args:
        content: The complete new markdown text for README.md.

    Returns a confirmation message with the new file size.
    """
    README_PATH.write_text(content, encoding="utf-8")
    size_kb = round(README_PATH.stat().st_size / 1024, 1)
    return f"README.md updated successfully ({size_kb} KB, {len(content.splitlines())} lines)."


@mcp.tool()
def get_dev_context() -> str:
    """Return the full contents of CLAUDE.md — architecture notes and dev context for this project."""
    if not CLAUDE_MD_PATH.exists():
        return "CLAUDE.md not found."
    return CLAUDE_MD_PATH.read_text(encoding="utf-8")


@mcp.tool()
def list_mcp_services() -> str:
    """
    List all MCP servers in this project with their registered tools and a short description.

    Returns a JSON array — one entry per server file found under mcp/.
    """
    mcp_dir = REPO_ROOT / "mcp"
    servers = []

    # Static catalogue — kept in sync with CLAUDE.md
    catalogue = {
        "recordings_server.py": {
            "server_name": "hub-recordings",
            "description": "Browse, search, transcribe, annotate recordings, and control live recording (reads SQLite + calls app API).",
            "tools": [
                "list_recordings — browse with date/project/meeting filters",
                "get_recording — full detail: transcript, summary, notes",
                "search_recordings — keyword search across transcripts and summaries",
                "transcribe_recording — run Whisper-1 on a WAV file",
                "write_notes — persist notes to the database",
                "launch_app — start the SmartRecorder Electron app",
                "start_recording — start a live recording (mic/app/both); requires app running",
                "get_current_recording — get live recording state and elapsed time; requires app running",
            ],
        },
        "projects_server.py": {
            "server_name": "hub-projects",
            "description": "Manage projects that organise recordings.",
            "tools": [
                "list_projects — all projects with recording counts and total duration",
                "get_project — project metadata and its recordings",
                "create_project — create a new project",
                "update_project — update project fields by ID",
                "assign_recording_to_project — link or unlink a recording",
            ],
        },
        "focus_server.py": {
            "server_name": "hub-focus",
            "description": "Focus Time statistics and individual session rows (reads focus_time.db).",
            "tools": [
                "get_focus_stats — daily totals (hours + session count) for last 30 days",
                "get_work_sessions — individual sessions with start/end times, filterable by date",
            ],
        },
        "calendar_server.py": {
            "server_name": "hub-calendar",
            "description": "Read upcoming meetings from Outlook via COM (no app needed).",
            "tools": [
                "get_calendar_events — upcoming Outlook meetings; days param controls look-ahead",
            ],
        },
        "onenote_server.py": {
            "server_name": "hub-onenote",
            "description": "Read and manage OneNote notebooks, sections, and pages via COM (no app needed).",
            "tools": [
                "get_onenote_hierarchy — notebooks → sections/groups → pages (typed JSON with IDs)",
                "get_page_content — read all text content from a page by ID",
                "create_page — create a page in the first available section",
                "create_page_in_section — create a page in a specific section (by section ID)",
                "create_section — create a new section inside a notebook",
                "create_recording_page — push a recording's notes to OneNote",
                "rename_page — rename a page by ID",
                "rename_section — rename a section by ID",
                "rename_notebook — rename a notebook display name by ID",
                "move_page — move a page to a different section",
            ],
        },
        "teams_server.py": {
            "server_name": "hub-teams",
            "description": "Microsoft Teams via Graph API — MSAL device-code auth, token cached locally.",
            "tools": [
                "teams_whoami — confirm Graph auth; returns display name + email",
                "teams_list_chats — list 5 most recent Teams chats",
            ],
        },
        "hub_server.py": {
            "server_name": "hub-meta",
            "description": "Hub meta-information: README, dev context, service catalogue.",
            "tools": [
                "get_readme — return the full README.md content",
                "update_readme — overwrite README.md with new content",
                "get_dev_context — return CLAUDE.md (architecture and dev notes)",
                "list_mcp_services — this tool — list all MCP servers and their tools",
            ],
        },
    }

    for filename, info in catalogue.items():
        entry = {"file": f"mcp/{filename}", **info}
        entry["file_exists"] = (mcp_dir / filename).exists()
        servers.append(entry)

    return json.dumps(servers, indent=2)


if __name__ == "__main__":
    mcp.run()
