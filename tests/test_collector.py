import dataclasses
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import collector


class WatchlistTests(unittest.TestCase):
    def test_load_watched_repositories_deduplicates_and_trims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchlist.json"
            path.write_text(
                json.dumps(
                    {
                        "github_releases": [
                            " vllm-project/vllm ",
                            "vllm-project/vllm",
                            "huggingface/transformers",
                        ]
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                collector.load_watched_repositories(path),
                ["vllm-project/vllm", "huggingface/transformers"],
            )

    def test_load_watched_repositories_rejects_invalid_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchlist.json"
            path.write_text('{"github_releases": ["not-a-repository"]}', encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Invalid GitHub repository"):
                collector.load_watched_repositories(path)


class ReleaseTests(unittest.TestCase):
    def test_fetch_watched_releases_filters_to_recent_stable_releases(self) -> None:
        response = [
            {
                "name": "Current stable",
                "tag_name": "v2.0.0",
                "html_url": "https://example.test/v2.0.0",
                "published_at": "2026-07-24T12:00:00Z",
                "body": "# Important\n\nA useful update.",
                "draft": False,
                "prerelease": False,
            },
            {
                "tag_name": "v2.1.0-rc1",
                "html_url": "https://example.test/v2.1.0-rc1",
                "published_at": "2026-07-24T12:00:00Z",
                "draft": False,
                "prerelease": True,
            },
            {
                "tag_name": "v1.0.0",
                "html_url": "https://example.test/v1.0.0",
                "published_at": "2026-07-01T12:00:00Z",
                "draft": False,
                "prerelease": False,
            },
        ]
        since = dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc)

        with patch("collector.fetch_json", return_value=response):
            releases = collector.fetch_watched_releases(
                ["owner/project"], since=since, limit=2
            )

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0].repository, "owner/project")
        self.assertEqual(releases[0].tag, "v2.0.0")
        self.assertEqual(releases[0].summary, "Important A useful update.")

    def test_fetch_watched_releases_keeps_only_latest_release_per_repository(self) -> None:
        newer = {
            "tag_name": "v2.0.0",
            "html_url": "https://example.test/v2",
            "published_at": "2026-07-24T12:00:00Z",
            "draft": False,
            "prerelease": False,
        }
        older = {
            "tag_name": "v1.0.0",
            "html_url": "https://example.test/v1",
            "published_at": "2026-07-22T12:00:00Z",
            "draft": False,
            "prerelease": False,
        }
        since = dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc)

        with patch("collector.fetch_json", return_value=[newer, older]):
            releases = collector.fetch_watched_releases(
                ["owner/project"], since=since, limit=2
            )

        self.assertEqual([release.tag for release in releases], ["v2.0.0"])

    def test_release_fetch_failure_is_best_effort(self) -> None:
        since = dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc)
        with patch("collector.fetch_json", side_effect=OSError("network unavailable")):
            self.assertEqual(
                collector.fetch_watched_releases(["owner/project"], since=since, limit=2),
                [],
            )

    def test_release_fetch_failure_does_not_hide_other_repositories(self) -> None:
        since = dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc)
        stable_release = {
            "tag_name": "v1.0.0",
            "html_url": "https://example.test/v1",
            "published_at": "2026-07-24T12:00:00Z",
            "draft": False,
            "prerelease": False,
        }
        with patch(
            "collector.fetch_json",
            side_effect=[OSError("network unavailable"), [stable_release]],
        ):
            releases = collector.fetch_watched_releases(
                ["broken/project", "healthy/project"], since=since, limit=2
            )

        self.assertEqual([release.repository for release in releases], ["healthy/project"])

    def test_zero_release_limit_skips_fetching(self) -> None:
        since = dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc)
        with patch("collector.fetch_json") as fetch_json:
            releases = collector.fetch_watched_releases(
                ["owner/project"], since=since, limit=0
            )

        self.assertEqual(releases, [])
        fetch_json.assert_not_called()


