#!/usr/bin/env python3
"""Low-noise weekly collector for GitHub, Hacker News, and watched releases."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import html
import json
import os
import re
import ssl
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


GITHUB_TRENDING_URL = "https://github.com/trending?since=weekly"
GITHUB_RELEASES_URL = "https://api.github.com/repos/{repository}/releases?per_page=20"
HF_DAILY_PAPERS_URL = "https://huggingface.co/api/daily_papers"
HN_TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
TELEGRAM_API_BASE = "https://api.telegram.org"
FETCH_ATTEMPTS = 3
MAX_REPORT_ITEMS = 8
SSL_CONTEXT: ssl.SSLContext | None = None


@dataclasses.dataclass
class GitHubRepo:
    name: str
    url: str
    description: str
    language: str
    stars_today: str


@dataclasses.dataclass
class HNStory:
    title: str
    url: str
    points: int
    comments: int
    author: str


@dataclasses.dataclass
class GitHubRelease:
    repository: str
    name: str
    url: str
    tag: str
    summary: str
    published_at: dt.datetime


@dataclasses.dataclass
class HFDailyPaper:
    paper_id: str
    title: str
    url: str
    summary: str
    resource_url: str
    upvotes: int
    submitted_at: dt.datetime


class GitHubTrendingParser(HTMLParser):
    """Extract minimal repo cards from GitHub Trending HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.repos: list[GitHubRepo] = []
        self._in_article = False
        self._current_tag: str | None = None
        self._buffer: list[str] = []
        self._current = {
            "name": "",
            "url": "",
            "description": "",
            "language": "",
            "stars_today": "",
        }
        self._capture_description = False
        self._capture_name = False
        self._capture_language = False
        self._capture_stars = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class", "") or ""

        if tag == "article" and "Box-row" in classes:
            self._in_article = True
            self._current = {
                "name": "",
                "url": "",
                "description": "",
                "language": "",
                "stars_today": "",
            }
            return

        if not self._in_article:
            return

        self._current_tag = tag

        if tag == "h2":
            self._capture_name = True
            self._buffer = []
        elif tag == "a" and self._capture_name:
            href = attrs_dict.get("href") or ""
            if href.startswith("/"):
                self._current["url"] = "https://github.com" + href
        elif tag == "p":
            self._capture_description = True
            self._buffer = []
        elif tag == "span" and "d-inline-block ml-0 mr-3" in classes:
            self._capture_language = True
            self._buffer = []
        elif tag == "span" and "float-sm-right" in classes:
            self._capture_stars = True
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if not self._in_article:
            return

        if tag == "h2" and self._capture_name:
            self._current["name"] = normalize_space("".join(self._buffer)).replace(" / ", "/")
            self._capture_name = False
            self._buffer = []
        elif tag == "p" and self._capture_description:
            self._current["description"] = normalize_space("".join(self._buffer))
            self._capture_description = False
            self._buffer = []
        elif tag == "span" and self._capture_language:
            self._current["language"] = normalize_space("".join(self._buffer))
            self._capture_language = False
            self._buffer = []
        elif tag == "span" and self._capture_stars:
            self._current["stars_today"] = normalize_space("".join(self._buffer))
            self._capture_stars = False
            self._buffer = []
        elif tag == "article":
            self._in_article = False
            if self._current["name"] and self._current["url"]:
                self.repos.append(GitHubRepo(**self._current))

    def handle_data(self, data: str) -> None:
        if any(
            [
                self._capture_name,
                self._capture_description,
                self._capture_language,
                self._capture_stars,
            ]
        ):
            self._buffer.append(data)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def build_ssl_context(allow_insecure: bool = False) -> ssl.SSLContext:
    if allow_insecure:
        return ssl._create_unverified_context()

    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "weekly-tech-collector/1.0",
            "Accept": "text/html,application/json",
        },
    )
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=SSL_CONTEXT
            ) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            if attempt == FETCH_ATTEMPTS - 1:
                raise
            delay = 2**attempt
            print(
                f"Warning: fetch failed ({exc}); retrying in {delay}s "
                f"({attempt + 1}/{FETCH_ATTEMPTS})",
                file=sys.stderr,
            )
            time.sleep(delay)

    raise RuntimeError("Unreachable fetch retry state")


