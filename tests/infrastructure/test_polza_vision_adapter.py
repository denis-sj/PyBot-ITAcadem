"""Unit tests for PolzaVisionAdapter."""

import json
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from pybot.domain.exceptions import (
    AttendanceNetworkError,
    AttendancePhotoEmptyError,
    AttendanceRecognitionError,
)
from pybot.dto import AttendanceRecognitionResultDTO
from pybot.infrastructure.attendance_photo_preprocessor import PreparedAttendancePhoto
from pybot.infrastructure.attendance_row_locator import AttendanceRowCrop
from pybot.infrastructure.adapters import PolzaVisionAdapter


@pytest.fixture
def adapter() -> PolzaVisionAdapter:
    return PolzaVisionAdapter(
        api_key="test_api_key_secret",
        base_url="https://polza.ai/api/v1",
        model="google/gemini-2.5-flash",
        timeout_s=30,
    )


@pytest.fixture(autouse=True)
def mock_valid_prepared_photo() -> Generator[None, None, None]:
    """Keep API parser tests independent from OpenCV image decoding."""
    prepared_photo = PreparedAttendancePhoto(
        normalized_image_bytes=b"normalized-test-image",
        mime_type="image/jpeg",
        row_evidence_sheet_bytes=b"time-cell-contact-sheet",
        layout_verified=True,
        expected_row_counts=(),
        time_cell_has_ink=(),
    )
    with patch(
        "pybot.infrastructure.adapters.polza_vision_adapter.prepare_attendance_photo",
        return_value=prepared_photo,
    ):
        yield


@pytest.mark.asyncio
async def test_recognize_raises_when_photo_bytes_empty(adapter: PolzaVisionAdapter) -> None:
    with pytest.raises(AttendancePhotoEmptyError, match="Файл фотографии пуст"):
        await adapter.recognize_attendance_sheet(b"", "image/jpeg", [])


@pytest.mark.asyncio
async def test_recognize_raises_when_api_key_is_none() -> None:
    adapter_no_key = PolzaVisionAdapter(api_key=None)
    with pytest.raises(AttendanceRecognitionError, match="API key is not configured"):
        await adapter_no_key.recognize_attendance_sheet(b"dummy_bytes", "image/jpeg", [])


@pytest.mark.asyncio
async def test_recognize_happy_path_recalculates_duration_and_strips_markdown(
    adapter: PolzaVisionAdapter,
) -> None:
    ai_response_payload = {
        "quality": {
            "isGood": True,
            "comment": "Хорошо читаемый лист",
            "problems": [],
        },
        "dates": [
            {
                "date": "2026-09-25",
                "rows": [
                    {
                        "participantFullName": "Иванов Иван",
                        "timeRaw": "18:00-20:00",
                        # Model claims 999 minutes, but Python should recalculate to 120!
                        "durationMinutes": 999,
                        "isEmptyTime": False,
                        "confidence": 0.95,
                        "status": "recognized",
                    },
                    {
                        "participantFullName": "Петров Петр",
                        "timeRaw": "-",
                        "durationMinutes": 60,
                        "isEmptyTime": True,
                        "confidence": 0.9,
                        "status": "recognized",
                    },
                ],
            }
        ],
    }

    # Wrap in markdown fences to test fence stripping
    wrapped_content = f"```json\n{json.dumps(ai_response_payload)}\n```"
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": wrapped_content}}]},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await adapter.recognize_attendance_sheet(
            photo_bytes=b"fake_image_bytes",
            mime_type="image/jpeg",
            candidate_user_names=["Иванов Иван", "Петров Петр"],
        )

        assert result.quality.is_good is True
        assert result.quality.comment == "Хорошо читаемый лист"
        assert len(result.dates) == 1
        block = result.dates[0]
        assert str(block.date) == "2026-09-25"
        assert len(block.rows) == 2

        # Check recalculated duration
        row1 = block.rows[0]
        assert row1.raw_name == "Иванов Иван"
        assert row1.duration_minutes == 120  # Recomputed, not 999!
        assert row1.is_empty_time is False
        assert row1.status == "recognized"
        row2 = block.rows[1]
        assert row2.raw_name == "Петров Петр"
        assert row2.time_raw == "N"
        assert row2.duration_minutes == 0
        assert row2.is_empty_time is True
        assert row2.status == "recognized"


