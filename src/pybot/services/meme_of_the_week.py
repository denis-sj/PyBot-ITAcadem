"""Select, assemble and publish one safe Lemmy meme for a weekly Telegram slot."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from html import unescape
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from ..core import logger
from ..core.config import AppSettings
from ..dto.meme_of_the_week import LemmyPostDTO, MemeImageDTO, MemePublicationDTO
from ..infrastructure.weekly_meme_publication_repository import WeeklyMemePublicationRepository
from .ports import LemmyClientError, LemmyClientPort, MemeDeliveryError, MemeDeliveryPort
from .ports.lemmy_client import MAX_IMAGE_SIZE_BYTES

YEKATERINBURG_TZ = ZoneInfo("Asia/Yekaterinburg")
MAX_MEME_IMAGES = 10
MAX_TOTAL_IMAGE_BYTES = 40 * 1024 * 1024
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_MESSAGE_LIMIT = 4096
_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(\s*<?(?P<url>https?://[^\s)>]+)>?(?:\s+[^)]*)?\)", re.IGNORECASE)
_HTML_IMAGE_PATTERN = re.compile(r"<img\b[^>]*?src=[\"'](?P<url>https?://[^\"']+)[\"'][^>]*>", re.IGNORECASE)
_HTML_IMAGE_TAG_PATTERN = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_MARKDOWN_LINK_PATTERN = re.compile(r"(?<!!)\[([^\]]+)\]\(\s*<?([^\s)>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_MARKDOWN_IMAGE_ANY_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MARKDOWN_IMAGE_REF_PATTERN = re.compile(r"!\[[^\]]*\]\[[^\]]*\]")
_IMAGE_LINK_PATH_PATTERN = re.compile(r"\.(?:jpe?g|png|webp|gif)$", re.IGNORECASE)


class MemePublicationStatus(StrEnum):
    """User-visible result of a weekly, replacement or test publication attempt."""

    PUBLISHED = "published"
    ALREADY_PUBLISHED = "already_published"
    NO_CANDIDATES = "no_candidates"
    NO_CURRENT_MEME = "no_current_meme"
    REPLACEMENT_LIMIT = "replacement_limit"
    BUSY = "busy"
    DISABLED = "disabled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MemePublicationResult:
    """Small status object for presentation and TaskIQ callers."""

    status: MemePublicationStatus


@dataclass(frozen=True, slots=True)
class MemeCandidate:
    """A fully validated post with ordered image bytes and plain-text captions."""

    post: LemmyPostDTO
    image_urls: tuple[str, ...]
    images: tuple[MemeImageDTO, ...]
    caption: str
    continuation: str | None


class MemeOfTheWeekService:
    """Own candidate filtering, media assembly, weekly idempotence and admin replacements."""

    def __init__(
        self,
        db: AsyncSession,
        repository: WeeklyMemePublicationRepository,
        lemmy_client: LemmyClientPort,
        delivery: MemeDeliveryPort,
        settings: AppSettings,
    ) -> None:
        self._db = db
        self._repository = repository
        self._lemmy_client = lemmy_client
        self._delivery = delivery
        self._settings = settings

    async def publish_weekly(self, now: datetime | None = None) -> MemePublicationResult:
        """Publish at most one meme for the current Yekaterinburg week."""
        if not self._settings.meme_publication_enabled:
            return MemePublicationResult(MemePublicationStatus.DISABLED)
        publication_week = self._publication_week(now)
        await self._repository.acquire_week_lock(self._db, publication_week)
        records = await self._repository.find_week_records(self._db, publication_week)
        if any(record.status == "published" and record.delivery_state == "pending" for record in records):
            return MemePublicationResult(MemePublicationStatus.BUSY)
        if any(record.status == "published" and record.delivery_state == "complete" for record in records):
            return MemePublicationResult(MemePublicationStatus.ALREADY_PUBLISHED)
        return await self._publish_next_candidate(publication_week, test=False)

    async def publish_test(self, now: datetime | None = None) -> MemePublicationResult:
        """Publish one test meme without reserving the weekly publication slot."""
        if not self._settings.meme_publication_enabled:
            return MemePublicationResult(MemePublicationStatus.DISABLED)
        return await self._publish_next_candidate(self._publication_week(now), test=True)

    async def publish_replacement(
        self,
        *,
        replacer_telegram_id: int,
        now: datetime | None = None,
    ) -> MemePublicationResult:
        """Publish a replacement before deleting all messages from the current weekly meme."""
        if not self._settings.meme_publication_enabled:
            return MemePublicationResult(MemePublicationStatus.DISABLED)
        publication_week = self._publication_week(now)
        await self._repository.acquire_week_lock(self._db, publication_week)
        target = await self._find_replacement_target(publication_week)
        if isinstance(target, MemePublicationResult):
            return target
        current = target

        attempted_post_ids: set[str] = set()
        while True:
            seen_posts, seen_urls = await self._find_seen_identities()
            candidate = await self._find_candidate(seen_posts, seen_urls, excluded_post_ids=attempted_post_ids)
            if candidate is None:
                logger.info(
                    "event=weekly_meme status=no_replacement_candidate week={publication_week}",
                    publication_week=publication_week,
                )
                return MemePublicationResult(
                    MemePublicationStatus.FAILED if attempted_post_ids else MemePublicationStatus.NO_CANDIDATES
                )

            pending = await self._repository.create_publication(
                self._db,
                publication_week=publication_week,
                post=candidate.post,
                image_urls=candidate.image_urls,
                status="published",
            )
            sent_message_ids = await self._deliver_candidate(candidate, chat_id=self._chat_id, topic_id=self._topic_id)
            if sent_message_ids is None:
                attempted_post_ids.add(candidate.post.post_id)
                await self._repository.mark_failed(self._db, pending.id)
                await self._repository.acquire_week_lock(self._db, publication_week)
                records = await self._repository.find_week_records(self._db, publication_week)
                updated_current = await self._repository.find_current_publication(self._db, publication_week)
                if (
                    updated_current is None
                    or updated_current.id != current.id
                    or any(record.status == "published" and record.delivery_state == "pending" for record in records)
                ):
                    return MemePublicationResult(MemePublicationStatus.BUSY)
                continue

            deleted_all = await self._delete_messages(current.telegram_message_ids)
            if not deleted_all:
                await self._delete_messages(sent_message_ids)
                await self._repository.mark_failed(self._db, pending.id)
                return MemePublicationResult(MemePublicationStatus.FAILED)

            await self._repository.complete_replacement(
                self._db,
                old_publication_id=current.id,
                new_publication_id=pending.id,
                replacer_telegram_id=replacer_telegram_id,
                telegram_message_ids=sent_message_ids,
            )
            self._log_candidate(candidate, "replaced")
            return MemePublicationResult(MemePublicationStatus.PUBLISHED)

    async def _publish_next_candidate(self, publication_week: date, *, test: bool) -> MemePublicationResult:
        attempted_post_ids: set[str] = set()
        while True:
            seen_posts, seen_urls = await self._find_seen_identities()
            candidate = await self._find_candidate(seen_posts, seen_urls, excluded_post_ids=attempted_post_ids)
            if candidate is None:
                logger.info(
                    "event=weekly_meme status=no_candidate week={publication_week} test={test}",
                    publication_week=publication_week,
                    test=str(test).lower(),
                )
                return MemePublicationResult(
                    MemePublicationStatus.FAILED if attempted_post_ids else MemePublicationStatus.NO_CANDIDATES
                )

            pending = await self._repository.create_publication(
                self._db,
                publication_week=publication_week,
                post=candidate.post,
                image_urls=candidate.image_urls,
                status="test" if test else "published",
            )
            sent_message_ids = await self._deliver_candidate(
                candidate,
                chat_id=self._chat_id,
                topic_id=self._topic_id,
            )
            if sent_message_ids is None:
                attempted_post_ids.add(candidate.post.post_id)
                await self._repository.mark_failed(self._db, pending.id)
                if test:
                    return MemePublicationResult(MemePublicationStatus.FAILED)
                await self._repository.acquire_week_lock(self._db, publication_week)
                records = await self._repository.find_week_records(self._db, publication_week)
                if any(record.status == "published" and record.delivery_state == "pending" for record in records):
                    return MemePublicationResult(MemePublicationStatus.BUSY)
                continue

            await self._repository.mark_published(
                self._db,
                publication_id=pending.id,
                telegram_message_ids=sent_message_ids,
                status="test" if test else "published",
            )
            self._log_candidate(candidate, "test" if test else "published")
            return MemePublicationResult(MemePublicationStatus.PUBLISHED)

    async def _find_candidate(
        self,
        seen_post_ids: set[str],
        seen_image_urls: set[str],
        *,
        excluded_post_ids: set[str] | None = None,
    ) -> MemeCandidate | None:
        excluded_ids = excluded_post_ids or set()
        for sort in ("TopWeek", "TopMonth"):
            posts: list[LemmyPostDTO] = []
            for community in self._settings.meme_communities:
                try:
                    posts.extend(await self._lemmy_client.find_posts(community, sort))
                except LemmyClientError as error:
                    logger.warning(
                        "event=weekly_meme_lemmy status=unavailable community={community} "
                        "sort={sort} error_type={error_type}",
                        community=community,
                        sort=sort,
                        error_type=type(error).__name__,
                    )
            for post in sorted(posts, key=lambda item: item.score, reverse=True):
                if (
                    post.nsfw
                    or post.removed
                    or post.deleted
                    or post.score < self._settings.meme_min_score
                    or post.post_id in seen_post_ids
                    or post.post_id in excluded_ids
                ):
                    self._log_candidate_status(post, "filtered")
                    continue
                image_urls = self._collect_image_urls(post)
                if not image_urls or len(image_urls) > MAX_MEME_IMAGES or seen_image_urls.intersection(image_urls):
                    self._log_candidate_status(post, "images_filtered")
                    continue
                images = await self._download_candidate_images(image_urls)
                if images is None:
                    self._log_candidate_status(post, "download_failed")
                    continue
                caption, continuation = build_meme_caption(post.title, post.body)
                return MemeCandidate(post, image_urls, images, caption, continuation)
            if sort == "TopWeek":
                continue
        return None

    async def _find_replacement_target(
        self,
        publication_week: date,
    ) -> MemePublicationDTO | MemePublicationResult:
        records = await self._repository.find_week_records(self._db, publication_week)
        has_pending_delivery = any(
            record.status == "published" and record.delivery_state == "pending" for record in records
        )
        if has_pending_delivery:
            return MemePublicationResult(MemePublicationStatus.BUSY)
        current = await self._repository.find_current_publication(self._db, publication_week)
        if current is None:
            return MemePublicationResult(MemePublicationStatus.NO_CURRENT_MEME)
        replacements = await self._repository.count_replacements(self._db, publication_week)
        if replacements >= self._settings.meme_max_replacements_per_week:
            return MemePublicationResult(MemePublicationStatus.REPLACEMENT_LIMIT)
        return current

    async def _download_candidate_images(self, image_urls: tuple[str, ...]) -> tuple[MemeImageDTO, ...] | None:
        images: list[MemeImageDTO] = []
        total_size = 0
        try:
            for url in image_urls:
                image = await self._lemmy_client.download_image(url)
                if len(image.content) > MAX_IMAGE_SIZE_BYTES:
                    return None
                total_size += len(image.content)
                if total_size > MAX_TOTAL_IMAGE_BYTES:
                    return None
                images.append(image)
        except (LemmyClientError, ValueError):
            return None
        return tuple(images)

    async def _deliver_candidate(
        self, candidate: MemeCandidate, *, chat_id: int, topic_id: int
    ) -> tuple[int, ...] | None:
        sent_message_ids: list[int] = []
        try:
            sent_message_ids.extend(
                await self._delivery.send_images(
                    chat_id=chat_id,
                    topic_id=topic_id,
                    images=candidate.images,
                    caption=candidate.caption,
                )
            )
            for text_part in _split_message(candidate.continuation):
                sent_message_ids.append(
                    await self._delivery.send_text(chat_id=chat_id, topic_id=topic_id, text=text_part)
                )
        except MemeDeliveryError as error:
            sent_message_ids.extend(error.sent_message_ids)
            await self._delete_messages(tuple(dict.fromkeys(sent_message_ids)))
            logger.warning(
                "event=weekly_meme_telegram status=failed post_id={post_id} error_type={error_type}",
                post_id=candidate.post.post_id,
                error_type=type(error).__name__,
            )
            return None
        except Exception as error:
            await self._delete_messages(tuple(dict.fromkeys(sent_message_ids)))
            logger.warning(
                "event=weekly_meme_telegram status=failed post_id={post_id} error_type={error_type}",
                post_id=candidate.post.post_id,
                error_type=type(error).__name__,
            )
            return None
        return tuple(sent_message_ids)

    async def _delete_messages(self, message_ids: Sequence[int]) -> bool:
        succeeded = True
        for message_id in message_ids:
            try:
                await self._delivery.delete_message(chat_id=self._chat_id, message_id=message_id)
            except Exception as error:
                succeeded = False
                logger.warning(
                    "event=weekly_meme_delete status=failed error_type={error_type}",
                    error_type=type(error).__name__,
                )
        return succeeded

    async def _find_seen_identities(self) -> tuple[set[str], set[str]]:
        records = await self._repository.find_seen_memes(self._db)
        post_ids = {post_id for post_id, _image_urls in records}
        image_urls = {image_url for _post_id, urls in records for image_url in urls}
        return post_ids, image_urls

    @property
    def _chat_id(self) -> int:
        if self._settings.meme_chat_id is None:
            raise RuntimeError("Meme destination is not configured")
        return self._settings.meme_chat_id

    @property
    def _topic_id(self) -> int:
        if self._settings.meme_topic_id is None:
            raise RuntimeError("Meme destination is not configured")
        return self._settings.meme_topic_id

    @staticmethod
    def _publication_week(now: datetime | None) -> date:
        local_now = (now or datetime.now(UTC)).astimezone(YEKATERINBURG_TZ)
        return local_now.date() - timedelta(days=local_now.weekday())

    @staticmethod
    def _collect_image_urls(post: LemmyPostDTO) -> tuple[str, ...]:
        image_urls: list[str] = []
        if post.url:
            if not _is_image_url(post.url, post.url_content_type):
                return ()
            image_urls.append(post.url)
        image_urls.extend(_extract_markdown_image_urls(post.body or ""))
        ordered_unique = tuple(dict.fromkeys(image_urls))
        try:
            if any(urlsplit(url).scheme.lower() != "https" for url in ordered_unique):
                return ()
        except ValueError:
            return ()
        return ordered_unique

    @staticmethod
    def _log_candidate(candidate: MemeCandidate, status: str) -> None:
        logger.info(
            "event=weekly_meme status={status} post_id={post_id} score={score}",
            status=status,
            post_id=candidate.post.post_id,
            score=candidate.post.score,
        )

    @staticmethod
    def _log_candidate_status(post: LemmyPostDTO, status: str) -> None:
        logger.info(
            "event=weekly_meme_candidate status={status} post_id={post_id} score={score}",
            status=status,
            post_id=post.post_id,
            score=post.score,
        )


def _extract_markdown_image_urls(body: str) -> tuple[str, ...]:
    matches = (*_MARKDOWN_IMAGE_PATTERN.finditer(body), *_HTML_IMAGE_PATTERN.finditer(body))
    return tuple(match.group("url") for match in sorted(matches, key=lambda match: match.start()))


def _is_image_url(url: str, content_type: str | None = None) -> bool:
    if content_type and content_type.split(";", maxsplit=1)[0].strip().lower() in {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }:
        return True
    parsed = urlsplit(url)
    if _IMAGE_LINK_PATH_PATTERN.search(parsed.path):
        return True
    nested_url = parse_qs(parsed.query).get("url", [""])[0]
    if nested_url:
        nested_path = urlsplit(unquote(nested_url)).path
        return any(nested_path.lower().endswith(extension) for extension in _SUPPORTED_EXTENSIONS)
    return False


def build_meme_caption(title: str, body: str | None) -> tuple[str, str | None]:
    """Build plain text caption and optional body continuation after removing Markdown images."""
    plain_title = strip_markdown_text(title).strip()
    plain_body = strip_markdown_text(body or "").strip()
    header = "😂 Мем недели" + (f"\n{plain_title}" if plain_title else "")
    complete_caption = f"{header}\n{plain_body}" if plain_body else header
    if len(complete_caption) <= TELEGRAM_CAPTION_LIMIT:
        return complete_caption, None
    caption = header[:TELEGRAM_CAPTION_LIMIT]
    continuation_parts = [plain_title[max(0, TELEGRAM_CAPTION_LIMIT - len("😂 Мем недели\n")) :]]
    if plain_body:
        continuation_parts.append(plain_body)
    continuation = "\n".join(part for part in continuation_parts if part)
    return caption, continuation or None


def strip_markdown_text(text: str) -> str:
    """Keep readable content, links and line structure while removing markup and image embeds."""
    normalized = unescape(text.replace("\r\n", "\n").replace("\r", "\n"))
    normalized = _HTML_IMAGE_TAG_PATTERN.sub("", normalized)
    normalized = _MARKDOWN_IMAGE_ANY_PATTERN.sub("", normalized)
    normalized = _MARKDOWN_IMAGE_REF_PATTERN.sub("", normalized)
    normalized = re.sub(r"```[^\n]*\n?", "", normalized)
    normalized = re.sub(r":::\s*spoiler[^\n]*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"^\s*:::\s*$", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"^\s{0,3}>\s?", "", normalized, flags=re.MULTILINE)
    normalized = _MARKDOWN_LINK_PATTERN.sub(r"\1 (\2)", normalized)
    normalized = re.sub(r"^\s{0,3}#{1,6}\s+", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"(?<!\w)(?:\*\*|__|~~|\*|_)(?=\S)|(?<=\S)(?:\*\*|__|~~|\*|_)(?!\w)", "", normalized)
    normalized = re.sub(r"`{1,3}", "", normalized)
    return normalized.strip()


def _split_message(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    parts: list[str] = []
    remaining = text
    while len(remaining) > TELEGRAM_MESSAGE_LIMIT:
        split_at = remaining.rfind("\n", 0, TELEGRAM_MESSAGE_LIMIT + 1)
        if split_at <= 0:
            split_at = TELEGRAM_MESSAGE_LIMIT
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return tuple(parts)
