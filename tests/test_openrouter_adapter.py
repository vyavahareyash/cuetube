"""Unit tests for OpenRouter schema-enforced LLM classification adapter."""

import io
import json
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from cuetube.adapters.openrouter import (
    ClassificationSchemaError,
    OpenRouterApiError,
    OpenRouterAuthError,
    OpenRouterClassifierAdapter,
    OpenRouterConfig,
    OpenRouterRateLimitError,
    _normalize_cognitive_demand,
    _normalize_engagement_mode,
)
from cuetube.domain.models import (
    ChapterMarker,
    CognitiveDemand,
    EngagementMode,
    Transcript,
    VideoItem,
)


class TestOpenRouterAdapter(unittest.TestCase):
    """Test suite for OpenRouter classification adapter, prompt generation, and retry logic."""

    def setUp(self) -> None:
        self.config = OpenRouterConfig(
            api_key="test-api-key",
            model="google/gemini-2.5-flash",
            max_retries=2,
            initial_backoff_seconds=0.01,
            backoff_factor=2.0,
        )
        self.sample_item = VideoItem(
            video_id="vid-distributed",
            title="Distributed Systems and Raft Consensus",
            channel="Tech Academy",
            duration_seconds=1800,
            published_at="2026-01-01T00:00:00Z",
            url="https://youtube.com/watch?v=vid-distributed",
            description="Complete syllabus covering Raft election and log replication.",
            chapters=(
                ChapterMarker(title="Introduction", start_seconds=0, end_seconds=120),
                ChapterMarker(title="Raft Algorithm", start_seconds=120, end_seconds=1800),
            ),
        )
        self.sample_transcript = Transcript(
            video_id="vid-distributed",
            text="Welcome to distributed systems. Today we cover consensus and leader election.",
            is_sampled=False,
            is_fallback=False,
        )

    def _make_canned_response(
        self,
        video_id: str = "vid-distributed",
        category: str = "Technical Deep-Dive",
        cognitive_demand: str = "High (active)",
        engagement_mode: str = "Active",
        recommended_session_duration_minutes: int = 35,
        summary: str = "In-depth technical lecture on Raft consensus requiring high mental focus.",
        target_playlist: str = "Tech-Tutorials",
    ) -> dict:
        content = {
            "video_id": video_id,
            "category": category,
            "cognitive_demand": cognitive_demand,
            "engagement_mode": engagement_mode,
            "recommended_session_duration_minutes": recommended_session_duration_minutes,
            "summary": summary,
            "target_playlist": target_playlist,
        }
        return {
            "id": "gen-123",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(content),
                    }
                }
            ],
        }

    def test_config_from_env(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "env-key-xyz",
                "OPENROUTER_MODEL": "openai/gpt-4o-mini",
            },
            clear=True,
        ):
            cfg = OpenRouterConfig.from_env()
            self.assertEqual(cfg.api_key, "env-key-xyz")
            self.assertEqual(cfg.model, "openai/gpt-4o-mini")

    def test_config_missing_api_key_raises_auth_error(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("pathlib.Path.exists", return_value=False),
        ):
            with self.assertRaises(OpenRouterAuthError):
                OpenRouterConfig.from_env()

    def test_classify_video_full_transcript_success(self) -> None:
        recorded_requests: list[urllib.request.Request] = []

        def mock_transport(req: urllib.request.Request) -> dict:
            recorded_requests.append(req)
            return self._make_canned_response()

        adapter = OpenRouterClassifierAdapter(config=self.config, http_transport=mock_transport)
        triage = adapter.classify_video(self.sample_item, self.sample_transcript)

        self.assertEqual(len(recorded_requests), 1)
        req = recorded_requests[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer test-api-key")
        self.assertEqual(req.get_header("Content-type"), "application/json")

        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["model"], "google/gemini-2.5-flash")
        user_prompt = payload["messages"][1]["content"]
        self.assertIn("vid-distributed", user_prompt)
        self.assertIn("Distributed Systems and Raft Consensus", user_prompt)
        self.assertIn("Transcript Content:", user_prompt)
        self.assertIn("Raft Algorithm", user_prompt)

        # Invariant checks on domain TriageMetadata
        self.assertEqual(triage.video_id, "vid-distributed")
        self.assertEqual(triage.cognitive_demand, CognitiveDemand.HIGH_ACTIVE)
        self.assertEqual(triage.engagement_mode, EngagementMode.ACTIVE)
        self.assertEqual(triage.category, "Technical Deep-Dive")
        self.assertEqual(triage.recommended_session_duration_minutes, 35)
        self.assertEqual(triage.target_playlist, "Tech-Tutorials")

    def test_classify_video_fallback_metadata_success(self) -> None:
        recorded_requests: list[urllib.request.Request] = []

        fallback_transcript = Transcript(
            video_id="vid-distributed",
            text="Title: Distributed Systems\nDescription: Syllabus...",
            is_fallback=True,
        )

        def mock_transport(req: urllib.request.Request) -> dict:
            recorded_requests.append(req)
            return self._make_canned_response(
                category="Conceptual Explainer",
                cognitive_demand="Medium (reflective)",
                engagement_mode="Reflective",
                target_playlist="Explanations-Queue",
            )

        adapter = OpenRouterClassifierAdapter(config=self.config, http_transport=mock_transport)
        triage = adapter.classify_video(self.sample_item, fallback_transcript)

        self.assertEqual(len(recorded_requests), 1)
        payload = json.loads(recorded_requests[0].data.decode("utf-8"))
        user_prompt = payload["messages"][1]["content"]
        self.assertIn("Notice: Transcript is unavailable", user_prompt)
        self.assertIn("Description:", user_prompt)

        self.assertEqual(triage.cognitive_demand, CognitiveDemand.MEDIUM_REFLECTIVE)
        self.assertEqual(triage.engagement_mode, EngagementMode.REFLECTIVE)
        self.assertEqual(triage.target_playlist, "Explanations-Queue")

    def test_classify_video_strips_markdown_code_fences(self) -> None:
        canned = self._make_canned_response()
        raw_json_str = canned["choices"][0]["message"]["content"]
        canned["choices"][0]["message"]["content"] = f"```json\n{raw_json_str}\n```"

        adapter = OpenRouterClassifierAdapter(config=self.config, http_transport=lambda req: canned)
        triage = adapter.classify_video(self.sample_item, self.sample_transcript)
        self.assertEqual(triage.video_id, "vid-distributed")
        self.assertEqual(triage.category, "Technical Deep-Dive")

    def test_enum_normalization_variations(self) -> None:
        self.assertEqual(
            _normalize_cognitive_demand("Very High (active)"), CognitiveDemand.VERY_HIGH_ACTIVE
        )
        self.assertEqual(_normalize_cognitive_demand("very high"), CognitiveDemand.VERY_HIGH_ACTIVE)
        self.assertEqual(_normalize_cognitive_demand("HIGH_ACTIVE"), CognitiveDemand.HIGH_ACTIVE)
        self.assertEqual(
            _normalize_cognitive_demand("medium (reflective)"), CognitiveDemand.MEDIUM_REFLECTIVE
        )
        self.assertEqual(_normalize_cognitive_demand("Low (passive)"), CognitiveDemand.LOW_PASSIVE)
        self.assertEqual(_normalize_cognitive_demand("very low"), CognitiveDemand.VERY_LOW_PASSIVE)

        with self.assertRaises(ClassificationSchemaError):
            _normalize_cognitive_demand("unknown-tier")

        self.assertEqual(_normalize_engagement_mode("Active"), EngagementMode.ACTIVE)
        self.assertEqual(_normalize_engagement_mode("reflective"), EngagementMode.REFLECTIVE)
        self.assertEqual(_normalize_engagement_mode("PASSIVE"), EngagementMode.PASSIVE)

        with self.assertRaises(ClassificationSchemaError):
            _normalize_engagement_mode("unknown-mode")

    def test_schema_validation_missing_required_fields(self) -> None:
        incomplete_resp = {
            "choices": [
                {"message": {"content": json.dumps({"video_id": "vid-1", "category": "Tech"})}}
            ]
        }
        adapter = OpenRouterClassifierAdapter(
            config=self.config, http_transport=lambda req: incomplete_resp
        )
        with self.assertRaises(ClassificationSchemaError):
            adapter.classify_video(self.sample_item, self.sample_transcript)

    def test_schema_validation_invalid_duration(self) -> None:
        invalid_dur_resp = self._make_canned_response(recommended_session_duration_minutes=-5)
        adapter = OpenRouterClassifierAdapter(
            config=self.config, http_transport=lambda req: invalid_dur_resp
        )
        with self.assertRaises(ClassificationSchemaError):
            adapter.classify_video(self.sample_item, self.sample_transcript)

    def test_schema_validation_malformed_json(self) -> None:
        malformed_resp = {"choices": [{"message": {"content": "Not JSON at all"}}]}
        adapter = OpenRouterClassifierAdapter(
            config=self.config, http_transport=lambda req: malformed_resp
        )
        with self.assertRaises(ClassificationSchemaError):
            adapter.classify_video(self.sample_item, self.sample_transcript)

    def test_rate_limit_retry_with_exponential_backoff(self) -> None:
        call_count = 0
        sleep_durations: list[float] = []

        def mock_transport(req: urllib.request.Request) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise urllib.error.HTTPError(
                    req.full_url,
                    429,
                    "Too Many Requests",
                    {},
                    io.BytesIO(b"rate limited"),  # type: ignore
                )
            return self._make_canned_response()

        adapter = OpenRouterClassifierAdapter(
            config=self.config,
            http_transport=mock_transport,
            sleep_fn=sleep_durations.append,
        )
        triage = adapter.classify_video(self.sample_item, self.sample_transcript)

        self.assertEqual(call_count, 2)
        self.assertEqual(len(sleep_durations), 1)
        self.assertEqual(sleep_durations[0], 0.01)
        self.assertEqual(triage.video_id, "vid-distributed")

    def test_rate_limit_retry_after_header(self) -> None:
        call_count = 0
        sleep_durations: list[float] = []

        def mock_transport(req: urllib.request.Request) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                headers = {"Retry-After": "3"}
                raise urllib.error.HTTPError(
                    req.full_url,
                    429,
                    "Too Many Requests",
                    headers,
                    io.BytesIO(b"wait 3"),  # type: ignore
                )
            return self._make_canned_response()

        adapter = OpenRouterClassifierAdapter(
            config=self.config,
            http_transport=mock_transport,
            sleep_fn=sleep_durations.append,
        )
        triage = adapter.classify_video(self.sample_item, self.sample_transcript)

        self.assertEqual(call_count, 2)
        self.assertEqual(sleep_durations, [3.0])
        self.assertEqual(triage.video_id, "vid-distributed")

    def test_rate_limit_exhausted_raises_error(self) -> None:
        def failing_transport(req: urllib.request.Request) -> dict:
            raise urllib.error.HTTPError(
                req.full_url,
                429,
                "Rate Limit Exceeded",
                {},
                io.BytesIO(b"quota exhausted"),  # type: ignore
            )

        adapter = OpenRouterClassifierAdapter(
            config=self.config,
            http_transport=failing_transport,
            sleep_fn=lambda s: None,
        )
        with self.assertRaises(OpenRouterRateLimitError):
            adapter.classify_video(self.sample_item, self.sample_transcript)

    def test_server_error_retry_and_exhaustion(self) -> None:
        call_count = 0

        def failing_transport(req: urllib.request.Request) -> dict:
            nonlocal call_count
            call_count += 1
            raise urllib.error.HTTPError(
                req.full_url,
                503,
                "Service Unavailable",
                {},
                io.BytesIO(b"backend error"),  # type: ignore
            )

        adapter = OpenRouterClassifierAdapter(
            config=self.config,
            http_transport=failing_transport,
            sleep_fn=lambda s: None,
        )
        with self.assertRaises(OpenRouterApiError):
            adapter.classify_video(self.sample_item, self.sample_transcript)
        # Attempted initial + 2 retries = 3 calls total
        self.assertEqual(call_count, 3)

    def test_auth_error_fails_immediately_without_retry(self) -> None:
        call_count = 0

        def auth_fail_transport(req: urllib.request.Request) -> dict:
            nonlocal call_count
            call_count += 1
            raise urllib.error.HTTPError(
                req.full_url,
                401,
                "Unauthorized",
                {},
                io.BytesIO(b"invalid token"),  # type: ignore
            )

        adapter = OpenRouterClassifierAdapter(
            config=self.config,
            http_transport=auth_fail_transport,
            sleep_fn=lambda s: None,
        )
        with self.assertRaises(OpenRouterAuthError):
            adapter.classify_video(self.sample_item, self.sample_transcript)
        self.assertEqual(call_count, 1)

    def test_truncated_json_healing_and_playlist_inference(self) -> None:
        summary_text = (
            "This video demonstrates a ChatGPT prompt to automate job openings, "
            "offering a practical application of AI for business processes. "
            "It's suitable for those looking for quick, actionabletips"
        )
        truncated_raw = (
            "{\n"
            '  "video_id": "HHNqqiXQtPs",\n'
            '  "category": "AI Tools",\n'
            '  "cognitive_demand": "Medium (reflective)",\n'
            '  "engagement_mode": "Reflective",\n'
            '  "recommended_session_duration_minutes": 1,\n'
            f'  "summary": "{summary_text}'
        )
        resp = {"choices": [{"message": {"content": truncated_raw}}]}
        adapter = OpenRouterClassifierAdapter(config=self.config, http_transport=lambda req: resp)
        triage = adapter.classify_video(self.sample_item, self.sample_transcript)
        self.assertEqual(triage.video_id, "HHNqqiXQtPs")
        self.assertEqual(triage.category, "AI Tools")
        self.assertEqual(triage.cognitive_demand, CognitiveDemand.MEDIUM_REFLECTIVE)
        self.assertEqual(triage.engagement_mode, EngagementMode.REFLECTIVE)
        self.assertIn("actionabletips", triage.summary)
        self.assertEqual(triage.target_playlist, "Tech-Tutorials")

    def test_markdown_code_fence_truncated_json_healing(self) -> None:
        fence_raw = """```json
{
  "video_id": "vid-distributed",
  "category": "Programming Tutorial",
  "cognitive_demand": "Very High (active)",
  "engagement_mode": "Active",
  "recommended_session_duration_minutes": 45,
  "summary": "In-depth guide to distributed consensus algorithms and practical Raft"""
        resp = {"choices": [{"message": {"content": fence_raw}}]}
        adapter = OpenRouterClassifierAdapter(config=self.config, http_transport=lambda req: resp)
        triage = adapter.classify_video(self.sample_item, self.sample_transcript)
        self.assertEqual(triage.video_id, "vid-distributed")
        self.assertEqual(triage.category, "Programming Tutorial")
        self.assertEqual(triage.cognitive_demand, CognitiveDemand.VERY_HIGH_ACTIVE)
        self.assertEqual(triage.target_playlist, "Tech-Tutorials")

    def test_missing_category_heals_from_item_metadata(self) -> None:
        raw_json = json.dumps(
            {
                "video_id": "vid-distributed",
                "cognitive_demand": "High (active)",
                "engagement_mode": "Active",
                "recommended_session_duration_minutes": 25,
                "summary": "Deep dive into distributed systems.",
                "target_playlist": "Tech-Tutorials",
            }
        )
        resp = {"choices": [{"message": {"content": raw_json}}]}
        adapter = OpenRouterClassifierAdapter(config=self.config, http_transport=lambda req: resp)
        triage = adapter.classify_video(self.sample_item, self.sample_transcript)
        self.assertEqual(triage.video_id, "vid-distributed")
        self.assertEqual(triage.category, "Tech Academy")  # From item.channel

    def test_category_alias_topic_and_missing_engagement_mode(self) -> None:
        raw_json = json.dumps(
            {
                "video_id": "vid-distributed",
                "topic": "Consensus Algorithms",
                "cognitive_demand": "Very High (active)",
                "recommended_session_duration_minutes": 30,
                "summary": "Consensus in distributed systems.",
            }
        )
        resp = {"choices": [{"message": {"content": raw_json}}]}
        adapter = OpenRouterClassifierAdapter(config=self.config, http_transport=lambda req: resp)
        triage = adapter.classify_video(self.sample_item, self.sample_transcript)
        self.assertEqual(triage.category, "Consensus Algorithms")
        self.assertEqual(triage.cognitive_demand, CognitiveDemand.VERY_HIGH_ACTIVE)
        self.assertEqual(triage.engagement_mode, EngagementMode.ACTIVE)  # Inferred
        self.assertEqual(triage.target_playlist, "Tech-Tutorials")  # Inferred

    def test_live_classification_if_env_configured(self) -> None:
        import os
        from pathlib import Path

        if os.getenv("RUN_LIVE_TESTS") != "1":
            self.skipTest("Set RUN_LIVE_TESTS=1 to run against live OpenRouter API")

        env_file = Path(".env")
        if not env_file.exists():
            self.skipTest("No .env file found; skipping live test")

        try:
            adapter = OpenRouterClassifierAdapter.from_env()
        except OpenRouterAuthError:
            self.skipTest("OPENROUTER_API_KEY missing in environment or .env; skipping live test")

        triage = adapter.classify_video(self.sample_item, self.sample_transcript)
        self.assertEqual(triage.video_id, self.sample_item.video_id)
        self.assertIn(triage.cognitive_demand, list(CognitiveDemand))
        self.assertIn(triage.engagement_mode, list(EngagementMode))
        self.assertTrue(len(triage.summary) > 0)
        self.assertTrue(triage.recommended_session_duration_minutes > 0)


if __name__ == "__main__":
    unittest.main()
