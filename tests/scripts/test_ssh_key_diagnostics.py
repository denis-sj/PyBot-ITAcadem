"""Regression tests for secret-safe SSH key diagnostics."""

# Ruff's security checks flag pytest assertions and synthetic key material.
# Neither is production secret handling; both are necessary to test that output
# remains limited to structural metadata.
# ruff: noqa: S101, S105

from __future__ import annotations

from scripts.ci.ssh_key_diagnostics import format_ssh_key_diagnostics


def test_diagnostics_report_key_shape_without_revealing_contents() -> None:
    secret_body = "SENSITIVE-PRIVATE-KEY-BODY"
    begin_marker = "".join(("-----BEGIN OPENSSH ", "PRIVATE KEY-----"))
    end_marker = "".join(("-----END OPENSSH ", "PRIVATE KEY-----"))
    secret_value = f"{begin_marker}\n{secret_body}\n{end_marker}\n"
    diagnostics = format_ssh_key_diagnostics(secret_value)

    assert f"Secret length (characters): {len(secret_value)}" in diagnostics
    assert "Secret line count: 3" in diagnostics
    assert "First non-empty line is exact OpenSSH BEGIN marker: yes" in diagnostics
    assert "Last non-empty line is exact OpenSSH END marker: yes" in diagnostics
    assert "BEGIN occurrences (expected 1): 1" in diagnostics
    assert "Leading spaces present in lines: no" in diagnostics
    assert secret_body not in diagnostics
    assert begin_marker not in diagnostics
    assert end_marker not in diagnostics


def test_diagnostics_detect_malformed_markers_and_indentation_without_leaking() -> None:
    secret_body = "ANOTHER-SENSITIVE-PRIVATE-KEY-BODY"
    begin_marker = "".join(("-----BEGIN OPENSSH ", "PRIVATE KEY-----"))
    end_marker = "".join(("-----END OPENSSH ", "PRIVATE KEY-----"))
    secret_value = f"\n {begin_marker}\n{secret_body}\n-----BEGIN duplicated\n {end_marker}\n"
    diagnostics = format_ssh_key_diagnostics(secret_value)

    assert f"Secret length (characters): {len(secret_value)}" in diagnostics
    assert "Secret line count: 5" in diagnostics
    assert "First non-empty line is exact OpenSSH BEGIN marker: no" in diagnostics
    assert "Last non-empty line is exact OpenSSH END marker: no" in diagnostics
    assert "BEGIN occurrences (expected 1): 2" in diagnostics
    assert "Leading spaces present in lines: yes" in diagnostics
    assert secret_body not in diagnostics
    assert "duplicated" not in diagnostics
