#!/usr/bin/env python3
"""Crea la cartella dell'episodio: output/<data>_<slug-titolo>/.

    python tools/run_init.py --title "Il trimestre in tre minuti"

Va chiamata PRIMA della scrittura del copione: il file di sessione delle
revisioni vive dentro questa cartella.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, note, run_tool  # noqa: E402
from src.audio_assembler import slugify  # noqa: E402
from src.run_layout import make_run_dir  # noqa: E402
from src.script_session import FILENAME as SESSION_FILENAME  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True, help="Titolo provvisorio dell'episodio")
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    run_dir = make_run_dir(config.output_dir, args.title)
    note(f"Cartella episodio creata: {run_dir}")

    emit(
        {
            "run_dir": str(run_dir),
            "slug": slugify(args.title),
            "session_file": str(run_dir / SESSION_FILENAME),
            "script_file": str(run_dir / "conversazione.json"),
        }
    )


if __name__ == "__main__":
    run_tool(main)
