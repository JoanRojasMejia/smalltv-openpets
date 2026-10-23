"""Deploy notifications from AWS CodePipeline for pipelines that build your CodeCommit repos.

Every POLL_SECONDS the worker asks tools/aws_pipelines.py (boto3, through uv) for the latest
executions of the pipelines sourced from `pipeline_repos` (config). A finished execution is
"yours" when its source commit was authored by `my_email`, or when your email appears in the
commit message (squash merges list every squashed author). A started execution shows "DEPLOYING..." once,
then Succeeded -> "DEPLOYED", Failed -> "DEPLOY FAILED"; while one of yours runs, polling speeds
up to FAST_POLL_SECONDS. When the AWS SSO session expires the screen asks you to run
`aws sso login`, and repeats the reminder every SSO_REMINDER_SECONDS until it works again.
"""

from __future__ import annotations

import json
import os
import re
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import config
import setup_device
from daily_digest import record_deploy
from status_board import BOARD
from pet_sync import GIF_DIR, ROOT, look_args, uv_binary

POLL_SECONDS = 120
DISCOVER_EVERY = 24 * 3600
SSO_REMINDER_SECONDS = 1800
PIPELINES_CACHE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "smalltv-openpets/pipelines.json"
CARD_SECONDS = {"running": 20, "ok": 30, "failed": 45, "sso": 45, "jira": 45}
FAST_POLL_SECONDS = 45  # while one of your deploys is running


class SsoExpired(Exception):
    """The AWS SSO session expired: `aws sso login` is needed."""


def env_label(pipeline: str) -> str:
    name = pipeline.removeprefix("cloudfront-").removesuffix("-Pipeline")
    return name.upper()


def commit_subject(summary: str, fallback: str = "") -> str:
    """'[ABC-39] feat(scope): text' -> 'ABC-39 text' (ticket kept, conventional prefix dropped)."""
    line = (summary or "").strip().splitlines()[0] if (summary or "").strip() else fallback
    ticket = re.match(r"^\[([^\]]+)\]\s*", line)
    rest = line[ticket.end():] if ticket else line
    rest = re.sub(r"^[a-z]+(\([^)]*\))?!?:\s*", "", rest)
    return f"{ticket.group(1)} {rest}".strip() if ticket else rest


def deploy_card(execution: dict) -> tuple[str, str, str]:
    """(title, body, kind) for a finished execution."""
    subject = commit_subject(execution.get("summary") or "", execution.get("branch") or "")
    body = f"{env_label(execution['pipeline'])}: {subject}".strip()
    if execution.get("phase") == "started":
        return "DEPLOYING...", body, "running"
    if execution["status"] == "Succeeded":
        return "DEPLOYED", body, "ok"
    return "DEPLOY FAILED", body, "failed"


def _aws_env() -> dict:
    env = {**os.environ}
    profile = config.load().get("aws_profile")
    if profile:
        env["SMALLTV_AWS_PROFILE"] = profile
    return env


def _run_tool(args: list[str], stdin: str | None = None, timeout: int = 600) -> list:
    out = subprocess.run([uv_binary(), "run", "--quiet", "--with", "boto3", "python",
                          str(ROOT / "tools/aws_pipelines.py"), *args],
                         input=stdin, capture_output=True, text=True, timeout=timeout, env=_aws_env(), cwd=ROOT)
    if out.returncode == 3:
        raise SsoExpired()
    if out.returncode != 0:
        raise RuntimeError(f"aws_pipelines {args[0]} failed")
    return json.loads(out.stdout)


def poll_tool(pipelines: list[dict], known: set[str]) -> list[dict]:
    return _run_tool(["poll"], json.dumps({"pipelines": pipelines, "known": sorted(known)}), timeout=300)


def discover_pipelines(repos: list[str], cache: Path = PIPELINES_CACHE) -> list[dict]:
    """Pipelines sourced from the repos, cached for a day (discovery scans every pipeline)."""
    try:
        data = json.loads(cache.read_text())
        if data["repos"] == sorted(repos) and time.time() - data["ts"] < DISCOVER_EVERY:
            return data["pipelines"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    pipelines = _run_tool(["discover", *repos], timeout=900)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"ts": time.time(), "repos": sorted(repos), "pipelines": pipelines}))
    return pipelines


class PipelineWatcher:
    """Turns polled executions of your deploys into events: "started" once, "finished" once."""

    FINISHED = {"Succeeded", "Failed", "Stopped", "Superseded"}
    REPORTED = {"Succeeded", "Failed"}

    def __init__(self, tool, pipelines: list[dict], my_emails):
        self.tool = tool
        self.pipelines = pipelines
        emails = [my_emails] if isinstance(my_emails, str) else list(my_emails)
        self.me = [e.strip().lower() for e in emails if e and e.strip()]
        self.known: set[str] = set()  # finished executions (their authors are not fetched again)
        self.mine_ids: set[str] = set()
        self.started: set[str] = set()
        self.baselined = False
        self.running_mine = False
        self.last_mine: dict | None = None  # newest finished Succeeded/Failed execution of yours
        self.finished_now: list[dict] = []  # executions of anyone that finished in the last poll

    def _mine(self, e: dict) -> bool:
        author, summary = (e.get("author") or "").lower(), (e.get("summary") or "").lower()
        return any(author == me or me in summary for me in self.me)

    def poll(self) -> list[dict]:
        executions = self.tool(self.pipelines, self.known)  # may raise SsoExpired
        events, running_mine = [], False
        self.finished_now = []
        for e in sorted(executions, key=lambda e: e["updated"]):
            if e["status"] in self.REPORTED and (self.last_mine is None or e["updated"] > self.last_mine["updated"]) \
                    and (e["id"] in self.mine_ids or (e["id"] not in self.known and self._mine(e))):
                self.last_mine = e
            if e["id"] in self.known:
                continue
            mine = self._mine(e)
            if mine:
                self.mine_ids.add(e["id"])
            if e["status"] in self.FINISHED:
                self.known.add(e["id"])
                if self.baselined:
                    self.finished_now.append(e)
                if self.baselined and e["status"] in self.REPORTED and mine:
                    events.append({**e, "phase": "finished"})
                continue
            running_mine = running_mine or mine
            if e["id"] not in self.started:
                self.started.add(e["id"])
                if self.baselined and mine:
                    events.append({**e, "phase": "started"})
        self.baselined = True
        self.running_mine = running_mine
        return events


