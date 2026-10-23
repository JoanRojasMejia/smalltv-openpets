"""Claude Code hook: record this session's state for the SmallTV bridge.

Registered for SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest,
Notification, Stop, StopFailure and SessionEnd. Reads the hook JSON on stdin, prints
nothing and always exits 0 so it can never block or alter Claude Code.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

END = object()

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "ToolSearch"}
TEST_COMMAND = re.compile(
    r"(^|[\s;&|(/])("
    r"pytest|py\.test|unittest|jest|vitest|mocha|rspec|phpunit|playwright\s+test|"
    r"go\s+test|cargo\s+test|dotnet\s+test|mvn\s+test|gradlew?\s+test|"
    r"(npm|pnpm|yarn|bun)\s+(run\s+)?test"
    r")\b"
)
LIFECYCLE = {"SessionStart": "idle", "UserPromptSubmit": "thinking", "PermissionRequest": "waiting",
             "PostToolUse": "working", "Stop": "done", "StopFailure": "error"}


def classify(event: dict):
    """Screen state for a hook event, END for a finished session, or None to ignore."""
    name = event.get("hook_event_name")
    if name == "SessionEnd":
        return END
    if name in LIFECYCLE:
        return LIFECYCLE[name]
    if name == "Notification":
        kind = str(event.get("notification_type", ""))
        message = str(event.get("message", "")).lower()
        if kind == "permission_prompt" or "permission" in message:
            return "waiting"
        # Stop does not fire when the user interrupts (Esc); the idle prompt clears the stuck state.
        if kind == "idle_prompt" or "waiting for your input" in message:
            return "idle"
        return None
    if name == "PreToolUse":
        tool = str(event.get("tool_name", ""))
        if tool == "AskUserQuestion":
            return "waiting"
        if tool in READ_TOOLS:
            return "thinking"
        if tool == "Bash":
            tool_input = event.get("tool_input")
            command = str(tool_input.get("command", "")) if isinstance(tool_input, dict) else ""
            return "testing" if TEST_COMMAND.search(command) else "working"
        return "working"
    return None


def waiting_detail(event: dict) -> str:
    """Short reason a session waits for the user, shown on the screen."""
    name = event.get("hook_event_name")
    tool = str(event.get("tool_name", ""))
    if name == "PermissionRequest":
        if tool.startswith("mcp__"):
            tool = " ".join(part for part in tool.split("__")[1:] if part)
        return f"allow {tool or 'tool'}?"
    if tool == "AskUserQuestion":
        return "has a question"
    if name == "Notification":
        return "needs permission"
    return ""


def main() -> None:
    from session_store import STATE_DIR, remove_session, write_state

    event = json.loads(sys.stdin.read() or "{}")
    if not isinstance(event, dict) or not event.get("session_id"):
        return
    state = classify(event)
    if state is None:
        return
    state_dir = Path(os.environ.get("SMALLTV_STATE_DIR") or STATE_DIR)
    session = str(event["session_id"])
    if state is END:
        remove_session("claude", session, state_dir=state_dir)
    else:
        detail = waiting_detail(event) if state == "waiting" else ""
        write_state("claude", session, state, str(event.get("cwd") or ""), state_dir=state_dir, detail=detail)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
