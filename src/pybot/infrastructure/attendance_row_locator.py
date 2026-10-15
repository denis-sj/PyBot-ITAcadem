"""Bind printed names and handwritten times to attendance rows by measured sheet geometry.

A vision model reads print and handwriting well but anchors them to rows by visual
proximity. On a curved or tilted sheet the printed rules bend between the name column
and the time column, and tall digits often cross the upper rule of their own row. The
model then shifts whole blocks of times, or skips a printed name, while still reporting
high confidence.

This module removes row binding from the model. It traces every row rule across the
table, separates handwriting from the printed rules, and assigns each stroke to the row
that holds most of its pixels. For every row it renders one image containing only that
row's printed name and only that row's handwriting, so the model has nothing to confuse
it with. Strokes split almost evenly between two rows are reported as ambiguous instead
of being forced into either row.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

_TABLE_X_RANGE: tuple[float, float] = (0.12, 0.83)
_NAME_COLUMN_X_RANGE: tuple[float, float] = (0.12, 0.585)
_TIME_COLUMN_X_RANGE: tuple[float, float] = (0.585, 0.83)
_RULE_TRACE_STEP: int = 8
_RULE_TRACE_SMOOTHING_RADIUS: int = 2
_OWNERSHIP_SHARE_THRESHOLD: float = 0.7
_CROP_INCLUSION_SHARE: float = 0.25
_TEXT_BAND_FIT_RATIO: float = 0.6
_COLUMN_RULE_WINDOW: int = 17
_COLUMN_RULE_COVERAGE: float = 1.2
_MIN_COMPONENT_AREA: int = 3
_MIN_ENTRY_MASS_RATIO: float = 0.12
_MIN_STROKE_MASS_RATIO: float = 0.1
_CROP_PADDING: int = 4
_ROW_IMAGE_SCALE: float = 2.0
_ROW_IMAGE_GAP: int = 24
_EMPTY_TIME_CELL_WIDTH: int = 220
_FRAME_COLOR: tuple[int, int, int] = (170, 170, 170)


@dataclass(frozen=True, slots=True)
class AttendanceRowCrop:
    """One printed row rendered in isolation: its name strip beside its own handwriting."""

    date_index: int
    row_index: int
    image_bytes: bytes
    has_handwriting: bool
    is_ambiguous: bool

    @property
    def row_id(self) -> str:
        """Return the stable identifier shown to the model next to the row image."""
        return f"D{self.date_index + 1}-R{self.row_index:02}"


@dataclass(frozen=True, slots=True)
class TableRowEvidence:
    """Row-level handwriting evidence and isolated row images for one date table."""

    row_has_ink: tuple[bool, ...]
    row_is_ambiguous: tuple[bool, ...]
    rows: tuple[AttendanceRowCrop, ...]


@dataclass(frozen=True, slots=True)
class _Stroke:
    """Connected handwriting component with each pixel's row and position inside it."""

    stroke_ys: NDArray[np.intp]
    stroke_xs: NDArray[np.intp]
    pixel_rows: NDArray[np.intp]
    row_positions: NDArray[np.float64]
    row_mass: NDArray[np.int64]

    @property
    def area(self) -> int:
        return len(self.stroke_ys)

    @property
    def owner_row(self) -> int:
        return int(np.argmax(self.row_mass))

    @property
    def owner_share(self) -> float:
        return float(self.row_mass[self.owner_row] / max(1, self.area))

    def rows_with_share(self, minimum_share: float) -> list[int]:
        """Return rows that hold at least the given share of this stroke's pixels."""
        return [row for row, mass in enumerate(self.row_mass) if mass / max(1, self.area) >= minimum_share]


@dataclass(slots=True)
class _RowAssignment:
    """Mutable accumulator of pixels and review flags while strokes are assigned."""

    row_count: int
    owned_mass: NDArray[np.int64]
    ambiguous_rows: NDArray[np.bool_]
    crop_pixels: dict[int, list[tuple[NDArray[np.intp], NDArray[np.intp]]]]

    @classmethod
    def empty(cls, row_count: int) -> _RowAssignment:
        return cls(
            row_count=row_count,
            owned_mass=np.zeros(row_count + 2, dtype=np.int64),
            ambiguous_rows=np.zeros(row_count + 2, dtype=bool),
            crop_pixels={},
        )

    def assign(self, row: int, stroke_ys: NDArray[np.intp], stroke_xs: NDArray[np.intp]) -> None:
        self.owned_mass[row] += len(stroke_ys)
        self.show(row, stroke_ys, stroke_xs)

    def show(self, row: int, stroke_ys: NDArray[np.intp], stroke_xs: NDArray[np.intp]) -> None:
        if 1 <= row <= self.row_count:
            self.crop_pixels.setdefault(row, []).append((stroke_ys, stroke_xs))


