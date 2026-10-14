import argparse
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
from collections.abc import Sequence
from typing import TextIO

from cuetube.config import (
    get_env_bool,
    get_env_int,
    get_env_list,
    get_env_str,
    load_dotenv,
)
from cuetube.domain.models import PipelineSummary, SchedulePlan
from cuetube.pipeline.runner import (
    PipelineConfig,
    format_draft_schedule_table,
    run_pipeline,
)

logger = logging.getLogger("cuetube")


class TerminalProgressBar:
    """Interactive single-line progress bar with tqdm-style visual formatting."""

    STAGE_LABELS: dict[str, str] = {
        "ingestion": "[1/4 Intake  ]",
        "triage": "[2/4 Triage  ]",
        "scheduling": "[3/4 Schedule]",
        "mutation": "[4/4 Dispatch]",
        "complete": "[Done        ]",
    }

    def __init__(
        self,
        stream: TextIO | None = None,
        enabled: bool = True,
        bar_width: int = 20,
    ) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled
        self.bar_width = bar_width
        self._start_time = time.monotonic()
        self._last_len = 0
        self._finished = False
        self._lock = threading.Lock()

    def __call__(self, stage: str, current: int, total: int, message: str) -> None:
        self.update(stage, current, total, message)

    def update(self, stage: str, current: int, total: int, message: str) -> None:
        if not self.enabled:
            return

        with self._lock:
            stage_label = self.STAGE_LABELS.get(stage, f"[{stage[:12]:<12}]")
            total_safe = max(0, total)
            current_safe = min(total_safe, max(0, current)) if total_safe > 0 else max(0, current)

            encoding = getattr(self.stream, "encoding", "utf-8") or "utf-8"
            is_ascii = "ascii" in encoding.lower()
            fill_char = "#" if is_ascii else "█"
            empty_char = "-" if is_ascii else "░"

            if total_safe > 0:
                fraction = min(1.0, current_safe / total_safe)
                filled_len = int(round(self.bar_width * fraction))
                empty_len = max(0, self.bar_width - filled_len)
                bar = f"|{fill_char * filled_len}{empty_char * empty_len}|"
                pct_str = f"{int(fraction * 100):>3d}%"
                counts_str = f"{current_safe}/{total_safe}"
            else:
                bar = f"|{empty_char * self.bar_width}|"
                pct_str = "  0%"
                counts_str = f"{current_safe}"

            elapsed = time.monotonic() - self._start_time
            if total_safe > 0 and current_safe > 0:
                rate = elapsed / current_safe
                remaining = rate * (total_safe - current_safe)
                time_str = f"[{self._format_time(elapsed)}<{self._format_time(remaining)}]"
            else:
                time_str = f"[{self._format_time(elapsed)}]"

            prefix = f"{stage_label} {bar} {pct_str} {counts_str} {time_str} "

            try:
                terminal_cols = shutil.get_terminal_size((80, 24)).columns
            except Exception:
                terminal_cols = 80
            columns = max(40, terminal_cols)

            avail_msg_len = columns - 1 - len(prefix)
            if avail_msg_len <= 0:
                rendered_msg = ""
            elif len(message) > avail_msg_len:
                rendered_msg = message[: max(0, avail_msg_len - 3)] + "..."
            else:
                rendered_msg = message

            line = f"{prefix}{rendered_msg}"
            pad = " " * max(0, self._last_len - len(line))
            self.stream.write(f"\r{line}{pad}\033[K")
            self.stream.flush()
            self._last_len = len(line)

    def clear(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._last_len == 0:
                return
            self.stream.write(f"\r{' ' * self._last_len}\r\033[K")
            self.stream.flush()
            self._last_len = 0

    def finish(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._finished:
                return
            self._finished = True
            if self._last_len > 0:
                self.stream.write("\n")
                self.stream.flush()
                self._last_len = 0

    @staticmethod
    def _format_time(seconds: float) -> str:
        sec = max(0, int(seconds))
        mins, s = divmod(sec, 60)
        hrs, m = divmod(mins, 60)
        if hrs > 0:
            return f"{hrs:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"


def build_parser() -> argparse.ArgumentParser:
    """Build unified CLI argument parser with subcommands and flags."""
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="cuetube",
        description="Autonomous YouTube backlog triage and chronobiological calendar scheduler.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose DEBUG logging.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured pipeline execution summary in JSON format.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        default=get_env_bool("CUETUBE_PROGRESS") if os.getenv("CUETUBE_PROGRESS") else None,
        dest="progress",
        help="Force enable interactive single-line visual progress bar.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_false",
        dest="progress",
        help="Disable interactive progress bar.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        dest="quiet",
        help="Suppress non-error status outputs and progress bars.",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=get_env_int("CUETUBE_CONCURRENCY", 4),
        dest="concurrency",
        help="Worker thread concurrency for triage (default: env CUETUBE_CONCURRENCY or 4).",
    )

    # Subparsers for commands: run and daemon
    subparsers = parser.add_subparsers(dest="command", title="Commands")

    # Command: run (default single execution)
    run_parser = subparsers.add_parser("run", help="Run a single autonomous pipeline pass.")
    _add_pipeline_options(run_parser)
    run_parser.add_argument(
        "--confirm",
        "--interactive",
        action="store_true",
        default=get_env_bool("CUETUBE_CONFIRM", False),
        dest="confirm",
        help=(
            "Display draft schedule table and prompt for confirmation "
            "before mutating calendar or playlists."
        ),
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=get_env_bool("CUETUBE_DRY_RUN", False),
        dest="dry_run",
        help=(
            "Execute intake, triage, and slotting without inserting "
            "calendar events or mutating playlists."
        ),
    )
    run_parser.add_argument(
        "--export",
        default=get_env_str("CUETUBE_EXPORT_PATH", None),
        dest="export_path",
        help="Export schedule plan to file (.json or .ics) without requiring calendar mutation.",
    )

    # Command: daemon (continuous background worker)
    daemon_parser = subparsers.add_parser(
        "daemon", help="Run pipeline continuously as a background daemon process."
    )
    _add_pipeline_options(daemon_parser)
    daemon_parser.add_argument(
        "--interval-minutes",
        type=int,
        default=get_env_int("CUETUBE_DAEMON_INTERVAL_MINUTES", 60),
        help="Polling interval between autonomous runs in minutes (default: 60).",
    )
    daemon_parser.add_argument(
        "--max-runs",
        type=int,
        default=(
            get_env_int("CUETUBE_DAEMON_MAX_RUNS", 0)
            if os.getenv("CUETUBE_DAEMON_MAX_RUNS")
            else None
        ),
        help="Maximum iterations to execute before stopping (default: run indefinitely).",
    )
    daemon_parser.add_argument(
        "--export",
        default=get_env_str("CUETUBE_EXPORT_PATH", None),
        dest="export_path",
        help="Export schedule plan to file on each run (.json or .ics).",
    )

    return parser


def _add_pipeline_options(p: argparse.ArgumentParser) -> None:
    """Add shared pipeline configuration arguments to a parser."""
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose DEBUG logging.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output structured pipeline execution summary in JSON format.",
    )
    p.add_argument(
        "--progress",
        action="store_true",
        default=get_env_bool("CUETUBE_PROGRESS") if os.getenv("CUETUBE_PROGRESS") else None,
        dest="progress",
        help="Force enable interactive single-line visual progress bar.",
    )
    p.add_argument(
        "--no-progress",
        action="store_false",
        dest="progress",
        help="Disable interactive progress bar.",
    )
    p.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        dest="quiet",
        help="Suppress non-error status outputs and progress bars.",
    )
    p.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=get_env_int("CUETUBE_CONCURRENCY", 4),
        dest="concurrency",
        help="Worker thread concurrency for triage (default: env CUETUBE_CONCURRENCY or 4).",
    )
    p.add_argument(
        "--inbox",
        default=get_env_str("CUETUBE_INBOX", "Queue-Inbox"),
        dest="inbox_playlist_name",
        help="Custom intake queue playlist title (default: env CUETUBE_INBOX or 'Queue-Inbox').",
    )
    p.add_argument(
        "--buffer-minutes",
        type=int,
        default=get_env_int("CUETUBE_BUFFER_MINUTES", 15),
        dest="buffer_minutes",
        help="Operational padding buffer minutes (default: env CUETUBE_BUFFER_MINUTES or 15).",
    )
    p.add_argument(
        "--planning-days",
        type=int,
        default=get_env_int("CUETUBE_PLANNING_DAYS", 7),
        dest="planning_days",
        help="Days forward to search availability (default: env CUETUBE_PLANNING_DAYS or 7).",
    )
    p.add_argument(
        "--calendar-id",
        action="append",
        dest="calendar_ids",
        help="Google Calendar ID to query. Defaults to env CUETUBE_CALENDAR_IDS or 'primary'.",
    )
    p.add_argument(
        "--all-calendars",
        action="store_true",
        default=get_env_bool("CUETUBE_ALL_CALENDARS", False),
        dest="include_all_accessible_calendars",
        help="Include all user-accessible and shared Google Calendars.",
    )
    p.add_argument(
        "--target-calendar",
        default=get_env_str("CUETUBE_TARGET_CALENDAR", None)
        or get_env_str("CUETUBE_TARGET_CALENDAR_ID", "primary"),
        dest="target_calendar",
        help=(
            "Target Google Calendar name or ID for scheduled events "
            "(default: env CUETUBE_TARGET_CALENDAR or 'primary')."
        ),
    )
    p.add_argument(
        "--no-relocate",
        "--skip-playlist-mutations",
        action="store_false",
        dest="relocate_playlists",
        default=get_env_bool("CUETUBE_RELOCATE_PLAYLISTS", True),
        help="Skip moving videos into destination playlists; only schedule calendar events.",
    )
    p.add_argument(
        "--takeout",
        default=get_env_str("CUETUBE_TAKEOUT_PATH", None),
        dest="takeout_path",
        help="Path to Google Takeout ZIP archive or CSV file to use as intake source.",
    )
    p.add_argument(
        "--mock",
        action="store_true",
        help="Use in-memory test doubles instead of live external API adapters.",
    )


