#!/usr/bin/env python3
"""Weekly collector for GitHub Trending and Hacker News with Telegram delivery."""

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
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


GITHUB_TRENDING_URL = "https://github.com/trending?since=weekly"
HN_TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
TELEGRAM_API_BASE = "https://api.telegram.org"
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
    with urllib.request.urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        return response.read().decode("utf-8", errors="replace")


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
        description="Collect weekly GitHub Trending and Hacker News stories."
    )
    parser.add_argument("--github-limit", type=int, default=10)
    parser.add_argument("--hn-limit", type=int, default=10)
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
    return parser.parse_args()


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
    report = format_report(github_repos, hn_stories, generated_at)

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
