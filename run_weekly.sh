#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

# GitHub Actions is the primary delivery path. This local job only sends a
# fallback when the current Beijing calendar day has no successful scheduled
# GitHub Actions delivery. If GitHub is unavailable, the check fails open so a
# local run can still deliver the digest.
if /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 - <<'PY'
import datetime as dt
import json
import ssl
import sys
import urllib.request
from zoneinfo import ZoneInfo

url = (
    "https://api.github.com/repos/LIHUA919/Search-Agent/actions/workflows/"
    "weekly-report.yml/runs?event=schedule&status=completed&per_page=20"
)
request = urllib.request.Request(
    url,
    headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "weekly-tech-collector-local-fallback",
    },
)

try:
    with urllib.request.urlopen(
        request, timeout=15, context=ssl._create_unverified_context()
    ) as response:
        runs = json.loads(response.read().decode("utf-8")).get("workflow_runs", [])
    beijing = ZoneInfo("Asia/Shanghai")
    today = dt.datetime.now(beijing).date()
    delivered = any(
        run.get("conclusion") == "success"
        and dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        .astimezone(beijing)
        .date()
        == today
        for run in runs
    )
except Exception:
    delivered = False

raise SystemExit(0 if delivered else 1)
PY
then
  echo "Skipping local delivery: GitHub Actions already delivered today's digest."
  exit 0
fi

/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 collector.py --insecure
