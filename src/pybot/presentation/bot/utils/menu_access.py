"""Access rules for main menu buttons."""

from collections.abc import Collection

from ....core.config import AppSettings
from ....utils import has_any_role


def can_use_broadcast_menu(user_roles: Collection[str] | None, settings: AppSettings) -> bool:
    """Tell whether the broadcast button should be shown to a user with the given roles."""
    if not user_roles or not settings.broadcast_allowed_roles:
        return False
    return has_any_role(set(user_roles), settings.broadcast_allowed_roles)