def build_table_row_evidence(
    color_image: NDArray[np.uint8],
    gray_image: NDArray[np.uint8],
    time_column_boundaries: tuple[float, ...],
    date_index: int,
) -> TableRowEvidence:
    """Bind handwriting to rows by pixel ownership and render every row in isolation.

    Args:
        color_image: Rectified page in BGR.
        gray_image: Grayscale version of the rectified page.
        time_column_boundaries: Row rule positions measured near the time column,
            one more value than the number of rows.
        date_index: Zero-based index of the date table on the page.

    Returns:
        TableRowEvidence: Presence and ambiguity flags per row plus one image per row.
    """
    row_count = len(time_column_boundaries) - 1
    row_height = float(np.median(np.diff(np.asarray(time_column_boundaries))))
    image_width = gray_image.shape[1]
    time_x_start, time_x_end = _fractional_range(image_width, _TIME_COLUMN_X_RANGE)
    name_x_start, name_x_end = _fractional_range(image_width, _NAME_COLUMN_X_RANGE)

    dark_pixels = _binarize_dark_pixels(gray_image, row_height)
    rules = _trace_row_rules(dark_pixels, time_column_boundaries, (time_x_start + time_x_end) // 2, row_height)
    band_half_width = max(
        round(row_height * 0.09),
        _measure_rule_half_thickness(dark_pixels, rules, time_x_start, time_x_end, row_height),
    )
    handwriting = _extract_handwriting(dark_pixels, rules, time_x_start, time_x_end, row_height, band_half_width)
    strokes = _measure_strokes(handwriting, rules, row_count)
    assignment = _assign_strokes_to_rows(strokes, row_count, _MIN_STROKE_MASS_RATIO * row_height * row_height)

    minimum_entry_mass = _MIN_ENTRY_MASS_RATIO * row_height * row_height
    row_has_ink = tuple(bool(assignment.owned_mass[row] >= minimum_entry_mass) for row in range(1, row_count + 1))
    row_is_ambiguous = tuple(bool(assignment.ambiguous_rows[row]) for row in range(1, row_count + 1))
    rows: list[AttendanceRowCrop] = []
    for row in range(1, row_count + 1):
        has_handwriting = row_has_ink[row - 1] or row_is_ambiguous[row - 1]
        name_strip = _render_name_strip(color_image, rules, row, name_x_start, name_x_end, band_half_width)
        handwriting_image = (
            _render_handwriting(color_image, assignment.crop_pixels.get(row, [])) if has_handwriting else None
        )
        rows.append(
            AttendanceRowCrop(
                date_index=date_index,
                row_index=row,
                image_bytes=_compose_row_image(name_strip, handwriting_image),
                has_handwriting=has_handwriting,
                is_ambiguous=row_is_ambiguous[row - 1],
            )
        )
    return TableRowEvidence(row_has_ink=row_has_ink, row_is_ambiguous=row_is_ambiguous, rows=tuple(rows))


def measure_rule_continuity(
    gray_image: NDArray[np.uint8],
    line_y: float,
    row_height: float,
    reference_x: int,
    target_x: int,
) -> float:
    """Follow a detected line from where it was found and report how much of it is a rule.

    A printed rule stays continuous along the whole walk even on a bent page, while a
    line of header text breaks up after a few steps. The result is the share of steps
    with rule support, from 0 to 1.
    """
    horizontal_lines = _horizontal_print_runs(_binarize_dark_pixels(gray_image, row_height), row_height)
    step = _RULE_TRACE_STEP if target_x >= reference_x else -_RULE_TRACE_STEP
    sample_xs = [int(x) for x in range(reference_x, target_x, step)]
    _, supported_steps = _walk_rule(horizontal_lines, line_y, sample_xs, max(4, int(row_height * 0.2)))
    return supported_steps / max(1, len(sample_xs))


def _assign_strokes_to_rows(
    strokes: list[_Stroke],
    row_count: int,
    minimum_stroke_mass: float,
) -> _RowAssignment:
    """Assign confident strokes whole, split touching strokes, and flag the rest.

    A component shared by two rows is split along the rule only when each part sits
    inside the handwriting band of its own row, measured from that row's other,
    unambiguous strokes. This separates two digits that merely touch across a rule,
    while a stroke that genuinely straddles two rows keeps both rows in manual review.
    """
    assignment = _RowAssignment.empty(row_count)
    uncertain_strokes: list[_Stroke] = []
    for stroke in strokes:
        if stroke.owner_share >= _OWNERSHIP_SHARE_THRESHOLD or stroke.area < minimum_stroke_mass:
            assignment.assign(stroke.owner_row, stroke.stroke_ys, stroke.stroke_xs)
        else:
            uncertain_strokes.append(stroke)

    text_bands = _measure_text_bands(strokes, minimum_stroke_mass)
    for stroke in uncertain_strokes:
        shared_rows = stroke.rows_with_share(_CROP_INCLUSION_SHARE)
        if all(_part_fits_text_band(stroke, row, text_bands.get(row)) for row in shared_rows):
            for row in range(row_count + 2):
                part = stroke.pixel_rows == row
                if part.any():
                    target_row = row if row in shared_rows else stroke.owner_row
                    assignment.assign(target_row, stroke.stroke_ys[part], stroke.stroke_xs[part])
            continue

        assignment.owned_mass[stroke.owner_row] += stroke.area
        for row in shared_rows:
            assignment.ambiguous_rows[row] = True
            assignment.show(row, stroke.stroke_ys, stroke.stroke_xs)
    return assignment


def _measure_text_bands(strokes: list[_Stroke], minimum_stroke_mass: float) -> dict[int, tuple[float, float]]:
    """Measure where handwriting normally sits inside each row, from 0 (top rule) to 1."""
    positions_by_row: dict[int, list[NDArray[np.float64]]] = {}
    for stroke in strokes:
        if stroke.owner_share < _OWNERSHIP_SHARE_THRESHOLD or stroke.area < minimum_stroke_mass:
            continue
        own_pixels = stroke.pixel_rows == stroke.owner_row
        positions_by_row.setdefault(stroke.owner_row, []).append(stroke.row_positions[own_pixels])
    return {
        row: (float(np.percentile(np.concatenate(parts), 5)), float(np.percentile(np.concatenate(parts), 95)))
        for row, parts in positions_by_row.items()
    }


def _part_fits_text_band(stroke: _Stroke, row: int, text_band: tuple[float, float] | None) -> bool:
    """Check that the stroke's part in a row lies inside that row's handwriting band."""
    if text_band is None:
        return False
    part_positions = stroke.row_positions[stroke.pixel_rows == row]
    part_top, part_bottom = float(np.percentile(part_positions, 10)), float(np.percentile(part_positions, 90))
    overlap = min(part_bottom, text_band[1]) - max(part_top, text_band[0])
    return overlap >= _TEXT_BAND_FIT_RATIO * max(part_bottom - part_top, 1e-6)


def _binarize_dark_pixels(gray_image: NDArray[np.uint8], row_height: float) -> NDArray[np.uint8]:
    """Mark pen and print pixels independently of pen color and uneven lighting."""
    block_size = max(15, int(row_height * 1.4)) | 1
    return np.asarray(
        cv2.adaptiveThreshold(gray_image, 1, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block_size, 12),
        dtype=np.uint8,
    )


def _trace_row_rules(
    dark_pixels: NDArray[np.uint8],
    boundaries: tuple[float, ...],
    reference_x: int,
    row_height: float,
) -> NDArray[np.float64]:
    """Follow each printed row rule across the whole table on a bent or tilted page.

    The rules are seeded at ``reference_x``, where ``boundaries`` were measured. Returns an
    array of shape (len(boundaries), image_width) with the center line of each rule for
    every x coordinate.
    """
    image_height, image_width = dark_pixels.shape
    horizontal_lines = _horizontal_print_runs(dark_pixels, row_height)
    search_radius = max(4, int(row_height * 0.2))
    table_x_start, table_x_end = _fractional_range(image_width, _TABLE_X_RANGE)
    sample_xs = np.arange(table_x_start, table_x_end, _RULE_TRACE_STEP)
    reference_x = int(sample_xs[np.argmin(np.abs(sample_xs - reference_x))])

    rules = np.zeros((len(boundaries), image_width), dtype=np.float64)
    for rule_index, seed_y in enumerate(boundaries):
        rightward, _ = _walk_rule(
            horizontal_lines, seed_y, [int(x) for x in sample_xs if x >= reference_x], search_radius
        )
        leftward, _ = _walk_rule(
            horizontal_lines, seed_y, [int(x) for x in sample_xs[::-1] if x < reference_x], search_radius
        )
        traced = {**rightward, **leftward}
        rule_ys = np.asarray([traced[int(x)] for x in sample_xs], dtype=np.float64)
        radius = _RULE_TRACE_SMOOTHING_RADIUS
        smoothed_ys = np.asarray(
            [np.median(rule_ys[max(0, index - radius) : index + radius + 1]) for index in range(len(rule_ys))]
        )
        rules[rule_index] = np.clip(
            np.interp(np.arange(image_width), sample_xs + _RULE_TRACE_STEP / 2, smoothed_ys), 0, image_height - 1
        )
    return rules


def _horizontal_print_runs(dark_pixels: NDArray[np.uint8], row_height: float) -> NDArray[np.uint8]:
    """Keep only horizontal runs long enough to be printed rules rather than pen strokes."""
    kernel_width = max(9, int(row_height * 0.8))
    return np.asarray(
        cv2.morphologyEx(dark_pixels, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1))),
        dtype=np.uint8,
    )


