from datetime import datetime

from app.parsing import parse_line

NOW = datetime(2026, 1, 1, 0, 0, 0)


def test_iso_timestamp_level_and_message():
    p = parse_line("2026-10-07T12:00:01Z ERROR payment-service Timeout calling bank", NOW)
    assert p.ts == datetime(2026, 10, 7, 12, 0, 1)
    assert p.level == "ERROR"
    assert p.message.startswith("payment-service Timeout")


def test_space_and_comma_millis_timestamp():
    p = parse_line("2026-10-07 12:00:01,123 WARN Retrying request 7", NOW)
    assert p.ts.year == 2026 and p.ts.hour == 12
    assert p.level == "WARN"


def test_timezone_offset_converted_to_utc():
    p = parse_line("2026-10-07T12:00:00+05:30 ERROR boom", NOW)
    assert p.ts == datetime(2026, 10, 7, 6, 30, 0)


def test_no_timestamp_uses_default_and_no_level_is_unknown():
    p = parse_line("something odd happened", NOW)
    assert p.ts == NOW
    assert p.level == "UNKNOWN"
    assert p.message == "something odd happened"


def test_warning_alias_and_blank_line():
    assert parse_line("WARNING disk almost full", NOW).level == "WARN"
    assert parse_line("   \n", NOW) is None
