"""Parse raw log lines into (timestamp, level, message).

Supports the common shapes:
    2026-10-07T12:00:01Z ERROR payment-service Timeout calling bank 4821
    2026-10-07 12:00:01,123 WARN Retrying request 7
    ERROR something broke            (no timestamp -> uses ingest time)
    plain text line                  (no level -> level UNKNOWN)
Anything else still parses; we never drop a line because of its format.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

LEVELS = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL")
_LEVEL_ALIASES = {"WARNING": "WARN", "CRITICAL": "FATAL"}

_LINE_RE = re.compile(
    r"""^\s*
    (?P<ts>\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?
    \s*
    (?:\[?(?P<level>TRACE|DEBUG|INFO|WARNING|WARN|ERROR|FATAL|CRITICAL)\]?\b)?
    [:\s\-]*
    (?P<msg>.*?)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class ParsedLine:
    ts: datetime  # naive UTC
    level: str
    message: str


def _parse_ts(raw: str) -> datetime | None:
    text = raw.replace(",", ".").replace(" ", "T", 1)
    # fromisoformat handles offsets like +05:30; make "+0530" and "Z" safe too
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif len(text) > 5 and text[-5] in "+-" and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_line(line: str, default_ts: datetime) -> ParsedLine | None:
    """Return a ParsedLine, or None for blank lines."""
    line = line.rstrip("\r\n")
    if not line.strip():
        return None
    m = _LINE_RE.match(line)
    if not m:  # extremely unlikely, regex matches anything, but stay safe
        return ParsedLine(default_ts, "UNKNOWN", line.strip())

    ts = _parse_ts(m.group("ts")) if m.group("ts") else None
    level = m.group("level")
    if level:
        level = level.upper()
        level = _LEVEL_ALIASES.get(level, level)
    else:
        level = "UNKNOWN"
    message = m.group("msg") or line.strip()
    return ParsedLine(ts or default_ts, level, message)


def hour_floor(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)
