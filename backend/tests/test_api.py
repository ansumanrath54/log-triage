import io

from datetime import datetime, timedelta


def _upload(client, lines: list[str]):
    data = "\n".join(lines).encode()
    return client.post("/api/ingest", files={"file": ("app.log", io.BytesIO(data), "text/plain")})


def _line(ts: datetime, level: str, msg: str) -> str:
    return f"{ts.strftime('%Y-%m-%dT%H:%M:%SZ')} {level} {msg}"


def test_ingest_groups_similar_lines(client):
    base = datetime(2026, 10, 7, 10, 0, 0)
    lines = [_line(base + timedelta(seconds=i), "ERROR", f"Timeout calling bank for order {1000 + i}") for i in range(50)]
    lines += [_line(base, "ERROR", "Null pointer in OrderService")]
    r = _upload(client, lines)
    assert r.status_code == 200
    body = r.json()
    assert body["lines_parsed"] == 51
    assert body["clusters_touched"] == 2

    clusters = client.get("/api/clusters").json()["clusters"]
    assert clusters[0]["total_count"] == 50
    assert "<num>" in clusters[0]["template"]


def test_second_upload_accumulates_not_duplicates(client):
    base = datetime(2026, 10, 7, 10, 0, 0)
    batch = [_line(base, "ERROR", "Disk full on node 3")] * 5
    _upload(client, batch)
    r = _upload(client, batch)
    assert r.json()["clusters_created"] == 0
    clusters = client.get("/api/clusters").json()["clusters"]
    assert len(clusters) == 1
    assert clusters[0]["total_count"] == 10


def test_trend_detects_rising_cluster(client):
    prev_hour = datetime(2026, 10, 7, 9, 30, 0)
    this_hour = datetime(2026, 10, 7, 10, 30, 0)
    lines = [_line(prev_hour, "ERROR", f"Cache miss storm key {i}") for i in range(2)]
    lines += [_line(this_hour, "ERROR", f"Cache miss storm key {i}") for i in range(20)]
    _upload(client, lines)
    c = client.get("/api/clusters?sort=trend").json()["clusters"][0]
    assert c["trend"] == "rising"
    assert c["recent_count"] == 20 and c["previous_count"] == 2


def test_new_cluster_is_flagged(client):
    t = datetime(2026, 10, 7, 10, 15, 0)
    _upload(client, [_line(t, "ERROR", "Brand new failure mode 1")] * 3)
    assert client.get("/api/clusters").json()["clusters"][0]["trend"] == "new"


def test_detail_trend_and_samples(client):
    t = datetime(2026, 10, 7, 10, 0, 0)
    _upload(client, [_line(t + timedelta(seconds=i), "WARN", f"Slow query took {i}ms") for i in range(10)])
    cid = client.get("/api/clusters").json()["clusters"][0]["id"]

    detail = client.get(f"/api/clusters/{cid}").json()
    assert len(detail["samples"]) == 5  # capped
    trend = client.get(f"/api/clusters/{cid}/trend").json()
    assert trend["points"][0]["count"] == 10


def test_summary_falls_back_without_api_key(client):
    t = datetime(2026, 10, 7, 10, 0, 0)
    _upload(client, [_line(t, "ERROR", "Connection reset by peer 7")] * 2)
    cid = client.get("/api/clusters").json()["clusters"][0]["id"]
    r = client.post(f"/api/clusters/{cid}/summarize").json()
    assert r["used_llm"] is False
    assert "Connection reset" in r["summary"] or "connection reset" in r["summary"]


def test_bad_inputs(client):
    assert client.get("/api/clusters?sort=bogus").status_code == 400
    assert client.get("/api/clusters/9999").status_code == 404
    assert client.get("/api/clusters/9999/trend").status_code == 404
    assert _upload(client, ["", "   "]).json()["lines_parsed"] == 0
