from __future__ import annotations

import sys

from pydantic import ValidationError

from .config import get_settings


def main() -> None:
    """Validate deploy-time runtime settings without echoing secret inputs."""
    try:
        settings = get_settings()
    except ValidationError as err:
        print("Runtime configuration is invalid:", file=sys.stderr)
        for detail in err.errors(include_input=False):
            location = ".".join(str(part) for part in detail["loc"])
            print(f"  {location}: {detail['msg']}", file=sys.stderr)
        sys.exit(1)

    if settings.bot_mode != "prod":
        print("Runtime configuration is invalid: BOT_MODE must be 'prod' for deployment", file=sys.stderr)
        sys.exit(1)

    print("Runtime configuration is valid")


if __name__ == "__main__":
    main()
