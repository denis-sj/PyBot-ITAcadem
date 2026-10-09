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
from ..attendance_photo_preprocessor import PreparedAttendancePhoto, prepare_attendance_photo
from ..attendance_row_locator import AttendanceRowCrop

_MIN_CONFIDENCE_THRESHOLD: float = 0.7

_SYSTEM_PROMPT = """\
Распознай лист посещаемости. Верни только валидный JSON:
{
  "quality":{"isGood":true,"comment":"","problems":[]},
  "dates":[{"date":"YYYY-MM-DD","rows":[
    {"rowId":"D1-R01","rowIndex":1,"participantFullName":"Фамилия Имя Отчество",
     "timeRaw":"14:20-18:00","confidence":0.95,"status":"recognized"}
  ]}]
}

Правила чтения:
1. Если в запросе есть изображения строк с подписью rowId (например, «rowId D1-R06»), строки
   уже разделены программой по линейкам листа. На каждом таком изображении слева напечатано
   ФИО ровно одной строки, справа в рамке — только рукописная запись этой же строки.
   Для каждого rowId верни ровно одну строку с этим rowId в дате с тем же номером D. Имя и
   время бери только с этого изображения; не переноси их из соседних строк и с целого листа.
   Пустая рамка означает отсутствие: timeRaw="N".
2. Если изображений строк нет, читай каждую дату отдельно, сверху вниз, и верни ровно одну
   строку на каждое напечатанное ФИО в исходном порядке; rowIndex — номер строки от 1.
3. Переписывай цифры времени буквально, включая двоеточие/точку и незаконченный диапазон с
   дефисом (например, «8:40-»). Не исправляй, не округляй и не угадывай цифры и не заменяй
   необычное значение на правдоподобное (например, 15:80 на 15:00). Если символ неясен,
   укажи прочитанное и поставь status="needs_manual_review". Если видна только одна граница
   времени (например, «08:40»), верни timeRaw="08:40-" и needs_manual_review; не придумывай конец.
4. Прочерк или «Н/N/нет» означают отсутствие: timeRaw="N", status="recognized".
   Подпись преподавателя игнорируй.
5. Список кандидатов используй только для точного написания ФИО, никогда для вывода о присутствии
   или времени. При точном совпадении верни написание из списка.
6. Укажи все даты как YYYY-MM-DD. confidence — число 0..1, status — recognized или
   needs_manual_review. Не выводи пояснения, markdown, startTime, endTime или durationMinutes.
7. На изображении таблиц вверху каждой даты показан повёрнутый фрагмент рукописной даты.
   Читай число даты оттуда, сверяя с целым листом; не подменяй неясную цифру соседней датой.
   Если фото смазано, обрезано или таблицу нельзя надёжно прочитать, выставь quality.isGood=false.
"""

_UNREADABLE_TIME_MARKER: str = "?"
_ROW_ID_PATTERN = re.compile(r"^D(\d+)-R(\d+)$")
_EMPTY_TIME_INDICATORS: frozenset[str] = frozenset({"", "-", "—", "–", "нет", "прочерк", "н", "н.", "n", "n.", "0"})

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


def _get_expected_row_counts(prepared_photo: PreparedAttendancePhoto | None) -> tuple[int, ...]:
    """Return layout row counts only when the preprocessor verified the table geometry."""
    if prepared_photo is None or not prepared_photo.layout_verified:
        return ()
    return prepared_photo.expected_row_counts


def _validate_date_count(
    quality: AttendanceQualityDTO,
    actual_count: int,
    expected_row_counts: tuple[int, ...],
) -> AttendanceQualityDTO:
    """Reject a response whose date blocks cannot be aligned with detected tables."""
    if not expected_row_counts or actual_count == len(expected_row_counts):
        return quality
    return quality.model_copy(
        update={
            "is_good": False,
            "comment": "Не удалось надежно сопоставить распознанные даты с фотографией.",
            "problems": [*quality.problems, "Количество распознанных дат не совпало с разметкой листа."],
        }
    )