def _walk_rule(
    horizontal_lines: NDArray[np.uint8],
    seed_y: float,
    sample_xs: list[int],
    search_radius: int,
) -> tuple[dict[int, float], int]:
    """Step along one rule, keeping the previous position where the rule is interrupted.

    Returns the rule position at every sample and the number of samples with rule support.
    """
    image_height = horizontal_lines.shape[0]
    current_y = seed_y
    positions: dict[int, float] = {}
    supported_steps = 0
    for sample_x in sample_xs:
        y_start = max(0, int(current_y) - search_radius)
        y_end = min(image_height, int(current_y) + search_radius + 1)
        support = horizontal_lines[y_start:y_end, sample_x : sample_x + _RULE_TRACE_STEP].sum(axis=1)
        if support.size and support.max() >= _RULE_TRACE_STEP * 0.5:
            line_rows = np.flatnonzero(support >= support.max() * 0.8)
            current_y = float(y_start + line_rows.mean())
            supported_steps += 1
        positions[int(sample_x)] = current_y
    return positions, supported_steps


def _measure_rule_half_thickness(
    dark_pixels: NDArray[np.uint8],
    rules: NDArray[np.float64],
    x_start: int,
    x_end: int,
    row_height: float,
) -> int:
    """Estimate how far printed rule pixels extend from the traced center line."""
    image_height = dark_pixels.shape[0]
    search_radius = max(3, int(row_height * 0.2))
    sample_xs = np.arange(x_start, x_end, _RULE_TRACE_STEP)
    thicknesses: list[int] = []
    for rule_ys in rules:
        for sample_x in sample_xs:
            center_y = round(rule_ys[sample_x])
            top_y = max(0, center_y - search_radius)
            column = dark_pixels[top_y : min(image_height, center_y + search_radius + 1), sample_x]
            center_index = center_y - top_y
            if 0 <= center_index < len(column) and column[center_index]:
                thicknesses.append(_run_length_through_center(column, center_index))
    if not thicknesses:
        return 2
    return max(2, int(np.ceil(float(np.median(thicknesses)) / 2)) + 1)


