import unittest

from pipeline_source import PipelineWatcher, SsoExpired, commit_subject, deploy_card, env_label

ME = "joan@example.com"
P = {"name": "cloudfront-shop-CI5-Pipeline", "repo": "web-app", "branch": "joan/x"}


def ex(id, status="Succeeded", author=ME, summary="[ABC-39] feat: shareholders\n\nbody", pipeline=P["name"]):
    return {"pipeline": pipeline, "repo": "web-app", "branch": "joan/x", "id": id, "status": status,
            "start": 1.0, "updated": 2.0, "commit": "abc", "summary": summary, "author": author}


class FakeTool:
    def __init__(self):
        self.executions = []
        self.requests = []
        self.sso = False

    def __call__(self, pipelines, known):
        self.requests.append(set(known))
        if self.sso:
            raise SsoExpired()
        return list(self.executions)


class PipelineWatcherTest(unittest.TestCase):
    def setUp(self):
        self.tool = FakeTool()
        self.w = PipelineWatcher(self.tool, [P], ME)

    def test_first_poll_is_a_baseline(self):
        self.tool.executions = [ex("old")]
        self.assertEqual(self.w.poll(), [])
        self.assertEqual(self.w.poll(), [])

    def test_reports_my_finished_deploys_once(self):
        self.w.poll()
        self.tool.executions = [ex("e1"), ex("e2", status="Failed")]
        events = self.w.poll()
        self.assertEqual([(e["id"], e["status"], e["phase"]) for e in events],
                         [("e1", "Succeeded", "finished"), ("e2", "Failed", "finished")])
        self.assertEqual(self.w.poll(), [])
        self.assertIn("e1", self.tool.requests[-1])  # known ids are passed so authors are not refetched

    def test_mine_by_squash_summary_and_case_insensitive_author(self):
        self.w.poll()
        squash = "[ABC-96] feat: x\n\nSquashed commit of the following\n\ncommit b4\nAuthor: Joan <JOAN@example.com>"
        self.tool.executions = [ex("s", author="merger@example.com", summary=squash), ex("u", author="Joan@Example.com")]
        self.assertEqual({e["id"] for e in self.w.poll()}, {"s", "u"})

    def test_ignores_other_people_and_superseded(self):
        self.w.poll()
        self.tool.executions = [ex("o", author="ana@example.com"), ex("or", author="ana@example.com", status="InProgress"),
                                ex("x", status="Superseded"), ex("t", status="Stopped")]
        self.assertEqual(self.w.poll(), [])
        self.assertFalse(self.w.running_mine)

    def test_my_deploy_is_announced_when_it_starts_and_when_it_finishes(self):
        self.w.poll()
        self.tool.executions = [ex("r", status="InProgress")]
        self.assertEqual([(e["id"], e["phase"]) for e in self.w.poll()], [("r", "started")])
        self.assertTrue(self.w.running_mine)
        self.assertEqual(self.w.poll(), [])  # still running: announced once
        self.tool.executions = [ex("r", status="Succeeded")]
        self.assertEqual([(e["id"], e["phase"]) for e in self.w.poll()], [("r", "finished")])
        self.assertFalse(self.w.running_mine)

    def test_deploy_already_running_at_baseline_is_only_reported_when_it_finishes(self):
        self.tool.executions = [ex("r", status="InProgress")]
        self.assertEqual(self.w.poll(), [])
        self.tool.executions = [ex("r", status="Failed")]
        self.assertEqual([e["phase"] for e in self.w.poll()], ["finished"])

    def test_any_of_my_emails_counts(self):
        w = PipelineWatcher(self.tool, [P], ["joan@work.com", "Joan@Example.com"])
        w.poll()
        self.tool.executions = [ex("a", author="joan@work.com"), ex("b", author="joan@example.com"),
                                ex("c", author="x@y.com", summary="Squashed\nAuthor: Joan <JOAN@WORK.COM>"),
                                ex("d", author="x@y.com", summary="nothing")]
        self.assertEqual([e["id"] for e in w.poll()], ["a", "b", "c"])

    def test_sso_expiry_propagates(self):
        self.tool.sso = True
        with self.assertRaises(SsoExpired):
            self.w.poll()


class CardTest(unittest.TestCase):
    def test_env_label(self):
        self.assertEqual(env_label("cloudfront-shop-CI5-Pipeline"), "SHOP-CI5")
        self.assertEqual(env_label("admin-PROD-Pipeline"), "ADMIN-PROD")

    def test_cards(self):
        self.assertEqual(deploy_card(ex("e")), ("DEPLOYED", "SHOP-CI5: ABC-39 shareholders", "ok"))
        self.assertEqual(deploy_card(ex("e", status="Failed"))[0::2], ("DEPLOY FAILED", "failed"))
        self.assertEqual(deploy_card(dict(ex("e", status="InProgress"), phase="started"))[0::2],
                         ("DEPLOYING...", "running"))


class SubjectTest(unittest.TestCase):
    def test_commit_subjects(self):
        self.assertEqual(commit_subject("[ABC-96] feat(billing): vista modal\n\nbody"), "ABC-96 vista modal")
        self.assertEqual(commit_subject("Merge branch 'x' into y\n"), "Merge branch 'x' into y")
        self.assertEqual(commit_subject("fix!: hotfix"), "hotfix")
        self.assertEqual(commit_subject("", "joan/feature"), "joan/feature")


class LastMineTest(unittest.TestCase):
    def test_last_finished_deploy_of_mine_includes_the_baseline(self):
        tool = FakeTool()
        w = PipelineWatcher(tool, [P], ME)
        old = dict(ex("a"), updated=5.0)
        tool.executions = [old, dict(ex("o", author="ana@example.com"), updated=9.0)]
        w.poll()
        self.assertEqual(w.last_mine["id"], "a")
        tool.executions = [old, dict(ex("b", status="Failed"), updated=7.0), dict(ex("r", status="InProgress"), updated=8.0)]
        w.poll()
        self.assertEqual((w.last_mine["id"], w.last_mine["status"]), ("b", "Failed"))


if __name__ == "__main__":
    unittest.main()
