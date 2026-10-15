"""Модуль бота IT Academ."""

from collections.abc import Sequence

from aiogram.types import CallbackQuery, Contact, Message, ReplyKeyboardRemove
from aiogram_dialog import DialogManager
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Button, ManagedMultiselect
from dishka.integrations.aiogram import FromDishka
from dishka.integrations.aiogram_dialog import inject

from .....core import logger
from .....domain.exceptions import NameInputValidationError
from .....domain.services.technology_stack import TechnologyStackValidationError
from .....dto import UserCreateDTO, UserReadDTO
from .....dto.competence_dto import CompetenceReadDTO
from .....dto.technology_stack import ParsedTechnologyStackDTO, TechnologyStackItemDTO
from .....mappers.user_mappers import map_dialog_data_to_user_registration_dto
from .....services import (
    CompetenceService,
    TechnologyStackParsingService,
    TechnologyStackService,
    UserRegistrationService,
)
from .....services.user_services import UserProfileService, UserService
from ....texts import (
    REGISTRATION_CONTACT_ACCEPTED,
    REGISTRATION_CONTACT_EMPTY,
    REGISTRATION_CONTACT_OWNER_MISMATCH,
    REGISTRATION_CONTACT_PROMPT,
    REGISTRATION_INTERNAL_ERROR,
    REGISTRATION_NAME_EMPTY,
    REGISTRATION_NAME_INVALID_SYMBOLS,
    REGISTRATION_NAME_TOO_SHORT,
    REGISTRATION_VALUE_INVALID,
    registration_existing_profile,
    registration_name_too_long,
    registration_profile_created,
    render_profile_message,
)
from ....texts.technology_stack import (
    STACK_BUTTON_AUTHOR_ONLY,
    STACK_NO_TECHNOLOGIES,
    STACK_PARSE_RETRY,
    STACK_SAVE_FAILED,
    render_stack_confirmation,
    render_stack_saved_recommendations,
)
from ...keyboards.auth import request_contact_kb
from ...keyboards.main_menu import get_main_menu_keyboard
from .getters import RegistrationCompetenceOption


async def on_other_messages(message: Message, message_input: MessageInput, manager: DialogManager) -> None:
    """Вспомогательная функция on_other_messages."""
    del message_input, manager
    await message.answer(REGISTRATION_VALUE_INVALID)


def _name_input_error_text(error: NameInputValidationError) -> str:
    match error.reason:
        case "empty":
            return REGISTRATION_NAME_EMPTY
        case "invalid_symbols":
            return REGISTRATION_NAME_INVALID_SYMBOLS
        case "too_short":
            return REGISTRATION_NAME_TOO_SHORT
        case "too_long":
            return registration_name_too_long(error.max_length or UserCreateDTO.NAME_MAX_LENGTH)
        case _:
            return REGISTRATION_VALUE_INVALID


async def request_contact_prompt(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
) -> None:
    """Вспомогательная функция request_contact_prompt."""
    del button
    if callback.message is not None:
        await callback.message.answer(
            REGISTRATION_CONTACT_PROMPT,
            reply_markup=request_contact_kb,
        )
    await callback.answer()
    await manager.next()


@inject
async def on_contact_input(
    message: Message,
    message_input: MessageInput,
    manager: DialogManager,
    user_service: FromDishka[UserService],
) -> None:
    """Вспомогательная функция on_contact_input."""
    del message_input
    await _handle_contact_input(message, manager, user_service)


