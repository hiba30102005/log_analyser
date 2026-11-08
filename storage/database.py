import logging
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

Event = Dict[str, object]
MetricRows = Dict[str, List[Tuple[str, int]]]
DATABASE_PATH = Path(__file__).with_name("auth_events.sqlite3")


def initialize_database() -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    connection = None
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(DATABASE_PATH)
        connection.executescript(schema_path.read_text(encoding="utf-8"))
    except (OSError, sqlite3.Error):
        logger.exception("Could not initialize the SQLite database")
        raise RuntimeError("Could not initialize the SQLite database") from None
    finally:
        if connection is not None:
            connection.close()
    logger.info("SQLite database is ready at %s", DATABASE_PATH)


def import_events(events: Iterable[Event]) -> int:
    rows = list(events)
    if not rows:
        logger.info("No valid events to import")
        return 0

    values = [
        (
            event["occurred_at"],
            event["username"] or None,
            event["country"],
            event["status"],
            event["reason"] or None,
            int(bool(event["is_invalid_user"])),
            event["source_file"],
            event["line_number"],
        )
        for event in rows
    ]

    connection = None
    try:
        connection = sqlite3.connect(DATABASE_PATH)
        with connection:
            connection.executemany(
                """
                INSERT INTO auth_events (
                    occurred_at, username, country, status, reason,
                    is_invalid_user, source_file, line_number
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_file, line_number) DO UPDATE SET
                    occurred_at = excluded.occurred_at,
                    username = excluded.username,
                    country = excluded.country,
                    status = excluded.status,
                    reason = excluded.reason,
                    is_invalid_user = excluded.is_invalid_user
                """,
                values,
            )
    except (OSError, sqlite3.Error):
        logger.exception("Could not import events into the SQLite database")
        raise RuntimeError("Could not import events into the SQLite database") from None
    finally:
        if connection is not None:
            connection.close()

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
        WHERE status = 'failed' AND is_invalid_user = 1
        ORDER BY metric, label
    """
    connection = None
    try:
        connection = sqlite3.connect(DATABASE_PATH)
        rows = connection.execute(query).fetchall()
    except (OSError, sqlite3.Error):
        logger.exception("Could not load dashboard metrics from the SQLite database")
        raise RuntimeError(
            "Could not load dashboard metrics from the SQLite database"
        ) from None
    finally:
        if connection is not None:
            connection.close()

    metrics: MetricRows = {"failed_by_country": [], "success_by_user": []}
    invalid_user_failures = 0
    for metric, label, total in rows:
        if metric == "failed_by_country":
            metrics[metric].append((label, total))
        elif metric == "success_by_user":
            metrics[metric].append((label, total))
        elif metric == "invalid_user_failures":
            invalid_user_failures = total
        else:
            raise RuntimeError("Unknown dashboard metric in the SQLite database")
    logger.info("Dashboard metrics loaded from SQLite")
    return metrics, invalid_user_failures
