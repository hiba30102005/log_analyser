import csv
import getpass
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


def _connection_settings() -> Tuple[str, str, str, str]:
    return (
        os.environ.get("PGHOST", "localhost"),
        os.environ.get("PGPORT", "5432"),
        os.environ.get("PGDATABASE", "log_analyser"),
        os.environ.get("PGUSER") or getpass.getuser(),
    )


def _pgpass_path() -> Path:
    configured_path = os.environ.get("PGPASSFILE")
    if configured_path:
        return Path(configured_path)
    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        if app_data:
            return Path(app_data) / "postgresql" / "pgpass.conf"
    return Path.home() / ".pgpass"


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
            "Install PostgreSQL client tools, add psql to PATH, or set PSQL_PATH."
        )

    host, port, database, user = _connection_settings()
    command = [
        executable,
        "-X",
        "-w",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        host,
        "-p",
        port,
        "-U",
        user,
        "-d",
        database,
    ]
    if csv_output:
        command.append("--csv")
    return command


def _run_psql(
    arguments: List[str],
    input_text: Optional[str] = None,
    csv_output: bool = False,
) -> str:
    command = _psql_command(csv_output)
    command.extend(arguments)
    command_environment = os.environ.copy()
    command_environment["PGCLIENTENCODING"] = "UTF8"
    command_environment["PGPASSFILE"] = str(_pgpass_path())
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
            timeout=60,
        )
    except subprocess.TimeoutExpired as error:
        logger.exception("PostgreSQL command timed out")
        raise RuntimeError("PostgreSQL command timed out after 60 seconds") from error
    except OSError as error:
        logger.exception("Could not start the PostgreSQL client")
        raise RuntimeError("Could not start the PostgreSQL client") from error
    if result.returncode == 0:
        return result.stdout

    error_details = (result.stderr or "").strip()
    lowered_error = error_details.lower()
    authentication_error = (
        "password" in lowered_error
        or "mot de passe" in lowered_error
        or "authentication" in lowered_error
        or "authentification" in lowered_error
    )
    logger.error("PostgreSQL command failed: %s", error_details)
    if authentication_error:
        host, port, database, user = _connection_settings()
        raise RuntimeError(
            "PostgreSQL authentication failed without prompting. Configure "
            f"access for {user}@{host}:{port}/{database} in pgpass.conf or "
            "use a PostgreSQL authentication method that does not require a "
            "password prompt."
        )
    raise RuntimeError(f"PostgreSQL command failed: {error_details}")


def initialize_database() -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    try:
        _run_psql(["--file", str(schema_path)])
    except (OSError, RuntimeError):
        logger.exception("Could not initialize the PostgreSQL schema")
        raise
    logger.info("PostgreSQL schema is ready")


def import_events(
    events: Iterable[Event],
    replace_existing: bool = False,
) -> int:
    rows = list(events)
    if not rows and not replace_existing:
        logger.info("No valid events to import")
        return 0

    if not rows:
        sql_script = "BEGIN;\nDELETE FROM auth_events;\nCOMMIT;\n"
    else:
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
            + ("DELETE FROM auth_events;\n" if replace_existing else "")
            + "INSERT INTO auth_events "
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

    try:
        _run_psql([], input_text=sql_script)
    except (OSError, RuntimeError):
        logger.exception("Could not import events into PostgreSQL")
        raise
    logger.info("Imported %d valid event(s) into PostgreSQL", len(rows))
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
        WHERE status = 'success'
          AND username IS NOT NULL
          AND username <> ''
        GROUP BY username
        UNION ALL
        SELECT 'invalid_user_failures', 'Invalid user', COUNT(*)
        FROM auth_events
        WHERE status = 'failed'
          AND is_invalid_user = TRUE
        UNION ALL
        SELECT 'invalid_user_users', username, COUNT(*)
        FROM auth_events
        WHERE status = 'failed'
          AND is_invalid_user = TRUE
          AND username IS NOT NULL
          AND username <> ''
        GROUP BY username
        ORDER BY metric, label
    """
    try:
        output = _run_psql(["--command", query], csv_output=True)
    except (OSError, RuntimeError):
        logger.exception("Could not fetch dashboard metrics from PostgreSQL")
        raise

    parsed_rows = list(csv.reader(io.StringIO(output)))
    if not parsed_rows or parsed_rows[0] != ["metric", "label", "total"]:
        logger.error("Unexpected CSV response from PostgreSQL dashboard query")
        raise RuntimeError("Unexpected CSV response from PostgreSQL")

    metrics: MetricRows = {
        "failed_by_country": [],
        "success_by_user": [],
        "invalid_user_users": [],
    }
    invalid_user_failures = 0
    try:
        for row in parsed_rows[1:]:
            if len(row) != 3:
                raise ValueError("Malformed dashboard row")
            metric, label, total = row
            count = int(total)
            if metric in metrics:
                metrics[metric].append((label, count))
            elif metric == "invalid_user_failures":
                invalid_user_failures = count
            else:
                raise ValueError(f"Unknown dashboard metric: {metric}")
    except ValueError as error:
        logger.exception("Invalid dashboard data returned by PostgreSQL")
        raise RuntimeError("Invalid dashboard data returned by PostgreSQL") from error

    logger.info("Dashboard metrics loaded from PostgreSQL")
    return metrics, invalid_user_failures