def _setup_logging(verbose: bool) -> None:
    """Configure root logger format and verbosity."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _format_summary_dict(summary: PipelineSummary) -> dict:
    """Convert PipelineSummary dataclass into serializable dictionary."""
    return {
        "total_ingested": summary.total_ingested,
        "total_classified": summary.total_classified,
        "total_scheduled": summary.total_scheduled,
        "total_relocated": summary.total_relocated,
        "errors": list(summary.errors),
    }


def _create_pipeline_config(args: argparse.Namespace) -> PipelineConfig:
    """Construct PipelineConfig from parsed CLI arguments."""
    concurrency = max(1, getattr(args, "concurrency", 4))
    if getattr(args, "mock", False):
        return PipelineConfig.create_mock_default(
            buffer_minutes=args.buffer_minutes,
            concurrency=concurrency,
            inbox_playlist_name=args.inbox_playlist_name,
        )

    calendar_ids = (
        tuple(args.calendar_ids)
        if args.calendar_ids
        else get_env_list("CUETUBE_CALENDAR_IDS", ("primary",))
    )
    return PipelineConfig.from_env(
        inbox_playlist_name=args.inbox_playlist_name,
        buffer_minutes=args.buffer_minutes,
        planning_days=args.planning_days,
        calendar_ids=calendar_ids,
        include_all_accessible_calendars=args.include_all_accessible_calendars,
        target_calendar=getattr(args, "target_calendar", None),
        takeout_path=args.takeout_path,
        concurrency=concurrency,
    )


def execute_run(args: argparse.Namespace) -> int:
    """Execute single pipeline pass."""
    cfg = _create_pipeline_config(args)
    confirm_flag = getattr(args, "confirm", False)
    dry_run_flag = getattr(args, "dry_run", False)
    export_path = getattr(args, "export_path", None)
    json_mode = getattr(args, "json", False)
    quiet_mode = getattr(args, "quiet", False)
    progress_arg = getattr(args, "progress", None)

    if progress_arg is not None:
        show_progress = progress_arg
    else:
        show_progress = not json_mode and not quiet_mode and sys.stderr.isatty()

    progress_bar = TerminalProgressBar(enabled=show_progress)

    if not quiet_mode and not json_mode:
        source_label = (
            f"Takeout archive '{args.takeout_path}'"
            if getattr(args, "takeout_path", None)
            else f"playlist '{cfg.inbox_playlist_name}'"
        )
        print(
            f"🎬 Processing {source_label} "
            f"(concurrency={cfg.concurrency}, buffer={cfg.buffer_minutes}m)"
        )

    confirm_cb = None
    if confirm_flag:

        def _confirm_callback(plan: SchedulePlan) -> bool:
            progress_bar.clear()
            if not json_mode:
                print("\n" + format_draft_schedule_table(plan) + "\n")
            try:
                ans = (
                    input("Proceed with calendar dispatch and playlist relocation? [y/N]: ")
                    .strip()
                    .lower()
                )
                return ans in ("y", "yes")
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return False

        confirm_cb = _confirm_callback
    elif dry_run_flag and not json_mode:

        def _dry_run_preview(plan: SchedulePlan) -> bool:
            progress_bar.clear()
            print("\n" + format_draft_schedule_table(plan) + "\n")
            return False

        confirm_cb = _dry_run_preview

    try:
        summary = run_pipeline(
            cfg,
            dry_run=dry_run_flag,
            confirm_callback=confirm_cb,
            export_path=export_path,
            progress_callback=progress_bar if show_progress else None,
            relocate_playlists=getattr(args, "relocate_playlists", True),
        )
    finally:
        progress_bar.finish()

    if json_mode:
        print(json.dumps(_format_summary_dict(summary), indent=2))
    elif not quiet_mode:
        source_label = (
            f"Takeout archive '{args.takeout_path}'"
            if getattr(args, "takeout_path", None)
            else f"playlist '{cfg.inbox_playlist_name}'"
        )
        status_line = (
            f"✅ Pipeline Completed for {source_label}: "
            f"{summary.total_ingested} ingested, "
            f"{summary.total_classified} classified, "
            f"{summary.total_scheduled} scheduled, "
            f"{summary.total_relocated} relocated."
        )
        if dry_run_flag:
            status_line = (
                f"ℹ️ Dry-run completed for {source_label}: "
                f"{summary.total_ingested} ingested, "
                f"{summary.total_classified} classified, "
                f"{summary.total_scheduled} scheduled (no mutations made)."
            )
        print(status_line)
        if export_path:
            print(f"📁 Schedule exported to: {export_path}")
        if summary.errors:
            print(f"⚠️ Encountered {len(summary.errors)} partial error(s):")
            for err in summary.errors:
                print(f"  - [{err.get('stage', 'error')}] {err.get('error', err)}")

    return 2 if summary.errors else 0


def execute_daemon(args: argparse.Namespace) -> int:
    """Execute continuous daemon scheduling loop."""
    interval_seconds = max(1, args.interval_minutes * 60)
    max_runs = args.max_runs
    current_run = 0
    shutdown_requested = False
    progress_arg = getattr(args, "progress", None)
    show_progress = bool(progress_arg) if progress_arg is not None else False

    def _sig_handler(signum: int, _frame: object) -> None:
        nonlocal shutdown_requested
        logger.info("Received signal %d; shutting down daemon gracefully...", signum)
        shutdown_requested = True

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    logger.info(
        "Starting CueTube daemon (interval: %dm, max_runs: %s)",
        args.interval_minutes,
        max_runs or "infinite",
    )

    while not shutdown_requested:
        current_run += 1
        logger.info("Executing daemon pass #%d...", current_run)

        try:
            cfg = _create_pipeline_config(args)
            progress_bar = TerminalProgressBar(enabled=show_progress)
            try:
                summary = run_pipeline(
                    cfg,
                    export_path=getattr(args, "export_path", None),
                    progress_callback=progress_bar if show_progress else None,
                )
            finally:
                progress_bar.finish()
            logger.info(
                "Pass #%d summary: %d ingested, %d scheduled, %d relocated, %d errors",
                current_run,
                summary.total_ingested,
                summary.total_scheduled,
                summary.total_relocated,
                len(summary.errors),
            )
        except Exception as exc:
            logger.error("Pass #%d failed with exception: %s", current_run, exc)

        if max_runs is not None and current_run >= max_runs:
            logger.info("Reached maximum iterations (%d); daemon exiting.", max_runs)
            break

        # Sleep in small slices to respond promptly to shutdown signals
        sleep_cursor = 0
        while sleep_cursor < interval_seconds and not shutdown_requested:
            step = min(1, interval_seconds - sleep_cursor)
            time.sleep(step)
            sleep_cursor += step

    logger.info("CueTube daemon stopped.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    args_list = list(argv) if argv is not None else sys.argv[1:]

    # Default to 'run' command if first argument is not a known command or help
    if not args_list or (args_list[0] not in ("run", "daemon", "-h", "--help")):
        args_list = ["run"] + args_list

    try:
        parsed_args = parser.parse_args(args_list)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 0

    _setup_logging(parsed_args.verbose)

    try:
        if parsed_args.command == "daemon":
            return execute_daemon(parsed_args)
        return execute_run(parsed_args)
    except Exception as exc:
        logger.error("CueTube execution failed: %s", exc)
        if parsed_args.verbose:
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
