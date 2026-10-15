from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from aiogram.client.session.aiohttp import AiohttpSession


def create_telegram_proxy_session(proxy_url: str) -> AiohttpSession:
    """Create a Telegram HTTP session, enabling remote DNS for ``socks5h`` URLs."""
    parsed_proxy_url = urlsplit(proxy_url)
    if parsed_proxy_url.scheme.lower() != "socks5h":
        return AiohttpSession(proxy=proxy_url)

    normalized_proxy_url = urlunsplit(parsed_proxy_url._replace(scheme="socks5"))
    session = AiohttpSession(proxy=normalized_proxy_url)
    # aiogram exposes no rdns argument; this is passed to aiohttp-socks ProxyConnector.
    session._connector_init["rdns"] = True
    return session
