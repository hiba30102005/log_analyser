import gzip
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".jsonl", ".log", ".txt")


def _log_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        candidates = [path]
    elif path.is_dir():
        candidates = sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
    else:
        raise FileNotFoundError("Input path does not exist: {}".format(path))

    for candidate in candidates:
        name = candidate.name.lower()
        if name.endswith(".gz"):
            name = name[:-3]
        if name.endswith(SUPPORTED_SUFFIXES):
            yield candidate


def iter_log_lines(path: Path) -> Iterator[Tuple[Path, int, str]]:
    """Yield supported log lines with their source file and 1-based line number."""
    for log_file in _log_files(path):
        opener = gzip.open if log_file.name.lower().endswith(".gz") else open
        try:
            with opener(log_file, mode="rt", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    yield log_file, line_number, line.rstrip("\r\n")
        except (OSError, UnicodeError):
            logger.exception("Could not read log file %s", log_file)
            raise


def parse_event(line: str) -> Dict[str, object]:
    """Parse one JSON log line into the normalized authentication event schema."""
    record = json.loads(line)
    if not isinstance(record, dict):
        raise ValueError("Each log line must contain a JSON object")

    occurred_at = record.get("timestamp")
    username = record.get("username")
    country = record.get("country", "Unknown")
    if country is None:
        country = "Unknown"
    status = record.get("status")
    reason = record.get("reason")

    if not isinstance(occurred_at, str) or not occurred_at:
        raise ValueError("Missing or invalid 'timestamp'")
    try:
        parsed_timestamp = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("'timestamp' must be an ISO 8601 date/time") from error
    if not isinstance(status, str) or status.lower() not in ("success", "failed"):
        raise ValueError("'status' must be 'success' or 'failed'")
    for field_name, field_value in (("username", username), ("country", country), ("reason", reason)):
        if field_value is not None and not isinstance(field_value, str):
            raise ValueError("'{}' must be a string or null".format(field_name))

    normalized_reason = reason.strip() if reason else None
    normalized_status = status.lower()
    return {
        "occurred_at": parsed_timestamp.isoformat(),
        "username": username,
        "country": country.strip() or "Unknown",
        "status": normalized_status,
        "reason": normalized_reason,
        "is_invalid_user": (
            normalized_status == "failed"
            and normalized_reason is not None
            and normalized_reason.lower() == "invalid_user"
        ),
    }


def toRows(path: str) -> List[str]:
    """Keep the original helper API while returning rows from every supported file."""
    return [line for _, _, line in iter_log_lines(Path(path))]
