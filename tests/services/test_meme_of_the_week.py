from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from collections.abc import Sequence

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from sqlalchemy.ext.asyncio import AsyncSession

from pybot.core.config import AppSettings
from pybot.dto.meme_of_the_week import LemmyPostDTO, MemeImageDTO, MemePublicationDTO
from pybot.infrastructure.adapters.lemmy_http_client import _image_signature
from pybot.infrastructure.weekly_meme_publication_repository import WeeklyMemePublicationRepository
from pybot.services.meme_of_the_week import (
    MemeOfTheWeekService,
    MemePublicationStatus,
    _extract_markdown_image_urls,
    build_meme_caption,
    strip_markdown_text,
)
from pybot.services.ports.lemmy_client import LemmyClientPort
from pybot.services.ports.meme_delivery import MemeDeliveryError, MemeDeliveryPort


class IsolatedSettings(AppSettings):
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def meme_settings(*, max_replacements_per_week: int = 3) -> IsolatedSettings:
    return IsolatedSettings.model_validate(
        {
            "BOT_TOKEN": "123456:prod",
            "BOT_MODE": "prod",
            "ROLE_REQUEST_ADMIN_TG_ID": 11,
            "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/pybot_test",
            "MEME_ENABLED": True,
            "MEME_CHAT_ID": -100123,
            "MEME_TOPIC_ID": 42,
            "MEME_MAX_REPLACEMENTS_PER_WEEK": max_replacements_per_week,
        }
    )


def post(
    post_id: str,
    *,
    score: int = 30,
    url: str | None = "https://cdn.example.test/meme.jpg",
    url_content_type: str | None = None,
    body: str | None = None,
    nsfw: bool = False,
    removed: bool = False,
    deleted: bool = False,
) -> LemmyPostDTO:
    return LemmyPostDTO(
        post_id=post_id,
        community="programmer_humor@programming.dev",
        title=f"Title {post_id}",
        score=score,
        url=url,
        url_content_type=url_content_type,
        body=body,
        nsfw=nsfw,
        removed=removed,
        deleted=deleted,
    )


class FakeLemmyClient(LemmyClientPort):
    def __init__(self, posts: dict[str, list[LemmyPostDTO]], images: dict[str, MemeImageDTO] | None = None) -> None:
        self.posts = posts
        self.images = images or {}
        self.post_calls: list[str] = []
        self.image_calls: list[str] = []

    async def find_posts(self, community: str, sort: str) -> tuple[LemmyPostDTO, ...]:
        self.post_calls.append(sort)
        return tuple(self.posts.get(sort, ()))

    async def download_image(self, url: str) -> MemeImageDTO:
        self.image_calls.append(url)
        return self.images.get(url, MemeImageDTO(url, b"image", "image/jpeg", ".jpg"))


