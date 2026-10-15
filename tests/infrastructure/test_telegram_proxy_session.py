from aiogram.client.session.aiohttp import AiohttpSession

from pybot.infrastructure.telegram_proxy_session import create_telegram_proxy_session


def test_socks5h_is_normalized_and_uses_remote_dns() -> None:
    session = create_telegram_proxy_session("socks5h://proxy-user:proxy-password@proxy.example:1080")

    assert isinstance(session, AiohttpSession)
    assert session._proxy == "socks5://proxy-user:proxy-password@proxy.example:1080"
    assert session._connector_init["rdns"] is True
