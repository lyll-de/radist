import unittest

import radist_dialogs
from radist_dialogs import ApiError, parse_args, utc_range_inclusive, extract_items


class CliTests(unittest.TestCase):
    def test_parse_latest(self):
        cfg = parse_args(["--token", "t", "--latest", "5"])
        self.assertEqual(cfg.mode, "latest")
        self.assertEqual(cfg.latest, 5)

    def test_parse_date_range(self):
        cfg = parse_args(
            [
                "--token",
                "t",
                "--date-range",
                "--from-date",
                "2026-01-01",
                "--to-date",
                "2026-01-10",
            ]
        )
        self.assertEqual(cfg.mode, "date_range")

    def test_utc_range_is_inclusive(self):
        start, end = utc_range_inclusive("2026-01-01", "2026-01-02")
        self.assertEqual(start, "2026-01-01T00:00:00Z")
        self.assertTrue(end.startswith("2026-01-02T23:59:59"))

    def test_extract_items(self):
        payload = {"data": [{"id": 1}, {"id": 2}]}
        self.assertEqual(len(extract_items(payload)), 2)

    def test_endpoint_autodetection_uses_first_working_candidate(self):
        cfg = parse_args(["--token", "t", "--latest", "1"])

        original = radist_dialogs.fetch_page

        def fake_fetch(url, token, timeout):
            if "/chat?" in url:
                return {"data": []}
            if "/chats?" in url:
                raise radist_dialogs.HttpStatusError(status_code=404, url=url, body="")
            raise ApiError("unexpected")

        radist_dialogs.fetch_page = fake_fetch
        try:
            self.assertEqual(radist_dialogs.resolve_endpoint(cfg), "/chat")
        finally:
            radist_dialogs.fetch_page = original

    def test_endpoint_autodetection_fails_with_hint(self):
        cfg = parse_args(["--token", "t", "--latest", "1"])
        original = radist_dialogs.fetch_page

        def fake_fetch(url, token, timeout):
            raise radist_dialogs.HttpStatusError(status_code=404, url=url, body="")

        radist_dialogs.fetch_page = fake_fetch
        try:
            with self.assertRaises(ApiError):
                radist_dialogs.resolve_endpoint(cfg)
        finally:
            radist_dialogs.fetch_page = original


if __name__ == "__main__":
    unittest.main()
