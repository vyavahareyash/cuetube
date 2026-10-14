"""Tests for CueTube unified CLI runner and daemon scheduler."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cuetube.cli import build_parser, main


class TestCueTubeCLI(unittest.TestCase):
    """Test suite for cuetube command line entry point."""

    def setUp(self) -> None:
        self._env_patch = patch.dict("os.environ", {}, clear=True)
        self._env_patch.start()
        self._dotenv_patch = patch("cuetube.cli.load_dotenv")
        self._dotenv_patch.start()
        self._config_dotenv_patch = patch("cuetube.config.load_dotenv")
        self._config_dotenv_patch.start()

    def tearDown(self) -> None:
        self._config_dotenv_patch.stop()
        self._dotenv_patch.stop()
        self._env_patch.stop()

    def test_build_parser_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.inbox_playlist_name, "Queue-Inbox")
        self.assertEqual(args.buffer_minutes, 15)
        self.assertEqual(args.planning_days, 7)
        self.assertFalse(args.mock)
        self.assertFalse(args.dry_run)
        self.assertFalse(args.confirm)
        self.assertFalse(args.json)

    def test_run_mock_success(self) -> None:
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out):
            code = main(["run", "--mock"])
        self.assertEqual(code, 0)
        output = captured_out.getvalue()
        self.assertIn("Processing playlist 'Queue-Inbox'", output)
        self.assertIn("Pipeline Completed for playlist 'Queue-Inbox'", output)

    def test_run_mock_custom_playlist_display(self) -> None:
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out):
            code = main(["run", "--mock", "--inbox", "Custom-Queue"])
        self.assertEqual(code, 0)
        output = captured_out.getvalue()
        self.assertIn("Processing playlist 'Custom-Queue'", output)
        self.assertIn("Pipeline Completed for playlist 'Custom-Queue'", output)

    def test_run_mock_json_output(self) -> None:
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out):
            code = main(["run", "--mock", "--json"])
        self.assertEqual(code, 0)
        output_str = captured_out.getvalue()
        data = json.loads(output_str)
        self.assertIn("total_ingested", data)
        self.assertIn("total_scheduled", data)
        self.assertIn("total_relocated", data)
        self.assertGreater(data["total_ingested"], 0)

    def test_run_mock_dry_run(self) -> None:
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out):
            code = main(["run", "--mock", "--dry-run", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(captured_out.getvalue())
        self.assertEqual(data["total_relocated"], 0)
        self.assertGreater(data["total_scheduled"], 0)

    def test_run_mock_export_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_file = Path(tmp_dir) / "calendar.ics"
            code = main(["run", "--mock", "--export", str(export_file), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertTrue(export_file.exists())
            self.assertIn("BEGIN:VCALENDAR", export_file.read_text(encoding="utf-8"))

    def test_run_mock_interactive_confirm_yes(self) -> None:
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out), patch("builtins.input", return_value="y"):
            code = main(["run", "--mock", "--confirm", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(captured_out.getvalue())
        self.assertGreater(data["total_relocated"], 0)

    def test_run_mock_interactive_confirm_no(self) -> None:
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out), patch("builtins.input", return_value="n"):
            code = main(["run", "--mock", "--confirm", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(captured_out.getvalue())
        self.assertEqual(data["total_relocated"], 0)
        self.assertEqual(data["total_scheduled"], 0)

    def test_daemon_mock_bounded_runs(self) -> None:
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out), patch("time.sleep", return_value=None):
            code = main(["daemon", "--mock", "--max-runs", "2", "--interval-minutes", "1"])
        self.assertEqual(code, 0)

    def test_implicit_run_command(self) -> None:
        # Calling `cuetube --mock` should implicitly route to `run --mock`
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out):
            code = main(["--mock", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(captured_out.getvalue())
        self.assertGreater(data["total_ingested"], 0)

    def test_terminal_progress_bar_render_and_clear(self) -> None:
        from cuetube.cli import TerminalProgressBar

        stream = io.StringIO()
        pbar = TerminalProgressBar(stream=stream, enabled=True, bar_width=10)
        pbar.update("triage", 1, 2, "LLM classification: Test Video")

        out = stream.getvalue()
        self.assertIn("\r", out)
        self.assertIn("[2/4 Triage  ]", out)
        self.assertIn("50%", out)
        self.assertIn("1/2", out)
        self.assertIn("LLM classification: Test", out)

        # Clear should erase
        pbar.clear()
        self.assertIn("\033[K", stream.getvalue())

        # Update again and finish should flush newline
        pbar.update("complete", 1, 1, "Pipeline run finished")
        pbar.finish()
        self.assertTrue(stream.getvalue().endswith("\n"))

    def test_terminal_progress_bar_disabled(self) -> None:
        from cuetube.cli import TerminalProgressBar

        stream = io.StringIO()
        pbar = TerminalProgressBar(stream=stream, enabled=False)
        pbar.update("triage", 1, 2, "Test")
        pbar.clear()
        pbar.finish()
        self.assertEqual(stream.getvalue(), "")

    def test_cli_explicit_progress_flag(self) -> None:
        captured_out = io.StringIO()
        captured_err = io.StringIO()
        with patch("sys.stdout", captured_out), patch("sys.stderr", captured_err):
            code = main(["run", "--mock", "--progress"])
        self.assertEqual(code, 0)
        err_val = captured_err.getvalue()
        self.assertIn("[1/4 Intake  ]", err_val)
        self.assertIn("[2/4 Triage  ]", err_val)
        self.assertIn("[3/4 Schedule]", err_val)
        self.assertIn("[4/4 Dispatch]", err_val)
        self.assertIn("Pipeline Completed", captured_out.getvalue())

    def test_cli_explicit_no_progress_flag(self) -> None:
        captured_out = io.StringIO()
        captured_err = io.StringIO()
        with patch("sys.stdout", captured_out), patch("sys.stderr", captured_err):
            code = main(["run", "--mock", "--no-progress"])
        self.assertEqual(code, 0)
        err_val = captured_err.getvalue()
        self.assertNotIn("[1/4 Intake  ]", err_val)
        self.assertNotIn("[2/4 Triage  ]", err_val)
        self.assertIn("Pipeline Completed", captured_out.getvalue())

    def test_cli_quiet_flag(self) -> None:
        captured_out = io.StringIO()
        captured_err = io.StringIO()
        with patch("sys.stdout", captured_out), patch("sys.stderr", captured_err):
            code = main(["run", "--mock", "-q"])
        self.assertEqual(code, 0)
        self.assertEqual(captured_out.getvalue(), "")
        self.assertEqual(captured_err.getvalue(), "")

    def test_cli_concurrency_flag(self) -> None:
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out):
            code = main(["run", "--mock", "--concurrency", "2", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(captured_out.getvalue())
        self.assertEqual(data["total_ingested"], 2)
        self.assertEqual(data["total_classified"], 2)

    def test_cli_target_calendar_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--target-calendar", "CueTube"])
        self.assertEqual(args.target_calendar, "CueTube")

    def test_cli_target_calendar_forwarded_to_from_env(self) -> None:
        from cuetube.cli import _create_pipeline_config

        parser = build_parser()
        args = parser.parse_args(["run", "--target-calendar", "DedicatedTube"])
        with patch("cuetube.pipeline.runner.PipelineConfig.from_env") as mock_from_env:
            _create_pipeline_config(args)
            mock_from_env.assert_called_once()
            _, kwargs = mock_from_env.call_args
            self.assertEqual(kwargs.get("target_calendar"), "DedicatedTube")

    def test_cli_no_relocate_flag(self) -> None:
        parser = build_parser()
        args1 = parser.parse_args(["run"])
        self.assertTrue(args1.relocate_playlists)
        args2 = parser.parse_args(["run", "--no-relocate"])
        self.assertFalse(args2.relocate_playlists)
        args3 = parser.parse_args(["run", "--skip-playlist-mutations"])
        self.assertFalse(args3.relocate_playlists)


if __name__ == "__main__":
    unittest.main()
