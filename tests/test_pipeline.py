import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import main as pipeline_main
from ingestion.raw import iter_log_lines, load_log_records, parse_event, parse_log_record
from serving.dashboard import create_dashboard, main as dashboard_main
from storage.database import (
    _psql_command,
    _run_psql,
    fetch_dashboard_metrics,
    import_events,
    initialize_database,
)


class PipelineTests(unittest.TestCase):
    def test_default_input_is_relative_to_script_directory(self):
        records = [{"message": "sample", "status": "other"}]
        with (
            patch("main.configure_logging"),
            patch("main.load_log_records", return_value=records) as load_records,
            patch("main.create_dashboard") as show_dashboard,
            patch("sys.argv", ["main.py"]),
        ):
            result = pipeline_main()

        self.assertEqual(result, 0)
        load_records.assert_called_once_with(Path(__file__).resolve().parents[1] / "data")
        show_dashboard.assert_called_once_with(records)

    def test_uses_selected_input_for_dashboard_logs(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "logs"
            records = [{"message": "log", "status": "other"}]
            with (
                patch("main.configure_logging"),
                patch("main.load_log_records", return_value=records) as load_records,
                patch("main.create_dashboard") as show_dashboard,
                patch(
                    "sys.argv",
                    ["main.py", "--input", str(input_path)],
                ),
            ):
                result = pipeline_main()

        self.assertEqual(result, 0)
        load_records.assert_called_once_with(input_path)
        show_dashboard.assert_called_once_with(records)


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

    def test_uses_unknown_country_when_missing_or_null(self):
        missing = parse_event(
            '{"timestamp":"2026-10-25T10:17:00Z","status":"success"}'
        )
        null = parse_event(
            '{"timestamp":"2026-10-25T10:17:00Z","country":null,"status":"success"}'
        )
        self.assertEqual(missing["country"], "Unknown")
        self.assertEqual(null["country"], "Unknown")


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

    def test_loads_every_line_including_gzip_and_normalizes_ssh_events(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "auth.log.1").write_text(
                "May 24 00:28:00 ns sshd[1]: Failed password for invalid user test "
                "from 45.156.87.204 port 41740 ssh2\n"
                "May 24 00:30:00 ns sshd[2]: Accepted password for admin "
                "from 10.0.0.1 port 22 ssh2\n"
                "May 24 00:31:00 ns CRON[3]: task completed\n",
                encoding="utf-8",
            )
            with gzip.open(
                directory / "auth.log.2.gz", "wt", encoding="utf-8"
            ) as stream:
                stream.write(
                    "May 23 00:20:00 ns sshd[4]: Failed password for root "
                    "from 192.0.2.1 port 22 ssh2\n"
                )

            records = load_log_records(directory)

        self.assertEqual(len(records), 4)
        self.assertEqual(records[0]["status"], "failed")
        self.assertTrue(records[0]["is_invalid_user"])
        self.assertEqual(records[0]["username"], "test")
        self.assertEqual(records[0]["source"], "45.156.87.204")
        self.assertEqual(records[1]["status"], "success")
        self.assertEqual(records[2]["category"], "Autre log")
        self.assertEqual(records[3]["source_file"], "auth.log.2.gz")

    def test_normalizes_structured_json_and_failed_attempts(self):
        record = parse_log_record(
            '{"timestamp":"2026-10-25T10:17:00Z","username":"unknown",'
            '"country":"France","status":"failed","reason":"invalid_user"}'
        )

        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["country"], "France")
        self.assertTrue(record["is_invalid_user"])


