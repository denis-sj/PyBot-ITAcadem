"""HTTP adapter for public Lemmy APIs with bounded downloads and SSRF controls."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Sequence
from typing import TypeGuard
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import ResolveResult

from ...dto.meme_of_the_week import LemmyPostDTO, MemeImageDTO
from ...services.ports.lemmy_client import MAX_IMAGE_SIZE_BYTES, LemmyClientError, LemmyClientPort

LEMMY_POST_LIMIT = 50
REQUEST_TIMEOUT_SECONDS = 20
HTTP_OK = 200
WEBP_HEADER_LENGTH = 12
_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)


class UnsafeLemmyUrlError(LemmyClientError):
    """The URL is not an allowed HTTPS public-internet destination."""


class PublicInternetResolver(AbstractResolver):
    """Resolve names once per connection and reject every non-public address."""

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[ResolveResult]:
        if host.endswith((".localhost", ".local", ".internal")) or host in {"localhost", "metadata.google.internal"}:
            raise UnsafeLemmyUrlError("Non-public host is not allowed")
        try:
            address_info = await asyncio.get_running_loop().getaddrinfo(
                host,
                port,
                family=family,
                type=socket.SOCK_STREAM,
            )
        except OSError as error:
            raise LemmyClientError("Unable to resolve public Lemmy host") from error
        if not address_info:
            raise LemmyClientError("Unable to resolve public Lemmy host")

        resolved: list[ResolveResult] = []
        for address_family, _socket_type, protocol, _canonical_name, address in address_info:
            ip_address = ipaddress.ip_address(address[0])
            if not ip_address.is_global:
                raise UnsafeLemmyUrlError("Non-public IP addresses are not allowed")
            resolved.append(
                {
                    "hostname": host,
                    "host": ip_address.compressed,
                    "port": port,
                    "family": address_family,
                    "proto": protocol,
                    "flags": 0,
                }
            )
        return resolved

    async def close(self) -> None:
        """This resolver does not keep resources open."""


class LemmyHttpClient(LemmyClientPort):
    """Call Lemmy v3 and download original image files from public HTTPS hosts."""

    def __init__(self, *, instances: Sequence[str], session: aiohttp.ClientSession) -> None:
        self._instances = tuple(instances)
        self._session = session

    async def find_posts(self, community: str, sort: str) -> Sequence[LemmyPostDTO]:
        """Fetch up to Lemmy's documented page limit, using configured instance failover."""
        if sort not in {"TopWeek", "TopMonth"}:
            raise ValueError("Unsupported Lemmy sort period")
        last_error: Exception | None = None
        for instance in self._instances:
            try:
                return await self._fetch_posts_from_instance(instance, community, sort)
            except (TimeoutError, aiohttp.ClientError, LemmyClientError, ValueError) as error:
                last_error = error
        raise LemmyClientError("All configured Lemmy instances failed") from last_error

    async def _fetch_posts_from_instance(
        self,
        instance: str,
        community: str,
        sort: str,
    ) -> Sequence[LemmyPostDTO]:
        endpoint = f"{instance}/api/v3/post/list"
        async with self._session.get(
            endpoint,
            params={"community_name": community, "sort": sort, "limit": LEMMY_POST_LIMIT},
            allow_redirects=False,
        ) as response:
            if response.status != HTTP_OK:
                raise LemmyClientError("Lemmy returned an unsuccessful status")
            payload = await response.json(content_type=None)
        if not _is_json_object(payload):
            raise LemmyClientError("Lemmy response did not contain a post list")
        rows = payload.get("posts")
        if not isinstance(rows, list):
            raise LemmyClientError("Lemmy response did not contain a post list")
        return tuple(post for row in rows if (post := self._parse_post(row, community)) is not None)

    async def download_image(self, url: str) -> MemeImageDTO:
        """Download a single image under a strict byte cap; redirects are always rejected."""
        _validate_https_url(url)
        try:
            async with self._session.get(url, allow_redirects=False) as response:
                if response.status != HTTP_OK:
                    raise LemmyClientError("Image endpoint returned an unsuccessful status")
                if response.content_length is not None and response.content_length > MAX_IMAGE_SIZE_BYTES:
                    raise LemmyClientError("Image exceeds the per-file size limit")
                content = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_SIZE_BYTES:
                        raise LemmyClientError("Image exceeds the per-file size limit")
        except (TimeoutError, aiohttp.ClientError) as error:
            raise LemmyClientError("Image download failed") from error

        signature = _image_signature(bytes(content))
        if signature is None:
            raise LemmyClientError("Image content has an unsupported signature")
        content_type, extension = signature
        return MemeImageDTO(source_url=url, content=bytes(content), content_type=content_type, extension=extension)

    @staticmethod
    def _parse_post(row: object, community: str) -> LemmyPostDTO | None:
        if not _is_json_object(row):
            return None
        post = row.get("post")
        counts = row.get("counts")
        if not _is_json_object(post) or not _is_json_object(counts):
            return None
        post_id = post.get("ap_id") or post.get("id")
        if post_id is None:
            return None
        score_value = counts.get("score", 0)
        if not isinstance(score_value, (int, str)) or isinstance(score_value, bool):
            return None
        try:
            score = int(score_value)
        except ValueError:
            return None
        url = post.get("url")
        if not isinstance(url, str):
            url = None
        url_content_type = post.get("url_content_type")
        body = post.get("body")
        if not isinstance(body, str):
            body = None
        return LemmyPostDTO(
            post_id=str(post_id),
            community=community,
            title=str(post.get("name") or ""),
            score=score,
            url=url,
            url_content_type=url_content_type if isinstance(url_content_type, str) else None,
            body=body,
            nsfw=bool(post.get("nsfw")),
            removed=bool(post.get("removed")),
            deleted=bool(post.get("deleted")),
        )


def create_lemmy_session() -> aiohttp.ClientSession:
    """Create an HTTP session whose resolver pins validated public IPs per connection."""
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS, connect=5, sock_read=10)
    connector = aiohttp.TCPConnector(resolver=PublicInternetResolver(), use_dns_cache=False)
    return aiohttp.ClientSession(timeout=timeout, connector=connector, trust_env=False)


def _validate_https_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise UnsafeLemmyUrlError("Malformed URL is not allowed") from error
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise UnsafeLemmyUrlError("Only public HTTPS URLs on port 443 are allowed")
    try:
        ip_address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        ip_address = None
    if ip_address is not None and not ip_address.is_global:
        raise UnsafeLemmyUrlError("Non-public IP addresses are not allowed")


def _image_signature(content: bytes) -> tuple[str, str] | None:
    for signature, content_type, extension in _IMAGE_SIGNATURES:
        if content.startswith(signature):
            return content_type, extension
    if len(content) >= WEBP_HEADER_LENGTH and content.startswith(b"RIFF") and content[8:WEBP_HEADER_LENGTH] == b"WEBP":
        return "image/webp", ".webp"
    return None


def _is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)
