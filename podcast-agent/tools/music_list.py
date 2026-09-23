#!/usr/bin/env python3
"""Elenca i brani disponibili in musiche/, o risolve un nome.

    python tools/music_list.py
    python tools/music_list.py --resolve intro "sigla lil"

Senza --resolve elenca i brani per categoria (durata, licenza si/no, parlato
si/no). Con --resolve CATEGORIA NOME risolve un nome dentro quella categoria:
nome ambiguo -> exit 3 con i candidati; nome inesistente -> exit 1 con
l'elenco disponibile.

A differenza di voices_list.py (che ritorna sempre `match: null` e lascia
decidere all'agente), qui l'esito sta nell'exit code: questo tool serve anche
a music_set.py, che deve potersi fermare da solo su un nome incerto senza
un agente in mezzo a interpretare `match: null`.
"""

from __future__ import annotations

import argparse

from _common import NeedsUserInput, add_config_arg, bootstrap, emit, note, run_tool  # noqa: E402
from src.music_library import CATEGORIES, MusicLibrary, MusicTrack  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resolve", nargs=2, metavar=("CATEGORIA", "NOME"), default=None,
        help="Risolve un nome dentro una categoria (intro|outro|sottofondo)",
    )
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    library = MusicLibrary(config.music_dir, config.accepted_extensions)
    library.scan()

    if args.resolve:
        category, name = args.resolve
        _do_resolve(library, category, name)
        return

    _do_list(library)


def _track_summary(track: MusicTrack) -> dict:
    return {
        "name": track.name,
        "extension": track.extension,
        "duration_seconds": track.duration_seconds(),
        "has_licenza": bool(track.metadata.licenza),
        "parlato": track.metadata.parlato,
        "usable": track.metadata_error is None,
        "error": track.metadata_error,
    }


def _do_list(library: MusicLibrary) -> None:
    payload: dict[str, object] = {
        "music_dir": str(library.dir), "available": library.available(),
    }
    for category in CATEGORIES:
        tracks = library.tracks(category)
        rejected = library.rejected_files(category)
        payload[category] = [_track_summary(t) for t in tracks]
        payload[f"{category}_rejected"] = [p.name for p in rejected]

        note(
            f"{category}: {len(tracks)} brano/i"
            + (f", {len(rejected)} scartati (formato non riconosciuto)" if rejected else "")
        )
        for track in tracks:
            flags = []
            if not track.metadata.licenza:
                flags.append("SENZA licenza dichiarata")
            if track.metadata.parlato:
                flags.append("parlato")
            if track.metadata_error:
                flags.append(f"NON UTILIZZABILE: {track.metadata_error}")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            duration = track.duration_seconds()
            duration_label = f"{duration:.1f}s" if duration is not None else "durata sconosciuta"
            note(f"  - {track.name} ({duration_label}){suffix}")

    emit(payload)


def _do_resolve(library: MusicLibrary, category: str, name: str) -> None:
    if category not in CATEGORIES:
        raise ValueError(f"Categoria '{category}' sconosciuta. Ammesse: {', '.join(CATEGORIES)}.")

    match, candidates = library.resolve(category, name)

    if match is not None:
        if match.metadata_error:
            raise ValueError(
                f"'{match.name}' ({category}) non e' utilizzabile: {match.metadata_error}"
            )
        note(f"'{name}' -> '{match.name}'")
        emit({"category": category, "query": name, "match": match.name, **_track_summary(match)})
        return

    if candidates:
        names = ", ".join(c.name for c in candidates)
        note(f"'{name}': nome ambiguo in {category}/, candidati: {names}")
        raise NeedsUserInput(
            f"'{name}' non e' un nome certo in {category}/: candidati {names}."
        )

    available = ", ".join(t.name for t in library.tracks(category)) or "nessuno"
    raise ValueError(
        f"'{name}' non corrisponde a nessun brano in {category}/. Disponibili: {available}."
    )


if __name__ == "__main__":
    run_tool(main)
