"""Unit tests for CueTube centralized config and environment variable loading."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cuetube.cli import build_parser
from cuetube.config import (
    get_env_bool,
    get_env_int,
    get_env_list,
    get_env_str,
    load_dotenv,
)
from cuetube.pipeline.runner import PipelineConfig


class TestCueTubeConfig(unittest.TestCase):
    """Test suite for config helpers and environment variable integration."""

    def test_load_dotenv_parses_file_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "# Comment\n"
                "EXISTING_KEY=new_val\n"
                "NEW_KEY=my_val\n"
                'QUOTED_KEY="quoted_val"\n'
                "EMPTY_LINE=\n"
                "INVALID LINE\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"EXISTING_KEY": "original"}, clear=True):
                load_dotenv(env_file)
                self.assertEqual(os.environ.get("EXISTING_KEY"), "original")
                self.assertEqual(os.environ.get("NEW_KEY"), "my_val")
                self.assertEqual(os.environ.get("QUOTED_KEY"), "quoted_val")

    def test_get_env_str(self) -> None:
        with patch.dict(os.environ, {"TEST_STR": "hello", "EMPTY_STR": "   "}, clear=True):
            self.assertEqual(get_env_str("TEST_STR", "def"), "hello")
            self.assertEqual(get_env_str("EMPTY_STR", "def"), "def")
            self.assertEqual(get_env_str("UNSET_STR", "def"), "def")

    def test_get_env_int(self) -> None:
        with patch.dict(os.environ, {"TEST_INT": "42", "INVALID_INT": "abc"}, clear=True):
            self.assertEqual(get_env_int("TEST_INT", 10), 42)
            self.assertEqual(get_env_int("INVALID_INT", 10), 10)
            self.assertEqual(get_env_int("UNSET_INT", 10), 10)

    def test_get_env_bool(self) -> None:
        with patch.dict(
            os.environ,
            {"B_TRUE": "true", "B_ONE": "1", "B_YES": "yes", "B_FALSE": "false"},
            clear=True,
        ):
            self.assertTrue(get_env_bool("B_TRUE"))
            self.assertTrue(get_env_bool("B_ONE"))
            self.assertTrue(get_env_bool("B_YES"))
            self.assertFalse(get_env_bool("B_FALSE"))
            self.assertFalse(get_env_bool("UNSET_BOOL", False))
            self.assertTrue(get_env_bool("UNSET_BOOL", True))

    def test_get_env_list(self) -> None:
        with patch.dict(os.environ, {"TEST_LIST": "a, b ,  c  "}, clear=True):
            self.assertEqual(get_env_list("TEST_LIST"), ("a", "b", "c"))
            self.assertEqual(get_env_list("UNSET_LIST", ("default",)), ("default",))

    def test_cli_parser_defaults_from_env(self) -> None:
        env_vars = {
            "CUETUBE_INBOX": "Env-Inbox",
            "CUETUBE_BUFFER_MINUTES": "30",
            "CUETUBE_PLANNING_DAYS": "14",
            "CUETUBE_CONCURRENCY": "8",
            "CUETUBE_ALL_CALENDARS": "1",
            "CUETUBE_DRY_RUN": "1",
            "CUETUBE_CONFIRM": "1",
            "CUETUBE_EXPORT_PATH": "env-export.ics",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            parser = build_parser()
            args = parser.parse_args(["run"])
            self.assertEqual(args.inbox_playlist_name, "Env-Inbox")
            self.assertEqual(args.buffer_minutes, 30)
            self.assertEqual(args.planning_days, 14)
            self.assertEqual(args.concurrency, 8)
            self.assertTrue(args.include_all_accessible_calendars)
            self.assertTrue(args.dry_run)
            self.assertTrue(args.confirm)
            self.assertEqual(args.export_path, "env-export.ics")

    def test_cli_flags_override_env_defaults(self) -> None:
        env_vars = {
            "CUETUBE_INBOX": "Env-Inbox",
            "CUETUBE_CONCURRENCY": "8",
            "CUETUBE_BUFFER_MINUTES": "30",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            parser = build_parser()
            # Explicit CLI flags override environment defaults
            args = parser.parse_args(
                ["run", "--inbox", "CLI-Inbox", "--concurrency", "2", "--buffer-minutes", "10"]
            )
            self.assertEqual(args.inbox_playlist_name, "CLI-Inbox")
            self.assertEqual(args.concurrency, 2)
            self.assertEqual(args.buffer_minutes, 10)

    def test_pipeline_config_from_env_defaults(self) -> None:
        env_vars = {
            "CUETUBE_INBOX": "Custom-Takeout-Inbox",
            "CUETUBE_BUFFER_MINUTES": "25",
            "CUETUBE_PLANNING_DAYS": "10",
            "CUETUBE_CONCURRENCY": "6",
            "OPENROUTER_API_KEY": "test-key",
            "GOOGLE_CLIENT_ID": "test-id",
            "GOOGLE_CLIENT_SECRET": "test-secret",
            "GOOGLE_REFRESH_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            # Using takeout_path so it doesn't need live YouTube credentials
            with tempfile.NamedTemporaryFile(suffix=".csv") as tf:
                cfg = PipelineConfig.from_env(takeout_path=tf.name)
                self.assertEqual(cfg.inbox_playlist_name, "Custom-Takeout-Inbox")
                self.assertEqual(cfg.buffer_minutes, 25)
                self.assertEqual(cfg.concurrency, 6)


if __name__ == "__main__":
    unittest.main()
