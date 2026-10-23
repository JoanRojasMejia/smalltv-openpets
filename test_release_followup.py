import tempfile
import unittest
from pathlib import Path

from release_followup import ReleaseFollowup, is_prod, ticket_keys


def ex(pipeline="cloudfront-shop-PROD-Pipeline", summary="[ABC-41] feat(x): thing\n\nSquashed commit\nAuthor: J <j@x.com>",
       status="Succeeded"):
    return {"pipeline": pipeline, "summary": summary, "status": status, "updated": 1.0}


class HelpersTest(unittest.TestCase):
    def test_ticket_keys(self):
        self.assertEqual(ticket_keys("[ABC-41] feat: x\n\n* [ABC-41] more\n* [XYZ-7] fix\nISO-8601 date"),
                         ["ABC-41", "XYZ-7"])
        self.assertEqual(ticket_keys("chore: no ticket"), [])

    def test_is_prod(self):
        self.assertTrue(is_prod("cloudfront-shop-PROD-Pipeline"))
        self.assertTrue(is_prod("admin-PROD-Pipeline"))
        self.assertFalse(is_prod("cloudfront-shop-TEST-PROD-Pipeline"))
        self.assertFalse(is_prod("cloudfront-shop-CI5-Pipeline"))


class FakeJira:
    def __init__(self, open_tickets):
        self.open = dict(open_tickets)  # key -> summary of my not-done tickets
        self.calls = []

    def __call__(self, keys):
        self.calls.append(list(keys))
        return [{"key": k, "summary": self.open[k]} for k in keys if k in self.open]


class ReleaseFollowupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "pending.json"
        self.jira = FakeJira({"ABC-41": "Rename documents"})
        self.f = ReleaseFollowup(self.jira, self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prod_deploy_of_my_open_ticket_reminds_once(self):
        new = self.f.on_deploys([ex()])
        self.assertEqual(new, [{"key": "ABC-41", "summary": "Rename documents", "env": "SHOP-PROD"}])
        self.assertEqual(self.f.on_deploys([ex()]), [])
        self.assertEqual([p["key"] for p in self.f.pending()], ["ABC-41"])

    def test_ignores_non_prod_failed_and_tickets_not_mine_or_done(self):
        self.assertEqual(self.f.on_deploys([ex(pipeline="cloudfront-shop-CI5-Pipeline"), ex(status="Failed"),
                                            ex(summary="[XYZ-7] someone else's")]), [])
        self.assertEqual(self.jira.calls, [["XYZ-7"]])

    def test_refresh_drops_tickets_moved_to_done_and_persists(self):
        self.f.on_deploys([ex()])
        again = ReleaseFollowup(self.jira, self.state)
        self.assertEqual([p["key"] for p in again.pending()], ["ABC-41"])
        self.jira.open.clear()
        again.refresh()
        self.assertEqual(again.pending(), [])
        self.assertEqual(ReleaseFollowup(self.jira, self.state).pending(), [])

    def test_jira_errors_keep_the_pending_list(self):
        self.f.on_deploys([ex()])

        def broken(keys):
            raise OSError("jira down")

        self.f.lookup = broken
        self.f.refresh()
        self.assertEqual([p["key"] for p in self.f.pending()], ["ABC-41"])


if __name__ == "__main__":
    unittest.main()