def _build_recognition_content(
    prepared_photo: PreparedAttendancePhoto,
    candidate_user_names: list[str],
) -> list[dict[str, Any]]:
    """Build one multimodal request with the page and contextual row evidence."""
    recognition_image = prepared_photo.normalized_image_bytes
    image_b64 = base64.b64encode(recognition_image).decode("ascii")
    image_url = f"data:{prepared_photo.mime_type};base64,{image_b64}"
    if prepared_photo.layout_verified:
        user_text = (
            "Первое изображение — выровненный исходный лист. Второе — увеличенные таблицы по датам: "
            "D1 означает верхнюю дату, D2 — следующую; над каждой таблицей есть повёрнутая рукописная дата. "
            "Верни ровно указанное число строк для каждой даты."
        )
    else:
        user_text = "Распознай лист учёта посещаемости на этом изображении."

    image_content: list[dict[str, Any]] = [
        {"type": "text", "text": user_text},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    if prepared_photo.layout_verified:
        contact_sheet_b64 = base64.b64encode(prepared_photo.row_evidence_sheet_bytes).decode("ascii")
        image_content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{contact_sheet_b64}"},
            }
        )
    image_content.extend(_build_row_crop_content(prepared_photo))
    if candidate_user_names:
        candidates_str = "\n".join(f"- {name}" for name in candidate_user_names)
        image_content[0]["text"] += (
            "\nСписок известных участников для проверки написания ФИО "
            "(порядок не соответствует строкам таблицы):\n"
            f"{candidates_str}"
        )
    return image_content


def _build_row_crop_content(prepared_photo: PreparedAttendancePhoto) -> list[dict[str, Any]]:
    """Attach each geometry-bound row image right after its identifier."""
    if not prepared_photo.rows_bound_by_geometry:
        return []
    row_crops = [row_crop for row_crop in prepared_photo.row_crops if row_crop.image_bytes]
    if not row_crops:
        return []

    row_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Далее — изображения отдельных строк. Каждое содержит ФИО одной строки и в рамке только "
                "её рукописное время; строка определена по линейкам листа. Верни по одной строке на rowId."
            ),
        }
    ]
    for row_crop in row_crops:
        row_b64 = base64.b64encode(row_crop.image_bytes).decode("ascii")
        row_content.append({"type": "text", "text": f"rowId {row_crop.row_id}"})
        row_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{row_b64}"}})
    return row_content


