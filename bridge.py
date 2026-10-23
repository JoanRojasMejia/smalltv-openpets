"""Mirror OpenPets agent status onto a GeekMagic SmallTV-Ultra.

Tails the OpenPets debug log, maps each pet reaction to a screen state and
switches the screen's photo album to that state's pre-uploaded GIF.

Usage: python3 bridge.py [--host <screen-ip>] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import config
import pet_sync
import session_store
import setup_device
from pet_sync import PetSyncer
from calendar_source import CalendarNotifier
from pipeline_source import PipelineNotifier
from slack_notifier import SlackNotifier
from states import REACTION_TO_STATE, device_path

DEFAULT_LOG = Path.home() / "Library/Application Support/@open-pets/desktop/logs/openpets.log"
REACTION_RE = re.compile(r'DEBUG ipc\s+pet (?:react|say) requested .*?reaction="(\w+)"')

# Seconds without new events before a state falls back to idle. "waiting" never does:
# an agent that needs you keeps saying so.
STALE_AFTER = {"thinking": 600, "working": 600, "testing": 600, "done": 60, "error": 300}
# How long a Slack message stays on screen before the agent state returns (seconds).
MESSAGE_SECONDS = 20
# On startup, resume from the last reaction if it is at most this old (seconds).
STARTUP_LOOKBACK = 600


def parse_reaction(line: str) -> str | None:
    match = REACTION_RE.search(line)
    return match.group(1) if match else None


def recent_reaction(path, now_epoch: float, max_age: float) -> tuple[str, float] | None:
    """Last reaction in the log's tail and its age in seconds, if newer than max_age."""
    try:
        with open(path, "rb") as f:
            f.seek(max(os.path.getsize(path) - 256 * 1024, 0))
            lines = f.read().decode(errors="replace").splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        reaction = parse_reaction(line)
        if not reaction:
            continue
        try:
            stamp = datetime.strptime(line.split(" ", 1)[0], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        age = now_epoch - stamp.timestamp()
        return (reaction, age) if 0 <= age <= max_age else None
    return None


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


class StateMachine:
    def __init__(self, stale_after: dict[str, float] | None = None):
        self.stale_after = STALE_AFTER if stale_after is None else stale_after
        self.state = "idle"
        self.last_event = 0.0

    def on_reaction(self, reaction: str, now: float) -> str | None:
        """Return the new state if it changed, else None."""
        state = REACTION_TO_STATE.get(reaction)
        if state is None:
            return None
        self.last_event = now
        if state == self.state:
            return None
        self.state = state
        return state

    def tick(self, now: float) -> str | None:
        """Return "idle" when the current state went stale, else None."""
        limit = self.stale_after.get(self.state)
        if limit is not None and now - self.last_event >= limit:
            self.state = "idle"
            return "idle"
        return None


def http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read().decode(errors="replace").strip()


class Display:
    """Pushes the latest wanted state to the screen, rate-limited and retried."""

    def __init__(self, host: str, http=http_get, min_interval: float = 1.5, retry_after: float = 10):
        self.host = host
        self.http = http
        self.min_interval = min_interval
        self.retry_after = retry_after
        self.pending: str | None = None
        self.pending_path = ""
        self.current_path: str | None = None  # last GIF the screen confirmed
        self.next_try = 0.0

    def show(self, state: str, now: float) -> None:
        self.show_path(state, device_path(state), now)

    def show_path(self, label: str, path: str, now: float) -> None:
        """Show any GIF stored on the screen (label is only used for logging)."""
        self.pending, self.pending_path = label, path

    def flush(self, now: float) -> None:
        if self.pending is None or now < self.next_try:
            return
        state = self.pending
        url_path = self.pending_path
        url = f"http://{self.host}/set?img={urllib.parse.quote(url_path, safe='')}"
        try:
            reply = self.http(url)
        except OSError as exc:
            log(f"screen unreachable ({exc}); retrying {state} in {self.retry_after:.0f}s")
            self.next_try = now + self.retry_after
            return
        if reply != "OK":
            log(f"screen replied {reply!r} for {state}")
        else:
            log(f"screen -> {state}")
        if reply == "OK":
            self.current_path = url_path
        if self.pending == state:
            self.pending = None
        self.next_try = now + self.min_interval


class LogTailer:
    """Reads lines appended to a file, following truncation and rotation."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.file = None
        self.inode = None
        self.buffer = ""
        self._open(from_end=True)

    def _open(self, from_end: bool) -> None:
        if self.file:
            self.file.close()
            self.file = None
        try:
            self.file = open(self.path, "r", errors="replace")
        except FileNotFoundError:
            return
        self.inode = os.fstat(self.file.fileno()).st_ino
        self.buffer = ""
        if from_end:
            self.file.seek(0, os.SEEK_END)

    def _rotated(self) -> bool:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return False
        return self.file is None or st.st_ino != self.inode or st.st_size < self.file.tell()

    def read_lines(self) -> list[str]:
        if self._rotated():
            self._open(from_end=False)
        if self.file is None:
            return []
        self.buffer += self.file.read()
        *lines, self.buffer = self.buffer.split("\n")
        return lines


class SessionWatcher:
    """Aggregates the per-session state files written by the agent hooks."""

    def __init__(self, state_dir: Path = session_store.STATE_DIR):
        self.state_dir = state_dir
        self.state: str | None = None
        self.sessions: list[dict] = []

    def poll(self, now_epoch: float) -> str | None:
        """Return the aggregated state when it changed, else None."""
        self.sessions = session_store.read_sessions(self.state_dir)
        state = session_store.aggregate(self.sessions, now_epoch)
        if state == self.state:
            return None
        self.state = state
        return state


WAITING_DETAIL_PATH = "/image/op-waiting-detail.gif"


def render_waiting_detail(host: str, lines: list[str]) -> bool:
    """Render op-waiting-detail.gif with the current pet and upload it. Runs in a thread."""
    try:
        with tempfile.TemporaryDirectory(prefix="smalltv-wait-") as tmp:
            pet = pet_sync.synced_pet() or pet_sync.active_pet() or "miss-minute"
            subprocess.run([pet_sync.uv_binary(), "run", "--quiet", "--with", "pillow", "python",
                            str(pet_sync.ROOT / "make_gifs.py"), "--pet", pet, "--out", tmp,
                            "--waiting-detail", "|".join(lines)], check=True, capture_output=True, timeout=120)
            target = pet_sync.GIF_DIR / "op-waiting-detail.gif"
            shutil.copy2(Path(tmp) / target.name, target)
        setup_device.upload_verified(host, target)
        log(f"waiting detail uploaded ({len(lines)} session(s))")
        return True
    except (Exception, SystemExit) as exc:
        log(f"waiting detail failed: {type(exc).__name__}")
        return False


def make_display(host: str, dry_run: bool) -> Display:
    http = (lambda url: (log(f"[dry-run] GET {url}"), "OK")[1]) if dry_run else http_get
    return Display(host, http=http)


class MessageOverlay:
    """A chat message covers the agent state for a while, then hands the screen back."""

    def __init__(self, seconds: float = MESSAGE_SECONDS):
        self.seconds = seconds
        self.until = 0.0

    def start(self, now: float, seconds: float | None = None) -> None:
        self.until = now + (self.seconds if seconds is None else seconds)

    def active(self, now: float) -> bool:
        return now < self.until

    def just_ended(self, now: float) -> bool:
        """True exactly once, on the first check after the message time is over."""
        if self.until and now >= self.until:
            self.until = 0.0
            return True
        return False


class WaitingDetail:
    """Keeps a GIF on the screen that says which sessions wait for you and why.

    The static "I need you!" GIF shows at once; the detailed one is rendered and uploaded in
    the background (job(lines) does both) and replaces it when ready. Each distinct set of
    waiting sessions is uploaded once.
    """

    def __init__(self, job, start=lambda target, arg: threading.Thread(target=target, args=(arg,), daemon=True).start()):
        self.job = job
        self.start = start
        self.lock = threading.Lock()
        self.uploaded: str | None = None
        self.running: str | None = None
        self.fresh = False

    def _work(self, key: str) -> None:
        ok = False
        try:
            ok = bool(self.job(key.split("|")))
        finally:
            with self.lock:
                self.running = None
                if ok:
                    self.uploaded, self.fresh = key, True

    def poll(self, lines: list[str] | None) -> bool:
        """Call every loop with the waiting lines (None when nothing waits).
        True once, when the detail GIF for the current lines has just been uploaded."""
        key = "|".join(lines) if lines else None
        with self.lock:
            if key is None:
                return False
            if self.uploaded == key:
                fresh, self.fresh = self.fresh, False
                return fresh
            if self.running is None:
                self.running = key
                self.start(self._work, key)
            return False

    def ready_for(self, lines: list[str] | None) -> bool:
        with self.lock:
            return bool(lines) and self.uploaded == "|".join(lines)

    def invalidate(self) -> None:
        """Forget the uploaded GIF (e.g. the pet changed) so it is rendered again."""
        with self.lock:
            self.uploaded, self.fresh = None, False


def run_sessions(host: str, state_dir: Path, dry_run: bool, poll: float, pet_sync: bool, slack: bool,
                 calendar: bool, pipelines: bool) -> None:
    display = make_display(host, dry_run)
    watcher = SessionWatcher(state_dir)
    syncer = PetSyncer(host, log=log) if pet_sync and not dry_run else None
    notifiers = [n for n in (SlackNotifier.start(host, log) if slack and not dry_run else None,
                             CalendarNotifier.start(host, log) if calendar and not dry_run else None,
                             PipelineNotifier.start(host, log) if pipelines and not dry_run else None) if n]
    overlay = MessageOverlay()
    detail = WaitingDetail(lambda lines: render_waiting_detail(host, lines)) if not dry_run else None

    def waiting_lines(wall: float) -> list[str] | None:
        return session_store.waiting_summary(watcher.sessions, wall) if watcher.state == "waiting" else None

    def show_state(now: float, wall: float) -> None:
        if watcher.state == "waiting" and detail and detail.ready_for(waiting_lines(wall)):
            display.show_path("waiting detail", WAITING_DETAIL_PATH, now)
        else:
            display.show(watcher.state or "idle", now)

    log(f"watching sessions in {state_dir} -> {host}")
    next_prune = 0.0
    while True:
        now, wall = time.monotonic(), time.time()
        if now >= next_prune:
            session_store.prune(state_dir, now=wall)
            next_prune = now + 3600
        for notifier in notifiers:
            notification = notifier.take()
            if notification:
                path, seconds = notification
                display.show_path(Path(path).stem, path, now)
                overlay.start(now, seconds)
        changed = watcher.poll(wall)
        if changed:
            log(f"sessions -> {changed}")
        resync = syncer.poll(now, watcher.state) if syncer else False
        if resync and detail:
            detail.invalidate()
        detail_ready = detail.poll(waiting_lines(wall)) if detail else False
        # Self-heal: the detail GIF is ready for the current waiting sessions but not on screen.
        detail_ready = detail_ready or bool(
            detail and display.pending is None and display.current_path != WAITING_DETAIL_PATH
            and detail.ready_for(waiting_lines(wall)))
        if overlay.just_ended(now) or (not overlay.active(now) and (changed or resync or detail_ready)):
            show_state(now, wall)
        display.flush(now)
        time.sleep(poll)


def run(host: str, log_path: Path, dry_run: bool, poll: float) -> None:
    display = make_display(host, dry_run)
    machine = StateMachine()
    tailer = LogTailer(log_path)
    log(f"watching {log_path} -> {host}")
    now = time.monotonic()
    recent = recent_reaction(log_path, now_epoch=time.time(), max_age=STARTUP_LOOKBACK)
    if recent:
        reaction, age = recent
        machine.on_reaction(reaction, now - age)
        log(f"resuming from {reaction} ({age:.0f}s ago)")
    display.show(machine.state, now)
    while True:
        now = time.monotonic()
        for line in tailer.read_lines():
            reaction = parse_reaction(line)
            if reaction:
                changed = machine.on_reaction(reaction, now)
                if changed:
                    log(f"reaction {reaction} -> {changed}")
                    display.show(changed, now)
        stale = machine.tick(now)
        if stale:
            log("no activity -> idle")
            display.show(stale, now)
        display.flush(now)
        time.sleep(poll)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("SMALLTV_HOST") or config.load().get("host"),
                        help="screen IP (default: SMALLTV_HOST or host in ~/.config/smalltv-openpets/config.json)")
    parser.add_argument("--source", choices=["sessions", "log"], default="sessions",
                        help="per-session hook files (default) or the OpenPets log")
    parser.add_argument("--state-dir", type=Path, default=session_store.STATE_DIR, help="hook state directory")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="OpenPets log file (--source log)")
    parser.add_argument("--dry-run", action="store_true", help="print requests instead of sending them")
    parser.add_argument("--no-slack", action="store_true", help="do not show Slack DMs and mentions")
    parser.add_argument("--no-calendar", action="store_true", help="do not show meeting reminders")
    parser.add_argument("--no-pipelines", action="store_true", help="do not show CodePipeline deploys / AWS SSO expiry")
    parser.add_argument("--no-pet-sync", action="store_true",
                        help="do not re-render and upload the GIFs when the OpenPets pet changes")
    parser.add_argument("--poll", type=float, default=0.5, help="seconds between log reads")
    args = parser.parse_args()
    if not args.host:
        parser.error("--host is required (or set SMALLTV_HOST)")
    try:
        if args.source == "sessions":
            run_sessions(args.host, args.state_dir, args.dry_run, args.poll,
                         pet_sync=not args.no_pet_sync, slack=not args.no_slack, calendar=not args.no_calendar,
                         pipelines=not args.no_pipelines)
        else:
            run(args.host, args.log, args.dry_run, args.poll)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
