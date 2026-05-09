"""Sync planned-workout descriptions from Intervals.icu to Strava run activities.

Modes:
  - Single: python sync_strava.py --activity-id 12345678901
  - Backfill: python sync_strava.py [--count 20]

For each Strava run we look up the matching Intervals.icu activity (external_id
first, then start-time + duration window). If that activity has a paired
calendar event with a description, we mirror it into the Strava activity's
description. Re-runs no-op via full-content equality against the current
Strava description.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

from src.api.fitness_mcp import MCPIntervalsAPI, MCPStravaAPI, _clean_for_strava, _parse_iso_utc


STRAVA_RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
MATCH_WINDOW_SECONDS = 120


def is_strava_run(activity):
    sport = (activity.get("sport_type") or activity.get("type") or "").strip()
    return sport in STRAVA_RUN_TYPES


def is_intervals_run(activity):
    t = (activity.get("type") or "").lower()
    return "run" in t


def normalize_title(title):
    return " ".join((title or "").casefold().split())


def find_matching_intervals(strava_activity, intervals_activities):
    sid = strava_activity["id"]
    ext_id = f"strava-{sid}"

    by_ext = next((a for a in intervals_activities if a.get("external_id") == ext_id), None)
    if by_ext:
        return by_ext

    try:
        s_start = _parse_iso_utc(strava_activity["start_date"])
    except (KeyError, ValueError):
        return None
    s_dur = strava_activity.get("elapsed_time") or 0

    candidates = []
    for a in intervals_activities:
        if not is_intervals_run(a):
            continue
        try:
            a_start = _parse_iso_utc(a["start_date"])
        except (KeyError, ValueError):
            continue
        a_dur = a.get("elapsed_time") or 0
        if abs((a_start - s_start).total_seconds()) > MATCH_WINDOW_SECONDS:
            continue
        if abs(a_dur - s_dur) > MATCH_WINDOW_SECONDS:
            continue
        candidates.append(a)

    if not candidates:
        return None

    title = normalize_title(strava_activity.get("name"))
    title_match = next(
        (a for a in candidates if normalize_title(a.get("name")) == title),
        None,
    )
    return title_match or candidates[0]


def sync_one_activity(strava, intervals, strava_activity, intervals_activities):
    sid = strava_activity["id"]

    if not is_strava_run(strava_activity):
        print(
            f" - Strava {sid}: not a run "
            f"(sport_type={strava_activity.get('sport_type')}). Skipping."
        )
        return

    match = find_matching_intervals(strava_activity, intervals_activities)
    if not match:
        print(f" - Strava {sid}: no matching Intervals.icu activity in the search window.")
        return

    event_id = match.get("paired_event_id")
    if not event_id:
        print(
            f" - Strava {sid} ↔ Intervals {match.get('id')}: "
            "no paired planned event. Skipping."
        )
        return

    try:
        event = intervals.get_event(event_id)
    except Exception as e:
        print(
            f" - Strava {sid}: could not fetch Intervals event {event_id}: {e}",
            file=sys.stderr,
        )
        return

    # Repair Intervals.icu mojibake, then strip Markdown (Strava renders the
    # description as plain text — bullets become "    ⦁ ", emphasis/headers/
    # code markers drop, links flatten to "label (url)").
    new_desc = _clean_for_strava((event.get("description") or "").strip())
    if not new_desc:
        print(
            f" - Strava {sid} ↔ Intervals event {event_id}: "
            "event has no description. Skipping."
        )
        return

    # SummaryActivity (from list_activities) lacks `description`; only the
    # detailed shape returned by get_activity has it. Refresh if missing so
    # idempotency comparisons aren't always against an empty string.
    current_raw = strava_activity.get("description")
    if current_raw is None:
        try:
            current_raw = (strava.get_activity(sid).get("description") or "")
        except Exception as e:
            print(
                f" - Strava {sid}: failed to fetch detailed activity for compare: {e}",
                file=sys.stderr,
            )
            current_raw = ""
    current = (current_raw or "").strip()
    if current == new_desc.strip():
        print(f" - Strava {sid}: already in sync with Intervals event {event_id}.")
        return

    try:
        strava.update_activity(sid, description=new_desc)
        print(f" - Strava {sid}: updated description from Intervals event {event_id}.")
    except Exception as e:
        print(f" - Strava {sid}: update failed: {e}", file=sys.stderr)


def sync_one(activity_id):
    strava = MCPStravaAPI()
    intervals = MCPIntervalsAPI()

    activity = strava.get_activity(activity_id)
    try:
        s_start = _parse_iso_utc(activity["start_date"])
    except (KeyError, ValueError):
        print(f"Strava {activity_id}: missing start_date, cannot match.", file=sys.stderr)
        return

    oldest = (s_start - timedelta(days=2)).strftime("%Y-%m-%d")
    newest = (s_start + timedelta(days=2)).strftime("%Y-%m-%d")
    intervals_activities = intervals.get_activities(oldest=oldest, newest=newest)
    sync_one_activity(strava, intervals, activity, intervals_activities)


def sync_backfill(count=None, days=None):
    strava = MCPStravaAPI()
    intervals = MCPIntervalsAPI()

    # per_page=100 keeps each chunk small enough for the MCP streamable-HTTP
    # response; the wrapper paginates as needed.
    if days is not None:
        after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        activities = strava.list_activities(after=after, per_page=100)
        scope = f"the last {days} day(s)"
    else:
        activities = strava.list_activities(per_page=100, limit=count)
        scope = f"the last {count} activities"
    runs = [a for a in activities if is_strava_run(a)]
    print(f"Backfill: found {len(runs)} Strava run(s) in {scope}.")
    if not runs:
        return

    # Pull Intervals activities covering the span of the runs we'll match against.
    starts = []
    for r in runs:
        try:
            starts.append(_parse_iso_utc(r["start_date"]))
        except (KeyError, ValueError):
            continue
    if not starts:
        print("No usable start_date on any returned run; nothing to match.")
        return
    oldest_start = min(starts)
    oldest = (oldest_start - timedelta(days=2)).strftime("%Y-%m-%d")
    intervals_activities = intervals.get_activities(oldest=oldest)
    print(f"Fetched {len(intervals_activities)} Intervals.icu activities for matching.")

    for s_act in runs:
        sync_one_activity(strava, intervals, s_act, intervals_activities)


def parse_args(argv):
    activity_id = None
    count = int(os.environ.get("STRAVA_SYNC_COUNT", "20"))
    days = None
    if os.environ.get("STRAVA_SYNC_DAYS"):
        days = int(os.environ["STRAVA_SYNC_DAYS"])
    i = 0
    while i < len(argv):
        if argv[i] == "--activity-id":
            activity_id = int(argv[i + 1])
            i += 2
        elif argv[i] == "--count":
            count = int(argv[i + 1])
            i += 2
        elif argv[i] == "--days":
            days = int(argv[i + 1])
            i += 2
        else:
            print(f"Unknown arg: {argv[i]}", file=sys.stderr)
            sys.exit(2)
    return activity_id, count, days


def main():
    activity_id, count, days = parse_args(sys.argv[1:])
    if activity_id is not None:
        sync_one(activity_id)
    elif days is not None:
        sync_backfill(days=days)
    else:
        sync_backfill(count=count)


if __name__ == "__main__":
    main()
