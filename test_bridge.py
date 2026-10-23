import os
import tempfile
import unittest

from bridge import Display, LogTailer, StateMachine, parse_reaction, recent_reaction

REACT = '2026-10-22T20:26:16.864Z DEBUG ipc          pet react requested requestId="abc" reaction="thinking"'
SAY = '2026-10-22T20:01:44.916Z DEBUG ipc          pet say requested requestId="def" reaction="waiting" messageLength=15'
HEARTBEAT = '2026-10-22T20:29:04.561Z DEBUG ipc          lease heartbeat requested leaseId="977"'


class ParseReactionTest(unittest.TestCase):
    def test_react_line(self):
        self.assertEqual(parse_reaction(REACT), "thinking")

    def test_say_line(self):
        self.assertEqual(parse_reaction(SAY), "waiting")

    def test_unrelated_line(self):
        self.assertIsNone(parse_reaction(HEARTBEAT))
        self.assertIsNone(parse_reaction('DEBUG pet.window default content render begin reaction="thinking"'))


class StateMachineTest(unittest.TestCase):
    def test_maps_reactions_to_states(self):
        sm = StateMachine()
        self.assertEqual(sm.on_reaction("editing", now=0), "working")
        self.assertEqual(sm.on_reaction("success", now=1), "done")
        self.assertEqual(sm.on_reaction("waiting", now=2), "waiting")

    def test_same_state_is_not_reported_again(self):
        sm = StateMachine()
        sm.on_reaction("editing", now=0)
        self.assertIsNone(sm.on_reaction("running", now=1))

    def test_unknown_reaction_is_ignored(self):
        sm = StateMachine()
        self.assertIsNone(sm.on_reaction("dancing", now=0))
        self.assertEqual(sm.state, "idle")

    def test_active_state_goes_idle_when_stale(self):
        sm = StateMachine(stale_after={"working": 600})
        sm.on_reaction("editing", now=0)
        self.assertIsNone(sm.tick(now=599))
        self.assertEqual(sm.tick(now=600), "idle")

    def test_waiting_never_goes_stale(self):
        sm = StateMachine()
        sm.on_reaction("waiting", now=0)
        self.assertIsNone(sm.tick(now=10**6))
        self.assertEqual(sm.state, "waiting")

    def test_new_reaction_resets_stale_timer(self):
        sm = StateMachine(stale_after={"working": 600})
        sm.on_reaction("editing", now=0)
        sm.on_reaction("editing", now=500)
        self.assertIsNone(sm.tick(now=900))


class FakeHttp:
    def __init__(self, fail_times=0):
        self.calls = []
        self.fail_times = fail_times

    def __call__(self, url):
        self.calls.append(url)
        if self.fail_times:
            self.fail_times -= 1
            raise OSError("unreachable")
        return "OK"


class DisplayTest(unittest.TestCase):
    def test_pushes_state_gif(self):
        http = FakeHttp()
        d = Display("10.0.0.5", http=http, min_interval=0)
        d.show("done", now=0)
        d.flush(now=0)
        self.assertEqual(http.calls, ["http://10.0.0.5/set?img=%2Fimage%2Fop-done.gif"])

    def test_coalesces_bursts_to_latest_state(self):
        http = FakeHttp()
        d = Display("h", http=http, min_interval=2)
        d.show("thinking", now=0)
        d.flush(now=0)
        d.show("working", now=0.5)
        d.show("done", now=1)
        d.flush(now=1)
        d.flush(now=2)
        self.assertEqual(len(http.calls), 2)
        self.assertTrue(http.calls[-1].endswith("op-done.gif"))

    def test_retries_after_failure(self):
        http = FakeHttp(fail_times=1)
        d = Display("h", http=http, min_interval=0, retry_after=5)
        d.show("error", now=0)
        d.flush(now=0)
        d.flush(now=1)
        self.assertEqual(len(http.calls), 1)
        d.flush(now=5)
        self.assertEqual(len(http.calls), 2)
        self.assertIsNone(d.pending)


class LogTailerTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.close(fd)

    def tearDown(self):
        os.unlink(self.path)

    def append(self, text):
        with open(self.path, "a") as f:
            f.write(text)

    def test_starts_at_end_and_reads_new_lines(self):
        self.append("old line\n")
        t = LogTailer(self.path)
        self.assertEqual(t.read_lines(), [])
        self.append("new 1\nnew 2\n")
        self.assertEqual(t.read_lines(), ["new 1", "new 2"])

    def test_holds_partial_line_until_complete(self):
        t = LogTailer(self.path)
        self.append("half")
        self.assertEqual(t.read_lines(), [])
        self.append(" done\n")
        self.assertEqual(t.read_lines(), ["half done"])

    def test_recent_reaction_returns_last_one_with_age(self):
        self.append(REACT + "\n" + HEARTBEAT + "\n" + SAY.replace("20:01:44.916Z", "20:30:00.000Z") + "\n")
        # 2026-10-22T20:31:00Z
        self.assertEqual(recent_reaction(self.path, now_epoch=1792701060, max_age=600), ("waiting", 60.0))

    def test_recent_reaction_ignores_old_events(self):
        self.append(REACT + "\n")
        self.assertIsNone(recent_reaction(self.path, now_epoch=1792701060, max_age=60))

    def test_recent_reaction_missing_file(self):
        self.assertIsNone(recent_reaction(self.path + ".nope", now_epoch=0, max_age=600))

    def test_follows_rotation(self):
        self.append("a long line before rotation\n")
        t = LogTailer(self.path)
        os.unlink(self.path)
        self.append("fresh\n")
        self.assertEqual(t.read_lines(), ["fresh"])


if __name__ == "__main__":
    unittest.main()
