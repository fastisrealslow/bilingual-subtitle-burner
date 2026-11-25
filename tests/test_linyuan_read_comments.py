import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

SPEC = importlib.util.spec_from_file_location('read_comments', Path(__file__).parents[1] / 'linyuan/read_comments.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
NEW, OLD = 'BVabcdefghij', 'BV1234567890'
OWNER = m.OWNER

class CommentsTests(unittest.TestCase):
    def state(self):
        return {'published': {'old': {'bvid': OLD, 'ts': 1}, 'new': {'bvid': NEW, 'ts': 2, 'parts': [{'bvid': NEW}]}}, 'dispatched': [{'bvid': 'BVsource1234'}]}

    def test_newest_unique_and_no_source(self):
        self.assertEqual(m.select_candidates(self.state()), [NEW, OLD])

    def test_bounded_and_empty(self):
        self.assertEqual(m.select_candidates(self.state(), 1), [NEW])
        self.assertEqual(m.select_candidates({}), [])

    def test_invalid_bvid_filtered(self):
        self.assertEqual(m.select_candidates({'published': {'x': {'bvid': 'https://evil.invalid/'}}}), [])

    def execute(self, responses, state=None, owner=OWNER):
        calls = []
        def request(req, **kwargs):
            self.assertEqual(req.get_method(), 'GET')
            self.assertTrue(req.full_url.startswith('https://api.bilibili.com/'))
            self.assertNotIn('/add', req.full_url)
            calls.append(req.full_url)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return io.StringIO(json.dumps(response))
        raw = json.dumps({'cookies': [{'name': 'DedeUserID', 'value': owner}, {'name': 'bili_jct', 'value': 'FAKE_TEST_ONLY'}]})
        output = io.StringIO()
        exit_code = 0
        with patch.dict(m.os.environ, {'BILIBILI_COOKIES': raw}), patch.object(m.Path, 'read_text', return_value=json.dumps(state or {'published': {'x': {'bvid': NEW}}})), patch.object(m.opener, 'open', side_effect=request), patch.object(m.time, 'sleep'), contextlib.redirect_stdout(output):
            try:
                m.main()
            except SystemExit as exc:
                exit_code = exc.code
        self.assertNotIn('FAKE_TEST_ONLY', output.getvalue())
        return exit_code, calls, [json.loads(x) for x in output.getvalue().splitlines()]

    def nav(self):
        return {'code': 0, 'data': {'isLogin': True, 'mid': int(OWNER)}}

    def video(self, owner=OWNER, count=0):
        return {'code': 0, 'data': {'owner': {'mid': int(owner)}, 'aid': 123, 'stat': {'reply': count}, 'title': 'Test'}}

    def test_zero_comments_no_write(self):
        code, calls, events = self.execute([self.nav(), self.video()])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(events[-1], {'kind': 'complete', 'comments_read': 0, 'writes': 0})

    def test_wrong_cookie_owner_stops_before_network(self):
        code, calls, _ = self.execute([], owner='1')
        self.assertEqual((code, calls), (1, []))

    def test_nav_identity_mismatch_stops(self):
        code, calls, _ = self.execute([{'code': 0, 'data': {'isLogin': True, 'mid': 1}}])
        self.assertEqual((code, len(calls)), (1, 1))

    def test_other_video_owner_not_read(self):
        code, calls, _ = self.execute([self.nav(), self.video(owner='1', count=2)])
        self.assertEqual((code, len(calls)), (0, 2))

    def test_http_412_no_retry(self):
        code, calls, events = self.execute([HTTPError('https://api.bilibili.com/', 412, 'blocked', {}, None)])
        self.assertEqual((code, len(calls)), (1, 1))
        self.assertEqual(events[-1]['http_status'], 412)

    def test_api_412_no_retry(self):
        code, calls, _ = self.execute([{'code': -412}])
        self.assertEqual((code, len(calls)), (1, 1))

    def test_missing_owner_preview_fetches_thread(self):
        replies = {'code': 0, 'data': {'page': {'count': 1}, 'replies': [{'rpid': '9', 'mid': 1, 'rcount': 1, 'content': {'message': 'Hi'}}]}}
        thread = {'code': 0, 'data': {'page': {'count': 1}, 'replies': [{'rpid': '10', 'mid': int(OWNER), 'content': {'message': 'Thanks'}}]}}
        code, calls, events = self.execute([self.nav(), self.video(count=1), replies, thread])
        self.assertEqual(code, 0)
        self.assertIn('/x/v2/reply/reply?', calls[-1])
        self.assertTrue(next(x for x in events if x['kind'] == 'comment')['visible_owner_reply'])
        self.assertEqual(events[-1]['writes'], 0)

    def test_redirect_refused(self):
        self.assertIsNone(m.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_no_post_code(self):
        source = Path(m.__file__).read_text()
        self.assertNotIn('method="POST"', source)
        self.assertNotIn('reply/add', source)

if __name__ == '__main__':
    unittest.main()