async def _handle_contact_input(
    message: Message,
    manager: DialogManager,
    user_service: UserService,
) -> None:
    contact: Contact | None = message.contact if message.contact else None
    if contact is None or contact.phone_number is None:
        await message.answer(REGISTRATION_CONTACT_EMPTY)
        return
    if message.from_user is None or contact.user_id != message.from_user.id:
        await message.answer(REGISTRATION_CONTACT_OWNER_MISMATCH)
        return

    phone: str = contact.phone_number
    user = await user_service.find_user_by_phone(phone)
    if user:
        await message.answer(
            registration_existing_profile(user.id),
            reply_markup=ReplyKeyboardRemove(),
        )
        await manager.done()
        return

    manager.dialog_data["phone_number"] = contact.phone_number
    manager.dialog_data["competence_ids"] = []
    if message.from_user and hasattr(message.from_user, "id"):
        manager.dialog_data["tg_id"] = message.from_user.id
        manager.dialog_data["telegram_username"] = message.from_user.username
    else:
        manager.dialog_data["tg_id"] = None
        manager.dialog_data["telegram_username"] = None
    await message.answer(REGISTRATION_CONTACT_ACCEPTED, reply_markup=ReplyKeyboardRemove())
    await manager.next()


async def on_first_name_input(
    message: Message,
    widget: MessageInput,
    manager: DialogManager,
) -> None:
    """Вспомогательная функция on_first_name_input."""
    del widget
    first_name = message.text or ""
    try:
        validated_first_name = UserCreateDTO.validate_name_input(first_name)
    except NameInputValidationError as err:
        await message.answer(_name_input_error_text(err))
        return

    manager.dialog_data["first_name"] = validated_first_name
    await manager.next()


async def on_last_name_input(
    message: Message,
    widget: MessageInput,
    manager: DialogManager,
) -> None:
    """Вспомогательная функция on_last_name_input."""
    del widget
    last_name = message.text or ""
    try:
        validated_last_name = UserCreateDTO.validate_name_input(last_name)
    except NameInputValidationError as err:
        await message.answer(_name_input_error_text(err))
        return

    manager.dialog_data["last_name"] = validated_last_name
    await manager.next()


@inject
async def on_patronymic_input(
    message: Message,
    widget: MessageInput,
    manager: DialogManager,
    competence_service: FromDishka[CompetenceService],
) -> None:
    """Вспомогательная функция on_patronymic_input."""
    await _on_patronymic_input_impl(
        message=message,
        widget=widget,
        manager=manager,
        competence_service=competence_service,
    )


async def _on_patronymic_input_impl(
    message: Message,
    widget: MessageInput,
    manager: DialogManager,
    competence_service: CompetenceService,
) -> None:
    del widget
    patronymic = message.text or ""
    try:
        cleaned_patronymic = UserCreateDTO.validate_name_input(patronymic, allow_empty=True)
    except NameInputValidationError as err:
        await message.answer(_name_input_error_text(err))
        return

    manager.dialog_data["patronymic"] = cleaned_patronymic
    await _prepare_registration_competence_step(manager, competence_service)
    await manager.next()


@inject
async def on_patronymic_skip(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
    competence_service: FromDishka[CompetenceService],
) -> None:
    """Вспомогательная функция on_patronymic_skip."""
    del button
    await _on_patronymic_skip_impl(
        callback=callback,
        manager=manager,
        competence_service=competence_service,
    )


async def _on_patronymic_skip_impl(
    callback: CallbackQuery,
    manager: DialogManager,
    competence_service: CompetenceService,
) -> None:
    manager.dialog_data["patronymic"] = None
    await _prepare_registration_competence_step(manager, competence_service)
    await callback.answer()
    await manager.next()


async def on_competence_selection_changed(
    callback: CallbackQuery,
    widget: ManagedMultiselect[int],
    manager: DialogManager,
    item_id: int,
) -> None:
    """Форматирует текст для списка компетенций."""
    del callback, item_id
    manager.dialog_data["competence_ids"] = widget.get_checked()


@inject
async def on_competence_submit(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
    user_reg_service: FromDishka[UserRegistrationService],
    user_profile_service: FromDishka[UserProfileService],
) -> None:
    """Форматирует текст для списка компетенций."""
    del button
    await _on_competence_submit_impl(
        callback=callback,
        manager=manager,
        user_reg_service=user_reg_service,
        user_profile_service=user_profile_service,
    )


