"""One-time SmallTV setup: upload the state GIFs and pin the photo album.

Without --apply it only prints what it would do.

Usage: python3 setup_device.py --host <screen-ip> [--apply]
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import time
import urllib.request
import uuid
from pathlib import Path

from states import STATES

GIF_DIR = Path(__file__).parent / "gifs"
FREE_MARGIN = 100 * 1024


def get(host: str, path: str, attempts: int = 3) -> str:
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(f"http://{host}{path}", timeout=10) as resp:
                return resp.read().decode(errors="replace").strip()
        except http.client.IncompleteRead:
            if attempt == attempts - 1:
                raise
            time.sleep(1)  # the ESP8266 sometimes cuts replies short while busy
    raise AssertionError("unreachable")


def upload(host: str, file: Path) -> None:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{file.name}"\r\n'
        "Content-Type: image/gif\r\n\r\n"
    ).encode() + file.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"http://{host}/doUpload?dir=/image/",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        try:
            resp.read()
        except http.client.IncompleteRead:
            pass  # the ESP8266 often cuts the reply short; listed_sizes() verifies the upload


def listed_sizes(host: str) -> dict[str, int]:
    """File name -> size in KB as reported by the screen's /image/ listing."""
    listing = get(host, "/filelist?dir=/image/")
    return {name: int(kb) for name, kb in re.findall(r"/image/([^']+)'>[^<]*</a></td><td>(\d+)<", listing)}


def upload_verified(host: str, file: Path, attempts: int = 3) -> None:
    expected_kb = file.stat().st_size // 1024
    for _ in range(attempts):
        upload(host, file)
        time.sleep(1)
        if abs(listed_sizes(host).get(file.name, -10) - expected_kb) <= 1:
            print(f"uploaded {file.name} ({expected_kb} KB)")
            return
    raise SystemExit(f"upload of {file.name} did not land after {attempts} attempts")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", required=True)
    parser.add_argument("--apply", action="store_true", help="actually write to the screen")
    parser.add_argument("--replace", action="store_true", help="re-upload GIFs already on the screen")
    args = parser.parse_args()

    files = [GIF_DIR / f"op-{state}.gif" for state in STATES]
    missing = [f.name for f in files if not f.exists()]
    if missing:
        raise SystemExit(f"missing GIFs (run make_gifs.py first): {', '.join(missing)}")

    listing = get(args.host, "/filelist?dir=/image/")
    to_upload = files if args.replace else [f for f in files if f"/image/{f.name}'" not in listing]
    need = sum(f.stat().st_size for f in to_upload)
    free = json.loads(get(args.host, "/space.json"))["free"]
    print(f"free {free // 1024} KB, need {need // 1024} KB for {len(to_upload)} GIF(s)")
    if need + FREE_MARGIN > free:
        raise SystemExit("not enough space on the screen; delete some album files first")

    steps = [f"upload {f.name}" for f in to_upload]
    steps += ["GET /set?i_i=30&autoplay=0  (stop album rotation)", "GET /set?theme=3  (photo album theme)"]
    for step in steps:
        print(("apply: " if args.apply else "plan:  ") + step)
    if not args.apply:
        print("dry run only; pass --apply to write to the screen")
        return

    for f in to_upload:
        upload_verified(args.host, f)
    print("set autoplay off:", get(args.host, "/set?i_i=30&autoplay=0"))
    print("set theme album:", get(args.host, "/set?theme=3"))


if __name__ == "__main__":
    main()
