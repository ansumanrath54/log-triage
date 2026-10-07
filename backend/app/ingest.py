"""Batch ingestion: lines in -> clusters, hourly buckets and samples out.

Performance idea: never touch the database once per line. We first
aggregate the whole upload in memory (a dict keyed by cluster), then write
each distinct cluster once. For N lines with K distinct clusters that is
O(N) in-memory work plus O(K) database work, instead of O(N) writes.
"""

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .fingerprint import fingerprint
from .models import Cluster, ClusterBucket, ClusterSample
from .parsing import hour_floor, parse_line

MAX_SAMPLES_PER_CLUSTER = 5
LOOKUP_CHUNK = 500  # keep IN (...) lists small for SQLite/Postgres


@dataclass
class _Agg:
    level: str
    fp: str
    template: str
    count: int = 0
    first: datetime | None = None
    last: datetime | None = None
    buckets: Counter = field(default_factory=Counter)
    samples: list[tuple[datetime, str]] = field(default_factory=list)


@dataclass
class IngestResult:
    lines_read: int
    lines_parsed: int
    clusters_touched: int
    clusters_created: int
    seconds: float

    @property
    def lines_per_second(self) -> float:
        return self.lines_parsed / self.seconds if self.seconds > 0 else 0.0


def ingest_lines(db: Session, lines: Iterable[str], now: datetime | None = None) -> IngestResult:
    started = time.perf_counter()
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, like all stored times

    aggs: dict[tuple[str, str], _Agg] = {}
    read = parsed = 0

    # ---- Phase 1: aggregate in memory ----
    for raw in lines:
        read += 1
        p = parse_line(raw, now)
        if p is None:
            continue
        parsed += 1
        fp, template = fingerprint(p.message)
        key = (fp, p.level)
        agg = aggs.get(key)
        if agg is None:
            agg = aggs[key] = _Agg(level=p.level, fp=fp, template=template)
        agg.count += 1
        agg.first = p.ts if agg.first is None or p.ts < agg.first else agg.first
        agg.last = p.ts if agg.last is None or p.ts > agg.last else agg.last
        agg.buckets[hour_floor(p.ts)] += 1
        if len(agg.samples) < MAX_SAMPLES_PER_CLUSTER:
            agg.samples.append((p.ts, p.message[:1000]))

    if not aggs:
        return IngestResult(read, parsed, 0, 0, time.perf_counter() - started)

    # ---- Phase 2: load existing clusters for these keys (chunked) ----
    existing: dict[tuple[str, str], Cluster] = {}
    fps = list({fp for fp, _ in aggs})
    for i in range(0, len(fps), LOOKUP_CHUNK):
        chunk = fps[i : i + LOOKUP_CHUNK]
        for c in db.scalars(select(Cluster).where(Cluster.fingerprint.in_(chunk))):
            existing[(c.fingerprint, c.level)] = c

    # ---- Phase 3: upsert clusters ----
    created = 0
    for key, agg in aggs.items():
        cluster = existing.get(key)
        if cluster is None:
            cluster = Cluster(
                fingerprint=agg.fp,
                level=agg.level,
                template=agg.template,
                total_count=0,
                first_seen=agg.first,
                last_seen=agg.last,
            )
            db.add(cluster)
            existing[key] = cluster
            created += 1
        cluster.total_count += agg.count
        if agg.first < cluster.first_seen:
            cluster.first_seen = agg.first
        if agg.last > cluster.last_seen:
            cluster.last_seen = agg.last
    db.flush()  # assigns ids to new clusters

    # ---- Phase 4: upsert hourly buckets ----
    cluster_ids = [c.id for c in existing.values()]
    existing_buckets: dict[tuple[int, datetime], ClusterBucket] = {}
    for i in range(0, len(cluster_ids), LOOKUP_CHUNK):
        chunk = cluster_ids[i : i + LOOKUP_CHUNK]
        for b in db.scalars(select(ClusterBucket).where(ClusterBucket.cluster_id.in_(chunk))):
            existing_buckets[(b.cluster_id, b.bucket_start)] = b

    for key, agg in aggs.items():
        cid = existing[key].id
        for bucket_start, n in agg.buckets.items():
            b = existing_buckets.get((cid, bucket_start))
            if b is None:
                db.add(ClusterBucket(cluster_id=cid, bucket_start=bucket_start, count=n))
            else:
                b.count += n

    # ---- Phase 5: top up samples (max N per cluster) ----
    sample_counts = Counter()
    for i in range(0, len(cluster_ids), LOOKUP_CHUNK):
        chunk = cluster_ids[i : i + LOOKUP_CHUNK]
        for s in db.scalars(select(ClusterSample).where(ClusterSample.cluster_id.in_(chunk))):
            sample_counts[s.cluster_id] += 1
    for key, agg in aggs.items():
        cid = existing[key].id
        room = MAX_SAMPLES_PER_CLUSTER - sample_counts[cid]
        for ts, msg in agg.samples[: max(room, 0)]:
            db.add(ClusterSample(cluster_id=cid, ts=ts, message=msg))

    db.commit()
    return IngestResult(read, parsed, len(aggs), created, time.perf_counter() - started)
