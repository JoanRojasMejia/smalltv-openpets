import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hooks.claude_hook import END, classify, waiting_detail
from session_store import aggregate, prune, read_sessions, remove_session, waiting_summary, write_state

HOOK = Path(__file__).parent / "hooks/claude_hook.py"


def ev(name, **kw):
    return {"hook_event_name": name, "session_id": "s1", "cwd": "/tmp/p", **kw}


class ClassifyTest(unittest.TestCase):
    def test_prompt_is_thinking(self):
        self.assertEqual(classify(ev("UserPromptSubmit")), "thinking")

    def test_edit_tools_are_working(self):
        for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            self.assertEqual(classify(ev("PreToolUse", tool_name=tool)), "working", tool)

    def test_read_tools_are_thinking(self):
        for tool in ("Read", "Grep", "Glob", "WebFetch", "WebSearch"):
            self.assertEqual(classify(ev("PreToolUse", tool_name=tool)), "thinking", tool)

    def test_bash_tests_are_testing(self):
        for cmd in ("pytest -q", "npm test", "python3 -m unittest test_x", "go test ./...", "pnpm run test", "bun test"):
            self.assertEqual(classify(ev("PreToolUse", tool_name="Bash", tool_input={"command": cmd})), "testing", cmd)

    def test_other_bash_is_working(self):
        self.assertEqual(classify(ev("PreToolUse", tool_name="Bash", tool_input={"command": "ls latest"})), "working")

    def test_permission_and_question_are_waiting(self):
        self.assertEqual(classify(ev("PermissionRequest", tool_name="Bash")), "waiting")
        self.assertEqual(classify(ev("PreToolUse", tool_name="AskUserQuestion")), "waiting")

    def test_permission_notification_is_waiting(self):
        self.assertEqual(classify(ev("Notification", message="Claude needs your permission to use Bash")), "waiting")
        self.assertEqual(classify(ev("Notification", notification_type="permission_prompt", message="")), "waiting")

    def test_idle_prompt_clears_to_idle(self):
        # Stop does not fire on Esc; the idle prompt is the clearing signal. It must not
        # bring "done" back a minute after it expired.
        self.assertEqual(classify(ev("Notification", message="Claude is waiting for your input")), "idle")
        self.assertEqual(classify(ev("Notification", notification_type="idle_prompt", message="")), "idle")

    def test_other_notifications_ignored(self):
        self.assertIsNone(classify(ev("Notification", message="Auth succeeded")))

    def test_non_dict_tool_input_is_working(self):
        self.assertEqual(classify(ev("PreToolUse", tool_name="Bash", tool_input="npm test")), "working")

    def test_post_tool_use_resumes_working(self):
        self.assertEqual(classify(ev("PostToolUse", tool_name="Bash")), "working")

    def test_lifecycle(self):
        self.assertEqual(classify(ev("SessionStart")), "idle")
        self.assertEqual(classify(ev("Stop")), "done")
        self.assertEqual(classify(ev("StopFailure")), "error")
        self.assertIs(classify(ev("SessionEnd")), END)

    def test_unknown_event_ignored(self):
        self.assertIsNone(classify(ev("PreCompact")))


class WaitingDetailTest(unittest.TestCase):
    def test_permission_names_the_tool(self):
        self.assertEqual(waiting_detail(ev("PermissionRequest", tool_name="Bash")), "allow Bash?")
        self.assertEqual(waiting_detail(ev("PermissionRequest", tool_name="mcp__slack__send")), "allow slack send?")

    def test_question_and_notification(self):
        self.assertEqual(waiting_detail(ev("PreToolUse", tool_name="AskUserQuestion")), "has a question")
        self.assertEqual(waiting_detail(ev("Notification", message="Claude needs your permission to use Bash")),
                         "needs permission")

    def test_hook_process_records_detail(self):
        with tempfile.TemporaryDirectory() as d:
            run_hook(json.dumps(ev("PermissionRequest", tool_name="Edit")), d)
            [s] = read_sessions(Path(d))
            self.assertEqual((s["state"], s["detail"], s["cwd"]), ("waiting", "allow Edit?", "/tmp/p"))
            run_hook(json.dumps(ev("PostToolUse", tool_name="Edit")), d)
            self.assertEqual(read_sessions(Path(d))[0]["detail"], "")


