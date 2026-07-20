"""
Read Microsoft Teams data via Graph API.

Uses the Microsoft Graph PowerShell public client ID — no Azure App Registration
needed, works with any Microsoft 365 / Entra ID account.

Auth flow (same pattern as src/outlook.py — zero config):
  1. Reads cached token from DATA_DIR/teams_token_cache.json (silent on restart)
  2. If no cache: WAM broker pops a native Windows account picker (one click)
  3. After first sign-in: completely silent forever

No env vars required.
"""

import os
import re
from pathlib import Path

# Microsoft Graph PowerShell — public client ID published by Microsoft for scripting.
# No app registration needed; works with any Microsoft 365 tenant.
_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
_AUTHORITY = "https://login.microsoftonline.com/organizations"
_GRAPH = "https://graph.microsoft.com/v1.0"
_SCOPES = [
    "https://graph.microsoft.com/Chat.Read",
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Team.ReadBasic.All",
    "https://graph.microsoft.com/Channel.ReadBasic.All",
    "https://graph.microsoft.com/Presence.Read",
]

_DATA_DIR = Path(os.environ.get(
    "SMART_RECORDER_DATA_DIR",
    Path.home() / "OneDrive - Accenture" / "Desktop" / "MyDB",
))
_CACHE_PATH = _DATA_DIR / "teams_token_cache.json"


def _get_token() -> str:
    import msal

    cache = msal.SerializableTokenCache()
    if _CACHE_PATH.exists():
        cache.deserialize(_CACHE_PATH.read_text(encoding="utf-8"))

    app = msal.PublicClientApplication(_CLIENT_ID, authority=_AUTHORITY, token_cache=cache)

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(_SCOPES, account=accounts[0])

    if not result:
        # WAM broker: native Windows account picker — no browser, no device code.
        # On AAD-joined machines this is often fully silent.
        hwnd = getattr(msal.PublicClientApplication, "CONSOLE_WINDOW_HANDLE", 0)
        result = app.acquire_token_interactive(_SCOPES, parent_window_handle=hwnd)

    if cache.has_state_changed:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")

    if "access_token" not in result:
        raise RuntimeError(f"Teams auth failed: {result.get('error_description', result)}")

    return result["access_token"]


def _get(path: str, params: dict | None = None) -> dict:
    import requests
    r = requests.get(f"{_GRAPH}{path}", headers={"Authorization": f"Bearer {_get_token()}"}, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict) -> dict:
    import requests
    r = requests.post(f"{_GRAPH}{path}", headers={"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json"}, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_me() -> dict:
    d = _get("/me")
    return {"id": d["id"], "name": d.get("displayName", ""), "email": d.get("mail") or d.get("userPrincipalName", "")}


def list_teams() -> list[dict]:
    return [{"id": t["id"], "name": t["displayName"], "description": t.get("description") or ""} for t in _get("/me/joinedTeams").get("value", [])]


def list_channels(team_id: str) -> list[dict]:
    return [{"id": c["id"], "name": c["displayName"], "description": c.get("description") or ""} for c in _get(f"/teams/{team_id}/channels").get("value", [])]


def list_chats(limit: int = 20) -> list[dict]:
    data = _get("/me/chats", {"$top": limit, "$expand": "members", "$orderby": "lastMessagePreview/createdDateTime desc"})
    chats = []
    for c in data.get("value", []):
        members = [m.get("displayName", "") for m in c.get("members", []) if m.get("displayName")]
        preview = (c.get("lastMessagePreview") or {}).get("body", {})
        chats.append({
            "id": c["id"],
            "type": c.get("chatType", ""),
            "topic": c.get("topic") or ", ".join(members[:3]) or "(no topic)",
            "last_updated": c.get("lastUpdatedDateTime", ""),
            "last_message_preview": _strip_html(preview.get("content", ""))[:120],
        })
    return chats


def get_chat_messages(chat_id: str, limit: int = 20) -> list[dict]:
    msgs = []
    for m in _get(f"/me/chats/{chat_id}/messages", {"$top": limit}).get("value", []):
        user = ((m.get("from") or {}).get("user") or {}).get("displayName", "Unknown")
        msgs.append({
            "id": m["id"],
            "sender": user,
            "created": m.get("createdDateTime", ""),
            "body": _strip_html((m.get("body") or {}).get("content", "")),
            "importance": m.get("importance", "normal"),
        })
    return msgs


def send_chat_message(chat_id: str, text: str) -> dict:
    r = _post(f"/me/chats/{chat_id}/messages", {"body": {"content": text, "contentType": "text"}})
    return {"id": r.get("id"), "created": r.get("createdDateTime")}


def get_my_presence() -> dict:
    d = _get("/me/presence")
    return {"availability": d.get("availability", ""), "activity": d.get("activity", "")}
