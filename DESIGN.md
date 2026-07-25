# Weekly Tech Collector Design

## Goal

Deliver one weekly Telegram digest containing GitHub Trending repositories and
Hacker News stories, without relying on a developer laptop being awake.

## Delivery architecture

```text
GitHub Actions (primary, Sunday 08:17 Beijing)
  -> collect sources -> write local artifact -> send Telegram
  -> commit successful-delivery heartbeat

macOS launchd (fallback, Sunday 18:00 Beijing)
  -> query today's GitHub Actions result
  -> skip when primary succeeded; otherwise collect and send Telegram
```

GitHub Actions is the primary scheduler because it runs independently of the
Mac. The workflow is deliberately scheduled at minute 17 rather than minute 0:
GitHub documents that schedules at the start of an hour can be delayed or
dropped under load.

## Inactivity protection

Public repositories have scheduled workflows automatically disabled after 60
days without repository activity. A successful scheduled run therefore writes
`.github/weekly-tech-collector-heartbeat.json` and commits it with the
`github-actions[bot]` identity. A failed collection or Telegram delivery never
writes the heartbeat, so a green heartbeat always represents a delivered
digest.

The workflow has `contents: write` permission solely for that heartbeat commit.
The heartbeat contains no source content, Telegram credential, or chat ID.

## Local fallback behavior

`launchd` replaces `cron`. Unlike `cron`, a `StartCalendarInterval` job missed
while macOS sleeps is run once after the Mac wakes. Before sending, the local
job checks the public GitHub Actions API for a successful scheduled delivery on
the current Beijing calendar day. It skips in the normal case and sends only
when the cloud run is absent or the API cannot be reached.

The fallback check intentionally fails open: loss of API connectivity produces
a potentially duplicate message rather than silently losing the week's digest.

## Failure and recovery

- A collector or Telegram failure marks the GitHub Actions run failed and does
  not write a heartbeat.
- A disabled workflow is restored with `gh workflow enable weekly-report.yml`.
- The local launch agent writes both output streams to `collector.log`.
- Reports remain local and ignored by Git; they are not used as delivery state.

## Operational checks

```bash
gh workflow list --all
gh run list --workflow weekly-report.yml --limit 10
launchctl print "gui/$(id -u)/com.lihua.weekly-tech-collector"
tail -n 100 collector.log
```