@pytest.mark.asyncio
async def test_verified_layout_orders_by_row_index_and_flags_uncertain_time_cell(
    adapter: PolzaVisionAdapter,
) -> None:
    prepared_photo = PreparedAttendancePhoto(
        normalized_image_bytes=b"normalized-test-image",
        mime_type="image/jpeg",
        row_evidence_sheet_bytes=b"time-cell-contact-sheet",
        layout_verified=True,
        expected_row_counts=(2,),
        time_cell_has_ink=((True, False),),
    )
    payload = {
        "quality": {"isGood": True, "comment": "", "problems": []},
        "dates": [
            {
                "date": "2026-06-16",
                "rows": [
                    {
                        "rowIndex": 2,
                        "participantFullName": "Петров Петр",
                        "timeRaw": "N",
                        "confidence": 0.99,
                        "status": "recognized",
                    },
                    {
                        "rowIndex": 1,
                        "participantFullName": "Иванов Иван",
                        "timeRaw": "10:20-15:80",
                        "confidence": 0.99,
                        "status": "recognized",
                    },
                ],
            }
        ],
    }
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )

    with (
        patch(
            "pybot.infrastructure.adapters.polza_vision_adapter.prepare_attendance_photo",
            return_value=prepared_photo,
        ),
        patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post,
    ):
        mock_post.return_value = mock_response
        result = await adapter.recognize_attendance_sheet(b"photo", "image/jpeg", [])

    rows = result.dates[0].rows
    assert len(rows) == 2
    assert rows[0].raw_name == "Иванов Иван"
    assert rows[0].time_raw == "10:20-15:80"
    assert rows[0].status == "needs_manual_review"
    assert rows[1].raw_name == "Петров Петр"
    assert rows[1].is_empty_time is True
    assert rows[1].status == "recognized"

    request_content = mock_post.call_args.kwargs["json"]["messages"][1]["content"]
    assert len([item for item in request_content if item["type"] == "image_url"]) == 2


@pytest.mark.asyncio
async def test_unverified_layout_keeps_model_rows_manual(adapter: PolzaVisionAdapter) -> None:
    payload = {
        "quality": {"isGood": True, "comment": "", "problems": []},
        "dates": [
            {
                "date": "2026-06-16",
                "rows": [
                    {
                        "participantFullName": "Иванов Иван",
                        "timeRaw": "18:00-20:00",
                        "confidence": 0.99,
                        "status": "recognized",
                    }
                ],
            }
        ],
    }
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )
    unverified_photo = PreparedAttendancePhoto(
        normalized_image_bytes=b"original-photo",
        mime_type="image/jpeg",
        row_evidence_sheet_bytes=b"",
        layout_verified=False,
        expected_row_counts=(),
        time_cell_has_ink=(),
    )

    with (
        patch(
            "pybot.infrastructure.adapters.polza_vision_adapter.prepare_attendance_photo",
            return_value=unverified_photo,
        ),
        patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post,
    ):
        mock_post.return_value = mock_response
        result = await adapter.recognize_attendance_sheet(b"photo", "image/jpeg", [])

    assert result.dates[0].rows[0].status == "needs_manual_review"


