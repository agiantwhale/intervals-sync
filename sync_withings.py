import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.api.fitness_mcp import MCPIntervalsAPI, MCPWithingsAPI

KG_TO_LB = 2.2046226218

# Withings measure type IDs (see withings_get_measurements tool docs).
TYPE_WEIGHT = 1
TYPE_FAT_RATIO = 6
TYPE_MUSCLE_KG = 76
TYPE_HYDRATION_KG = 77
TYPE_BONE_KG = 88

WANTED_TYPES = [
    TYPE_WEIGHT,
    TYPE_FAT_RATIO,
    TYPE_MUSCLE_KG,
    TYPE_HYDRATION_KG,
    TYPE_BONE_KG,
]

DEFAULT_TZ = "America/New_York"


def decode_measure(m):
    return m["value"] * (10 ** m["unit"])


def reading_from_group(group):
    out = {}
    for m in group.get("measures", []):
        t = m.get("type")
        if t in WANTED_TYPES:
            out[t] = decode_measure(m)
    return out


def to_wellness_body(reading):
    weight_kg = reading.get(TYPE_WEIGHT)
    if weight_kg is None or weight_kg <= 0:
        return None
    body = {"weight": round(weight_kg, 3)}
    if TYPE_FAT_RATIO in reading:
        body["bodyFat"] = round(reading[TYPE_FAT_RATIO], 2)
    if TYPE_HYDRATION_KG in reading:
        body["BodyWater"] = round(reading[TYPE_HYDRATION_KG] / weight_kg * 100, 2)
    if TYPE_MUSCLE_KG in reading:
        body["MuscleMassLB"] = round(reading[TYPE_MUSCLE_KG] * KG_TO_LB, 2)
    if TYPE_BONE_KG in reading:
        body["BoneMassLB"] = round(reading[TYPE_BONE_KG] * KG_TO_LB, 2)
    return body


def sync_withings():
    intervals = MCPIntervalsAPI()
    withings = MCPWithingsAPI()

    days = int(os.environ.get("WITHINGS_SYNC_DAYS", "7"))
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    resp = withings.get_body_measurements(start, end, meastypes=WANTED_TYPES)
    if not isinstance(resp, dict):
        print(f"Unexpected Withings response: {resp!r}")
        return

    groups = resp.get("measuregrps") or []
    if not groups:
        print(f"No Withings measurements in last {days} days.")
        return

    tz_name = resp.get("timezone") or os.environ.get("WITHINGS_TZ") or DEFAULT_TZ
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        print(f"Unknown timezone {tz_name!r}, falling back to {DEFAULT_TZ}.")
        local_tz = ZoneInfo(DEFAULT_TZ)

    # Bucket by local date; later (higher `date`) reading wins.
    by_date = {}
    for g in sorted(groups, key=lambda g: g.get("date", 0)):
        ts = datetime.fromtimestamp(g["date"], tz=timezone.utc).astimezone(local_tz)
        by_date[ts.strftime("%Y-%m-%d")] = g

    print(f"Syncing {len(by_date)} day(s) of Withings readings...")

    for date_str, group in sorted(by_date.items()):
        reading = reading_from_group(group)
        body = to_wellness_body(reading)
        if body is None:
            print(f" - {date_str}: skipping reading {group.get('grpid')} (no weight)")
            continue
        body["id"] = date_str
        try:
            intervals.update_wellness(body)
            fields = [k for k in body if k != "id"]
            print(f" - {date_str}: synced {fields}")
        except Exception as e:
            print(f" - {date_str}: ERROR {e}")


if __name__ == "__main__":
    sync_withings()
