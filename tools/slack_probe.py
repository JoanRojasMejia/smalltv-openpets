"""Check which Slack search queries find your DMs and mentions.

Reads the token from the macOS Keychain (service "smalltv-openpets-slack") and prints only
metadata: your user id, result counts, conversation kinds and ages. It never prints the token
or message text.

Usage: python3 tools/slack_probe.py
"""

import json
import subprocess
import time
import urllib.parse
import urllib.request


def main() -> None:
    token = subprocess.run(["security", "find-generic-password", "-s", "smalltv-openpets-slack", "-w"],
                           capture_output=True, text=True, check=True).stdout.strip()

    def api(method, **params):
        req = urllib.request.Request(f"https://slack.com/api/{method}?" + urllib.parse.urlencode(params),
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)

    me = api("auth.test")
    print("auth.test:", me.get("ok"), "user_id:", me.get("user_id"), me.get("error") or "")
    uid = me["user_id"]
    for query in (f"<@{uid}>", "is:dm", "to:me", f"to:<@{uid}>", "is:dm -from:me"):
        r = api("search.messages", query=query, sort="timestamp", sort_dir="desc", count=5)
        matches = r.get("messages", {}).get("matches", [])
        top = []
        for m in matches:
            ch = m.get("channel", {})
            kind = "im" if ch.get("is_im") else "mpim" if ch.get("is_mpim") else "channel"
            who = "me" if m.get("user") == uid else "other"
            top.append(f"{kind}/{int(time.time() - float(m['ts'])) // 60}min/{who}")
        print(f"search {query!r}: ok={r.get('ok')} {r.get('error') or ''} total={r.get('messages', {}).get('total')} top={top}")
    convs = api("conversations.list", types="im,mpim", limit=200, exclude_archived="true")
    print("im/mpim conversations:", convs.get("ok"), len(convs.get("channels", [])), convs.get("error") or "")


if __name__ == "__main__":
    main()
