"""Keep the screen's GIFs in sync with the pet selected in OpenPets.

`python3 pet_sync.py --host <screen-ip> --pet bender` renders the pet's GIFs, uploads them
and records the pet in gifs/.pet. The bridge runs it automatically when the OpenPets
default pet changes (see PetSyncer).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

import setup_device
from states import STATES, device_path

ROOT = Path(__file__).resolve().parent
GIF_DIR = ROOT / "gifs"
STAMP = GIF_DIR / ".pet"
OPENPETS_STATE = Path.home() / "Library/Application Support/@open-pets/desktop/openpets-state.json"
RETRY_FAILED_AFTER = 600


def active_pet(state_file: Path = OPENPETS_STATE) -> str | None:
    """The OpenPets default pet id, or None if it cannot be read."""
    try:
        pet = json.loads(state_file.read_text())["preferences"]["defaultPetId"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return pet if isinstance(pet, str) and pet else None


def synced_pet(stamp: Path = STAMP) -> str | None:
    try:
        return stamp.read_text().strip() or None
    except OSError:
        return None


class PetSyncer:
    """Starts pet_sync.py in the background whenever the OpenPets pet differs from the screen's."""

    def __init__(self, host: str, state_file: Path = OPENPETS_STATE, stamp: Path = STAMP,
                 check_every: float = 5, runner=subprocess.Popen, log=print):
        self.host = host
        self.state_file = state_file
        self.stamp = stamp
        self.check_every = check_every
        self.runner = runner
        self.log = log
        self.proc = None
        self.pet: str | None = None
        self.failed: tuple[str, float] | None = None
        self.next_check = 0.0

    def poll(self, now: float, current_state: str | None = None) -> bool:
        """Advance the sync; True right after a sync finished successfully.

        current_state is what the screen shows now; it is uploaded and shown first.
        """
        if self.proc is not None:
            code = self.proc.poll()
            if code is None:
                return False
            self.proc = None
            if code == 0:
                self.log(f"pet synced: {self.pet}")
                return True
            self.log(f"pet sync failed for {self.pet} (exit {code}); retrying in {RETRY_FAILED_AFTER}s")
            self.failed = (self.pet, now)
            return False
        if now < self.next_check:
            return False
        self.next_check = now + self.check_every
        pet = active_pet(self.state_file)
        if not pet or pet == synced_pet(self.stamp):
            return False
        if self.failed and self.failed[0] == pet and now - self.failed[1] < RETRY_FAILED_AFTER:
            return False
        self.pet = pet
        self.log(f"pet changed to {pet}; rendering and uploading")
        args = [sys.executable, "-u", str(ROOT / "pet_sync.py"), "--host", self.host, "--pet", pet]
        if current_state in STATES:
            args += ["--first", current_state]
        self.proc = self.runner(args)
        return False


def uv_binary() -> str:
    return shutil.which("uv") or "/opt/homebrew/bin/uv"


def render_gifs(pet: str, states: list[str]) -> None:
    """Render the given states' GIFs for a pet into GIF_DIR."""
    with tempfile.TemporaryDirectory(prefix="smalltv-gifs-") as tmp:
        subprocess.run([uv_binary(), "run", "--quiet", "--with", "pillow", "python", str(ROOT / "make_gifs.py"),
                        "--pet", pet, "--out", tmp, "--only", ",".join(states)], check=True, cwd=ROOT)
        GIF_DIR.mkdir(exist_ok=True)
        for gif in Path(tmp).glob("*.gif"):
            shutil.copy2(gif, GIF_DIR / gif.name)


def sync(host: str, pet: str, first_state: str | None = None,
         render=render_gifs, upload=setup_device.upload_verified, http_get=setup_device.get) -> None:
    """Render and upload a pet's GIFs. The state on screen goes first and is shown right away."""
    rest = list(STATES)
    if first_state in STATES:
        render(pet, [first_state])
        upload(host, GIF_DIR / f"op-{first_state}.gif")
        http_get(host, f"/set?img={urllib.parse.quote(device_path(first_state), safe='')}")
        print(f"showing {pet} ({first_state}) while the other GIFs upload", flush=True)
        rest.remove(first_state)
    render(pet, rest)
    for state in rest:
        upload(host, GIF_DIR / f"op-{state}.gif")
    STAMP.write_text(pet + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", required=True)
    parser.add_argument("--pet", default=None, help="pet id (default: the OpenPets default pet)")
    parser.add_argument("--first", default=None, choices=list(STATES),
                        help="state currently on screen: upload and show it before the rest")
    args = parser.parse_args()
    pet = args.pet or active_pet()
    if not pet:
        raise SystemExit("could not determine the OpenPets pet")
    try:
        sync(args.host, pet, first_state=args.first)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise SystemExit(f"pet sync failed: {exc}")
    print(f"screen now shows {pet}")


if __name__ == "__main__":
    main()
