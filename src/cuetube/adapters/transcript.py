"""YouTube subtitle/caption retrieval, chapter marker parsing, and transcript sampling adapter."""

import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from cuetube.domain.models import ChapterMarker, Transcript, TranscriptSegment, VideoItem

logger = logging.getLogger(__name__)

# Regular expressions for chapter parsing
_TIMESTAMP_LINE_REGEX = re.compile(
    r"""
    ^\s*
    (?:(?:[#•*\-–—]+|\d+[\.\)])\s*)?    # Optional list bullet like '1.' or '•'
    [\[\(]?                             # Optional opening bracket
    (?:(?P<hours>\d{1,2}):)?            # Optional hours (H or HH)
    (?P<minutes>\d{1,2}):               # Minutes (M or MM)
    (?P<seconds>\d{2})                  # Seconds (SS)
    [\]\)]?                             # Optional closing bracket
    [\s\-–—|:]*                         # Separator between timestamp and title
    (?P<title>[^\r\n]+?)                # Chapter title
    \s*$
    """,
    re.VERBOSE | re.MULTILINE,
)

_TIMESTAMP_TRAILING_REGEX = re.compile(
    r"""
    ^\s*
    (?P<title>[^\r\n]+?)                # Chapter title first
    [\s\-–—|:]+                         # Separator
    [\[\(]?                             # Optional opening bracket
    (?:(?P<hours>\d{1,2}):)?            # Optional hours (H or HH)
    (?P<minutes>\d{1,2}):               # Minutes (M or MM)
    (?P<seconds>\d{2})                  # Seconds (SS)
    [\]\)]?                             # Optional closing bracket
    \s*$
    """,
    re.VERBOSE | re.MULTILINE,
)

# Reject lines mentioning AM/PM or dollar values as false positives
_IGNORE_FALSE_POSITIVE = re.compile(r"\b(?:am|pm|a\.m\.|p\.m\.|o'clock|\$)\b", re.IGNORECASE)


def parse_chapter_markers(
    description: str,
    total_duration_seconds: int | None = None,
) -> tuple[ChapterMarker, ...]:
    """Parse chapter markers and timestamps from video description text."""
    if not description or not description.strip():
        return ()

    raw_chapters: list[tuple[int, str]] = []

    for line in description.splitlines():
        line_clean = line.strip()
        if not line_clean or _IGNORE_FALSE_POSITIVE.search(line_clean):
            continue

        match = _TIMESTAMP_LINE_REGEX.match(line_clean)
        if not match:
            match = _TIMESTAMP_TRAILING_REGEX.match(line_clean)

        if match:
            parts = match.groupdict()
            h = int(parts.get("hours") or 0)
            m = int(parts["minutes"])
            s = int(parts["seconds"])
            title = parts["title"].strip(" -–—|:[]()\"'")
            if title:
                start_sec = h * 3600 + m * 60 + s
                raw_chapters.append((start_sec, title))

    if not raw_chapters:
        return ()

    # Deduplicate by start timestamp and sort
    seen_starts: set[int] = set()
    unique_chapters: list[tuple[int, str]] = []
    for start_sec, title in sorted(raw_chapters, key=lambda x: x[0]):
        if start_sec not in seen_starts:
            seen_starts.add(start_sec)
            unique_chapters.append((start_sec, title))

    # Calculate end_seconds for each chapter
    chapter_markers: list[ChapterMarker] = []
    for idx, (start_sec, title) in enumerate(unique_chapters):
        if idx + 1 < len(unique_chapters):
            end_sec: int | None = unique_chapters[idx + 1][0]
        elif total_duration_seconds is not None and total_duration_seconds >= start_sec:
            end_sec = total_duration_seconds
        else:
            end_sec = None

        try:
            marker = ChapterMarker(title=title, start_seconds=start_sec, end_seconds=end_sec)
            chapter_markers.append(marker)
        except ValueError as exc:
            logger.debug("Skipping invalid chapter marker %s (%d): %s", title, start_sec, exc)

    return tuple(chapter_markers)


