import json
import os
import unicodedata
from datetime import date, datetime, timedelta, timezone

import requests


def _parse_iso_utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def mcp_configured():
    return bool(os.environ.get("FITNESS_MCP_URL") and os.environ.get("FITNESS_MCP_TOKEN"))


# Fitness data sometimes round-trips through fitness-mcp / Intervals.icu / Strava
# with UTF-8 bytes interpreted as Latin-1 ("mojibake"). Strava's Latin-1 form
# decoding adds another layer on top, breaking display + idempotency. Fold to
# ASCII at the boundary to keep things stable.
_PUNCT_FOLD = {
    "–": "-", "—": "-",         # en/em dash
    "‘": "'", "’": "'",         # curly single quotes
    "“": '"', "”": '"',         # curly double quotes
    "×": "x", "•": "*", "·": "*",
}


def _ascii_clean(s):
    if not s:
        return s
    try:
        s = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    for k, v in _PUNCT_FOLD.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.encode("ascii", "replace").decode("ascii").replace("?", "-")


class FitnessMCPClient:
    def __init__(self, url=None, token=None):
        self.url = (url or os.environ["FITNESS_MCP_URL"]).rstrip("/")
        if not self.url.endswith("/mcp"):
            self.url = f"{self.url}/mcp"
        self.token = token or os.environ["FITNESS_MCP_TOKEN"]
        self.session = requests.Session()
        self.session.headers.update({
            "authorization": f"Bearer {self.token}",
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        })
        self._next_id = 1
        self._initialized = False

    def call_tool(self, name, arguments=None):
        self._initialize()
        result = self._rpc("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        return self._decode_tool_result(result)

    def _initialize(self):
        if self._initialized:
            return
        self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {
                "name": "intervals-sync",
                "version": "0.1.0",
            },
        })
        self._notify("notifications/initialized")
        self._initialized = True

    def _notify(self, method, params=None):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        self._post(payload)

    def _rpc(self, method, params=None):
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        data = self._post(payload)
        if "error" in data:
            raise RuntimeError(f"MCP {method} failed: {data['error']}")
        return data.get("result")

    def _post(self, payload):
        r = self.session.post(self.url, data=json.dumps(payload), timeout=60)
        if "mcp-session-id" in r.headers:
            self.session.headers["mcp-session-id"] = r.headers["mcp-session-id"]
        r.raise_for_status()
        if not r.content:
            return {}
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            return self._parse_event_stream(r.text)
        return r.json()

    def _parse_event_stream(self, text):
        for line in text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    return json.loads(data)
        return {}

    def _decode_tool_result(self, result):
        if not isinstance(result, dict):
            return result
        content = result.get("content") or []
        text = ""
        if content and content[0].get("type") == "text":
            text = content[0].get("text", "")
        if result.get("isError"):
            raise RuntimeError(f"MCP tool error: {text or result}")
        if not content:
            return result
        if content[0].get("type") != "text":
            return result
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


class MCPHevyAPI:
    def __init__(self, client=None):
        self.client = client or FitnessMCPClient()

    def get_workouts_page(self, page=1, page_size=10):
        return self.client.call_tool("hevy_list_workouts", {
            "page": page,
            "pageSize": page_size,
        })

    def get_workout(self, workout_id):
        return self.client.call_tool("hevy_get_workout", {"workoutId": workout_id})

    def get_recent_workouts(self, days=7):
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

    def get_all_workouts(self):
        page = 1
        while True:
            data = self.get_workouts_page(page=page, page_size=10)
            workouts = data.get("workouts", [])
            if not workouts:
                return
            for w in workouts:
                yield w
            page_count = data.get("page_count", page)
            if page >= page_count:
                return
            page += 1


class MCPIntervalsAPI:
    def __init__(self, client=None):
        self.client = client or FitnessMCPClient()

    def get_activities(self, oldest, newest=None):
        return self.client.call_tool("intervals_list_activities", {
            "oldest": oldest,
            "newest": newest or date.today().isoformat(),
        })

    def update_activity(self, activity_id, payload):
        return self.client.call_tool("intervals_update_activity", {
            "activityId": activity_id,
            "body": payload,
        })

    def get_activity_messages(self, activity_id):
        return self.client.call_tool("intervals_list_activity_messages", {
            "activityId": activity_id,
        })

    def post_activity_message(self, activity_id, message):
        return self.client.call_tool("intervals_create_activity_message", {
            "activityId": activity_id,
            "body": {"content": message},
        })

    def get_streams(self, activity_id, types=None):
        arguments = {"activityId": activity_id}
        if types:
            arguments["types"] = ",".join(types)
        return self.client.call_tool("intervals_get_activity_streams", arguments)

    def upload_custom_stream(self, activity_id, stream_type, data):
        return self.client.call_tool("intervals_update_activity_streams", {
            "activityId": activity_id,
            "body": [{"type": stream_type, "data": data}],
        })

    def get_event(self, event_id):
        return self.client.call_tool("intervals_get_event", {
            "eventId": int(event_id),
        })

    def create_event(self, payload):
        return self.client.call_tool("intervals_create_event", payload)

    def update_event(self, event_id, payload):
        args = {"eventId": str(event_id), **payload}
        return self.client.call_tool("intervals_update_event", args)


class MCPStravaAPI:
    def __init__(self, client=None):
        self.client = client or FitnessMCPClient()

    def get_activity(self, activity_id, include_all_efforts=False):
        return self.client.call_tool("strava_get_activity", {
            "id": int(activity_id),
            "include_all_efforts": include_all_efforts,
        })

    def list_activities(self, after=None, before=None, per_page=200, limit=None):
        out = []
        page = 1
        while True:
            args = {"page": page, "per_page": per_page}
            if after is not None:
                args["after"] = int(after)
            if before is not None:
                args["before"] = int(before)
            chunk = self.client.call_tool("strava_list_activities", args)
            if not chunk:
                return out
            out.extend(chunk)
            if limit is not None and len(out) >= limit:
                return out[:limit]
            if len(chunk) < per_page:
                return out
            page += 1

    def update_activity(self, activity_id, **fields):
        payload = {"id": int(activity_id)}
        for k, v in fields.items():
            if v is not None:
                payload[k] = v
        return self.client.call_tool("strava_update_activity", payload)


class MCPNightscoutAPI:
    def __init__(self, client=None):
        self.client = client or FitnessMCPClient()

    def get_glucose(self, start_dt, duration_secs):
        end_dt = start_dt + timedelta(seconds=duration_secs)
        query = "&".join([
            f"find[date][$gte]={int(start_dt.timestamp() * 1000)}",
            f"find[date][$lte]={int(end_dt.timestamp() * 1000)}",
        ])
        entries = self.client.call_tool("nightscout_list_sgv", {
            "count": 1000,
            "query": query,
        })

        values = []
        seconds = []
        for entry in reversed(entries):
            timestamp = entry["date"] / 1000.0
            offset = int(timestamp - start_dt.timestamp())
            if 0 <= offset <= duration_secs:
                values.append(entry["sgv"])
                seconds.append(offset)

        if seconds and seconds[0] > 0:
            seconds.insert(0, 0)
            values.insert(0, values[0])

        return values, seconds
