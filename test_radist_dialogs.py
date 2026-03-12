import unittest

from radist_dialogs import parse_args, utc_range_inclusive, extract_items


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


if __name__ == "__main__":
    unittest.main()
