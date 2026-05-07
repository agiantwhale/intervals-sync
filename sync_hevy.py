import os
import sys
from datetime import datetime, timedelta

from src.api.hevy import HevyAPI, _parse_iso_utc
from src.api.intervals import IntervalsAPI


def normalize_title(title):
    return " ".join((title or "").casefold().split())


def workout_title(workout):
    return (workout.get("title") or "Strength Training").strip()


def calculate_kg_lifted(workout):
    total = 0
    for exercise in workout.get("exercises", []):
        for s in exercise.get("sets", []):
            weight = s.get("weight_kg")
            reps = s.get("reps")
            if weight is None or reps is None:
                continue
            total += weight * reps
    return round(total, 3)


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
        parts.append(f"{weight:g}kg × {reps}" if weight else f"{reps} reps")
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
    return "\n".join(lines).strip()


def message_marker(workout):
    return f"[hevy-sync:{workout['id']}]"


def render_message(workout, description):
    marker = message_marker(workout)
    if description:
        return f"{marker}\n{description}"
    return f"{marker}\nNo Hevy workout details found."


def message_text(message):
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        return message.get("content") or message.get("message") or message.get("text") or message.get("body") or ""
    return ""


def has_synced_message(intervals, activity_id, workout):
    marker = message_marker(workout)
    try:
        messages = intervals.get_activity_messages(activity_id)
    except Exception as e:
        print(f"   Could not fetch Intervals messages for {activity_id}: {e}", file=sys.stderr)
        return False

    return any(marker in message_text(m) for m in messages)


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


def sync_hevy():
    hevy = HevyAPI()
    intervals = IntervalsAPI()
    days = int(os.environ.get("HEVY_SYNC_DAYS", "7"))

    workouts = list(hevy.get_recent_workouts(days=days))
    print(f"Found {len(workouts)} Hevy workouts in the last {days} days.")

    if not workouts:
        return

    # Fetch Intervals activities for the lookback period
    oldest = (datetime.now() - timedelta(days=days + 2)).strftime("%Y-%m-%d")
    activities = intervals.get_activities(oldest=oldest)
    print(f"Fetched {len(activities)} activities from Intervals.icu for matching.")

    for w in workouts:
        ext_id = f"hevy-{w['id']}"
        start_utc = _parse_iso_utc(w["start_time"])
        end_utc = _parse_iso_utc(w["end_time"])
        w_duration = max(int((end_utc - start_utc).total_seconds()), 0)

        match = find_matching_activity(activities, w, ext_id, start_utc, w_duration)

        if match:
            title = workout_title(w)
            description = render_description(w)
            kg_lifted = calculate_kg_lifted(w)
            has_message = has_synced_message(intervals, match["id"], w)
            if (
                match.get("description") == description
                and match.get("name") == title
                and match.get("kg_lifted") == kg_lifted
                and has_message
            ):
                print(f" - Hevy {w['id']}: Already synced and up to date in Intervals {match['id']}. Skipping.")
                continue

            payload = {
                "name": title,
                "description": description,
                "external_id": ext_id,
                "kg_lifted": kg_lifted,
            }
            try:
                intervals.update_activity(match["id"], payload)
                if not has_message:
                    intervals.post_activity_message(match["id"], render_message(w, description))
                print(
                    f" - Hevy {w['id']}: Updated Intervals activity \"{title}\" "
                    f"({match['id']}) with workout details."
                )
            except Exception as e:
                print(f" - Hevy {w['id']}: Update failed for Intervals {match['id']}: {e}", file=sys.stderr)
        else:
            print(f" - Hevy {w['id']}: No matching Intervals activity found (Start: {start_utc}, Duration: {w_duration}s). Skipping.")


if __name__ == "__main__":
    sync_hevy()
