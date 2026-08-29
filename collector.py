#!/usr/bin/env python3
"""Low-noise weekly collector and draft-only Agent Radar."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
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
MAX_AGENT_RADAR_ITEMS = 5
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


@dataclasses.dataclass(frozen=True)
class AgentReleaseSource:
    repository: str
    publisher: str
    topics: tuple[str, ...]
    priority: str


@dataclasses.dataclass(frozen=True)
class AgentResearchFeed:
    repository: str
    branch: str
    path_template: str
    publisher: str
    priority: str


@dataclasses.dataclass(frozen=True)
class AgentSignal:
    signal_id: str
    title: str
    url: str
    publisher: str
    source_kind: str
    published_at: dt.datetime
    topics: tuple[str, ...]
    priority: str
    evidence_level: str
    summary: str
    why_it_matters: str
    x_angle: str


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
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                raise
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


AGENT_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
AGENT_TOPIC_KEYWORDS = {
    "control_plane": (
        "control plane",
        "orchestration",
        "workflow",
        "handoff",
        "approval",
        "recovery",
        "parallel agent",
        "multi-agent",
        "multi agent",
        "long-horizon",
        "long horizon",
    ),
    "memory_context_skills": (
        "memory",
        "context",
        "skill",
        "tool use",
        "tool-use",
        "retrieval",
        "model context protocol",
        "mcp",
    ),
    "agent_evaluation": (
        "benchmark",
        "evaluation",
        "evaluate",
        "metric",
        "verification",
        "reproducib",
        "reliability",
    ),
}
AGENT_DIRECT_PATTERNS = (
    r"\bagents?\b",
    r"\bagentic\b",
    r"\bmulti[- ]agent\b",
    r"\bmodel context protocol\b",
)
AGENT_TOPIC_LABELS = {
    "control_plane": "长时、多 Agent 与控制平面",
    "memory_context_skills": "Memory、Context 与 Skills",
    "agent_evaluation": "复杂任务评测与量化",
}


def _validate_repository(value: object, label: str) -> str:
    repository = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RuntimeError(f"Invalid GitHub repository in {label}: {value!r}")
    return repository


def _validate_priority(value: object, label: str) -> str:
    priority = str(value or "").strip()
    if priority not in AGENT_PRIORITY_ORDER:
        raise RuntimeError(f"{label}.priority must be one of P0, P1, or P2")
    return priority


def _validate_topics(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(
        isinstance(topic, str) and topic.strip() for topic in value
    ):
        raise RuntimeError(f"{label}.topics must be a non-empty list of strings")
    return tuple(dict.fromkeys(topic.strip() for topic in value))


def load_agent_sources(
    config_path: Path,
) -> tuple[list[AgentReleaseSource], list[AgentResearchFeed]]:
    """Load the tracked, auditable registry used only by Agent Radar mode."""

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Agent source registry not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid agent source registry JSON: {config_path}") from exc

    if not isinstance(config, dict) or config.get("schema_version") != "agent_sources_v1":
        raise RuntimeError("Agent source registry must use schema_version agent_sources_v1")

    raw_releases = config.get("github_releases", [])
    raw_feeds = config.get("research_feeds", [])
    if not isinstance(raw_releases, list) or not isinstance(raw_feeds, list):
        raise RuntimeError("Agent source registry lists must be arrays")

    releases: list[AgentReleaseSource] = []
    seen_repositories: set[str] = set()
    for index, item in enumerate(raw_releases):
        label = f"github_releases[{index}]"
        if not isinstance(item, dict):
            raise RuntimeError(f"{label} must be an object")
        repository = _validate_repository(item.get("repository"), label)
        publisher = str(item.get("publisher") or "").strip()
        if not publisher:
            raise RuntimeError(f"{label}.publisher must be a non-empty string")
        if repository in seen_repositories:
            raise RuntimeError(f"Duplicate Agent release source: {repository}")
        releases.append(
            AgentReleaseSource(
                repository=repository,
                publisher=publisher,
                topics=_validate_topics(item.get("topics"), label),
                priority=_validate_priority(item.get("priority"), label),
            )
        )
        seen_repositories.add(repository)

    feeds: list[AgentResearchFeed] = []
    for index, item in enumerate(raw_feeds):
        label = f"research_feeds[{index}]"
        if not isinstance(item, dict) or item.get("kind") != "github_daily_jsonl":
            raise RuntimeError(f"{label}.kind must be github_daily_jsonl")
        path_template = str(item.get("path_template") or "").strip()
        if "{date}" not in path_template or path_template.startswith("/"):
            raise RuntimeError(f"{label}.path_template must be relative and contain {{date}}")
        publisher = str(item.get("publisher") or "").strip()
        branch = str(item.get("branch") or "main").strip()
        if not publisher or not branch:
            raise RuntimeError(f"{label}.publisher and branch must be non-empty strings")
        feeds.append(
            AgentResearchFeed(
                repository=_validate_repository(item.get("repository"), label),
                branch=branch,
                path_template=path_template,
                publisher=publisher,
                priority=_validate_priority(item.get("priority"), label),
            )
        )
    return releases, feeds


def infer_agent_topics(value: str) -> tuple[str, ...]:
    searchable = value.lower()
    return tuple(
        topic
        for topic, keywords in AGENT_TOPIC_KEYWORDS.items()
        if any(keyword in searchable for keyword in keywords)
    )


def is_agent_relevant(value: str) -> bool:
    searchable = value.lower()
    return any(re.search(pattern, searchable) for pattern in AGENT_DIRECT_PATTERNS)


def agent_signal_id(source_kind: str, url: str) -> str:
    identity = f"{source_kind}:{url.strip().lower()}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:16]


def describe_agent_impact(topics: Iterable[str]) -> str:
    topic_set = set(topics)
    if "control_plane" in topic_set:
        return "重点不是又多了一个功能，而是它是否改变长任务的状态、恢复、协作或人工接管边界。"
    if "memory_context_skills" in topic_set:
        return "它可能改变 Agent 如何保留信息、装配上下文或复用能力，值得检查效果是否可测且可迁移。"
    return "它提供了可复查的复杂任务证据，适合用来校准成功率、成本、恢复能力和人工介入等指标。"


def draft_x_angle(topics: Iterable[str]) -> str:
    topic_set = set(topics)
    if "control_plane" in topic_set:
        return "从控制平面切入：模型更强之后，真正限制长时 Agent 的可能是状态、恢复、审批和多 Agent 冲突。"
    if "memory_context_skills" in topic_set:
        return "从可量化性切入：不要只问有没有 memory/context/skill，要问它让哪类任务成功率提升了多少、代价是什么。"
    return "从评测切入：把演示改写成可复现实验，报告任务长度、成功条件、人工介入、恢复次数与总成本。"


def release_to_agent_signal(
    release: GitHubRelease,
    source: AgentReleaseSource,
) -> AgentSignal:
    topics = source.topics
    return AgentSignal(
        signal_id=agent_signal_id("first_party_release", release.url),
        title=f"{release.repository} {release.tag}",
        url=release.url,
        publisher=source.publisher,
        source_kind="first_party_release",
        published_at=release.published_at,
        topics=topics,
        priority=source.priority,
        evidence_level="first_party",
        summary=release.summary,
        why_it_matters=describe_agent_impact(topics),
        x_angle=draft_x_angle(topics),
    )


def parse_research_jsonl(
    payload: str,
    source: AgentResearchFeed,
    published_at: dt.datetime,
) -> list[AgentSignal]:
    signals: list[AgentSignal] = []
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError:
            print(
                f"Warning: invalid JSONL at {source.repository}:{line_number}",
                file=sys.stderr,
            )
            continue
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        url = str(item.get("abs") or "").strip()
        searchable = f"{title} {summary}".lower()
        if (
            not title
            or not url.startswith("https://arxiv.org/abs/")
            or not is_agent_relevant(searchable)
        ):
            continue
        topics = infer_agent_topics(searchable) or ("agent_evaluation",)
        signals.append(
            AgentSignal(
                signal_id=agent_signal_id("author_preprint", url),
                title=title,
                url=url,
                publisher=source.publisher,
                source_kind="author_preprint",
                published_at=published_at,
                topics=topics,
                priority=source.priority,
                evidence_level="author_preprint",
                summary=summarize_release(summary),
                why_it_matters=describe_agent_impact(topics),
                x_angle=draft_x_angle(topics),
            )
        )
    return signals


def fetch_agent_research_signals(
    feeds: Iterable[AgentResearchFeed],
    generated_at: dt.datetime,
    window_days: int,
) -> list[AgentSignal]:
    signals: list[AgentSignal] = []
    generated_utc = generated_at.astimezone(dt.timezone.utc)
    for feed in feeds:
        for offset in range(window_days):
            date = generated_utc.date() - dt.timedelta(days=offset)
            path = feed.path_template.format(date=date.isoformat())
            encoded_path = urllib.parse.quote(path, safe="/")
            url = (
                f"https://raw.githubusercontent.com/{feed.repository}/"
                f"{feed.branch}/{encoded_path}"
            )
            try:
                payload = fetch_text(url)
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    print(f"Warning: unable to fetch research feed {url}: {exc}", file=sys.stderr)
                continue
            except Exception as exc:
                print(f"Warning: unable to fetch research feed {url}: {exc}", file=sys.stderr)
                continue
            published_at = dt.datetime.combine(date, dt.time(), tzinfo=dt.timezone.utc)
            signals.extend(parse_research_jsonl(payload, feed, published_at))
    return signals


def rank_agent_signals(signals: Iterable[AgentSignal], limit: int) -> list[AgentSignal]:
    unique: dict[str, AgentSignal] = {}
    for signal in signals:
        unique.setdefault(signal.signal_id, signal)
    ranked = sorted(
        unique.values(),
        key=lambda signal: (
            AGENT_PRIORITY_ORDER[signal.priority],
            -signal.published_at.timestamp(),
            -len(signal.topics),
            signal.title.lower(),
        ),
    )
    return ranked[:limit]


def collect_agent_signals(
    release_sources: Iterable[AgentReleaseSource],
    research_feeds: Iterable[AgentResearchFeed],
    generated_at: dt.datetime,
    window_days: int,
    limit: int,
) -> list[AgentSignal]:
    release_source_list = list(release_sources)
    source_by_repository = {
        source.repository: source for source in release_source_list
    }
    since = generated_at.astimezone(dt.timezone.utc) - dt.timedelta(days=window_days)
    releases = fetch_watched_releases(
        source_by_repository,
        since=since,
        limit=len(source_by_repository),
    )
    signals = [
        release_to_agent_signal(release, source_by_repository[release.repository])
        for release in releases
    ]
    signals.extend(
        fetch_agent_research_signals(research_feeds, generated_at, window_days)
    )
    return rank_agent_signals(signals, limit)


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


def format_agent_radar(
    signals: Iterable[AgentSignal],
    generated_at: dt.datetime,
) -> str:
    lines = [
        "# Daily Agent Radar",
        "",
        f"Generated at: {generated_at.strftime('%Y-%m-%d %H:%M %Z').strip()}",
        "",
        "> Draft only: this report does not publish to X or send Telegram messages.",
        "",
        "## Signals",
        "",
    ]
    signal_list = list(signals)
    if not signal_list:
        lines.append("No qualifying first-party release or Agent research signal was found in this window.")
        return "\n".join(lines)

    for index, signal in enumerate(signal_list, start=1):
        topic_labels = "、".join(
            AGENT_TOPIC_LABELS.get(topic, topic) for topic in signal.topics
        )
        published_label = signal.published_at.astimezone(dt.timezone.utc).strftime(
            "%Y-%m-%d UTC"
        )
        lines.extend(
            [
                f"### {index}. [{signal.title}]({signal.url})",
                "",
                f"- 发生了什么：{signal.summary}",
                f"- 为什么重要：{signal.why_it_matters}",
                f"- 主题：{topic_labels}",
                f"- 优先级：{signal.priority}",
                f"- 证据：{signal.evidence_level} · {signal.publisher} · {published_label}",
                f"- X 草稿角度：{signal.x_angle}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


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
        description="Collect a low-noise weekly digest or a draft-only Agent Radar."
    )
    parser.add_argument(
        "--agent-radar",
        action="store_true",
        help="Generate a daily Agent Radar draft without sending Telegram.",
    )
    parser.add_argument(
        "--agent-sources-file",
        default="agent_sources.json",
        help="Tracked Agent Radar source registry.",
    )
    parser.add_argument(
        "--agent-limit",
        type=int,
        default=5,
        help=f"Maximum Agent Radar signals to include (1-{MAX_AGENT_RADAR_ITEMS}).",
    )
    parser.add_argument(
        "--agent-window-days",
        type=int,
        default=2,
        help="Agent Radar lookback window in days (1-7).",
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
    if not args.agent_radar and sum(limits) > MAX_REPORT_ITEMS:
        parser.error(f"combined item limits must not exceed {MAX_REPORT_ITEMS}")
    if not 1 <= args.agent_limit <= MAX_AGENT_RADAR_ITEMS:
        parser.error(f"agent limit must be between 1 and {MAX_AGENT_RADAR_ITEMS}")
    if not 1 <= args.agent_window_days <= 7:
        parser.error("agent window must be between 1 and 7 days")
    return args


def main() -> int:
    args = parse_args()
    global SSL_CONTEXT
    project_root = Path(__file__).resolve().parent
    load_env(project_root / ".env")
    SSL_CONTEXT = build_ssl_context(allow_insecure=args.insecure)

    generated_at = dt.datetime.now().astimezone()
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.agent_radar:
        source_path = Path(args.agent_sources_file)
        if not source_path.is_absolute():
            source_path = project_root / source_path
        release_sources, research_feeds = load_agent_sources(source_path)
        signals = collect_agent_signals(
            release_sources,
            research_feeds,
            generated_at=generated_at,
            window_days=args.agent_window_days,
            limit=args.agent_limit,
        )
        report = format_agent_radar(signals, generated_at)
        timestamp = generated_at.strftime("%Y%m%d-%H%M%S")
        report_path = output_dir / f"daily-agent-radar-{timestamp}.md"
        report_path.write_text(report + "\n", encoding="utf-8")
        print(f"Agent Radar draft written to {report_path}")
        return 0

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