def sample_transcript(
    text: str,
    segments: tuple[TranscriptSegment, ...] = (),
    chapters: tuple[ChapterMarker, ...] = (),
    max_words: int = 2000,
    intro_words: int = 1500,
    outro_words: int = 500,
) -> tuple[str, tuple[TranscriptSegment, ...], bool]:
    """Sample long transcripts into opening and closing segments to respect token budgets."""
    words = text.split()
    if len(words) <= max_words:
        return text, segments, False

    # Extract intro and outro words
    intro_slice = words[:intro_words]
    outro_slice = words[-outro_words:]
    omitted_count = len(words) - len(intro_slice) - len(outro_slice)

    chapter_summary = ""
    if chapters:
        chapter_lines = [f"- {c.start_seconds}s: {c.title}" for c in chapters]
        chapter_summary = "\n\nChapters:\n" + "\n".join(chapter_lines)

    delimiter = (
        f"\n\n[... Transcript sampled: {omitted_count} words omitted ...]{chapter_summary}\n\n"
    )
    sampled_text = f"{' '.join(intro_slice)}{delimiter}{' '.join(outro_slice)}"

    # Sample corresponding timed segments if present
    sampled_segments: list[TranscriptSegment] = []
    if segments:
        # Intro segments
        current_words = 0
        intro_end_idx = 0
        for idx, seg in enumerate(segments):
            seg_word_count = len(seg.text.split())
            if current_words + seg_word_count > intro_words and idx > 0:
                intro_end_idx = idx
                break
            current_words += seg_word_count
            sampled_segments.append(seg)
        else:
            intro_end_idx = len(segments)

        # Outro segments
        outro_accumulator: list[TranscriptSegment] = []
        current_words = 0
        for seg in reversed(segments[intro_end_idx:]):
            seg_word_count = len(seg.text.split())
            if current_words + seg_word_count > outro_words and outro_accumulator:
                break
            current_words += seg_word_count
            outro_accumulator.append(seg)

        sampled_segments.extend(reversed(outro_accumulator))

    return sampled_text, tuple(sampled_segments), True


def resolve_caption_track(
    tracks: list[dict[str, Any]],
    preferred_languages: tuple[str, ...] = ("en", "en-US", "en-GB"),
) -> dict[str, Any] | None:
    """Resolve caption track prioritizing manual over auto-generated in preferred language order."""
    if not tracks:
        return None

    # Normalization helper
    def norm_lang(lang: str) -> str:
        return lang.strip().lower()

    norm_prefs = [norm_lang(p) for p in preferred_languages]

    # Partition into manual and ASR (auto-generated) tracks
    manual_tracks: list[dict[str, Any]] = []
    asr_tracks: list[dict[str, Any]] = []

    for t in tracks:
        is_asr = t.get("kind") == "asr"
        if is_asr:
            asr_tracks.append(t)
        else:
            manual_tracks.append(t)

    # 1. Preferred manual track in preference order
    for pref in norm_prefs:
        for t in manual_tracks:
            code = norm_lang(t.get("languageCode", ""))
            if code == pref or code.startswith(f"{pref}-"):
                return t

    # 2. Preferred auto-generated (ASR) track in preference order
    for pref in norm_prefs:
        for t in asr_tracks:
            code = norm_lang(t.get("languageCode", ""))
            if code == pref or code.startswith(f"{pref}-"):
                return t

    # 3. Any manual track
    if manual_tracks:
        return manual_tracks[0]

    # 4. Any auto-generated track
    if asr_tracks:
        return asr_tracks[0]

    return tracks[0]


