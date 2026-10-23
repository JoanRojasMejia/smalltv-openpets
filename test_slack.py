import tempfile
import unittest
from pathlib import Path

from slack_source import SlackPoller, calcifer_query, clean_text, parse_calcifer

CALCIFER_USER, CALCIFER_CHANNEL = "UCALC", "CPRS"
CALCIFER_QUERY = calcifer_query(CALCIFER_USER, CALCIFER_CHANNEL)

ME = "UME"


def match(ts, user="UANA", text="hola", channel="D1", username="ana", thread_ts=None):
    m = {"ts": str(ts), "user": user, "username": username, "text": text, "channel": {"id": channel}}
    if thread_ts:
        m["permalink"] = f"https://x.slack.com/archives/{channel}/p{ts}?thread_ts={thread_ts}&cid={channel}"
    return m


def calcifer_reply(ts, thread_ts, text="<@UHOOK> Este PR quedó *RECHAZADO*. Corregilos.\n\n&gt; *Motivo:* falta un test."):
    return match(ts, user=CALCIFER_USER, text=text, channel=CALCIFER_CHANNEL, username="calcifer", thread_ts=thread_ts)


class FakeSlack:
    def __init__(self):
        self.results = {"to:me": [], f"<@{ME}>": [], CALCIFER_QUERY: []}
        self.threads = {}  # thread_ts -> parent text
        self.calls = []
        self.fail = None
        self.users = {"UANA": "Ana María", "UBOB": "Bob", ME: "Joan", CALCIFER_USER: "Calcifer"}

    def __call__(self, method, **params):
        self.calls.append((method, params))
        if self.fail:
            return {"ok": False, "error": self.fail}
        if method == "auth.test":
            return {"ok": True, "user_id": ME}
        if method == "search.messages":
            matches = sorted(self.results[params["query"]], key=lambda m: -float(m["ts"]))
            return {"ok": True, "messages": {"matches": matches}}
        if method == "conversations.replies":
            text = self.threads.get(params["ts"])
            if text is None:
                return {"ok": False, "error": "thread_not_found"}
            return {"ok": True, "messages": [{"ts": params["ts"], "text": text}]}
        if method == "users.info":
            name = self.users.get(params["user"])
            if not name:
                return {"ok": False, "error": "user_not_found"}
            return {"ok": True, "user": {"name": name.lower(), "profile": {"display_name": "", "real_name": name}}}
        raise AssertionError(method)


class SlackPollerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "slack.json"
        self.api = FakeSlack()
        self.poller = SlackPoller(self.api, state_file=self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_poll_only_sets_a_baseline(self):
        self.api.results["to:me"] = [match(100)]
        self.assertEqual(self.poller.poll(now=200), [])

    def test_reports_new_dms_and_mentions_oldest_first(self):
        self.poller.poll(now=100)
        self.api.results["to:me"] = [match(150, text="dm")]
        self.api.results[f"<@{ME}>"] = [match(120, user="UBOB", text=f"<@{ME}> mira esto", channel="C1")]
        msgs = self.poller.poll(now=200)
        self.assertEqual([(m.sender, m.text) for m in msgs], [("Bob", "@Joan mira esto"), ("Ana María", "dm")])

    def test_ignores_own_messages_old_ones_and_duplicates(self):
        self.poller.poll(now=100)
        dm = match(150)
        self.api.results["to:me"] = [dm, match(160, user=ME), match(90)]
        self.api.results[f"<@{ME}>"] = [dm]  # a DM that also mentions me
        self.assertEqual(len(self.poller.poll(now=200)), 1)
        self.assertEqual(self.poller.poll(now=300), [])

    def test_baseline_survives_restart(self):
        self.poller.poll(now=100)
        self.api.results["to:me"] = [match(150)]
        self.poller.poll(now=200)
        again = SlackPoller(self.api, state_file=self.state)
        self.assertEqual(again.poll(now=300), [])
        self.api.results["to:me"].append(match(250, text="new"))
        self.assertEqual([m.text for m in again.poll(now=400)], ["new"])

    def test_api_errors_return_nothing_and_back_off(self):
        self.poller.poll(now=100)
        self.api.fail = "ratelimited"
        self.assertEqual(self.poller.poll(now=200), [])
        self.assertGreater(self.poller.backoff_until, 200)
        self.assertIn("ratelimited", self.poller.last_error)
        self.api.fail = None
        self.poller.poll(now=400)
        self.assertIsNone(self.poller.last_error)

    def test_user_names_are_cached(self):
        self.poller.poll(now=100)
        self.api.results["to:me"] = [match(150), match(151)]
        self.poller.poll(now=200)
        self.assertEqual(sum(1 for m, _ in self.api.calls if m == "users.info"), 1)


class CalciferTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.api = FakeSlack()
        self.api.threads = {
            "50.1": f"review <@{CALCIFER_USER}|Calcifer> <@{ME}|Joan> <https://x/lists/1> revisá este PR",
            "60.1": f"review <@{CALCIFER_USER}|Calcifer> <@UBOB|Bob> <https://x/lists/2> revisá este PR",
        }
        self.poller = SlackPoller(self.api, state_file=Path(self.tmp.name) / "s.json",
                                  calcifer=(CALCIFER_USER, CALCIFER_CHANNEL))
        self.poller.poll(now=100)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reviews_of_my_prs_become_calcifer_notifications(self):
        self.api.results[CALCIFER_QUERY] = [calcifer_reply(150, "50.1")]
        [msg] = self.poller.poll(now=200)
        self.assertEqual((msg.kind, msg.verdict, msg.text), ("calcifer", "rejected", "falta un test."))
        self.assertEqual(msg.sender, "Calcifer")

    def test_reviews_of_other_peoples_prs_are_ignored(self):
        self.api.results[CALCIFER_QUERY] = [calcifer_reply(150, "60.1")]
        self.assertEqual(self.poller.poll(now=200), [])

    def test_thread_owner_is_looked_up_once(self):
        self.api.results[CALCIFER_QUERY] = [calcifer_reply(150, "50.1"), calcifer_reply(160, "50.1")]
        self.assertEqual(len(self.poller.poll(now=200)), 2)
        self.assertEqual(sum(1 for m, _ in self.api.calls if m == "conversations.replies"), 1)

    def test_review_trigger_posts_are_not_mention_notifications(self):
        trigger = match(150, user="UHOOK", channel=CALCIFER_CHANNEL, text=self.api.threads["50.1"])
        self.api.results[f"<@{ME}>"] = [trigger]
        self.assertEqual(self.poller.poll(now=200), [])

    def test_unreadable_thread_is_skipped(self):
        self.api.results[CALCIFER_QUERY] = [calcifer_reply(150, "99.9")]
        self.assertEqual(self.poller.poll(now=200), [])


class ParseCalciferTest(unittest.TestCase):
    def test_verdicts(self):
        cases = {
            "Este PR quedó *Aprobado con sugerencias* -- ya se aprobó": "approved_suggestions",
            "Este PR quedó *Aprobado* sin hallazgos": "approved",
            "Este PR quedó *RECHAZADO*. Si estás de acuerdo": "rejected",
            "Este PR quedó en *Revisión Humana*. Calcifer encontró": "human_review",
            "Por favor, justificalo mejor y volvé a mover la card": None,
        }
        for text, verdict in cases.items():
            self.assertEqual(parse_calcifer(f"<@UHOOK|Enviar PR> {text}")[0], verdict, text)

    def test_reason_prefers_motivo(self):
        text = "<@UHOOK|hook> Este PR quedó *Aprobado*.\n\n&gt; *Motivo:* la validación salió limpia."
        self.assertEqual(parse_calcifer(text), ("approved", "la validación salió limpia."))

    def test_reason_without_motivo_is_the_message_without_the_hook_mention(self):
        _, reason = parse_calcifer("<@UHOOK|Enviar PR> Por favor, justificalo mejor.")
        self.assertEqual(reason, "Por favor, justificalo mejor.")


class CleanTextTest(unittest.TestCase):
    def test_slack_markup(self):
        names = {"U1": "Ana"}.get
        self.assertEqual(clean_text("hey <@U1> mira <https://github.com/x/y/pull/1|el PR>", names), "hey @Ana mira el PR")
        self.assertEqual(clean_text("ver <https://docs.example.com/a/b> ya", names), "ver docs.example.com ya")
        self.assertEqual(clean_text("<!here> en <#C1|frontend> :rocket: &amp; listo", names), "@here en #frontend & listo")
        self.assertEqual(clean_text("<@U9>", names), "@someone")

    def test_empty_text_becomes_attachment_marker(self):
        self.assertEqual(clean_text("", lambda u: None), "[file]")


class OverlayAndSummaryTest(unittest.TestCase):
    def test_overlay_hides_agent_state_then_ends_once(self):
        from bridge import MessageOverlay

        o = MessageOverlay(seconds=20)
        self.assertFalse(o.active(0))
        o.start(100)
        self.assertTrue(o.active(119))
        self.assertFalse(o.just_ended(119))
        self.assertTrue(o.just_ended(120))
        self.assertFalse(o.just_ended(121))
        self.assertFalse(o.active(121))

    def test_summary_uses_newest_and_counts_the_rest(self):
        from slack_notifier import Notification, summarize
        from slack_source import Message

        one = [Message("Ana", "hola", 1, "D1")]
        self.assertEqual(summarize(one), Notification("message", "Ana", "hola"))
        three = one + [Message("Bob", "x", 2, "C1"), Message("Eva", "ultimo", 3, "D2")]
        self.assertEqual(summarize(three), Notification("message", "Eva +2", "ultimo"))

    def test_calcifer_review_wins_over_newer_chat_messages(self):
        from slack_notifier import Notification, summarize
        from slack_source import Message

        batch = [Message("Calcifer", "falta un test", 1, "C", kind="calcifer", verdict="rejected"),
                 Message("Ana", "hola", 2, "D1")]
        self.assertEqual(summarize(batch), Notification("calcifer", "rejected", "falta un test"))
        comment = [Message("Calcifer", "justificalo", 1, "C", kind="calcifer", verdict=None)]
        self.assertEqual(summarize(comment).title, "comment")

    def test_overlay_custom_duration(self):
        from bridge import MessageOverlay

        o = MessageOverlay(seconds=20)
        o.start(0, seconds=45)
        self.assertTrue(o.active(44))
        self.assertFalse(o.active(45))


class CalciferDisabledTest(unittest.TestCase):
    def test_without_config_the_review_search_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            api = FakeSlack()
            SlackPoller(api, state_file=Path(d) / "s.json").poll(now=100)
            queries = [p["query"] for m, p in api.calls if m == "search.messages"]
            self.assertEqual(queries, ["to:me", f"<@{ME}>"])


if __name__ == "__main__":
    unittest.main()