class FakeRepository(WeeklyMemePublicationRepository):
    def __init__(self, records: list[MemePublicationDTO] | None = None) -> None:
        self.records = records or []
        self.next_id = max((record.id for record in self.records), default=0) + 1

    async def acquire_week_lock(self, db: AsyncSession, publication_week: date) -> None:
        del db, publication_week

    async def find_week_records(self, db: AsyncSession, publication_week: date) -> tuple[MemePublicationDTO, ...]:
        del db
        return tuple(record for record in self.records if record.publication_week == publication_week)

    async def find_current_publication(self, db: AsyncSession, publication_week: date) -> MemePublicationDTO | None:
        del db
        return next(
            (
                record
                for record in reversed(self.records)
                if record.publication_week == publication_week
                and record.status == "published"
                and record.delivery_state == "complete"
            ),
            None,
        )

    async def count_replacements(self, db: AsyncSession, publication_week: date) -> int:
        del db
        return sum(
            record.publication_week == publication_week
            and record.status == "replaced"
            and record.delivery_state == "complete"
            for record in self.records
        )

    async def find_seen_memes(self, db: AsyncSession) -> tuple[tuple[str, tuple[str, ...]], ...]:
        del db
        return tuple(
            (record.post_id, record.image_urls)
            for record in self.records
            if record.status in {"published", "replaced"} and record.delivery_state in {"pending", "complete"}
        )

    async def create_publication(
        self,
        db: AsyncSession,
        *,
        publication_week: date,
        post: LemmyPostDTO,
        image_urls: tuple[str, ...],
        status: str,
    ) -> MemePublicationDTO:
        del db
        record = MemePublicationDTO(
            id=self.next_id,
            publication_week=publication_week,
            post_id=post.post_id,
            image_urls=image_urls,
            community=post.community,
            score=post.score,
            telegram_message_ids=(),
            status=status,
            delivery_state="pending",
            replaced_by_id=None,
            replaced_by_telegram_id=None,
            created_at=datetime.now(UTC),
        )
        self.next_id += 1
        self.records.append(record)
        return record

    async def mark_published(
        self,
        db: AsyncSession,
        *,
        publication_id: int,
        telegram_message_ids: tuple[int, ...],
        status: str = "published",
    ) -> None:
        del db
        self._replace(
            publication_id,
            status=status,
            delivery_state="complete",
            telegram_message_ids=telegram_message_ids,
        )

    async def mark_failed(self, db: AsyncSession, publication_id: int) -> None:
        del db
        self._replace(publication_id, delivery_state="failed")

    async def complete_replacement(
        self,
        db: AsyncSession,
        *,
        old_publication_id: int,
        new_publication_id: int,
        replacer_telegram_id: int,
        telegram_message_ids: tuple[int, ...],
    ) -> None:
        del db
        self._replace(
            old_publication_id,
            status="replaced",
            delivery_state="complete",
            replaced_by_id=new_publication_id,
            replaced_by_telegram_id=replacer_telegram_id,
        )
        self._replace(
            new_publication_id,
            status="published",
            delivery_state="complete",
            telegram_message_ids=telegram_message_ids,
        )

    def _replace(
        self,
        publication_id: int,
        *,
        status: str | None = None,
        delivery_state: str | None = None,
        telegram_message_ids: tuple[int, ...] | None = None,
        replaced_by_id: int | None = None,
        replaced_by_telegram_id: int | None = None,
    ) -> None:
        updated_records: list[MemePublicationDTO] = []
        for record in self.records:
            if record.id != publication_id:
                updated_records.append(record)
                continue
            updated_records.append(
                replace(
                    record,
                    status=status if status is not None else record.status,
                    delivery_state=delivery_state if delivery_state is not None else record.delivery_state,
                    telegram_message_ids=(
                        telegram_message_ids if telegram_message_ids is not None else record.telegram_message_ids
                    ),
                    replaced_by_id=replaced_by_id if replaced_by_id is not None else record.replaced_by_id,
                    replaced_by_telegram_id=(
                        replaced_by_telegram_id
                        if replaced_by_telegram_id is not None
                        else record.replaced_by_telegram_id
                    ),
                )
            )
        self.records = updated_records


class FakeDelivery(MemeDeliveryPort):
    def __init__(self, *, fail_urls: set[str] | None = None, fail_text: bool = False) -> None:
        self.fail_urls = fail_urls or set()
        self.fail_text = fail_text
        self.next_message_id = 100
        self.sent_images: list[tuple[str, ...]] = []
        self.sent_texts: list[str] = []
        self.deleted_ids: list[int] = []

    async def send_images(
        self,
        *,
        chat_id: int,
        topic_id: int,
        images: Sequence[MemeImageDTO],
        caption: str,
    ) -> tuple[int, ...]:
        del chat_id, topic_id, caption
        source_urls = tuple(image.source_url for image in images)
        self.sent_images.append(source_urls)
        if self.fail_urls.intersection(source_urls):
            raise MemeDeliveryError("partial album failure", sent_message_ids=(77,))
        message_ids = tuple(range(self.next_message_id, self.next_message_id + len(source_urls)))
        self.next_message_id += len(source_urls)
        return message_ids

    async def send_text(self, *, chat_id: int, topic_id: int, text: str) -> int:
        del chat_id, topic_id
        if self.fail_text:
            raise MemeDeliveryError("text send failed")
        self.sent_texts.append(text)
        self.next_message_id += 1
        return self.next_message_id - 1

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        del chat_id
        self.deleted_ids.append(message_id)


def service(
    client: FakeLemmyClient,
    *,
    repository: FakeRepository | None = None,
    delivery: FakeDelivery | None = None,
    max_replacements_per_week: int = 3,
) -> MemeOfTheWeekService:
    return MemeOfTheWeekService(
        AsyncSession(),
        repository or FakeRepository(),
        client,
        delivery or FakeDelivery(),
        meme_settings(max_replacements_per_week=max_replacements_per_week),
    )