def parse_timed_text(content: str | bytes) -> tuple[TranscriptSegment, ...]:
    if isinstance(content, bytes):
        text_content = content.decode("utf-8", errors="replace")
    else:
        text_content = content
    trimmed = text_content.strip()
    if not trimmed:
        return ()

    segments: list[TranscriptSegment] = []

    # Try JSON3 parsing first
    if trimmed.startswith("{") and trimmed.endswith("}"):
        try:
            data = json.loads(trimmed)
            for event in data.get("events", []):
                t_start = float(event.get("tStartMs", 0)) / 1000.0
                d_dur = float(event.get("dDurationMs", 0)) / 1000.0
                segs = event.get("segs", [])
                text_part = "".join(s.get("utf8", "") for s in segs if isinstance(s, dict)).strip()
                if text_part:
                    clean_text = html.unescape(text_part).replace("\n", " ")
                    try:
                        segments.append(
                            TranscriptSegment(
                                start_seconds=t_start,
                                duration_seconds=d_dur,
                                text=clean_text,
                            )
                        )
                    except ValueError:
                        continue
            if segments:
                return tuple(segments)
        except json.JSONDecodeError:
            pass

    # Try XML parsing
    try:
        root = ET.fromstring(trimmed)
        for elem in root.findall(".//text"):
            raw_start = elem.get("start")
            raw_dur = elem.get("dur", "0")
            if raw_start is not None:
                start_s = float(raw_start)
                dur_s = float(raw_dur) if raw_dur else 0.0
                txt = html.unescape(elem.text or "").strip().replace("\n", " ")
                if txt:
                    try:
                        segments.append(
                            TranscriptSegment(
                                start_seconds=start_s,
                                duration_seconds=dur_s,
                                text=txt,
                            )
                        )
                    except ValueError:
                        continue
    except ET.ParseError:
        pass

    return tuple(segments)


class YouTubeTranscriptProvider:
    """TranscriptProvider fetching YouTube subtitles with sampling and metadata fallbacks."""

    def __init__(
        self,
        preferred_languages: tuple[str, ...] = ("en", "en-US", "en-GB"),
        max_words: int = 2000,
        intro_words: int = 1500,
        outro_words: int = 500,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
        innertube_client_name: str = "WEB",
        innertube_client_version: str = "2.20240101.00.00",
    ) -> None:
        self.preferred_languages = preferred_languages
        self.max_words = max_words
        self.intro_words = intro_words
        self.outro_words = outro_words
        self.http_transport = http_transport
        self.innertube_client_name = innertube_client_name
        self.innertube_client_version = innertube_client_version

    def extract_transcript(
        self,
        video_id: str,
        title: str = "",
        description: str = "",
        duration_seconds: int | None = None,
    ) -> Transcript:
        """Extract subtitles, sample long content, parse chapters, and fall back gracefully."""
        # 1. Parse chapter markers from description
        chapters = parse_chapter_markers(description, total_duration_seconds=duration_seconds)

        # 2. Attempt caption acquisition
        caption_tracks = self._fetch_caption_tracks(video_id)
        if caption_tracks:
            chosen_track = resolve_caption_track(caption_tracks, self.preferred_languages)
            if chosen_track and chosen_track.get("baseUrl"):
                base_url = chosen_track["baseUrl"]
                is_asr = chosen_track.get("kind") == "asr"
                lang = chosen_track.get("languageCode", "en")

                timed_content = self._fetch_timed_text(base_url)
                if timed_content:
                    segments = parse_timed_text(timed_content)
                    if segments:
                        full_text = " ".join(s.text for s in segments)
                        sampled_text, sampled_segments, is_sampled = sample_transcript(
                            text=full_text,
                            segments=segments,
                            chapters=chapters,
                            max_words=self.max_words,
                            intro_words=self.intro_words,
                            outro_words=self.outro_words,
                        )
                        return Transcript(
                            video_id=video_id,
                            text=sampled_text,
                            segments=sampled_segments,
                            is_sampled=is_sampled,
                            is_fallback=False,
                            is_generated=is_asr,
                            language=lang,
                            chapters=chapters,
                        )

        # 3. Graceful fallback structure when captions are absent or unavailable
        return self._build_fallback_transcript(
            video_id=video_id,
            title=title,
            description=description,
            chapters=chapters,
        )

    def _fetch_caption_tracks(self, video_id: str) -> list[dict[str, Any]]:
        """Query InnerTube player API to retrieve available caption tracks."""
        url = "https://www.youtube.com/youtubei/v1/player"
        body = {
            "videoId": video_id,
            "context": {
                "client": {
                    "clientName": self.innertube_client_name,
                    "clientVersion": self.innertube_client_version,
                    "hl": "en",
                }
            },
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            method="POST",
        )

        try:
            if self.http_transport:
                resp_data = self.http_transport(req)
            else:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))

            if isinstance(resp_data, dict):
                captions = resp_data.get("captions", {})
                tracklist = captions.get("playerCaptionsTracklistRenderer", {})
                tracks = tracklist.get("captionTracks", [])
                if isinstance(tracks, list):
                    return tracks
        except Exception as exc:
            logger.debug("InnerTube caption lookup failed for video %s: %s", video_id, exc)

        return []

    def _fetch_timed_text(self, base_url: str) -> str:
        """Fetch timedtext content from caption baseUrl requesting json3 format."""
        # Append fmt=json3 query parameter if not present
        parsed = urllib.parse.urlparse(base_url)
        q = urllib.parse.parse_qs(parsed.query)
        if "fmt" not in q:
            q["fmt"] = ["json3"]
        new_query = urllib.parse.urlencode(q, doseq=True)
        fetch_url = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
        )

        req = urllib.request.Request(
            fetch_url,
            headers={"User-Agent": "Mozilla/5.0"},
            method="GET",
        )

        try:
            if self.http_transport:
                resp = self.http_transport(req)
                if isinstance(resp, (str, bytes)):
                    return resp if isinstance(resp, str) else resp.decode("utf-8", errors="replace")
                if isinstance(resp, dict):
                    return json.dumps(resp)
            else:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.debug("Failed to download timed text from %s: %s", fetch_url, exc)

        return ""

    def _build_fallback_transcript(
        self,
        video_id: str,
        title: str,
        description: str,
        chapters: tuple[ChapterMarker, ...],
    ) -> Transcript:
        """Construct a valid fallback transcript from metadata without halting processing."""
        lines: list[str] = []
        if title and title.strip():
            lines.append(f"Title: {title.strip()}")
        if chapters:
            chapter_lines = [f"- {c.start_seconds}s: {c.title}" for c in chapters]
            lines.append("Chapters:\n" + "\n".join(chapter_lines))
        if description and description.strip():
            lines.append(f"Description:\n{description.strip()}")

        fallback_text = "\n\n".join(lines) if lines else f"YouTube Video {video_id}"
        return Transcript(
            video_id=video_id,
            text=fallback_text,
            segments=(),
            is_sampled=False,
            is_fallback=True,
            is_generated=False,
            language="en",
            chapters=chapters,
        )


