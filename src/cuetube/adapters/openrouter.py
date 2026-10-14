"""OpenRouter schema-enforced LLM classification adapter."""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cuetube.domain.models import (
    CognitiveDemand,
    EngagementMode,
    Transcript,
    TriageMetadata,
    VideoItem,
)

logger = logging.getLogger(__name__)

# Valid destination playlists defined by system taxonomy
VALID_TARGET_PLAYLISTS = (
    "Tech-Tutorials",
    "Study-Queue",
    "Explanations-Queue",
    "Background-Audio",
    "Leisure-Backlog",
)


class ClassificationError(Exception):
    """Base exception for video classification failures."""


class OpenRouterAuthError(ClassificationError):
    """Raised when authentication with OpenRouter fails (HTTP 401/403)."""


class OpenRouterRateLimitError(ClassificationError):
    """Raised when OpenRouter rate limit is exhausted (HTTP 429)."""


class OpenRouterApiError(ClassificationError):
    """Raised when OpenRouter API encounters unhandled server errors (HTTP 5xx)."""


class ClassificationSchemaError(ClassificationError):
    """Raised when LLM response violates the structured TriageMetadata contract."""


@dataclass(frozen=True)
class OpenRouterConfig:
    """Configuration for OpenRouter LLM inference client."""

    api_key: str
    model: str = "google/gemini-2.5-flash"
    base_url: str = "https://openrouter.ai/api/v1/chat/completions"
    max_retries: int = 3
    initial_backoff_seconds: float = 1.0
    backoff_factor: float = 2.0
    timeout_seconds: float = 30.0
    site_url: str = "https://github.com/vyavahareyash/cuetube"
    app_name: str = "CueTube"

    @classmethod
    def from_env(cls, env_prefix: str = "OPENROUTER_") -> "OpenRouterConfig":
        """Load OpenRouter configuration from environment or .env file."""
        api_key = os.getenv(f"{env_prefix}API_KEY")
        model = os.getenv(f"{env_prefix}MODEL", "google/gemini-2.5-flash")
        base_url = os.getenv(
            f"{env_prefix}BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
        )

        if not api_key:
            env_file = Path(".env")
            if env_file.exists():
                with open(env_file, encoding="utf-8") as f:
                    for line in f:
                        line_str = line.strip()
                        if "=" in line_str and not line_str.startswith("#"):
                            k, v = line_str.split("=", 1)
                            clean_v = v.strip("\"'")
                            if k == f"{env_prefix}API_KEY":
                                api_key = clean_v
                            elif k == f"{env_prefix}MODEL":
                                model = clean_v
                            elif k == f"{env_prefix}BASE_URL":
                                base_url = clean_v

        if not api_key:
            raise OpenRouterAuthError(
                f"Missing OpenRouter API key. Set {env_prefix}API_KEY or configure .env."
            )

        return cls(api_key=api_key, model=model, base_url=base_url)


def _normalize_cognitive_demand(raw_val: str) -> CognitiveDemand:
    """Map string representation resiliently to CognitiveDemand enum."""
    val_clean = raw_val.strip().lower()
    for demand in CognitiveDemand:
        if val_clean == demand.value.lower() or val_clean == demand.name.lower():
            return demand

    # Substring heuristics
    if "very high" in val_clean:
        return CognitiveDemand.VERY_HIGH_ACTIVE
    if "high" in val_clean:
        return CognitiveDemand.HIGH_ACTIVE
    if "medium" in val_clean or "reflective" in val_clean:
        return CognitiveDemand.MEDIUM_REFLECTIVE
    if "very low" in val_clean:
        return CognitiveDemand.VERY_LOW_PASSIVE
    if "low" in val_clean or "passive" in val_clean:
        return CognitiveDemand.LOW_PASSIVE

    raise ClassificationSchemaError(f"Cannot map '{raw_val}' to CognitiveDemand enum.")