def _run_length_through_center(column: NDArray[np.uint8], center_index: int) -> int:
    """Count consecutive dark pixels through the center of a vertical sample."""
    top = center_index
    while top > 0 and column[top - 1]:
        top -= 1
    bottom = center_index
    while bottom < len(column) - 1 and column[bottom + 1]:
        bottom += 1
    return bottom - top + 1


def _extract_handwriting(  # noqa: PLR0913
    dark_pixels: NDArray[np.uint8],
    rules: NDArray[np.float64],
    x_start: int,
    x_end: int,
    row_height: float,
    band_half_width: int,
) -> NDArray[np.uint8]:
    """Remove printed rules but keep strokes connected where they cross a rule."""
    image_height = dark_pixels.shape[0]
    y_coordinates = np.arange(image_height, dtype=np.float64)[:, None]
    rule_band = np.zeros_like(dark_pixels)
    rule_neighborhood = np.zeros_like(dark_pixels)
    for rule_ys in rules:
        distance_to_rule = np.abs(y_coordinates - rule_ys[None, :])
        rule_band |= (distance_to_rule <= band_half_width).astype(np.uint8)
        rule_neighborhood |= (distance_to_rule <= row_height * 0.25).astype(np.uint8)
    # Doubled or thick borders between tables leave long printed runs next to the traced rule.
    rule_band |= _horizontal_print_runs(dark_pixels, row_height) & rule_neighborhood

    y_start = max(0, int(rules[0].min() - row_height))
    y_end = min(image_height, int(rules[-1].max() + row_height))
    vertical_band = _find_column_rules(dark_pixels, y_start, y_end, row_height)

    handwriting = dark_pixels & (1 - rule_band) & (1 - vertical_band)
    bridge_reach = 2 * band_half_width + 2
    vertical_reach = np.ones((bridge_reach, 1), dtype=np.uint8)
    stroke_above = np.asarray(cv2.dilate(handwriting, vertical_reach, anchor=(0, bridge_reach - 1)), dtype=np.uint8)
    stroke_below = np.asarray(cv2.dilate(handwriting, vertical_reach, anchor=(0, 0)), dtype=np.uint8)
    crossing_strokes = rule_band & stroke_above & stroke_below & (1 - vertical_band)
    handwriting |= crossing_strokes & dark_pixels

    region = np.zeros_like(handwriting)
    region[y_start:y_end, x_start:x_end] = 1
    return np.asarray(handwriting & region, dtype=np.uint8)


