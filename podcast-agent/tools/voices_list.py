#!/usr/bin/env python3
"""Elenca le voci disponibili e risolve i nomi digitati dall'utente.

    python tools/voices_list.py
    python tools/voices_list.py --resolve "Mario Rossi" --resolve "giulia b"

REGOLA: per le corrispondenze parziali questo tool ritorna `match: null` e la
lista dei `candidates`. Non sceglie mai al posto tuo. Stai selezionando la voce
di una persona reale: la conferma la chiede l'agente all'utente.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, note, run_tool  # noqa: E402
from src.registry import VoiceRegistry  # noqa: E402
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
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    library = VoiceLibrary(config.voices_dir, config.accepted_extensions)
    library.scan()
    if args.require:
        library.require(args.require)

    registry = VoiceRegistry(config.registry_file, project_root=config.registry_file.parent)
    provider_name = config.active_provider

    voices = []
    for voice in library.voices:
        voice_id, reason = registry.lookup(voice.name, voice.path, provider_name)
        voices.append(
            {
                "name": voice.name,
                "path": str(voice.path),
                "extension": voice.extension,
                "size_mb": round(voice.size_bytes / (1024 * 1024), 2),
                "cached": voice_id is not None,
                "voice_id": voice_id,
                "cache_reason": reason,
            }
        )

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
            "provider": provider_name,
            "count": len(voices),
            "voices": voices,
            "rejected": [p.name for p in library.rejected_files],
            "resolved": resolved,
        }
    )


if __name__ == "__main__":
    run_tool(main)
