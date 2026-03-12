import tempfile
import unittest
from pathlib import Path

import radist_dialogs
from radist_dialogs import ApiError, build_auth_value, parse_args, utc_range_inclusive


class CliTests(unittest.TestCase):
    def test_parse_latest(self):
        cfg = parse_args(["--token", "t", "--company-id", "163146", "--latest", "5"])
        self.assertEqual(cfg.mode, "latest")
        self.assertEqual(cfg.latest, 5)
        self.assertEqual(cfg.company_id, 163146)
        self.assertEqual(cfg.auth_header, "X-Api-Key")
        self.assertEqual(cfg.auth_prefix, "")

    def test_parse_index_range(self):
        cfg = parse_args(
            [
                "--token",
                "t",
                "--company-id",
                "163146",
                "--index-range",
                "--from-index",
                "500",
                "--to-index",
                "1000",
            ]
        )
        self.assertEqual(cfg.mode, "index_range")
        self.assertEqual(cfg.from_index, 500)
        self.assertEqual(cfg.to_index, 1000)

    def test_parse_setup_only(self):
        cfg = parse_args(["--token", "t", "--company-id", "163146", "--save-config"])
        self.assertTrue(cfg.setup_only)
        self.assertIsNone(cfg.mode)

    def test_parse_uses_saved_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "radist.json"
            config_path.write_text(
                '{"token":"saved","company_id":163146,"auth_header":"X-Api-Key","auth_prefix":""}',
                encoding="utf-8",
            )
            cfg = parse_args(["--config", str(config_path), "--latest", "1"])
            self.assertEqual(cfg.token, "saved")
            self.assertEqual(cfg.company_id, 163146)
            self.assertEqual(cfg.latest, 1)

    def test_utc_range_is_inclusive(self):
        start, end = utc_range_inclusive("2026-01-01", "2026-01-02")
        self.assertEqual(start, "2026-01-01T00:00:00Z")
        self.assertTrue(end.startswith("2026-01-02T23:59:59"))

    def test_build_auth_value(self):
        self.assertEqual(build_auth_value("Bearer", "abc"), "Bearer abc")
        self.assertEqual(build_auth_value("", "abc"), "abc")

    def test_flatten_chats(self):
        payload = {
            "data": [
                {
                    "contact_id": 1,
                    "contact_name": "Alice",
                    "last_chat_updated_at": "2026-03-01T10:00:00Z",
                    "chats": [{"chat_id": 10, "name": "Chat A", "unanswered_count": 0}],
                }
            ]
        }
        dialogs = radist_dialogs.flatten_chats(payload)
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0]["contact"]["contact_name"], "Alice")
        self.assertEqual(dialogs[0]["chat"]["chat_id"], 10)

    def test_select_dialog_slice_for_index_range(self):
        cfg = parse_args(
            [
                "--token",
                "t",
                "--company-id",
                "163146",
                "--index-range",
                "--from-index",
                "2",
                "--to-index",
                "3",
            ]
        )
        dialogs = [{"chat": {"chat_id": 1}}, {"chat": {"chat_id": 2}}, {"chat": {"chat_id": 3}}]
        sliced = radist_dialogs.select_dialog_slice(cfg, dialogs)
        self.assertEqual([item["chat"]["chat_id"] for item in sliced], [2, 3])

    def test_resolve_company_id_from_single_company(self):
        cfg = parse_args(["--token", "t", "--latest", "1"])
        original = radist_dialogs.fetch_json

        def fake_fetch(url, config):
            return {"companies": [{"id": 163146, "name": "Only"}]}

        radist_dialogs.fetch_json = fake_fetch
        try:
            self.assertEqual(radist_dialogs.resolve_company_id(cfg), 163146)
        finally:
            radist_dialogs.fetch_json = original

    def test_resolve_company_id_requires_explicit_when_multiple(self):
        cfg = parse_args(["--token", "t", "--latest", "1"])
        original = radist_dialogs.fetch_json

        def fake_fetch(url, config):
            return {"companies": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]}

        radist_dialogs.fetch_json = fake_fetch
        try:
            with self.assertRaises(ApiError):
                radist_dialogs.resolve_company_id(cfg)
        finally:
            radist_dialogs.fetch_json = original

    def test_target_dialog_count_for_index_range(self):
        cfg = parse_args(
            [
                "--token",
                "t",
                "--company-id",
                "163146",
                "--index-range",
                "--from-index",
                "500",
                "--to-index",
                "1000",
            ]
        )
        self.assertEqual(radist_dialogs.target_dialog_count(cfg), 1000)


if __name__ == "__main__":
    unittest.main()