class HFDailyPaperTests(unittest.TestCase):
    def test_fetch_hf_daily_papers_requires_recent_relevant_supported_signal(self) -> None:
        qualifying = {
            "paper": {
                "id": "2607.12345",
                "title": "Reliable Tool Use for Language Model Agents",
                "summary": "We evaluate an agent framework with reproducible experiments.",
                "submittedOnDailyAt": "2026-07-24T00:00:00.000Z",
                "githubRepo": "https://github.com/example/agent-paper",
                "upvotes": 12,
            }
        }
        no_resource = {
            "paper": {
                "id": "2607.22222",
                "title": "Another LLM Study",
                "summary": "No public implementation.",
                "submittedOnDailyAt": "2026-07-24T00:00:00.000Z",
                "upvotes": 50,
            }
        }
        low_signal = {
            "paper": {
                "id": "2607.33333",
                "title": "A Multimodal Model",
                "summary": "Code and experiments.",
                "submittedOnDailyAt": "2026-07-24T00:00:00.000Z",
                "githubRepo": "https://github.com/example/model",
                "upvotes": 2,
            }
        }
        since = dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc)

        with patch(
            "collector.fetch_json",
            return_value=[low_signal, no_resource, qualifying],
        ):
            papers = collector.fetch_hf_daily_papers(since=since, limit=1)

        self.assertEqual([paper.paper_id for paper in papers], ["2607.12345"])
        self.assertEqual(papers[0].upvotes, 12)

    def test_hf_daily_papers_failure_is_best_effort(self) -> None:
        since = dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc)
        with patch("collector.fetch_json", side_effect=OSError("network unavailable")):
            self.assertEqual(collector.fetch_hf_daily_papers(since=since, limit=1), [])


