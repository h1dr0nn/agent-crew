"""Writes the `crew` launcher into the crew home, pointing at this installation.

Run by the plugin's SessionStart hook, so the launcher follows the plugin when
it is updated or moved. Silent unless something goes wrong.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from crew.cli import main  # noqa: E402

raise SystemExit(main(["shim", "--quiet"]))
