"""Unit tests for Google Takeout CSV and ZIP ingestion adapter."""

import io
import unittest
import zipfile
from pathlib import Path

from cuetube.adapters.takeout import (
    TakeoutArchiveAdapter,
    TakeoutCsvSource,
    extract_video_id,
    parse_duration_seconds,
)


class TestTakeoutCsvSource(unittest.TestCase):
    """Test suite for TakeoutCsvSource adapter."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).parent / "fixtures"
        self.sample_file = self.fixtures_dir / "Coding-videos.csv"

    def test_extract_video_id_variants(self) -> None:
        # Standard ID with whitespace
        self.assertEqual(extract_video_id("  RPGpA5OLt7M  "), "RPGpA5OLt7M")
        # Leading dash
        self.assertEqual(extract_video_id(" -SdLQFmAZOk "), "-SdLQFmAZOk")
        # Standard watch URL
        self.assertEqual(
            extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        # Short URL
        self.assertEqual(
            extract_video_id("https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        # Shorts URL
        self.assertEqual(
            extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        # Embed URL
        self.assertEqual(
            extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        # Invalid / empty
        self.assertIsNone(extract_video_id(""))
        self.assertIsNone(extract_video_id("invalid id with spaces"))
        self.assertIsNone(extract_video_id("not-11-chars", strict=True))
        self.assertEqual(extract_video_id("not-11-chars", strict=False), "not-11-chars")
        self.assertIsNone(extract_video_id("https://google.com"))

    def test_parse_duration_seconds(self) -> None:
        self.assertEqual(parse_duration_seconds("1800"), 1800)
        self.assertEqual(parse_duration_seconds("01:30:00"), 5400)
        self.assertEqual(parse_duration_seconds("15:30"), 930)
        self.assertEqual(parse_duration_seconds(""), 0)
        self.assertEqual(parse_duration_seconds(None), 0)
        self.assertEqual(parse_duration_seconds("invalid"), 0)

    def test_ingest_real_user_takeout_fixture(self) -> None:
        if not self.sample_file.exists():
            self.skipTest("Sample fixture not found")

        source = TakeoutCsvSource(self.sample_file)
        videos = source.fetch_pending_videos()

        # 25 valid video rows in Coding-videos.csv
        self.assertEqual(len(videos), 25)
        self.assertEqual(len(source.skipped_records), 0)
        self.assertEqual(len(source.duplicate_records), 0)

        # Check first video
        self.assertEqual(videos[0].video_id, "RPGpA5OLt7M")
        self.assertEqual(videos[0].published_at, "2023-04-12T05:32:10+00:00")
        self.assertEqual(videos[0].url, "https://www.youtube.com/watch?v=RPGpA5OLt7M")
        self.assertEqual(videos[0].title, "YouTube Video RPGpA5OLt7M")
        self.assertEqual(videos[0].channel, "Unknown")

        # Check video that had leading whitespace and leading dash: " -SdLQFmAZOk"
        dash_video = next((v for v in videos if v.video_id == "-SdLQFmAZOk"), None)
        self.assertIsNotNone(dash_video)
        self.assertEqual(dash_video.published_at, "2024-12-20T20:08:44+00:00")

    def test_ingest_rich_metadata_csv(self) -> None:
        csv_data = """Video ID,Title,Channel Title,Duration,Playlist Video Creation Timestamp,URL
v1,System Design Deep Dive,Tech Academy,01:00:00,2026-01-01T00:00:00Z,https://youtube.com/watch?v=v1
v2,Python Tips,PyChannel,300,2026-01-02T00:00:00Z,https://youtube.com/watch?v=v2
"""
        source = TakeoutCsvSource(csv_data)
        videos = source.fetch_pending_videos()

        self.assertEqual(len(videos), 2)
        self.assertEqual(videos[0].title, "System Design Deep Dive")
        self.assertEqual(videos[0].channel, "Tech Academy")
        self.assertEqual(videos[0].duration_seconds, 3600)
        self.assertEqual(videos[1].duration_seconds, 300)

    def test_deduplication_intra_archive(self) -> None:
        csv_data = """Video ID,Playlist Video Creation Timestamp
RPGpA5OLt7M,2023-04-12T05:32:10+00:00
RPGpA5OLt7M,2023-04-12T05:32:10+00:00
_rwNTY5Mn40,2023-04-20T15:05:07+00:00
RPGpA5OLt7M,2023-04-12T05:32:10+00:00
"""
        source = TakeoutCsvSource(csv_data)
        videos = source.fetch_pending_videos()

        # 4 rows but only 2 unique IDs
        self.assertEqual(len(videos), 2)
        self.assertEqual(len(source.duplicate_records), 2)
        self.assertEqual(source.duplicate_records, ["RPGpA5OLt7M", "RPGpA5OLt7M"])
        self.assertEqual([v.video_id for v in videos], ["RPGpA5OLt7M", "_rwNTY5Mn40"])

    def test_deduplication_against_existing_ids(self) -> None:
        csv_data = """Video ID,Playlist Video Creation Timestamp
RPGpA5OLt7M,2023-04-12T05:32:10+00:00
_rwNTY5Mn40,2023-04-20T15:05:07+00:00
gb4EQQOsgQc,2023-04-21T18:42:15+00:00
"""
        existing = {"RPGpA5OLt7M"}
        source = TakeoutCsvSource(csv_data, existing_ids=existing)
        videos = source.fetch_pending_videos()

        self.assertEqual(len(videos), 2)
        self.assertNotIn("RPGpA5OLt7M", [v.video_id for v in videos])
        self.assertEqual(source.existing_filtered_records, ["RPGpA5OLt7M"])

    def test_malformed_and_incomplete_rows_resilience(self) -> None:
        csv_data = """Video ID,Playlist Video Creation Timestamp
RPGpA5OLt7M,2023-04-12T05:32:10+00:00
,,
   ,2023-05-01T00:00:00Z
invalid id with spaces,2023-05-01T00:00:00Z
_rwNTY5Mn40,2023-04-20T15:05:07+00:00
"""
        source = TakeoutCsvSource(csv_data)
        videos = source.fetch_pending_videos()

        # Only RPGpA5OLt7M and _rwNTY5Mn40 are valid
        self.assertEqual(len(videos), 2)
        # Empty rows skipped without recording error
        # Incomplete and invalid rows captured in skipped_records
        self.assertEqual(len(source.skipped_records), 2)
        self.assertIn("Missing or invalid video ID", source.skipped_records[0]["error"])
        self.assertIn("invalid id with spaces", source.skipped_records[1]["error"])

    def test_zip_archive_ingestion(self) -> None:
        zip_buffer = io.BytesIO()
        csv_content = (
            b"Video ID,Playlist Video Creation Timestamp\nRPGpA5OLt7M,2023-04-12T05:32:10+00:00\n"
        )

        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("YouTube and YouTube Music/playlists/Favorites.csv", csv_content)

        zip_buffer.seek(0)
        source = TakeoutCsvSource(zip_buffer)
        videos = source.fetch_pending_videos()

        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].video_id, "RPGpA5OLt7M")

    def test_alias_compatibility(self) -> None:
        self.assertIs(TakeoutArchiveAdapter, TakeoutCsvSource)


if __name__ == "__main__":
    unittest.main()
