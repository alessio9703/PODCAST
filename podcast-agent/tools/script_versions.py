#!/usr/bin/env python3
"""Versioni del copione: elenco, contenuto, confronto, ripristino.

    python tools/script_versions.py --run-dir output/<run> --list
    python tools/script_versions.py --run-dir output/<run> --show 2
    python tools/script_versions.py --run-dir output/<run> --diff 2 [3] [--only-changes]
    python tools/script_versions.py --run-dir output/<run> --restore 2 [--expected-version 3] [--by U]

--diff A B confronta la versione A (prima) con la B (dopo); senza B confronta
con la versione corrente. Le battute risultano invariate, modificate (con i
pezzi cambiati parola per parola), aggiunte o rimosse. Con --only-changes le
battute invariate consecutive diventano un'unica riga {"stato": "invariate",
"conteggio", "da_n", "a_n"}.

--restore N NON riscrive la storia: crea una nuova versione con il contenuto
della N, da approvare di nuovo (l'approvazione viene ritirata). Tutto locale e
gratuito.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.script_diff import diff_scripts, only_changes  # noqa: E402
from src.script_editing import restore_version, versions_list  # noqa: E402
from src.script_session import FILENAME as SESSION_FILENAME  # noqa: E402
from src.script_session import ScriptSession  # noqa: E402
from src.script_view import script_payload, session_cast, structured_turns  # noqa: E402
from src.script_writer import PodcastScript  # noqa: E402


def _version_header(entry: dict) -> dict:
    return {
        "version": entry.get("version"),
        "at": entry.get("at"),
        "instruction": entry.get("instruction"),
        "kind": entry.get("kind"),
        "by": entry.get("by"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="Elenco delle versioni")
    mode.add_argument("--show", type=int, metavar="N", help="Contenuto della versione N")
    mode.add_argument("--diff", type=int, nargs="+", metavar="N", help="Confronta A con B (default: la corrente)")
    mode.add_argument("--restore", type=int, metavar="N", help="Nuova versione con il contenuto della N")
    parser.add_argument("--only-changes", action="store_true", help="Con --diff: solo le battute cambiate")
    parser.add_argument("--expected-version", type=int, default=None, help="Con --restore: versione che l'utente stava guardando")
    parser.add_argument("--by", default=None, help="Con --restore: chi ripristina")
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    run_dir = resolve_path(args.run_dir)

    if args.restore is not None:
        session, script, script_path, warnings = restore_version(
            run_dir, args.restore, expected_version=args.expected_version, by=args.by
        )
        note(
            f"Ripristinata la versione {args.restore} come versione {session.version_count}: "
            "va approvata di nuovo."
        )
        payload = script_payload(
            script,
            session,
            script_file=script_path,
            warnings=warnings,
            words_per_minute=int(config.script.get("words_per_minute", 150)),
        )
        emit({**payload, "restored_from": args.restore, "versions": versions_list(session)})
        return

    session = ScriptSession.load(run_dir / SESSION_FILENAME)

    if args.list:
        emit(
            {
                "run_dir": str(run_dir),
                "current_version": session.version_count,
                **session.approval_view(),
                "versions": versions_list(session),
            }
        )
        return

    if args.show is not None:
        entry = session.version(args.show)
        script = PodcastScript.from_dict(entry["script"])
        cast = session_cast(session, session.speakers or script.speakers)
        emit(
            {
                **_version_header(entry),
                "current": args.show == session.version_count,
                "errors": entry.get("errors") or [],
                "titolo": script.titolo,
                "turni": script.turni,
                "turn_count": len(script.turni),
                "cast": cast,
                "turni_strutturati": structured_turns(script.turni, cast),
            }
        )
        return

    # --diff
    if len(args.diff) > 2:
        raise ValueError("--diff vuole una o due versioni: --diff A [B].")
    first = args.diff[0]
    second = args.diff[1] if len(args.diff) == 2 else session.version_count
    before = session.version(first)
    after = session.version(second)
    result = diff_scripts(before["script"], after["script"])
    rows = only_changes(result["righe"]) if args.only_changes else result["righe"]
    counts = result["conteggi"]
    note(
        f"v{first} -> v{second}: {counts['modificata']} modificate, {counts['aggiunta']} aggiunte, "
        f"{counts['rimossa']} rimosse, {counts['invariata']} invariate."
    )
    emit(
        {
            "from": _version_header(before),
            "to": {**_version_header(after), "current": second == session.version_count},
            "only_changes": args.only_changes,
            **{k: v for k, v in result.items() if k != "righe"},
            "righe": rows,
        }
    )


if __name__ == "__main__":
    run_tool(main)
