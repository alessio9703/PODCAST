#!/usr/bin/env python3
"""Registra l'approvazione dell'utente sul copione corrente.

    python tools/script_approve.py --run-dir output/<run> --current

Va chiamato SOLO dopo aver mostrato il copione per intero all'utente e aver
ricevuto un si' esplicito (AskUserQuestion). Non e' una formalita': finche'
questo comando non gira, voices_prepare.py e synthesize.py si rifiutano di
partire.

L'approvazione viene registrata insieme allo sha256 del copione approvato. Se
il copione cambia dopo, l'hash non corrisponde piu' e il via libera decade da
solo: un si' vale sul contenuto che l'utente ha letto, non sul file.

Dall'interfaccia:

    python tools/script_approve.py --run-dir output/<run> --version 3 --by "Mario Rossi"
    python tools/script_approve.py --run-dir output/<run> --revoke --by "Mario Rossi"

`--version` e' la versione che l'utente ha letto: se nel frattempo il copione
e' cambiato il comando si ferma (exit 1, VersionConflict) invece di approvare
un testo che nessuno ha visto. Per approvare serve `--version` oppure
`--current` (la versione corrente, qualunque sia: e' il flag esplicito per il
subagent, che ha appena mostrato il copione all'utente nella stessa
conversazione). Senza nessuno dei due: exit 1, VersionRequired. L'interfaccia
passa sempre `--version`. Una versione con errori bloccanti (solo dal
percorso API) non si approva: exit 1, ScriptHasErrors.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import add_config_arg, bootstrap, emit, note, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.errors import DomainError  # noqa: E402
from src.run_lock import session_lock  # noqa: E402
from src.script_editing import check_expected_version  # noqa: E402
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
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--version", type=int, default=None, help="Versione che l'utente ha letto e approva")
    which.add_argument(
        "--current",
        action="store_true",
        help="Approva la versione corrente senza indicarne il numero (uso locale, subagent)",
    )
    parser.add_argument("--by", default=None, help="Chi approva (o ritira l'approvazione)")
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    # --session vale come la sua cartella: stessa regola di --run-dir.
    run_dir = run_dir_arg(args.run_dir or str(Path(args.session).expanduser().parent), config)
    session_path = run_dir / SESSION_FILENAME
    with session_lock(session_path.parent):
        _approve_or_revoke(args, session_path)


def _approve_or_revoke(args, session_path) -> None:  # type: ignore[no-untyped-def]
    session = ScriptSession.load(session_path)

    if args.revoke:
        revoked = session.approved
        session.approved = None
        session.save()
        note("Approvazione ritirata." if revoked else "Non c'era nessuna approvazione da ritirare.")
        emit(
            {
                "approved": False,
                "revoked": revoked,
                "revoked_by": args.by,
                "current_version": session.version_count,
                "run": session_path.parent.name,
                "session_file": str(session_path),
            }
        )
        return

    if args.version is None and not args.current:
        raise DomainError(
            "Per approvare indica la versione che l'utente ha letto (--version N): "
            "un'approvazione senza versione potrebbe valere su un copione che nel "
            "frattempo e' cambiato. In locale, --current approva la versione corrente.",
            error_type="VersionRequired",
            details={"current_version": session.version_count},
        )
    check_expected_version(session, args.version)

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

    approval = session.approve(by=args.by)
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
            "by": approval.get("by"),
            "run": session_path.parent.name,
            "session_file": str(session_path),
        }
    )


if __name__ == "__main__":
    run_tool(main)
