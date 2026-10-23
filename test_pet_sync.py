import json
import tempfile
import unittest
from pathlib import Path

from unittest import mock

import pet_sync
from pet_sync import PetSyncer, active_pet
from states import STATES


class FakeProc:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.proc = FakeProc()

    def __call__(self, args):
        self.calls.append(args)
        return self.proc


class PetSyncerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.state, self.stamp = d / "state.json", d / ".pet"
        self.runner = FakeRunner()
        self.syncer = PetSyncer("h", self.state, self.stamp, check_every=5, runner=self.runner, log=lambda m: None)

    def tearDown(self):
        self.tmp.cleanup()

    def set_pet(self, pet):
        self.state.write_text(json.dumps({"preferences": {"defaultPetId": pet}}))

    def test_active_pet_reads_preferences(self):
        self.set_pet("bender")
        self.assertEqual(active_pet(self.state), "bender")
        self.state.write_text("{broken")
        self.assertIsNone(active_pet(self.state))
        self.assertIsNone(active_pet(self.state.with_name("missing.json")))

    def test_no_sync_when_screen_already_has_the_pet(self):
        self.set_pet("bender")
        self.stamp.write_text("bender\n")
        self.syncer.poll(0)
        self.assertEqual(self.runner.calls, [])

    def test_starts_sync_on_change_and_reports_success_once(self):
        self.set_pet("bender")
        self.stamp.write_text("miss-minute\n")
        self.assertFalse(self.syncer.poll(0, "working"))
        self.assertEqual(self.runner.calls[0][-4:], ["--pet", "bender", "--first", "working"])
        self.assertFalse(self.syncer.poll(1))  # still running
        self.runner.proc.code = 0
        self.assertTrue(self.syncer.poll(2))
        self.assertFalse(self.syncer.poll(3))

    def test_checks_are_rate_limited(self):
        self.set_pet("bender")
        self.stamp.write_text("bender\n")
        self.syncer.poll(0)
        self.set_pet("finn")
        self.syncer.poll(1)
        self.assertEqual(self.runner.calls, [])
        self.syncer.poll(5)
        self.assertEqual(len(self.runner.calls), 1)

    def test_failed_sync_is_not_retried_immediately(self):
        self.set_pet("bender")
        self.syncer.poll(0)
        self.runner.proc.code = 1
        self.assertFalse(self.syncer.poll(1))
        self.runner.proc = FakeProc()
        self.syncer.poll(10)
        self.assertEqual(len(self.runner.calls), 1)
        self.syncer.poll(700)
        self.assertEqual(len(self.runner.calls), 2)

    def test_without_a_known_current_state_no_first_flag(self):
        self.set_pet("bender")
        self.syncer.poll(0, None)
        self.assertNotIn("--first", self.runner.calls[0])

    def test_unreadable_state_does_nothing(self):
        self.syncer.poll(0)
        self.assertEqual(self.runner.calls, [])


class SyncOrderTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.tmp = tempfile.TemporaryDirectory()
        self.stamp_patch = mock.patch.object(pet_sync, "STAMP", Path(self.tmp.name) / ".pet")
        self.stamp_patch.start()

    def tearDown(self):
        self.stamp_patch.stop()
        self.tmp.cleanup()

    def run_sync(self, first):
        pet_sync.sync(
            "h", "bender", first_state=first,
            render=lambda pet, states: self.events.append(("render", tuple(states))),
            upload=lambda host, f: self.events.append(("upload", f.name)),
            http_get=lambda host, path: self.events.append(("get", path)),
        )

    def test_visible_state_is_uploaded_and_shown_before_the_rest(self):
        self.run_sync("working")
        self.assertEqual(self.events[:3], [
            ("render", ("working",)),
            ("upload", "op-working.gif"),
            ("get", "/set?img=%2Fimage%2Fop-working.gif"),
        ])
        rest = [s for s in STATES if s != "working"]
        self.assertEqual(self.events[3], ("render", tuple(rest)))
        self.assertEqual([e[1] for e in self.events[4:]], [f"op-{s}.gif" for s in rest])
        self.assertEqual(pet_sync.STAMP.read_text().strip(), "bender")

    def test_without_first_state_uploads_everything_in_order(self):
        self.run_sync(None)
        self.assertEqual(self.events[0], ("render", tuple(STATES)))
        self.assertNotIn("get", [e[0] for e in self.events])
        self.assertEqual(len(self.events), 1 + len(STATES))


if __name__ == "__main__":
    unittest.main()
