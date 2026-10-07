"""Log fingerprinting: turn many similar log lines into one stable signature.

Idea: two log lines like
    "User 4821 failed login from 10.0.3.7 after 3 attempts"
    "User 977 failed login from 10.0.9.1 after 5 attempts"
are the same *kind* of event. We replace the variable parts (IDs, IPs,
numbers, hex strings, quoted values) with placeholders, so both become
    "user <num> failed login from <ip> after <num> attempts"
and hash that string. Same hash => same cluster.

Order of the substitutions matters: more specific patterns (UUIDs, IPs)
must run before the generic number pattern, or the number rule would
chew them up first.
"""

import hashlib
import re

# (compiled pattern, replacement). Applied in order.
_RULES = [
    # UUIDs: 8-4-4-4-12 hex
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    # IPv4 addresses (optionally with :port)
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    # 0x-prefixed hex or long hex strings (hashes, request ids)
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<hex>"),
    # Quoted values: "abc" or 'abc'
    (re.compile(r'"[^"]*"'), "<str>"),
    (re.compile(r"'[^']*'"), "<str>"),
    # File paths like /var/log/app/x.log
    (re.compile(r"(?:/[\w.\-]+){2,}"), "<path>"),
    # Any remaining number (ints, decimals, durations like 350ms)
    (re.compile(r"\b\d+(?:\.\d+)?"), "<num>"),
]

_WHITESPACE = re.compile(r"\s+")

# Cap very long lines so one giant line can't blow up regex time or storage.
MAX_LINE_LENGTH = 2000


def normalize(message: str) -> str:
    """Replace variable parts of a log message with placeholders."""
    text = message[:MAX_LINE_LENGTH]
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    text = _WHITESPACE.sub(" ", text).strip().lower()
    return text


def fingerprint(message: str) -> tuple[str, str]:
    """Return (fingerprint_hash, normalized_template) for a message."""
    template = normalize(message)
    digest = hashlib.sha1(template.encode("utf-8")).hexdigest()[:16]
    return digest, template
