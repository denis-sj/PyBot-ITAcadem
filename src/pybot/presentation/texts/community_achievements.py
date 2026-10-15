"""Russian presentation text for community achievements."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from ...dto import AchievementAwardReadDTO, AchievementPublicationDTO, AttendanceMonthlyRankingItemDTO

_WEEK_PLURAL_THRESHOLD = 5
_ACHIEVEMENT_LABELS: dict[str, tuple[str, str]] = {
    "first_step": ("👣", "Первый шаг"),
    "streak_4": ("🔥", "Постоянство · 4 недели"),
    "streak_8": ("🔥", "Постоянство · 8 недель"),
    "streak_16": ("🔥", "Постоянство · 16 недель"),
    "visits_10": ("🔟", "Десятка"),
    "visits_25": ("🏅", "25 посещений"),
    "visits_50": ("🏆", "50 посещений"),
    "comeback": ("🔄", "Камбэк"),
    "top_week": ("🥇", "Топ недели"),
    "goal_done": ("🎯", "Цель взята"),
    "mentor": ("🎓", "Ментор"),
}


def achievement_title(code: str) -> str:
    """Return the Russian name for a stable achievement code."""
    return _ACHIEVEMENT_LABELS.get(code, ("🏅", "Достижение"))[1]


def render_achievement_profile_section(
    current_streak: int,
    achievements: Sequence[AchievementAwardReadDTO],
) -> str:
    """Render the current streak and earned badges in a profile."""
    streak_label = (
        "недель"
        if current_streak == 0 or current_streak >= _WEEK_PLURAL_THRESHOLD
        else "неделя"
        if current_streak == 1
        else "недели"
    )
    lines = [f"🔥 Текущая серия посещений: {current_streak} {streak_label}"]
    if achievements:
        labels = [achievement_title(item.code) for item in achievements]
        lines.append("🏅 Достижения: " + ", ".join(labels))
    else:
        lines.append("🏅 Достижения: пока нет")
    return "\n".join(lines)


def render_achievement_congratulation(codes: Sequence[str]) -> str:
    """Build one immediate personal message for all newly earned badges."""
    lines = ["🎉 Поздравляем! Новые достижения:"]
    lines.extend(
        f"{_ACHIEVEMENT_LABELS.get(code, ('🏅', 'Достижение'))[0]} {achievement_title(code)}" for code in codes
    )
    return "\n".join(lines)


def render_achievement_digest(awards: Sequence[AchievementPublicationDTO]) -> str:
    """Build the daily group digest from awards that are eligible for publication."""
    grouped: dict[tuple[int, str], list[str]] = {}
    for award in awards:
        name = " ".join(
            part for part in (award.first_name.strip(), award.last_name.strip() if award.last_name else "") if part
        )
        title = achievement_title(award.code)
        if award.code == "comeback":
            title += ", с возвращением!"
        grouped.setdefault((award.user_id, name), []).append(title)

    lines = ["🏆 Достижения сегодня:"]
    for (_, name), titles in grouped.items():
        safe_name = escape(name or "Участник")
        safe_titles = ", ".join(escape(title) for title in titles)
        lines.append(f"• <b>{safe_name}</b> — {safe_titles}")
    return "\n".join(lines)


def render_top_week_message(
    rows: Sequence[AttendanceMonthlyRankingItemDTO],
    *,
    start_date: str,
    end_date: str,
) -> str:
    """Build one weekly top post using the existing attendance ranking results."""
    lines = [f"🏆 Топ посещаемости за {start_date}–{end_date}:"]
    for row in rows:
        hours, minutes = divmod(row.total_minutes, 60)
        duration = f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"
        lines.append(f"{row.rank}. {escape(row.user_full_name)} — {duration}")
    return "\n".join(lines)