def _normalize_engagement_mode(raw_val: str) -> EngagementMode:
    """Map string representation resiliently to EngagementMode enum."""
    val_clean = raw_val.strip().lower()
    for mode in EngagementMode:
        if val_clean == mode.value.lower() or val_clean == mode.name.lower():
            return mode

    if "active" in val_clean:
        return EngagementMode.ACTIVE
    if "reflective" in val_clean:
        return EngagementMode.REFLECTIVE
    if "passive" in val_clean:
        return EngagementMode.PASSIVE

    raise ClassificationSchemaError(f"Cannot map '{raw_val}' to EngagementMode enum.")


def _normalize_target_playlist(raw: str, default: str = "Tech-Tutorials") -> str:
    """Normalize playlist string to a valid destination taxonomy playlist."""
    cleaned = raw.strip()
    for valid in VALID_TARGET_PLAYLISTS:
        if cleaned.lower() == valid.lower() or (
            cleaned.lower().replace("-", " ") == valid.lower().replace("-", " ")
        ):
            return valid
    for valid in VALID_TARGET_PLAYLISTS:
        if valid.lower() in cleaned.lower() or cleaned.lower() in valid.lower():
            return valid
    return default


def _infer_target_playlist(
    category: str,
    cognitive_demand: CognitiveDemand,
    engagement_mode: EngagementMode,
    title: str = "",
) -> str:
    """Infer destination playlist from cognitive signals when omitted or truncated."""
    combined = f"{category.lower()} {title.lower()}".strip()

    if cognitive_demand == CognitiveDemand.VERY_LOW_PASSIVE:
        return "Background-Audio"
    if any(
        k in combined
        for k in ("ambient", "lofi", "beats", "music", "soundtrack", "sleep", "soundscape")
    ):
        return "Background-Audio"

    if any(
        k in combined
        for k in (
            "tutorial",
            "code",
            "programming",
            "dev",
            "software",
            "python",
            "javascript",
            "react",
            "ai tools",
            "chatgpt",
            "build",
            "api",
        )
    ):
        return "Tech-Tutorials"
    if (
        cognitive_demand in (CognitiveDemand.VERY_HIGH_ACTIVE, CognitiveDemand.HIGH_ACTIVE)
        and engagement_mode == EngagementMode.ACTIVE
    ):
        if any(
            k in combined
            for k in ("math", "physics", "lecture", "course", "academic", "theory", "paper")
        ):
            return "Study-Queue"
        return "Tech-Tutorials"

    if any(
        k in combined
        for k in (
            "study",
            "lecture",
            "course",
            "academic",
            "math",
            "history",
            "philosophy",
            "science",
            "theory",
        )
    ):
        return "Study-Queue"

    if (
        cognitive_demand == CognitiveDemand.MEDIUM_REFLECTIVE
        or engagement_mode == EngagementMode.REFLECTIVE
    ):
        return "Explanations-Queue"
    if any(k in combined for k in ("explainer", "overview", "essay", "analysis", "how", "why")):
        return "Explanations-Queue"

    if cognitive_demand in (CognitiveDemand.LOW_PASSIVE, CognitiveDemand.VERY_LOW_PASSIVE):
        return "Leisure-Backlog"

    return "Tech-Tutorials"


def _demand_to_mode(demand: CognitiveDemand) -> EngagementMode:
    """Map cognitive demand to its canonical engagement mode."""
    if demand in (CognitiveDemand.VERY_HIGH_ACTIVE, CognitiveDemand.HIGH_ACTIVE):
        return EngagementMode.ACTIVE
    if demand == CognitiveDemand.MEDIUM_REFLECTIVE:
        return EngagementMode.REFLECTIVE
    return EngagementMode.PASSIVE


def _mode_to_demand(mode: EngagementMode) -> CognitiveDemand:
    """Map engagement mode to a default cognitive demand level."""
    if mode == EngagementMode.ACTIVE:
        return CognitiveDemand.HIGH_ACTIVE
    if mode == EngagementMode.REFLECTIVE:
        return CognitiveDemand.MEDIUM_REFLECTIVE
    return CognitiveDemand.LOW_PASSIVE


