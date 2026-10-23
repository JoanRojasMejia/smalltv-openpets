"""Keep the screen's GIFs in sync with the pet (and outfit) selected in OpenPets.

`python3 pet_sync.py --host <screen-ip> --pet flutter --outfit halloween` renders the GIFs,
uploads them and records "pet:outfit" in gifs/.pet. The bridge runs it automatically when the
OpenPets default pet or its worn outfit changes (see PetSyncer). The worn outfit follows
OpenPets' own rules (resolve_outfit): a manual choice wins, otherwise the dated schedule.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path

import setup_device
from states import STATES, device_path

ROOT = Path(__file__).resolve().parent
GIF_DIR = ROOT / "gifs"
STAMP = GIF_DIR / ".pet"
OPENPETS_STATE = Path.home() / "Library/Application Support/@open-pets/desktop/openpets-state.json"
PETS_DIR = Path.home() / "Library/Application Support/@open-pets/desktop/pets"
RETRY_FAILED_AFTER = 600
DEFAULT_OUTFIT = "default"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # datetime.weekday() order


def active_pet(state_file: Path = OPENPETS_STATE) -> str | None:
    """The OpenPets default pet id, or None if it cannot be read."""
    try:
        pet = json.loads(state_file.read_text())["preferences"]["defaultPetId"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return pet if isinstance(pet, str) and pet else None


def _month_day(value) -> int | None:
    try:
        month, day = (int(part) for part in str(value).split("-"))
        datetime(2000, month, day)  # leap year: accepts 02-29
    except (ValueError, TypeError):
        return None
    return month * 100 + day


def _minutes(value) -> int | None:
    try:
        hours, minutes = (int(part) for part in str(value).split(":"))
    except (ValueError, TypeError):
        return None
    return hours * 60 + minutes if 0 <= hours < 24 and 0 <= minutes < 60 else None


def _matches(when: dict, now: datetime) -> bool:
    """Port of OpenPets' matchesOutfitScheduleWhen (days, time and date ranges may wrap)."""
    if when.get("days") and WEEKDAYS[now.weekday()] not in when["days"]:
        return False
    if "time" in when:
        start, end = _minutes(when["time"].get("from")), _minutes(when["time"].get("to"))
        minute = now.hour * 60 + now.minute
        if start is None or end is None:
            return False
        inside = start <= minute < end if start < end else minute >= start or minute < end
        if not inside:
            return False
    if "dates" in when:
        start, end = _month_day(when["dates"].get("from")), _month_day(when["dates"].get("to"))
        today = now.month * 100 + now.day
        if start is None or end is None:
            return False
        inside = start <= today <= end if start <= end else today >= start or today <= end
        if not inside:
            return False
    return True


def resolve_outfit(wardrobe: dict | None, preference: dict | None, now: datetime) -> str:
    """Port of OpenPets' resolveWornOutfitId: manual choice, else first matching schedule rule."""
    if not wardrobe:
        return DEFAULT_OUTFIT
    known = {DEFAULT_OUTFIT} | {o.get("id") for o in wardrobe.get("outfits", [])}
    preference = preference or {}
    if preference.get("outfit") in known:
        return preference["outfit"]
    for rule in preference.get("schedule") or wardrobe.get("schedule") or []:
        if rule.get("enabled") is not False and rule.get("outfit") in known and _matches(rule.get("when") or {}, now):
            return rule["outfit"]
    return DEFAULT_OUTFIT


def load_wardrobe(pet: str) -> dict | None:
    try:
        return json.loads((PETS_DIR / pet / "pet.json").read_text()).get("wardrobe")
    except (OSError, ValueError, AttributeError):
        return None