# Alias per architecture documentation
OfficialTranscriptAdapter = YouTubeTranscriptProvider


def fetch_video_oembed(
    video_id: str,
    http_transport: Callable[[urllib.request.Request], Any] | None = None,
) -> dict[str, str]:
    """Fetch keyless public video metadata (title, author/channel) via YouTube oEmbed."""
    url = (
        f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        if http_transport:
            resp_data = http_transport(req)
            if isinstance(resp_data, (str, bytes)):
                data = json.loads(resp_data)
            elif isinstance(resp_data, dict):
                data = resp_data
            else:
                data = {}
        else:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

        return {
            "title": data.get("title", ""),
            "channel": data.get("author_name", ""),
        }
    except Exception as exc:
        logger.debug("oEmbed lookup failed for %s: %s", video_id, exc)
        return {}


def hydrate_video_item(
    item: VideoItem,
    http_transport: Callable[[urllib.request.Request], Any] | None = None,
) -> VideoItem:
    """Hydrate missing/placeholder title and channel via zero-auth YouTube oEmbed."""
    needs_title = not item.title or item.title.startswith("YouTube Video ")
    needs_channel = not item.channel or item.channel == "Unknown"

    if not (needs_title or needs_channel):
        return item

    meta = fetch_video_oembed(item.video_id, http_transport=http_transport)
    updates: dict[str, Any] = {}
    if needs_title and meta.get("title"):
        updates["title"] = meta["title"]
    if needs_channel and meta.get("channel"):
        updates["channel"] = meta["channel"]

    if updates:
        return replace(item, **updates)
    return item
