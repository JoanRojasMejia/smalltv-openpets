"""Print your upcoming timed meetings from an iCal feed as JSON (run via uv, needs icalendar).

The feed URL comes from the SMALLTV_CALENDAR_URL environment variable (never argv, so it does
not show up in the process list). Recurring events are expanded; all-day events and meetings
you declined are skipped.

Usage: SMALLTV_CALENDAR_URL=... uv run --with icalendar --with recurring-ical-events python tools/calendar_events.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

import icalendar
import recurring_ical_events


def _declined(event, me: str) -> bool:
    attendees = event.get("ATTENDEE") or []
    if not isinstance(attendees, list):
        attendees = [attendees]
    for attendee in attendees:
        address = str(attendee).lower().replace("mailto:", "")
        if me and address == me and str(attendee.params.get("PARTSTAT", "")).upper() == "DECLINED":
            return True
    return False


def upcoming(ics: bytes, now: datetime, horizon_hours: float = 24) -> list[dict]:
    cal = icalendar.Calendar.from_ical(ics)
    me = str(cal.get("X-WR-CALNAME", "")).strip().lower()
    events = []
    for event in recurring_ical_events.of(cal).between(now, now + timedelta(hours=horizon_hours)):
        start = event["DTSTART"].dt
        if isinstance(start, date) and not isinstance(start, datetime):
            continue  # all-day
        if start.tzinfo is None:
            start = start.astimezone()  # floating time: treat as local
        if start < now or _declined(event, me):
            continue
        end = event["DTEND"].dt if event.get("DTEND") else start
        if isinstance(end, datetime) and end.tzinfo is None:
            end = end.astimezone()
        events.append({
            "uid": str(event.get("UID", "")),
            "title": str(event.get("SUMMARY", "Meeting")),
            "start": start.timestamp(),
            "end": end.timestamp() if isinstance(end, datetime) else start.timestamp(),
            "local": start.astimezone().strftime("%H:%M"),
        })
    return sorted(events, key=lambda e: e["start"])


def main() -> None:
    url = os.environ.get("SMALLTV_CALENDAR_URL", "")
    if not url.startswith("https://"):
        sys.exit("SMALLTV_CALENDAR_URL must be an https iCal address")
    with urllib.request.urlopen(url, timeout=30) as resp:
        ics = resp.read()
    print(json.dumps(upcoming(ics, datetime.now(timezone.utc))))


if __name__ == "__main__":
    main()
