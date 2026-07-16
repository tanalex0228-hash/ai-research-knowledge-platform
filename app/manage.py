#!/usr/bin/env python
import os
import sys
from pathlib import Path


def main() -> None:
    # Keep Django's default test discovery stable even when this script is called
    # from the repository root (for example: `python app/manage.py test`).
    script_path = Path(__file__).resolve()
    sys.argv[0] = str(script_path)
    os.chdir(script_path.parent)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django is not installed. Install requirements before running manage.py."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
