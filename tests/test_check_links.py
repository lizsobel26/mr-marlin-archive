import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_links as cl  # noqa: E402


def fake(responses):
    """A stand-in for fetch(): maps URL prefixes to (status, final_url, body)."""
    def get(url):
        for prefix, resp in responses.items():
            if url.startswith(prefix):
                return resp
        raise AssertionError("unexpected fetch: " + url)
    return get


OEMBED = "https://www.youtube.com/oembed"
WATCH = "https://www.youtube.com/watch?v=abc123DEF45"


class YouTube(unittest.TestCase):
    url = "https://www.youtube.com/watch?v=abc123DEF45"

    def test_ok(self):
        self.assertEqual(cl.check(self.url, fake({OEMBED: (200, "", "{}")}))[0], "ok")

    def test_deleted(self):
        self.assertEqual(cl.check(self.url, fake({OEMBED: (404, "", "")}))[0], "broken")

    def test_private_uses_watch_page_reason(self):
        page = '"playabilityStatus":{"status":"LOGIN_REQUIRED","reason":"This video is private"}'
        status, detail = cl.check(self.url, fake({OEMBED: (401, "", ""), WATCH: (200, WATCH, page)}))
        self.assertEqual((status, detail), ("broken", "This video is private"))

    def test_embedding_disabled_but_playable(self):
        page = '"playabilityStatus":{"status":"OK"}'
        self.assertEqual(cl.check(self.url, fake({OEMBED: (401, "", ""), WATCH: (200, WATCH, page)}))[0], "ok")

    def test_unreadable_watch_page_is_unknown(self):
        self.assertEqual(cl.check(self.url, fake({OEMBED: (401, "", ""), WATCH: (200, WATCH, "consent")}))[0], "unknown")

    def test_short_link_and_missing_id(self):
        self.assertEqual(cl.youtube_id("https://youtu.be/abc123DEF45"), "abc123DEF45")
        self.assertEqual(cl.check("https://www.youtube.com/watch", fake({}))[0], "broken")


class Archive(unittest.TestCase):
    meta = "https://archive.org/metadata/game-item"

    def test_item_ok(self):
        body = json.dumps({"metadata": {}, "files": [{"name": "Game 7.mp4"}]})
        self.assertEqual(cl.check("https://archive.org/details/game-item", fake({self.meta: (200, "", body)}))[0], "ok")

    def test_file_inside_item(self):
        body = json.dumps({"metadata": {}, "files": [{"name": "Game 7.mp4"}]})
        get = fake({self.meta: (200, "", body)})
        self.assertEqual(cl.check("https://archive.org/details/game-item/Game%207.mp4", get)[0], "ok")
        self.assertEqual(cl.check("https://archive.org/details/game-item/Game%206.mp4", get)[0], "broken")

    def test_removed_item(self):
        self.assertEqual(cl.check("https://archive.org/details/game-item", fake({self.meta: (200, "", "{}")}))[0], "broken")
        dark = json.dumps({"is_dark": True})
        self.assertEqual(cl.check("https://archive.org/details/game-item", fake({self.meta: (200, "", dark)}))[0], "broken")


class Generic(unittest.TestCase):
    def test_ok_with_canonical_redirect(self):
        url = "https://mlb.com/video/conine-s-grand-slam-c1599185283"
        final = "https://www.mlb.com/video/conine-s-grand-slam-c1599185283"
        self.assertEqual(cl.check(url, fake({url: (200, final, "<title>Conine's grand slam</title>")}))[0], "ok")

    def test_404_and_soft_404(self):
        url = "https://www.espn.com/video/clip?id=41021872"
        self.assertEqual(cl.check(url, fake({url: (404, url, "")}))[0], "broken")
        self.assertEqual(cl.check(url, fake({url: (200, url, "<title>Page Not Found - ESPN</title>")}))[0], "broken")

    def test_redirect_to_unrelated_page_is_moved(self):
        url = "https://www.foxsports.com/florida/video/692128323795"
        status, detail = cl.check(url, fake({url: (200, "https://www.foxsports.com/live", "")}))
        self.assertEqual(status, "moved")
        self.assertIn("foxsports.com/live", detail)

    def test_redirect_to_home_page_is_moved(self):
        url = "https://wsvn.com/sports/some-story/"
        self.assertEqual(cl.check(url, fake({url: (200, "https://wsvn.com/", "")}))[0], "moved")

    def test_bot_protection_is_not_broken(self):
        url = "https://www.mlb.com/video/x"
        for code in (403, 406, 429, 503):
            self.assertEqual(cl.check(url, fake({url: (code, url, "")}))[0], "unknown")

    def test_network_error_is_unknown(self):
        def boom(url):
            raise TimeoutError()
        self.assertEqual(cl.check("https://example.com/v/123456", boom)[0], "unknown")


class Report(unittest.TestCase):
    def test_lists_only_dead_links(self):
        videos = [{"id": "a", "title": "A | B", "url": "https://x/a", "date": "1997"},
                  {"id": "b", "title": "Fine", "url": "https://x/b", "date": ""}]
        results = {"a": {"status": "moved", "detail": "Now redirects to https://x/"}, "b": {"status": "ok", "detail": ""}}
        count, text = cl.build_report(videos, results)
        self.assertEqual(count, 1)
        self.assertIn("A / B", text)
        self.assertNotIn("Fine", text)

    def test_all_clear(self):
        self.assertEqual(cl.build_report([], {})[0], 0)


if __name__ == "__main__":
    unittest.main()