def fetch_json(url: str, timeout: int = 30) -> object:
    return json.loads(fetch_text(url, timeout=timeout))


def fetch_github_trending(limit: int) -> list[GitHubRepo]:
    parser = GitHubTrendingParser()
    parser.feed(fetch_text(GITHUB_TRENDING_URL))
    return parser.repos[:limit]


def fetch_hn_top(limit: int) -> list[HNStory]:
    item_ids = fetch_json(HN_TOP_STORIES_URL)
    if not isinstance(item_ids, list):
        raise RuntimeError("Unexpected Hacker News topstories response")

    stories: list[HNStory] = []
    for item_id in item_ids[: limit * 3]:
        item = fetch_json(HN_ITEM_URL.format(item_id=item_id))
        if not isinstance(item, dict):
            continue
        if item.get("type") != "story" or not item.get("title"):
            continue
        stories.append(
            HNStory(
                title=str(item.get("title", "")),
                url=str(item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"),
                points=int(item.get("score") or 0),
                comments=int(item.get("descendants") or 0),
                author=str(item.get("by") or ""),
            )
        )
        if len(stories) >= limit:
            break
    return stories


def load_watched_repositories(config_path: Path) -> list[str]:
    """Load unique ``owner/repository`` entries from the tracked watchlist."""

    if not config_path.exists():
        return []

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid watchlist JSON: {config_path}") from exc

    if not isinstance(config, dict):
        raise RuntimeError("Watchlist must be a JSON object")
    repositories = config.get("github_releases", [])
    if not isinstance(repositories, list) or not all(
        isinstance(repository, str) for repository in repositories
    ):
        raise RuntimeError("watchlist.github_releases must be a list of strings")

    unique_repositories: list[str] = []
    for repository in repositories:
        normalized = repository.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", normalized):
            raise RuntimeError(f"Invalid GitHub repository in watchlist: {repository!r}")
        if normalized not in unique_repositories:
            unique_repositories.append(normalized)
    return unique_repositories


def parse_github_timestamp(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            dt.timezone.utc
        )
    except ValueError:
        return None


def summarize_release(value: object, width: int = 180) -> str:
    if not isinstance(value, str) or not value.strip():
        return "No release notes provided."
    plain_text = re.sub(r"[`*_>#]", "", value)
    return textwrap.shorten(normalize_space(plain_text), width=width, placeholder="…")


def fetch_watched_releases(
    repositories: Iterable[str],
    since: dt.datetime,
    limit: int,
) -> list[GitHubRelease]:
    """Return stable releases published within the reporting window.

    The release section is intentionally best-effort: a rate limit or a single
    unavailable repository must not prevent the primary digest from sending.
    """

    if limit <= 0:
        return []

    since_utc = since.astimezone(dt.timezone.utc)
    releases: list[GitHubRelease] = []
    for repository in repositories:
        try:
            response = fetch_json(GITHUB_RELEASES_URL.format(repository=repository))
        except Exception as exc:
            print(f"Warning: unable to fetch releases for {repository}: {exc}", file=sys.stderr)
            continue

        if not isinstance(response, list):
            print(
                f"Warning: unexpected GitHub releases response for {repository}",
                file=sys.stderr,
            )
            continue

        for item in response:
            if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
                continue
            published_at = parse_github_timestamp(item.get("published_at"))
            if published_at is None or published_at < since_utc:
                continue
            tag = str(item.get("tag_name") or "")
            url = str(item.get("html_url") or "")
            if not tag or not url:
                continue
            releases.append(
                GitHubRelease(
                    repository=repository,
                    name=str(item.get("name") or tag),
                    url=url,
                    tag=tag,
                    summary=summarize_release(item.get("body")),
                    published_at=published_at,
                )
            )

    releases.sort(key=lambda release: release.published_at, reverse=True)
    latest_releases: list[GitHubRelease] = []
    seen_repositories: set[str] = set()
    for release in releases:
        if release.repository in seen_repositories:
            continue
        latest_releases.append(release)
        seen_repositories.add(release.repository)
        if len(latest_releases) >= limit:
            break
    return latest_releases


HF_RELEVANCE_TERMS = (
    "agent",
    "language model",
    "llm",
    "multimodal",
    "retrieval",
    "rag",
    "reasoning",
    "code",
    "tool use",
)
HF_MIN_UPVOTES = 5


def fetch_hf_daily_papers(
    since: dt.datetime,
    limit: int,
) -> list[HFDailyPaper]:
    """Return high-signal Daily Papers with public implementation resources."""

    if limit <= 0:
        return []

    try:
        response = fetch_json(HF_DAILY_PAPERS_URL)
    except Exception as exc:
        print(f"Warning: unable to fetch Hugging Face Daily Papers: {exc}", file=sys.stderr)
        return []
    if not isinstance(response, list):
        print("Warning: unexpected Hugging Face Daily Papers response", file=sys.stderr)
        return []

    since_utc = since.astimezone(dt.timezone.utc)
    papers: list[HFDailyPaper] = []
    for item in response:
        if not isinstance(item, dict) or not isinstance(item.get("paper"), dict):
            continue
        paper = item["paper"]
        paper_id = str(paper.get("id") or "")
        title = str(paper.get("title") or item.get("title") or "")
        summary = str(paper.get("summary") or item.get("summary") or "")
        submitted_at = parse_github_timestamp(paper.get("submittedOnDailyAt"))
        resource_url = str(paper.get("githubRepo") or paper.get("projectPage") or "")
        upvotes = int(paper.get("upvotes") or 0)
        searchable = f"{title} {summary}".lower()
        if (
            not paper_id
            or not title
            or submitted_at is None
            or submitted_at < since_utc
            or not resource_url.startswith(("https://github.com/", "https://huggingface.co/"))
            or upvotes < HF_MIN_UPVOTES
            or not any(term in searchable for term in HF_RELEVANCE_TERMS)
        ):
            continue
        papers.append(
            HFDailyPaper(
                paper_id=paper_id,
                title=title,
                url=f"https://huggingface.co/papers/{paper_id}",
                summary=summarize_release(summary),
                resource_url=resource_url,
                upvotes=upvotes,
                submitted_at=submitted_at,
            )
        )

    papers.sort(key=lambda paper: (paper.upvotes, paper.submitted_at), reverse=True)
    return papers[:limit]


def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def format_report(
    github_repos: Iterable[GitHubRepo],
    hn_stories: Iterable[HNStory],
    watched_releases: Iterable[GitHubRelease],
    hf_daily_papers: Iterable[HFDailyPaper],
    generated_at: dt.datetime,
) -> str:
    date_label = generated_at.strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Weekly Tech Digest",
        "",
        f"Generated at: {date_label}",
        "",
        "## GitHub Trending",
        "",
    ]

    for index, repo in enumerate(github_repos, start=1):
        description = repo.description or "No description"
        meta = " | ".join(filter(None, [repo.language, repo.stars_today]))
        lines.extend(
            [
                f"{index}. [{repo.name}]({repo.url})",
                f"   {description}",
                f"   {meta}" if meta else "",
            ]
        )

    lines.extend(["", "## Hacker News", ""])

    for index, story in enumerate(hn_stories, start=1):
        meta = f"{story.points} points | {story.comments} comments | by {story.author}"
        lines.extend(
            [
                f"{index}. [{story.title}]({story.url})",
                f"   {meta}",
            ]
        )

    releases = list(watched_releases)
    if releases:
        lines.extend(["", "## Watched Project Releases", ""])
        for index, release in enumerate(releases, start=1):
            published_label = release.published_at.strftime("%Y-%m-%d")
            lines.extend(
                [
                    f"{index}. [{release.repository} {release.tag}]({release.url})",
                    f"   {release.summary}",
                    f"   Released {published_label}",
                ]
            )

    papers = list(hf_daily_papers)
    if papers:
        lines.extend(["", "## Hugging Face Daily Papers Radar", ""])
        for index, paper in enumerate(papers, start=1):
            lines.extend(
                [
                    f"{index}. [{paper.title}]({paper.url})",
                    f"   {paper.summary}",
                    f"   {paper.upvotes} upvotes | [Public resources]({paper.resource_url})",
                ]
            )

    return "\n".join(line for line in lines if line != "")


