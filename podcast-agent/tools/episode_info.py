#!/usr/bin/env python3
"""Lo schermo "Episodio" (passo 7): intestazione, sezioni, trascrizione, file.

    python tools/episode_info.py --run-dir output/<run>

Gratis, nessuna chiamata a servizi esterni. Per ogni battuta della
trascrizione `inizio_finale`/`fine_finale` sono i secondi NEL FILE FINALE
(intro compresa): sono quelli da usare per il player e per "clicca un punto
della forma d'onda per vedere la battuta". `stato` e' derivato (pronto,
da_aggiornare con i `motivi`, in_corso, non_prodotto): vedi
src/episode_info.py.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.episode_info import episode_info  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    emit(episode_info(run_dir_arg(args.run_dir, config), config))


if __name__ == "__main__":
    run_tool(main)
