from pybot.presentation.texts import HELP_GROUP, HELP_PRIVATE, HELP_PRIVATE_PUBLIC


HELP_PAGE_BLOCK = (
    "Подробная инструкция по использованию бота:\nhttps://cewerty.github.io/PyBot-ITAcadem/user-guide/how-to-use-bot/"
)


def test_help_private_contains_roles_command() -> None:
    assert "/roles - показать все роли в системе" in HELP_PRIVATE


def test_help_private_contains_showroles_command() -> None:
    assert "/showroles [@user|id|reply] - показать роли пользователя" in HELP_PRIVATE


def test_help_private_public_contains_roles_command() -> None:
    assert "/roles - показать все роли в системе" in HELP_PRIVATE_PUBLIC


def test_help_private_public_contains_showroles_command() -> None:
    assert "/showroles [@user|id|reply] - показать роли пользователя" in HELP_PRIVATE_PUBLIC


def test_help_group_contains_roles_command() -> None:
    assert "/roles - показать все роли в системе" in HELP_GROUP


def test_help_group_contains_showroles_command() -> None:
    assert "/showroles [@user|id|reply] - показать роли пользователя" in HELP_GROUP


def test_help_private_contains_docs_link_block() -> None:
    assert HELP_PAGE_BLOCK in HELP_PRIVATE


def test_help_private_public_contains_docs_link_block() -> None:
    assert HELP_PAGE_BLOCK in HELP_PRIVATE_PUBLIC


def test_help_group_contains_docs_link_block() -> None:
    assert HELP_PAGE_BLOCK in HELP_GROUP


def test_help_private_my_visits_description() -> None:
    assert "/my_visits - выбор месяца и просмотр посещений" in HELP_PRIVATE
    assert "за текущий месяц" not in [line for line in HELP_PRIVATE.splitlines() if "/my_visits" in line][0]


def test_help_private_contains_edit_visits_command() -> None:
    assert "/edit_visits - управление посещениями участников" in HELP_PRIVATE


def test_help_private_attendance_commands_stay_compact() -> None:
    assert "/report - отчёты и добавление часов по фото" in HELP_PRIVATE
    assert "/attendance_photo - выбрать фото по месяцу и дате" in HELP_PRIVATE
    assert "откройте /report и нажмите" not in HELP_PRIVATE


def test_help_private_public_my_visits_description() -> None:
    assert "/my_visits - выбор месяца и просмотр посещений" in HELP_PRIVATE_PUBLIC
