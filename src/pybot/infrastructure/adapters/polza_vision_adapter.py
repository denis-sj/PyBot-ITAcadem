"""Adapter for Polza.ai vision API for attendance sheet recognition."""

from __future__ import annotations

import base64
import json
import math
import re
import time as time_module
from datetime import date, time
from http import HTTPStatus
from typing import Any, Literal

import httpx

from ...core import logger
from ...domain.exceptions import (
    AttendanceNetworkError,
    AttendancePhotoEmptyError,
    AttendanceRecognitionError,
)
from ...dto import (
    AttendanceQualityDTO,
    AttendanceRecognitionResultDTO,
    AttendanceRecognizedDateBlockDTO,
    AttendanceRecognizedRowDTO,
    calculate_duration_minutes,
    parse_time_range,
)
from ...services.ports import AttendanceRecognitionPort

_MIN_CONFIDENCE_THRESHOLD: float = 0.7

_SYSTEM_PROMPT = """\
Ты — специализированный ассистент для распознавания рукописных и печатных листов учёта посещаемости клуба.
Твоя задача — извлечь дату (или даты) и строки с именами участников и временем посещения.

Формат ответа — строго валидный JSON следующей структуры:
{
  "quality": {
    "isGood": true,
    "comment": "краткая оценка читаемости листа",
    "problems": []
  },
  "dates": [
    {
      "date": "YYYY-MM-DD",
      "rows": [
        {
          "participantFullName": "Фамилия Имя Отчество",
          "timeRaw": "18:00-20:00",
          "startTime": "18:00",
          "endTime": "20:00",
          "durationMinutes": 120,
          "isEmptyTime": false,
          "confidence": 0.95,
          "status": "recognized"
        }
      ]
    }
  ]
}

Правила:
1. Оцени качество изображения: isGood=false если изображение смазано, нерезко, обрезано или нечитаемо.
2. Извлеки все даты посещений в формате YYYY-MM-DD.
3. Для каждого участника найди строку времени.
4. Если время отсутствует или пусто, установи isEmptyTime=true, durationMinutes=0,
   startTime=null, endTime=null.
5. Если передан список участников клуба, сопоставь распознанные имена с ним.
   Если имя однозначно совпадает с кандидатом, используй точное имя кандидата.
6. Если имя распознано неуверенно, отсутствует в списке или время неразборчиво —
   установи status="needs_manual_review".
7. Если запись распознана четко и уверенно — установи status="recognized".
8. Поле confidence должно быть от 0.0 до 1.0.
"""

_TIME_PARSE_PATTERN = re.compile(r"^(\d{1,2})[:.](\d{2})$")


