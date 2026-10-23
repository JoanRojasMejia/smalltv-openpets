"""Daily stand-up digest: what you did on a workday, what is next, and what is blocked.

collect() gathers that day's facts: your commits in `work_repos`, Jira tickets whose status
changed, your task in progress and blocked tickets, your deploys, the agents' Engram memories
and their daily notes (add_note / `python3 daily_digest.py note "..."`). synthesize() asks
Claude Haiku (headless, no tools, no hooks) to turn them into three short lists; when the
model is unavailable, fallback_digest() builds them from the raw data. Digests are stored per
day in DAILY_DIR; the bridge shows the previous workday's digest before your daily meeting.

Usage:
  python3 daily_digest.py generate [--date YYYY-MM-DD]   # build and print a day's digest
  python3 daily_digest.py note "ABC-41: validation done"   # add a note for today
  python3 daily_digest.py send [--date YYYY-MM-DD]       # send the long digest by iMessage
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "smalltv-openpets"
DAILY_DIR = STATE / "daily"
NOTES_DIR = STATE / "notes"
DEPLOYS_DIR = STATE / "deploys"
ENGRAM_DB = Path.home() / ".engram/engram.db"
MAX_ITEM = 48
MAX_ITEMS = 3
MAX_DETAIL = 1800
WEEKDAYS_FULL_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
GENERATE_AT = time(17, 50)
SECTIONS = ("ayer", "hoy", "bloqueos")


# --- facts ---------------------------------------------------------------------------------

def _local_day_bounds(day: date, tz) -> tuple[datetime, datetime]:
    tz = tz or datetime.now().astimezone().tzinfo
    start = datetime.combine(day, time(0), tz)
    return start, start + timedelta(days=1)


def engram_memories(day: date, db: Path = ENGRAM_DB, exclude: set[str] = frozenset(), tz=None) -> list[tuple]:
    """(type, project, title) of the memories agents saved that local day (Engram stores UTC)."""
    if not db.exists():
        return []
    start, end = (b.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") for b in _local_day_bounds(day, tz))
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute("select type, project, title from observations where deleted_at is null "
                               "and created_at >= ? and created_at < ? order by created_at", (start, end)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return [tuple(r) for r in rows if r[1] not in exclude]


def add_note(text: str, notes_dir: Path = NOTES_DIR, now: datetime | None = None, cwd: str | None = None) -> None:
    now = now or datetime.now().astimezone()
    cwd = os.getcwd() if cwd is None else cwd
    project = f"[{Path(cwd).name}] " if cwd else ""
    notes_dir.mkdir(parents=True, exist_ok=True)
    with open(notes_dir / f"{now.date().isoformat()}.md", "a") as f:
        f.write(f"{now:%H:%M} {project}{' '.join(text.split())}\n")


def read_notes(day: date, notes_dir: Path = NOTES_DIR) -> list[str]:
    try:
        return [line for line in (notes_dir / f"{day.isoformat()}.md").read_text().splitlines() if line.strip()]
    except OSError:
        return []


def record_deploy(execution: dict, deploys_dir: Path = DEPLOYS_DIR) -> None:
    """Called by the pipeline worker for each of your finished deploys."""
    from pipeline_source import commit_subject, env_label

    day = datetime.fromtimestamp(execution["updated"]).date().isoformat()
    deploys_dir.mkdir(parents=True, exist_ok=True)
    with open(deploys_dir / f"{day}.jsonl", "a") as f:
        f.write(json.dumps([env_label(execution["pipeline"]), execution["status"],
                            commit_subject(execution.get("summary", ""))]) + "\n")


def read_deploys(day: date, deploys_dir: Path = DEPLOYS_DIR) -> list[list]:
    try:
        return [json.loads(line) for line in (deploys_dir / f"{day.isoformat()}.jsonl").read_text().splitlines()
                if line.strip()]
    except (OSError, ValueError):
        return []


def git_commits(day: date, repos: list[str], emails: list[str], tz=None) -> list[list[str]]:
    start, end = _local_day_bounds(day, tz)
    commits = []
    for repo in repos:
        path = Path(repo).expanduser()
        if not (path / ".git").exists():
            continue
        cmd = ["git", "-C", str(path), "log", "--all", "--no-merges", f"--since={start.isoformat()}",
               f"--until={end.isoformat()}", "--pretty=format:%s"] + [f"--author={e}" for e in emails]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        commits += [[path.name, s] for s in dict.fromkeys(out.stdout.splitlines()) if s.strip()]
    return commits[:25]


def collect(day: date, settings: dict) -> dict:
    """Every fact about `day` that is available; missing sources are simply empty."""
    import jira_source

    data = {"day": day.isoformat(), "commits": [], "jira_changes": [], "task": None, "blocked": [],
            "deploys": read_deploys(day), "notes": read_notes(day), "agents": None,
            "memories": engram_memories(day, exclude=set(settings.get("daily_exclude_projects", ["personal"])))}
    emails = settings.get("my_emails") or [settings.get("my_email")]
    data["commits"] = git_commits(day, settings.get("work_repos", []), [e for e in emails if e])
    try:
        stats = json.loads((STATE / "today.json").read_text())
        if stats.get("day") == day.isoformat():
            data["agents"] = {"sessions": len(stats["sessions"]), "asks": stats["asks"]}
    except (OSError, ValueError, KeyError):
        pass
    site, credentials = settings.get("jira_site"), jira_source.load_credentials()
    if site and credentials:
        span = f'"{day.isoformat()} 00:00", "{day.isoformat()} 23:59"'
        try:
            changed = jira_source.jira_search(site, *credentials, f"assignee = currentUser() AND status CHANGED "
                                              f"DURING ({span})", "summary,status", limit=15)
            data["jira_changes"] = [[i["key"], jira_source.clean_summary(i["fields"]["summary"]),
                                     i["fields"]["status"]["name"]] for i in changed]
            blocked = jira_source.jira_search(site, *credentials, "assignee = currentUser() AND status = "
                                              f'"{settings.get("jira_blocked_status", "Bloqueado")}"', "summary")
            data["blocked"] = [[i["key"], jira_source.clean_summary(i["fields"]["summary"])] for i in blocked]
            task = jira_source.fetch_current_task(site, *credentials, settings.get("jira_status") or "En Progreso",
                                                  settings.get("jira_points_field") or "customfield_10105",
                                                  settings.get("jira_review_status", "PR PROD"))
            if task:
                end = min(datetime.now(timezone.utc), _local_day_bounds(day, None)[1].astimezone(timezone.utc))
                worked = jira_source.task_worked_hours(
                    task, jira_source.Schedule.from_config(settings.get("work_schedule")), now=end)
                _, percent, left, summary = jira_source.summary_text(task, worked)
                data["task"] = {"key": task["key"], "summary": summary, "percent": percent, "left": left}
        except Exception:
            pass
    return data


# --- digest ----------------------------------------------------------------------------------

PROMPT = """Eres el asistente de un desarrollador frontend. Con los datos de su jornada (JSON abajo) \
prepara su resumen para la daily del día siguiente, en español, muy breve:
- "ayer": lo que hizo ese día (máximo 3 puntos, agrupa commits del mismo ticket, menciona el ticket).
- "hoy": lo que sigue (tarea en progreso y su avance, pendientes evidentes; máximo 3 puntos).
- "bloqueos": impedimentos reales (tickets bloqueados u otros); lista vacía si no hay.
Cada punto de máximo 45 caracteres, sin emojis, sin inventar nada que no esté en los datos.
Además, "detalle": un texto plano más completo para leer en el celular antes de la daily (máximo 1500
caracteres, sin markdown, líneas cortas), con estas partes en este orden: estado por ticket (qué se
hizo, estado en Jira, avance), commits y PRs agrupados por ticket, despliegues, bloqueos con contexto,
próximos pasos, y al final "Para decir en la daily:" con 2 o 3 frases en primera persona.
Responde SOLO con JSON: {"ayer": [...], "hoy": [...], "bloqueos": [...], "detalle": "..."}

