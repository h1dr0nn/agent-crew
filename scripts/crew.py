"""Runs the crew CLI from this checkout of the plugin, without installing it."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from crew.cli import main  # noqa: E402

raise SystemExit(main(sys.argv[1:]))