class AgentRadarTests(unittest.TestCase):
    def test_load_agent_sources_requires_versioned_typed_registry(self) -> None:
        config = {
            "schema_version": "agent_sources_v1",
            "github_releases": [
                {
                    "repository": "openai/openai-agents-python",
                    "publisher": "OpenAI",
                    "topics": ["control_plane", "memory_context_skills"],
                    "priority": "P0",
                }
            ],
            "research_feeds": [
                {
                    "kind": "github_daily_jsonl",
                    "repository": "LIHUA919/AI-Agents-Daily-Research",
                    "branch": "main",
                    "path_template": "data/{date}.jsonl",
                    "publisher": "arXiv via AI-Agents-Daily-Research",
                    "priority": "P1",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent_sources.json"
            path.write_text(json.dumps(config), encoding="utf-8")

            releases, feeds = collector.load_agent_sources(path)

        self.assertEqual(releases[0].repository, "openai/openai-agents-python")
        self.assertEqual(releases[0].topics, ("control_plane", "memory_context_skills"))
        self.assertEqual(feeds[0].path_template, "data/{date}.jsonl")

    def test_load_agent_sources_rejects_unbounded_priority(self) -> None:
        config = {
            "schema_version": "agent_sources_v1",
            "github_releases": [
                {
                    "repository": "openai/openai-agents-python",
                    "publisher": "OpenAI",
                    "topics": ["control_plane"],
                    "priority": "urgent",
                }
            ],
            "research_feeds": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent_sources.json"
            path.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "priority must be one of"):
                collector.load_agent_sources(path)

    def test_release_is_normalized_with_stable_evidence_fields(self) -> None:
        source = collector.AgentReleaseSource(
            repository="openai/openai-agents-python",
            publisher="OpenAI",
            topics=("control_plane",),
            priority="P0",
        )
        release = collector.GitHubRelease(
            repository=source.repository,
            name="Version 1",
            url="https://github.com/openai/openai-agents-python/releases/tag/v1",
            tag="v1",
            summary="Adds durable workflow recovery.",
            published_at=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
        )

        signal = collector.release_to_agent_signal(release, source)

        self.assertEqual(signal.evidence_level, "first_party")
        self.assertEqual(signal.priority, "P0")
        self.assertEqual(signal.signal_id, collector.agent_signal_id(signal.source_kind, signal.url))
        self.assertIn("状态", signal.why_it_matters)

    def test_research_jsonl_filters_and_classifies_agent_papers(self) -> None:
        source = collector.AgentResearchFeed(
            repository="LIHUA919/AI-Agents-Daily-Research",
            branch="main",
            path_template="data/{date}.jsonl",
            publisher="arXiv via AI-Agents-Daily-Research",
            priority="P1",
        )
        payload = "\n".join(
            [
                json.dumps(
                    {
                        "title": "Reliable Recovery for Long-Horizon Agents",
                        "summary": "An evaluation of agent handoff and recovery metrics.",
                        "abs": "https://arxiv.org/abs/2608.12345",
                    }
                ),
                json.dumps(
                    {
                        "title": "A Small Image Classifier",
                        "summary": "A reproducible evaluation with reliability metrics.",
                        "abs": "https://arxiv.org/abs/2608.99999",
                    }
                ),
                json.dumps(
                    {
                        "title": "LLM-Driven Hardware Compatibility Verification",
                        "summary": "A language model workflow uses memory for constraints.",
                        "abs": "https://arxiv.org/abs/2608.88888",
                    }
                ),
            ]
        )

        signals = collector.parse_research_jsonl(
            payload,
            source,
            dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].evidence_level, "author_preprint")
        self.assertIn("control_plane", signals[0].topics)
        self.assertIn("agent_evaluation", signals[0].topics)

    def test_rank_agent_signals_deduplicates_and_prefers_p0(self) -> None:
        published_at = dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc)
        base = {
            "publisher": "Example",
            "published_at": published_at,
            "topics": ("agent_evaluation",),
            "evidence_level": "author_preprint",
            "summary": "Summary",
            "why_it_matters": "Why",
            "x_angle": "Angle",
        }
        p1 = collector.AgentSignal(
            signal_id="same",
            title="P1 paper",
            url="https://example.test/p1",
            source_kind="author_preprint",
            priority="P1",
            **base,
        )
        duplicate = dataclasses.replace(p1, title="Duplicate")
        p0 = dataclasses.replace(
            p1,
            signal_id="p0",
            title="P0 release",
            url="https://example.test/p0",
            priority="P0",
            evidence_level="first_party",
        )

        ranked = collector.rank_agent_signals([p1, duplicate, p0], limit=5)

        self.assertEqual([signal.signal_id for signal in ranked], ["p0", "same"])

    def test_agent_radar_report_is_evidence_first_and_draft_only(self) -> None:
        signal = collector.AgentSignal(
            signal_id="abc",
            title="Agent Runtime v1",
            url="https://example.test/v1",
            publisher="Example",
            source_kind="first_party_release",
            published_at=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
            topics=("control_plane",),
            priority="P0",
            evidence_level="first_party",
            summary="Adds resumable workflows.",
            why_it_matters="Changes recovery boundaries.",
            x_angle="Write about recovery.",
        )

        report = collector.format_agent_radar(
            [signal], dt.datetime(2026, 8, 28, 9, 0, tzinfo=dt.timezone.utc)
        )

        self.assertIn("Draft only", report)
        self.assertIn("发生了什么", report)
        self.assertIn("为什么重要", report)
        self.assertIn("证据：first_party", report)
        self.assertIn("X 草稿角度", report)

    def test_agent_radar_main_never_sends_telegram(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    sys,
                    "argv",
                    ["collector.py", "--agent-radar", "--output-dir", directory],
                ),
                patch("collector.load_agent_sources", return_value=([], [])),
                patch("collector.collect_agent_signals", return_value=[]),
                patch("collector.send_telegram_message") as send_telegram,
            ):
                result = collector.main()

            reports = list(Path(directory).glob("daily-agent-radar-*.md"))

        self.assertEqual(result, 0)
        self.assertEqual(len(reports), 1)
        send_telegram.assert_not_called()