@pytest.mark.asyncio
async def test_indexed_rows_keep_their_slots_and_flag_ink_conflicts(adapter: PolzaVisionAdapter) -> None:
    prepared_photo = PreparedAttendancePhoto(
        normalized_image_bytes=b"normalized-test-image",
        mime_type="image/jpeg",
        row_evidence_sheet_bytes=b"time-cell-contact-sheet",
        layout_verified=True,
        expected_row_counts=(3,),
        time_cell_has_ink=((True, True, False),),
    )
    payload = {
        "quality": {"isGood": True, "comment": "", "problems": []},
        "dates": [
            {
                "date": "2026-06-16",
                "rows": [
                    {
                        "rowIndex": 1,
                        "participantFullName": "Иванов Иван",
                        "timeRaw": "N",
                        "confidence": 0.99,
                        "status": "recognized",
                    },
                    {
                        "rowIndex": 3,
                        "participantFullName": "Сидоров Сидор",
                        "timeRaw": "N",
                        "confidence": 0.99,
                        "status": "recognized",
                    },
                ],
            }
        ],
    }
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )

    with (
        patch(
            "pybot.infrastructure.adapters.polza_vision_adapter.prepare_attendance_photo",
            return_value=prepared_photo,
        ),
        patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post,
    ):
        mock_post.return_value = mock_response
        result = await adapter.recognize_attendance_sheet(b"photo", "image/jpeg", [])

    rows = result.dates[0].rows
    assert len(rows) == 3
    assert rows[0].raw_name == "Иванов Иван"
    assert rows[0].status == "needs_manual_review"  # The cell contains blue ink, but the model says N.
    assert rows[1].raw_name is None  # Missing row 2 stays in row 2 instead of shifting row 3 upward.
    assert rows[1].status == "needs_manual_review"
    assert rows[2].raw_name == "Сидоров Сидор"
    assert rows[2].status == "needs_manual_review"  # A reported time in a visually empty cell is suspicious.


@pytest.mark.asyncio
async def test_empty_time_and_absence_markers_are_preserved_as_n(adapter: PolzaVisionAdapter) -> None:
    ai_response_payload = {
        "quality": {"isGood": True, "comment": "OK", "problems": []},
        "dates": [
            {
                "date": "2026-09-25",
                "rows": [
                    {
                        "participantFullName": "Присутствовал А.",
                        "timeRaw": "18:00-19:30",
                        "confidence": 0.9,
                        "status": "recognized",
                    },
                    {
                        "participantFullName": "Отсутствовал Б.",
                        "timeRaw": "-",
                        "isEmptyTime": True,
                        "confidence": 0.9,
                        "status": "recognized",
                    },
                    {
                        "participantFullName": "Отсутствовал В.",
                        "timeRaw": "нет",
                        "confidence": 0.9,
                        "status": "recognized",
                    },
                    {
                        "participantFullName": "Отсутствовал Г.",
                        "timeRaw": "прочерк",
                        "confidence": 0.9,
                        "status": "recognized",
                    },
                    {
                        "participantFullName": "Отсутствовал Д.",
                        "timeRaw": "н",
                        "confidence": 0.9,
                        "status": "recognized",
                    },
                    {
                        "participantFullName": "Отсутствовал Е.",
                        "timeRaw": "",
                        "confidence": 0.9,
                        "status": "recognized",
                    },
                ],
            }
        ],
    }
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(ai_response_payload)}}]},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await adapter.recognize_attendance_sheet(b"bytes", "image/jpeg", ["Присутствовал А."])

        rows = result.dates[0].rows
        assert len(rows) == 6
        assert rows[0].raw_name == "Присутствовал А."
        assert rows[0].duration_minutes == 90
        for row in rows[1:]:
            assert row.time_raw == "N"
            assert row.duration_minutes == 0
            assert row.is_empty_time is True
            assert row.status == "recognized"


