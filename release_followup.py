"""Close the loop between production deploys and Jira.

When a production pipeline deploys a commit that mentions tickets ("[ABC-41] ..."), the ones
assigned to you that are not Done yet become reminders: "IN PROD: MOVE TO DONE?". They stay
pending (and in the idle summary) until Jira shows them done.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from pipeline_source import env_label

STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "smalltv-openpets/jira_pending.json"
TICKET = re.compile(r"\[([A-Z][A-Z0-9]{1,9}-\d+)\]")


def ticket_keys(text: str) -> list[str]:
    """Bracketed Jira keys in a commit message, in order, without duplicates."""
    keys = []
    for key in TICKET.findall(text or ""):
        if key not in keys:
            keys.append(key)
    return keys


def is_prod(pipeline: str) -> bool:
    name = pipeline.upper()
    return "PROD" in name and "TEST" not in name


class ReleaseFollowup:
    def __init__(self, lookup, state_file: Path = STATE_FILE):
        """lookup(keys) -> [{"key", "summary"}] of those keys assigned to you and not Done."""
        self.lookup = lookup
        self.state_file = state_file
        try:
            self._pending = json.loads(state_file.read_text())
        except (OSError, ValueError):
            self._pending = []

    def pending(self) -> list[dict]:
        return list(self._pending)

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._pending))
        os.replace(tmp, self.state_file)

    def on_deploys(self, executions: list[dict]) -> list[dict]:
        """New reminders for successful production deploys."""
        known = {p["key"] for p in self._pending}
        new = []
        for e in executions:
            if e.get("status") != "Succeeded" or not is_prod(e["pipeline"]):
                continue
            keys = [k for k in ticket_keys(e.get("summary", "")) if k not in known]
            if not keys:
                continue
            for ticket in self.lookup(keys):
                item = {"key": ticket["key"], "summary": ticket["summary"], "env": env_label(e["pipeline"])}
                new.append(item)
                known.add(ticket["key"])
        if new:
            self._pending += new
            self._save()
        return new

    def refresh(self) -> None:
        """Drop reminders whose ticket is Done now (keeps them if Jira cannot be reached)."""
        if not self._pending:
            return
        try:
            still_open = {t["key"] for t in self.lookup([p["key"] for p in self._pending])}
        except Exception:
            return
        kept = [p for p in self._pending if p["key"] in still_open]
        if kept != self._pending:
            self._pending = kept
            self._save()
