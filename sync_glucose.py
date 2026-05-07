import bisect
from datetime import datetime, timedelta

from src.api.intervals import IntervalsAPI
from src.api.nightscout import NightscoutAPI


def stream_exists(activity):
    """Check if the bloodglucose stream is already present."""
    return 'bloodglucose' in activity.get('stream_types', [])


def time_stream(streams):
    for stream in streams:
        if stream.get("type") == "time":
            return stream.get("data") or []
    raise ValueError("Could not find time stream")


def linear_interpolate(time_values, seconds, values):
    if not seconds or not values:
        return []

    result = []
    for t in time_values:
        idx = bisect.bisect_left(seconds, t)
        if idx == 0:
            result.append(values[0])
        elif idx == len(seconds):
            result.append(values[-1])
        else:
            t0, t1 = seconds[idx - 1], seconds[idx]
            v0, v1 = values[idx - 1], values[idx]
            if t1 == t0:
                result.append(v1)
            else:
                weight = (t - t0) / (t1 - t0)
                result.append(v0 + weight * (v1 - v0))
    return result


def sync_glucose():
    # Load configuration from environment
    intervals = IntervalsAPI()
    nightscout = NightscoutAPI()
    
    # Fetch activities from the last 3 days
    oldest = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    activities = intervals.get_activities(oldest=oldest)
    
    # Filter for exact 3-day window based on start_date_local
    cutoff = datetime.now() - timedelta(days=3)
    recent_activities = [a for a in activities if datetime.fromisoformat(a['start_date_local'].replace('Z', '')) > cutoff]
    
    print(f"Checking {len(recent_activities)} recent activities...")

    for activity in recent_activities:
        a_id = activity['id']
        if stream_exists(activity):
            print(f" - Activity {a_id}: Glucose already exists. Skipping.")
            continue
        
        print(f" - Activity {a_id}: Missing glucose. Syncing...")
        
        # Get start time for Nightscout window
        start_dt = datetime.fromisoformat(activity['start_date'].replace('Z', '+00:00'))
        vals, secs = nightscout.get_glucose(start_dt, activity['elapsed_time'])
        
        if vals:
            # Fetch existing time stream to get the correct length and sampling for interpolation
            streams = intervals.get_streams(a_id, types=['time'])
            
            # If the response is not a list (e.g. error dict), something is wrong
            if not isinstance(streams, list):
                print(f"   Could not retrieve streams for activity {a_id}: {streams}")
                continue

            # Interpolate data to match the activity's time stream
            try:
                interpolated_glucose = linear_interpolate(time_stream(streams), secs, vals)
                
                # Upload the interpolated stream
                intervals.upload_custom_stream(a_id, "bloodglucose", interpolated_glucose)
                print(f"   Done! Injected {len(interpolated_glucose)} data points.")
                
            except Exception as e:
                print(f"   Error merging/uploading data: {e}")
        else:
            print("   No data found in Nightscout for this window.")

if __name__ == "__main__":
    sync_glucose()
