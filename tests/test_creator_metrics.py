import unittest

from linyuan.collect_creator_metrics import public_projection


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
        self.assertEqual(v["average_watch_time_raw"], 24)
        self.assertNotIn("unfollow", v)
        self.assertNotIn("private", v)
        self.assertNotIn("hour_stat", v)

    def test_unknown_and_zero_are_different(self):
        s = public_projection([{**ROW, "stat": {"play": 12, "total_new_attention_cnt": 0,
                                                "tm_rate": 10001}}], PUB, "time")
        self.assertIsNone(s["videos"][0]["thumbnail_ctr_bps"])
        self.assertEqual(s["videos"][0]["new_follows"], 0)

    def test_no_private_or_unverified_video(self):
        for row, allowed in [({**ROW, "is_only_self": 1}, PUB),
                             ({**ROW, "is_only_self": None}, PUB), (ROW, []),
                             ({**ROW, "stat": {"play": 5}}, PUB)]:
            with self.subTest(row=row, allowed=allowed), self.assertRaises(ValueError):
                public_projection([row], allowed, "time")


if __name__ == "__main__":
    unittest.main()