def active_look(state_file: Path = OPENPETS_STATE, wardrobe_of=load_wardrobe, now: datetime | None = None):
    """(pet, outfit) OpenPets shows now, or None."""
    pet = active_pet(state_file)
    if not pet:
        return None
    try:
        prefs = json.loads(state_file.read_text())["preferences"].get("petWardrobePreferences") or {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        prefs = {}
    return pet, resolve_outfit(wardrobe_of(pet), prefs.get(pet), now or datetime.now())


def synced_look(stamp: Path = STAMP) -> tuple[str, str] | None:
    """(pet, outfit) on the screen; old stamps without an outfit mean the default one."""
    try:
        text = stamp.read_text().strip()
    except OSError:
        return None
    if not text:
        return None
    pet, _, outfit = text.partition(":")
    return pet, outfit or DEFAULT_OUTFIT


def synced_pet(stamp: Path = STAMP) -> str | None:
    look = synced_look(stamp)
    return look[0] if look else None


def look_args() -> list[str]:
    """make_gifs.py options for the pet and outfit the screen currently uses."""
    look = synced_look() or active_look() or ("miss-minute", DEFAULT_OUTFIT)
    return ["--pet", look[0], "--outfit", look[1]]


class PetSyncer:
    """Starts pet_sync.py in the background whenever the OpenPets pet differs from the screen's."""

    def __init__(self, host: str, state_file: Path = OPENPETS_STATE, stamp: Path = STAMP,
                 check_every: float = 5, runner=subprocess.Popen, log=print, wardrobe_of=load_wardrobe):
        self.host = host
        self.state_file = state_file
        self.stamp = stamp
        self.check_every = check_every
        self.runner = runner
        self.log = log
        self.wardrobe_of = wardrobe_of
        self.proc = None
        self.pet: str | None = None  # "pet:outfit" being synced
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
        look = active_look(self.state_file, self.wardrobe_of)
        if not look or look == synced_look(self.stamp):
            return False
        key = f"{look[0]}:{look[1]}"
        if self.failed and self.failed[0] == key and now - self.failed[1] < RETRY_FAILED_AFTER:
            return False
        self.pet = key
        self.log(f"pet changed to {key}; rendering and uploading")
        args = [sys.executable, "-u", str(ROOT / "pet_sync.py"), "--host", self.host,
                "--pet", look[0], "--outfit", look[1]]
        if current_state in STATES:
            args += ["--first", current_state]
        self.proc = self.runner(args)
        return False


def uv_binary() -> str:
    return shutil.which("uv") or "/opt/homebrew/bin/uv"


def render_gifs(pet: str, outfit: str, states: list[str]) -> None:
    """Render the given states' GIFs for a pet and outfit into GIF_DIR."""
    with tempfile.TemporaryDirectory(prefix="smalltv-gifs-") as tmp:
        subprocess.run([uv_binary(), "run", "--quiet", "--with", "pillow", "python", str(ROOT / "make_gifs.py"),
                        "--pet", pet, "--outfit", outfit, "--out", tmp, "--only", ",".join(states)],
                       check=True, cwd=ROOT)
        GIF_DIR.mkdir(exist_ok=True)
        for gif in Path(tmp).glob("*.gif"):
            shutil.copy2(gif, GIF_DIR / gif.name)


def sync(host: str, pet: str, first_state: str | None = None, outfit: str = DEFAULT_OUTFIT,
         render=render_gifs, upload=setup_device.upload_verified, http_get=setup_device.get) -> None:
    """Render and upload a pet's GIFs. The state on screen goes first and is shown right away."""
    rest = list(STATES)
    if first_state in STATES:
        render(pet, outfit, [first_state])
        upload(host, GIF_DIR / f"op-{first_state}.gif")
        http_get(host, f"/set?img={urllib.parse.quote(device_path(first_state), safe='')}")
        print(f"showing {pet} ({first_state}) while the other GIFs upload", flush=True)
        rest.remove(first_state)
    render(pet, outfit, rest)
    for state in rest:
        upload(host, GIF_DIR / f"op-{state}.gif")
    STAMP.write_text(f"{pet}:{outfit}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", required=True)
    parser.add_argument("--pet", default=None, help="pet id (default: the OpenPets default pet)")
    parser.add_argument("--outfit", default=None, help="outfit id (default: the one OpenPets shows now)")
    parser.add_argument("--first", default=None, choices=list(STATES),
                        help="state currently on screen: upload and show it before the rest")
    args = parser.parse_args()
    look = active_look()
    pet = args.pet or (look[0] if look else None)
    if not pet:
        raise SystemExit("could not determine the OpenPets pet")
    outfit = args.outfit or (look[1] if look and look[0] == pet else DEFAULT_OUTFIT)
    try:
        sync(args.host, pet, first_state=args.first, outfit=outfit)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise SystemExit(f"pet sync failed: {exc}")
    print(f"screen now shows {pet} ({outfit})")


if __name__ == "__main__":
    main()
