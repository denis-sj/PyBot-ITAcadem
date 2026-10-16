"""Database access for weekly meme publication and replacement state."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import WeeklyMemePublication
from ..dto.meme_of_the_week import LemmyPostDTO, MemePublicationDTO

_ADVISORY_LOCK_PREFIX = 0x4D454D4500000000
_SEEN_STATUSES = ("published", "replaced")


class WeeklyMemePublicationRepository:
    """Persist publication state without owning transaction boundaries beyond each state change."""

    async def acquire_week_lock(self, db: AsyncSession, publication_week: date) -> None:
        """Serialize decisions for one week before a pending record is created."""
        lock_key = _ADVISORY_LOCK_PREFIX + publication_week.toordinal()
        await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

    async def find_seen_memes(self, db: AsyncSession) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return source identities already published or currently being delivered."""
        statement = select(WeeklyMemePublication.lemmy_post_id, WeeklyMemePublication.image_urls).where(
            WeeklyMemePublication.status.in_(_SEEN_STATUSES),
            WeeklyMemePublication.delivery_state.in_(("pending", "complete")),
        )
        rows = (await db.execute(statement)).all()
        return tuple((post_id, tuple(image_urls)) for post_id, image_urls in rows)

    async def find_week_records(self, db: AsyncSession, publication_week: date) -> tuple[MemePublicationDTO, ...]:
        statement = (
            select(WeeklyMemePublication)
            .where(WeeklyMemePublication.publication_week == publication_week)
            .order_by(WeeklyMemePublication.id)
        )
        return tuple(self._to_dto(record) for record in (await db.scalars(statement)).all())

    async def find_current_publication(self, db: AsyncSession, publication_week: date) -> MemePublicationDTO | None:
        statement = select(WeeklyMemePublication).where(
            WeeklyMemePublication.publication_week == publication_week,
            WeeklyMemePublication.status == "published",
            WeeklyMemePublication.delivery_state == "complete",
        )
        record = await db.scalar(statement)
        return self._to_dto(record) if record is not None else None

    async def count_replacements(self, db: AsyncSession, publication_week: date) -> int:
        statement = (
            select(func.count())
            .select_from(WeeklyMemePublication)
            .where(
                WeeklyMemePublication.publication_week == publication_week,
                WeeklyMemePublication.status == "replaced",
                WeeklyMemePublication.delivery_state == "complete",
            )
        )
        return int(await db.scalar(statement) or 0)

    async def create_publication(
        self,
        db: AsyncSession,
        *,
        publication_week: date,
        post: LemmyPostDTO,
        image_urls: tuple[str, ...],
        status: str,
    ) -> MemePublicationDTO:
        record = WeeklyMemePublication(
            publication_week=publication_week,
            lemmy_post_id=post.post_id,
            image_urls=list(image_urls),
            community=post.community,
            score=post.score,
            telegram_message_ids=[],
            status=status,
            delivery_state="pending",
            created_at=datetime.now(UTC),
        )
        db.add(record)
        await db.flush()
        result = self._to_dto(record)
        await db.commit()
        return result

    async def mark_published(
        self,
        db: AsyncSession,
        *,
        publication_id: int,
        telegram_message_ids: tuple[int, ...],
        status: str = "published",
    ) -> None:
        await db.execute(
            update(WeeklyMemePublication)
            .where(WeeklyMemePublication.id == publication_id)
            .values(status=status, delivery_state="complete", telegram_message_ids=list(telegram_message_ids))
        )
        await db.commit()

    async def mark_failed(self, db: AsyncSession, publication_id: int) -> None:
        await db.execute(
            update(WeeklyMemePublication)
            .where(WeeklyMemePublication.id == publication_id)
            .values(delivery_state="failed")
        )
        await db.commit()

    async def complete_replacement(
        self,
        db: AsyncSession,
        *,
        old_publication_id: int,
        new_publication_id: int,
        replacer_telegram_id: int,
        telegram_message_ids: tuple[int, ...],
    ) -> None:
        await db.execute(
            update(WeeklyMemePublication)
            .where(WeeklyMemePublication.id == old_publication_id)
            .values(
                status="replaced",
                delivery_state="complete",
                replaced_by_id=new_publication_id,
                replaced_by_telegram_id=replacer_telegram_id,
            )
        )
        await db.execute(
            update(WeeklyMemePublication)
            .where(WeeklyMemePublication.id == new_publication_id)
            .values(status="published", delivery_state="complete", telegram_message_ids=list(telegram_message_ids))
        )
        await db.commit()

    @staticmethod
    def _to_dto(record: WeeklyMemePublication) -> MemePublicationDTO:
        return MemePublicationDTO(
            id=record.id,
            publication_week=record.publication_week,
            post_id=record.lemmy_post_id,
            image_urls=tuple(record.image_urls),
            community=record.community,
            score=record.score,
            telegram_message_ids=tuple(record.telegram_message_ids),
            status=record.status,
            delivery_state=record.delivery_state,
            replaced_by_id=record.replaced_by_id,
            replaced_by_telegram_id=record.replaced_by_telegram_id,
            created_at=record.created_at,
        )
