#!/usr/bin/env python3
"""Metadati di un brano della libreria Musiche: licenza, parlato, trascrizione.

    python tools/music_meta.py --category intro --name "Sigla" --licenza "CC-BY 4.0, Autore X" --by "Utente A"
    python tools/music_meta.py --category intro --name "Sigla" --no-licenza --by "Utente A"
    python tools/music_meta.py --category outro --name "Chiusura" --parlato true --trascrizione "Grazie per l'ascolto" --by "Utente A"

Stesse regole della libreria: un sottofondo non puo' avere parlato, la
trascrizione vale solo con parlato (exit 1 MusicMetaError). Il nome si risolve
senza indovinare (exit 3 AmbiguousName). Gratis. La licenza non si verifica:
si registra chi l'ha dichiarata e quando.
"""

from __future__ import annotations

import argparse

from _common import InvalidArguments, add_config_arg, bootstrap, emit, note, parse_args, run_tool  # noqa: E402
from src.music_catalog import track_row  # noqa: E402
from src.music_library import CATEGORIES, MusicLibrary  # noqa: E402
from src.music_meta import resolve_track, update_metadata  # noqa: E402


def _bool(value: str) -> bool:
    text = value.strip().lower()
    if text in ("true", "si", "sì", "1", "yes"):
        return True
    if text in ("false", "no", "0"):
        return False
    raise InvalidArguments(f"--parlato vuole true o false, non '{value}'.", details={"messaggio": "parlato"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", required=True, choices=CATEGORIES)
    parser.add_argument("--name", required=True, help="Nome del brano")
    licence = parser.add_mutually_exclusive_group()
    licence.add_argument("--licenza", default=None, help="Testo della licenza (chi, quale)")
    licence.add_argument("--no-licenza", action="store_true", help="Toglie la licenza dichiarata")
    parser.add_argument("--parlato", default=None, help="true | false")
    parser.add_argument("--trascrizione", default=None, help="Cosa dice il parlato del brano")
    parser.add_argument("--by", default=None, help="Chi modifica")
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    library = MusicLibrary(config.music_dir, config.accepted_extensions)
    library.scan()
    track = resolve_track(library, args.category, args.name)
    changes = [args.licenza is not None, args.no_licenza, args.parlato is not None, args.trascrizione is not None]
    if any(changes):
        update_metadata(
            track,
            by=args.by,
            licenza=args.licenza,
            clear_licenza=args.no_licenza,
            parlato=_bool(args.parlato) if args.parlato is not None else None,
            trascrizione=args.trascrizione,
        )
        note(f"Metadati di {track.name} ({track.category}) aggiornati.")
        library.scan()
        track = resolve_track(library, args.category, track.name)
    emit({"category": track.category, **track_row(track, config)})


if __name__ == "__main__":
    run_tool(main)