@pytest.mark.asyncio
async def test_candidate_filtering_checks_score_status_urls_and_image_count_in_order() -> None:
    body_urls = "\n".join(f"![image](https://cdn.example.test/{index}.png)" for index in range(11))
    client = FakeLemmyClient(
        {
            "TopWeek": [
                post("nsfw", score=100, nsfw=True),
                post("removed", score=90, removed=True),
                post("deleted", score=80, deleted=True),
                post("low", score=19),
                post("article", score=70, url="https://example.test/article"),
                post("too-many", score=60, url=None, body=body_urls),
                post(
                    "accepted",
                    score=50,
                    url="https://cdn.example.test/good.jpg",
                    body="![other](https://cdn.example.test/second.webp)",
                ),
            ]
        }
    )

    candidate = await service(client)._find_candidate(set(), set())

    assert candidate is not None
    assert candidate.post.post_id == "accepted"
    assert candidate.image_urls == (
        "https://cdn.example.test/good.jpg",
        "https://cdn.example.test/second.webp",
    )
    assert client.image_calls == list(candidate.image_urls)


@pytest.mark.asyncio
async def test_candidate_skips_multi_gif_and_mixed_albums_for_next_photo_post() -> None:
    mixed_photo_url = "https://cdn.example.test/mixed.jpg"
    mixed_gif_url = "https://cdn.example.test/mixed.gif"
    first_gif_url = "https://cdn.example.test/first.gif"
    second_gif_url = "https://cdn.example.test/second.gif"
    photo_urls = ("https://cdn.example.test/photo.jpg", "https://cdn.example.test/photo.webp")
    client = FakeLemmyClient(
        {
            "TopWeek": [
                post("mixed", score=60, url=mixed_photo_url, body=f"![animation]({mixed_gif_url})"),
                post("multiple-gifs", score=50, url=first_gif_url, body=f"![animation]({second_gif_url})"),
                post("photos-only", score=40, url=photo_urls[0], body=f"![photo]({photo_urls[1]})"),
            ]
        },
        {
            mixed_gif_url: MemeImageDTO(mixed_gif_url, b"gif", "image/gif", ".gif"),
            first_gif_url: MemeImageDTO(first_gif_url, b"gif", "image/gif", ".gif"),
            second_gif_url: MemeImageDTO(second_gif_url, b"gif", "image/gif", ".gif"),
        },
    )

    candidate = await service(client)._find_candidate(set(), set())

    assert candidate is not None
    assert candidate.post.post_id == "photos-only"
    assert candidate.image_urls == photo_urls
    assert all(not image.is_animation for image in candidate.images)
    assert client.image_calls == [mixed_photo_url, mixed_gif_url, first_gif_url, second_gif_url, *photo_urls]


@pytest.mark.asyncio
async def test_top_month_is_used_only_when_week_has_no_acceptable_candidate() -> None:
    client = FakeLemmyClient({"TopWeek": [post("too-low", score=3)], "TopMonth": [post("monthly")]})

    candidate = await service(client)._find_candidate(set(), set())

    assert candidate is not None
    assert candidate.post.post_id == "monthly"
    assert client.post_calls == ["TopWeek", "TopMonth"]


@pytest.mark.asyncio
async def test_seen_post_or_image_url_is_not_reselected() -> None:
    client = FakeLemmyClient({"TopWeek": [post("duplicate-post"), post("duplicate-image")]})
    duplicate_url = "https://cdn.example.test/meme.jpg"

    candidate = await service(client)._find_candidate({"duplicate-post"}, {duplicate_url})

    assert candidate is None
    assert client.image_calls == []


@pytest.mark.parametrize(
    "content,expected",
    [
        (b"\xff\xd8\xffpayload", ("image/jpeg", ".jpg")),
        (b"\x89PNG\r\n\x1a\npayload", ("image/png", ".png")),
        (b"GIF89apayload", ("image/gif", ".gif")),
        (b"RIFFxxxxWEBPpayload", ("image/webp", ".webp")),
        (b"<html>not image</html>", None),
    ],
)
def test_image_type_is_detected_from_signature(content: bytes, expected: tuple[str, str] | None) -> None:
    assert _image_signature(content) == expected


def test_images_are_collected_in_post_url_then_body_order_and_deduplicated() -> None:
    urls = _extract_markdown_image_urls(
        "before ![first](https://cdn.example.test/first.png) then ![second](<https://cdn.example.test/second.gif>)"
    )

    assert urls == (
        "https://cdn.example.test/first.png",
        "https://cdn.example.test/second.gif",
    )
    candidate = MemeOfTheWeekService._collect_image_urls(
        post("ordered", body="![first](https://cdn.example.test/first.png)\n![same](https://cdn.example.test/meme.jpg)")
    )
    assert candidate == (
        "https://cdn.example.test/meme.jpg",
        "https://cdn.example.test/first.png",
    )