@pytest.mark.asyncio
async def test_recognize_handles_http_auth_error(adapter: PolzaVisionAdapter) -> None:
    mock_response = httpx.Response(
        status_code=401,
        json={"error": "Unauthorized"},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(AttendanceRecognitionError, match="неверный API ключ"):
            await adapter.recognize_attendance_sheet(b"photo", "image/png", [])


@pytest.mark.asyncio
async def test_recognize_handles_http_rate_limit(adapter: PolzaVisionAdapter) -> None:
    mock_response = httpx.Response(
        status_code=429,
        json={"error": "Rate limit exceeded"},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(AttendanceNetworkError, match="Превышен лимит запросов"):
            await adapter.recognize_attendance_sheet(b"photo", "image/png", [])


@pytest.mark.asyncio
async def test_recognize_handles_http_server_error(adapter: PolzaVisionAdapter) -> None:
    mock_response = httpx.Response(
        status_code=502,
        text="Bad Gateway",
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(AttendanceNetworkError, match="временно недоступен"):
            await adapter.recognize_attendance_sheet(b"photo", "image/png", [])


@pytest.mark.asyncio
async def test_recognize_handles_timeout(adapter: PolzaVisionAdapter) -> None:
    with patch.object(httpx.AsyncClient, "post", side_effect=httpx.TimeoutException("Timeout")):
        with pytest.raises(AttendanceNetworkError, match="Таймаут запроса"):
            await adapter.recognize_attendance_sheet(b"photo", "image/png", [])


@pytest.mark.asyncio
async def test_recognize_handles_network_error(adapter: PolzaVisionAdapter) -> None:
    with patch.object(httpx.AsyncClient, "post", side_effect=httpx.NetworkError("Connection refused")):
        with pytest.raises(AttendanceNetworkError, match="Сетевая ошибка"):
            await adapter.recognize_attendance_sheet(b"photo", "image/png", [])


@pytest.mark.asyncio
async def test_recognize_handles_invalid_json(adapter: PolzaVisionAdapter) -> None:
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": "not a valid json {{"}}]},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(AttendanceRecognitionError, match="Не удалось распарсить JSON-ответ"):
            await adapter.recognize_attendance_sheet(b"photo", "image/png", [])


@pytest.mark.asyncio
async def test_recognize_missing_or_invalid_confidence_marks_manual_review(
    adapter: PolzaVisionAdapter,
) -> None:
    ai_response_payload = {
        "quality": {"isGood": True, "comment": "Хорошо", "problems": []},
        "dates": [
            {
                "date": "2026-09-25",
                "rows": [
                    {
                        "participantFullName": "Иванов Иван",
                        "timeRaw": "18:00-20:00",
                        # confidence is missing
                        "status": "recognized",
                    },
                    {
                        "participantFullName": "Петров Петр",
                        "timeRaw": "18:00-19:00",
                        "confidence": "not-a-number",
                        "status": "recognized",
                    },
                    {
                        "participantFullName": "Сидоров Сидор",
                        "timeRaw": "18:00-19:00",
                        "confidence": 0.5,
                        "status": "recognized",
                    },
                ],
            }
        ],
    }
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(ai_response_payload)}}]},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await adapter.recognize_attendance_sheet(
            photo_bytes=b"fake_image_bytes",
            mime_type="image/jpeg",
            candidate_user_names=["Иванов Иван", "Петров Петр", "Сидоров Сидор"],
        )

        rows = result.dates[0].rows
        # Missing confidence -> confidence=0.0, status="needs_manual_review"
        assert rows[0].confidence == 0.0
        assert rows[0].status == "needs_manual_review"

        # Invalid confidence -> confidence=0.0, status="needs_manual_review"
        assert rows[1].confidence == 0.0
        assert rows[1].status == "needs_manual_review"

        # Low confidence (< 0.7) -> confidence=0.5, status="needs_manual_review"
        assert rows[2].confidence == 0.5
        assert rows[2].status == "needs_manual_review"


@pytest.mark.parametrize(
    "non_finite_confidence",
    [
        "NaN",
        float("nan"),
        "Infinity",
        "-Infinity",
        "inf",
        "-inf",
        float("inf"),
        float("-inf"),
    ],
)
def test_parse_confidence_and_status_rejects_non_finite_confidence(
    non_finite_confidence: object,
) -> None:
    row = {
        "participantFullName": "Иванов Иван",
        "timeRaw": "18:00-20:00",
        "confidence": non_finite_confidence,
        "status": "recognized",
    }
    confidence, status = PolzaVisionAdapter._parse_confidence_and_status(row)
    assert confidence == 0.0
    assert status == "needs_manual_review"


@pytest.mark.parametrize(
    "non_finite_confidence",
    [
        "NaN",
        float("nan"),
        "Infinity",
        "-Infinity",
        "inf",
        "-inf",
        float("inf"),
        float("-inf"),
    ],
)
@pytest.mark.asyncio
async def test_recognize_rejects_non_finite_confidence_marks_manual_review(
    adapter: PolzaVisionAdapter,
    non_finite_confidence: object,
) -> None:
    ai_response_payload = {
        "quality": {"isGood": True, "comment": "Хорошо", "problems": []},
        "dates": [
            {
                "date": "2026-09-25",
                "rows": [
                    {
                        "participantFullName": "Иванов Иван",
                        "timeRaw": "18:00-20:00",
                        "confidence": non_finite_confidence,
                        "status": "recognized",
                    },
                ],
            }
        ],
    }
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(ai_response_payload)}}]},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await adapter.recognize_attendance_sheet(
            photo_bytes=b"fake_image_bytes",
            mime_type="image/jpeg",
            candidate_user_names=["Иванов Иван"],
        )

        row = result.dates[0].rows[0]
        assert row.confidence == 0.0
        assert row.status == "needs_manual_review"


