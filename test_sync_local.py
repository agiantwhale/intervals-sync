import sys
import unittest
from unittest.mock import patch
from datetime import datetime
import os

# Set dummy env vars before importing sync_hevy
os.environ["FITNESS_MCP_URL"] = "https://fitness-mcp.example.workers.dev"
os.environ["FITNESS_MCP_TOKEN"] = "dummy_mcp"

from sync_hevy import sync_hevy
from src.api.fitness_mcp import MCPHevyAPI, MCPIntervalsAPI, MCPNightscoutAPI

class TestHevySync(unittest.TestCase):
    def setUp(self):
        # sync_hevy() calls parse_args(sys.argv[1:]) on entry; unittest's own
        # argv would otherwise trigger SystemExit on unknown flags.
        argv_patch = patch.object(sys, "argv", ["sync_hevy"])
        argv_patch.start()
        self.addCleanup(argv_patch.stop)

    @patch('sync_hevy.MCPHevyAPI')
    @patch('sync_hevy.MCPIntervalsAPI')
    def test_sync_creates_event_with_external_id_and_pairs_activity(self, MockIntervals, MockHevy):
        hevy_inst = MockHevy.return_value
        workout_start = "2024-05-04T10:00:00Z"
        workout_end = "2024-05-04T11:00:00Z"  # 3600s
        hevy_inst.get_recent_workouts.return_value = [{
            "id": "workout_123",
            "start_time": workout_start,
            "end_time": workout_end,
            "title": "Leg Day",
            "exercises": [
                {
                    "title": "Squat",
                    "sets": [{"weight_kg": 100, "reps": 5, "set_type": "normal"}],
                }
            ],
        }]

        intervals_inst = MockIntervals.return_value
        intervals_inst.get_activities.return_value = [{
            "id": "intervals_999",
            "start_date": "2024-05-04T10:00:10Z",  # 10s diff
            "elapsed_time": 3595,                  # 5s diff
            "name": "Morning Gym",
            "type": "WeightTraining",
            "external_id": None,
            "paired_event_id": None,
            "start_date_local": "2024-05-04T10:00:00",
        }]
        intervals_inst.create_event.return_value = {"id": 5000}

        sync_hevy()

        # Event holds the workout content + hidden external_id; no marker in description.
        intervals_inst.create_event.assert_called_once()
        event_payload = intervals_inst.create_event.call_args.args[0]
        self.assertEqual(event_payload["name"], "Leg Day")
        self.assertEqual(event_payload["external_id"], "hevy-workout_123")
        self.assertIn("### Squat", event_payload["description"])
        self.assertIn("100kg x 5", event_payload["description"])
        self.assertNotIn("[hevy-event:", event_payload["description"])

        # Activity is paired to the new event, then its body fields are set
        # (name/external_id/kg_lifted) with description cleared.
        self.assertEqual(intervals_inst.update_activity.call_count, 2)
        pair_call, body_call = intervals_inst.update_activity.call_args_list
        self.assertEqual(pair_call.args, ("intervals_999", {"paired_event_id": 5000}))
        activity_id, body_payload = body_call.args
        self.assertEqual(activity_id, "intervals_999")
        self.assertEqual(body_payload["name"], "Leg Day")
        self.assertEqual(body_payload["external_id"], "hevy-workout_123")
        self.assertEqual(body_payload["kg_lifted"], 500)
        self.assertEqual(body_payload["description"], "")

    @patch('sync_hevy.MCPHevyAPI')
    @patch('sync_hevy.MCPIntervalsAPI')
    def test_sync_prefers_title_match_when_multiple_time_matches(self, MockIntervals, MockHevy):
        hevy_inst = MockHevy.return_value
        workout_start = "2024-05-04T10:00:00Z"
        workout_end = "2024-05-04T11:00:00Z"
        hevy_inst.get_recent_workouts.return_value = [{
            "id": "workout_456",
            "start_time": workout_start,
            "end_time": workout_end,
            "title": "Upper Body",
            "exercises": [],
        }]

        intervals_inst = MockIntervals.return_value
        intervals_inst.get_activities.return_value = [
            {
                "id": "wrong_title",
                "start_date": "2024-05-04T10:00:15Z",
                "elapsed_time": 3605,
                "name": "Lower Body",
                "type": "WeightTraining",
                "external_id": None,
                "paired_event_id": None,
                "start_date_local": "2024-05-04T10:00:00",
            },
            {
                "id": "right_title",
                "start_date": "2024-05-04T10:00:20Z",
                "elapsed_time": 3590,
                "name": " upper   body ",
                "type": "WeightTraining",
                "external_id": None,
                "paired_event_id": None,
                "start_date_local": "2024-05-04T10:00:00",
            },
        ]
        intervals_inst.create_event.return_value = {"id": 6000}

        sync_hevy()

        # Title match wins → "right_title" gets paired and body-updated.
        self.assertEqual(intervals_inst.update_activity.call_count, 2)
        for call in intervals_inst.update_activity.call_args_list:
            self.assertEqual(call.args[0], "right_title")
        body_payload = intervals_inst.update_activity.call_args_list[-1].args[1]
        self.assertEqual(body_payload["name"], "Upper Body")
        self.assertNotIn("kg_lifted", body_payload)

    @patch('sync_hevy.MCPHevyAPI')
    @patch('sync_hevy.MCPIntervalsAPI')
    def test_sync_ignores_non_strength_time_match(self, MockIntervals, MockHevy):
        hevy_inst = MockHevy.return_value
        workout_start = "2024-05-04T10:00:00Z"
        workout_end = "2024-05-04T11:00:00Z"
        hevy_inst.get_recent_workouts.return_value = [{
            "id": "workout_789",
            "start_time": workout_start,
            "end_time": workout_end,
            "title": "Leg Day",
            "exercises": [],
        }]

        intervals_inst = MockIntervals.return_value
        intervals_inst.get_activities.return_value = [
            {
                "id": "run_123",
                "start_date": "2024-05-04T10:00:10Z",
                "elapsed_time": 3595,
                "name": "Morning Run",
                "type": "Run",
                "external_id": None,
            }
        ]

        sync_hevy()

        intervals_inst.update_activity.assert_not_called()
        intervals_inst.create_event.assert_not_called()

    @patch('sync_hevy.MCPHevyAPI')
    @patch('sync_hevy.MCPIntervalsAPI')
    def test_sync_migrates_legacy_marker_event_to_external_id(self, MockIntervals, MockHevy):
        hevy_inst = MockHevy.return_value
        hevy_inst.get_recent_workouts.return_value = [{
            "id": "workout_legacy",
            "start_time": "2024-05-04T10:00:00Z",
            "end_time": "2024-05-04T11:00:00Z",
            "title": "Leg Day",
            "exercises": [],
        }]

        intervals_inst = MockIntervals.return_value
        intervals_inst.get_activities.return_value = [{
            "id": "intervals_legacy",
            "start_date": "2024-05-04T10:00:00Z",
            "elapsed_time": 3600,
            "name": "Leg Day",
            "type": "WeightTraining",
            "external_id": "hevy-workout_legacy",
            "kg_lifted": None,
            "paired_event_id": 7777,
            "start_date_local": "2024-05-04T10:00:00",
        }]
        # Existing event has the old marker stamped into description, no external_id yet.
        intervals_inst.get_event.return_value = {
            "id": 7777,
            "name": "Leg Day",
            "description": "### Squat\n  - 100kg x 5\n\n[hevy-event:workout_legacy]",
            "external_id": None,
            "start_date_local": "2024-05-04T10:00:00",
        }

        sync_hevy()

        # Recognizes the legacy event via the marker fallback and re-PUTs to set
        # external_id + clean description.
        intervals_inst.update_event.assert_called_once()
        event_id, event_payload = intervals_inst.update_event.call_args.args
        self.assertEqual(event_id, 7777)
        self.assertEqual(event_payload["external_id"], "hevy-workout_legacy")
        self.assertNotIn("[hevy-event:", event_payload["description"])
        intervals_inst.create_event.assert_not_called()


class FakeMCPClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments or {}))
        return self.responses[name]


class TestFitnessMCPClients(unittest.TestCase):
    def test_hevy_workout_page_uses_expected_tool(self):
        client = FakeMCPClient({
            "hevy_list_workouts": {"workouts": [], "page": 2, "page_count": 2},
        })
        hevy = MCPHevyAPI(client=client)

        self.assertEqual(hevy.get_workouts_page(page=2, page_size=5), {
            "workouts": [],
            "page": 2,
            "page_count": 2,
        })
        self.assertEqual(client.calls[0], (
            "hevy_list_workouts",
            {"page": 2, "pageSize": 5},
        ))

    def test_intervals_activity_methods_use_expected_tools(self):
        client = FakeMCPClient({
            "intervals_list_activities": [{"id": "a1"}],
            "intervals_update_activity": {"id": "a1"},
            "intervals_list_activity_messages": [],
            "intervals_create_activity_message": {"id": 1},
            "intervals_get_activity_streams": [{"type": "time", "data": [0]}],
            "intervals_update_activity_streams": {"ok": True},
        })
        intervals = MCPIntervalsAPI(client=client)

        self.assertEqual(intervals.get_activities("2026-05-01", "2026-05-07"), [{"id": "a1"}])
        intervals.update_activity("a1", {"name": "Lower"})
        intervals.get_activity_messages("a1")
        intervals.post_activity_message("a1", "details")
        intervals.get_streams("a1", types=["time"])
        intervals.upload_custom_stream("a1", "bloodglucose", [100])

        self.assertEqual(client.calls[0], (
            "intervals_list_activities",
            {"oldest": "2026-05-01", "newest": "2026-05-07"},
        ))
        self.assertEqual(client.calls[1], (
            "intervals_update_activity",
            {"activityId": "a1", "body": {"name": "Lower"}},
        ))
        self.assertEqual(client.calls[3], (
            "intervals_create_activity_message",
            {"activityId": "a1", "body": {"content": "details"}},
        ))
        self.assertEqual(client.calls[5], (
            "intervals_update_activity_streams",
            {"activityId": "a1", "body": [{"type": "bloodglucose", "data": [100]}]},
        ))

    def test_nightscout_glucose_uses_sgv_tool(self):
        start = datetime.fromisoformat("2026-05-07T12:00:00+00:00")
        client = FakeMCPClient({
            "nightscout_list_sgv": [
                {"date": int((start.timestamp() + 300) * 1000), "sgv": 120},
                {"date": int(start.timestamp() * 1000), "sgv": 100},
            ],
        })
        nightscout = MCPNightscoutAPI(client=client)

        values, seconds = nightscout.get_glucose(start, 600)

        self.assertEqual(values, [100, 120])
        self.assertEqual(seconds, [0, 300])
        self.assertEqual(client.calls[0][0], "nightscout_list_sgv")
        self.assertEqual(client.calls[0][1]["count"], 1000)

if __name__ == "__main__":
    unittest.main()
