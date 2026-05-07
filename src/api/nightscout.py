import os
from datetime import timedelta

import requests


class NightscoutAPI:
    def __init__(self, base_url=None, token=None):
        self.base_url = (base_url or os.environ["NS_URL"]).rstrip("/")
        self.token = token if token is not None else os.environ.get("NS_TOKEN")
        self.session = requests.Session()
        self.session.headers.update({"accept": "application/json"})

    def get_glucose(self, start_dt, duration_secs):
        end_dt = start_dt + timedelta(seconds=duration_secs)
        params = {
            "find[date][$gte]": int(start_dt.timestamp() * 1000),
            "find[date][$lte]": int(end_dt.timestamp() * 1000),
            "count": 1000,
        }
        if self.token:
            params["token"] = self.token

        r = self.session.get(
            f"{self.base_url}/api/v1/entries/sgv.json",
            params=params,
            timeout=30,
        )
        r.raise_for_status()

        values = []
        seconds = []
        for entry in reversed(r.json()):
            timestamp = entry["date"] / 1000.0
            offset = int(timestamp - start_dt.timestamp())
            if 0 <= offset <= duration_secs:
                values.append(entry["sgv"])
                seconds.append(offset)

        if seconds and seconds[0] > 0:
            seconds.insert(0, 0)
            values.insert(0, values[0])

        return values, seconds
