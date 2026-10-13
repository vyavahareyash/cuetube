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
        self, response_data: dict[str, Any], expected_video_id: str
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

        # Strip optional markdown code fences ```json ... ```
        cleaned_content = raw_content.strip()
        code_fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned_content)
        if code_fence_match:
            cleaned_content = code_fence_match.group(1).strip()

        try:
            parsed = json.loads(cleaned_content)
        except json.JSONDecodeError as exc:
            raise ClassificationSchemaError(f"Malformed JSON from LLM: {cleaned_content}") from exc

        if not isinstance(parsed, dict):
            raise ClassificationSchemaError(f"Expected JSON object, got {type(parsed)}")

        # Extract and validate required fields
        for req_field in (
            "video_id",
            "category",
            "cognitive_demand",
            "engagement_mode",
            "recommended_session_duration_minutes",
            "summary",
            "target_playlist",
        ):
            if req_field not in parsed:
                raise ClassificationSchemaError(
                    f"Missing required field '{req_field}' in triage output."
                )

        # Resolve cognitive demand and engagement mode enums
        cog_demand = _normalize_cognitive_demand(str(parsed["cognitive_demand"]))
        eng_mode = _normalize_engagement_mode(str(parsed["engagement_mode"]))

        try:
            session_dur = int(parsed["recommended_session_duration_minutes"])
            if session_dur <= 0:
                raise ValueError("Session duration must be positive")
        except (ValueError, TypeError) as exc:
            raw_dur = parsed.get("recommended_session_duration_minutes")
            raise ClassificationSchemaError(
                f"Invalid recommended_session_duration_minutes: {raw_dur}"
            ) from exc

        target_playlist = str(parsed["target_playlist"]).strip()
        if not target_playlist:
            raise ClassificationSchemaError("target_playlist must not be empty.")

        category = str(parsed["category"]).strip()
        if not category:
            raise ClassificationSchemaError("category must not be empty.")

        summary = str(parsed["summary"]).strip()
        if not summary:
            raise ClassificationSchemaError("summary must not be empty.")

        # Ensure video_id alignment
        res_video_id = str(parsed["video_id"]).strip() or expected_video_id

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
        return self._parse_and_validate_response(response_data, expected_video_id=item.video_id)
