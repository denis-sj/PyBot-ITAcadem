"""Rectify attendance photos and bind handwritten time entries to their printed rows."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import cv2
import numpy as np
from numpy.typing import NDArray

from .attendance_row_locator import (
    AttendanceRowCrop,
    TableRowEvidence,
    build_table_row_evidence,
    measure_rule_continuity,
)

_ROWS_PER_DATE: int = 18
_MAX_PAGE_SIDE: int = 1800
_MAX_DATE_TABLES: int = 4
_PAGE_CORNER_COUNT: int = 4
_MIN_PAGE_DIMENSION: int = 200
_MIN_REFERENCE_STRIP_WIDTH: int = 8
_MIN_ROW_LINE_GROUP_HEIGHT: int = 6
_ROW_HEIGHT_VARIATION_THRESHOLD: float = 0.16
_NAME_COLUMN_X_RANGE: tuple[float, float] = (0.13, 0.58)
_RULE_CONTINUITY_RATIO: float = 0.8
_ROW_EVIDENCE_X_RANGE: tuple[float, float] = (0.12, 0.82)
_DATE_EVIDENCE_X_RANGE: tuple[float, float] = (0.055, 0.125)
_ROW_LINE_REFERENCE_X_RANGE: tuple[float, float] = (0.84, 0.86)
_TIME_LINE_REFERENCE_X_RANGE: tuple[float, float] = (0.70, 0.72)
_EVIDENCE_SCALE: float = 2.0
_EVIDENCE_LABEL_WIDTH: int = 105
_EVIDENCE_DATE_HEIGHT: int = 135
_EVIDENCE_TABLE_PADDING: int = 16


@dataclass(frozen=True, slots=True)
class PreparedAttendancePhoto:
    """Recognition input plus verified row geometry and geometry-bound handwriting.

    Attributes:
        time_cell_has_ink: Per date table, whether geometry found handwriting owned by each row.
        time_cell_boundary_ambiguous: Per date table, rows whose handwriting could not be
            assigned to a single row.
        row_crops: One isolated image per printed row, showing only that row's name and
            only the handwriting that geometry assigned to it.
        rows_bound_by_geometry: True when ``row_crops`` were measured from the sheet; the
            recognition adapter must then take row binding from geometry and use the model
            only to read each row image.
    """

    normalized_image_bytes: bytes
    mime_type: str
    row_evidence_sheet_bytes: bytes
    layout_verified: bool
    expected_row_counts: tuple[int, ...]
    time_cell_has_ink: tuple[tuple[bool, ...], ...]
    time_cell_boundary_ambiguous: tuple[tuple[bool, ...], ...] = ()
    row_crops: tuple[AttendanceRowCrop, ...] = ()
    rows_bound_by_geometry: bool = False


def prepare_attendance_photo(photo_bytes: bytes, mime_type: str) -> PreparedAttendancePhoto:
    """Correct page perspective and label each detected attendance row.

    If the page or its fixed 18-row tables cannot be identified confidently, the
    original image is returned. Callers can then force all model results to manual
    review instead of trusting an unverified row mapping.
    """
    fallback = PreparedAttendancePhoto(
        normalized_image_bytes=photo_bytes,
        mime_type=mime_type,
        row_evidence_sheet_bytes=b"",
        layout_verified=False,
        expected_row_counts=(),
        time_cell_has_ink=(),
    )
    image = _decode_photo(photo_bytes)
    if image is None:
        return fallback

    try:
        return _prepare_decoded_photo(image, fallback)
    except cv2.error:
        return fallback


def _decode_photo(photo_bytes: bytes) -> NDArray[np.uint8] | None:
    """Decode uploaded bytes into an OpenCV color image."""
    if not photo_bytes:
        return None
    encoded_source = np.frombuffer(photo_bytes, dtype=np.uint8)
    decoded_image = cv2.imdecode(encoded_source, cv2.IMREAD_COLOR)
    if decoded_image is None:
        return None
    return np.asarray(decoded_image, dtype=np.uint8)


def _prepare_decoded_photo(
    image: NDArray[np.uint8],
    fallback: PreparedAttendancePhoto,
) -> PreparedAttendancePhoto:
    """Build normalized recognition inputs after successful image decoding."""
    rectified_image = _rectify_page(image)
    if rectified_image is None:
        return fallback

    gray_image = np.asarray(cv2.cvtColor(rectified_image, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
    threshold, _ = cv2.threshold(gray_image, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    row_boundaries = _find_date_row_boundaries(gray_image, threshold)
    if not row_boundaries:
        return fallback

    time_column_boundaries = tuple(
        _adjust_boundaries_for_time_column(gray_image, boundaries, threshold) for boundaries in row_boundaries
    )
    table_evidence: tuple[TableRowEvidence, ...] = tuple(
        build_table_row_evidence(rectified_image, gray_image, boundaries, date_index)
        for date_index, boundaries in enumerate(time_column_boundaries)
    )
    evidence_sheet = _build_row_evidence_sheet(rectified_image, row_boundaries)
    if not evidence_sheet:
        return fallback

    _draw_row_labels(rectified_image, row_boundaries)
    normalized_bytes = _encode_jpeg(rectified_image)
    if not normalized_bytes:
        return fallback

    return PreparedAttendancePhoto(
        normalized_image_bytes=normalized_bytes,
        mime_type="image/jpeg",
        row_evidence_sheet_bytes=evidence_sheet,
        layout_verified=True,
        expected_row_counts=tuple(_ROWS_PER_DATE for _ in row_boundaries),
        time_cell_has_ink=tuple(table.row_has_ink for table in table_evidence),
        time_cell_boundary_ambiguous=tuple(table.row_is_ambiguous for table in table_evidence),
        row_crops=tuple(row for table in table_evidence for row in table.rows),
        rows_bound_by_geometry=True,
    )


def _rectify_page(image: NDArray[np.uint8]) -> NDArray[np.uint8] | None:
    """Find the paper outline and warp the sheet to a flat rectangular image."""
    image_height, image_width = image.shape[:2]
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    softened_image = cv2.GaussianBlur(gray_image, (5, 5), 0)
    _, light_regions = cv2.threshold(softened_image, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(light_regions, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour_areas = [cv2.contourArea(contour) for contour in contours]
    largest_contour = contours[int(np.argmax(contour_areas))]
    paper_hull = cv2.convexHull(largest_contour)
    perimeter = cv2.arcLength(paper_hull, True)
    paper_corners = cv2.approxPolyDP(paper_hull, 0.01 * perimeter, True).reshape(-1, 2)
    if len(paper_corners) != _PAGE_CORNER_COUNT:
        return None

    source_points = _order_page_corners(paper_corners.astype(np.float32))
    page_area = abs(cv2.contourArea(source_points))
    if page_area < image_width * image_height * 0.3:
        return None

    top_width = float(np.linalg.norm(source_points[1] - source_points[0]))
    bottom_width = float(np.linalg.norm(source_points[2] - source_points[3]))
    right_height = float(np.linalg.norm(source_points[2] - source_points[1]))
    left_height = float(np.linalg.norm(source_points[3] - source_points[0]))
    target_width = int(max(top_width, bottom_width))
    target_height = int(max(right_height, left_height))
    if min(target_width, target_height) < _MIN_PAGE_DIMENSION:
        return None

    scale = min(1.0, _MAX_PAGE_SIDE / max(target_width, target_height))
    target_width = max(1, int(target_width * scale))
    target_height = max(1, int(target_height * scale))
    destination_points = np.array(
        [
            [0, 0],
            [target_width - 1, 0],
            [target_width - 1, target_height - 1],
            [0, target_height - 1],
        ],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(source_points, destination_points)
    rectified_image = cv2.warpPerspective(
        image,
        transform,
        (target_width, target_height),
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )

    if rectified_image.shape[1] > rectified_image.shape[0]:
        rectified_image = cv2.rotate(rectified_image, cv2.ROTATE_90_CLOCKWISE)
    return np.asarray(rectified_image, dtype=np.uint8)


def _order_page_corners(corners: NDArray[np.float32]) -> NDArray[np.float32]:
    """Return page corners in top-left, top-right, bottom-right, bottom-left order."""
    coordinate_sums = corners.sum(axis=1)
    coordinate_differences = np.diff(corners, axis=1).reshape(-1)
    return np.array(
        [
            corners[np.argmin(coordinate_sums)],
            corners[np.argmin(coordinate_differences)],
            corners[np.argmax(coordinate_sums)],
            corners[np.argmax(coordinate_differences)],
        ],
        dtype=np.float32,
    )


def _find_date_row_boundaries(
    gray_image: NDArray[np.uint8],
    threshold: float,
) -> tuple[tuple[float, ...], ...]:
    """Find one 19-boundary sequence for each repeated 18-student date table."""
    image_width = gray_image.shape[1]
    x_start, x_end = _fractional_range(image_width, _ROW_LINE_REFERENCE_X_RANGE)
    strip_width = x_end - x_start
    if strip_width < _MIN_REFERENCE_STRIP_WIDTH:
        return ()

    dark_pixel_counts = np.count_nonzero(gray_image[:, x_start:x_end] < threshold, axis=1)
    active_rows = np.flatnonzero(dark_pixel_counts >= max(2, int(strip_width * 0.4)))
    line_centers = _cluster_line_centers(active_rows)
    if len(line_centers) < _ROWS_PER_DATE + 1:
        return ()

    candidates = _find_consistent_boundary_candidates(line_centers, image_width)
    selected = _select_non_overlapping_candidates(candidates)
    if not selected or len(selected) > _MAX_DATE_TABLES:
        return ()

    selected.sort(key=lambda candidate: candidate[1][0])
    corrected_boundaries = tuple(_correct_table_row_boundaries(gray_image, boundaries) for _, boundaries in selected)
    if any(len(boundaries) != _ROWS_PER_DATE + 1 for boundaries in corrected_boundaries):
        return ()
    return corrected_boundaries


def _find_consistent_boundary_candidates(
    line_centers: list[float],
    image_width: int,
) -> list[tuple[float, int, tuple[float, ...]]]:
    """Find regular 18-row line sequences among horizontal rule candidates."""
    candidates: list[tuple[float, int, tuple[float, ...]]] = []
    minimum_gap = max(8.0, image_width * 0.012)
    maximum_gap = image_width * 0.045
    for start_index in range(len(line_centers) - _ROWS_PER_DATE):
        boundary_window = tuple(line_centers[start_index : start_index + _ROWS_PER_DATE + 1])
        row_heights = np.diff(np.asarray(boundary_window))
        average_height = float(np.mean(row_heights))
        if average_height <= 0 or float(np.min(row_heights)) < minimum_gap:
            continue
        if float(np.max(row_heights)) > maximum_gap:
            continue

        variation = float(np.std(row_heights) / average_height)
        if variation <= _ROW_HEIGHT_VARIATION_THRESHOLD:
            candidates.append((variation, start_index, boundary_window))
    return candidates


def _select_non_overlapping_candidates(
    candidates: list[tuple[float, int, tuple[float, ...]]],
) -> list[tuple[int, tuple[float, ...]]]:
    """Select the best row groups without counting the same rules twice."""
    selected: list[tuple[int, tuple[float, ...]]] = []
    for _, start_index, boundary_window in sorted(candidates):
        end_index = start_index + _ROWS_PER_DATE
        overlaps_selected_window = any(
            start_index <= chosen_start + _ROWS_PER_DATE and chosen_start <= end_index for chosen_start, _ in selected
        )
        if overlaps_selected_window:
            continue
        selected.append((start_index, boundary_window))

    return selected


def _correct_table_row_boundaries(
    gray_image: NDArray[np.uint8],
    boundaries: tuple[float, ...],
) -> tuple[float, ...]:
    """Skip a header-text false line and infer a faint final border when needed.

    A printed rule runs through the whole name column, while a line of header text
    covers only part of it. The first candidate line is dropped only when it is not a
    continuous rule but the next one is; comparing line strength in the merged date
    column instead is unreliable on a bent page, where the header border drifts into
    the neighboring row window.
    """
    data_boundaries = boundaries
    row_height = float(np.median(np.diff(np.asarray(boundaries))))
    if not _is_printed_rule(gray_image, boundaries[0], row_height) and _is_printed_rule(
        gray_image, boundaries[1], row_height
    ):
        data_boundaries = boundaries[1:]

    if len(data_boundaries) == _ROWS_PER_DATE:
        row_heights = np.diff(np.asarray(data_boundaries))
        typical_row_height = float(np.median(row_heights[-5:]))
        data_boundaries = (*data_boundaries, data_boundaries[-1] + typical_row_height)

    if len(data_boundaries) != _ROWS_PER_DATE + 1:
        return ()
    return data_boundaries


def _is_printed_rule(gray_image: NDArray[np.uint8], line_y: float, row_height: float) -> bool:
    """Check that a line found in the reference strip continues as a rule into the name column."""
    image_width = gray_image.shape[1]
    reference_start, reference_end = _fractional_range(image_width, _ROW_LINE_REFERENCE_X_RANGE)
    name_column_start, _ = _fractional_range(image_width, _NAME_COLUMN_X_RANGE)
    continuity = measure_rule_continuity(
        gray_image, line_y, row_height, (reference_start + reference_end) // 2, name_column_start
    )
    return continuity >= _RULE_CONTINUITY_RATIO


def _cluster_line_centers(active_rows: NDArray[np.int64]) -> list[float]:
    """Collapse adjacent dark pixels into the center of each printed row line."""
    groups: list[list[int]] = []
    for row_index in active_rows.tolist():
        if not groups or row_index > groups[-1][-1] + 2:
            groups.append([row_index])
        else:
            groups[-1].append(row_index)

    return [float(np.mean(group)) for group in groups if len(group) <= _MIN_ROW_LINE_GROUP_HEIGHT]


def _build_row_evidence_sheet(
    color_image: NDArray[np.uint8],
    row_boundaries: tuple[tuple[float, ...], ...],
) -> bytes:
    """Enlarge each table without repeating handwriting across row crops."""
    image_height, image_width = color_image.shape[:2]
    x_start, x_end = _fractional_range(image_width, _ROW_EVIDENCE_X_RANGE)
    table_crops: list[tuple[NDArray[np.uint8], int]] = []
    for boundaries in row_boundaries:
        y_start = max(0, int(boundaries[0]))
        y_end = min(image_height, int(boundaries[-1]))
        table_crop = color_image[y_start:y_end, x_start:x_end]
        if table_crop.size == 0:
            return b""
        table_crops.append((table_crop, y_start))

    table_width = int((x_end - x_start) * _EVIDENCE_SCALE)
    column_width = _EVIDENCE_LABEL_WIDTH + table_width + _EVIDENCE_TABLE_PADDING
    group_height = _EVIDENCE_DATE_HEIGHT + max(int(crop.shape[0] * _EVIDENCE_SCALE) for crop, _ in table_crops)
    group_count = (len(table_crops) + 1) // 2
    column_count = min(2, len(table_crops))
    evidence_sheet = np.full((group_count * group_height, column_count * column_width, 3), 255, dtype=np.uint8)

    for date_index, (boundaries, (table_crop, y_start)) in enumerate(zip(row_boundaries, table_crops, strict=True)):
        column_x = (date_index % 2) * column_width
        group_y = (date_index // 2) * group_height
        _draw_date_evidence(evidence_sheet, color_image, boundaries, date_index + 1, column_x, group_y, column_width)
        table_x = column_x + _EVIDENCE_LABEL_WIDTH
        table_y = group_y + _EVIDENCE_DATE_HEIGHT
        resized_table = cv2.resize(table_crop, (0, 0), fx=_EVIDENCE_SCALE, fy=_EVIDENCE_SCALE)
        evidence_sheet[table_y : table_y + resized_table.shape[0], table_x : table_x + resized_table.shape[1]] = (
            resized_table
        )
        for row_index, (top, bottom) in enumerate(pairwise(boundaries), start=1):
            row_center_y = table_y + int(((top + bottom) / 2 - y_start) * _EVIDENCE_SCALE)
            cv2.putText(
                evidence_sheet,
                f"D{date_index + 1} R{row_index:02}",
                (column_x + 6, row_center_y + 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.47,
                (120, 0, 160),
                1,
                cv2.LINE_AA,
            )

    return _encode_jpeg(evidence_sheet)


def _draw_date_evidence(  # noqa: PLR0913
    sheet: NDArray[np.uint8],
    page: NDArray[np.uint8],
    boundaries: tuple[float, ...],
    date_index: int,
    column_x: int,
    group_y: int,
    tile_width: int,
) -> None:
    """Rotate the handwritten vertical date into a readable horizontal crop."""
    x_start, x_end = _fractional_range(page.shape[1], _DATE_EVIDENCE_X_RANGE)
    y_start, y_end = int(boundaries[1]), int(boundaries[-2])
    date_strip = page[y_start:y_end, x_start:x_end]
    rotated_date = cv2.rotate(date_strip, cv2.ROTATE_90_CLOCKWISE)
    resized_date = cv2.resize(rotated_date, (0, 0), fx=_EVIDENCE_SCALE, fy=_EVIDENCE_SCALE)
    target_height = min(resized_date.shape[0], _EVIDENCE_DATE_HEIGHT - 12)
    target_width = min(resized_date.shape[1], tile_width - _EVIDENCE_LABEL_WIDTH - 12)
    sheet[
        group_y + 6 : group_y + 6 + target_height,
        column_x + _EVIDENCE_LABEL_WIDTH : column_x + _EVIDENCE_LABEL_WIDTH + target_width,
    ] = resized_date[:target_height, :target_width]
    cv2.putText(
        sheet,
        f"D{date_index} DATE",
        (column_x + 6, group_y + 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.54,
        (120, 0, 160),
        1,
        cv2.LINE_AA,
    )


def _adjust_boundaries_for_time_column(
    gray_image: NDArray[np.uint8],
    boundaries: tuple[float, ...],
    threshold: float,
) -> tuple[float, ...]:
    """Track row rules into the time column and smooth handwriting interference."""
    image_height, image_width = gray_image.shape
    x_start, x_end = _fractional_range(image_width, _TIME_LINE_REFERENCE_X_RANGE)
    line_profile = np.count_nonzero(gray_image[:, x_start:x_end] < threshold, axis=1)
    typical_row_height = float(np.median(np.diff(np.asarray(boundaries))))
    search_radius = max(5, int(typical_row_height * 0.55))
    offsets: list[float] = []
    for boundary in boundaries:
        y_start = max(0, int(boundary) - search_radius)
        y_end = min(image_height, int(boundary) + search_radius + 1)
        local_profile = line_profile[y_start:y_end]
        peak = y_start + int(np.argmax(local_profile))
        offsets.append(float(peak - boundary))

    smoothed_offsets = [
        float(np.median(offsets[max(0, index - 2) : min(len(offsets), index + 3)])) for index in range(len(offsets))
    ]
    maximum_offset = typical_row_height * 0.45
    return tuple(
        boundary + max(-maximum_offset, min(maximum_offset, offset))
        for boundary, offset in zip(boundaries, smoothed_offsets, strict=True)
    )


def _draw_row_labels(
    image: NDArray[np.uint8],
    row_boundaries: tuple[tuple[float, ...], ...],
) -> None:
    """Draw a synthetic row anchor in the otherwise ignored teacher-signature column."""
    image_height, image_width = image.shape[:2]
    label_x = int(image_width * 0.835)
    font_scale = max(0.35, image_width / 1800)
    for boundaries in row_boundaries:
        for row_index, (top, bottom) in enumerate(pairwise(boundaries), start=1):
            baseline_y = max(10, min(image_height - 4, int((top + bottom) / 2 + 5)))
            cv2.putText(
                image,
                f"R{row_index:02}",
                (label_x, baseline_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                (190, 0, 190),
                1,
                cv2.LINE_AA,
            )


def _fractional_range(
    image_width: int,
    bounds: tuple[float, float],
) -> tuple[int, int]:
    """Convert normalized horizontal bounds to a non-empty pixel range."""
    start = max(0, min(image_width - 1, int(image_width * bounds[0])))
    end = max(start + 1, min(image_width, int(image_width * bounds[1])))
    return start, end


def _encode_jpeg(image: NDArray[np.uint8]) -> bytes:
    """Encode a prepared image at high quality while keeping the request compact."""
    succeeded, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 94])
    return encoded.tobytes() if succeeded else b""
