"""Unit tests for YouTube transcript extraction, chapter parsing, and sampling adapter."""

import json
import unittest
import urllib.error
import urllib.request

from cuetube.adapters.transcript import (
    OfficialTranscriptAdapter,
    YouTubeTranscriptProvider,
    parse_chapter_markers,
    parse_timed_text,
    resolve_caption_track,
    sample_transcript,
)
from cuetube.domain.models import ChapterMarker, TranscriptSegment


class TestTranscriptAdapter(unittest.TestCase):
    """Test suite for transcript extraction, chapter parsing, and sampling."""

    def test_parse_chapter_markers_standard_formats(self) -> None:
        description = """
        Welcome to this distributed systems lecture.

        00:00 Introduction
        02:15 System Architecture
        10:45 Seam Design & Testing
        1:05:30 Final Summary
        """
        chapters = parse_chapter_markers(description, total_duration_seconds=4200)
        self.assertEqual(len(chapters), 4)

        # 00:00 Introduction
        self.assertEqual(chapters[0].title, "Introduction")
        self.assertEqual(chapters[0].start_seconds, 0)
        self.assertEqual(chapters[0].end_seconds, 135)

        # 02:15 System Architecture
        self.assertEqual(chapters[1].title, "System Architecture")
        self.assertEqual(chapters[1].start_seconds, 135)
        self.assertEqual(chapters[1].end_seconds, 645)

        # 10:45 Seam Design & Testing
        self.assertEqual(chapters[2].title, "Seam Design & Testing")
        self.assertEqual(chapters[2].start_seconds, 645)
        self.assertEqual(chapters[2].end_seconds, 3930)

        # 1:05:30 Final Summary
        self.assertEqual(chapters[3].title, "Final Summary")
        self.assertEqual(chapters[3].start_seconds, 3930)
        self.assertEqual(chapters[3].end_seconds, 4200)

    def test_parse_chapter_markers_bracketed_and_delimiters(self) -> None:
        description = """
        [0:00] - Getting Started
        [05:30] | High Cognitive Demand
        15:00: Scheduling Buffer
        """
        chapters = parse_chapter_markers(description)
        self.assertEqual(len(chapters), 3)
        self.assertEqual(chapters[0].title, "Getting Started")
        self.assertEqual(chapters[0].start_seconds, 0)
        self.assertEqual(chapters[0].end_seconds, 330)

        self.assertEqual(chapters[1].title, "High Cognitive Demand")
        self.assertEqual(chapters[1].start_seconds, 330)
        self.assertEqual(chapters[1].end_seconds, 900)

        self.assertEqual(chapters[2].title, "Scheduling Buffer")
        self.assertEqual(chapters[2].start_seconds, 900)
        self.assertIsNone(chapters[2].end_seconds)

    def test_parse_chapter_markers_trailing_timestamp_format(self) -> None:
        description = """
        Overview - 0:00
        Deep Dive - 10:00
        Conclusion - 20:00
        """
        chapters = parse_chapter_markers(description, total_duration_seconds=1500)
        self.assertEqual(len(chapters), 3)
        self.assertEqual(chapters[0].title, "Overview")
        self.assertEqual(chapters[0].start_seconds, 0)
        self.assertEqual(chapters[1].title, "Deep Dive")
        self.assertEqual(chapters[1].start_seconds, 600)
        self.assertEqual(chapters[2].title, "Conclusion")
        self.assertEqual(chapters[2].start_seconds, 1200)
        self.assertEqual(chapters[2].end_seconds, 1500)

    def test_parse_chapter_markers_empty_and_false_positives(self) -> None:
        # Empty description
        self.assertEqual(parse_chapter_markers(""), ())
        self.assertEqual(
            parse_chapter_markers("Just a regular video description with no timestamps."), ()
        )

        # False positive rejection (e.g. AM/PM times or dollar amounts)
        false_positive_desc = """
        Join our live stream tomorrow at 4:00 pm EST!
        Donation goal: $10:00
        """
        self.assertEqual(parse_chapter_markers(false_positive_desc), ())

    def test_sample_transcript_short_content_not_sampled(self) -> None:
        text = "This is a short video transcript under two thousand words explaining consensus."
        seg = TranscriptSegment(start_seconds=0.0, duration_seconds=10.0, text=text)
        sampled_text, sampled_segs, is_sampled = sample_transcript(
            text=text,
            segments=(seg,),
            max_words=2000,
        )
        self.assertFalse(is_sampled)
        self.assertEqual(sampled_text, text)
        self.assertEqual(len(sampled_segs), 1)

    def test_sample_transcript_long_content_sampled(self) -> None:
        # Create 2500 words
        intro_part = " ".join([f"intro{i}" for i in range(1500)])
        middle_part = " ".join([f"middle{i}" for i in range(500)])
        outro_part = " ".join([f"outro{i}" for i in range(500)])
        full_text = f"{intro_part} {middle_part} {outro_part}"

        chapters = (
            ChapterMarker(title="Part 1", start_seconds=0, end_seconds=300),
            ChapterMarker(title="Part 2", start_seconds=300, end_seconds=600),
        )

        sampled_text, _, is_sampled = sample_transcript(
            text=full_text,
            chapters=chapters,
            max_words=2000,
            intro_words=1500,
            outro_words=500,
        )
        self.assertTrue(is_sampled)
        self.assertIn("intro0", sampled_text)
        self.assertIn("intro1499", sampled_text)
        self.assertIn("outro0", sampled_text)
        self.assertIn("outro499", sampled_text)
        self.assertNotIn("middle250", sampled_text)
        self.assertIn("500 words omitted", sampled_text)
        self.assertIn("Chapters:", sampled_text)
        self.assertIn("- 0s: Part 1", sampled_text)
        self.assertIn("- 300s: Part 2", sampled_text)

    def test_resolve_caption_track_prioritizes_manual_over_asr(self) -> None:
        tracks = [
            {
                "languageCode": "en",
                "kind": "asr",
                "baseUrl": "https://timedtext.youtube.com/asr",
            },
            {
                "languageCode": "en",
                "baseUrl": "https://timedtext.youtube.com/manual",
            },
        ]
        chosen = resolve_caption_track(tracks, preferred_languages=("en",))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["baseUrl"], "https://timedtext.youtube.com/manual")

    def test_resolve_caption_track_fallback_to_asr_when_no_manual(self) -> None:
        tracks = [
            {
                "languageCode": "en",
                "kind": "asr",
                "baseUrl": "https://timedtext.youtube.com/asr",
            },
        ]
        chosen = resolve_caption_track(tracks, preferred_languages=("en",))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["baseUrl"], "https://timedtext.youtube.com/asr")

    def test_resolve_caption_track_language_order_and_empty(self) -> None:
        tracks = [
            {"languageCode": "es", "baseUrl": "https://timedtext.youtube.com/es"},
            {"languageCode": "en", "baseUrl": "https://timedtext.youtube.com/en"},
        ]
        chosen = resolve_caption_track(tracks, preferred_languages=("en", "es"))
        self.assertEqual(chosen["baseUrl"], "https://timedtext.youtube.com/en")

        self.assertIsNone(resolve_caption_track([]))

    def test_parse_timed_text_json3(self) -> None:
        json_data = json.dumps(
            {
                "events": [
                    {
                        "tStartMs": 1000,
                        "dDurationMs": 2500,
                        "segs": [{"utf8": "Hello &amp; welcome"}],
                    },
                    {
                        "tStartMs": 3500,
                        "dDurationMs": 1500,
                        "segs": [{"utf8": "to CueTube"}],
                    },
                ]
            }
        )
        segs = parse_timed_text(json_data)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].start_seconds, 1.0)
        self.assertEqual(segs[0].duration_seconds, 2.5)
        self.assertEqual(segs[0].text, "Hello & welcome")
        self.assertEqual(segs[1].start_seconds, 3.5)
        self.assertEqual(segs[1].text, "to CueTube")

    def test_parse_timed_text_xml(self) -> None:
        xml_data = """<?xml version="1.0" encoding="utf-8" ?>
        <transcript>
            <text start="2.4" dur="3.1">Distributed systems are complex</text>
            <text start="5.5" dur="4.0">Let&apos;s simplify them</text>
        </transcript>
        """
        segs = parse_timed_text(xml_data)
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0].start_seconds, 2.4)
        self.assertAlmostEqual(segs[0].duration_seconds, 3.1)
        self.assertEqual(segs[0].text, "Distributed systems are complex")
        self.assertEqual(segs[1].text, "Let's simplify them")

    def test_youtube_transcript_provider_full_extraction_success(self) -> None:
        def mock_transport(req: urllib.request.Request) -> dict:
            url = req.full_url
            if "youtubei/v1/player" in url:
                return {
                    "captions": {
                        "playerCaptionsTracklistRenderer": {
                            "captionTracks": [
                                {
                                    "baseUrl": "https://timedtext.youtube.com/subtitles?v=vid-1",
                                    "languageCode": "en",
                                }
                            ]
                        }
                    }
                }
            if "timedtext" in url:
                return {
                    "events": [
                        {
                            "tStartMs": 0,
                            "dDurationMs": 2000,
                            "segs": [{"utf8": "Introduction to Raft"}],
                        },
                        {
                            "tStartMs": 2000,
                            "dDurationMs": 3000,
                            "segs": [{"utf8": "Consensus algorithm breakdown"}],
                        },
                    ]
                }
            return {}

        provider = YouTubeTranscriptProvider(http_transport=mock_transport)
        desc = "0:00 Intro\n0:02 Algorithm"
        transcript = provider.extract_transcript(
            video_id="vid-1",
            title="Raft Consensus",
            description=desc,
            duration_seconds=5,
        )

        self.assertEqual(transcript.video_id, "vid-1")
        self.assertFalse(transcript.is_fallback)
        self.assertFalse(transcript.is_generated)
        self.assertFalse(transcript.is_sampled)
        self.assertEqual(transcript.language, "en")
        self.assertEqual(transcript.text, "Introduction to Raft Consensus algorithm breakdown")
        self.assertEqual(len(transcript.segments), 2)
        self.assertEqual(len(transcript.chapters), 2)
        self.assertEqual(transcript.chapters[0].title, "Intro")

    def test_youtube_transcript_provider_auto_generated_subtitles(self) -> None:
        def mock_transport(req: urllib.request.Request) -> dict:
            url = req.full_url
            if "youtubei/v1/player" in url:
                return {
                    "captions": {
                        "playerCaptionsTracklistRenderer": {
                            "captionTracks": [
                                {
                                    "baseUrl": "https://timedtext.youtube.com/subtitles?v=vid-asr",
                                    "languageCode": "en",
                                    "kind": "asr",
                                }
                            ]
                        }
                    }
                }
            if "timedtext" in url:
                return {
                    "events": [
                        {
                            "tStartMs": 0,
                            "dDurationMs": 1000,
                            "segs": [{"utf8": "ASR generated text"}],
                        }
                    ]
                }
            return {}

        provider = YouTubeTranscriptProvider(http_transport=mock_transport)
        transcript = provider.extract_transcript(video_id="vid-asr")
        self.assertEqual(transcript.video_id, "vid-asr")
        self.assertFalse(transcript.is_fallback)
        self.assertTrue(transcript.is_generated)
        self.assertEqual(transcript.text, "ASR generated text")

    def test_youtube_transcript_provider_graceful_fallback_no_captions(self) -> None:
        def mock_transport(req: urllib.request.Request) -> dict:
            if "youtubei/v1/player" in req.full_url:
                # Video has no captions available
                return {"captions": {}}
            return {}

        provider = YouTubeTranscriptProvider(http_transport=mock_transport)
        desc = "0:00 Intro\n05:00 Solo Track\nInstrumental chill track for programming."
        transcript = provider.extract_transcript(
            video_id="vid-nocap",
            title="Lofi Focus Beats",
            description=desc,
            duration_seconds=600,
        )

        self.assertEqual(transcript.video_id, "vid-nocap")
        self.assertTrue(transcript.is_fallback)
        self.assertFalse(transcript.is_generated)
        self.assertEqual(len(transcript.segments), 0)
        self.assertIn("Title: Lofi Focus Beats", transcript.text)
        self.assertIn("Chapters:", transcript.text)
        self.assertIn("- 0s: Intro", transcript.text)
        self.assertIn("Instrumental chill track", transcript.text)
        self.assertEqual(len(transcript.chapters), 2)

    def test_youtube_transcript_provider_network_error_fallback(self) -> None:
        def flaky_transport(req: urllib.request.Request) -> dict:
            raise urllib.error.URLError("Connection reset by peer")

        provider = YouTubeTranscriptProvider(http_transport=flaky_transport)
        transcript = provider.extract_transcript(
            video_id="vid-err",
            title="Broken Connection Video",
            description="Short desc",
        )

        # Extractor falls back gracefully without raising an exception!
        self.assertEqual(transcript.video_id, "vid-err")
        self.assertTrue(transcript.is_fallback)
        self.assertIn("Broken Connection Video", transcript.text)

    def test_official_transcript_adapter_alias(self) -> None:
        self.assertIs(OfficialTranscriptAdapter, YouTubeTranscriptProvider)


if __name__ == "__main__":
    unittest.main()
