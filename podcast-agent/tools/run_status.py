#!/usr/bin/env python3
"""Lo stato di un episodio dell'Archivio (stesso calcolo di runs_list.py).

    python tools/run_status.py --run-dir output/<run>

Utile all'interfaccia per aggiornare una riga, o per sapere da quale passo
riprendere (`passo`) e cosa proporre (`azione`). Gratis.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, parse_args, run_dir_arg, run_tool  # noqa: E402
from runs_list import build_provider_or_none  # noqa: E402
from src.run_state import run_state  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    emit(run_state(run_dir, config, build_provider_or_none(config)))


if __name__ == "__main__":
    run_tool(main)
