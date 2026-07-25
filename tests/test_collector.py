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


class ReportTests(unittest.TestCase):
    def test_release_section_is_omitted_when_watchlist_has_no_matches(self) -> None:
        report = collector.format_report([], [], [], dt.datetime(2026, 7, 25, 9, 0))

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
        report = collector.format_report([], [], [release], dt.datetime(2026, 7, 25, 9, 0))

        self.assertIn("## Watched Project Releases", report)
        self.assertIn("owner/project v1.0.0", report)
        self.assertIn("Security fix.", report)


class ArgumentTests(unittest.TestCase):
    def test_default_information_budget_is_eight_items(self) -> None:
        with patch.object(sys, "argv", ["collector.py"]):
            args = collector.parse_args()

        self.assertEqual((args.github_limit, args.hn_limit, args.release_limit), (3, 3, 2))

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


if __name__ == "__main__":
    unittest.main()
