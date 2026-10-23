"""Today's agent activity for the idle summary: sessions seen and times an agent asked for you.

The bridge feeds every poll of the session files to AgentDay.observe(). A session counts once
per day; an "ask" is a session entering the waiting state (permission or question). Counters
reset at local midnight and persist across bridge restarts.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

STATS_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "smalltv-openpets/today.json"


class AgentDay:
    def __init__(self, path: Path = STATS_FILE, tz=None):
        self.path = path
        self.tz = tz
        self.day = ""
        self.sessions: set[str] = set()
        self.asks = 0
        self.last_states: dict[str, str] = {}
        try:
            data = json.loads(path.read_text())
            self.day, self.sessions = data["day"], set(data["sessions"])
            self.asks, self.last_states = int(data["asks"]), dict(data["last_states"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def observe(self, sessions: list[dict], now: float) -> None:
        today = datetime.fromtimestamp(now, self.tz).date().isoformat()
        changed = False
        if today != self.day:
            self.day, self.sessions, self.asks, changed = today, set(), 0, True
        states = {}
        for s in sessions:
            key = f"{s.get('agent')}:{s.get('session')}"
            states[key] = s.get("state", "")
            if datetime.fromtimestamp(s.get("ts", now), self.tz).date().isoformat() != today:
                continue  # leftover file from an earlier day: not today's activity
            if key not in self.sessions:
                self.sessions.add(key)
                changed = True
            if states[key] == "waiting" and self.last_states.get(key) != "waiting":
                self.asks += 1
                changed = True
        if states != self.last_states:
            self.last_states = states
            changed = True
        if changed:
            self._save()

    def snapshot(self) -> dict:
        return {"sessions": len(self.sessions), "asks": self.asks}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"day": self.day, "sessions": sorted(self.sessions), "asks": self.asks,
                                       "last_states": self.last_states}))
            os.replace(tmp, self.path)
        except OSError:
            pass
