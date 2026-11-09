import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ingestion.raw import load_log_records
from serving.dashboard import create_dashboard


logger = logging.getLogger(__name__)
DEFAULT_INPUT = Path(__file__).resolve().parent / "data"


def configure_logging() -> None:
    log_directory = Path(__file__).resolve().parent / "logs"
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Display SSH log analysis from local log files."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Log file or directory containing JSONL, LOG, TXT, or GZ files (default: data)",
    )
    arguments = parser.parse_args()

    try:
        configure_logging()
        records = load_log_records(arguments.input)
        create_dashboard(records)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        logger.exception("Pipeline failed: %s", error)
        return 1

    logger.info("Log analysis completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
