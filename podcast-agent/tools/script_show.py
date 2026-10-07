#!/usr/bin/env python3
"""Mostra il copione corrente di una run, come lo serve la schermata Copione.

    python tools/script_show.py --run-dir output/<run>

Restituisce il copione con le battute strutturate (ruolo di chi parla, nome
pronunciato, tag con le loro posizioni), lo stato dell'approvazione e
l'elenco delle versioni. Se la run non ha ancora un copione esce comunque con
0 e `has_script: false`; se intanto il modello sta scrivendo, `in_corso` dice
quale operazione e da quando (lo stato "Sto scrivendo il copione...").

Sola lettura, nessun costo.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, run_dir_arg, run_tool  # noqa: E402
from src.run_lock import current_work  # noqa: E402
from src.script_editing import versions_list  # noqa: E402
from src.script_rules import validate_script  # noqa: E402
from src.script_session import FILENAME as SESSION_FILENAME  # noqa: E402
from src.script_session import ScriptSession  # noqa: E402
from src.script_view import script_payload  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Cartella della run inesistente: {run_dir}.")

    in_progress = current_work(run_dir)
    session_path = run_dir / SESSION_FILENAME
    session = ScriptSession.load(session_path) if session_path.is_file() else None

    if session is None or session.current_script is None:
        emit({"run_dir": str(run_dir), "has_script": False, "in_corso": in_progress})
        return

    script = session.script()
    # Gli avvisi non si salvano: si ricalcolano, con le stesse regole.
    _, warnings = validate_script(
        script.to_dict(),
        session.speakers or script.speakers,
        session.script_format,
        session.interviewer,
        session.presenter,
        session.participants,
    )
    payload = script_payload(
        script,
        session,
        script_file=run_dir / "conversazione.json",
        warnings=warnings,
        words_per_minute=int(config.script.get("words_per_minute", 150)),
    )
    emit(
        {
            "run_dir": str(run_dir),
            "has_script": True,
            "in_corso": in_progress,
            **payload,
            "versions": versions_list(session),
        }
    )


if __name__ == "__main__":
    run_tool(main)