def _find_column_rules(
    dark_pixels: NDArray[np.uint8],
    y_start: int,
    y_end: int,
    row_height: float,
) -> NDArray[np.uint8]:
    """Mask printed column borders, which run through most of the table height.

    Short vertical segments are collected first so that a slightly tilted border still
    counts; only x ranges where such segments cover most of the table are treated as
    borders, so a tall handwritten "1" or two touching digits are never erased.
    """
    segment_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(5, int(row_height))))
    vertical_segments = np.asarray(cv2.morphologyEx(dark_pixels, cv2.MORPH_OPEN, segment_kernel), dtype=np.uint8)
    table_height = max(1, y_end - y_start)
    column_coverage = vertical_segments[y_start:y_end].sum(axis=0).astype(np.float64)
    window = _COLUMN_RULE_WINDOW
    windowed_coverage = np.convolve(column_coverage, np.ones(window), mode="same")
    border_columns = windowed_coverage >= _COLUMN_RULE_COVERAGE * table_height

    column_mask = np.zeros_like(dark_pixels)
    column_mask[:, border_columns] = 1
    column_mask = np.asarray(cv2.dilate(column_mask, np.ones((1, window), dtype=np.uint8)), dtype=np.uint8)
    return np.asarray(cv2.dilate(vertical_segments & column_mask, np.ones((3, 5), dtype=np.uint8)), dtype=np.uint8)


def _measure_strokes(
    handwriting: NDArray[np.uint8],
    rules: NDArray[np.float64],
    row_count: int,
) -> list[_Stroke]:
    """Split handwriting into components and locate every pixel between traced rules.

    Row 0 lies above the table and row ``row_count + 1`` below it.
    """
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(handwriting, connectivity=8)
    strokes: list[_Stroke] = []
    for label in range(1, component_count):
        if stats[label, cv2.CC_STAT_AREA] < _MIN_COMPONENT_AREA:
            continue
        stroke_ys, stroke_xs = np.nonzero(labels == label)
        pixel_rows = (stroke_ys[None, :] >= rules[:, stroke_xs]).sum(axis=0)
        row_mass = np.bincount(pixel_rows, minlength=row_count + 2)[: row_count + 2].astype(np.int64)
        if not row_mass[1 : row_count + 1].any():
            continue
        strokes.append(
            _Stroke(
                stroke_ys=stroke_ys,
                stroke_xs=stroke_xs,
                pixel_rows=pixel_rows,
                row_positions=_positions_inside_rows(stroke_ys, stroke_xs, pixel_rows, rules),
                row_mass=row_mass,
            )
        )
    return strokes


