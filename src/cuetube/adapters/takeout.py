"""Google Takeout CSV and ZIP archive ingestion adapter."""

import csv
import io
import logging
import re
import zipfile
from collections.abc import Container, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from cuetube.domain.models import VideoItem

logger = logging.getLogger(__name__)

# YouTube video ID patterns
_YT_STRICT_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_FLEXIBLE_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]+$")

# Column alias mappings (normalized lowercase, stripped)
_VIDEO_ID_ALIASES = ("video id", "video_id", "videoid", "id", "track id", "trackid")
_TIMESTAMP_ALIASES = (
    "playlist video creation timestamp",
    "playlist video creation time",
    "creation timestamp",
    "time added",
    "date added",
    "added at",
    "published at",
    "timestamp",
    "published_at",
)
_TITLE_ALIASES = ("title", "video title", "name")
_CHANNEL_ALIASES = ("channel", "channel title", "channel name", "uploader", "creator")
_DURATION_ALIASES = (
    "duration",
    "duration seconds",
    "duration_seconds",
    "duration (seconds)",
    "length",
)
_DESCRIPTION_ALIASES = ("description", "summary")
_URL_ALIASES = ("url", "video url", "link")


def _is_empty_row(row: dict[Any, Any]) -> bool:
    """Check whether a CSV row is empty or contains only whitespace."""
    if not row:
        return True
    for v in row.values():
        if isinstance(v, str) and v.strip():
            return False
        if isinstance(v, list) and any(isinstance(item, str) and item.strip() for item in v):
            return False
    return True


def extract_video_id(raw_value: str, strict: bool = False) -> str | None:
    """Extract and validate YouTube video ID from string or URL."""
    cleaned = raw_value.strip()
    if not cleaned:
        return None

    regex = _YT_STRICT_ID_REGEX if strict else _YT_FLEXIBLE_ID_REGEX

    # Check URL formats
    if "youtube.com" in cleaned or "youtu.be" in cleaned:
        try:
            parsed = urlparse(cleaned)
            # Shortened youtu.be/<id>
            if "youtu.be" in parsed.netloc:
                candidate = parsed.path.lstrip("/").split("/")[0]
                if regex.match(candidate):
                    return candidate

            # youtube.com/watch?v=<id>
            query_params = parse_qs(parsed.query)
            if "v" in query_params and query_params["v"]:
                candidate = query_params["v"][0]
                if regex.match(candidate):
                    return candidate

            # youtube.com/shorts/<id> or /embed/<id>
            path_parts = [p for p in parsed.path.split("/") if p]
            if len(path_parts) >= 2 and path_parts[0] in ("shorts", "embed", "v"):
                candidate = path_parts[1]
                if regex.match(candidate):
                    return candidate
        except Exception:
            return None
        return None

    # Direct ID candidate
    if regex.match(cleaned):
        return cleaned

    return None


def parse_duration_seconds(raw_value: str | None) -> int:
    """Parse duration in seconds from integer string or HH:MM:SS format."""
    if not raw_value:
        return 0
    cleaned = raw_value.strip()
    if not cleaned:
        return 0

    # Direct integer seconds
    try:
        val = int(cleaned)
        return max(0, val)
    except ValueError:
        pass

    # Clock format HH:MM:SS or MM:SS
    parts = cleaned.split(":")
    if all(p.isdigit() for p in parts):
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 1:
            return int(parts[0])

    return 0


