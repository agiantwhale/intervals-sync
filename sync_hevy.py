import os
import sys
from datetime import datetime, timedelta

from src.api.fitness_mcp import MCPHevyAPI, MCPIntervalsAPI, _ascii_clean, _parse_iso_utc


def normalize_title(title):
    return " ".join((title or "").casefold().split())


def workout_title(workout):
    return _ascii_clean((workout.get("title") or "Strength Training").strip())


def calculate_kg_lifted(workout):
    total = 0
    for exercise in workout.get("exercises", []):
        for s in exercise.get("sets", []):
            weight = s.get("weight_kg")
            reps = s.get("reps")
            if weight is None or reps is None:
                continue
            total += weight * reps
    return round(total, 3) if total > 0 else None


def format_set(s):
    parts = []
    set_type = (s.get("set_type") or "normal").lower()
    if set_type != "normal":
        parts.append(set_type.upper())
    weight = s.get("weight_kg")
    reps = s.get("reps")
    duration = s.get("duration_seconds")
    distance = s.get("distance_meters")
    rpe = s.get("rpe")
    if reps is not None:
        # ASCII 'x' instead of '×' — Intervals.ICU's event API stores the
        # multiplication sign as mojibake, breaking idempotency comparisons.
        parts.append(f"{weight:g}kg x {reps}" if weight else f"{reps} reps")
    if duration:
        parts.append(f"{duration}s")
    if distance:
        parts.append(f"{distance:g}m")
    if rpe:
        parts.append(f"RPE {rpe:g}")
    return "  - " + ", ".join(parts) if parts else "  - (empty set)"


def render_description(workout):
    lines = []
    for ex in workout.get("exercises", []):
        title = ex.get("title", "Exercise")
        superset = ex.get("superset_id")
        header = f"### {title}"
        if superset is not None:
            header += f"  (superset {superset})"
        lines.append(header)
        if ex.get("notes"):
            lines.append(ex["notes"])
        for s in ex.get("sets", []):
            lines.append(format_set(s))
        lines.append("")
    if workout.get("description"):
        lines.append(workout["description"])
    return _ascii_clean("\n".join(lines).strip())


def event_marker(workout):
    return f"[hevy-event:{workout['id']}]"


def render_event_description(workout):
    desc = render_description(workout)
    return f"{desc}\n\n{event_marker(workout)}"


def is_strength_activity(activity):
    activity_type = activity.get("type")
    if activity_type is None:
        return True
    return normalize_title(activity_type) in {
        "weighttraining",
        "weight training",
        "strength",
        "strengthtraining",
        "strength training",
    }


def activity_matches_workout(activity, start_utc, duration):
    if not is_strength_activity(activity):
        return False

    try:
        a_start = _parse_iso_utc(activity["start_date"])
        a_duration = activity.get("elapsed_time", 0)
    except (KeyError, ValueError):
        return False

    # Matching criteria:
    # - Start time within 2 minutes
    # - Duration within 2 minutes
    time_diff = abs((a_start - start_utc).total_seconds())
    dur_diff = abs(a_duration - duration)
    return time_diff < 120 and dur_diff < 120


def find_matching_activity(activities, workout, ext_id, start_utc, duration):
    # 1. Try to find by external_id first
    match = next((a for a in activities if a.get("external_id") == ext_id), None)
    if match:
        return match

    # 2. If not found, match by start time and duration. Prefer a title match
    # when there is more than one candidate in the time window.
    candidates = [
        a for a in activities
        if activity_matches_workout(a, start_utc, duration)
    ]
    if not candidates:
        return None

    title = normalize_title(workout_title(workout))
    title_match = next(
        (a for a in candidates if normalize_title(a.get("name")) == title),
        None,
    )
    return title_match or candidates[0]


