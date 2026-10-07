from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Cluster(Base):
    """One 'kind' of log message, identified by its fingerprint + level.

    We store aggregates (counts per hour) and a few sample lines, not every
    raw log line. That keeps storage proportional to the number of distinct
    problems, not the number of log lines.
    """

    __tablename__ = "clusters"
    __table_args__ = (UniqueConstraint("fingerprint", "level", name="uq_cluster_fp_level"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(32), index=True)
    level: Mapped[str] = mapped_column(String(10), index=True)
    template: Mapped[str] = mapped_column(Text)
    total_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    buckets: Mapped[list["ClusterBucket"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )
    samples: Mapped[list["ClusterSample"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )


class ClusterBucket(Base):
    """Count of a cluster's occurrences within one hour. Powers the trend chart."""

    __tablename__ = "cluster_buckets"
    __table_args__ = (
        UniqueConstraint("cluster_id", "bucket_start", name="uq_bucket"),
        Index("ix_bucket_start", "bucket_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id", ondelete="CASCADE"))
    bucket_start: Mapped[datetime] = mapped_column(DateTime)
    count: Mapped[int] = mapped_column(Integer, default=0)

    cluster: Mapped[Cluster] = relationship(back_populates="buckets")


class ClusterSample(Base):
    """A few raw example lines per cluster, so a human can see real data."""

    __tablename__ = "cluster_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime)
    message: Mapped[str] = mapped_column(Text)

    cluster: Mapped[Cluster] = relationship(back_populates="samples")
