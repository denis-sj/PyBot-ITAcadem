"""Russian texts for participant technology stacks and helper recommendations."""

from collections.abc import Sequence

from ...dto.technology_stack import TechnologyStackHelperDTO, TechnologyStackItemDTO, TechnologyStackRecommendationDTO

STACK_ONBOARDING_PROMPT = (
    "Расскажи о своём стеке: что уже знаешь и что изучаешь. Например: знаю Python и SQL, изучаю Docker."
)
STACK_SKIP_BUTTON = "Пропустить"
STACK_SAVE_BUTTON = "Сохранить"
STACK_EDIT_BUTTON = "Исправить"
STACK_PARSE_RETRY = "Не получилось распознать стек. Напиши, что ты знаешь и что изучаешь."
STACK_SAVED = "Твой стек сохранён."
STACK_EMPTY = "Стек пока пуст. Напиши, что ты знаешь и что изучаешь."
STACK_BUTTON_AUTHOR_ONLY = "Эта кнопка доступна только автору регистрации."
STACK_SAVE_FAILED = "Не получилось сохранить стек. Попробуй ещё раз позже."


def render_stack_confirmation(items: Sequence[TechnologyStackItemDTO]) -> str:
    """Render a proposed full-stack replacement without claiming it was saved."""
    known = [item.technology for item in items if item.level.value == "know"]
    learning = [item.technology for item in items if item.level.value == "learning"]
    return f"Записал: знаю — {_join_technologies(known)}; изучаю — {_join_technologies(learning)}."


def render_stack_read(items: Sequence[TechnologyStackItemDTO]) -> str:
    """Format the caller's own stack for Telegram or an agent response."""
    if not items:
        return STACK_EMPTY
    known = [item.technology for item in items if item.level.value == "know"]
    learning = [item.technology for item in items if item.level.value == "learning"]
    return f"Твой стек:\nЗнаю: {_join_technologies(known)}.\nИзучаю: {_join_technologies(learning)}."


def render_helper_recommendation(recommendation: TechnologyStackRecommendationDTO) -> str:
    """Format up to three helpers without linking people who have no username."""
    if not recommendation.helpers:
        return f"Пока не нашёл, у кого можно спросить про {recommendation.technology}. Попробуй спросить в группе."
    helpers = ", ".join(_render_helper(helper) for helper in recommendation.helpers)
    return f"Кстати, в {recommendation.technology} у нас разбираются: {helpers}. Можешь написать им."


def render_stack_saved_recommendations(
    recommendations: Sequence[TechnologyStackRecommendationDTO],
) -> str:
    """Confirm a saved stack and append suggestions for each learning technology."""
    rendered_recommendations = [render_helper_recommendation(item) for item in recommendations]
    if not rendered_recommendations:
        return STACK_SAVED
    return STACK_SAVED + "\n\n" + "\n\n".join(rendered_recommendations)


def _render_helper(helper: TechnologyStackHelperDTO) -> str:
    visible_name = f"@{helper.telegram_username}" if helper.telegram_username else helper.full_name or "Имя не указано"
    return f"{visible_name} (ментор)" if helper.is_mentor else visible_name


def _join_technologies(technologies: Sequence[str]) -> str:
    return ", ".join(technologies) if technologies else "ничего"