def test_incomplete_handwritten_range_is_preserved_for_manual_review(adapter: PolzaVisionAdapter) -> None:
    row = adapter._process_recognized_row(
        {
            "participantFullName": "Калвайтис Мирон Андреевич",
            "timeRaw": "8:40-",
            "confidence": 0.99,
            "status": "recognized",
        }
    )

    assert row is not None
    assert row.time_raw == "8:40-"
    assert row.is_empty_time is False
    assert row.duration_minutes == 0
    assert row.status == "needs_manual_review"


def test_boundary_crossing_prevents_confident_row_assignment(adapter: PolzaVisionAdapter) -> None:
    rows = adapter._process_recognized_rows(
        [
            {
                "rowIndex": 1,
                "participantFullName": "Первый участник",
                "timeRaw": "14:26-19:00",
                "confidence": 0.95,
                "status": "recognized",
            },
            {
                "rowIndex": 2,
                "participantFullName": "Второй участник",
                "timeRaw": "10:20-15:00",
                "confidence": 0.95,
                "status": "recognized",
            },
        ],
        expected_row_count=2,
        ink_evidence=(True, True),
        boundary_ambiguity=(True, True),
    )

    assert len(rows) == 2
    assert all(row.status == "needs_manual_review" for row in rows)


def _geometry_bound_photo(
    row_count: int,
    handwriting_rows: tuple[int, ...],
    ambiguous_rows: tuple[int, ...] = (),
) -> PreparedAttendancePhoto:
    """Prepared photo whose rows were isolated by sheet geometry, one image per row."""
    return PreparedAttendancePhoto(
        normalized_image_bytes=b"normalized-test-image",
        mime_type="image/jpeg",
        row_evidence_sheet_bytes=b"row-evidence-sheet",
        layout_verified=True,
        expected_row_counts=(row_count,),
        time_cell_has_ink=(tuple(row in handwriting_rows for row in range(1, row_count + 1)),),
        time_cell_boundary_ambiguous=(tuple(row in ambiguous_rows for row in range(1, row_count + 1)),),
        row_crops=tuple(
            AttendanceRowCrop(
                date_index=0,
                row_index=row,
                image_bytes=f"row-{row}".encode(),
                has_handwriting=row in handwriting_rows or row in ambiguous_rows,
                is_ambiguous=row in ambiguous_rows,
            )
            for row in range(1, row_count + 1)
        ),
        rows_bound_by_geometry=True,
    )


def _model_row(row_id: str, name: str, time_raw: str = "N", status: str = "recognized") -> dict[str, object]:
    return {
        "rowId": row_id,
        "participantFullName": name,
        "timeRaw": time_raw,
        "confidence": 0.95,
        "status": status,
    }


def _sheet_payload(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "quality": {"isGood": True, "comment": "", "problems": []},
        "dates": [{"date": "2026-06-16", "rows": rows}],
    }


