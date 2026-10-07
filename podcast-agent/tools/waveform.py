#!/usr/bin/env python3
"""Forma d'onda del file finale dell'episodio (player del passo 7).

    python tools/waveform.py --run-dir output/<run> --points 400

`peaks` sono N valori fra 0 e 1, uno per barra, sull'intero file finale
(intro e outro comprese). In cache in `_forma_onda_<N>.json`, legata allo
sha256 del file: rimontando l'episodio si ricalcola da sola. Gratis.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.episode_info import final_file  # noqa: E402
from src.production import load_run_script  # noqa: E402
from src.waveform import waveform  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--points", type=int, default=400, help="Numero di barre (10-4000)")
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    final = final_file(run_dir, load_run_script(run_dir))
    emit({"run": run_dir.name, **waveform(run_dir, final, args.points)})


if __name__ == "__main__":
    run_tool(main)