def markdown_to_telegram_text(report: str) -> str:
    text = re.sub(r"^#\s+", "", report, flags=re.MULTILINE)
    text = re.sub(r"^##\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1\n\2", text)
    return html.unescape(text)


def split_message(text: str, max_length: int = 3800) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}".strip()
        if len(candidate) <= max_length:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = line
    if current:
        chunks.append(current)
    return chunks


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    for chunk in split_message(text):
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": chunk})
        data = payload.encode("utf-8")
        url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
        request = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"Telegram send failed: {body}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect a low-noise weekly technology digest."
    )
    parser.add_argument("--github-limit", type=int, default=3)
    parser.add_argument("--hn-limit", type=int, default=2)
    parser.add_argument(
        "--release-limit",
        type=int,
        default=2,
        help="Maximum watched-project releases to include.",
    )
    parser.add_argument(
        "--hf-limit",
        type=int,
        default=1,
        help="Maximum qualified Hugging Face Daily Papers to include.",
    )
    parser.add_argument(
        "--watchlist-file",
        default="watchlist.json",
        help="JSON file containing the GitHub release watchlist.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for generated markdown reports.",
    )
    parser.add_argument(
        "--skip-telegram",
        action="store_true",
        help="Generate the report without sending it to Telegram.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL certificate verification for temporary local testing.",
    )
    args = parser.parse_args()
    limits = (args.github_limit, args.hn_limit, args.release_limit, args.hf_limit)
    if any(limit < 0 for limit in limits):
        parser.error("item limits must be zero or greater")
    if sum(limits) > MAX_REPORT_ITEMS:
        parser.error(f"combined item limits must not exceed {MAX_REPORT_ITEMS}")
    return args


