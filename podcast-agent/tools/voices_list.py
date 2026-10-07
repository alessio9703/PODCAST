#!/usr/bin/env python3
"""Elenca le voci disponibili e risolve i nomi digitati dall'utente.

    python tools/voices_list.py
    python tools/voices_list.py --resolve "Mario Rossi" --resolve "giulia b"
    python tools/voices_list.py --filter consenso_da_confermare --search rossi

Per ogni voce (libreria Voci): durata del campione, se e' gia' clonata e
quando, consenso, nome pronunciato (`nome_confermato` se registrato con
voice_meta.py --spoken-name, altrimenti solo proposto dal nome del file) e in
quanti episodi e' stata usata.

REGOLA: per le corrispondenze parziali questo tool ritorna `match: null` e la
lista dei `candidates`. Non sceglie mai al posto tuo. Stai selezionando la voce
di una persona reale: la conferma la chiede l'agente all'utente.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, note, parse_args, run_tool  # noqa: E402
from src.voice_catalog import FILTERS, filter_rows, voice_rows  # noqa: E402
from src.voice_library import VoiceLibrary  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resolve",
        action="append",
        default=[],
        metavar="NOME",
        help="Nome da risolvere (ripetibile)",
    )
    parser.add_argument(
        "--require", type=int, default=0, help="Fallisci se ci sono meno di N voci"
    )
    parser.add_argument("--filter", choices=FILTERS, default=None, help="Filtro della libreria Voci")
    parser.add_argument("--search", default=None, help="Cerca nel nome")
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    library = VoiceLibrary(config.voices_dir, config.accepted_extensions)
    library.scan()
    if args.require:
        library.require(args.require)

    voices = filter_rows(voice_rows(config, library), args.filter, args.search)

    resolved = []
    for query in args.resolve:
        match, candidates = library.resolve(query)
        resolved.append(
            {
                "query": query,
                "match": match.name if match else None,
                "candidates": [c.name for c in candidates],
            }
        )
        if match is None:
            note(
                f"'{query}': nessuna corrispondenza certa"
                + (f", candidati: {', '.join(c.name for c in candidates)}" if candidates else "")
            )

    emit(
        {
            "voices_dir": str(config.voices_dir),
            "provider": config.active_provider,
            "count": len(voices),
            "voices": voices,
            "rejected": [p.name for p in library.rejected_files],
            "resolved": resolved,
        }
    )


if __name__ == "__main__":
    run_tool(main)
