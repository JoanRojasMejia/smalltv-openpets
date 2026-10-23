"""Background worker: turns new Slack messages into a notification GIF on the screen.

It polls Slack, renders the GIF with the current pet (a chat message, or a Calcifer review
verdict), uploads it and hands (device path, seconds on screen) to the bridge, which shows it
and then returns to the agent state. Message text is never logged.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import config
import setup_device
from pet_sync import GIF_DIR, ROOT, look_args, uv_binary
from slack_source import POLL_SECONDS, SlackPoller, load_token, make_api

MESSAGE_SECONDS = 20
CALCIFER_SECONDS = 45


@dataclass
class Notification:
    kind: str  # "message" or "calcifer"
    title: str  # sender line, or the Calcifer verdict key ("comment" when there is none)
    text: str


def summarize(messages) -> Notification:
    """What to show for a batch: the newest Calcifer review if any, else the newest message."""
    reviews = [m for m in messages if m.kind == "calcifer"]
    if reviews:
        newest = reviews[-1]
        return Notification("calcifer", newest.verdict or "comment", newest.text)
    newest = messages[-1]
    sender = newest.sender if len(messages) == 1 else f"{newest.sender} +{len(messages) - 1}"
    return Notification("message", sender, newest.text)


class SlackNotifier:
    def __init__(self, host: str, log, poller: SlackPoller):
        self.host = host
        self.log = log
        self.poller = poller
        self.ready: queue.Queue[tuple[str, float]] = queue.Queue()

    @classmethod
    def start(cls, host: str, log) -> SlackNotifier | None:
        token = load_token()
        if not token:
            log("slack disabled: no token in the Keychain (service smalltv-openpets-slack)")
            return None
        settings = config.load()
        calcifer = (settings.get("calcifer_user"), settings.get("calcifer_channel"))
        calcifer = calcifer if all(calcifer) else None
        notifier = cls(host, log, SlackPoller(make_api(token), calcifer=calcifer))
        threading.Thread(target=notifier._run, name="slack", daemon=True).start()
        log("slack notifications enabled" + (" (with Calcifer reviews)" if calcifer else ""))
        return notifier

    def take(self) -> tuple[str, float] | None:
        """(device path, seconds on screen) of a freshly uploaded notification, if any."""
        try:
            return self.ready.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        reported, first = None, True
        while True:
            try:
                messages = self.poller.poll(time.time())
                if first and self.poller.last_counts:
                    first = False
                    self.log(f"slack first poll: {self.poller.last_counts} results")
                if self.poller.last_error != reported:
                    reported = self.poller.last_error
                    self.log(f"slack poll failed ({reported}); backing off" if reported else "slack poll ok")
                if messages:
                    note = summarize(messages)
                    detail = f" (calcifer: {note.title})" if note.kind == "calcifer" else ""
                    self.log(f"slack: {len(messages)} new message(s){detail}")
                    self._show(note)
            except (Exception, SystemExit) as exc:  # keep the worker alive (upload_verified raises SystemExit)
                self.log(f"slack worker error: {type(exc).__name__}")
            time.sleep(POLL_SECONDS)

    def _show(self, note: Notification) -> None:
        if note.kind == "calcifer":
            filename, seconds = "op-calcifer.gif", CALCIFER_SECONDS
            options = ["--calcifer", note.title, "--message-text", note.text]
        else:
            filename, seconds = "op-message.gif", MESSAGE_SECONDS
            options = ["--message-from", note.title, "--message-text", note.text]
        with tempfile.TemporaryDirectory(prefix="smalltv-msg-") as tmp:
            subprocess.run([uv_binary(), "run", "--quiet", "--with", "pillow", "python", str(ROOT / "make_gifs.py"),
                            *look_args(), "--out", tmp, *options], check=True, cwd=ROOT, capture_output=True,
                           timeout=120)
            shutil.copy2(Path(tmp) / filename, GIF_DIR / filename)
        setup_device.upload_verified(self.host, GIF_DIR / filename)
        self.ready.put((f"/image/{filename}", seconds))