def _clean_json_markdown(text: str) -> str:
    """Remove markdown code blocks if the model wrapped JSON in them."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _parse_hh_mm(raw: str | None) -> time | None:
    """Parse time string in HH:MM or HH.MM format."""
    if not raw or not isinstance(raw, str):
        return None
    match = _TIME_PARSE_PATTERN.match(raw.strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    hours_in_day = 24
    minutes_in_hour = 60
    if 0 <= hours < hours_in_day and 0 <= minutes < minutes_in_hour:
        return time(hours, minutes)
    return None


class PolzaVisionAdapter(AttendanceRecognitionPort):
    """Adapter for recognizing attendance sheets using Polza.ai vision API."""

    def __init__(
        self,
        api_key: str | None,
        base_url: str = "https://polza.ai/api/v1",
        model: str = "google/gemini-2.5-flash",
        timeout_s: int = 60,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s

    async def recognize_attendance_sheet(
        self,
        photo_bytes: bytes,
        mime_type: str,
        candidate_user_names: list[str],
    ) -> AttendanceRecognitionResultDTO:
        """Recognize attendance sheet photo and extract structured attendance data.

        Security & Logging Note:
            This method never logs image bytes, user names, prompt content,
            or the raw response payload. Only model name, HTTP status code,
            and elapsed duration are logged.
        """
        if not photo_bytes:
            raise AttendancePhotoEmptyError("Файл фотографии пуст")

        if not self._api_key:
            raise AttendanceRecognitionError("Polza.ai API key is not configured")

        b64_image = base64.b64encode(photo_bytes).decode("ascii")
        data_url = f"data:{mime_type};base64,{b64_image}"

        user_text = "Распознай лист учёта посещаемости на этом изображении."
        if candidate_user_names:
            candidates_str = ", ".join(candidate_user_names)
            user_text += f"\nСписок известных участников клуба для сопоставления: {candidates_str}"

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
        }

        endpoint = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        start_time = time_module.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            elapsed_ms = int((time_module.monotonic() - start_time) * 1000)
            logger.warning(
                "polza_vision_timeout model={} elapsed_ms={}",
                self._model,
                elapsed_ms,
            )
            raise AttendanceNetworkError(
                f"Таймаут запроса к сервису распознавания Polza.ai ({self._timeout_s} с)",
                details={"timeout_s": self._timeout_s, "model": self._model},
            ) from exc
        except httpx.NetworkError as exc:
            elapsed_ms = int((time_module.monotonic() - start_time) * 1000)
            logger.warning(
                "polza_vision_network_error model={} elapsed_ms={}",
                self._model,
                elapsed_ms,
            )
            raise AttendanceNetworkError(
                "Сетевая ошибка при обращении к сервису распознавания Polza.ai",
                details={"model": self._model},
            ) from exc

        elapsed_ms = int((time_module.monotonic() - start_time) * 1000)

        if response.status_code != HTTPStatus.OK:
            logger.warning(
                "polza_vision_http_error model={} status_code={} elapsed_ms={}",
                self._model,
                response.status_code,
                elapsed_ms,
            )
            if response.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                raise AttendanceRecognitionError(
                    "Ошибка авторизации в сервисе распознавания Polza.ai (неверный API ключ)",
                    details={"status_code": response.status_code, "model": self._model},
                )
            if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                raise AttendanceNetworkError(
                    "Превышен лимит запросов к сервису распознавания Polza.ai",
                    details={"status_code": response.status_code, "model": self._model},
                )
            if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
                raise AttendanceNetworkError(
                    "Сервис распознавания Polza.ai временно недоступен",
                    details={"status_code": response.status_code, "model": self._model},
                )
            raise AttendanceRecognitionError(
                f"Сервис распознавания вернул ошибку HTTP {response.status_code}",
                details={"status_code": response.status_code, "model": self._model},
            )

        logger.info(
            "polza_vision_completed model={} status_code={} elapsed_ms={}",
            self._model,
            response.status_code,
            elapsed_ms,
        )

        return self._parse_and_validate_response(response.text)

    def _parse_and_validate_response(self, response_text: str) -> AttendanceRecognitionResultDTO:
        """Parse raw response text, validate structure, and recalculate durations."""
        try:
            body = json.loads(response_text)
            content = body["choices"][0]["message"]["content"]
            parsed_data = json.loads(_clean_json_markdown(content))
        except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            raise AttendanceRecognitionError(
                "Не удалось распарсить JSON-ответ от сервиса распознавания Polza.ai"
            ) from exc

        quality_dict = parsed_data.get("quality", {})
        is_good = bool(quality_dict.get("isGood", quality_dict.get("is_good", True)))
        comment = str(quality_dict.get("comment", ""))
        problems = [str(p) for p in quality_dict.get("problems", [])]
        quality_dto = AttendanceQualityDTO(
            is_good=is_good,
            comment=comment,
            problems=problems,
        )

        dates_list = parsed_data.get("dates", [])
        if not isinstance(dates_list, list):
            dates_list = []

        date_blocks: list[AttendanceRecognizedDateBlockDTO] = []
        for raw_block in dates_list:
            if not isinstance(raw_block, dict):
                continue

            raw_date = raw_block.get("date")
            try:
                block_date = date.fromisoformat(str(raw_date))
            except (ValueError, TypeError):  # fmt: skip
                continue

            raw_rows = raw_block.get("rows", [])
            if not isinstance(raw_rows, list):
                raw_rows = []

            rows: list[AttendanceRecognizedRowDTO] = []
            for raw_row in raw_rows:
                if not isinstance(raw_row, dict):
                    continue
                rows.append(self._process_recognized_row(raw_row))

            date_blocks.append(
                AttendanceRecognizedDateBlockDTO(
                    date=block_date,
                    rows=rows,
                )
            )

        return AttendanceRecognitionResultDTO(
            quality=quality_dto,
            dates=date_blocks,
        )

    @staticmethod
    def _parse_confidence_and_status(
        raw_row: dict[str, Any],
    ) -> tuple[float, Literal["recognized", "needs_manual_review"]]:
        """Extract confidence and base review status from row."""
        raw_confidence = raw_row.get("confidence")
        confidence = 0.0
        is_valid_confidence = False
        if raw_confidence is not None:
            try:
                parsed_conf = float(raw_confidence)
                if math.isfinite(parsed_conf):
                    confidence = max(0.0, min(1.0, parsed_conf))
                    is_valid_confidence = True
            except (ValueError, TypeError):  # fmt: skip
                confidence = 0.0

        raw_status = raw_row.get("status", "recognized")
        if not is_valid_confidence or raw_status != "recognized" or confidence < _MIN_CONFIDENCE_THRESHOLD:
            status: Literal["recognized", "needs_manual_review"] = "needs_manual_review"
            if not is_valid_confidence:
                confidence = 0.0
        else:
            status = "recognized"
        return confidence, status

    @staticmethod
    def _resolve_row_times(
        raw_row: dict[str, Any],
        time_raw: str | None,
        is_empty_time: bool,
    ) -> tuple[time | None, time | None, int, bool]:
        """Resolve start_time, end_time, duration_minutes, and whether time requires review."""
        if is_empty_time:
            return None, None, 0, False

        if not time_raw:
            return None, None, 0, True

        parsed_range = parse_time_range(time_raw)
        if parsed_range is not None:
            start_time, end_time, duration_minutes = parsed_range
            return start_time, end_time, duration_minutes, False

        model_start = _parse_hh_mm(raw_row.get("startTime") or raw_row.get("start_time"))
        model_end = _parse_hh_mm(raw_row.get("endTime") or raw_row.get("end_time"))
        if model_start and model_end:
            duration_minutes = calculate_duration_minutes(model_start, model_end)
            requires_review = duration_minutes <= 0
            return model_start, model_end, duration_minutes, requires_review

        return None, None, 0, True

    def _process_recognized_row(self, raw_row: dict[str, Any]) -> AttendanceRecognizedRowDTO:
        """Process and validate a single recognized row, recalculating duration in Python."""
        raw_name = raw_row.get("participantFullName") or raw_row.get("participant_full_name") or raw_row.get("raw_name")
        if raw_name is not None:
            raw_name = str(raw_name).strip() or None

        time_raw = raw_row.get("timeRaw") or raw_row.get("time_raw")
        if time_raw is not None:
            time_raw = str(time_raw).strip() or None

        is_empty_time = bool(raw_row.get("isEmptyTime", raw_row.get("is_empty_time", False)))
        if not time_raw or time_raw in ("-", "—", "–", "нет", "прочерк"):
            is_empty_time = True

        confidence, status = self._parse_confidence_and_status(raw_row)
        start_time, end_time, duration_minutes, time_requires_review = self._resolve_row_times(
            raw_row, time_raw, is_empty_time
        )
        if time_requires_review:
            status = "needs_manual_review"

        return AttendanceRecognizedRowDTO(
            raw_name=raw_name,
            time_raw=time_raw,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            is_empty_time=is_empty_time,
            confidence=confidence,
            status=status,
        )
