"""Small shared board where the notifiers leave facts for the idle summary card.

The calendar worker posts the next meeting, the pipeline worker your last finished deploy and
the Slack worker the last message you received; summary_rows() turns them into card rows.
"""

from __future__ import annotations

import threading
from datetime import datetime

import pixel_art as px


class Board:
    def __init__(self):
        self._lock = threading.Lock()
        self._facts: dict[str, dict] = {}

    def set(self, key: str, value: dict | None) -> None:
        with self._lock:
            if value is None:
                self._facts.pop(key, None)
            else:
                self._facts[key] = dict(value)

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._facts.items()}


BOARD = Board()


def ago(ts: float, now: float) -> str:
    """Coarse "how long ago" so the card text (and its upload) changes rarely."""
    seconds = max(0, now - ts)
    if seconds < 300:
        return "NOW"
    if seconds < 3600:
        return f"{int(seconds // 300) * 5}M"
    if seconds < 86400:
        return f"{int(seconds // 3600)}H"
    return f"{int(seconds // 86400)}D"


def summary_rows(facts: dict[str, dict], now: float, tz=None) -> list[tuple[str, str]]:
    """[(kind, text)] rows: next meeting, today's agents, last deploy, last Slack message (when known).

    The Slack row goes last: it shares its line with the pet, and names are cut anyway.
    """
    rows = []
    meeting = facts.get("next_meeting")
    if meeting and meeting["start"] > now:
        start = datetime.fromtimestamp(meeting["start"], tz)
        prefix = "" if start.date() == datetime.fromtimestamp(now, tz).date() else start.strftime("%a ").upper()
        rows.append(("meeting", px.normalize_text(f"{prefix}{meeting['local']} {meeting['title']}")))
    agents = facts.get("agents_today")
    if agents and agents.get("sessions"):
        n, asks = agents["sessions"], agents.get("asks", 0)
        rows.append(("agents", f"{n} AGENT{'S' if n != 1 else ''} {asks} ASK{'S' if asks != 1 else ''}"))
    deploy = facts.get("last_deploy")
    if deploy:
        kind = "deploy_ok" if deploy["status"] == "Succeeded" else "deploy_failed"
        rows.append((kind, px.normalize_text(f"{ago(deploy['ts'], now)} {deploy['env']}")))
    message = facts.get("last_message")
    if message:
        rows.append(("slack", px.normalize_text(f"{ago(message['ts'], now)} {message['sender']}")))
    return rows
