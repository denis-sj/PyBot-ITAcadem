"""Admin action tools require a code-owned confirmation and a fresh role check."""

from datetime import date, time
from unittest.mock import Mock

from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
import pytest
from pydantic import ValidationError

from pybot.core.constants import PointsTypeEnum, RoleEnum
from pybot.dto.agent import AgentConversation, AgentReplyStatus
from pybot.dto import UserReadDTO
from pybot.dto.agent_read import AgentUserCandidate, AgentUserSearchResult
from pybot.dto.value_objects import Points
from pybot.services.agent.admin_action_tools import confirmation_summary
from pybot.services.agent.tools import AgentToolRegistry
from pybot.services.ports.agent import AgentActionsPort
from tests.agent.conftest import build_service

ADMIN_ACTION_TOOLS = {
    "adjust_user_points",
    "change_user_role",
    "change_user_competence",
    "update_attendance_visit_time",
    "delete_attendance_visit",
    "add_manual_attendance_visit",
}


def test_admin_mutation_tools_are_filtered_and_always_deferred():
    registry = AgentToolRegistry()
    student_tools = registry.for_roles(frozenset({"Student"})).policies
    admin_tools = registry.for_roles(frozenset({"Admin"})).policies

    assert ADMIN_ACTION_TOOLS.isdisjoint(student_tools)
    assert ADMIN_ACTION_TOOLS <= admin_tools.keys()
    assert ADMIN_ACTION_TOOLS <= {entry.tool.name for entry in registry.entries if entry.tool.requires_approval}


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("adjust_user_points", {"telegram_id": "771234", "points_type": "academic", "delta": 10}),
        ("adjust_user_points", {"telegram_id": 771234, "points_type": "academic", "delta": "10"}),
        ("adjust_user_points", {"telegram_id": 771234, "points_type": "academic", "delta": 10.5}),
        ("adjust_user_points", {"telegram_id": -771234, "points_type": "academic", "delta": 10}),
        ("change_user_role", {"telegram_id": 771234, "role": "Mentor", "add": "true"}),
        ("change_user_role", {"telegram_id": 771234, "role": "mentor", "add": True}),
        ("change_user_role", {"telegram_id": 771234, "role": 3, "add": True}),
        ("change_user_role", {"telegram_id": 771234.5, "role": "Mentor", "add": True}),
        ("change_user_competence", {"telegram_id": 771234, "competence_name": "Python", "add": "false"}),
        (
            "update_attendance_visit_time",
            {"telegram_id": "771234", "visit_date": "2026-10-10", "start_time": "10:00", "end_time": "11:00"},
        ),
        (
            "update_attendance_visit_time",
            {
                "telegram_id": 771234,
                "visit_date": "2026-10-10",
                "start_time": "10:00:59",
                "end_time": "11:00",
            },
        ),
        ("delete_attendance_visit", {"telegram_id": 771234.5, "visit_date": "2026-10-10"}),
        ("send_broadcast", {"message": "Текст", "audience": 1}),
        ("send_broadcast", {"message": "Текст", "audience": "students"}),
        ("send_broadcast", {"message": "Текст", "audience": "role", "target": 1}),
        (
            "add_manual_attendance_visit",
            {
                "telegram_id": 771234,
                "visit_date": "2026-10-10",
                "start_time": "10:00:59",
                "end_time": "11:00",
            },
        ),
        (
            "add_manual_attendance_visit",
            {"telegram_id": -771234, "visit_date": "2026-10-10", "start_time": "10:00", "end_time": "11:00"},
        ),
    ],
)
def test_action_tool_arguments_reject_implicit_coercions(tool_name, arguments):
    registry = AgentToolRegistry()
    tool = next(entry.tool for entry in registry.entries if entry.tool.name == tool_name)

    with pytest.raises(ValidationError):
        tool.function_schema.validator.validate_python(arguments)


