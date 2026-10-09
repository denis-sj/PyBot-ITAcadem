"""Regression tests for binding printed names and handwritten times to attendance rows.

The synthetic sheets reproduce the geometry that shifted real recognitions by one row:
tall digits crossing the upper rule of their own row, rules tilted across the time
column, and digits of neighboring rows touching across a rule. No real photo or personal
data is used.
"""

import cv2
import numpy as np
from numpy.typing import NDArray

from pybot.infrastructure.attendance_row_locator import TableRowEvidence, build_table_row_evidence

_ROW_HEIGHT = 30
_ROW_COUNT = 8
_PAGE_WIDTH = 1000
_TABLE_TOP = 60
_TIME_COLUMN_LEFT = 580
_TIME_COLUMN_RIGHT = 838
_TEXT_X = 600
_PAPER_COLOR = (236, 236, 236)
_PRINT_COLOR = (35, 35, 35)
_PEN_COLOR = (120, 45, 30)


def _row_rule_y(rule_index: int) -> int:
    return _TABLE_TOP + rule_index * _ROW_HEIGHT


def _blank_sheet() -> NDArray[np.uint8]:
    """Draw an empty table: 8 rows with a bordered time column."""
    page_height = _TABLE_TOP + _ROW_COUNT * _ROW_HEIGHT + 80
    sheet = np.full((page_height, _PAGE_WIDTH, 3), _PAPER_COLOR, dtype=np.uint8)
    for rule_index in range(_ROW_COUNT + 1):
        rule_y = _row_rule_y(rule_index)
        cv2.line(sheet, (0, rule_y), (_PAGE_WIDTH - 1, rule_y), _PRINT_COLOR, 2)
    for column_x in (_TIME_COLUMN_LEFT, _TIME_COLUMN_RIGHT):
        cv2.line(sheet, (column_x, _row_rule_y(0)), (column_x, _row_rule_y(_ROW_COUNT)), _PRINT_COLOR, 2)
    return sheet


def _write_time(
    sheet: NDArray[np.uint8],
    text: str,
    baseline_y: int,
    scale: float = 1.0,
    left_x: int = _TEXT_X,
) -> None:
    """Imitate a handwritten time with a script font and a thick pen stroke."""
    cv2.putText(sheet, text, (left_x, baseline_y), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, scale, _PEN_COLOR, 2, cv2.LINE_AA)


def _shear_sheet(sheet: NDArray[np.uint8], slope: float) -> NDArray[np.uint8]:
    """Tilt rules and handwriting together, pivoting on the time column center."""
    pivot_x = (_TIME_COLUMN_LEFT + _TIME_COLUMN_RIGHT) / 2
    transform = np.array([[1.0, 0.0, 0.0], [slope, 1.0, -slope * pivot_x]], dtype=np.float64)
    sheared = cv2.warpAffine(sheet, transform, (sheet.shape[1], sheet.shape[0]), borderValue=_PAPER_COLOR)
    return np.asarray(sheared, dtype=np.uint8)


def _locate(sheet: NDArray[np.uint8]) -> TableRowEvidence:
    gray_sheet = np.asarray(cv2.cvtColor(sheet, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
    boundaries = tuple(float(_row_rule_y(rule_index)) for rule_index in range(_ROW_COUNT + 1))
    return build_table_row_evidence(sheet, gray_sheet, boundaries, date_index=0)


def _rows_with_ink(row_flags: tuple[bool, ...]) -> list[int]:
    return [row_index for row_index, has_ink in enumerate(row_flags, start=1) if has_ink]


def test_tall_digits_crossing_upper_rule_stay_in_row_of_their_baseline() -> None:
    sheet = _blank_sheet()
    # Row 4 is the participant's row; the digits are taller than the row and poke into row 3.
    _write_time(sheet, "14:26-19:00", baseline_y=_row_rule_y(4) - 3, scale=1.45)

    table = _locate(sheet)

    assert _rows_with_ink(table.row_has_ink) == [4]
    assert not any(table.row_is_ambiguous)
    assert [row.row_id for row in table.rows if row.has_handwriting] == ["D1-R04"]
    assert len(table.rows) == _ROW_COUNT
    assert all(row.image_bytes for row in table.rows)


def test_rules_tilted_across_time_column_are_traced_before_binding() -> None:
    sheet = _blank_sheet()
    _write_time(sheet, "14:26-19:00", baseline_y=_row_rule_y(3) - 4)
    _write_time(sheet, "11:15-15:20", baseline_y=_row_rule_y(6) - 4)
    # Near the column edges a horizontal row band would be off by almost half a row.
    tilted_sheet = _shear_sheet(sheet, slope=0.11)

    table = _locate(tilted_sheet)

    assert _rows_with_ink(table.row_has_ink) == [3, 6]
    assert not any(table.row_is_ambiguous)


def test_digits_touching_across_rule_are_split_between_their_rows() -> None:
    sheet = _blank_sheet()
    _write_time(sheet, "4:26-19:00", baseline_y=_row_rule_y(4) - 3, left_x=_TEXT_X + 24)
    _write_time(sheet, "0:20-15:80", baseline_y=_row_rule_y(5) - 3, left_x=_TEXT_X + 24)
    # The leading "1" of both times, written one under the other so that they merge on the rule.
    touching_x = _TEXT_X + 14
    cv2.line(sheet, (touching_x, _row_rule_y(3) + 8), (touching_x, _row_rule_y(5) - 4), _PEN_COLOR, 3)

    table = _locate(sheet)

    assert _rows_with_ink(table.row_has_ink) == [4, 5]
    assert not any(table.row_is_ambiguous)


def test_handwriting_straddling_a_rule_marks_both_rows_for_review() -> None:
    sheet = _blank_sheet()
    # The text body is split evenly by the rule between rows 5 and 6.
    _write_time(sheet, "8:40-", baseline_y=_row_rule_y(5) + 11)

    table = _locate(sheet)

    ambiguous_rows = [row_index for row_index, flag in enumerate(table.row_is_ambiguous, start=1) if flag]
    assert ambiguous_rows == [5, 6]
    assert {row.row_index for row in table.rows if row.is_ambiguous} == {5, 6}


def test_empty_time_column_produces_no_entries() -> None:
    table = _locate(_blank_sheet())

    assert not any(table.row_has_ink)
    assert not any(table.row_is_ambiguous)
    assert not any(row.has_handwriting for row in table.rows)
