"""Minimal Intervals.icu client for creating manual activities.

Kept separate from the existing IntervalsAPI on purpose — the methods here are
additive and don't overlap with the streams/glucose code path.
"""
import os
from datetime import datetime, timedelta

import requests

INTERVALS_BASE = "https://intervals.icu/api/v1"


class IntervalsActivityClient:
    def __init__(self, athlete_id=None, api_key=None):
        self.athlete_id = athlete_id or os.environ["INTERVALS_ID"]
        self.api_key = api_key or os.environ["INTERVALS_API_KEY"]
        self.session = requests.Session()
        self.session.auth = ("API_KEY", self.api_key)
        self.session.headers.update({"accept": "application/json"})

    def find_activity_by_external_id(self, external_id, lookback_days=60):
        """Return the activity dict if one exists with this external_id, else None.

        Intervals.icu's list endpoint supports an `ext_id` filter on some
        deployments; we try it, then fall back to a client-side scan over the
        lookback window."""
        url = f"{INTERVALS_BASE}/athlete/{self.athlete_id}/activities"
        oldest = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        r = self.session.get(url, params={"oldest": oldest, "ext_id": external_id}, timeout=30)
        r.raise_for_status()
        items = r.json() if isinstance(r.json(), list) else []
        for a in items:
            if a.get("external_id") == external_id:
                return a
        return None

    def get_activities(self, oldest, newest=None):
        """Fetch activities between oldest and newest dates (inclusive).
        Dates should be 'YYYY-MM-DD'."""
        url = f"{INTERVALS_BASE}/athlete/{self.athlete_id}/activities"
        params = {"oldest": oldest}
        if newest:
            params["newest"] = newest
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def create_manual_activity(self, payload):
        """POST a JSON activity. Use this for non-file uploads (e.g. strength)."""
        url = f"{INTERVALS_BASE}/athlete/{self.athlete_id}/activities/manual"
        r = self.session.post(url, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def update_activity(self, activity_id, payload):
        """PUT a JSON activity update."""
        url = f"{INTERVALS_BASE}/activity/{activity_id}"
        r = self.session.put(url, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_activity_messages(self, activity_id):
        """Fetch comments/messages attached to an activity."""
        url = f"{INTERVALS_BASE}/activity/{activity_id}/messages"
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.json()

    def post_activity_message(self, activity_id, message):
        """Post a comment/message to an activity."""
        url = f"{INTERVALS_BASE}/activity/{activity_id}/messages"
        r = self.session.post(url, json={"content": message}, timeout=30)
        r.raise_for_status()
        return r.json()
