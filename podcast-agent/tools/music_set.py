#!/usr/bin/env python3
"""Sceglie le musiche di un episodio: scrive musiche.json nella cartella della run.

    python tools/music_set.py --run-dir output/<run> --intro "Sigla LIL" \
        --sottofondo "Piano calmo"
    python tools/music_set.py --run-dir output/<run> --none outro

Operazione LOCALE e GRATUITA: non chiama nessun provider, non tocca il
copione, non richiede approvazione. Cambiare o togliere una musica non deve
costare niente (vedi README.md). I nomi passati vengono risolti contro
musiche/ con la stessa regola delle voci: nome ambiguo -> exit 3
(AmbiguousName, candidati in `details`), nome
inesistente -> exit 1 (vedi tools/music_list.py --resolve, che usa la stessa
logica).

Le categorie non menzionate restano invariate rispetto a quanto gia' scelto:
per azzerarne esplicitamente una si usa --none.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, note, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.music_choice import set_music_choice  # noqa: E402
from src.music_library import CATEGORIES  # noqa: E402


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
    args = parse_args(parser)

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    if not run_dir.is_dir():
        raise FileNotFoundError(
            f"Cartella della run inesistente: {run_dir.name}. Chiama prima run_init.py."
        )

    requested = {"intro": args.intro, "outro": args.outro, "sottofondo": args.sottofondo}
    updated, warnings, target = set_music_choice(run_dir, config, requested, list(args.none), note=note)

    emit(
        {
            "run": run_dir.name,
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