class ReportTests(unittest.TestCase):
    def test_release_section_is_omitted_when_watchlist_has_no_matches(self) -> None:
        report = collector.format_report([], [], [], [], dt.datetime(2026, 7, 25, 9, 0))

        self.assertNotIn("Watched Project Releases", report)

    def test_release_section_contains_version_and_summary(self) -> None:
        release = collector.GitHubRelease(
            repository="owner/project",
            name="Version 1",
            url="https://example.test/v1",
            tag="v1.0.0",
            summary="Security fix.",
            published_at=dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc),
        )
        report = collector.format_report(
            [], [], [release], [], dt.datetime(2026, 7, 25, 9, 0)
        )

        self.assertIn("## Watched Project Releases", report)
        self.assertIn("owner/project v1.0.0", report)
        self.assertIn("Security fix.", report)

    def test_hf_radar_contains_paper_signal_and_resources(self) -> None:
        paper = collector.HFDailyPaper(
            paper_id="2607.12345",
            title="Reliable Agents",
            url="https://huggingface.co/papers/2607.12345",
            summary="A reproducible agent study.",
            resource_url="https://github.com/example/reliable-agents",
            upvotes=12,
            submitted_at=dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc),
        )
        report = collector.format_report(
            [], [], [], [paper], dt.datetime(2026, 7, 25, 9, 0)
        )

        self.assertIn("## Hugging Face Daily Papers Radar", report)
        self.assertIn("Reliable Agents", report)
        self.assertIn("12 upvotes", report)
        self.assertIn("Public resources", report)


class ArgumentTests(unittest.TestCase):
    def test_default_information_budget_is_eight_items(self) -> None:
        with patch.object(sys, "argv", ["collector.py"]):
            args = collector.parse_args()

        self.assertEqual(
            (args.github_limit, args.hn_limit, args.release_limit, args.hf_limit),
            (3, 2, 2, 1),
        )

    def test_agent_radar_defaults_are_bounded(self) -> None:
        with patch.object(sys, "argv", ["collector.py", "--agent-radar"]):
            args = collector.parse_args()

        self.assertEqual((args.agent_limit, args.agent_window_days), (5, 2))

    def test_agent_limit_cannot_exceed_five(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["collector.py", "--agent-radar", "--agent-limit", "6"],
            ),
            self.assertRaises(SystemExit),
        ):
            collector.parse_args()

    def test_combined_information_budget_cannot_exceed_eight_items(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                [
                    "collector.py",
                    "--github-limit",
                    "4",
                    "--hn-limit",
                    "3",
                    "--release-limit",
                    "2",
                    "--hf-limit",
                    "1",
                ],
            ),
            self.assertRaises(SystemExit),
        ):
            collector.parse_args()

    def test_item_limits_cannot_be_negative(self) -> None:
        with (
            patch.object(sys, "argv", ["collector.py", "--release-limit", "-1"]),
            self.assertRaises(SystemExit),
        ):
            collector.parse_args()


class FetchTests(unittest.TestCase):
    def test_fetch_text_retries_a_transient_url_error(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'

        with (
            patch(
                "collector.urllib.request.urlopen",
                side_effect=[URLError("unexpected EOF"), response],
            ) as urlopen,
            patch("collector.time.sleep") as sleep,
        ):
            result = collector.fetch_text("https://example.test/data")

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_fetch_text_does_not_retry_a_missing_optional_feed(self) -> None:
        with (
            patch(
                "collector.urllib.request.urlopen",
                side_effect=collector.urllib.error.HTTPError(
                    "https://example.test/missing",
                    404,
                    "Not Found",
                    {},
                    None,
                ),
            ) as urlopen,
            patch("collector.time.sleep") as sleep,
            self.assertRaises(collector.urllib.error.HTTPError),
        ):
            collector.fetch_text("https://example.test/missing")

        urlopen.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
