import gzip
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".jsonl", ".log", ".txt")
FAILED_SSH_RE = re.compile(
    r"\bFailed\s+\S+\s+for\s+(?:(invalid user)\s+)?(\S+)\s+from\s+(\S+)",
    re.IGNORECASE,
)
ACCEPTED_SSH_RE = re.compile(
    r"\bAccepted\s+\S+\s+for\s+(\S+)\s+from\s+(\S+)",
    re.IGNORECASE,
)
INVALID_USER_RE = re.compile(
    r"\bInvalid user\s+(\S+)\s+from\s+(\S+)",
    re.IGNORECASE,
)


def _log_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        candidates = [path]
    elif path.is_dir():
        candidates = sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
    else:
        error = FileNotFoundError("Input path does not exist: {}".format(path))
        logger.error("%s", error)
        raise error

    for candidate in candidates:
        name = candidate.name.lower()
        if name.endswith(".gz"):
            name = name[:-3]
        if name.endswith(SUPPORTED_SUFFIXES) or re.search(r"\.log\.\d+$", name):
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


def parse_log_record(line: str) -> Dict[str, object]:
    """Normalize structured events and common OpenSSH syslog lines for display."""
    try:
        event = parse_event(line)
    except (TypeError, ValueError):
        event = None

    record: Dict[str, object] = {
        "message": line,
        "occurred_at": None,
        "username": None,
        "country": None,
        "source": None,
        "status": "other",
        "category": "Autre log",
        "is_invalid_user": False,
    }
    if event is not None:
        status = str(event["status"])
        invalid_user = bool(event["is_invalid_user"])
        record.update(
            {
                "occurred_at": event["occurred_at"],
                "username": event["username"],
                "country": event["country"],
                "status": status,
                "category": (
                    "Échec (utilisateur invalide)"
                    if invalid_user
                    else "Échec"
                    if status == "failed"
                    else "Connexion réussie"
                ),
                "is_invalid_user": invalid_user,
            }
        )
        return record

    failed_match = FAILED_SSH_RE.search(line)
    if failed_match:
        invalid_user, username, source = failed_match.groups()
        invalid = invalid_user is not None
        record.update(
            {
                "username": username,
                "source": source.rstrip(","),
                "status": "failed",
                "category": (
                    "Échec (utilisateur invalide)" if invalid else "Échec SSH"
                ),
                "is_invalid_user": invalid,
            }
        )
        return record

    accepted_match = ACCEPTED_SSH_RE.search(line)
    if accepted_match:
        username, source = accepted_match.groups()
        record.update(
            {
                "username": username,
                "source": source.rstrip(","),
                "status": "success",
                "category": "Connexion réussie",
            }
        )
        return record

    invalid_match = INVALID_USER_RE.search(line)
    if invalid_match:
        username, source = invalid_match.groups()
        record.update(
            {
                "username": username,
                "source": source.rstrip(","),
                "category": "Utilisateur invalide détecté",
                "is_invalid_user": True,
            }
        )
    elif "authentication failure" in line.lower():
        record["category"] = "Détail d'échec PAM"
    return record


def load_log_records(path: Path) -> List[Dict[str, object]]:
    """Read every line from every supported log file for dashboard display."""
    root = path if path.is_dir() else path.parent
    records: List[Dict[str, object]] = []
    for log_file, line_number, line in iter_log_lines(path):
        record = parse_log_record(line)
        record["source_file"] = log_file.relative_to(root).as_posix()
        record["line_number"] = line_number
        records.append(record)
    logger.info("Read %d log line(s) from %s", len(records), path)
    return records


def toRows(path: str) -> List[str]:
    """Keep the original helper API while returning rows from every supported file."""
    return [line for _, _, line in iter_log_lines(Path(path))]