def sync_workout(intervals, workout, intervals_activities):
    """Sync one Hevy workout into Intervals.icu by creating/updating a paired
    planned-event holding the workout structure and pairing it to the matched
    activity."""
    ext_id = f"hevy-{workout['id']}"
    start_utc = _parse_iso_utc(workout["start_time"])
    end_utc = _parse_iso_utc(workout["end_time"])
    duration = max(int((end_utc - start_utc).total_seconds()), 0)

    match = find_matching_activity(intervals_activities, workout, ext_id, start_utc, duration)
    if not match:
        print(
            f" - Hevy {workout['id']}: no matching Intervals activity "
            f"(start={start_utc}, duration={duration}s). Skipping."
        )
        return

    activity_id = match["id"]
    title = workout_title(workout)
    kg_lifted = calculate_kg_lifted(workout)
    marker = event_marker(workout)
    desired_event_desc = render_event_description(workout)

    paired_id = match.get("paired_event_id")
    paired_event = None
    if paired_id:
        try:
            paired_event = intervals.get_event(paired_id)
        except Exception as e:
            print(f"   Could not fetch paired event {paired_id}: {e}", file=sys.stderr)

    is_our_event = paired_event and marker in (paired_event.get("description") or "")

    # 1. Find / create / update the paired event
    if is_our_event:
        needs_update = (
            (paired_event.get("description") or "").strip() != desired_event_desc.strip()
            or paired_event.get("name") != title
        )
        if needs_update:
            try:
                intervals.update_event(paired_id, {
                    # start_date_local is required by the fitness-mcp event
                    # input schema even on PUT; pass through unchanged.
                    "start_date_local": paired_event.get("start_date_local"),
                    "name": title,
                    "description": desired_event_desc,
                })
                print(f" - Hevy {workout['id']}: updated paired event {paired_id}.")
            except Exception as e:
                print(f" - Hevy {workout['id']}: event update failed: {e}", file=sys.stderr)
                return
    elif paired_event:
        # Activity already paired to a non-Hevy event — don't clobber.
        print(
            f" - Hevy {workout['id']}: activity {activity_id} paired to non-Hevy "
            f"event {paired_id}. Skipping."
        )
        return
    else:
        start_local = match.get("start_date_local") or match.get("start_date")
        try:
            new_event = intervals.create_event({
                "start_date_local": start_local,
                "category": "WORKOUT",
                "type": "WeightTraining",
                "name": title,
                "description": desired_event_desc,
                "moving_time": duration,
            })
        except Exception as e:
            print(f" - Hevy {workout['id']}: event create failed: {e}", file=sys.stderr)
            return
        new_event_id = new_event.get("id")
        try:
            intervals.update_activity(activity_id, {"paired_event_id": new_event_id})
        except Exception as e:
            print(
                f" - Hevy {workout['id']}: pairing failed (event {new_event_id} created "
                f"but not paired): {e}",
                file=sys.stderr,
            )
            return
        paired_id = new_event_id
        print(f" - Hevy {workout['id']}: created event {new_event_id}, paired to activity {activity_id}.")

    # 2. Update the activity itself: workout details now live on the event, so
    #    clear description here. Keep name + kg_lifted + external_id on the activity.
    activity_payload = {
        "name": title,
        "external_id": ext_id,
        "description": "",
    }
    if kg_lifted is not None:
        activity_payload["kg_lifted"] = kg_lifted

    needs_activity_update = (
        match.get("name") != title
        or (match.get("description") or "").strip()
        or match.get("external_id") != ext_id
        or (kg_lifted is not None and match.get("kg_lifted") != kg_lifted)
    )
    if needs_activity_update:
        try:
            intervals.update_activity(activity_id, activity_payload)
        except Exception as e:
            print(f"   Activity {activity_id} update failed: {e}", file=sys.stderr)


def parse_args(argv):
    days = int(os.environ.get("HEVY_SYNC_DAYS", "7"))
    limit = None
    all_flag = False
    i = 0
    while i < len(argv):
        if argv[i] == "--days":
            days = int(argv[i + 1]); i += 2
        elif argv[i] == "--limit":
            limit = int(argv[i + 1]); i += 2
        elif argv[i] == "--all":
            all_flag = True; i += 1
        else:
            print(f"Unknown arg: {argv[i]}", file=sys.stderr)
            sys.exit(2)
    return days, limit, all_flag


def sync_hevy():
    days, limit, all_flag = parse_args(sys.argv[1:])
    hevy = MCPHevyAPI()
    intervals = MCPIntervalsAPI()

    if all_flag:
        workouts = list(hevy.get_all_workouts())
        print(f"Found {len(workouts)} Hevy workouts (all-time).")
    else:
        workouts = list(hevy.get_recent_workouts(days=days))
        print(f"Found {len(workouts)} Hevy workouts in the last {days} days.")

    if limit is not None:
        workouts = workouts[:limit]
        print(f"Capped to {len(workouts)} workout(s) by --limit.")

    if not workouts:
        return

    starts = [_parse_iso_utc(w["start_time"]) for w in workouts]
    oldest = (min(starts) - timedelta(days=2)).strftime("%Y-%m-%d")
    newest = (max(starts) + timedelta(days=2)).strftime("%Y-%m-%d")
    activities = intervals.get_activities(oldest=oldest, newest=newest)
    print(f"Fetched {len(activities)} Intervals.icu activities for matching.")

    for w in workouts:
        sync_workout(intervals, w, activities)


if __name__ == "__main__":
    sync_hevy()