def _parse_row_id(raw_row: dict[str, Any]) -> tuple[int, int] | None:
    """Return (date number, row number) from a model row's rowId, if present and well formed."""
    match = _ROW_ID_PATTERN.match(str(raw_row.get("rowId", raw_row.get("row_id", ""))).strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


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

        prepared_photo = prepare_attendance_photo(photo_bytes, mime_type)
        image_content = _build_recognition_content(prepared_photo, candidate_user_names)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": image_content,
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": 4096,
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

        return self._parse_and_validate_response(response.text, prepared_photo)

    def _parse_and_validate_response(
        self,
        response_text: str,
        prepared_photo: PreparedAttendancePhoto | None = None,
    ) -> AttendanceRecognitionResultDTO:
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

        expected_row_counts = _get_expected_row_counts(prepared_photo)
        quality_dto = _validate_date_count(quality_dto, len(dates_list), expected_row_counts)
        date_blocks = self._parse_date_blocks(dates_list, prepared_photo, expected_row_counts)
        quality_dto = _validate_date_count(quality_dto, len(date_blocks), expected_row_counts)

        return AttendanceRecognitionResultDTO(
            quality=quality_dto,
            dates=date_blocks,
        )

    def _parse_date_blocks(
        self,
        dates_list: list[Any],
        prepared_photo: PreparedAttendancePhoto | None,
        expected_row_counts: tuple[int, ...],
    ) -> list[AttendanceRecognizedDateBlockDTO]:
        """Parse blocks in image order and validate each row against its table geometry."""
        date_blocks: list[AttendanceRecognizedDateBlockDTO] = []
        for block_index, raw_block in enumerate(dates_list):
            if not isinstance(raw_block, dict):
                continue
            date_block = self._parse_date_block(raw_block, block_index, prepared_photo, expected_row_counts)
            if date_block is not None:
                date_blocks.append(date_block)
        return date_blocks

    def _parse_date_block(
        self,
        raw_block: dict[str, Any],
        block_index: int,
        prepared_photo: PreparedAttendancePhoto | None,
        expected_row_counts: tuple[int, ...],
    ) -> AttendanceRecognizedDateBlockDTO | None:
        """Parse one model date block, retaining manual review when layout is uncertain."""
        try:
            block_date = date.fromisoformat(str(raw_block.get("date")))
        except (ValueError, TypeError):
            return None

        raw_rows = raw_block.get("rows", [])
        if not isinstance(raw_rows, list):
            raw_rows = []
        rows = self._process_date_rows(raw_rows, block_index, prepared_photo, expected_row_counts)
        return AttendanceRecognizedDateBlockDTO(date=block_date, rows=rows)

    def _process_date_rows(
        self,
        raw_rows: list[Any],
        block_index: int,
        prepared_photo: PreparedAttendancePhoto | None,
        expected_row_counts: tuple[int, ...],
    ) -> list[AttendanceRecognizedRowDTO]:
        """Select strict indexed parsing only when visual row geometry was verified."""
        if prepared_photo is None or not prepared_photo.layout_verified:
            return [self._mark_row_for_manual_review(row) for row in self._process_recognized_rows(raw_rows)]
        if not expected_row_counts:
            return self._process_recognized_rows(raw_rows)
        if block_index >= len(expected_row_counts):
            return [self._mark_row_for_manual_review(row) for row in self._process_recognized_rows(raw_rows)]
        if prepared_photo.rows_bound_by_geometry:
            table_rows = {
                row_crop.row_index: row_crop
                for row_crop in prepared_photo.row_crops
                if row_crop.date_index == block_index
            }
            return self._compose_geometry_bound_rows(
                raw_rows, block_index, expected_row_counts[block_index], table_rows
            )

        ink_evidence = (
            prepared_photo.time_cell_has_ink[block_index] if block_index < len(prepared_photo.time_cell_has_ink) else ()
        )
        boundary_ambiguity = (
            prepared_photo.time_cell_boundary_ambiguous[block_index]
            if block_index < len(prepared_photo.time_cell_boundary_ambiguous)
            else ()
        )
        return self._process_recognized_rows(
            raw_rows,
            expected_row_count=expected_row_counts[block_index],
            ink_evidence=ink_evidence,
            boundary_ambiguity=boundary_ambiguity,
        )

    def _compose_geometry_bound_rows(
        self,
        raw_rows: list[Any],
        block_index: int,
        expected_row_count: int,
        table_rows: dict[int, AttendanceRowCrop],
    ) -> list[AttendanceRecognizedRowDTO]:
        """Build rows from per-row images whose names and times were isolated by geometry.

        Each model row is matched to its image by ``rowId``. A row image without handwriting
        is an absence regardless of what the model says; a row with handwriting but no
        readable time stays visible as unreadable instead of becoming an absence.
        """
        row_mapping, has_mapping_conflict = self._index_rows_by_row_id(raw_rows, block_index, expected_row_count)
        rows: list[AttendanceRecognizedRowDTO] = []
        for row_index in range(1, expected_row_count + 1):
            raw_row = row_mapping.get(row_index)
            row_crop = table_rows.get(row_index)
            if raw_row is None or row_crop is None:
                row = self._empty_manual_review_row()
            elif row_crop.has_handwriting:
                row = self._bind_row_with_handwriting(raw_row, row_crop)
            else:
                row = self._bind_row_without_handwriting(raw_row)
            if has_mapping_conflict:
                row = self._mark_row_for_manual_review(row)
            rows.append(row)
        return rows

    @staticmethod
    def _index_rows_by_row_id(
        raw_rows: list[Any],
        block_index: int,
        expected_row_count: int,
    ) -> tuple[dict[int, dict[str, Any]], bool]:
        """Map model rows to row numbers by rowId; any missing, foreign or repeated id is a conflict."""
        row_mapping: dict[int, dict[str, Any]] = {}
        has_mapping_conflict = False
        for raw_row in raw_rows:
            parsed_row_id = _parse_row_id(raw_row) if isinstance(raw_row, dict) else None
            if parsed_row_id is None:
                has_mapping_conflict = True
                continue
            date_number, row_index = parsed_row_id
            if date_number != block_index + 1 or not 1 <= row_index <= expected_row_count or row_index in row_mapping:
                has_mapping_conflict = True
                continue
            row_mapping[row_index] = raw_row
        if len(row_mapping) != expected_row_count:
            has_mapping_conflict = True
        return row_mapping, has_mapping_conflict

    def _bind_row_without_handwriting(self, raw_row: dict[str, Any]) -> AttendanceRecognizedRowDTO:
        """Build an N row; a time claimed for an empty cell can only be a misreading."""
        claimed_time = str(raw_row.get("timeRaw") or raw_row.get("time_raw") or "").strip()
        absent_row = self._process_recognized_row({**raw_row, "timeRaw": "N", "isEmptyTime": True})
        if absent_row is None:
            return self._empty_manual_review_row()
        if claimed_time and claimed_time.lower() not in _EMPTY_TIME_INDICATORS:
            return self._mark_row_for_manual_review(absent_row)
        return absent_row

    def _bind_row_with_handwriting(
        self,
        raw_row: dict[str, Any],
        row_crop: AttendanceRowCrop,
    ) -> AttendanceRecognizedRowDTO:
        """Read the row's own time, failing closed when it is unreadable or ambiguous."""
        raw_time = str(raw_row.get("timeRaw") or raw_row.get("time_raw") or "").strip()
        if not raw_time or raw_time.lower() in _EMPTY_TIME_INDICATORS:
            return self._unreadable_time_row(raw_row)
        bound_row = self._process_recognized_row({**raw_row, "timeRaw": raw_time, "isEmptyTime": False})
        if bound_row is None:
            return self._unreadable_time_row(raw_row)
        if row_crop.is_ambiguous:
            return self._mark_row_for_manual_review(bound_row)
        return bound_row

    def _unreadable_time_row(self, name_row: dict[str, Any]) -> AttendanceRecognizedRowDTO:
        """Keep a detected but unread time visible as unreadable instead of absent."""
        raw_name = name_row.get("participantFullName") or name_row.get("participant_full_name")
        return AttendanceRecognizedRowDTO(
            raw_name=(str(raw_name).strip() or None) if raw_name is not None else None,
            time_raw=_UNREADABLE_TIME_MARKER,
            is_empty_time=False,
            confidence=0.0,
            status="needs_manual_review",
        )

    def _process_recognized_rows(
        self,
        raw_rows: list[Any],
        expected_row_count: int | None = None,
        ink_evidence: tuple[bool, ...] = (),
        boundary_ambiguity: tuple[bool, ...] = (),
    ) -> list[AttendanceRecognizedRowDTO]:
        """Parse rows and flag model-reported row positions that do not match their sequence."""
        if expected_row_count is not None:
            return self._process_indexed_rows(raw_rows, expected_row_count, ink_evidence, boundary_ambiguity)

        rows: list[AttendanceRecognizedRowDTO] = []
        for expected_row_index, raw_row in enumerate(raw_rows, start=1):
            if not isinstance(raw_row, dict):
                continue

            processed_row = self._process_recognized_row(raw_row)
            if processed_row is not None and not self._row_index_matches(raw_row, expected_row_index):
                processed_row = processed_row.model_copy(update={"confidence": 0.0, "status": "needs_manual_review"})
            if processed_row is not None:
                rows.append(processed_row)
        return rows

    def _process_indexed_rows(
        self,
        raw_rows: list[Any],
        expected_row_count: int,
        ink_evidence: tuple[bool, ...],
        boundary_ambiguity: tuple[bool, ...],
    ) -> list[AttendanceRecognizedRowDTO]:
        """Reorder by explicit row IDs and fail closed on missing or contradictory geometry."""
        row_mapping, has_mapping_conflict, has_any_row_index = self._index_response_rows(raw_rows, expected_row_count)
        return [
            self._process_indexed_row(
                row_index,
                row_mapping.get(row_index),
                has_mapping_conflict,
                has_any_row_index,
                ink_evidence,
                boundary_ambiguity,
            )
            for row_index in range(1, expected_row_count + 1)
        ]

    @staticmethod
    def _index_response_rows(
        raw_rows: list[Any],
        expected_row_count: int,
    ) -> tuple[dict[int, dict[str, Any]], bool, bool]:
        """Map model output to row numbers and report missing, duplicate, or invalid indexes."""
        row_mapping: dict[int, dict[str, Any]] = {}
        has_any_row_index = any(
            isinstance(raw_row, dict) and ("rowIndex" in raw_row or "row_index" in raw_row) for raw_row in raw_rows
        )
        has_mapping_conflict = len(raw_rows) != expected_row_count

        for response_index, raw_row in enumerate(raw_rows, start=1):
            if not isinstance(raw_row, dict):
                has_mapping_conflict = True
                continue

            reported_index = raw_row.get("rowIndex", raw_row.get("row_index"))
            if not has_any_row_index:
                row_index = response_index
            else:
                row_index = PolzaVisionAdapter._parse_reported_row_index(reported_index)
                if row_index is None:
                    has_mapping_conflict = True
                    continue

            if not 1 <= row_index <= expected_row_count or row_index in row_mapping:
                has_mapping_conflict = True
                continue
            row_mapping[row_index] = raw_row

        if set(row_mapping) != set(range(1, expected_row_count + 1)):
            has_mapping_conflict = True
        return row_mapping, has_mapping_conflict, has_any_row_index

    def _process_indexed_row(  # noqa: PLR0913
        self,
        row_index: int,
        raw_row: dict[str, Any] | None,
        has_mapping_conflict: bool,
        has_any_row_index: bool,
        ink_evidence: tuple[bool, ...],
        boundary_ambiguity: tuple[bool, ...],
    ) -> AttendanceRecognizedRowDTO:
        """Parse one mapped output row and verify its claimed attendance against visible ink."""
        processed_row = self._process_recognized_row(raw_row) if raw_row is not None else None
        if processed_row is None:
            processed_row = self._empty_manual_review_row()
        if has_mapping_conflict or not has_any_row_index:
            return self._mark_row_for_manual_review(processed_row)
        if row_index <= len(boundary_ambiguity) and boundary_ambiguity[row_index - 1]:
            return self._mark_row_for_manual_review(processed_row)
        if row_index <= len(ink_evidence) and self._ink_evidence_conflicts(processed_row, ink_evidence[row_index - 1]):
            return self._mark_row_for_manual_review(processed_row)
        return processed_row

    @staticmethod
    def _empty_manual_review_row() -> AttendanceRecognizedRowDTO:
        """Create a blank row placeholder that must be resolved in the draft."""
        return AttendanceRecognizedRowDTO(
            raw_name=None,
            time_raw=None,
            is_empty_time=True,
            confidence=0.0,
            status="needs_manual_review",
        )

    @staticmethod
    def _ink_evidence_conflicts(row: AttendanceRecognizedRowDTO, cell_has_ink: bool) -> bool:
        """Flag disagreement between independent pen-color evidence and model output."""
        return cell_has_ink != (not row.is_empty_time)

    @staticmethod
    def _parse_reported_row_index(raw_index: Any) -> int | None:
        """Parse a positive integer row index without silently truncating floats or booleans."""
        if isinstance(raw_index, bool):
            return None
        if isinstance(raw_index, int):
            return raw_index
        if isinstance(raw_index, str) and raw_index.isdecimal():
            return int(raw_index)
        return None

    @staticmethod
    def _mark_row_for_manual_review(row: AttendanceRecognizedRowDTO) -> AttendanceRecognizedRowDTO:
        """Keep the recognized values visible while preventing unverified auto-confirmation."""
        return row.model_copy(update={"status": "needs_manual_review"})

    @staticmethod
    def _row_index_matches(raw_row: dict[str, Any], expected_row_index: int) -> bool:
        """Check a supplied 1-based row index while accepting legacy responses without one."""
        reported_row_index = raw_row.get("rowIndex", raw_row.get("row_index"))
        if reported_row_index is None:
            return True
        return PolzaVisionAdapter._parse_reported_row_index(reported_row_index) == expected_row_index

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
        is_empty_time: bool = False,
    ) -> tuple[time | None, time | None, int, bool]:
        """Resolve start_time, end_time, duration_minutes, and whether time requires review."""
        if is_empty_time or not time_raw:
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

    def _process_recognized_row(self, raw_row: dict[str, Any]) -> AttendanceRecognizedRowDTO | None:
        """Process and validate a single recognized row, recalculating duration in Python.

        Returns an N marker for a participant with empty or absent time.
        """
        time_raw = raw_row.get("timeRaw") or raw_row.get("time_raw")
        if time_raw is not None:
            time_raw = str(time_raw).strip() or None

        is_empty_time = bool(raw_row.get("isEmptyTime", raw_row.get("is_empty_time", False)))
        raw_name = raw_row.get("participantFullName") or raw_row.get("participant_full_name") or raw_row.get("raw_name")
        if raw_name is not None:
            raw_name = str(raw_name).strip() or None

        confidence, status = self._parse_confidence_and_status(raw_row)
        if is_empty_time or not time_raw or time_raw.lower() in _EMPTY_TIME_INDICATORS:
            return AttendanceRecognizedRowDTO(
                raw_name=raw_name,
                time_raw="N",
                start_time=None,
                end_time=None,
                duration_minutes=0,
                is_empty_time=True,
                confidence=confidence,
                status=status,
            )

        start_time, end_time, duration_minutes, time_requires_review = self._resolve_row_times(
            raw_row, time_raw, is_empty_time=False
        )
        if time_requires_review:
            status = "needs_manual_review"

        return AttendanceRecognizedRowDTO(
            raw_name=raw_name,
            time_raw=time_raw,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            is_empty_time=False,
            confidence=confidence,
            status=status,
        )
