"""Unit tests for PolzaVisionAdapter."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from pybot.domain.exceptions import (
    AttendanceNetworkError,
    AttendancePhotoEmptyError,
    AttendanceRecognitionError,
)
from pybot.infrastructure.adapters import PolzaVisionAdapter


@pytest.fixture
def adapter() -> PolzaVisionAdapter:
    return PolzaVisionAdapter(
        api_key="test_api_key_secret",
        base_url="https://polza.ai/api/v1",
        model="google/gemini-2.5-flash",
        timeout_s=30,
    )


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
        assert len(block.rows) == 1

        # Check recalculated duration
        row1 = block.rows[0]
        assert row1.raw_name == "Иванов Иван"
        assert row1.duration_minutes == 120  # Recomputed, not 999!
        assert row1.is_empty_time is False
        assert row1.status == "recognized"


@pytest.mark.asyncio
async def test_empty_time_and_dash_rows_are_discarded(adapter: PolzaVisionAdapter) -> None:
    ai_response_payload = {
        "quality": {"isGood": True, "comment": "OK", "problems": []},
        "dates": [
            {
                "date": "2026-09-25",
                "rows": [
                    {"participantFullName": "Присутствовал А.", "timeRaw": "18:00-19:30"},
                    {"participantFullName": "Отсутствовал Б.", "timeRaw": "-", "isEmptyTime": True},
                    {"participantFullName": "Отсутствовал В.", "timeRaw": "нет"},
                    {"participantFullName": "Отсутствовал Г.", "timeRaw": "прочерк"},
                    {"participantFullName": "Отсутствовал Д.", "timeRaw": "н"},
                    {"participantFullName": "Отсутствовал Е.", "timeRaw": ""},
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

        assert len(result.dates[0].rows) == 1
        assert result.dates[0].rows[0].raw_name == "Присутствовал А."
        assert result.dates[0].rows[0].duration_minutes == 90


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