def test_negative_points_delta_remains_a_valid_deduction():
    registry = AgentToolRegistry()
    tool = next(entry.tool for entry in registry.entries if entry.tool.name == "adjust_user_points")

    validated = tool.function_schema.validator.validate_python(
        {"telegram_id": 771234, "points_type": "academic", "delta": -10}
    )

    assert validated["delta"] == -10


@pytest.mark.parametrize("tool_name", ["update_attendance_visit_time", "add_manual_attendance_visit"])
def test_attendance_action_time_schema_accepts_only_hh_mm(tool_name):
    registry = AgentToolRegistry()
    tool = next(entry.tool for entry in registry.entries if entry.tool.name == tool_name)
    arguments = {
        "telegram_id": 771234,
        "visit_date": "2026-10-10",
        "start_time": "10:00",
        "end_time": "11:00",
    }

    validated = tool.function_schema.validator.validate_python(arguments)

    assert validated["start_time"] == "10:00"
    assert validated["end_time"] == "11:00"
    with pytest.raises(ValidationError, match="pattern"):
        tool.function_schema.validator.validate_python({**arguments, "start_time": "10:00:59"})


@pytest.mark.asyncio
async def test_visit_with_seconds_is_rejected_without_confirmation(agent_settings, conversation):
    actions = Mock(spec=AgentActionsPort)

    def model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, RetryPromptPart) for part in message.parts)
            for message in messages
        ):
            return ModelResponse(parts=[TextPart("Время посещения нужно указать в формате ЧЧ:ММ, без секунд.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "update_attendance_visit_time",
                    {
                        "telegram_id": 771234,
                        "visit_date": "2026-10-10",
                        "start_time": "10:00:59",
                        "end_time": "11:00",
                    },
                    tool_call_id="visit-with-seconds",
                )
            ]
        )

    service, users, _ = build_service(agent_settings, model=FunctionModel(model), actions=actions)
    users.find_all_user_roles.return_value = {"Admin"}

    result = await service.answer(conversation, "Измени посещение Telegram ID 771234 на 10:00:59–11:00")

    assert result.confirmation_id is None
    assert result.text == "Время посещения нужно указать в формате ЧЧ:ММ, без секунд."
    assert not service.runtime.pending
    actions.validate_visit_update.assert_not_awaited()


def test_points_confirmation_uses_exact_action_arguments():
    call = ToolCallPart(
        "adjust_user_points",
        {
            "telegram_id": 771234,
            "points_type": PointsTypeEnum.ACADEMIC.value,
            "delta": -12,
            "reason": "возврат за отменённый доклад",
        },
        tool_call_id="points",
    )

    assert confirmation_summary(call, "Иванов Иван, Telegram ID 771234") == (
        "списать участнику Иванов Иван, Telegram ID 771234 12 академических баллов. "
        "Причина: «возврат за отменённый доклад»."
    )


async def admin_points_model(messages, info):
    return ModelResponse(
        parts=[
            ToolCallPart(
                "adjust_user_points",
                {
                    "telegram_id": 771234,
                    "points_type": PointsTypeEnum.ACADEMIC.value,
                    "delta": 10,
                },
                tool_call_id="change-points",
            )
        ]
    )


