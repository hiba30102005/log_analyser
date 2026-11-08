import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List

from ingestion.raw import iter_log_lines, parse_event
from serving.dashboard import create_dashboard
from storage.database import fetch_dashboard_metrics, import_events, initialize_database


logger = logging.getLogger(__name__)


def configure_logging() -> None:
    log_directory = Path("logs")
    log_directory.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_directory / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logging.basicConfig(
        level=logging.INFO,
        handlers=(file_handler, console_handler),
        force=True,
    )


def load_events(input_path: Path) -> List[Dict[str, object]]:
    root = input_path if input_path.is_dir() else input_path.parent
    events: List[Dict[str, object]] = []
    skipped_lines = 0
    for log_file, line_number, line in iter_log_lines(input_path):
        if not line.strip():
            continue
        try:
            event = parse_event(line)
        except (TypeError, ValueError) as error:
            logger.warning(
                "Skipping invalid event in %s at line %d: %s",
                log_file,
                line_number,
                error,
            )
            skipped_lines += 1
            continue
        event["source_file"] = log_file.relative_to(root).as_posix()
        event["line_number"] = line_number
        events.append(event)
    if skipped_lines:
        logger.warning("Skipped %d malformed log line(s)", skipped_lines)
    logger.info("Read %d valid event(s) from %s", len(events), input_path)
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse authentication logs with PostgreSQL.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data"),
        help="Log file or directory containing JSONL, LOG, TXT, or GZ files (default: data)",
    )
    arguments = parser.parse_args()

    try:
        configure_logging()
        events = load_events(arguments.input)
        initialize_database()
        import_events(events)
        metrics, invalid_user_failures = fetch_dashboard_metrics()
        create_dashboard(metrics, invalid_user_failures)
    except (OSError, RuntimeError) as error:
        logger.exception("Pipeline failed: %s", error)
        return 1

    logger.info("Log analysis completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
