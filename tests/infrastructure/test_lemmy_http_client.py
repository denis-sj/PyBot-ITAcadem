from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from typing import Any, cast

import aiohttp
import pytest

from pybot.infrastructure.adapters.lemmy_http_client import (
    LemmyClientError,
    LemmyHttpClient,
    PublicInternetResolver,
    UnsafeLemmyUrlError,
    _validate_https_url,
)


class FakeContent:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, chunk_size: int) -> AsyncIterator[bytes]:
        del chunk_size
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, *, status: int, payload: dict[str, Any] | None = None, chunks: tuple[bytes, ...] = ()) -> None:
        self.status = status
        self.content_length = sum(map(len, chunks)) if chunks else None
        self.content = FakeContent(chunks)
        self._payload = payload or {}

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self, *, content_type: str | None = None) -> dict[str, Any]:
        del content_type
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_lemmy_v3_client_falls_back_to_next_instance_and_maps_post_fields() -> None:
    row = {
        "post": {
            "id": 7,
            "ap_id": "https://programming.dev/post/7",
            "name": "Meme title",
            "url": "https://cdn.example.test/meme.png",
            "url_content_type": "image/png",
            "body": "![also](https://cdn.example.test/also.gif)",
            "nsfw": False,
            "removed": False,
            "deleted": False,
        },
        "counts": {"score": 41},
    }
    session = FakeSession([FakeResponse(status=503), FakeResponse(status=200, payload={"posts": [row]})])
    client = LemmyHttpClient(
        instances=("https://primary.example.test", "https://backup.example.test"),
        session=cast(aiohttp.ClientSession, session),
    )

    posts = await client.find_posts("programmer_humor@programming.dev", "TopWeek")

    assert session.urls == [
        "https://primary.example.test/api/v3/post/list",
        "https://backup.example.test/api/v3/post/list",
    ]
    assert posts[0].post_id == "https://programming.dev/post/7"
    assert posts[0].title == "Meme title"
    assert posts[0].score == 41
    assert posts[0].url_content_type == "image/png"
    assert posts[0].body == "![also](https://cdn.example.test/also.gif)"


@pytest.mark.asyncio
async def test_redirect_and_oversized_image_are_rejected() -> None:
    redirect_client = LemmyHttpClient(
        instances=("https://lemmy.example.test",),
        session=cast(aiohttp.ClientSession, FakeSession([FakeResponse(status=302)])),
    )
    oversized_client = LemmyHttpClient(
        instances=("https://lemmy.example.test",),
        session=cast(
            aiohttp.ClientSession,
            FakeSession([FakeResponse(status=200, chunks=(b"x" * (10 * 1024 * 1024 + 1),))]),
        ),
    )

    with pytest.raises(LemmyClientError):
        await redirect_client.download_image("https://cdn.example.test/meme.png")
    with pytest.raises(LemmyClientError):
        await oversized_client.download_image("https://cdn.example.test/meme.png")


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.example.test/meme.jpg",
        "https://user:password@cdn.example.test/meme.jpg",
        "https://cdn.example.test:8443/meme.jpg",
        "https://127.0.0.1/meme.jpg",
    ],
)
def test_unsafe_image_url_schemes_and_authorities_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeLemmyUrlError):
        _validate_https_url(url)


@pytest.mark.asyncio
async def test_public_resolver_rejects_private_dns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLoop:
        async def getaddrinfo(self, *_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

    with pytest.raises(UnsafeLemmyUrlError):
        await PublicInternetResolver().resolve("rebind.example.test", 443, socket.AF_UNSPEC)