def main() -> int:
    args = parse_args()
    global SSL_CONTEXT
    project_root = Path(__file__).resolve().parent
    load_env(project_root / ".env")
    SSL_CONTEXT = build_ssl_context(allow_insecure=args.insecure)

    generated_at = dt.datetime.now()
    output_dir = project_root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    github_repos = fetch_github_trending(args.github_limit)
    hn_stories = fetch_hn_top(args.hn_limit)
    watchlist_path = Path(args.watchlist_file)
    if not watchlist_path.is_absolute():
        watchlist_path = project_root / watchlist_path
    watched_repositories = load_watched_repositories(watchlist_path)
    watched_releases = fetch_watched_releases(
        watched_repositories,
        since=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7),
        limit=args.release_limit,
    )
    hf_daily_papers = fetch_hf_daily_papers(
        since=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7),
        limit=args.hf_limit,
    )
    report = format_report(
        github_repos,
        hn_stories,
        watched_releases,
        hf_daily_papers,
        generated_at,
    )

    timestamp = generated_at.strftime("%Y%m%d-%H%M%S")
    report_path = output_dir / f"weekly-report-{timestamp}.md"
    report_path.write_text(report + "\n", encoding="utf-8")

    if not args.skip_telegram:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        telegram_text = markdown_to_telegram_text(report)
        send_telegram_message(bot_token, chat_id, telegram_text)

    print(f"Report written to {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except ssl.SSLCertVerificationError as exc:
        print(
            "Error: SSL certificate verification failed. "
            "Try installing certifi (`python3 -m pip install certifi`) "
            "or run once with `--insecure` for local testing only.",
            file=sys.stderr,
        )
        print(f"Details: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
