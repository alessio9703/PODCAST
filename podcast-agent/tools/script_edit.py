#!/usr/bin/env python3
"""Modifica a mano il testo di UNA battuta (tag comprese): nuova versione.

    python tools/script_edit.py --run-dir output/<run> --turn 3 \
        --text "[pausa] Risposta con un esempio concreto tratto dal documento." \
        [--expected-version 3] [--by "Mario Rossi"]

Lo speaker resta quello della battuta. La nuova versione porta l'istruzione
"modifica manuale battuta N" e l'approvazione decade da sola (il gate
confronta lo sha256). Il copione risultante si valida con le stesse regole di
script_save.py: se una regola bloccante non torna, non si salva niente
(exit 1, error_type ScriptInvalid, errori in details).

Con --expected-version il comando si ferma (exit 1, VersionConflict) se nel
frattempo il copione e' cambiato. Operazione locale e gratuita.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, note, parse_args, require_by_in_web, run_dir_arg, run_tool  # noqa: E402
from src.script_editing import edit_turn  # noqa: E402
from src.script_view import script_payload  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--turn", type=int, required=True, help="Numero della battuta (da 1)")
    parser.add_argument("--text", required=True, help="Nuovo testo, con le eventuali tag [fra quadre]")
    parser.add_argument("--expected-version", type=int, default=None, help="Versione che l'utente stava guardando")
    parser.add_argument("--by", default=None, help="Chi fa la modifica")
    add_config_arg(parser)
    args = parse_args(parser)
    require_by_in_web(args.by, "la modifica di una battuta")

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    session, script, script_path, warnings = edit_turn(
        run_dir, args.turn, args.text, expected_version=args.expected_version, by=args.by
    )
    note(f"Battuta {args.turn} modificata: copione alla versione {session.version_count}.")
    for warning in warnings:
        note(f"avviso: {warning}")

    payload = script_payload(
        script,
        session,
        script_file=script_path,
        warnings=warnings,
        words_per_minute=int(config.script.get("words_per_minute", 150)),
    )
    emit({**payload, "edited_turn": args.turn})


if __name__ == "__main__":
    run_tool(main)
