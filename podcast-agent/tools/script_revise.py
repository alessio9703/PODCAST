#!/usr/bin/env python3
"""Revisiona il copione a partire dalla sessione salvata su disco.

    python tools/script_revise.py --session output/<run>/_script_session.json \
        --instructions "Accorcia l'introduzione e aggiungi un esempio concreto"

La conversazione viene ricostruita dalla sessione: compito iniziale + copione
attuale + le istruzioni gia' date. Le bozze scartate non rientrano nel contesto,
quindi il costo non cresce a ogni revisione.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from script_write import build_writer, report  # noqa: E402
from src.script_session import ScriptSession  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="File _script_session.json")
    parser.add_argument("--instructions", required=True, help="Cosa cambiare")
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    session = ScriptSession.load(resolve_path(args.session))

    note(
        f"Revisione #{session.revision_count + 1} "
        f"({session.revision_count} gia' applicate)"
    )

    writer = build_writer(config)
    messages = session.seed_messages(args.instructions)
    script = writer.run_messages(messages)

    session.record(script, instruction=args.instructions)
    session.save()

    script_path = Path(session.path).parent / "conversazione.json"
    script.save(script_path)
    note(f"Copione aggiornato in {script_path}")

    report(
        script,
        session.path,
        script_path,
        session.speakers,
        {
            "revision_count": session.revision_count,
            "revisions": [r["instruction"] for r in session.revisions],
        },
    )


if __name__ == "__main__":
    run_tool(main)