DATOS:
"""


def parse_digest(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return None
    if not all(isinstance(raw.get(k), list) for k in SECTIONS):
        return None
    digest = {k: [str(i).strip()[:MAX_ITEM] for i in raw[k] if str(i).strip()][:MAX_ITEMS] for k in SECTIONS}
    if isinstance(raw.get("detalle"), str) and raw["detalle"].strip():
        digest["detalle"] = raw["detalle"].strip()[:MAX_DETAIL]
    return digest


def fallback_digest(data: dict) -> dict:
    from pipeline_source import commit_subject

    ayer = []
    if data["commits"]:
        ayer.append(f"{len(data['commits'])} commits: {commit_subject(data['commits'][0][1])}"[:MAX_ITEM])
    ayer += [f"{env} {'desplegado' if status == 'Succeeded' else 'falló'}" for env, status, *_ in data["deploys"]]
    ayer += [f"{key} -> {status}" for key, _, status in data["jira_changes"]]
    hoy = [f"{data['task']['key']} al {data['task']['percent']}% ({data['task']['left']})"] if data.get("task") else []
    bloqueos = [f"{key} {summary}"[:MAX_ITEM] for key, summary in data["blocked"]]
    return {"ayer": ayer[:MAX_ITEMS], "hoy": hoy[:MAX_ITEMS], "bloqueos": bloqueos[:MAX_ITEMS]}


def fallback_detail(data: dict) -> str:
    """The long version without the model: every fact, grouped."""
    lines = []
    if data.get("task"):
        t = data["task"]
        lines += [f"Tarea en progreso: {t['key']} {t.get('summary', '')}", f"Avance: {t['percent']}% ({t['left']})", ""]
    if data["jira_changes"]:
        lines += ["Cambios en Jira:"] + [f"- {k} {s} -> {st}" for k, s, st in data["jira_changes"]] + [""]
    if data["commits"]:
        lines += [f"Commits ({len(data['commits'])}):"] + [f"- {repo}: {subject}" for repo, subject in data["commits"][:10]] + [""]
    if data["deploys"]:
        lines.append("Despliegues:")
        for env, status, *rest in data["deploys"]:
            subject = rest[0] if rest else ""
            lines.append(f"- {env} {'OK' if status == 'Succeeded' else 'FALLÓ'} {subject}".rstrip())
        lines.append("")
    if data["blocked"]:
        lines += ["Bloqueos:"] + [f"- {k} {s}" for k, s in data["blocked"]] + [""]
    if data["notes"]:
        lines += ["Notas de los agentes:"] + [f"- {n}" for n in data["notes"][:8]] + [""]
    if data.get("agents"):
        lines.append(f"Agentes: {data['agents']['sessions']} sesiones, {data['agents']['asks']} pedidos de ayuda")
    return "\n".join(lines).strip()[:MAX_DETAIL]


def message_text(record: dict) -> str:
    """The long digest as a phone message."""
    day = date.fromisoformat(record["day"])
    parts = [f"Daily - {WEEKDAYS_FULL_ES[day.weekday()]} {day:%d/%m}"]
    for key, empty in (("ayer", "Sin registros"), ("hoy", "Sin pendientes registrados"), ("bloqueos", "Ninguno")):
        items = record.get(key) or [empty]
        parts.append(key.upper() + "\n" + "\n".join(f"- {i}" for i in items))
    if record.get("detalle"):
        parts.append(record["detalle"])
    return "\n\n".join(parts)


def message_due(now: datetime, daily_starts: list[float]) -> bool:
    """Send on workdays from 09:00, or 15 minutes before an earlier daily meeting."""
    if now.weekday() >= 5:
        return False
    if now.time() >= time(9, 0):
        return True
    return any(0 < start - now.timestamp() <= 15 * 60 for start in daily_starts)


SEND_SCRIPT = """on run argv
    set theText to item 1 of argv
    set theRecipient to item 2 of argv
    tell application "Messages"
        set theService to 1st account whose service type = iMessage
        send theText to participant theRecipient of theService
    end tell
