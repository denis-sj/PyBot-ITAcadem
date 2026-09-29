"""Tests for geometry-aware attendance photo preparation."""

import cv2
import numpy as np
import pytest

from pybot.infrastructure.attendance_photo_preprocessor import _correct_table_row_boundaries, prepare_attendance_photo

_ROWS_PER_DATE = 18


def _build_perspective_attendance_photo() -> bytes:
    """Create a synthetic two-date form photographed at a perspective angle."""
    page = np.full((1200, 800, 3), 238, dtype=np.uint8)
    column_lines = (24, 96, 480, 640, 776)
    table_blocks = ((70, 118), (610, 658))
    row_height = 27

    for table_index, (header_top, data_top) in enumerate(table_blocks):
        table_bottom = data_top + _ROWS_PER_DATE * row_height
        for x_coordinate in column_lines:
            cv2.line(page, (x_coordinate, header_top), (x_coordinate, table_bottom), (30, 30, 30), 2)
        cv2.line(page, (column_lines[0], header_top), (column_lines[-1], header_top), (30, 30, 30), 2)
        cv2.line(page, (column_lines[0], data_top), (column_lines[-1], data_top), (30, 30, 30), 2)
        cv2.putText(
            page,
            "Date",
            (column_lines[0] + 6, header_top + 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (30, 30, 30),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            page,
            "Teacher signature",
            (column_lines[3] + 6, header_top + 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.36,
            (30, 30, 30),
            1,
            cv2.LINE_AA,
        )

        for row_index in range(_ROWS_PER_DATE):
            row_bottom = data_top + (row_index + 1) * row_height
            cv2.line(page, (column_lines[0], row_bottom), (column_lines[-1], row_bottom), (30, 30, 30), 2)
            row_center = row_bottom - row_height // 2
            cv2.putText(
                page,
                f"Student {row_index + 1}",
                (column_lines[1] + 8, row_center + 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (30, 30, 30),
                1,
                cv2.LINE_AA,
            )

            if table_index == 0 and row_index == 5:
                cv2.putText(
                    page,
                    "10:20-15:00",
                    (column_lines[2] + 8, row_center + 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.48,
                    (170, 35, 25),
                    1,
                    cv2.LINE_AA,
                )

    canvas = np.full((1280, 960, 3), (85, 72, 58), dtype=np.uint8)
    page_corners = np.array(
        [[0, 0], [799, 0], [799, 1199], [0, 1199]],
        dtype=np.float32,
    )
    photographed_corners = np.array(
        [[42, 46], [915, 92], [948, 1232], [20, 1186]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(page_corners, photographed_corners)
    photographed_page = cv2.warpPerspective(page, transform, (960, 1280), borderValue=(85, 72, 58))
    _, encoded = cv2.imencode(".jpg", photographed_page)
    return encoded.tobytes()


def test_prepare_attendance_photo_rectifies_and_marks_rows() -> None:
    source_photo = _build_perspective_attendance_photo()
    prepared_photo = prepare_attendance_photo(source_photo, "image/jpeg")

    assert prepared_photo.layout_verified is True
    assert prepared_photo.expected_row_counts == (_ROWS_PER_DATE, _ROWS_PER_DATE)
    assert len(prepared_photo.time_cell_has_ink) == 2
    assert prepared_photo.time_cell_has_ink[0][5] is True
    assert all(not has_ink for index, has_ink in enumerate(prepared_photo.time_cell_has_ink[0]) if index != 5)
    assert not any(prepared_photo.time_cell_has_ink[1])
    assert prepared_photo.normalized_image_bytes != source_photo
    assert prepared_photo.row_evidence_sheet_bytes
    assert len(prepared_photo.time_cell_boundary_ambiguous) == 2
    assert not any(any(table_flags) for table_flags in prepared_photo.time_cell_boundary_ambiguous)
    assert prepared_photo.rows_bound_by_geometry is True
    assert len(prepared_photo.row_crops) == 2 * _ROWS_PER_DATE
    assert [row.row_id for row in prepared_photo.row_crops if row.has_handwriting] == ["D1-R06"]
    row_image_bytes = np.frombuffer(prepared_photo.row_crops[5].image_bytes, dtype=np.uint8)
    assert cv2.imdecode(row_image_bytes, cv2.IMREAD_COLOR) is not None
    evidence_sheet = cv2.imdecode(
        np.frombuffer(prepared_photo.row_evidence_sheet_bytes, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert evidence_sheet is not None
    assert evidence_sheet.shape[1] > evidence_sheet.shape[0] // 2
    assert evidence_sheet.shape[0] > _ROWS_PER_DATE * 40


@pytest.mark.parametrize("photo_bytes", [b"", b"not-an-image"])
def test_prepare_attendance_photo_falls_back_when_image_cannot_be_decoded(photo_bytes: bytes) -> None:
    prepared_photo = prepare_attendance_photo(photo_bytes, "image/jpeg")

    assert prepared_photo.layout_verified is False
    assert prepared_photo.expected_row_counts == ()
    assert prepared_photo.time_cell_has_ink == ()
    assert prepared_photo.row_crops == ()
    assert prepared_photo.rows_bound_by_geometry is False
    assert prepared_photo.normalized_image_bytes == photo_bytes
    assert prepared_photo.row_evidence_sheet_bytes == b""


def _draw_tilted_table(first_rule_is_header_text: bool) -> tuple[np.ndarray, tuple[float, ...]]:
    """Draw 18 rows whose rules drop by almost a row between the signature and name columns."""
    page = np.full((640, 1000), 238, dtype=np.uint8)
    reference_x, slope, row_height = 850, 0.03, 25
    first_rule_y = 100
    for rule_index in range(_ROWS_PER_DATE + (0 if first_rule_is_header_text else 1)):
        rule_y = first_rule_y + rule_index * row_height
        left_y = round(rule_y + slope * (reference_x - 20))
        right_y = round(rule_y - slope * (980 - reference_x))
        cv2.line(page, (20, left_y), (980, right_y), 30, 2)
    if first_rule_is_header_text:
        # Bold header words in the signature column look like a rule in the reference strip only.
        cv2.putText(page, "Teacher", (800, first_rule_y - row_height + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 30, 3)
        boundaries = tuple(float(first_rule_y + (index - 1) * row_height) for index in range(_ROWS_PER_DATE + 1))
    else:
        boundaries = tuple(float(first_rule_y + index * row_height) for index in range(_ROWS_PER_DATE + 1))
    return page, boundaries


def test_tilted_table_keeps_its_first_rule() -> None:
    page, boundaries = _draw_tilted_table(first_rule_is_header_text=False)

    assert _correct_table_row_boundaries(page, boundaries) == boundaries


def test_header_text_mistaken_for_a_rule_is_dropped() -> None:
    page, boundaries = _draw_tilted_table(first_rule_is_header_text=True)

    corrected = _correct_table_row_boundaries(page, boundaries)

    assert corrected[:-1] == boundaries[1:]
    assert corrected[-1] == boundaries[-1] + 25
