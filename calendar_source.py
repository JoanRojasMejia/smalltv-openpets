"""Next-meeting reminders from your Google Calendar secret iCal address.

The address is read from the macOS Keychain (service "smalltv-openpets-calendar") and passed
to tools/calendar_events.py through the environment; it is never logged. Starting LEAD seconds
before a meeting, the screen shows one reminder per minute left ("IN 5 MIN" ... "STARTING NOW").
"""

from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import setup_device
from status_board import BOARD
from pet_sync import GIF_DIR, ROOT, look_args, uv_binary

KEYCHAIN_SERVICE = "smalltv-openpets-calendar"
LEAD = 300
REFRESH_SECONDS = 600
REMINDER_SECONDS = 65  # until the next per-minute reminder replaces it
STARTING_SECONDS = 90
MEETING_PATH = "/image/op-meeting.gif"


def reminder_text(event: dict, minutes: int) -> tuple[str, str]:
    title = "STARTING NOW" if minutes == 0 else f"IN {minutes} MIN"
    return title, f"{event['local']} {event['title']}".upper()


class MeetingReminder:
    """Decides when to show a reminder: once per minute left, from LEAD before the start."""

    def __init__(self, lead: float = LEAD):
        self.lead = lead
        self.events: list[dict] = []
        self.shown: set[tuple] = set()

    def poll(self, now: float) -> tuple[dict, int] | None:
        for event in sorted(self.events, key=lambda e: e["start"]):
            left = event["start"] - now
            if left <= -60:
                continue  # already running
            if left > self.lead:
                return None
            minutes = max(0, math.ceil(left / 60))
            key = (event["uid"], event["start"], minutes)
            if key in self.shown:
                return None
            self.shown.add(key)
            return event, minutes
        return None


def load_url() -> str | None:
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    url = out.stdout.strip()
    return url if out.returncode == 0 and url.startswith("https://") else None


HELPER = ROOT / "tools/bin/calendar-events"
CALENDARS_FILE = ROOT / "calendars.txt"  # one calendar name per line; missing or empty = all calendars


def followed_calendars(path: Path = CALENDARS_FILE) -> set[str]:
    try:
        return {line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")}
    except OSError:
        return set()


def filter_calendars(events: list[dict], names: set[str]) -> list[dict]:
    return [e for e in events if not names or e.get("calendar") in names]


def fetch_macos_events() -> list[dict]:
    """Meetings from the macOS Calendar app through the EventKit helper."""
    out = subprocess.run([str(HELPER)], capture_output=True, text=True, timeout=60)
    if out.returncode == 2:
        raise PermissionError("Calendar access not granted (System Settings > Privacy & Security > Calendars)")
    out.check_returncode()
    return filter_calendars(json.loads(out.stdout), followed_calendars())


def fetch_ics_events(url: str) -> list[dict]:
    """Meetings from a secret iCal address (alternative when EventKit is not available)."""
    env = {**os.environ, "SMALLTV_CALENDAR_URL": url}
    out = subprocess.run([uv_binary(), "run", "--quiet", "--with", "icalendar", "--with", "recurring-ical-events",
                          "python", str(ROOT / "tools/calendar_events.py")],
                         capture_output=True, text=True, timeout=120, env=env, cwd=ROOT, check=True)
    return json.loads(out.stdout)


class CalendarNotifier:
    """Background worker: refreshes the calendar and uploads reminder GIFs for the bridge."""

    def __init__(self, host: str, log, fetch):
        self.host = host
        self.log = log
        self.fetch = fetch
        self.reminder = MeetingReminder()
        self.ready: queue.Queue[tuple[str, float]] = queue.Queue()

    @classmethod
    def start(cls, host: str, log) -> CalendarNotifier | None:
        if HELPER.exists():
            fetch, source = fetch_macos_events, "macOS Calendar"
        else:
            url = load_url()
            if not url:
                log("calendar disabled: build tools/bin/calendar-events or store an iCal address in the Keychain")
                return None
            fetch, source = (lambda: fetch_ics_events(url)), "iCal address"
        notifier = cls(host, log, fetch)
        threading.Thread(target=notifier._run, name="calendar", daemon=True).start()
        log(f"calendar reminders enabled ({source})")
        return notifier

    def take(self) -> tuple[str, float] | None:
        try:
            return self.ready.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        next_refresh, error = 0.0, "start"
        while True:
            now = time.time()
            try:
                if now >= next_refresh:
                    next_refresh = now + REFRESH_SECONDS
                    self.reminder.events = self.fetch()
                    if error:
                        names = sorted({e.get("calendar", "?") for e in self.reminder.events})
                        self.log(f"calendar: {len(self.reminder.events)} meeting(s) in the next 24 h"
                                 + (f" from {', '.join(names)}" if names else ""))
                    error = None
                upcoming = [e for e in sorted(self.reminder.events, key=lambda e: e["start"]) if e["start"] > now]
                BOARD.set("next_meeting", upcoming[0] if upcoming else None)
                hit = self.reminder.poll(now)
                if hit:
                    event, minutes = hit
                    self.log(f"calendar: reminder, {minutes} min left")
                    self._show(*reminder_text(event, minutes), STARTING_SECONDS if minutes == 0 else REMINDER_SECONDS)
            except (Exception, SystemExit) as exc:  # keep the worker alive; retry at the next refresh
                message = str(exc) if isinstance(exc, PermissionError) else type(exc).__name__
                if message != error:
                    self.log(f"calendar error: {message}")
                error = message
                next_refresh = min(next_refresh, now + 60)
            time.sleep(5)

    def _show(self, title: str, body: str, seconds: float) -> None:
        with tempfile.TemporaryDirectory(prefix="smalltv-meet-") as tmp:
            subprocess.run([uv_binary(), "run", "--quiet", "--with", "pillow", "python", str(ROOT / "make_gifs.py"),
                            *look_args(), "--out", tmp, "--meeting-title", title, "--message-text", body],
                           check=True, cwd=ROOT, capture_output=True, timeout=120)
            shutil.copy2(Path(tmp) / "op-meeting.gif", GIF_DIR / "op-meeting.gif")
        setup_device.upload_verified(self.host, GIF_DIR / "op-meeting.gif")
        self.ready.put((MEETING_PATH, seconds))