class DatabaseAdapterTests(unittest.TestCase):
    def test_builds_psql_command_from_environment(self):
        with patch.dict(
            "os.environ",
            {
                "PSQL_PATH": sys.executable,
                "PGHOST": "localhost",
                "PGPORT": "5432",
                "PGUSER": "log_user",
                "PGDATABASE": "log_analyser",
            },
            clear=False,
        ):
            command = _psql_command(csv_output=True)

        self.assertEqual(command[0], sys.executable)
        self.assertIn("--csv", command)
        self.assertIn("-w", command)
        self.assertEqual(
            command[command.index("-h") + 1 : command.index("-h") + 2],
            ["localhost"],
        )
        self.assertEqual(command[command.index("-U") + 1], "log_user")
        self.assertEqual(command[command.index("-d") + 1], "log_analyser")

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

    def test_sets_client_encoding_and_timeout(self):
        result = subprocess.CompletedProcess(
            [sys.executable], 0, stdout="", stderr=""
        )
        with (
            patch("storage.database._psql_command", return_value=[sys.executable]),
            patch("storage.database.subprocess.run", return_value=result) as run,
        ):
            _run_psql([])

        self.assertEqual(run.call_args.kwargs["env"]["PGCLIENTENCODING"], "UTF8")
        self.assertEqual(run.call_args.kwargs["timeout"], 60)

    def test_authentication_failure_never_prompts_or_retries(self):
        auth_failure = subprocess.CompletedProcess(
            [sys.executable], 2, stdout="", stderr="fe_sendauth: no password supplied"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            passfile = Path(temporary_directory) / "pgpass.conf"
            with (
                patch.dict(
                    "os.environ",
                    {
                        "PGPASSFILE": str(passfile),
                        "PGHOST": "localhost",
                        "PGPORT": "5432",
                        "PGUSER": "log_user",
                        "PGDATABASE": "log_analyser",
                    },
                ),
                patch("storage.database._psql_command", return_value=[sys.executable]),
                patch("storage.database.subprocess.run", return_value=auth_failure) as run,
                patch("storage.database.getpass.getpass") as prompt,
                self.assertLogs("storage.database", level="ERROR"),
                self.assertRaisesRegex(
                    RuntimeError, "authentication failed without prompting"
                ),
            ):
                _run_psql([])

        self.assertEqual(run.call_count, 1)
        prompt.assert_not_called()
        self.assertFalse(passfile.exists())

    def test_import_copies_events_and_replaces_stale_rows(self):
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
            imported = import_events([event], replace_existing=True)

        self.assertEqual(imported, 1)
        script = run_psql.call_args.kwargs["input_text"]
        self.assertIn("\\copy auth_events_stage", script)
        self.assertIn('"user,one"', script)
        self.assertIn("DELETE FROM auth_events;", script)
        self.assertIn("ON CONFLICT (source_file, line_number) DO UPDATE", script)

    def test_empty_replace_clears_postgresql_table(self):
        with patch("storage.database._run_psql") as run_psql:
            imported = import_events([], replace_existing=True)

        self.assertEqual(imported, 0)
        self.assertIn("DELETE FROM auth_events;", run_psql.call_args.kwargs["input_text"])

    def test_fetches_all_three_dashboard_metrics_from_postgresql(self):
        response = (
            "metric,label,total\n"
            "failed_by_country,France,3\n"
            "invalid_user_failures,Invalid user,2\n"
            "invalid_user_users,unknown,2\n"
            "success_by_user,amina,5\n"
        )
        with patch("storage.database._run_psql", return_value=response) as run_psql:
            metrics, invalid_user_failures = fetch_dashboard_metrics()

        self.assertEqual(metrics["failed_by_country"], [("France", 3)])
        self.assertEqual(metrics["success_by_user"], [("amina", 5)])
        self.assertEqual(metrics["invalid_user_users"], [("unknown", 2)])
        self.assertEqual(invalid_user_failures, 2)
        self.assertTrue(run_psql.call_args.kwargs["csv_output"])
        self.assertIn("FROM auth_events", run_psql.call_args.args[0][1])

    def test_rejects_malformed_postgresql_csv(self):
        with (
            patch("storage.database._run_psql", return_value="unexpected\n"),
            self.assertLogs("storage.database", level="ERROR"),
            self.assertRaisesRegex(RuntimeError, "Unexpected CSV"),
        ):
            fetch_dashboard_metrics()


class DashboardTests(unittest.TestCase):
    def test_displays_native_dashboard_for_all_supplied_log_records(self):
        records = [
            {
                "message": f"log line {index}",
                "source_file": "auth.log",
                "line_number": index + 1,
                "category": "Échec SSH",
                "status": "failed",
                "username": "root",
                "source": "192.0.2.1",
                "is_invalid_user": False,
            }
            for index in range(25)
        ]

        with (
            patch("matplotlib.pyplot.show") as show,
            patch("matplotlib.figure.Figure.savefig") as savefig,
        ):
            create_dashboard(records)

        show.assert_called_once_with()
        savefig.assert_not_called()

    def test_standalone_entry_point_loads_local_data_directory(self):
        records = [{"message": "log", "status": "other"}]
        with (
            patch("ingestion.raw.load_log_records", return_value=records) as load_logs,
            patch("serving.dashboard.create_dashboard") as create,
        ):
            result = dashboard_main()

        self.assertEqual(result, 0)
        load_logs.assert_called_once_with(Path(__file__).resolve().parents[1] / "data")
        create.assert_called_once_with(records)


if __name__ == "__main__":
    unittest.main()
