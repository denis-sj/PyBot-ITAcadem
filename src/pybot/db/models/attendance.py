"""ORM models for attendance accounting."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base_class import Base

if TYPE_CHECKING:
    from .user_module import User


class AttendanceImportStatus(StrEnum):
    """Lifecycle status of an attendance sheet import."""

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class AttendanceImport(Base):
    """Record of an uploaded attendance sheet image and its parsing lifecycle."""

    __tablename__ = "attendance_imports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    author_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=AttendanceImportStatus.DRAFT.value,
        server_default="DRAFT",
    )
    photo_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    photo_mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    photo_checksum: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_ai_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    dates: Mapped[list[AttendanceDate]] = relationship(
        "AttendanceDate",
        back_populates="import_record",
        cascade="all, delete-orphan",
    )
    records: Mapped[list[AttendanceRecord]] = relationship(
        "AttendanceRecord",
        back_populates="import_record",
        cascade="all, delete-orphan",
    )


class AttendanceDate(Base):
    """Calendar date block tied to a confirmed or draft attendance import."""

    __tablename__ = "attendance_dates"
    __table_args__ = (UniqueConstraint("date", name="uq_attendance_dates_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    import_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("attendance_imports.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[dt.date] = mapped_column(Date, nullable=False, unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    import_record: Mapped[AttendanceImport] = relationship(
        "AttendanceImport",
        back_populates="dates",
    )
    records: Mapped[list[AttendanceRecord]] = relationship(
        "AttendanceRecord",
        back_populates="attendance_date",
        cascade="all, delete-orphan",
    )


class AttendanceRecord(Base):
    """Individual participant attendance row confirmed for a given date."""

    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_attendance_records_user_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    import_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("attendance_imports.id", ondelete="CASCADE"),
        nullable=True,
    )
    attendance_date_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("attendance_dates.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[dt.time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[dt.time | None] = mapped_column(Time, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_empty_time: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="photo_ai", server_default="photo_ai")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    import_record: Mapped[AttendanceImport | None] = relationship(
        "AttendanceImport",
        back_populates="records",
    )
    attendance_date: Mapped[AttendanceDate | None] = relationship(
        "AttendanceDate",
        back_populates="records",
    )
    user: Mapped[User] = relationship("User")


class AttendanceChangeHistory(Base):
    """Audit log record for manual attendance modifications."""

    __tablename__ = "attendance_change_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    record_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    visit_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    old_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    admin_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    admin_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[User] = relationship("User")
