"""Personal settings kept outside the repository.

~/.config/smalltv-openpets/config.json (see config.example.json):
  host               the screen's IP address (or --host / SMALLTV_HOST)
  calcifer_user      Slack user id of the PR review bot whose verdicts get a special card
  calcifer_channel   Slack channel id where that bot answers in review threads
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "smalltv-openpets/config.json"


def load(path: Path = CONFIG_FILE) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
