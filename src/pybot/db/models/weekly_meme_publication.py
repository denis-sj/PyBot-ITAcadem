"""Published meme records used for weekly idempotence and Telegram replacement."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ..base_class import Base


class WeeklyMemePublication(Base):
    """Track source identity, publication state and every Telegram message for a meme."""

    __tablename__ = "weekly_meme_publications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('published', 'replaced', 'test')",
            name="ck_weekly_meme_publications_status",
        ),
        CheckConstraint(
            "delivery_state IN ('pending', 'complete', 'failed')",
            name="ck_weekly_meme_publications_delivery_state",
        ),
        Index(
            "uq_weekly_meme_publications_week_published",
            "publication_week",
            unique=True,
            postgresql_where=text("status = 'published' AND delivery_state = 'complete'"),
        ),
        Index("ix_weekly_meme_publications_week_status", "publication_week", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    publication_week: Mapped[date] = mapped_column(Date, nullable=False)
    lemmy_post_id: Mapped[str] = mapped_column(Text, nullable=False)
    image_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    community: Mapped[str] = mapped_column(String(200), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_message_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, server_default=text("'[]'"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    delivery_state: Mapped[str] = mapped_column(String(16), nullable=False)
    replaced_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("weekly_meme_publications.id", ondelete="SET NULL"), nullable=True
    )
    replaced_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=text("now()")
    )
