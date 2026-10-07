#!/usr/bin/env python3
"""L'Archivio: tutti gli episodi, dalle bozze a quelli pronti.

    python tools/runs_list.py [--status bozze|da_approvare|in_produzione|pronti] [--search TESTO]

Lo stato di ogni episodio si deriva dai file della run e dai lock (vedi
src/run_state.py): nessun campo salvato che possa andare fuori sincrono.
Gratis. Le run sono le sottocartelle di output/: sono le sole che i comandi
accettano come --run-dir (si passa il campo `run`).
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, parse_args, run_tool  # noqa: E402
from src.run_state import GROUPS, list_runs  # noqa: E402


def build_provider_or_none(config):  # type: ignore[no-untyped-def]
    """Il provider serve solo a contare le battute mancanti (impronta di sintesi)."""
    try:
        return config.build_provider()
    except Exception:  # noqa: BLE001 - senza provider l'Archivio si vede lo stesso
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", choices=GROUPS, default=None, help="Filtro dell'Archivio")
    parser.add_argument("--search", default=None, help="Cerca nel titolo e nel nome del documento")
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    rows = list_runs(config, build_provider_or_none(config), group=args.status, search=args.search)
    counts: dict[str, int] = {}
    for row in list_runs(config, None) if (args.status or args.search) else rows:
        counts[row["gruppo"]] = counts.get(row["gruppo"], 0) + 1
    emit({"episodi": rows, "totale": len(rows), "conteggi": counts})


if __name__ == "__main__":
    run_tool(main)
