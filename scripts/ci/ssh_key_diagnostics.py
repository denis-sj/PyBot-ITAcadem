"""Print safe structural diagnostics for a staging SSH private key."""

from __future__ import annotations

import os

_OPENSSH_BEGIN_MARKER = "".join(("-----BEGIN OPENSSH ", "PRIVATE KEY-----"))
_OPENSSH_END_MARKER = "".join(("-----END OPENSSH ", "PRIVATE KEY-----"))
_SECRET_ENV_NAME = "STAGING_DEPLOY_SSH_KEY"  # noqa: S105


def format_ssh_key_diagnostics(secret_value: str) -> str:
    """Describe key formatting without returning any part of the key itself."""
    lines = secret_value.splitlines()
    non_empty_lines = [line for line in lines if line.strip()]
    first_line_is_begin_marker = bool(non_empty_lines) and non_empty_lines[0] == _OPENSSH_BEGIN_MARKER
    last_line_is_end_marker = bool(non_empty_lines) and non_empty_lines[-1] == _OPENSSH_END_MARKER
    begin_occurrences = secret_value.count("BEGIN")
    has_leading_spaces = any(line.startswith(" ") for line in lines)

    return "\n".join(
        (
            "SSH key diagnostics (secret contents omitted):",
            f"Secret length (characters): {len(secret_value)}",
            f"Secret line count: {len(lines)}",
            f"First non-empty line is exact OpenSSH BEGIN marker: {'yes' if first_line_is_begin_marker else 'no'}",
            f"Last non-empty line is exact OpenSSH END marker: {'yes' if last_line_is_end_marker else 'no'}",
            f"BEGIN occurrences (expected 1): {begin_occurrences}",
            f"Leading spaces present in lines: {'yes' if has_leading_spaces else 'no'}",
        )
    )


def main() -> None:
    """Print only structural metadata for the secret supplied by GitHub Actions."""
    print(format_ssh_key_diagnostics(os.environ.get(_SECRET_ENV_NAME, "")))


if __name__ == "__main__":
    main()