end run"""


MESSAGES_DB = Path.home() / "Library/Messages/chat.db"
APPLE_EPOCH = 978307200  # 2001-01-01, Messages' time origin


def delivery_status(to: str, since: float, db: Path = MESSAGES_DB) -> str:
    """Status of the newest message sent to `to` after `since`: delivered, sent, failed (...) or unknown.

    Reading chat.db may need Full Disk Access; without it the status is "unknown"."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("select m.is_sent, m.is_delivered, m.error from message m join handle h "
                              "on m.handle_id = h.ROWID where m.is_from_me = 1 and h.id = ? and m.date > ? "
                              "order by m.date desc limit 1", (to, int((since - APPLE_EPOCH) * 1e9))).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return "unknown"
    if row is None:
        return "unknown"
    is_sent, is_delivered, error = row
    if error:
        return f"failed (error {error})"
    return "delivered" if is_delivered else "sent" if is_sent else "unknown"


def send_imessage(to: str, text: str, wait: float = 20) -> str:
    """Send through the Messages app and report what Messages did with it.

    macOS asks once to allow controlling Messages. Returns delivery_status() after up to `wait` s."""
    started = time_module.time()
    subprocess.run(["osascript", "-e", SEND_SCRIPT, text, to], check=True, capture_output=True, timeout=120)
    status = "unknown"
    while time_module.time() - started < wait:
        status = delivery_status(to, started - 1)
        if status == "delivered" or status.startswith("failed"):
            break
        time_module.sleep(2)
    return status