def test_markdown_body_becomes_plain_text_with_images_removed() -> None:
    body = (
        "# Heading\n\n**bold** [docs](https://example.test)\n"
        "![photo](https://cdn.example.test/a.png)\n> quote\n\nuse snake_case"
    )

    assert strip_markdown_text(body) == "Heading\n\nbold docs (https://example.test)\nquote\n\nuse snake_case"


def test_long_caption_places_body_in_a_separate_continuation() -> None:
    caption, continuation = build_meme_caption("Compiler meme", "body " * 250)

    assert len(caption) <= 1024
    assert caption == "😂 Мем недели\nCompiler meme"
    assert continuation == ("body " * 250).strip()


def test_publication_week_uses_yekaterinburg_monday() -> None:
    from pybot.services.meme_of_the_week import YEKATERINBURG_TZ

    local_time = datetime(2026, 10, 21, 16, 16, tzinfo=YEKATERINBURG_TZ)

    assert MemeOfTheWeekService._publication_week(local_time).isoformat() == "2026-10-19"


@pytest.mark.asyncio
async def test_empty_candidate_set_sends_nothing_to_telegram() -> None:
    delivery = FakeDelivery()
    meme_service = service(FakeLemmyClient({"TopWeek": [], "TopMonth": []}), delivery=delivery)

    result = await meme_service.publish_weekly(datetime(2026, 10, 21, tzinfo=UTC))

    assert result.status is MemePublicationStatus.NO_CANDIDATES
    assert delivery.sent_images == []
    assert delivery.sent_texts == []


@pytest.mark.asyncio
async def test_weekly_publication_is_idempotent_across_repeated_task_runs() -> None:
    delivery = FakeDelivery()
    repository = FakeRepository()
    meme_service = service(FakeLemmyClient({"TopWeek": [post("weekly")]}), repository=repository, delivery=delivery)
    now = datetime(2026, 10, 21, 16, 16, tzinfo=UTC)

    first_result = await meme_service.publish_weekly(now)
    second_result = await meme_service.publish_weekly(now)

    assert first_result.status is MemePublicationStatus.PUBLISHED
    assert second_result.status is MemePublicationStatus.ALREADY_PUBLISHED
    assert len(delivery.sent_images) == 1
    assert [record.status for record in repository.records] == ["published"]


@pytest.mark.asyncio
async def test_partial_media_failure_is_cleaned_up_and_next_candidate_is_published() -> None:
    bad_url = "https://cdn.example.test/broken.jpg"
    delivery = FakeDelivery(fail_urls={bad_url})
    repository = FakeRepository()
    client = FakeLemmyClient({"TopWeek": [post("broken", url=bad_url, score=50), post("good", score=40)]})
    meme_service = service(client, repository=repository, delivery=delivery)

    result = await meme_service.publish_weekly(datetime(2026, 10, 21, 16, 16, tzinfo=UTC))

    assert result.status is MemePublicationStatus.PUBLISHED
    assert delivery.deleted_ids == [77]
    assert [record.delivery_state for record in repository.records] == ["failed", "complete"]
    assert repository.records[1].post_id == "good"


@pytest.mark.asyncio
async def test_failed_long_caption_rolls_back_already_sent_media() -> None:
    delivery = FakeDelivery(fail_text=True)
    repository = FakeRepository()
    client = FakeLemmyClient({"TopWeek": [post("long-body", body="body " * 300)]})

    result = await service(client, repository=repository, delivery=delivery).publish_weekly(
        datetime(2026, 10, 21, 16, 16, tzinfo=UTC)
    )

    assert result.status is MemePublicationStatus.FAILED
    assert delivery.deleted_ids == [100]
    assert [record.delivery_state for record in repository.records] == ["failed"]


@pytest.mark.asyncio
async def test_long_body_follows_media_and_both_message_ids_are_persisted() -> None:
    delivery = FakeDelivery()
    repository = FakeRepository()
    client = FakeLemmyClient({"TopWeek": [post("long-body", body="body " * 300)]})
    meme_service = service(client, repository=repository, delivery=delivery)

    result = await meme_service.publish_test(datetime(2026, 10, 21, 16, 16, tzinfo=UTC))

    assert result.status is MemePublicationStatus.PUBLISHED
    assert delivery.sent_images == [("https://cdn.example.test/meme.jpg",)]
    assert delivery.sent_texts == [("body " * 300).strip()]
    assert repository.records[0].status == "test"
    assert len(repository.records[0].telegram_message_ids) == 2


