"""Russian user-facing text for goals, check-ins and notification preferences."""

from ...dto.community_checkins import PulseReportDTO, UserNotificationPreferencesDTO

CHECKIN_SAVED = "Записал, удачи на неделе!"
CHECKIN_PARSE_RETRY = "Не получилось разобрать ответ. Попробуй написать, чем занимаешься и что сейчас мешает."
CHECKIN_MESSAGE_TOO_LONG = "Ответ слишком длинный. Сократи его до 4000 символов и отправь ещё раз."
CHECKIN_PAUSED = "Чекины выключены. Включить их снова можно командой /resume_checkin."
CHECKIN_RESUMED = "Чекины включены. Следующий опрос придёт в среду в 15:00."
CHECKIN_ALREADY_PAUSED = "Чекины уже выключены."
CHECKIN_ALREADY_RESUMED = "Чекины уже включены."
GOAL_EMPTY = "Активной цели пока нет. Её можно назвать в ответе на чекин или попросить агента поставить цель."
GOAL_SET = "Записал твою новую цель."
GOAL_COMPLETED = "Поздравляю с достигнутой целью! Добавил значок «Цель взята»."
GOAL_NO_ACTIVE = "Сейчас нет активной цели, которую можно отметить достигнутой."
STREAK_WARNING = (
    "У тебя уже {streak} недель подряд с посещениями 🔥 На этой неделе визита ещё не было — "
    "приходи, чтобы продолжить серию!"
)
PULSE_PDF_CAPTION = "Ответили {answered}/{invited}, нужна помощь — {needs_help}."
PULSE_PDF_FAILED = "⚠️ PDF-отчёт не собрался. Краткая сводка:"
PULSE_EMPTY = "За эту неделю на чекин никто не ответил."

NOTIFICATION_SETTING_LABELS = {
    "checkins_enabled": "Еженедельные чекины",
    "monthly_summary_enabled": "Итоги месяца",
    "achievement_notifications_enabled": "Личные поздравления с достижениями",
    "streak_warnings_enabled": "Предупреждение о серии",
    "meeting_invitations_enabled": "Приглашения на встречи",
}


def render_checkin_prompt(goal_text: str | None, has_technology_stack: bool) -> str:
    """Build the Wednesday private check-in prompt from current participant data."""
    audience_notice = "Ответ увидят администраторы клуба — так им проще помочь."
    if goal_text:
        return f"Привет! Как продвигается цель „{goal_text}“? Чем занимаешься на этой неделе? {audience_notice}"

    text = f"Привет! К чему ты сейчас готовишься и чем занимаешься на этой неделе? {audience_notice}"
    if not has_technology_stack:
        text += "\n\nЕсли стека технологий пока нет, расскажи, что ты уже знаешь и что изучаешь."
    return text


def render_checkin_reminder(goal_text: str | None) -> str:
    """Build the one Thursday reminder for a participant with no response."""
    prefix = "Напоминаю про еженедельный чекин. "
    if goal_text:
        return f"{prefix}Как продвигается цель „{goal_text}“? Ответ увидят администраторы клуба."
    return f"{prefix}Расскажи, к чему готовишься и чем занимаешься. Ответ увидят администраторы клуба."


def render_notification_preferences(preferences: UserNotificationPreferencesDTO) -> str:
    """Render all notification switches and their current state."""
    lines = ["Настройки уведомлений:"]
    setting_values = (
        ("checkins_enabled", preferences.checkins_enabled),
        ("monthly_summary_enabled", preferences.monthly_summary_enabled),
        ("achievement_notifications_enabled", preferences.achievement_notifications_enabled),
        ("streak_warnings_enabled", preferences.streak_warnings_enabled),
        ("meeting_invitations_enabled", preferences.meeting_invitations_enabled),
    )
    for field_name, is_enabled in setting_values:
        label = NOTIFICATION_SETTING_LABELS[field_name]
        state = "включены" if is_enabled else "выключены"
        lines.append(f"{label}: {state}")
    return "\n".join(lines)


def render_pulse_summary(report: PulseReportDTO) -> str:
    """Build the concise text fallback using the same four KPI counts as the PDF."""
    return (
        f"Пульс сообщества · {report.week_start:%d.%m}–{report.week_end:%d.%m}\n"
        f"Ответили: {report.answered_count}/{report.invited_count} ({_format_delta(report.answered_delta)}).\n"
        f"Нужна помощь: {report.needs_help_count} ({_format_delta(report.needs_help_delta)}).\n"
        f"Целей достигнуто: {report.goals_achieved_count} ({_format_delta(report.goals_achieved_delta)}).\n"
        f"Посещений: {report.visit_count} ({_format_delta(report.visits_delta)})."
    )


def _format_delta(value: int) -> str:
    """Render a signed change without an ambiguous zero sign."""
    if value > 0:
        return f"+{value} к прошлой неделе"
    if value < 0:
        return f"{value} к прошлой неделе"
    return "без изменений"
