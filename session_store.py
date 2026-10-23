"""Per-session agent state shared by the agent hooks (writers) and the bridge (reader).

Each agent session owns one JSON file: <STATE_DIR>/<agent>-<session>.json
  {"agent": "claude", "session": "...", "state": "working", "ts": 1792701060.1, "cwd": "..."}
The OpenCode plugin (hooks/opencode-plugin.js) writes the same format.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "smalltv-openpets/sessions"

# Highest first: what the screen shows when several sessions disagree.
PRIORITY = ["waiting", "error", "testing", "working", "thinking", "done", "idle"]

# Seconds after the last event before a session state is considered stale.
STALE_AFTER = {
    "thinking": 600,
    "working": 600,
    "testing": 600,
    "done": 60,
    "error": 300,
    "waiting": 7200,
    "idle": 900,
}


def _session_file(agent: str, session: str, state_dir: Path) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{agent}-{session}")[:120]
    return state_dir / f"{safe}.json"


def write_state(agent: str, session: str, state: str, cwd: str = "", state_dir: Path = STATE_DIR,
                now: float | None = None, detail: str = "") -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    target = _session_file(agent, session, state_dir)
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    payload = {"agent": agent, "session": session, "state": state, "ts": time.time() if now is None else now,
               "cwd": cwd, "detail": detail}
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, target)


def remove_session(agent: str, session: str, state_dir: Path = STATE_DIR) -> None:
    _session_file(agent, session, state_dir).unlink(missing_ok=True)


def read_sessions(state_dir: Path = STATE_DIR) -> list[dict]:
    sessions = []
    for file in state_dir.glob("*.json"):
        try:
            data = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("state") in STALE_AFTER and isinstance(data.get("ts"), (int, float)):
            sessions.append(data)
    return sessions


def aggregate(sessions: list[dict], now: float) -> str:
    """The highest-priority non-stale state across sessions, or idle."""
    live = [s["state"] for s in sessions if now - s["ts"] < STALE_AFTER[s["state"]]]
    return min(live, key=PRIORITY.index) if live else "idle"


def waiting_summary(sessions: list[dict], now: float) -> list[str]:
    """One line per live waiting session: "<project> - <why>", oldest first."""
    waiting = [s for s in sessions if s["state"] == "waiting" and now - s["ts"] < STALE_AFTER["waiting"]]
    lines = []
    for s in sorted(waiting, key=lambda s: s["ts"]):
        project = Path(str(s.get("cwd") or "")).name or str(s.get("agent") or "agent")
        lines.append(f"{project} - {s.get('detail') or 'needs you'}")
    return lines


def prune(state_dir: Path = STATE_DIR, now: float | None = None, max_age: float = 86400) -> None:
    """Delete session files untouched for max_age (sessions that never sent an end event)."""
    now = time.time() if now is None else now
    for file in state_dir.glob("*.json"):
        try:
            if now - file.stat().st_mtime > max_age:
                file.unlink()
        except OSError:
            continue
