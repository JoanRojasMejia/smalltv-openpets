"""Install (or remove) the agent hooks that feed the SmallTV bridge.

- Claude Code: adds hooks/claude_hook.py to ~/.claude/settings.json for the events it needs,
  next to any existing hooks (a timestamped backup is written first).
- OpenCode: copies hooks/smalltv-openpets.js into ~/.config/opencode/plugins/.

Without --apply it only prints what it would change. Running it twice changes nothing.

Usage: python3 install_hooks.py [--apply] [--uninstall]
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLAUDE_SETTINGS = Path.home() / ".claude/settings.json"
OPENCODE_PLUGIN = Path.home() / ".config/opencode/plugins/smalltv-openpets.js"
HOOK_COMMAND = f"/usr/bin/python3 {ROOT / 'hooks/claude_hook.py'}"
EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
          "Notification", "Stop", "StopFailure", "SessionEnd"]


def is_ours(group: dict) -> bool:
    return any(h.get("command") == HOOK_COMMAND for h in group.get("hooks", []))


def patch_settings(settings: dict, uninstall: bool) -> list[str]:
    hooks = settings.setdefault("hooks", {})
    changes = []
    for event in EVENTS:
        groups = hooks.get(event, [])
        present = any(is_ours(g) for g in groups)
        if uninstall and present:
            hooks[event] = [g for g in groups if not is_ours(g)]
            if not hooks[event]:
                del hooks[event]
            changes.append(f"remove Claude hook on {event}")
        elif not uninstall and not present:
            hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 3}]})
            changes.append(f"add Claude hook on {event}")
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    settings = json.loads(CLAUDE_SETTINGS.read_text())
    changes = patch_settings(settings, args.uninstall)

    plugin_src = ROOT / "hooks/smalltv-openpets.js"
    if args.uninstall and OPENCODE_PLUGIN.exists():
        changes.append(f"delete {OPENCODE_PLUGIN}")
    elif not args.uninstall and (not OPENCODE_PLUGIN.exists() or OPENCODE_PLUGIN.read_bytes() != plugin_src.read_bytes()):
        changes.append(f"copy OpenCode plugin to {OPENCODE_PLUGIN}")

    for change in changes or ["nothing to change"]:
        print(("apply: " if args.apply else "plan:  ") + change)
    if not args.apply or not changes:
        return

    if any("Claude hook" in c for c in changes):
        backup = CLAUDE_SETTINGS.with_name(f"settings.json.smalltv-backup-{time.strftime('%Y%m%dT%H%M%S')}.json")
        shutil.copy2(CLAUDE_SETTINGS, backup)
        tmp = CLAUDE_SETTINGS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
        json.loads(tmp.read_text())
        tmp.replace(CLAUDE_SETTINGS)
        print(f"backup: {backup}")
    if args.uninstall:
        OPENCODE_PLUGIN.unlink(missing_ok=True)
    elif any("OpenCode" in c for c in changes):
        OPENCODE_PLUGIN.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin_src, OPENCODE_PLUGIN)


if __name__ == "__main__":
    main()
