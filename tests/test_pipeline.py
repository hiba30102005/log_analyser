import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import main as pipeline_main
from ingestion.raw import iter_log_lines, parse_event
from serving.dashboard import create_dashboard, main as dashboard_main
import storage.database as database
from storage.database import fetch_dashboard_metrics, import_events, initialize_database


class PipelineTests(unittest.TestCase):
    def test_default_input_is_relative_to_script_directory(self):
        metrics = {"failed_by_country": [], "success_by_user": []}
        with (
            patch("main.configure_logging"),
            patch("main.load_events", return_value=[]) as load_events,
            patch("main.initialize_database"),
            patch("main.import_events"),
            patch("main.fetch_dashboard_metrics", return_value=(metrics, 0)),
            patch("main.create_dashboard"),
            patch("sys.argv", ["main.py"]),
        ):
            result = pipeline_main()

        self.assertEqual(result, 0)
        self.assertEqual(
            load_events.call_args.args[0],
            Path(__file__).resolve().parents[1] / "data",
        )


class ParseEventTests(unittest.TestCase):
    def test_parses_invalid_user_failure(self):
        event = parse_event(
            json.dumps(
                {
                    "timestamp": "2026-10-25T10:17:00Z",
                    "username": "unknown",
                    "country": "France",
                    "status": "FAILED",
                    "reason": "invalid_user",
                }
            )
        )

        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["occurred_at"], "2026-10-25T10:17:00+00:00")
        self.assertTrue(event["is_invalid_user"])

    def test_rejects_unknown_status(self):
        with self.assertRaisesRegex(ValueError, "status"):
            parse_event('{"timestamp":"2026-10-25T10:17:00Z","status":"blocked"}')

    def test_uses_unknown_country_when_missing(self):
        event = parse_event(
            '{"timestamp":"2026-10-25T10:17:00Z","status":"success"}'
        )
        self.assertEqual(event["country"], "Unknown")
        self.assertFalse(event["is_invalid_user"])

    def test_uses_unknown_country_when_null(self):
        event = parse_event(
            '{"timestamp":"2026-10-25T10:17:00Z","country":null,"status":"success"}'
        )
        self.assertEqual(event["country"], "Unknown")


class IngestionTests(unittest.TestCase):
    def test_reads_plain_and_gzip_logs(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "one.jsonl").write_text("first\n", encoding="utf-8")
            with gzip.open(directory / "two.log.gz", "wt", encoding="utf-8") as stream:
                stream.write("second\n")

            rows = list(iter_log_lines(directory))

        self.assertEqual([row[2] for row in rows], ["first", "second"])
        self.assertEqual([row[1] for row in rows], [1, 1])


class DatabaseTests(unittest.TestCase):
    def test_imports_events_and_updates_existing_source_lines(self):
        event = {
            "occurred_at": "2026-10-25T10:17:00+00:00",
            "username": "user,one",
            "country": "France",
            "status": "failed",
            "reason": "invalid_user",
            "is_invalid_user": True,
            "source_file": "auth.jsonl",
            "line_number": 2,
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "events.sqlite3"
            with patch.object(database, "DATABASE_PATH", database_path):
                initialize_database()
                self.assertEqual(import_events([event]), 1)
                self.assertEqual(import_events([dict(event, country="Belgium")]), 1)
                metrics, invalid_user_failures = fetch_dashboard_metrics()

        self.assertEqual(metrics["failed_by_country"], [("Belgium", 1)])
        self.assertEqual(metrics["success_by_user"], [])
        self.assertEqual(invalid_user_failures, 1)

    def test_main_pipeline_runs_with_local_database(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            input_path = directory / "auth.jsonl"
            input_path.write_text(
                '{"timestamp":"2026-10-25T10:17:00Z","username":"amina",'
                '"country":"France","status":"success"}\n',
                encoding="utf-8",
            )
            database_path = directory / "events.sqlite3"
            with (
                patch("main.configure_logging"),
                patch("main.create_dashboard") as show_dashboard,
                patch.object(database, "DATABASE_PATH", database_path),
                patch("sys.argv", ["main.py", "--input", str(input_path)]),
            ):
                result = pipeline_main()

        self.assertEqual(result, 0)
        show_dashboard.assert_called_once_with(
            {"failed_by_country": [], "success_by_user": [("amina", 1)]},
            0,
        )

    def test_reads_dashboard_metrics_from_sqlite(self):
        event = {
            "occurred_at": "2026-10-25T10:17:00+00:00",
            "username": "amina",
            "country": "France",
            "status": "success",
            "reason": None,
            "is_invalid_user": False,
            "source_file": "auth.jsonl",
            "line_number": 1,
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(
                database,
                "DATABASE_PATH",
                Path(temporary_directory) / "events.sqlite3",
            ):
                initialize_database()
                import_events([event])
                metrics, invalid_user_failures = fetch_dashboard_metrics()

        self.assertEqual(metrics["failed_by_country"], [])
        self.assertEqual(metrics["success_by_user"], [("amina", 1)])
        self.assertEqual(invalid_user_failures, 0)


class DashboardTests(unittest.TestCase):
    def test_displays_dashboard_without_saving_png(self):
        metrics = {
            "failed_by_country": [("France", 3)],
            "success_by_user": [("amina", 5)],
        }

        with patch("matplotlib.pyplot.show") as show:
            create_dashboard(metrics, 2)

        show.assert_called_once_with()

    def test_standalone_entry_point_loads_metrics_from_database(self):
        metrics = {
            "failed_by_country": [("France", 3)],
            "success_by_user": [("amina", 5)],
        }
        with (
            patch("storage.database.fetch_dashboard_metrics", return_value=(metrics, 2)),
            patch("serving.dashboard.create_dashboard") as create,
        ):
            result = dashboard_main()

        self.assertEqual(result, 0)
        create.assert_called_once_with(metrics, 2)


if __name__ == "__main__":
    unittest.main()