def _playlist_to_demand_mode(playlist: str) -> tuple[CognitiveDemand, EngagementMode]:
    """Derive cognitive demand and engagement mode from target playlist taxonomy."""
    pl = playlist.lower()
    if "study" in pl:
        return (CognitiveDemand.HIGH_ACTIVE, EngagementMode.ACTIVE)
    if "tutorial" in pl or "tech" in pl:
        return (CognitiveDemand.HIGH_ACTIVE, EngagementMode.ACTIVE)
    if "explanation" in pl:
        return (CognitiveDemand.MEDIUM_REFLECTIVE, EngagementMode.REFLECTIVE)
    if "audio" in pl or "background" in pl:
        return (CognitiveDemand.VERY_LOW_PASSIVE, EngagementMode.PASSIVE)
    return (CognitiveDemand.LOW_PASSIVE, EngagementMode.PASSIVE)


def _repair_json(raw: str) -> dict[str, Any] | None:
    """Attempt heuristic repair on truncated or malformed JSON strings."""
    if not raw or not raw.strip():
        return None

    cleaned = raw.strip()
    code_fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)(?:```|$)", cleaned)
    if code_fence_match:
        cleaned = code_fence_match.group(1).strip()

    # 1. Direct parse attempt
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # Find the first opening brace
    start_idx = cleaned.find("{")
    if start_idx == -1:
        return None

    s = cleaned[start_idx:]

    # 2. Repair unclosed quotes and braces
    in_str = False
    escape = False
    stack: list[str] = []

    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch in ("{", "["):
                stack.append("}" if ch == "{" else "]")
            elif ch in ("}", "]"):
                if stack and stack[-1] == ch:
                    stack.pop()

    repaired = s
    if in_str:
        repaired += '"'

    repaired_tail = repaired.rstrip()
    repaired_tail = re.sub(r",\s*$", "", repaired_tail)
    repaired_tail = re.sub(r',\s*"[^"]*"\s*:\s*$', "", repaired_tail)
    repaired_tail = re.sub(r',\s*"[^"]*"\s*$', "", repaired_tail)

    while stack:
        repaired_tail += stack.pop()

    try:
        data = json.loads(repaired_tail)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # 3. Regex property extraction fallback if bracket healing alone didn't parse
    extracted: dict[str, Any] = {}
    m_vid = re.search(r'"video_id"\s*:\s*"([^"]+)"', cleaned)
    if m_vid:
        extracted["video_id"] = m_vid.group(1)

    m_cat = re.search(r'"category"\s*:\s*"([^"]+)"', cleaned)
    if m_cat:
        extracted["category"] = m_cat.group(1)

    m_cog = re.search(r'"cognitive_demand"\s*:\s*"([^"]+)"', cleaned)
    if m_cog:
        extracted["cognitive_demand"] = m_cog.group(1)

    m_eng = re.search(r'"engagement_mode"\s*:\s*"([^"]+)"', cleaned)
    if m_eng:
        extracted["engagement_mode"] = m_eng.group(1)

    m_dur = re.search(r'"recommended_session_duration_minutes"\s*:\s*(\d+)', cleaned)
    if m_dur:
        extracted["recommended_session_duration_minutes"] = int(m_dur.group(1))

    m_sum = re.search(r'"summary"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"?', cleaned)
    if m_sum:
        extracted["summary"] = m_sum.group(1).rstrip('"')

    m_pl = re.search(r'"target_playlist"\s*:\s*"([^"]+)"', cleaned)
    if m_pl:
        extracted["target_playlist"] = m_pl.group(1)

    if extracted and any(k in extracted for k in ("cognitive_demand", "category", "summary")):
        return extracted

    return None


def _build_system_prompt() -> str:
    """Construct structured taxonomy and role instruction for video triage."""
    return """You are the autonomous video classification engine for CueTube.
Your role is to analyze a video's metadata, chapters, and transcript, and classify it.

### Two-Dimensional Taxonomy:
1. Cognitive Demand & Engagement Mode:
   - "Very High (active)" / "Active": Technical deep-dives, hard programming courses.
   - "High (active)" / "Active": Academic lectures, math/science theory, formal courses.
   - "Medium (reflective)" / "Reflective": Conceptual explainers, architectural overviews.
   - "Low (passive)" / "Passive": Leisure, entertainment, general commentary, casual vlogs.
   - "Very Low (passive)" / "Passive": Ambient soundscapes, focus music, lofi beats.

2. Destination Playlists:
   - "Tech-Tutorials": Practical programming, tutorials, hands-on software development.
   - "Study-Queue": Academic lectures, mathematics, theory, formal courses.
   - "Explanations-Queue": Conceptual explainers, architectural discussions, tech talks.
   - "Background-Audio": Ambient music, lofi beats, focus soundtracks.
   - "Leisure-Backlog": Casual entertainment, comedy, leisure vlogs.

### Output Contract:
You MUST output a valid JSON object matching this schema exactly:
{
  "video_id": "<string matching input video_id>",
  "category": "<short descriptive topic tag>",
  "cognitive_demand": "<Very High (active) | High (active) | Medium (reflective) | ...>",
  "engagement_mode": "<Active | Reflective | Passive>",
  "recommended_session_duration_minutes": <integer >= 1>,
  "summary": "<concise 1-2 sentence synopsis explaining content and triage rationale>",
  "target_playlist": "<Tech-Tutorials | Study-Queue | Explanations-Queue | Background-Audio | ...>"
}
"""


def _build_user_prompt(item: VideoItem, transcript: Transcript | None) -> str:
    """Format video signals and transcript or fallback metadata for prompt payload."""
    duration_mins = max(1, item.duration_seconds // 60)
    lines: list[str] = [
        f"Video ID: {item.video_id}",
        f"Title: {item.title}",
        f"Channel: {item.channel}",
        f"Duration: {duration_mins} minutes ({item.duration_seconds} seconds)",
        f"Published At: {item.published_at}",
    ]

    if item.chapters:
        lines.append("\nChapter Markers:")
        for ch in item.chapters:
            end_str = f" to {ch.end_seconds}s" if ch.end_seconds is not None else ""
            lines.append(f"- {ch.start_seconds}s{end_str}: {ch.title}")

    if transcript and not transcript.is_fallback and transcript.text.strip():
        lines.append("\nTranscript Content:")
        lines.append(transcript.text.strip())
    else:
        lines.append(
            "\n[Notice: Transcript is unavailable. Classify using metadata and description.]"
        )
        if item.description and item.description.strip():
            lines.append(f"Description:\n{item.description.strip()}")

    return "\n".join(lines)


class OpenRouterClassifierAdapter:
    """LlmClassifier adapter evaluating videos via OpenRouter chat completions API."""

    def __init__(
        self,
        config: OpenRouterConfig,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.http_transport = http_transport
        self.sleep_fn = sleep_fn

    @classmethod
    def from_env(
        cls,
        env_prefix: str = "OPENROUTER_",
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> "OpenRouterClassifierAdapter":
        """Factory creating adapter from environment variables or .env."""
        config = OpenRouterConfig.from_env(env_prefix=env_prefix)
        return cls(config=config, http_transport=http_transport, sleep_fn=sleep_fn)

    def _build_request_body(self, item: VideoItem, transcript: Transcript | None) -> dict[str, Any]:
        """Construct OpenAI-compatible chat completion payload with JSON schema format."""
        system_content = _build_system_prompt()
        user_content = _build_user_prompt(item, transcript)

        schema = {
            "name": "video_triage_metadata",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "video_id": {"type": "string"},
                    "category": {"type": "string"},
                    "cognitive_demand": {
                        "type": "string",
                        "enum": [d.value for d in CognitiveDemand],
                    },
                    "engagement_mode": {
                        "type": "string",
                        "enum": [m.value for m in EngagementMode],
                    },
                    "recommended_session_duration_minutes": {
                        "type": "integer",
                        "minimum": 1,
                    },
                    "summary": {"type": "string"},
                    "target_playlist": {
                        "type": "string",
                        "enum": list(VALID_TARGET_PLAYLISTS),
                    },
                },
                "required": [
                    "video_id",
                    "category",
                    "cognitive_demand",
                    "engagement_mode",
                    "recommended_session_duration_minutes",
                    "summary",
                    "target_playlist",
                ],
                "additionalProperties": False,
            },
        }

        return {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": schema,
            },
            "temperature": 0.1,
            "max_tokens": 1000,
        }

    def _execute_http_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send HTTP request with exponential backoff and rate limit handling."""
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.config.base_url, data=data_bytes, method="POST")
        req.add_header("Authorization", f"Bearer {self.config.api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("HTTP-Referer", self.config.site_url)
        req.add_header("X-Title", self.config.app_name)

        attempts = 0
        backoff = self.config.initial_backoff_seconds

        while True:
            attempts += 1
            try:
                if self.http_transport:
                    resp_obj = self.http_transport(req)
                else:
                    with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                        resp_bytes = resp.read()
                        resp_obj = json.loads(resp_bytes.decode("utf-8"))

                if not isinstance(resp_obj, dict):
                    raise ClassificationSchemaError(
                        f"Unexpected API response type: {type(resp_obj)}"
                    )
                return resp_obj

            except urllib.error.HTTPError as exc:
                err_body = ""
                try:
                    if exc.fp is not None:
                        err_body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    err_body = str(exc)

                status = exc.code

                if status in (401, 403):
                    raise OpenRouterAuthError(
                        f"OpenRouter authentication failed (HTTP {status}): {err_body}"
                    ) from exc

                if status == 429:
                    if attempts > self.config.max_retries:
                        raise OpenRouterRateLimitError(
                            f"OpenRouter rate limit exceeded ({attempts - 1} retries): {err_body}"
                        ) from exc

                    retry_after_hdr = exc.headers.get("Retry-After") if exc.headers else None
                    sleep_time = float(retry_after_hdr) if retry_after_hdr else backoff
                    logger.warning(
                        "HTTP 429 hit. Backing off for %.2fs (attempt %d)", sleep_time, attempts
                    )
                    self.sleep_fn(sleep_time)
                    backoff *= self.config.backoff_factor
                    continue

                if status in (500, 502, 503, 504):
                    if attempts > self.config.max_retries:
                        raise OpenRouterApiError(
                            f"OpenRouter error (HTTP {status}, {attempts - 1} retries): {err_body}"
                        ) from exc

                    logger.warning(
                        "HTTP %d error. Backing off for %.2fs (attempt %d)",
                        status,
                        backoff,
                        attempts,
                    )
                    self.sleep_fn(backoff)
                    backoff *= self.config.backoff_factor
                    continue

                raise OpenRouterApiError(f"OpenRouter HTTP {status} error: {err_body}") from exc

            except (urllib.error.URLError, TimeoutError) as exc:
                if attempts > self.config.max_retries:
                    raise OpenRouterApiError(
                        f"Network failure after {attempts - 1} retries: {exc}"
                    ) from exc

                logger.warning("Network connection failed. Retrying in %.2fs...", backoff)
                self.sleep_fn(backoff)
                backoff *= self.config.backoff_factor
                continue

    def _parse_and_validate_response(
        self,
        response_data: dict[str, Any],
        expected_video_id: str,
        item: VideoItem | None = None,
    ) -> TriageMetadata:
        """Parse LLM chat response payload into verified immutable TriageMetadata."""
        choices = response_data.get("choices")
        if not choices or not isinstance(choices, list):
            raise ClassificationSchemaError(f"Missing 'choices' array in response: {response_data}")

        choice = choices[0]
        message = choice.get("message", {})
        raw_content = message.get("content", "")

        if not raw_content or not isinstance(raw_content, str):
            raise ClassificationSchemaError(f"Empty or non-string message content: {message}")

        cleaned_content = raw_content.strip()

        # Attempt robust parsing and automatic healing of truncated/malformed JSON
        parsed = _repair_json(cleaned_content)
        if parsed is None or not isinstance(parsed, dict):
            raise ClassificationSchemaError(f"Malformed JSON from LLM: {cleaned_content}")

        # Resolve category with alias and fallback support
        raw_category = (
            parsed.get("category")
            or parsed.get("topic")
            or parsed.get("subject")
            or parsed.get("genre")
            or parsed.get("tag")
            or (
                parsed.get("tags")[0]
                if isinstance(parsed.get("tags"), list) and parsed.get("tags")
                else None
            )
        )
        if not raw_category:
            if item and item.channel:
                raw_category = item.channel
            elif item and item.title:
                raw_category = item.title[:30]
            else:
                raw_category = "General"
        category = str(raw_category).strip() or "General"

        # Resolve cognitive taxonomy fields (cognitive_demand and engagement_mode)
        raw_cog = parsed.get("cognitive_demand") or parsed.get("demand") or parsed.get("cognitive")
        raw_eng = parsed.get("engagement_mode") or parsed.get("mode") or parsed.get("engagement")
        raw_pl = str(parsed.get("target_playlist") or parsed.get("playlist") or "").strip()

        if not raw_cog and not raw_eng and not raw_pl:
            raise ClassificationSchemaError(
                "Missing required cognitive taxonomy fields in triage output."
            )

        if raw_cog:
            cog_demand = _normalize_cognitive_demand(str(raw_cog))
            if raw_eng:
                eng_mode = _normalize_engagement_mode(str(raw_eng))
            else:
                eng_mode = _demand_to_mode(cog_demand)
        elif raw_eng:
            eng_mode = _normalize_engagement_mode(str(raw_eng))
            cog_demand = _mode_to_demand(eng_mode)
        else:
            target_playlist = _normalize_target_playlist(raw_pl)
            cog_demand, eng_mode = _playlist_to_demand_mode(target_playlist)

        # summary: if missing or truncated, fallback to item title and category
        raw_summary = str(parsed.get("summary") or "").strip()
        if not raw_summary:
            if item and item.title:
                raw_summary = f"{item.title} ({category})"
            else:
                raise ClassificationSchemaError("summary must not be empty.")
        summary = raw_summary

        # recommended_session_duration_minutes: default to video duration or 15
        raw_dur = parsed.get("recommended_session_duration_minutes")
        if raw_dur is not None:
            try:
                session_dur = int(raw_dur)
                if session_dur <= 0:
                    raise ValueError("Session duration must be positive")
            except (ValueError, TypeError) as exc:
                raise ClassificationSchemaError(
                    f"Invalid recommended_session_duration_minutes: {raw_dur}"
                ) from exc
        elif item and item.duration_seconds > 0:
            session_dur = max(1, item.duration_seconds // 60)
        else:
            session_dur = 15

        # target_playlist: validate or infer if omitted/truncated
        if "target_playlist" not in locals():
            raw_pl = str(parsed.get("target_playlist") or parsed.get("playlist") or "").strip()
            if raw_pl:
                target_playlist = _normalize_target_playlist(raw_pl)
            else:
                target_playlist = _infer_target_playlist(
                    category=category,
                    cognitive_demand=cog_demand,
                    engagement_mode=eng_mode,
                    title=item.title if item else "",
                )

        res_video_id = str(parsed.get("video_id") or expected_video_id).strip() or expected_video_id

        return TriageMetadata(
            video_id=res_video_id,
            category=category,
            cognitive_demand=cog_demand,
            engagement_mode=eng_mode,
            recommended_session_duration_minutes=session_dur,
            summary=summary,
            target_playlist=target_playlist,
        )

    def classify_video(self, item: VideoItem, transcript: Transcript | None) -> TriageMetadata:
        """Classify a video item and transcript against the 2D taxonomy."""
        payload = self._build_request_body(item, transcript)
        response_data = self._execute_http_request(payload)
        return self._parse_and_validate_response(
            response_data, expected_video_id=item.video_id, item=item
        )