@pytest.mark.asyncio
async def test_test_publication_does_not_consume_weekly_slot() -> None:
    delivery = FakeDelivery()
    repository = FakeRepository()
    meme_service = service(FakeLemmyClient({"TopWeek": [post("test-first")]}), repository=repository, delivery=delivery)
    now = datetime(2026, 10, 21, 16, 16, tzinfo=UTC)

    await meme_service.publish_test(now)
    weekly_result = await meme_service.publish_weekly(now)

    assert weekly_result.status is MemePublicationStatus.PUBLISHED
    assert [record.status for record in repository.records] == ["test", "published"]
    assert len(delivery.sent_images) == 2


@pytest.mark.asyncio
async def test_replacement_deletes_every_previous_message_and_records_replacer() -> None:
    week = date(2026, 10, 19)
    current = MemePublicationDTO(
        id=1,
        publication_week=week,
        post_id="old-post",
        image_urls=("https://cdn.example.test/old.jpg",),
        community="programmer_humor@programming.dev",
        score=40,
        telegram_message_ids=(11, 12, 13),
        status="published",
        delivery_state="complete",
        replaced_by_id=None,
        replaced_by_telegram_id=None,
        created_at=datetime.now(UTC),
    )
    delivery = FakeDelivery()
    repository = FakeRepository([current])
    meme_service = service(
        FakeLemmyClient({"TopWeek": [post("new-post")]}),
        repository=repository,
        delivery=delivery,
    )

    result = await meme_service.publish_replacement(
        replacer_telegram_id=777,
        now=datetime(2026, 10, 21, 16, 16, tzinfo=UTC),
    )

    assert result.status is MemePublicationStatus.PUBLISHED
    assert delivery.deleted_ids == [11, 12, 13]
    assert repository.records[0].status == "replaced"
    assert repository.records[0].replaced_by_id == repository.records[1].id
    assert repository.records[0].replaced_by_telegram_id == 777
    assert repository.records[1].status == "published"


@pytest.mark.asyncio
async def test_failed_replacement_candidate_is_rolled_back_before_trying_the_next() -> None:
    week = date(2026, 10, 19)
    current = MemePublicationDTO(
        id=1,
        publication_week=week,
        post_id="old-post",
        image_urls=("https://cdn.example.test/old.jpg",),
        community="programmer_humor@programming.dev",
        score=40,
        telegram_message_ids=(11, 12),
        status="published",
        delivery_state="complete",
        replaced_by_id=None,
        replaced_by_telegram_id=None,
        created_at=datetime.now(UTC),
    )
    broken_url = "https://cdn.example.test/broken.jpg"
    delivery = FakeDelivery(fail_urls={broken_url})
    repository = FakeRepository([current])
    client = FakeLemmyClient({"TopWeek": [post("broken", url=broken_url, score=60), post("valid", score=50)]})
    meme_service = service(client, repository=repository, delivery=delivery)

    result = await meme_service.publish_replacement(
        replacer_telegram_id=777,
        now=datetime(2026, 10, 21, 16, 16, tzinfo=UTC),
    )

    assert result.status is MemePublicationStatus.PUBLISHED
    assert delivery.deleted_ids == [77, 11, 12]
    assert [record.status for record in repository.records] == ["replaced", "published", "published"]
    assert [record.delivery_state for record in repository.records] == ["complete", "failed", "complete"]
    assert repository.records[0].replaced_by_id == repository.records[2].id


@pytest.mark.asyncio
async def test_replacement_limit_is_checked_before_candidate_fetch() -> None:
    week = date(2026, 10, 19)
    current = MemePublicationDTO(
        id=1,
        publication_week=week,
        post_id="old-post",
        image_urls=("https://cdn.example.test/old.jpg",),
        community="programmer_humor@programming.dev",
        score=40,
        telegram_message_ids=(11,),
        status="published",
        delivery_state="complete",
        replaced_by_id=None,
        replaced_by_telegram_id=None,
        created_at=datetime.now(UTC),
    )
    client = FakeLemmyClient({"TopWeek": [post("new-post")]})
    meme_service = service(
        client,
        repository=FakeRepository([current]),
        max_replacements_per_week=0,
    )

    result = await meme_service.publish_replacement(
        replacer_telegram_id=777,
        now=datetime(2026, 10, 21, 16, 16, tzinfo=UTC),
    )

    assert result.status is MemePublicationStatus.REPLACEMENT_LIMIT
    assert client.post_calls == []