class WaitingSummaryTest(unittest.TestCase):
    def s(self, cwd, state="waiting", detail="allow Bash?", ts=0, agent="claude"):
        return {"agent": agent, "state": state, "ts": ts, "cwd": cwd, "detail": detail}

    def test_one_line_per_waiting_session_with_project_name(self):
        sessions = [self.s("/Users/j/front"), self.s("/Users/j/api", detail="has a question", agent="opencode"),
                    self.s("/Users/j/other", state="working")]
        self.assertEqual(waiting_summary(sessions, now=1), ["front - allow Bash?", "api - has a question"])

    def test_stale_waiting_and_missing_fields(self):
        sessions = [self.s("/x/old", ts=-10**6), self.s("", detail="", agent="opencode")]
        self.assertEqual(waiting_summary(sessions, now=1), ["opencode - needs you"])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_read_remove(self):
        write_state("claude", "a/b", "working", "/x", state_dir=self.dir, now=100)
        [s] = read_sessions(self.dir)
        self.assertEqual((s["agent"], s["session"], s["state"], s["ts"], s["cwd"]), ("claude", "a/b", "working", 100, "/x"))
        remove_session("claude", "a/b", state_dir=self.dir)
        self.assertEqual(read_sessions(self.dir), [])

    def test_ignores_corrupt_files(self):
        (self.dir / "bad.json").write_text("{nope")
        (self.dir / "odd.json").write_text(json.dumps({"state": "dancing", "ts": 1}))
        self.assertEqual(read_sessions(self.dir), [])

    def test_aggregate_priority(self):
        sessions = [{"state": "done", "ts": 0}, {"state": "working", "ts": 0}, {"state": "waiting", "ts": 0}]
        self.assertEqual(aggregate(sessions, now=1), "waiting")
        self.assertEqual(aggregate(sessions[:2], now=1), "working")

    def test_done_falls_back_to_idle_after_a_minute(self):
        self.assertEqual(aggregate([{"state": "done", "ts": 0}], now=59), "done")
        self.assertEqual(aggregate([{"state": "done", "ts": 0}], now=60), "idle")

    def test_error_falls_back_to_idle_after_five_minutes(self):
        self.assertEqual(aggregate([{"state": "error", "ts": 0}], now=299), "error")
        self.assertEqual(aggregate([{"state": "error", "ts": 0}], now=300), "idle")

    def test_aggregate_drops_stale_and_defaults_idle(self):
        self.assertEqual(aggregate([{"state": "working", "ts": 0}], now=600), "idle")
        self.assertEqual(aggregate([{"state": "working", "ts": 0}, {"state": "done", "ts": 590}], now=600), "done")
        self.assertEqual(aggregate([], now=0), "idle")

    def test_prune_old_files(self):
        write_state("claude", "old", "done", state_dir=self.dir)
        prune(self.dir, now=10**12)
        self.assertEqual(read_sessions(self.dir), [])


class SessionWatcherTest(unittest.TestCase):
    def test_reports_only_changes_and_prioritizes_waiting(self):
        from bridge import SessionWatcher

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            w = SessionWatcher(d)
            self.assertEqual(w.poll(now_epoch=100), "idle")
            write_state("claude", "a", "working", state_dir=d, now=100)
            self.assertEqual(w.poll(now_epoch=101), "working")
            self.assertIsNone(w.poll(now_epoch=102))
            write_state("opencode", "b", "waiting", state_dir=d, now=103)
            self.assertEqual(w.poll(now_epoch=104), "waiting")
            remove_session("opencode", "b", state_dir=d)
            self.assertEqual(w.poll(now_epoch=105), "working")


class WaitingDetailFlowTest(unittest.TestCase):
    def make(self, ok=True):
        from bridge import WaitingDetail

        self.jobs = []
        self.later = []
        detail = WaitingDetail(lambda lines: self.jobs.append(lines) or ok,
                               start=lambda target, arg: self.later.append((target, arg)))
        return detail

    def run_jobs(self):
        while self.later:
            target, arg = self.later.pop(0)
            target(arg)

    def test_renders_once_and_signals_once(self):
        d = self.make()
        lines = ["front - allow Bash?"]
        self.assertFalse(d.poll(lines))  # job started in background
        self.assertFalse(d.poll(lines))  # still running: no second job
        self.run_jobs()
        self.assertEqual(self.jobs, [lines])
        self.assertTrue(d.ready_for(lines))
        self.assertTrue(d.poll(lines))
        self.assertFalse(d.poll(lines))

    def test_new_set_of_waiting_sessions_renders_again(self):
        d = self.make()
        d.poll(["a - x"]); self.run_jobs(); d.poll(["a - x"])
        self.assertFalse(d.poll(["a - x", "b - y"]))
        self.run_jobs()
        self.assertEqual(self.jobs[-1], ["a - x", "b - y"])
        self.assertFalse(d.ready_for(["a - x"]))

    def test_not_waiting_starts_nothing_and_failed_job_is_not_ready(self):
        d = self.make(ok=False)
        self.assertFalse(d.poll(None))
        self.assertEqual(self.later, [])
        d.poll(["a - x"]); self.run_jobs()
        self.assertFalse(d.ready_for(["a - x"]))

    def test_invalidate_forces_a_new_render(self):
        d = self.make()
        d.poll(["a - x"]); self.run_jobs(); d.poll(["a - x"])
        d.invalidate()
        self.assertFalse(d.ready_for(["a - x"]))
        d.poll(["a - x"]); self.run_jobs()
        self.assertEqual(len(self.jobs), 2)


def run_hook(payload, state_dir):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env={"SMALLTV_STATE_DIR": str(state_dir), "PATH": "/usr/bin:/bin"},
        timeout=10,
    )


class HookProcessTest(unittest.TestCase):
    def run_hook(self, payload, state_dir):
        return run_hook(payload, state_dir)

    def test_writes_state_and_ends_session(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_hook(json.dumps(ev("UserPromptSubmit")), d)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            self.assertEqual(read_sessions(Path(d))[0]["state"], "thinking")
            self.run_hook(json.dumps(ev("SessionEnd")), d)
            self.assertEqual(read_sessions(Path(d)), [])

    def test_bad_input_never_fails(self):
        with tempfile.TemporaryDirectory() as d:
            for payload in ("", "not json", "[]", json.dumps({"hook_event_name": "Stop"})):
                r = self.run_hook(payload, d)
                self.assertEqual((r.returncode, r.stdout), (0, ""), payload)


if __name__ == "__main__":
    unittest.main()
