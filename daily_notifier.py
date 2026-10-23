"""Background worker for the stand-up digest.

- Generates each workday's digest at 17:50 (or the next morning if the Mac was off).
- Shows the previous workday's digest as three full-screen pages (AYER / HOY / BLOQUEOS):
  once when the workday starts (first check after 09:00) and 10 and 3 minutes before any
  meeting whose title contains `daily_meeting_match` (config, default "daily").
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path

import config
import daily_digest as dd
import setup_device
from pet_sync import GIF_DIR, ROOT, uv_binary
from status_board import BOARD

PAGE_SECONDS = 20
CHECK_SECONDS = 30
MORNING_HOUR = 9
WEEKDAYS_ES = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]


def digest_pages(digest: dict, day: date) -> list[tuple[str, list[str]]]:
    """(title, entries) for the three pages of a digest."""
    return [
        (f"AYER ({WEEKDAYS_ES[day.weekday()]} {day.day})", digest.get("ayer") or ["Sin registros"]),
        ("HOY", digest.get("hoy") or ["Sin pendientes registrados"]),
        ("BLOQUEOS", digest.get("bloqueos") or ["Ninguno"]),
    ]


class DailyNotifier:
    def __init__(self, host: str, log, settings: dict):
        self.host = host
        self.log = log
        self.settings = settings
        self.match = (settings.get("daily_meeting_match") or "daily").lower()
        self.shown: set[tuple] = set()
        self.ready: queue.Queue[tuple[str, float]] = queue.Queue()

    @classmethod
    def start(cls, host: str, log) -> DailyNotifier:
        notifier = cls(host, log, config.load())
        threading.Thread(target=notifier._run, name="daily", daemon=True).start()
        log("daily digest enabled")
        return notifier

    def take(self) -> tuple[str, float] | None:
        try:
            return self.ready.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        error = None
        while True:
            try:
                self._tick(datetime.now().astimezone())
                error = None
            except (Exception, SystemExit) as exc:  # keep the worker alive
                if type(exc).__name__ != error:
                    self.log(f"daily error: {type(exc).__name__}")
                error = type(exc).__name__
            time.sleep(CHECK_SECONDS)

    def _tick(self, now: datetime) -> None:
        have = {date.fromisoformat(p.stem) for p in dd.DAILY_DIR.glob("*.json")} if dd.DAILY_DIR.exists() else set()
        due = dd.digest_due(now, have)
        if due:
            record = dd.generate(due, self.settings)
            self.log(f"daily: digest for {due} ready ({record['source']})")
        today = now.date()
        if today.weekday() < 5 and now.hour >= MORNING_HOUR and ("morning", today) not in self.shown:
            self.shown.add(("morning", today))
            self._show_digest(today, "morning")
        for meeting in (BOARD.snapshot().get("meetings") or {}).get("items", []):
            if self.match not in meeting.get("title", "").lower():
                continue
            slot = dd.daily_slot(now.timestamp(), meeting["start"])
            key = (meeting.get("uid"), meeting["start"], slot)
            if slot and key not in self.shown:
                self.shown.add(key)
                self._show_digest(today, f"{slot} min before {meeting['title']}")

    def _show_digest(self, today: date, reason: str) -> None:
        day = dd.previous_workday(today)
        digest = dd.load_digest(day) or dd.generate(day, self.settings)
        self.log(f"daily: showing {day} digest ({reason})")
        for number, (title, entries) in enumerate(digest_pages(digest, day), start=1):
            filename = f"op-daily-{number}.gif"
            with tempfile.TemporaryDirectory(prefix="smalltv-daily-") as tmp:
                subprocess.run([uv_binary(), "run", "--quiet", "--with", "pillow", "python",
                                str(ROOT / "make_gifs.py"), "--out", tmp, "--card", "daily", "--page",
                                "--card-title", title, "--card-lines", "|".join(e.replace("|", "/") for e in entries)],
                               check=True, cwd=ROOT, capture_output=True, timeout=120)
                shutil.copy2(Path(tmp) / "op-card.gif", GIF_DIR / filename)
            setup_device.upload_verified(self.host, GIF_DIR / filename)
            self.ready.put((f"/image/{filename}", PAGE_SECONDS))
