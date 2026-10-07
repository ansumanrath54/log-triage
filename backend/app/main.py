import io
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from . import llm
from .db import Base, engine, get_db
from .ingest import ingest_lines
from .models import Cluster, ClusterBucket, ClusterSample

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per upload

Base.metadata.create_all(engine)

app = FastAPI(title="Log Triage", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SORTS = {"count", "trend", "recent"}


def _reference_bucket(db: Session) -> datetime | None:
    """The newest hour present in the data. Trend compares it to the hour before.

    We use the data's own latest time, not 'now', so uploading an old log
    file still gives meaningful trends.
    """
    latest = db.scalar(select(func.max(Cluster.last_seen)))
    return latest.replace(minute=0, second=0, microsecond=0) if latest else None


def _trend_label(total: int, recent: int, prev: int, first_seen: datetime, ref: datetime) -> str:
    if prev == 0 and recent > 0 and first_seen >= ref:
        return "new"
    if recent >= 5 and recent >= 2 * max(prev, 1):
        return "rising"
    if prev >= 5 and recent <= prev / 2:
        return "falling"
    if recent == 0 and prev == 0:
        return "quiet"
    return "steady"


def _cluster_row(c: Cluster, recent: int, prev: int, ref: datetime | None) -> dict:
    return {
        "id": c.id,
        "level": c.level,
        "template": c.template,
        "total_count": c.total_count,
        "first_seen": c.first_seen.isoformat(),
        "last_seen": c.last_seen.isoformat(),
        "recent_count": recent,
        "previous_count": prev,
        "trend": _trend_label(c.total_count, recent, prev, c.first_seen, ref) if ref else "quiet",
        "has_summary": c.summary is not None,
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/ingest")
async def ingest(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a plain-text log file (max 50 MB).

    The file is read fully into memory, then processed line by line. The size
    cap keeps that safe; true streaming ingestion is listed under limitations.
    """
    size = 0
    chunks = []
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
        chunks.append(chunk)
    text = io.StringIO(b"".join(chunks).decode("utf-8", errors="replace"))
    result = ingest_lines(db, text)
    return {
        "lines_read": result.lines_read,
        "lines_parsed": result.lines_parsed,
        "clusters_touched": result.clusters_touched,
        "clusters_created": result.clusters_created,
        "seconds": round(result.seconds, 3),
        "lines_per_second": round(result.lines_per_second),
    }


@app.get("/api/clusters")
def list_clusters(
    sort: str = Query("count"),
    level: str | None = Query(None, description="e.g. ERROR, WARN"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    if sort not in SORTS:
        raise HTTPException(400, f"sort must be one of {sorted(SORTS)}")
    ref = _reference_bucket(db)

    recent_sum = func.coalesce(
        func.sum(case((ClusterBucket.bucket_start == ref, ClusterBucket.count), else_=0)), 0
    )
    prev_sum = func.coalesce(
        func.sum(
            case((ClusterBucket.bucket_start == (ref - timedelta(hours=1)) if ref else None,
                  ClusterBucket.count), else_=0)
        ),
        0,
    )

    q = (
        select(Cluster, recent_sum.label("recent"), prev_sum.label("prev"))
        .join(ClusterBucket, ClusterBucket.cluster_id == Cluster.id, isouter=True)
        .group_by(Cluster.id)
    )
    if level:
        q = q.where(Cluster.level == level.upper())

    rows = db.execute(q).all()
    out = [_cluster_row(c, int(r), int(p), ref) for c, r, p in rows]

    if sort == "count":
        out.sort(key=lambda x: x["total_count"], reverse=True)
    elif sort == "recent":
        out.sort(key=lambda x: x["last_seen"], reverse=True)
    else:  # trend: biggest recent increase first
        out.sort(key=lambda x: x["recent_count"] - x["previous_count"], reverse=True)
    return {"total_clusters": len(out), "clusters": out[:limit]}


@app.get("/api/clusters/{cluster_id}")
def cluster_detail(cluster_id: int, db: Session = Depends(get_db)):
    c = db.get(Cluster, cluster_id)
    if c is None:
        raise HTTPException(404, "Cluster not found")
    ref = _reference_bucket(db)
    recent = prev = 0
    if ref:
        for b in db.scalars(select(ClusterBucket).where(ClusterBucket.cluster_id == c.id)):
            if b.bucket_start == ref:
                recent = b.count
            elif b.bucket_start == ref - timedelta(hours=1):
                prev = b.count
    samples = db.scalars(
        select(ClusterSample).where(ClusterSample.cluster_id == c.id).order_by(ClusterSample.ts)
    ).all()
    row = _cluster_row(c, recent, prev, ref)
    row["summary"] = c.summary
    row["samples"] = [{"ts": s.ts.isoformat(), "message": s.message} for s in samples]
    return row


@app.get("/api/clusters/{cluster_id}/trend")
def cluster_trend(cluster_id: int, db: Session = Depends(get_db)):
    if db.get(Cluster, cluster_id) is None:
        raise HTTPException(404, "Cluster not found")
    buckets = db.scalars(
        select(ClusterBucket)
        .where(ClusterBucket.cluster_id == cluster_id)
        .order_by(ClusterBucket.bucket_start)
    ).all()
    return {
        "cluster_id": cluster_id,
        "points": [{"hour": b.bucket_start.isoformat(), "count": b.count} for b in buckets],
    }


@app.post("/api/clusters/{cluster_id}/summarize")
def summarize_cluster(cluster_id: int, refresh: bool = False, db: Session = Depends(get_db)):
    c = db.get(Cluster, cluster_id)
    if c is None:
        raise HTTPException(404, "Cluster not found")
    if c.summary and not refresh:
        return {"summary": c.summary, "used_llm": True, "provider": None, "reason": None, "cached": True}
    samples = [
        s.message
        for s in db.scalars(select(ClusterSample).where(ClusterSample.cluster_id == c.id)).all()
    ]
    result = llm.summarize(c.template, c.level, c.total_count, samples)
    if result.used_llm:  # only cache real LLM answers; fallbacks are cheap to regenerate
        c.summary = result.text
        db.commit()
    return {
        "summary": result.text,
        "used_llm": result.used_llm,
        "provider": result.provider,
        "reason": result.reason,  # why the fallback was used, or null
        "cached": False,
    }


@app.delete("/api/reset")
def reset(db: Session = Depends(get_db)):
    """Wipe all data (handy for demos)."""
    db.execute(delete(ClusterSample))
    db.execute(delete(ClusterBucket))
    db.execute(delete(Cluster))
    db.commit()
    return {"status": "cleared"}
