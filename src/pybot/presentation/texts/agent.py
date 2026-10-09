"""Safe Telegram rendering for agent outcomes; internal error details never escape."""

from ...dto.agent import AgentReply, AgentReplyStatus

AGENT_UNAVAILABLE = "Сейчас не получается ответить, попробуйте позже или воспользуйтесь меню"
AGENT_REGISTER = "Пройдите регистрацию в личном чате с ботом: /start"
AGENT_EXPIRED = "Запрос устарел, повторите"
AGENT_DENIED = "Недостаточно прав"
_STATUS_TEXTS = {
    AgentReplyStatus.UNAVAILABLE: AGENT_UNAVAILABLE,
    AgentReplyStatus.REGISTRATION_REQUIRED: AGENT_REGISTER,
    AgentReplyStatus.ACCESS_DENIED: AGENT_DENIED,
    AgentReplyStatus.EXPIRED: AGENT_EXPIRED,
    AgentReplyStatus.NOT_AUTHOR: "Это не ваш запрос",
    AgentReplyStatus.CANCELLED: "Отменено",
}


def render_agent_reply(reply: AgentReply) -> str:
    """Translate application outcomes into safe transport text."""
    if reply.confirmation_id is not None:
        return "Будет выполнено:\n" + reply.text
    return _STATUS_TEXTS.get(reply.status, reply.text)
