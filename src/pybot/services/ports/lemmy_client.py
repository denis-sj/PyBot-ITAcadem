"""Outbound contract for reading public Lemmy posts and downloading verified image bytes."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ...dto.meme_of_the_week import LemmyPostDTO, MemeImageDTO

MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024


class LemmyClientError(Exception):
    """A public Lemmy request or image download failed safely."""


class LemmyClientPort(ABC):
    """Read candidate posts and images without exposing HTTP implementation details."""

    @abstractmethod
    async def find_posts(self, community: str, sort: str) -> Sequence[LemmyPostDTO]:
        """Return one bounded Lemmy post page sorted by the requested period."""

    @abstractmethod
    async def download_image(self, url: str) -> MemeImageDTO:
        """Download an HTTPS image only after validating its public destination and bytes."""
