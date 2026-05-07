import os

import requests

INTERVALS_BASE = "https://intervals.icu/api/v1"


class IntervalsAPI:
    def __init__(self, athlete_id=None, api_key=None):
        self.athlete_id = athlete_id or os.environ.get("INTERVALS_ID", "0")
        self.api_key = api_key or os.environ["INTERVALS_API_KEY"]
        self.session = requests.Session()
        self.session.auth = ("API_KEY", self.api_key)
        self.session.headers.update({"accept": "application/json"})

    def get_activities(self, oldest, newest=None):
        url = f"{INTERVALS_BASE}/athlete/{self.athlete_id}/activities"
        params = {"oldest": oldest}
        if newest:
            params["newest"] = newest
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_streams(self, activity_id, types=None):
        url = f"{INTERVALS_BASE}/activity/{activity_id}/streams.json"
        params = {}
        if types:
            params["types"] = ",".join(types)
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def upload_custom_stream(self, activity_id, stream_type, data):
        url = f"{INTERVALS_BASE}/activity/{activity_id}/streams"
        payload = [{"type": stream_type, "data": data}]
        r = self.session.put(url, json=payload, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else None