def make_followup():
    """Jira follow-up for production deploys, when Jira is configured (see jira_source)."""
    from jira_source import load_credentials, open_tickets
    from release_followup import ReleaseFollowup

    site, credentials = config.load().get("jira_site"), load_credentials()
    if not site or not credentials:
        return None
    return ReleaseFollowup(lambda keys: open_tickets(site, *credentials, keys))


class PipelineNotifier:
    """Background worker: polls pipelines and uploads deploy / SSO-login cards for the bridge."""

    def __init__(self, host: str, log, repos: list[str], my_emails: list[str]):
        self.host = host
        self.log = log
        self.repos = repos
        self.my_emails = my_emails
        self.ready: queue.Queue[tuple[str, float]] = queue.Queue()
        self.followup = make_followup()
        self.next_followup_refresh = 0.0

    @classmethod
    def start(cls, host: str, log) -> PipelineNotifier | None:
        settings = config.load()
        emails = settings.get("my_emails") or [settings.get("my_email")]
        emails = [e for e in emails if e]
        repos = settings.get("pipeline_repos") or []
        if not repos or not emails:
            log("pipelines disabled: set pipeline_repos and my_emails in the config")
            return None
        notifier = cls(host, log, repos, emails)
        threading.Thread(target=notifier._run, name="pipelines", daemon=True).start()
        log(f"pipeline deploy notifications enabled ({len(repos)} repo(s))")
        return notifier

    def take(self) -> tuple[str, float] | None:
        try:
            return self.ready.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        watcher, sso_until, error, fast = None, 0.0, None, False
        while True:
            now = time.time()
            try:
                if watcher is None or now - watcher.created > DISCOVER_EVERY:
                    pipelines = discover_pipelines(self.repos)
                    watcher = PipelineWatcher(poll_tool, pipelines, self.my_emails)
                    watcher.created = now
                    self.log(f"pipelines: watching {len(pipelines)} pipeline(s)")
                events = watcher.poll()
                for e in events:
                    if e["phase"] == "finished":
                        record_deploy(e)
                for e in events[-2:]:  # a burst shows only the latest two
                    title, body, kind = deploy_card(e)
                    self.log(f"pipelines: {kind} {env_label(e['pipeline'])}")
                    self._show("op-deploy.gif", kind, title, body)
                fast = watcher.running_mine
                self._follow_up(watcher.finished_now, now)
                last = watcher.last_mine
                BOARD.set("last_deploy", {"env": env_label(last["pipeline"]), "status": last["status"],
                                          "ts": last["updated"]} if last else None)
                if sso_until:
                    self.log("aws sso session is valid again")
                sso_until, error = 0.0, None
            except SsoExpired:
                if now >= sso_until:
                    self.log("aws sso session expired: run `aws sso login`")
                    self._show("op-sso.gif", "sso", "AWS LOGIN NEEDED", "RUN: AWS SSO LOGIN")
                    sso_until = now + SSO_REMINDER_SECONDS
            except (Exception, SystemExit) as exc:  # keep the worker alive
                if type(exc).__name__ != error:
                    self.log(f"pipelines error: {type(exc).__name__}")
                error = type(exc).__name__
            time.sleep(60 if sso_until else FAST_POLL_SECONDS if fast else POLL_SECONDS)

    def _follow_up(self, finished: list[dict], now: float) -> None:
        """Remind to close Jira tickets that just reached production."""
        from jira_source import clean_summary

        if self.followup is None:
            return
        for item in self.followup.on_deploys(finished)[-2:]:
            self.log(f"jira: {item['key']} reached {item['env']}")
            self._show("op-jira.gif", "jira", "IN PROD: MOVE TO DONE?", f"{item['key']} {clean_summary(item['summary'])}")
        if now >= self.next_followup_refresh:
            self.followup.refresh()
            self.next_followup_refresh = now + 1800
        BOARD.set("jira_pending", {"items": self.followup.pending()})

    def _show(self, filename: str, kind: str, title: str, body: str) -> None:
        with tempfile.TemporaryDirectory(prefix="smalltv-card-") as tmp:
            subprocess.run([uv_binary(), "run", "--quiet", "--with", "pillow", "python", str(ROOT / "make_gifs.py"),
                            *look_args(), "--out", tmp, "--card", kind, "--card-title", title,
                            "--message-text", body], check=True, cwd=ROOT, capture_output=True, timeout=120)
            shutil.copy2(Path(tmp) / "op-card.gif", GIF_DIR / filename)
        setup_device.upload_verified(self.host, GIF_DIR / filename)
        self.ready.put((f"/image/{filename}", CARD_SECONDS[kind]))
