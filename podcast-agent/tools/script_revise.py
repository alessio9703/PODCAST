#!/usr/bin/env python3
"""Revisiona il copione a partire dalla sessione salvata su disco.

    python tools/script_revise.py --run-dir output/<run> \
        --instructions "Accorcia l'introduzione e aggiungi un esempio concreto"
    python tools/script_revise.py --session output/<run>/_script_session.json --instructions "..."

La conversazione viene ricostruita dalla sessione: compito iniziale + copione
attuale + le istruzioni gia' date. Le bozze scartate non rientrano nel contesto,
quindi il costo non cresce a ogni revisione.

Il risultato si valida con le stesse regole di script_save.py (vedi
script_write.py per cosa succede se ha errori). Se mentre il modello scriveva
qualcuno ha creato un'altra versione, la revisione si aggiunge comunque in coda
e `warnings` lo dice: la storia non si riscrive.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import add_config_arg, bootstrap, note, run_dir_arg, run_tool  # noqa: E402
from script_write import report  # noqa: E402
from src.script_authoring import revise_draft  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--session", help="File _script_session.json")
    where.add_argument("--run-dir", help="Cartella dell'episodio")
    parser.add_argument("--instructions", required=True, help="Cosa cambiare")
    parser.add_argument("--by", default=None, help="Chi ha chiesto la modifica")
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    # --session vale come la sua cartella: stessa regola di --run-dir.
    run_dir = run_dir_arg(args.run_dir or str(Path(args.session).expanduser().parent), config)

    result = revise_draft(run_dir, config, instructions=args.instructions, by=args.by, note=note)
    note(f"Copione aggiornato in {result.script_path} (versione {result.session.version_count})")
    for warning in result.warnings:
        note(f"avviso: {warning}")

    report(
        result,
        config,
        {"revisions": [r["instruction"] for r in result.session.revisions]},
    )


if __name__ == "__main__":
    run_tool(main)