async def _on_competence_submit_impl(
    callback: CallbackQuery,
    manager: DialogManager,
    user_reg_service: UserRegistrationService,
    user_profile_service: UserProfileService,
) -> None:
    try:
        user = await _register_user_from_dialog(manager, user_reg_service)
    except Exception as error:
        logger.warning(
            "event=registration phase=completion status=failed error_type={error_type}",
            error_type=type(error).__name__,
        )
        if callback.message is not None:
            await callback.message.answer(REGISTRATION_INTERNAL_ERROR)
        await manager.done()
        return

    if user is None:
        if callback.message is not None:
            await callback.message.answer(REGISTRATION_INTERNAL_ERROR)
        await manager.done()
        return

    logger.info("event=registration phase=completion status=created")
    if callback.message is not None:
        await callback.message.answer(registration_profile_created(user.first_name))
        user_profile_dto = await user_profile_service.build_profile_view(user)
        await callback.message.answer(
            render_profile_message(user_profile_dto),
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(),
        )
        manager.dialog_data["registered_user_id"] = user.id
        manager.dialog_data["stack_owner_telegram_id"] = user.telegram_id
        manager.dialog_data["technology_stack_saved"] = False
        await manager.next()
    else:
        await manager.done()


@inject
async def on_competence_skip(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
    user_reg_service: FromDishka[UserRegistrationService],
    user_profile_service: FromDishka[UserProfileService],
) -> None:
    """Форматирует текст для списка компетенций."""
    del button
    await _on_competence_skip_impl(
        callback=callback,
        manager=manager,
        user_reg_service=user_reg_service,
        user_profile_service=user_profile_service,
    )


async def _on_competence_skip_impl(
    callback: CallbackQuery,
    manager: DialogManager,
    user_reg_service: UserRegistrationService,
    user_profile_service: UserProfileService,
) -> None:
    manager.dialog_data["competence_ids"] = []
    await _on_competence_submit_impl(
        callback=callback,
        manager=manager,
        user_reg_service=user_reg_service,
        user_profile_service=user_profile_service,
    )


async def _prepare_registration_competence_step(
    manager: DialogManager,
    competence_service: CompetenceService,
) -> None:
    competencies = await competence_service.find_all_competencies()
    manager.dialog_data["registration_competencies"] = _map_competencies_to_options(competencies)
    manager.dialog_data.setdefault("competence_ids", [])


def _map_competencies_to_options(competencies: Sequence[CompetenceReadDTO]) -> list[RegistrationCompetenceOption]:
    return [{"id": competence.id, "name": competence.name} for competence in competencies]


async def _register_user_from_dialog(
    manager: DialogManager,
    user_reg_service: UserRegistrationService,
) -> UserReadDTO | None:
    user_data = await map_dialog_data_to_user_registration_dto(manager)
    if user_data is None:
        return None
    return await user_reg_service.register_student(user_data)


@inject
async def on_technology_stack_input(
    message: Message,
    widget: MessageInput,
    manager: DialogManager,
    parsing_service: FromDishka[TechnologyStackParsingService],
) -> None:
    """Parse the next registration-state message into an unsaved stack draft."""
    del widget
    await _on_technology_stack_input_impl(message, manager, parsing_service)


async def _on_technology_stack_input_impl(
    message: Message,
    manager: DialogManager,
    parsing_service: TechnologyStackParsingService,
) -> None:
    if not _is_stack_owner(manager, message.from_user.id if message.from_user is not None else None):
        return
    if message.text is None or message.text.startswith("/"):
        return
    try:
        items = await parsing_service.parse(message.text)
    except TechnologyStackValidationError as error:
        await message.answer(error.message)
        return
    except Exception as error:
        logger.warning(
            "event=technology_stack phase=parse status=failed error_type={error_type}",
            error_type=type(error).__name__,
        )
        await message.answer(STACK_PARSE_RETRY)
        return

    if not items:
        await message.answer(STACK_NO_TECHNOLOGIES)
        return

    manager.dialog_data["technology_stack_draft"] = [
        {"technology": item.technology, "level": item.level.value} for item in items
    ]
    manager.dialog_data["technology_stack_confirmation"] = render_stack_confirmation(items)
    manager.dialog_data["technology_stack_saved"] = False
    await manager.next()


