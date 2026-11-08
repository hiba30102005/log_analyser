import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ingestion.raw import iter_log_lines, parse_event
from serving.dashboard import create_dashboard, main as dashboard_main
from storage.database import _psql_command, _run_psql, fetch_dashboard_metrics, import_events


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


class DatabaseAdapterTests(unittest.TestCase):
    def test_uses_explicit_psql_path(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            psql_path = Path(temporary_directory) / "psql.exe"
            psql_path.touch()
            with (
                patch.dict("os.environ", {"PSQL_PATH": str(psql_path)}),
                patch("storage.database.shutil.which", return_value=None),
            ):
                command = _psql_command()

        self.assertEqual(command[0], str(psql_path))

    def test_discovers_psql_in_standard_windows_installation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            postgres_directory = Path(temporary_directory) / "PostgreSQL"
            older_psql = postgres_directory / "16" / "bin" / "psql.exe"
            installed_psql = postgres_directory / "18" / "bin" / "psql.exe"
            older_psql.parent.mkdir(parents=True)
            installed_psql.parent.mkdir(parents=True)
            older_psql.touch()
            installed_psql.touch()
            with (
                patch("storage.database.os.name", "nt"),
                patch.dict(
                    "os.environ",
                    {"ProgramFiles": temporary_directory},
                    clear=False,
                ),
                patch.dict("os.environ", {"PSQL_PATH": ""}),
                patch("storage.database.shutil.which", return_value=None),
            ):
                command = _psql_command()

        self.assertEqual(command[0], str(installed_psql))

    def test_handles_non_utf8_psql_error_output(self):
        command = [
            sys.executable,
            "-c",
            "import sys; sys.stderr.buffer.write(bytes([0xe9])); sys.exit(1)",
        ]
        with patch("storage.database._psql_command", return_value=command):
            with self.assertRaisesRegex(RuntimeError, "PostgreSQL command failed"):
                _run_psql([])

    def test_sets_postgresql_client_encoding_to_utf8(self):
        command = [sys.executable]
        result = subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        with (
            patch("storage.database._psql_command", return_value=command),
            patch("storage.database.subprocess.run", return_value=result) as run,
        ):
            _run_psql([])

        self.assertEqual(run.call_args.kwargs["env"]["PGCLIENTENCODING"], "UTF8")

    def test_import_sends_csv_and_idempotent_upsert(self):
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

        with patch("storage.database._run_psql") as run_psql:
            imported = import_events([event])

        self.assertEqual(imported, 1)
        script = run_psql.call_args.kwargs["input_text"]
        self.assertIn("\\copy auth_events_stage", script)
        self.assertIn('"user,one"', script)
        self.assertIn("ON CONFLICT (source_file, line_number) DO UPDATE", script)

    def test_reads_dashboard_metrics_from_csv(self):
        response = (
            'metric,label,total\n'
            'failed_by_country,France,3\n'
            'invalid_user_failures,Invalid user,2\n'
            'success_by_user,"user,one",5\n'
        )
        with patch("storage.database._run_psql", return_value=response):
            metrics, invalid_user_failures = fetch_dashboard_metrics()

        self.assertEqual(metrics["failed_by_country"], [("France", 3)])
        self.assertEqual(metrics["success_by_user"], [("user,one", 5)])
        self.assertEqual(invalid_user_failures, 2)


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
