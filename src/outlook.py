"""Read Outlook calendar events and emails via COM."""
import datetime
import logging
import threading

log = logging.getLogger(__name__)

# One COM session per thread — CoInitialize + Dispatch + GetNamespace run once,
# so Outlook's Object Model Guard fires at most once per thread lifetime.
_local = threading.local()


def _get_session():
    import pythoncom
    import win32com.client

    if not getattr(_local, "initialized", False):
        pythoncom.CoInitialize()
        _local.initialized = True

    if getattr(_local, "outlook_app", None) is None:
        _local.outlook_app = win32com.client.Dispatch("Outlook.Application")
        _local.ns = _local.outlook_app.GetNamespace("MAPI")

    return _local.outlook_app, _local.ns


def _reset_session():
    """Clear the cached session so the next call reconnects (call after a com_error)."""
    _local.outlook_app = None
    _local.ns = None


def get_events(days_ahead: int = 1) -> list[dict]:
    from pywintypes import com_error
    try:
        return _get_events(days_ahead)
    except com_error:
        _reset_session()
        raise


def _get_events(days_ahead: int) -> list[dict]:
    outlook, ns = _get_session()
    calendar = ns.GetDefaultFolder(9)   # olFolderCalendar

    now   = datetime.datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + datetime.timedelta(days=days_ahead)

    # Outlook COM Restrict date format must match the system locale's short date order.
    # On dd/MM/yyyy locales (e.g. Spanish) "9/3/2026" means March 9, not September 3.
    import ctypes
    buf = ctypes.create_unicode_buffer(80)
    ctypes.windll.kernel32.GetLocaleInfoW(0, 0x1F, buf, 80)  # LOCALE_SSHORTDATE
    _day_first = buf.value.lower().index('d') < buf.value.lower().index('m')

    def _fmt(dt: datetime.datetime) -> str:
        if _day_first:
            return f"{dt.day}/{dt.month}/{dt.year} {dt.strftime('%I:%M %p')}"
        return f"{dt.month}/{dt.day}/{dt.year} {dt.strftime('%I:%M %p')}"

    items = calendar.Items
    items.IncludeRecurrences = True
    items.Sort("[Start]")
    restriction = f"[Start] >= '{_fmt(start)}' AND [Start] < '{_fmt(end)}'"
    items = items.Restrict(restriction)

    events = []
    for item in items:
        try:
            item_start = item.Start
            item_end   = item.End
            item_start_dt = datetime.datetime(
                item_start.year, item_start.month, item_start.day,
                item_start.hour, item_start.minute, item_start.second,
            )
            if item_start_dt < start or item_start_dt >= end:
                continue
            start_str = item_start_dt.strftime("%Y-%m-%dT%H:%M:%S")
            end_str   = str(item_end)[:19].replace(" ", "T")
            body      = (item.Body or "")[:500]
            events.append({
                "subject":   item.Subject or "(No title)",
                "start":     start_str,
                "end":       end_str,
                "location":  getattr(item, "Location", "") or "",
                "organizer": getattr(item, "Organizer", "") or "",
                "attendees": getattr(item, "RequiredAttendees", "") or "",
                "body":      body,
            })
        except Exception as exc:
            log.warning("Skipping calendar item: %s", exc)
            continue

    return events


def get_emails(count: int = 20, unread_only: bool = False) -> list[dict]:
    from pywintypes import com_error
    try:
        return _get_emails(count, unread_only)
    except com_error:
        _reset_session()
        raise


def _get_emails(count: int, unread_only: bool) -> list[dict]:
    outlook, ns = _get_session()
    inbox = ns.GetDefaultFolder(6)   # olFolderInbox

    items = inbox.Items
    items.Sort("[ReceivedTime]", True)  # descending

    if unread_only:
        items = items.Restrict("[UnRead] = True")

    emails = []
    for item in items:
        if len(emails) >= count:
            break
        try:
            received = item.ReceivedTime
            received_str = f"{received.year}-{received.month:02d}-{received.day:02d}T{received.hour:02d}:{received.minute:02d}:{received.second:02d}"
            emails.append({
                "subject":       item.Subject or "(No subject)",
                "sender":        getattr(item, "SenderName", "") or "",
                "sender_email":  getattr(item, "SenderEmailAddress", "") or "",
                "received":      received_str,
                "unread":        bool(item.UnRead),
                "body_preview":  (item.Body or "")[:300].strip(),
            })
        except Exception as exc:
            log.warning("Skipping email item: %s", exc)
            continue

    return emails


def is_outlook_available() -> bool:
    try:
        get_events(days_ahead=0)
        return True
    except Exception:
        return False
