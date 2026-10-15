"""Persist one-time community achievement awards and delivery state."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...base_class import Base

if TYPE_CHECKING:
    from .user import User


class UserAchievementAward(Base):
    """An immutable achievement award with independent delivery markers."""

    __tablename__ = "user_achievement_awards"
    __table_args__ = (UniqueConstraint("user_id", "code", name="uq_user_achievement_awards_user_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    achieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))

    user: Mapped[User] = relationship("User", back_populates="community_achievement_awards")
