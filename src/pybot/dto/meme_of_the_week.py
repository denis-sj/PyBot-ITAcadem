"""Typed data exchanged by the weekly meme service and its adapters."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class LemmyPostDTO:
    """The subset of Lemmy post data used for candidate filtering and publication."""

    post_id: str
    community: str
    title: str
    score: int
    url: str | None
    url_content_type: str | None
    body: str | None
    nsfw: bool
    removed: bool
    deleted: bool


@dataclass(frozen=True, slots=True)
class MemeImageDTO:
    """A downloaded image that has passed the byte-signature and size checks."""

    source_url: str
    content: bytes
    content_type: str
    extension: str

    @property
    def is_animation(self) -> bool:
        return self.content_type == "image/gif"


@dataclass(frozen=True, slots=True)
class MemePublicationDTO:
    """Persisted publication state returned by the meme repository."""

    id: int
    publication_week: date
    post_id: str
    image_urls: tuple[str, ...]
    community: str
    score: int
    telegram_message_ids: tuple[int, ...]
    status: str
    delivery_state: str
    replaced_by_id: int | None
    replaced_by_telegram_id: int | None
    created_at: datetime