class TakeoutCsvSource:
    """IngestionSource adapter parsing Google Takeout CSV archives and files.

    Hides archive file/stream inspection, column normalization, deduplication,
    and malformed row handling behind `fetch_pending_videos()`.
    """

    def __init__(
        self,
        source: str | Path | io.TextIOBase | io.BytesIO | bytes,
        existing_ids: Container[str] | None = None,
        default_channel: str = "Unknown",
        default_title_prefix: str = "YouTube Video",
        strict_video_id: bool = False,
    ) -> None:
        self.source = source
        self.existing_ids: Container[str] = existing_ids or set()
        self.default_channel = default_channel
        self.default_title_prefix = default_title_prefix
        self.strict_video_id = strict_video_id

        # Diagnostic auditing properties
        self.skipped_records: list[dict[str, Any]] = []
        self.duplicate_records: list[str] = []
        self.existing_filtered_records: list[str] = []

    def fetch_pending_videos(
        self,
        existing_ids: Container[str] | None = None,
    ) -> list[VideoItem]:
        """Parse source, deduplicate, normalize, and return domain VideoItem records."""
        filter_ids = self.existing_ids if existing_ids is None else existing_ids
        seen_in_archive: set[str] = set()
        items: list[VideoItem] = []

        for csv_stream in self._iter_csv_streams():
            for row_idx, row in self._iter_csv_rows(csv_stream):
                # Empty or whitespace row
                if _is_empty_row(row):
                    continue

                raw_vid = self._find_field(row, _VIDEO_ID_ALIASES)
                video_id = (
                    extract_video_id(raw_vid, strict=self.strict_video_id) if raw_vid else None
                )

                if not video_id:
                    err_msg = f"Missing or invalid video ID: raw value {raw_vid!r}"
                    logger.warning("Skipping malformed Takeout row %d: %s", row_idx, err_msg)
                    self.skipped_records.append(
                        {"row_index": row_idx, "error": err_msg, "row": row}
                    )
                    continue

                # 1. Deduplicate against already processed items
                if filter_ids and video_id in filter_ids:
                    logger.debug("Filtered existing backlog video ID: %s", video_id)
                    self.existing_filtered_records.append(video_id)
                    continue

                # 2. Deduplicate within archive batch
                if video_id in seen_in_archive:
                    logger.debug("Deduplicated repeated video ID in archive: %s", video_id)
                    self.duplicate_records.append(video_id)
                    continue

                # 3. Normalize fields
                title = (
                    self._find_field(row, _TITLE_ALIASES)
                    or f"{self.default_title_prefix} {video_id}"
                )
                channel = self._find_field(row, _CHANNEL_ALIASES) or self.default_channel
                timestamp = self._find_field(row, _TIMESTAMP_ALIASES) or ""
                duration_raw = self._find_field(row, _DURATION_ALIASES)
                duration_seconds = parse_duration_seconds(duration_raw)
                description = self._find_field(row, _DESCRIPTION_ALIASES) or ""
                custom_url = self._find_field(row, _URL_ALIASES)
                url = (
                    custom_url.strip()
                    if custom_url and custom_url.strip()
                    else f"https://www.youtube.com/watch?v={video_id}"
                )

                try:
                    video_item = VideoItem(
                        video_id=video_id,
                        title=title,
                        channel=channel,
                        duration_seconds=duration_seconds,
                        published_at=timestamp,
                        url=url,
                        description=description,
                        chapters=(),
                    )
                    items.append(video_item)
                    seen_in_archive.add(video_id)
                except ValueError as exc:
                    logger.warning(
                        "Validation failed for row %d (video_id %s): %s",
                        row_idx,
                        video_id,
                        exc,
                    )
                    self.skipped_records.append(
                        {"row_index": row_idx, "error": str(exc), "row": row}
                    )

        return items

    def _find_field(self, row: dict[str, str], aliases: tuple[str, ...]) -> str | None:
        """Find value by matching field names case-insensitively against aliases."""
        for k, v in row.items():
            if k and k.strip().lower() in aliases:
                val = v.strip() if v else ""
                if val:
                    return val
        return None

    def _iter_csv_rows(self, stream: io.TextIOBase) -> Iterator[tuple[int, dict[str, str]]]:
        """Yield 1-indexed row numbers and row dictionaries using DictReader."""
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            return

        # Normalize fieldnames to strip surrounding whitespace
        reader.fieldnames = [f.strip() if f else "" for f in reader.fieldnames]

        yield from enumerate(reader, start=2)

    def _iter_csv_streams(self) -> Iterator[io.TextIOBase]:
        """Yield readable text streams from file path, zip archive, or in-memory buffer."""
        # 1. In-memory TextIOBase
        if isinstance(self.source, io.TextIOBase):
            self.source.seek(0)
            yield self.source
            return

        # 2. BytesIO or raw bytes
        if isinstance(self.source, (io.BytesIO, bytes)):
            raw_bytes = (
                self.source.getvalue() if isinstance(self.source, io.BytesIO) else self.source
            )
            if zipfile.is_zipfile(io.BytesIO(raw_bytes)):
                with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                    for name in zf.namelist():
                        if name.lower().endswith(".csv") and not name.startswith("__MACOSX"):
                            content = zf.read(name).decode("utf-8-sig", errors="replace")
                            yield io.StringIO(content)
                return
            yield io.StringIO(raw_bytes.decode("utf-8-sig", errors="replace"))
            return

        # 3. Path or string
        path_or_str = self.source
        if isinstance(path_or_str, str) and ("\n" in path_or_str or "\r" in path_or_str):
            # Raw CSV string content directly
            yield io.StringIO(path_or_str)
            return

        path = Path(path_or_str)
        if not path.exists():
            # If string without newlines was given but file doesn't exist, treat as CSV text
            if isinstance(path_or_str, str):
                yield io.StringIO(path_or_str)
                return
            raise FileNotFoundError(f"Source file not found: {path}")

        # Directory: find all CSV files
        if path.is_dir():
            for csv_file in sorted(path.rglob("*.csv")):
                with open(csv_file, encoding="utf-8-sig", errors="replace") as f:
                    yield io.StringIO(f.read())
            return

        # ZIP archive
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".csv") and not name.startswith("__MACOSX"):
                        content = zf.read(name).decode("utf-8-sig", errors="replace")
                        yield io.StringIO(content)
            return

        # Single file
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            yield io.StringIO(f.read())


# Alias as per architecture documentation
TakeoutArchiveAdapter = TakeoutCsvSource