def _positions_inside_rows(
    stroke_ys: NDArray[np.intp],
    stroke_xs: NDArray[np.intp],
    pixel_rows: NDArray[np.intp],
    rules: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Express pixel height relative to its row: 0 on the upper rule, 1 on the lower rule."""
    upper_rule = rules[np.clip(pixel_rows - 1, 0, len(rules) - 1), stroke_xs]
    lower_rule = rules[np.clip(pixel_rows, 0, len(rules) - 1), stroke_xs]
    row_heights = np.maximum(lower_rule - upper_rule, 1.0)
    return np.asarray((stroke_ys - upper_rule) / row_heights, dtype=np.float64)


def _render_name_strip(  # noqa: PLR0913
    color_image: NDArray[np.uint8],
    rules: NDArray[np.float64],
    row: int,
    x_start: int,
    x_end: int,
    band_half_width: int,
) -> NDArray[np.uint8]:
    """Cut the printed name of one row, whitening everything outside its two traced rules."""
    image_height = color_image.shape[0]
    upper_rule = rules[row - 1, x_start:x_end]
    lower_rule = rules[row, x_start:x_end]
    y_start = max(0, int(np.floor(upper_rule.min())))
    y_end = min(image_height, int(np.ceil(lower_rule.max())) + 1)
    y_coordinates = np.arange(y_start, y_end, dtype=np.float64)[:, None]
    inside_row = (y_coordinates > upper_rule[None, :] + band_half_width) & (
        y_coordinates < lower_rule[None, :] - band_half_width
    )
    strip = np.full((y_end - y_start, x_end - x_start, 3), 255, dtype=np.uint8)
    strip[inside_row] = color_image[y_start:y_end, x_start:x_end][inside_row]
    return np.asarray(
        cv2.resize(strip, (0, 0), fx=_ROW_IMAGE_SCALE, fy=_ROW_IMAGE_SCALE, interpolation=cv2.INTER_CUBIC),
        dtype=np.uint8,
    )


def _render_handwriting(
    color_image: NDArray[np.uint8],
    pixel_groups: list[tuple[NDArray[np.intp], NDArray[np.intp]]],
) -> NDArray[np.uint8] | None:
    """Draw only this row's strokes on white so neighbor rows and rules cannot be misread."""
    if not pixel_groups:
        return None
    stroke_ys = np.concatenate([group_ys for group_ys, _ in pixel_groups])
    stroke_xs = np.concatenate([group_xs for _, group_xs in pixel_groups])
    image_height, image_width = color_image.shape[:2]
    y_start = max(0, int(stroke_ys.min()) - _CROP_PADDING)
    y_end = min(image_height, int(stroke_ys.max()) + _CROP_PADDING + 1)
    x_start = max(0, int(stroke_xs.min()) - _CROP_PADDING)
    x_end = min(image_width, int(stroke_xs.max()) + _CROP_PADDING + 1)

    stroke_mask = np.zeros((y_end - y_start, x_end - x_start), dtype=np.uint8)
    stroke_mask[stroke_ys - y_start, stroke_xs - x_start] = 1
    visible_mask = cv2.dilate(stroke_mask, np.ones((3, 3), dtype=np.uint8)) == 1
    crop = np.full((y_end - y_start, x_end - x_start, 3), 255, dtype=np.uint8)
    crop[visible_mask] = color_image[y_start:y_end, x_start:x_end][visible_mask]
    return crop


def _compose_row_image(name_strip: NDArray[np.uint8], handwriting: NDArray[np.uint8] | None) -> bytes:
    """Place the row's name and its framed time cell side by side, as on the sheet."""
    cell_height = name_strip.shape[0]
    if handwriting is None:
        time_cell = np.full((cell_height, _EMPTY_TIME_CELL_WIDTH, 3), 255, dtype=np.uint8)
    else:
        scale = cell_height / max(1, handwriting.shape[0])
        time_cell = np.asarray(
            cv2.resize(handwriting, (max(1, round(handwriting.shape[1] * scale)), cell_height)),
            dtype=np.uint8,
        )
    cv2.rectangle(time_cell, (0, 0), (time_cell.shape[1] - 1, cell_height - 1), _FRAME_COLOR, 1)
    gap = np.full((cell_height, _ROW_IMAGE_GAP, 3), 255, dtype=np.uint8)
    row_image = np.hstack([name_strip, gap, time_cell])
    succeeded, encoded = cv2.imencode(".png", row_image)
    return encoded.tobytes() if succeeded else b""


def _fractional_range(image_width: int, bounds: tuple[float, float]) -> tuple[int, int]:
    """Convert normalized horizontal bounds to a non-empty pixel range."""
    start = max(0, min(image_width - 1, int(image_width * bounds[0])))
    end = max(start + 1, min(image_width, int(image_width * bounds[1])))
    return start, end
