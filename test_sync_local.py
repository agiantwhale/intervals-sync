import unittest
from unittest.mock import patch
import os

# Set dummy env vars before importing sync_hevy
os.environ["HEVY_API_KEY"] = "dummy_hevy"
os.environ["INTERVALS_ID"] = "dummy_id"
os.environ["INTERVALS_API_KEY"] = "dummy_intervals"

from sync_hevy import sync_hevy

class TestHevySync(unittest.TestCase):
    @patch('sync_hevy.HevyAPI')
    @patch('sync_hevy.IntervalsAPI')
    def test_sync_matching_logic(self, MockIntervals, MockHevy):
        # Setup Hevy Mock
        hevy_inst = MockHevy.return_value
        workout_start = "2024-05-04T10:00:00Z"
        workout_end = "2024-05-04T11:00:00Z" # 3600s
        hevy_inst.get_recent_workouts.return_value = [{
            "id": "workout_123",
            "start_time": workout_start,
            "end_time": workout_end,
            "title": "Leg Day",
            "exercises": [
                {
                    "title": "Squat",
                    "sets": [{"weight_kg": 100, "reps": 5, "set_type": "normal"}]
                }
            ]
        }]

        # Setup Intervals Mock
        intervals_inst = MockIntervals.return_value
        # Mock an activity that matches by time and duration
        intervals_inst.get_activities.return_value = [
            {
                "id": "intervals_999",
                "start_date": "2024-05-04T10:00:10Z", # 10s difference
                "elapsed_time": 3595,                 # 5s difference
                "name": "Morning Gym",
                "type": "WeightTraining",
                "external_id": None
            }
        ]

        # Run sync
        sync_hevy()

        # Verify update was called for the matched activity
        intervals_inst.update_activity.assert_called_once()
        args, kwargs = intervals_inst.update_activity.call_args
        activity_id, payload = args
        
        self.assertEqual(activity_id, "intervals_999")
        self.assertEqual(payload["name"], "Leg Day")
        self.assertEqual(payload["external_id"], "hevy-workout_123")
        self.assertEqual(payload["kg_lifted"], 500)
        self.assertIn("### Squat", payload["description"])
        self.assertIn("100kg × 5", payload["description"])
        print("\nTest passed: Successfully matched by time/duration and updated activity.")

    @patch('sync_hevy.HevyAPI')
    @patch('sync_hevy.IntervalsAPI')
    def test_sync_prefers_title_match_when_multiple_time_matches(self, MockIntervals, MockHevy):
        hevy_inst = MockHevy.return_value
        workout_start = "2024-05-04T10:00:00Z"
        workout_end = "2024-05-04T11:00:00Z"
        hevy_inst.get_recent_workouts.return_value = [{
            "id": "workout_456",
            "start_time": workout_start,
            "end_time": workout_end,
            "title": "Upper Body",
            "exercises": []
        }]

        intervals_inst = MockIntervals.return_value
        intervals_inst.get_activities.return_value = [
            {
                "id": "wrong_title",
                "start_date": "2024-05-04T10:00:15Z",
                "elapsed_time": 3605,
                "name": "Lower Body",
                "type": "WeightTraining",
                "external_id": None
            },
            {
                "id": "right_title",
                "start_date": "2024-05-04T10:00:20Z",
                "elapsed_time": 3590,
                "name": " upper   body ",
                "type": "WeightTraining",
                "external_id": None
            }
        ]

        sync_hevy()

        intervals_inst.update_activity.assert_called_once()
        activity_id, payload = intervals_inst.update_activity.call_args.args
        self.assertEqual(activity_id, "right_title")
        self.assertEqual(payload["name"], "Upper Body")
        self.assertEqual(payload["kg_lifted"], 0)

    @patch('sync_hevy.HevyAPI')
    @patch('sync_hevy.IntervalsAPI')
    def test_sync_ignores_non_strength_time_match(self, MockIntervals, MockHevy):
        hevy_inst = MockHevy.return_value
        workout_start = "2024-05-04T10:00:00Z"
        workout_end = "2024-05-04T11:00:00Z"
        hevy_inst.get_recent_workouts.return_value = [{
            "id": "workout_789",
            "start_time": workout_start,
            "end_time": workout_end,
            "title": "Leg Day",
            "exercises": []
        }]

        intervals_inst = MockIntervals.return_value
        intervals_inst.get_activities.return_value = [
            {
                "id": "run_123",
                "start_date": "2024-05-04T10:00:10Z",
                "elapsed_time": 3595,
                "name": "Morning Run",
                "type": "Run",
                "external_id": None
            }
        ]

        sync_hevy()

        intervals_inst.update_activity.assert_not_called()

if __name__ == "__main__":
    unittest.main()