async def _recognize_with(
    adapter: PolzaVisionAdapter,
    prepared_photo: PreparedAttendancePhoto,
    payload: dict[str, object],
) -> tuple[AttendanceRecognitionResultDTO, list[dict[str, object]]]:
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        request=httpx.Request("POST", "https://polza.ai/api/v1/chat/completions"),
    )
    with (
        patch(
            "pybot.infrastructure.adapters.polza_vision_adapter.prepare_attendance_photo",
            return_value=prepared_photo,
        ),
        patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post,
    ):
        mock_post.return_value = mock_response
        result = await adapter.recognize_attendance_sheet(b"photo", "image/jpeg", [])
    return result, mock_post.call_args.kwargs["json"]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_row_images_bind_names_and_times_by_row_id(adapter: PolzaVisionAdapter) -> None:
    # Rows arrive out of order; binding must follow rowId, not response order or a model rowIndex.
    payload = _sheet_payload(
        [
            _model_row("D1-R03", "Третий участник", "8:40-", status="needs_manual_review"),
            _model_row("D1-R01", "Первый участник"),
            _model_row("D1-R04", "Четвёртый участник", "11:15-15:20"),
            _model_row("D1-R02", "Второй участник", "14:26-19:00"),
        ]
    )

    result, request_content = await _recognize_with(adapter, _geometry_bound_photo(4, (2, 3, 4)), payload)

    first, second, third, fourth = result.dates[0].rows
    assert (first.raw_name, first.is_empty_time, first.status) == ("Первый участник", True, "recognized")
    assert (second.raw_name, second.time_raw, second.duration_minutes) == ("Второй участник", "14:26-19:00", 274)
    assert second.status == "recognized"
    assert (third.raw_name, third.time_raw, third.end_time, third.duration_minutes) == (
        "Третий участник",
        "8:40-",
        None,
        0,
    )
    assert third.status == "needs_manual_review"
    assert (fourth.raw_name, fourth.time_raw, fourth.duration_minutes) == ("Четвёртый участник", "11:15-15:20", 245)

    row_labels = []
    for content_part in request_content:
        text_content = content_part.get("text")
        if content_part.get("type") == "text" and isinstance(text_content, str) and "rowId" in text_content:
            row_labels.append(text_content)
    assert row_labels[-4:] == ["rowId D1-R01", "rowId D1-R02", "rowId D1-R03", "rowId D1-R04"]
    assert len([item for item in request_content if item["type"] == "image_url"]) == 2 + 4


@pytest.mark.asyncio
async def test_skipped_row_id_sends_the_whole_date_to_manual_review(adapter: PolzaVisionAdapter) -> None:
    # The model dropped row 2 and invented a row that is not on the sheet.
    payload = _sheet_payload(
        [
            _model_row("D1-R01", "Первый участник"),
            _model_row("D1-R03", "Третий участник", "10:00-12:00"),
            _model_row("D1-R04", "Время нахождения в"),
            _model_row("D1-R05", "Лишняя строка"),
        ]
    )

    result, _ = await _recognize_with(adapter, _geometry_bound_photo(4, (3,)), payload)

    rows = result.dates[0].rows
    assert rows[1].raw_name is None
    assert rows[2].raw_name == "Третий участник"
    assert rows[2].time_raw == "10:00-12:00"
    assert all(row.status == "needs_manual_review" for row in rows)


@pytest.mark.asyncio
async def test_ambiguous_row_image_requires_manual_review(adapter: PolzaVisionAdapter) -> None:
    payload = _sheet_payload(
        [_model_row("D1-R01", "Первый участник", "10:00-12:00"), _model_row("D1-R02", "Второй участник", "10:00-12:00")]
    )

    result, _ = await _recognize_with(adapter, _geometry_bound_photo(2, (), ambiguous_rows=(1, 2)), payload)

    assert [row.status for row in result.dates[0].rows] == ["needs_manual_review", "needs_manual_review"]


@pytest.mark.asyncio
async def test_row_with_handwriting_read_as_empty_is_unreadable_not_absent(adapter: PolzaVisionAdapter) -> None:
    payload = _sheet_payload([_model_row("D1-R01", "Первый участник", "N")])

    result, _ = await _recognize_with(adapter, _geometry_bound_photo(1, (1,)), payload)

    row = result.dates[0].rows[0]
    assert (row.raw_name, row.is_empty_time, row.time_raw, row.status) == (
        "Первый участник",
        False,
        "?",
        "needs_manual_review",
    )


@pytest.mark.asyncio
async def test_time_claimed_for_row_without_handwriting_requires_manual_review(adapter: PolzaVisionAdapter) -> None:
    payload = _sheet_payload(
        [_model_row("D1-R01", "Первый участник", "09:00-10:00"), _model_row("D1-R02", "Второй участник")]
    )

    result, _ = await _recognize_with(adapter, _geometry_bound_photo(2, (2,)), payload)

    first, second = result.dates[0].rows
    assert first.is_empty_time is True
    assert first.status == "needs_manual_review"
    assert second.time_raw == "?"
    assert second.status == "needs_manual_review"