async def on_technology_stack_skip(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
) -> None:
    del button
    await _on_technology_stack_skip_impl(callback, manager)


async def _on_technology_stack_skip_impl(callback: CallbackQuery, manager: DialogManager) -> None:
    if not _is_stack_owner(manager, callback.from_user.id):
        await callback.answer(STACK_BUTTON_AUTHOR_ONLY, show_alert=True)
        return
    manager.dialog_data.pop("technology_stack_draft", None)
    manager.dialog_data.pop("technology_stack_confirmation", None)
    await callback.answer()
    await manager.done()


@inject
async def on_technology_stack_save(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
    technology_stack_service: FromDishka[TechnologyStackService],
) -> None:
    del button
    await _on_technology_stack_save_impl(callback, manager, technology_stack_service)


async def _on_technology_stack_save_impl(
    callback: CallbackQuery,
    manager: DialogManager,
    technology_stack_service: TechnologyStackService,
) -> None:
    if not _is_stack_owner(manager, callback.from_user.id):
        await callback.answer(STACK_BUTTON_AUTHOR_ONLY, show_alert=True)
        return
    if manager.dialog_data.get("technology_stack_saved") is True:
        await callback.answer("Стек уже сохранён.", show_alert=True)
        return

    draft = _technology_stack_draft(manager)
    owner_id = manager.dialog_data.get("registered_user_id")
    if draft is None or not isinstance(owner_id, int):
        await callback.answer(STACK_PARSE_RETRY, show_alert=True)
        return
    try:
        saved_items = await technology_stack_service.replace_user_stack(owner_id, draft)
    except Exception as error:
        logger.warning(
            "event=technology_stack phase=save status=failed error_type={error_type}",
            error_type=type(error).__name__,
        )
        await callback.answer(STACK_SAVE_FAILED, show_alert=True)
        return

    manager.dialog_data["technology_stack_saved"] = True
    await callback.answer()
    if isinstance(callback.message, Message):
        try:
            recommendations = await technology_stack_service.recommend_for_learning_items(owner_id, saved_items)
            response_text = render_stack_saved_recommendations(recommendations)
        except Exception as error:
            logger.warning(
                "event=technology_stack phase=recommendations status=failed error_type={error_type}",
                error_type=type(error).__name__,
            )
            response_text = "Твой стек сохранён. Не получилось подобрать помощников сейчас — попробуй позже."
        await callback.message.edit_text(response_text, reply_markup=None)
    await manager.done()


@inject
async def on_technology_stack_edit(
    callback: CallbackQuery,
    button: Button,
    manager: DialogManager,
) -> None:
    del button
    await _on_technology_stack_edit_impl(callback, manager)


async def _on_technology_stack_edit_impl(callback: CallbackQuery, manager: DialogManager) -> None:
    if not _is_stack_owner(manager, callback.from_user.id):
        await callback.answer(STACK_BUTTON_AUTHOR_ONLY, show_alert=True)
        return
    if manager.dialog_data.get("technology_stack_saved") is True:
        await callback.answer("Стек уже сохранён.", show_alert=True)
        return
    manager.dialog_data.pop("technology_stack_draft", None)
    manager.dialog_data.pop("technology_stack_confirmation", None)
    await callback.answer()
    await manager.back()


def _is_stack_owner(manager: DialogManager, telegram_id: int | None) -> bool:
    owner_telegram_id = manager.dialog_data.get("stack_owner_telegram_id")
    return isinstance(telegram_id, int) and telegram_id == owner_telegram_id


def _technology_stack_draft(manager: DialogManager) -> tuple[TechnologyStackItemDTO, ...] | None:
    raw_draft = manager.dialog_data.get("technology_stack_draft")
    if not isinstance(raw_draft, list):
        return None
    try:
        return ParsedTechnologyStackDTO.model_validate({"items": raw_draft}).items
    except Exception:
        return None
