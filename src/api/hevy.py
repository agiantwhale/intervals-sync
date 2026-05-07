import os
from datetime import datetime, timedelta, timezone

import requests

HEVY_BASE = "https://api.hevyapp.com/v1"


class HevyAPI:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ["HEVY_API_KEY"]
        self.session = requests.Session()
        self.session.headers.update({
            "api-key": self.api_key,
            "accept": "application/json",
        })

    def _get(self, path, **params):
        r = self.session.get(f"{HEVY_BASE}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_workouts_page(self, page=1, page_size=10):
        return self._get("/workouts", page=page, pageSize=page_size)

    def get_workout(self, workout_id):
        return self._get(f"/workouts/{workout_id}")

    def get_recent_workouts(self, days=7):
        """Yield workouts whose start_time falls within the last `days` days,
        newest first. Stops paginating as soon as a workout falls outside the window."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        page = 1
        while True:
            data = self.get_workouts_page(page=page, page_size=10)
            workouts = data.get("workouts", [])
            if not workouts:
                return
            for w in workouts:
                start = _parse_iso_utc(w["start_time"])
                if start < cutoff:
                    return
                yield w
            page_count = data.get("page_count", page)
            if page >= page_count:
                return
            page += 1


def _parse_iso_utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
