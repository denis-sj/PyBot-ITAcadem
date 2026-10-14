"""Private notification preference controls and check-in pause commands."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka import FromDishka

from ....core import logger
from ....domain.services.community_checkins import NotificationPreferenceKey
from ....dto.community_checkins import UserNotificationPreferencesDTO
from ....presentation.texts.community_checkins import (
    CHECKIN_ALREADY_PAUSED,
    CHECKIN_ALREADY_RESUMED,
    CHECKIN_PAUSED,
    CHECKIN_RESUMED,
    NOTIFICATION_SETTING_LABELS,
    render_notification_preferences,
)
from ....services.user_notification_preferences import UserNotificationPreferencesService

notification_preferences_router = Router(name="notification_preferences")


class NotificationPreferenceCB(CallbackData, prefix="notify"):
    """Address one notification preference toggle to its Telegram owner."""

    setting: str
    owner_telegram_id: int


@notification_preferences_router.message(Command("notifications"), F.chat.type == "private")
async def cmd_notifications(
    message: Message,
    service: FromDishka[UserNotificationPreferencesService],
    user_id: int | None = None,
) -> None:
    """Show every community notification switch with inline toggle buttons."""
    if user_id is None or message.from_user is None:
        return
    preferences = await service.get_preferences(user_id)
    await message.answer(
        render_notification_preferences(preferences),
        reply_markup=_build_keyboard(preferences, message.from_user.id),
    )


@notification_preferences_router.message(Command("pause_checkin"), F.chat.type == "private")
async def cmd_pause_checkin(
    message: Message,
    service: FromDishka[UserNotificationPreferencesService],
    user_id: int | None = None,
) -> None:
    """Turn off check-ins while keeping other notification preferences unchanged."""
    if user_id is None:
        return
    changed = await service.set_checkins_enabled(user_id, False)
    await message.answer(CHECKIN_PAUSED if changed else CHECKIN_ALREADY_PAUSED)


@notification_preferences_router.message(Command("resume_checkin"), F.chat.type == "private")
async def cmd_resume_checkin(
    message: Message,
    service: FromDishka[UserNotificationPreferencesService],
    user_id: int | None = None,
) -> None:
    """Turn check-ins on without changing other notification preferences."""
    if user_id is None:
        return
    changed = await service.set_checkins_enabled(user_id, True)
    await message.answer(CHECKIN_RESUMED if changed else CHECKIN_ALREADY_RESUMED)


@notification_preferences_router.callback_query(NotificationPreferenceCB.filter())
async def cb_toggle_notification(
    callback: CallbackQuery,
    callback_data: NotificationPreferenceCB,
    service: FromDishka[UserNotificationPreferencesService],
    user_id: int | None = None,
) -> None:
    """Toggle only the private message author's preference and refresh the card."""
    if callback_data.owner_telegram_id != callback.from_user.id or user_id is None:
        await callback.answer("Эти настройки открыты другим участником.", show_alert=True)
        return
    try:
        setting = NotificationPreferenceKey(callback_data.setting)
    except ValueError:
        logger.warning(
            "event=notification_preference_callback status=rejected setting={setting}", setting=callback_data.setting
        )
        await callback.answer("Настройка не найдена.", show_alert=True)
        return

    preferences = await service.toggle(user_id, setting)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_notification_preferences(preferences),
            reply_markup=_build_keyboard(preferences, callback.from_user.id),
        )
    await callback.answer(f"{NOTIFICATION_SETTING_LABELS[setting.value]} обновлены.")


def _build_keyboard(
    preferences: UserNotificationPreferencesDTO,
    owner_telegram_id: int,
) -> InlineKeyboardMarkup:
    """Create one owner-bound button per stored preference."""
    values = (
        (NotificationPreferenceKey.CHECKINS, preferences.checkins_enabled),
        (NotificationPreferenceKey.MONTHLY_SUMMARY, preferences.monthly_summary_enabled),
        (NotificationPreferenceKey.ACHIEVEMENTS, preferences.achievement_notifications_enabled),
        (NotificationPreferenceKey.STREAK_WARNINGS, preferences.streak_warnings_enabled),
        (NotificationPreferenceKey.MEETING_INVITATIONS, preferences.meeting_invitations_enabled),
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✅' if enabled else '◻️'} {NOTIFICATION_SETTING_LABELS[setting.value]}",
                    callback_data=NotificationPreferenceCB(
                        setting=setting.value,
                        owner_telegram_id=owner_telegram_id,
                    ).pack(),
                )
            ]
            for setting, enabled in values
        ]
    )