def claude_binary() -> str:
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def synthesize(data: dict) -> tuple[dict, str]:
    """(digest, source) where source is "haiku" or "fallback"."""
    try:
        out = subprocess.run([claude_binary(), "-p", "--model", "haiku", "--no-session-persistence",
                              "--strict-mcp-config", "--tools", "", "--settings", '{"disableAllHooks": true}',
                              "--output-format", "text"],
                             input=PROMPT + json.dumps(data, ensure_ascii=False), capture_output=True, text=True,
                             timeout=180, cwd=str(STATE) if STATE.exists() else None)
        digest = parse_digest(out.stdout) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        digest = None
    return (digest, "haiku") if digest else (fallback_digest(data), "fallback")


def generate(day: date, settings: dict, daily_dir: Path = DAILY_DIR) -> dict:
    data = collect(day, settings)
    digest, source = synthesize(data)
    digest.setdefault("detalle", fallback_detail(data))
    record = {"day": day.isoformat(), "generated": datetime.now().astimezone().isoformat(timespec="minutes"),
              "source": source, **digest,
              "facts": {k: len(v) if isinstance(v, list) else bool(v) for k, v in data.items() if k != "day"}}
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / f"{day.isoformat()}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record


def load_digest(day: date, daily_dir: Path = DAILY_DIR) -> dict | None:
    try:
        return json.loads((daily_dir / f"{day.isoformat()}.json").read_text())
    except (OSError, ValueError):
        return None


# --- schedule --------------------------------------------------------------------------------

def previous_workday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def digest_due(now: datetime, have: set[date]) -> date | None:
    """The day whose digest should be generated now, if any."""
    today = now.date()
    if today.weekday() < 5 and now.time() >= GENERATE_AT:
        target = today
    else:
        target = previous_workday(today)
    return None if target in have else target


def daily_slot(now: float, start: float) -> str | None:
    """Which reminder before the daily is due: "10" (10 min before) or "3" (3 min before)."""
    left = start - now
    if 240 < left <= 600:
        return "10"
    if 0 < left <= 240:
        return "3"
    return None


def main() -> None:
    import config

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--date", default=None)
    note = sub.add_parser("note")
    note.add_argument("text", nargs="+")
    send = sub.add_parser("send", help="send a day's long digest by iMessage to imessage_to")
    send.add_argument("--date", default=None)
    args = parser.parse_args()
    if args.cmd == "note":
        add_note(" ".join(args.text))
        print("noted")
        return
    if args.cmd == "send":
        settings = config.load()
        day = date.fromisoformat(args.date) if args.date else previous_workday(date.today())
        record = load_digest(day)
        if not record or not record.get("detalle"):
            record = generate(day, settings)
        status = send_imessage(settings["imessage_to"], message_text(record))
        print(f"{day} digest: {status}")
        if status.startswith("failed"):
            sys.exit(1)
        return
    day = date.fromisoformat(args.date) if args.date else digest_due(datetime.now().astimezone(), set()) or date.today()
    record = generate(day, config.load())
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
