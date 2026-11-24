import unittest

from unittest.mock import patch

from linyuan.collect_creator_metrics import enrich_watch_time, public_projection, watch_seconds


BV = "BV1wJyazdEtQ"
PUB = [{"bvid": BV, "title": "public title"}]
FIELDS = {"play": 1500, "total_new_attention_cnt": 0, "play_trans_fan_rate": 0,
          "tm_rate": 700, "crash_rate": 3000, "full_play_ratio": 8000,
          "avg_play_time_int": 24, "interact_rate": 200, "unfollow": 12,
          "private": "do not release"}
ROW = {"bvid": BV, "is_only_self": 0, "title": "backend title", "stat": FIELDS,
       "hour_stat": {"play": 50}}


class TestPublicProjection(unittest.TestCase):
    def test_nested_fields_and_allowlist(self):
        s = public_projection([ROW, ROW], PUB, "verified-time")
        self.assertIsNone(s["window"])
        self.assertEqual(len(s["videos"]), 1)
        v = s["videos"][0]
        self.assertEqual(v["title"], "public title")
        self.assertEqual(v["new_follows"], 0)
        self.assertEqual(v["thumbnail_ctr_bps"], 700)
        self.assertIsNone(v["average_watch_seconds"])
        self.assertNotIn("average_watch_time_raw", v)
        self.assertNotIn("unfollow", v)
        self.assertNotIn("private", v)
        self.assertNotIn("hour_stat", v)

    def test_unknown_and_zero_are_different(self):
        s = public_projection([{**ROW, "stat": {"play": 12, "total_new_attention_cnt": 0,
                                                "tm_rate": 10001}}], PUB, "time")
        self.assertIsNone(s["videos"][0]["thumbnail_ctr_bps"])
        self.assertEqual(s["videos"][0]["new_follows"], 0)

    def test_archive_duration_is_not_a_zero_placeholder(self):
        self.assertEqual(watch_seconds({"play": {"avg_duration": 44, "duration": 105}}), 44)
        for value in (None, 0, -1, True, "44"):
            self.assertIsNone(watch_seconds({"play": {"avg_duration": value}}))
        self.assertIsNone(watch_seconds({"stat": {"avg_play_time_int": 24}}))

    @patch("linyuan.collect_creator_metrics.sleep")
    @patch("linyuan.collect_creator_metrics.fetch_data")
    def test_only_queries_projected_rows_and_stops_on_failure(self, fetch, _sleep):
        second, third = "BV1Z9yWzbEpo", "BV11psv6rEYR"
        rows = [ROW, {**ROW, "bvid": second}, {**ROW, "bvid": third}]
        public = PUB + [{"bvid": second}, {"bvid": third}]
        out = public_projection(rows, public, "time")
        fetch.side_effect = [{"play": {"avg_duration": 44}}, OSError("stop")]
        enrich_watch_time(out["videos"], "header", object())
        self.assertEqual([v["average_watch_seconds"] for v in out["videos"]], [44, None, None])
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(fetch.call_args_list[0].args[0].endswith(BV))
        self.assertTrue(fetch.call_args_list[1].args[0].endswith(second))

    def test_no_private_or_unverified_video(self):
        for row, allowed in [({**ROW, "is_only_self": 1}, PUB),
                             ({**ROW, "is_only_self": None}, PUB), (ROW, []),
                             ({**ROW, "stat": {"play": 5}}, PUB)]:
            with self.subTest(row=row, allowed=allowed), self.assertRaises(ValueError):
                public_projection([row], allowed, "time")


if __name__ == "__main__":
    unittest.main()
