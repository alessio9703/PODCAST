#!/usr/bin/env python3
"""Registra l'approvazione dell'utente sul copione corrente.

    python tools/script_approve.py --run-dir output/<run>

Va chiamato SOLO dopo aver mostrato il copione per intero all'utente e aver
ricevuto un si' esplicito (AskUserQuestion). Non e' una formalita': finche'
questo comando non gira, voices_prepare.py e synthesize.py si rifiutano di
partire.

L'approvazione viene registrata insieme allo sha256 del copione approvato. Se
il copione cambia dopo, l'hash non corrisponde piu' e il via libera decade da
solo: un si' vale sul contenuto che l'utente ha letto, non sul file.
"""

from __future__ import annotations

import argparse
import json

from _common import add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.script_session import FILENAME as SESSION_FILENAME  # noqa: E402
from src.script_session import ScriptSession, script_hash  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-dir", help="Cartella dell'episodio")
    group.add_argument("--session", help="File _script_session.json")
    parser.add_argument(
        "--revoke", action="store_true", help="Ritira un'approvazione data prima"
    )
    add_config_arg(parser)
    args = parser.parse_args()

    bootstrap(args)
    session_path = (
        resolve_path(args.session)
        if args.session
        else resolve_path(args.run_dir) / SESSION_FILENAME
    )
    session = ScriptSession.load(session_path)

    if args.revoke:
        session.approved = None
        session.save()
        note("Approvazione ritirata.")
        emit({"approved": False, "session_file": str(session_path)})
        return

    # L'approvazione vale sul copione che sta davvero su disco.
    script_file = session_path.parent / "conversazione.json"
    if script_file.is_file():
        on_disk = json.loads(script_file.read_text(encoding="utf-8"))
        if script_hash(on_disk) != session.current_hash:
            raise ValueError(
                f"{script_file.name} non corrisponde alla versione registrata nella "
                "sessione: qualcuno lo ha modificato fuori da script_save.py.\n"
                "Risalvalo con script_save.py, rimostralo all'utente e fatti dare "
                "una nuova conferma."
            )

    approval = session.approve()
    session.save()
    note(
        f"Approvata la versione {approval['version']} del copione "
        f"({approval['sha256'][:19]}...)."
    )

    emit(
        {
            "approved": True,
            "version": approval["version"],
            "sha256": approval["sha256"],
            "at": approval["at"],
            "session_file": str(session_path),
        }
    )


if __name__ == "__main__":
    run_tool(main)
