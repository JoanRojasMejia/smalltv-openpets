"""Idle summary card: story-point budget of your Jira task in progress, plus the facts other
workers leave on the status board (next meeting, last deploy, last Slack message).

1 story point = HOURS_PER_POINT working hours. The task is the newest one assigned to you
in status `jira_status` (config, default "En Progreso"); work time is counted from when it
entered the in-progress category, only within your working schedule (config `work_schedule`,
default Mon-Fri 09:00-18:00 with lunch 13:00-14:00).

Jira access: an Atlassian API token in the Keychain (service "smalltv-openpets-jira", account =
your Atlassian email) and `jira_site` in the config (e.g. "yourcompany.atlassian.net").
"""

from __future__ import annotations

import base64
import json
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import setup_device
from status_board import BOARD, summary_rows
from pet_sync import GIF_DIR, ROOT, look_args, uv_binary

KEYCHAIN_SERVICE = "smalltv-openpets-jira"
HOURS_PER_POINT = 4
REFRESH_SECONDS = 300
SUMMARY_PATH = "/image/op-summary.gif"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Schedule:
    days: tuple = ("mon", "tue", "wed", "thu", "fri")
    start: str = "09:00"
    end: str = "18:00"
    lunch: str = "13:00-14:00"

    @classmethod
    def from_config(cls, value: dict | None) -> Schedule:
        value = value or {}
        return cls(tuple(value.get("days") or cls.days), value.get("start", cls.start),
                   value.get("end", cls.end), value.get("lunch", cls.lunch))

    def blocks(self) -> list[tuple[int, int]]:
        """Working minute ranges within a day."""
        start, end = _minutes(self.start), _minutes(self.end)
        if not self.lunch:
            return [(start, end)]
        lunch_start, lunch_end = (_minutes(part) for part in self.lunch.split("-"))
        return [(start, lunch_start), (lunch_end, end)]


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.strip().split(":")
    return int(hours) * 60 + int(minutes)


def worked_hours(start: datetime, end: datetime, schedule: Schedule, tz=None) -> float:
    """Working hours between two instants, counted in the schedule's local time zone."""
    tz = tz or datetime.now().astimezone().tzinfo
    start, end = start.astimezone(tz), end.astimezone(tz)
    if end <= start:
        return 0
    total = 0.0
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        if WEEKDAYS[day.weekday()] in schedule.days:
            for block_start, block_end in schedule.blocks():
                lo = max(start, day + timedelta(minutes=block_start))
                hi = min(end, day + timedelta(minutes=block_end))
                if hi > lo:
                    total += (hi - lo).total_seconds() / 3600
        day = (day + timedelta(days=1, hours=2)).replace(hour=0)  # safe across DST changes
    return round(total, 2)


def progress(points, worked: float) -> tuple[int, float] | None:
    """(percent of the budget used, hours left; negative when over budget)."""
    if not points:
        return None
    budget = points * HOURS_PER_POINT
    return round(worked / budget * 100), round(budget - worked, 1)


def _hours(value: float) -> str:
    return f"{value:g}" if value != int(value) else str(int(value))


def clean_summary(summary: str) -> str:
    """'FE - [web-app] Rename ...' -> 'Rename ...' (team prefix and [tags] dropped)."""
    summary = re.sub(r"^\s*[A-Z]{2,5}\s+-\s+", "", summary or "")
    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", "", summary)).strip()


def summary_text(task: dict, worked: float) -> tuple[str, int | None, str, str]:
    """(title, percent or None, remaining line, task summary) for the card."""
    title = f"{task['key']} - {task['points']:g} SP" if task.get("points") else task["key"]
    summary = clean_summary(task.get("summary", ""))
    result = progress(task.get("points"), worked)
    if result is None:
        return title, None, "NO STORY POINTS", summary
    percent, left = result
    line = f"{_hours(left)} H LEFT" if left >= 0 else f"{_hours(-left)} H OVER"
    return title, percent, line, summary


def parse_issue(raw: dict, points_field: str) -> dict:
    fields = raw.get("fields") or {}
    started = fields.get("statuscategorychangedate")
    try:
        started = datetime.strptime(started, "%Y-%m-%dT%H:%M:%S.%f%z") if started else None
    except ValueError:
        started = None
    points = fields.get(points_field)
    return {"key": raw.get("key", "?"), "summary": fields.get("summary") or "",
            "points": points if isinstance(points, (int, float)) else None, "started": started}


def load_credentials() -> tuple[str, str] | None:
    """(email, API token) from the Keychain; the email is the item's account name."""
    try:
        meta = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
                              capture_output=True, text=True, timeout=10)
        token = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                               capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    account = re.search(r'"acct"<blob>="([^"]+)"', meta.stdout)
    if meta.returncode or token.returncode or not account or not token.stdout.strip():
        return None
    return account.group(1), token.stdout.strip()


