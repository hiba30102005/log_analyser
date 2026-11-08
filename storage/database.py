import csv
import io
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

Event = Dict[str, object]
MetricRows = Dict[str, List[Tuple[str, int]]]


def _psql_command(csv_output: bool = False) -> List[str]:
    configured_executable = os.environ.get("PSQL_PATH")
    if configured_executable:
        if not Path(configured_executable).is_file():
            raise RuntimeError(
                "PSQL_PATH does not point to a psql executable file: "
                f"{configured_executable}"
            )
        executable = configured_executable
    else:
        executable = shutil.which("psql")
        if executable is None and os.name == "nt":
            postgres_directory = Path(
                os.environ.get("ProgramFiles", r"C:\Program Files")
            ) / "PostgreSQL"
            if postgres_directory.is_dir():
                versions = sorted(
                    (
                        directory
                        for directory in postgres_directory.iterdir()
                        if directory.is_dir()
                        and all(part.isdigit() for part in directory.name.split("."))
                    ),
                    key=lambda directory: tuple(
                        int(part) for part in directory.name.split(".")
                    ),
                    reverse=True,
                )
                for version in versions:
                    candidate = version / "bin" / "psql.exe"
                    if candidate.is_file():
                        executable = str(candidate)
                        break
    if executable is None:
        raise RuntimeError(
            "PostgreSQL command-line client 'psql' was not found. "
            "Add it to PATH or set PSQL_PATH to the full path of psql.exe."
        )

    command = [executable, "-X", "-v", "ON_ERROR_STOP=1"]
    connection_options = (
        ("PGHOST", "-h"),
        ("PGPORT", "-p"),
        ("PGUSER", "-U"),
        ("PGDATABASE", "-d"),
    )
    for environment_name, option in connection_options:
        value = os.environ.get(environment_name)
        if value:
            command.extend((option, value))
    if not os.environ.get("PGDATABASE"):
        command.extend(("-d", "log_analyser"))
    if csv_output:
        command.append("--csv")
    return command


def _run_psql(
    arguments: List[str], input_text: Optional[str] = None, csv_output: bool = False
) -> str:
    command = _psql_command(csv_output)
    command.extend(arguments)
    command_environment = os.environ.copy()
    command_environment["PGCLIENTENCODING"] = "UTF8"
    try:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=command_environment,
            check=False,
        )
    except OSError:
        logger.exception("Could not start the PostgreSQL client")
        raise
    if result.returncode != 0:
        logger.error("PostgreSQL command failed: %s", (result.stderr or "").strip())
        raise RuntimeError("PostgreSQL command failed; see logs/app.log for details")
    return result.stdout


def initialize_database() -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    _run_psql(["-f", str(schema_path)])
    logger.info("PostgreSQL schema is ready")


def import_events(events: Iterable[Event]) -> int:
    rows = list(events)
    if not rows:
        logger.info("No valid events to import")
        return 0

    csv_stream = io.StringIO(newline="")
    writer = csv.writer(csv_stream, lineterminator="\n")
    columns = (
        "occurred_at",
        "username",
        "country",
        "status",
        "reason",
        "is_invalid_user",
        "source_file",
        "line_number",
    )
    writer.writerow(columns)
    for event in rows:
        writer.writerow(
            [
                event["occurred_at"],
                event["username"] or "",
                event["country"],
                event["status"],
                event["reason"] or "",
                "true" if event["is_invalid_user"] else "false",
                event["source_file"],
                event["line_number"],
            ]
        )

    sql_script = (
        "BEGIN;\n"
        "CREATE TEMP TABLE auth_events_stage (\n"
        "    occurred_at TIMESTAMPTZ NOT NULL,\n"
        "    username TEXT,\n"
        "    country TEXT NOT NULL,\n"
        "    status TEXT NOT NULL,\n"
        "    reason TEXT,\n"
        "    is_invalid_user BOOLEAN NOT NULL,\n"
        "    source_file TEXT NOT NULL,\n"
        "    line_number INTEGER NOT NULL\n"
        ");\n"
        "\\copy auth_events_stage ({}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE)\n"
    ).format(", ".join(columns))
    sql_script += csv_stream.getvalue()
    sql_script += (
        "\\.\n"
        "INSERT INTO auth_events "
        "(occurred_at, username, country, status, reason, is_invalid_user, "
        "source_file, line_number)\n"
        "SELECT occurred_at, NULLIF(username, ''), country, status, "
        "NULLIF(reason, ''), is_invalid_user, source_file, line_number\n"
        "FROM auth_events_stage\n"
        "ON CONFLICT (source_file, line_number) DO UPDATE SET\n"
        "    occurred_at = EXCLUDED.occurred_at,\n"
        "    username = EXCLUDED.username,\n"
        "    country = EXCLUDED.country,\n"
        "    status = EXCLUDED.status,\n"
        "    reason = EXCLUDED.reason,\n"
        "    is_invalid_user = EXCLUDED.is_invalid_user;\n"
        "COMMIT;\n"
    )
    _run_psql([], input_text=sql_script)
    logger.info("Imported %d valid event(s)", len(rows))
    return len(rows)


def fetch_dashboard_metrics() -> Tuple[MetricRows, int]:
    query = """
        SELECT 'failed_by_country' AS metric, country AS label, COUNT(*) AS total
        FROM auth_events
        WHERE status = 'failed'
        GROUP BY country
        UNION ALL
        SELECT 'success_by_user', username, COUNT(*)
        FROM auth_events
        WHERE status = 'success' AND username IS NOT NULL AND username <> ''
        GROUP BY username
        UNION ALL
        SELECT 'invalid_user_failures', 'Invalid user', COUNT(*)
        FROM auth_events
        WHERE status = 'failed' AND is_invalid_user = TRUE
        ORDER BY metric, label
    """
    output = _run_psql(["--command", query], csv_output=True)
    parsed_rows = list(csv.reader(io.StringIO(output)))
    if not parsed_rows:
        raise RuntimeError("PostgreSQL returned no dashboard data")
    if parsed_rows[0] != ["metric", "label", "total"]:
        raise RuntimeError("Unexpected CSV response from PostgreSQL")

    metrics: MetricRows = {"failed_by_country": [], "success_by_user": []}
    invalid_user_failures = 0
    for row in parsed_rows[1:]:
        if len(row) != 3:
            raise RuntimeError("Malformed dashboard row returned by PostgreSQL")
        metric, label, total = row
        if metric == "failed_by_country":
            metrics[metric].append((label, int(total)))
        elif metric == "success_by_user":
            metrics[metric].append((label, int(total)))
        elif metric == "invalid_user_failures":
            invalid_user_failures = int(total)
        else:
            raise RuntimeError("Unknown dashboard metric returned by PostgreSQL")
    logger.info("Dashboard metrics loaded from PostgreSQL")
    return metrics, invalid_user_failures
