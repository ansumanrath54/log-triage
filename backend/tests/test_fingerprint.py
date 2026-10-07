from app.fingerprint import fingerprint, normalize


def test_numbers_and_ips_are_replaced():
    a = "User 4821 failed login from 10.0.3.7 after 3 attempts"
    b = "User 977 failed login from 192.168.1.20 after 5 attempts"
    assert normalize(a) == "user <num> failed login from <ip> after <num> attempts"
    assert fingerprint(a) == fingerprint(b)


def test_different_messages_get_different_fingerprints():
    a = "Connection timeout to db-primary after 30s"
    b = "Null pointer exception in OrderService"
    assert fingerprint(a)[0] != fingerprint(b)[0]


def test_uuid_and_hex_are_replaced():
    msg = "Request 3f2b8c1a-9d4e-4b7a-8c11-2f5e6a7b8c9d failed, trace 0xDEADBEEF"
    assert normalize(msg) == "request <uuid> failed, trace <hex>"


def test_quoted_values_and_paths_are_replaced():
    msg = "Cannot open file '/var/log/app/out.log' for user \"alice\""
    assert normalize(msg) == "cannot open file <str> for user <str>"


def test_ip_is_not_split_into_numbers():
    # IP rule must run before the generic number rule
    assert "<ip>" in normalize("peer 10.1.2.3:8080 reset connection")
    assert "<num>" not in normalize("peer 10.1.2.3:8080 reset connection")


def test_whitespace_and_case_are_normalized():
    assert fingerprint("Disk   FULL on  node 7") == fingerprint("disk full on node 12")


def test_very_long_line_is_truncated_not_crashing():
    msg = "error " + "x" * 100_000
    digest, template = fingerprint(msg)
    assert len(digest) == 16
    assert len(template) <= 2100