@pytest.mark.asyncio
async def test_points_change_runs_only_after_confirmation(agent_settings, conversation):
    actions = Mock(spec=AgentActionsPort)
    actions.adjust_points.return_value = "Участнику Ивану начислено 10 баллов."
    actions.find_user.return_value = UserReadDTO(
        id=17,
        first_name="Пётр",
        last_name="Петров",
        patronymic="Петрович",
        telegram_id=771234,
        academic_points=Points(value=10, point_type=PointsTypeEnum.ACADEMIC),
        reputation_points=Points(value=0, point_type=PointsTypeEnum.REPUTATION),
        join_date=date(2026, 1, 1),
        telegram_username="petrov",
    )
    service, users, memory = build_service(
        agent_settings,
        model=FunctionModel(admin_points_model),
        actions=actions,
    )
    users.find_all_user_roles.return_value = {"Admin"}

    question = "Начисли 10 академических баллов участнику с Telegram ID 771234 за доклад"
    pending = await service.answer(conversation, question)

    assert pending.confirmation_id is not None
    assert "Петров Пётр Петрович (@petrov), Telegram ID 771234" in pending.text
    assert (
        pending.text
        == "начислить участнику Петров Пётр Петрович (@petrov), Telegram ID 771234 10 академических баллов."
    )
    pending_request = service.runtime.pending[pending.confirmation_id]
    assert pending_request.requests.approvals[0].args_as_dict() == {
        "telegram_id": 771234,
        "points_type": PointsTypeEnum.ACADEMIC.value,
        "delta": 10,
        "reason": None,
    }
    actions.validate_points_adjustment.assert_awaited_once_with(42, 771234, PointsTypeEnum.ACADEMIC, 10)
    actions.adjust_points.assert_not_awaited()
    memory.append.assert_not_awaited()

    service.runtime.bind_message(pending.confirmation_id, 99)
    result = await service.confirm(pending.confirmation_id, conversation, 99, approved=True)

    assert result.text == "Участнику Ивану начислено 10 баллов."
    actions.adjust_points.assert_awaited_once_with(42, 771234, PointsTypeEnum.ACADEMIC, 10, None)
    assert actions.validate_points_adjustment.await_count == 2
    memory.append.assert_awaited_once_with(conversation, question, "Участнику Ивану начислено 10 баллов.")