def jira_search(site: str, email: str, token: str, jql: str, fields: str, limit: int = 50) -> list[dict]:
    query = urllib.parse.urlencode({"jql": jql, "fields": fields, "maxResults": limit})
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(f"https://{site}/rest/api/3/search/jql?{query}",
                                 headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp).get("issues") or []


def fetch_current_task(site: str, email: str, token: str, status: str, points_field: str) -> dict | None:
    jql = f'assignee = currentUser() AND status = "{status}" ORDER BY updated DESC'
    issues = jira_search(site, email, token, jql, f"summary,{points_field},statuscategorychangedate", limit=5)
    return parse_issue(issues[0], points_field) if issues else None


def open_tickets(site: str, email: str, token: str, keys: list[str]) -> list[dict]:
    """Which of these tickets are assigned to you and not Done: [{"key", "summary"}]."""
    keys = [k for k in keys if re.fullmatch(r"[A-Z][A-Z0-9]{1,9}-\d+", k)]
    if not keys:
        return []
    jql = f"key in ({','.join(keys)}) AND assignee = currentUser() AND statusCategory != Done"
    try:
        issues = jira_search(site, email, token, jql, "summary")
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        if len(keys) == 1:
            return []  # the key does not exist
        return [t for k in keys for t in open_tickets(site, email, token, [k])]  # find the bad key(s)
    return [{"key": i["key"], "summary": (i.get("fields") or {}).get("summary", "")} for i in issues]


class SummaryNotifier:
    """Keeps op-summary.gif (story-point progress of the task in progress) up to date."""

    def __init__(self, host: str, log, site: str, credentials: tuple[str, str] | None, settings: dict):
        self.host = host
        self.log = log
        self.site = site
        self.credentials = credentials
        self.status = settings.get("jira_status") or "En Progreso"
        self.points_field = settings.get("jira_points_field") or "customfield_10105"
        self.schedule = Schedule.from_config(settings.get("work_schedule"))
        self.lock = threading.Lock()
        self.ready_path: str | None = None
        self.fresh = False

    @classmethod
    def start(cls, host: str, log) -> SummaryNotifier | None:
        settings = config.load()
        site = settings.get("jira_site")
        if not site:
            log("summary disabled: set jira_site in the config")
            return None
        credentials = load_credentials()
        notifier = cls(host, log, site, credentials, settings)
        threading.Thread(target=notifier._run, name="summary", daemon=True).start()
        log("idle summary enabled (Jira story points)" if credentials else
            "summary waiting: no Jira API token in the Keychain yet (service smalltv-openpets-jira)")
        return notifier

    def take_fresh(self) -> bool:
        """True once after a new summary GIF was uploaded."""
        with self.lock:
            fresh, self.fresh = self.fresh, False
            return fresh

    def ready(self) -> str | None:
        with self.lock:
            return self.ready_path

    def _run(self) -> None:
        shown_key, error = None, None
        while True:
            if not self.credentials:
                self.credentials = load_credentials()  # picked up as soon as it is stored, no restart needed
                if not self.credentials:
                    time.sleep(60)
                    continue
                self.log("idle summary enabled (Jira story points)")
            try:
                task = fetch_current_task(self.site, *self.credentials, self.status, self.points_field)
                card = None
                if task:
                    started = task["started"] or datetime.now(timezone.utc)
                    card = summary_text(task, worked_hours(started, datetime.now(timezone.utc), self.schedule))
                rows = summary_rows(BOARD.snapshot(), time.time())
                if not card and not rows:
                    with self.lock:
                        self.ready_path = None
                    shown_key = None
                elif (card, rows) != shown_key:
                    self._render(card, rows)
                    shown_key = (card, rows)
                    self.log(f"summary: {task['key'] + ' ' + str(card[1]) + '%' if card else 'no task'}, {len(rows)} row(s)")
                error = None
            except (Exception, SystemExit) as exc:  # keep the worker alive
                if type(exc).__name__ != error:
                    self.log(f"summary error: {type(exc).__name__}")
                error = type(exc).__name__
            time.sleep(REFRESH_SECONDS)

    def _render(self, card: tuple | None, rows: list[tuple[str, str]]) -> None:
        title, percent, line, summary = card or ("", None, "", "")
        with tempfile.TemporaryDirectory(prefix="smalltv-sum-") as tmp:
            subprocess.run([uv_binary(), "run", "--quiet", "--with", "pillow", "python", str(ROOT / "make_gifs.py"),
                            *look_args(), "--out", tmp, "--summary-title", title,
                            "--summary-percent", "" if percent is None else str(percent),
                            "--summary-line", line, "--message-text", summary,
                            "--summary-rows", json.dumps(rows)],
                           check=True, cwd=ROOT, capture_output=True, timeout=120)
            shutil.copy2(Path(tmp) / "op-summary.gif", GIF_DIR / "op-summary.gif")
        setup_device.upload_verified(self.host, GIF_DIR / "op-summary.gif")
        with self.lock:
            self.ready_path, self.fresh = SUMMARY_PATH, True
