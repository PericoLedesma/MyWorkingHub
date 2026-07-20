"""Read Outlook calendar events via COM."""
import datetime
import logging

log = logging.getLogger(__name__)


def get_events(days_ahead: int = 1) -> list[dict]:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        outlook  = win32com.client.Dispatch("Outlook.Application")
        ns       = outlook.GetNamespace("MAPI")
        calendar = ns.GetDefaultFolder(9)   # olFolderCalendar

        now   = datetime.datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end   = start + datetime.timedelta(days=days_ahead)

        items = calendar.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")
        restriction = (
            f"[Start] >= '{start.strftime('%m/%d/%Y')}' "
            f"AND [Start] < '{end.strftime('%m/%d/%Y')}'"
        )
        items = items.Restrict(restriction)

        events = []
        for item in items:
            try:
                start_str = str(item.Start)[:19].replace(" ", "T")
                end_str   = str(item.End)[:19].replace(" ", "T")
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

    finally:
        pythoncom.CoUninitialize()


def is_outlook_available() -> bool:
    try:
        get_events(days_ahead=0)
        return True
    except Exception:
        return False