@pytest.mark.asyncio
async def test_invalid_action_arguments_never_create_a_confirmation(agent_settings, conversation):
    def model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, RetryPromptPart) for part in message.parts)
            for message in messages
        ):
            return ModelResponse(parts=[TextPart("Уточните, выдать или снять роль.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "change_user_role",
                    {"telegram_id": 771234, "role": "Mentor", "add": "true"},
                    tool_call_id="invalid-role-change",
                )
            ]
        )

    actions = Mock(spec=AgentActionsPort)
    service, users, _ = build_service(agent_settings, model=FunctionModel(model), actions=actions)
    users.find_all_user_roles.return_value = {"Admin"}

    result = await service.answer(conversation, "Выдай роль Mentor")

    assert result.confirmation_id is None
    assert result.text == "Уточните, выдать или снять роль."
    assert not service.runtime.pending
    actions.change_role.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "tool_name",
        "arguments",
        "roles",
        "validation_name",
        "validation_arguments",
        "execution_name",
        "execution_arguments",
        "canonical_arguments",
    ),
    [
        (
            "request_mentor_role",
            {},
            {"Student"},
            "validate_role_request",
            (42,),
            "request_mentor_role",
            (42,),
            {},
        ),
        (
            "adjust_user_points",
            {"telegram_id": 771234, "points_type": "academic", "delta": 10},
            {"Admin"},
            "validate_points_adjustment",
            (42, 771234, PointsTypeEnum.ACADEMIC, 10),
            "adjust_points",
            (42, 771234, PointsTypeEnum.ACADEMIC, 10, None),
            {"telegram_id": 771234, "points_type": "academic", "delta": 10, "reason": None},
        ),
        (
            "change_user_role",
            {"telegram_id": 771234, "role": "Mentor", "add": True},
            {"Admin"},
            "validate_role_change",
            (771234, RoleEnum.MENTOR, True),
            "change_role",
            (771234, RoleEnum.MENTOR, True),
            {"telegram_id": 771234, "role": "Mentor", "add": True},
        ),
        (
            "change_user_competence",
            {"telegram_id": 771234, "competence_name": "Python", "add": False},
            {"Admin"},
            "validate_competence_change",
            (771234, "Python", False),
            "change_competence",
            (771234, "Python", False),
            {"telegram_id": 771234, "competence_name": "Python", "add": False},
        ),
        (
            "update_attendance_visit_time",
            {
                "telegram_id": 771234,
                "visit_date": "2026-10-10",
                "start_time": "10:00",
                "end_time": "11:00",
            },
            {"Admin"},
            "validate_visit_update",
            (771234, date(2026, 10, 10), time(10, 0), time(11, 0)),
            "update_visit_time",
            (771234, date(2026, 10, 10), time(10, 0), time(11, 0), "actor"),
            {
                "telegram_id": 771234,
                "visit_date": "2026-10-10",
                "start_time": "10:00",
                "end_time": "11:00",
            },
        ),
        (
            "delete_attendance_visit",
            {"telegram_id": 771234, "visit_date": "2026-10-10"},
            {"Admin"},
            "validate_visit_delete",
            (771234, date(2026, 10, 10)),
            "delete_visit",
            (771234, date(2026, 10, 10), "actor"),
            {"telegram_id": 771234, "visit_date": "2026-10-10"},
        ),
        (
            "add_manual_attendance_visit",
            {
                "telegram_id": 771234,
                "visit_date": "2026-10-10",
                "start_time": "10:00",
                "end_time": "11:00",
            },
            {"Admin"},
            "validate_manual_visit",
            (771234, date(2026, 10, 10), time(10, 0), time(11, 0)),
            "create_manual_visit",
            (771234, date(2026, 10, 10), time(10, 0), time(11, 0), "actor"),
            {
                "telegram_id": 771234,
                "visit_date": "2026-10-10",
                "start_time": "10:00",
                "end_time": "11:00",
            },
        ),
        (
            "send_broadcast",
            {"message": "Собрание в 18:00", "audience": "all"},
            {"Admin"},
            "validate_broadcast",
            ("all", None, "Собрание в 18:00"),
            "broadcast",
            ("all", None, "Собрание в 18:00"),
            {"message": "Собрание в 18:00", "audience": "all", "target": None},
        ),
    ],
)
async def test_action_preview_uses_the_same_canonical_arguments_as_execution(
    agent_settings,
    conversation,
    tool_name,
    arguments,
    roles,
    validation_name,
    validation_arguments,
    execution_name,
    execution_arguments,
    canonical_arguments,
):
    actions = Mock(spec=AgentActionsPort)
    for entry in AgentActionsPort.__dict__:
        method = getattr(actions, entry, None)
        if hasattr(method, "return_value"):
            method.return_value = "Действие выполнено"
    actions.find_user.return_value = UserReadDTO(
        id=17,
        first_name="Пётр",
        last_name="Петров",
        patronymic="Петрович",
        telegram_id=771234,
        academic_points=Points(value=10, point_type=PointsTypeEnum.ACADEMIC),
        reputation_points=Points(value=0, point_type=PointsTypeEnum.REPUTATION),
        join_date=date(2026, 1, 1),
        telegram_username="petrov",
    )
    getattr(actions, execution_name).return_value = "Действие выполнено"

    def model(messages, info):
        if any(
            isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        ):
            return ModelResponse(parts=[TextPart("Действие выполнено")])
        return ModelResponse(parts=[ToolCallPart(tool_name, arguments, tool_call_id=f"call-{tool_name}")])

    registry = AgentToolRegistry(broadcast_allowed_roles=frozenset({"Admin"}))
    service, users, _ = build_service(agent_settings, model=FunctionModel(model), registry=registry, actions=actions)
    users.find_all_user_roles.return_value = roles
    action_conversation = (
        AgentConversation(chat_id=11, topic_id=None, telegram_id=11) if tool_name == "send_broadcast" else conversation
    )

    question = "Подготовь действие для Telegram ID 771234"
    pending = await service.answer(action_conversation, question)

    assert pending.confirmation_id is not None
    stored = service.runtime.pending[pending.confirmation_id]
    assert stored.requests.approvals[0].args_as_dict() == canonical_arguments
    stored_tool_call = next(
        part
        for message in stored.messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    )
    assert stored_tool_call.args_as_dict() == canonical_arguments
    if "telegram_id" in arguments:
        assert "Петров Пётр Петрович (@petrov), Telegram ID 771234" in pending.text
    if tool_name == "update_attendance_visit_time":
        assert pending.text.endswith("на 10:00–11:00.")
    if tool_name == "add_manual_attendance_visit":
        assert pending.text.endswith(": 10:00–11:00.")

    service.runtime.bind_message(pending.confirmation_id, 77)
    confirmed = await service.confirm(pending.confirmation_id, action_conversation, 77, approved=True)

    assert confirmed.status == AgentReplyStatus.SUCCESS
    getattr(actions, validation_name).assert_awaited_with(*validation_arguments)
    if "actor" in execution_arguments:
        execution_arguments = tuple(
            users.find_user_by_telegram_id.return_value if argument == "actor" else argument
            for argument in execution_arguments
        )
    getattr(actions, execution_name).assert_awaited_with(*execution_arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_result", "action_telegram_id", "should_preview"),
    [
        (
            AgentUserSearchResult(
                name_query="Иванов",
                candidates=[
                    AgentUserCandidate(full_name="Иванов Иван", telegram_id=771234),
                    AgentUserCandidate(full_name="Иванова Анна", telegram_id=771235),
                ],
                total_matches=2,
            ),
            771234,
            False,
        ),
        (
            AgentUserSearchResult(name_query="@missing", candidates=[], total_matches=0),
            771234,
            False,
        ),
        (
            AgentUserSearchResult(
                name_query="Иванов",
                candidates=[AgentUserCandidate(full_name="Иванов Иван", telegram_id=771234)],
                total_matches=1,
            ),
            771234,
            True,
        ),
        (
            AgentUserSearchResult(
                name_query="Иванов",
                candidates=[AgentUserCandidate(full_name="Иванов Иван", telegram_id=771234)],
                total_matches=1,
            ),
            771235,
            False,
        ),
    ],
)
async def test_action_preview_requires_one_exact_candidate(
    agent_settings, conversation, search_result, action_telegram_id, should_preview
):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from pybot.services.agent.reads import AgentReadService

    reads = Mock(spec=AgentReadService)
    reads.find_user_candidates.return_value = search_result
    actions = Mock(spec=AgentActionsPort)
    actions.find_user.return_value = UserReadDTO(
        id=17,
        first_name="Иван",
        last_name="Иванов",
        patronymic="Иванович",
        telegram_id=action_telegram_id,
        academic_points=Points(value=10, point_type=PointsTypeEnum.ACADEMIC),
        reputation_points=Points(value=0, point_type=PointsTypeEnum.REPUTATION),
        join_date=date(2026, 1, 1),
        telegram_username="petya",
    )

    def model(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "find_user_candidates",
                    {"name_query": search_result.name_query},
                    tool_call_id="candidate-search",
                ),
                ToolCallPart(
                    "change_user_role",
                    {"telegram_id": action_telegram_id, "role": "Mentor", "add": True},
                    tool_call_id="change-role",
                ),
            ]
        )

    service, users, _ = build_service(
        agent_settings,
        model=FunctionModel(model),
        reads=reads,
        actions=actions,
    )
    users.find_all_user_roles.return_value = {"Admin"}

    result = await service.answer(conversation, f"Выдай роль {search_result.name_query}")

    if should_preview:
        assert result.confirmation_id is not None
        assert "Иванов Иван Иванович (@petya), Telegram ID 771234" in result.text
    else:
        assert result.confirmation_id is None
        assert "Уточните" in result.text
        if search_result.total_matches == 0:
            assert "Совпадений не нашёл" in result.text
        elif search_result.total_matches > 1:
            assert "Иванов Иван — Telegram ID 771234" in result.text
            assert "Иванова Анна — Telegram ID 771235" in result.text
        assert not service.runtime.pending
        actions.find_user.assert_not_awaited()
    actions.change_role.assert_not_awaited()
