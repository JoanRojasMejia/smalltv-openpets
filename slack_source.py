"""Poll Slack for new direct messages and mentions with your user token.

The token is read from the macOS Keychain (service "smalltv-openpets-slack") and is never
logged. Slack does not push events to a plain user token, so this searches every
POLL_SECONDS for `to:me` (DMs) and `<@you>` (mentions) and reports messages it has not seen.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import unescape
from pathlib import Path

KEYCHAIN_SERVICE = "smalltv-openpets-slack"
STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "smalltv-openpets/slack.json"
POLL_SECONDS = 30
BACKOFF_SECONDS = 120
SEEN_LIMIT = 500


# Calcifer, an agentic PR reviewer bot, answers in review threads without mentioning the PR
# author; the thread's parent post mentions the author. Its user and channel ids come from the
# local config (calcifer_user, calcifer_channel); without them this feature is off.


def calcifer_query(user: str, channel: str) -> str:
    return f"from:<@{user}> in:<#{channel}>"

VERDICTS = [  # most specific first
    (re.compile(r"\*\s*aprobado con sugerencias\s*\*", re.I), "approved_suggestions"),
    (re.compile(r"\*\s*aprobado\s*\*", re.I), "approved"),
    (re.compile(r"\*\s*rechazado\s*\*", re.I), "rejected"),
    (re.compile(r"\*\s*revisi[oó]n humana\s*\*", re.I), "human_review"),
]


@dataclass
class Message:
    sender: str
    text: str
    ts: float
    channel: str
    kind: str = "message"  # "message" or "calcifer"
    verdict: str | None = None


def parse_calcifer(text: str) -> tuple[str | None, str]:
    """Verdict key (or None for a plain comment) and the reason to show."""
    raw = unescape(text or "")
    verdict = next((key for pattern, key in VERDICTS if pattern.search(raw)), None)
    motivo = re.search(r"\*?motivo:?\*?:?\s*(.+)", raw, re.I | re.S)
    reason = motivo.group(1) if motivo else re.sub(r"^\s*<@[^>]+>\s*", "", raw)
    reason = re.sub(r"[*_~`]|^>\s*", "", reason, flags=re.M)
    return verdict, re.sub(r"\s+", " ", reason).strip()


def load_token() -> str | None:
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    token = out.stdout.strip()
    return token if out.returncode == 0 and token.startswith("xox") else None


def make_api(token: str):
    def api(method: str, **params) -> dict:
        req = urllib.request.Request(f"https://slack.com/api/{method}?" + urllib.parse.urlencode(params),
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    return api


def clean_text(text: str, resolve_name) -> str:
    """Slack mrkdwn -> plain text: mentions resolved, links shortened, emoji codes removed."""
    def link(m: re.Match) -> str:
        target, _, label = m.group(1).partition("|")
        if target.startswith("@"):
            return "@" + (resolve_name(target[1:]) or label or "someone")
        if target.startswith("#"):
            return "#" + (label or "channel")
        if target.startswith("!"):
            return label or "@" + target[1:].split("^")[0]
        if label:
            return label
        if target.startswith("mailto:"):
            return target[7:]
        host = urllib.parse.urlparse(target).netloc
        return host[4:] if host.startswith("www.") else host or target

    text = re.sub(r"<([^<>]+)>", link, text or "")
    text = re.sub(r":[a-z0-9_+'-]+:", "", text)
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    return text or "[file]"


class SlackPoller:
    def __init__(self, api, state_file: Path = STATE_FILE, calcifer: tuple[str, str] | None = None):
        self.api = api
        self.calcifer_user, self.calcifer_channel = calcifer or (None, None)
        self.state_file = state_file
        self.me: str | None = None
        self.names: dict[str, str | None] = {}
        self.thread_owner_is_me: dict[str, bool] = {}
        self.backoff_until = 0.0
        self.last_error: str | None = None
        self.last_counts: dict[str, int] = {}
        self.baseline: float | None = None
        self.seen: list[str] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.state_file.read_text())
            self.baseline = float(data["baseline"])
            self.seen = [str(s) for s in data.get("seen", [])][-SEEN_LIMIT:]
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"baseline": self.baseline, "seen": self.seen[-SEEN_LIMIT:]}))
        os.replace(tmp, self.state_file)

    def _name(self, user_id: str) -> str | None:
        if user_id not in self.names:
            info = self.api("users.info", user=user_id)
            user = info.get("user") or {}
            profile = user.get("profile") or {}
            self.names[user_id] = (profile.get("display_name") or profile.get("real_name") or user.get("name")) \
                if info.get("ok") else None
        return self.names[user_id]

    def _is_my_pr_thread(self, channel: str, m: dict) -> bool:
        """Whether a Calcifer reply belongs to a review thread whose parent post mentions me."""
        thread = re.search(r"thread_ts=([\d.]+)", m.get("permalink", ""))
        if not thread:
            return f"<@{self.me}" in m.get("text", "")
        thread_ts = thread.group(1)
        if thread_ts not in self.thread_owner_is_me:
            r = self.api("conversations.replies", channel=channel, ts=thread_ts, limit=1)
            parent = (r.get("messages") or [{}])[0] if r.get("ok") else {}
            self.thread_owner_is_me[thread_ts] = f"<@{self.me}" in parent.get("text", "")
        return self.thread_owner_is_me[thread_ts]

    def _search(self, query: str) -> list[dict]:
        r = self.api("search.messages", query=query, sort="timestamp", sort_dir="desc", count=20)
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "search failed")
        return r.get("messages", {}).get("matches", [])

    def poll(self, now: float) -> list[Message]:
        """New messages since the baseline, oldest first. now is wall-clock time."""
        if now < self.backoff_until:
            return []
        try:
            if self.me is None:
                auth = self.api("auth.test")
                if not auth.get("ok"):
                    raise RuntimeError(auth.get("error") or "auth failed")
                self.me = auth["user_id"]
            dms, mentions = self._search("to:me"), self._search(f"<@{self.me}>")
            reviews = self._search(calcifer_query(self.calcifer_user, self.calcifer_channel)) \
                if self.calcifer_user and self.calcifer_channel else []
            self.last_counts = {"dms": len(dms), "mentions": len(mentions), "calcifer": len(reviews)}
            matches = [(m, "message") for m in dms + mentions] + [(m, "calcifer") for m in reviews]
            if self.baseline is None:
                self.baseline = now
                self._save()
                return []
            fresh, keys = [], set(self.seen)
            for m, kind in sorted(matches, key=lambda pair: float(pair[0]["ts"])):
                channel = (m.get("channel") or {}).get("id", "")
                key = f"{channel}:{m['ts']}"
                if key in keys or float(m["ts"]) <= self.baseline or m.get("user") == self.me:
                    continue
                keys.add(key)
                self.seen.append(key)
                if kind == "calcifer":
                    if not self._is_my_pr_thread(channel, m):
                        continue
                    verdict, reason = parse_calcifer(m.get("text", ""))
                    fresh.append(Message("Calcifer", clean_text(reason, self._name), float(m["ts"]), channel,
                                         kind="calcifer", verdict=verdict))
                    continue
                if self.calcifer_user and channel == self.calcifer_channel and f"<@{self.calcifer_user}" in m.get("text", ""):
                    continue  # "review @Calcifer @you" trigger posts; the verdict comes later
                sender = self._name(m.get("user", "")) or m.get("username") or "Slack"
                fresh.append(Message(sender, clean_text(m.get("text", ""), self._name), float(m["ts"]), channel))
            if fresh:
                self._save()
            self.last_error = None
            return fresh
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:120]
            self.backoff_until = now + BACKOFF_SECONDS
            return []
