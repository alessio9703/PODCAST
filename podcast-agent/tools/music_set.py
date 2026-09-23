#!/usr/bin/env python3
"""Sceglie le musiche di un episodio: scrive musiche.json nella cartella della run.

    python tools/music_set.py --run-dir output/<run> --intro "Sigla LIL" \
        --sottofondo "Piano calmo"
    python tools/music_set.py --run-dir output/<run> --none outro

Operazione LOCALE e GRATUITA: non chiama nessun provider, non tocca il
copione, non richiede approvazione. Cambiare o togliere una musica non deve
costare niente (vedi README.md). I nomi passati vengono risolti contro
musiche/ con la stessa regola delle voci: nome ambiguo -> exit 3, nome
inesistente -> exit 1 (vedi tools/music_list.py --resolve, che usa la stessa
logica).

Le categorie non menzionate restano invariate rispetto a quanto gia' scelto:
per azzerarne esplicitamente una si usa --none.
"""

from __future__ import annotations

import argparse

from _common import NeedsUserInput, add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.music_library import CATEGORIES, MusicLibrary, load_music_choice, write_music_choice  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--intro", default=None, help="Nome del brano da usare come intro")
    parser.add_argument("--outro", default=None, help="Nome del brano da usare come outro")
    parser.add_argument("--sottofondo", default=None, help="Nome del brano da usare come sottofondo")
    parser.add_argument(
        "--none", nargs="+", choices=CATEGORIES, default=[], metavar="CATEGORIA",
        help="Categorie da azzerare esplicitamente (intro, outro, sottofondo)",
    )
    add_config_arg(parser)
    args = parser.parse_args()

    requested = {"intro": args.intro, "outro": args.outro, "sottofondo": args.sottofondo}
    for category in args.none:
        if requested[category] is not None:
            raise ValueError(
                f"--{category} e '--none {category}' sono in conflitto: scegli uno dei due."
            )

    if not any(requested.values()) and not args.none:
        raise ValueError(
            "Nessuna scelta indicata: passa almeno uno fra --intro/--outro/--sottofondo, "
            "oppure --none CATEGORIA per azzerarne una."
        )

    config = bootstrap(args)
    run_dir = resolve_path(args.run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(
            f"Cartella della run inesistente: {run_dir}. Chiama prima run_init.py."
        )

    library = MusicLibrary(config.music_dir, config.accepted_extensions)
    library.scan()

    updated = load_music_choice(run_dir)
    warnings: list[str] = []

    for category in args.none:
        updated[category] = None
        note(f"{category}: nessuna musica.")

    for category, name in requested.items():
        if name is None:
            continue
        match, candidates = library.resolve(category, name)
        if match is None and candidates:
            names = ", ".join(c.name for c in candidates)
            note(f"'{name}' ({category}): nome ambiguo, candidati: {names}")
            raise NeedsUserInput(f"'{name}' ({category}) non e' un nome certo: candidati {names}.")
        if match is None:
            available = ", ".join(t.name for t in library.tracks(category)) or "nessuno"
            raise ValueError(
                f"'{name}' non corrisponde a nessun brano in {category}/. Disponibili: {available}."
            )
        if match.metadata_error:
            raise ValueError(f"'{match.name}' ({category}) non e' utilizzabile: {match.metadata_error}")

        updated[category] = match.name
        if not match.metadata.licenza:
            warning = (
                f"'{match.name}' ({category}) non ha una licenza dichiarata nei metadati: "
                "verifica di poterlo pubblicare (vedi la nota etica sulle musiche in README.md)."
            )
            warnings.append(warning)
            note(f"{category}: '{match.name}' (SENZA licenza dichiarata nei metadati)")
        else:
            note(f"{category}: '{match.name}'")

    target = write_music_choice(run_dir, updated)

    emit(
        {
            "run_dir": str(run_dir),
            "musiche_file": str(target),
            "intro": updated.get("intro"),
            "outro": updated.get("outro"),
            "sottofondo": updated.get("sottofondo"),
            "warnings": warnings,
        }
    )


if __name__ == "__main__":
    run_tool(main)
