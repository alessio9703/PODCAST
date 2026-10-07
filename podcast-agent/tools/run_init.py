#!/usr/bin/env python3
"""Crea la cartella dell'episodio: output/<data>_<slug-titolo>/.

    python tools/run_init.py --title "Il trimestre in tre minuti"

Va chiamata PRIMA della scrittura del copione: il file di sessione delle
revisioni vive dentro questa cartella.

Scrive anche episodio.json con il titolo (la bozza dell'Archivio). Con
`--document` registra subito il documento: e' il passo 1 dell'interfaccia
("Da quale documento parte l'episodio?") in un comando solo. Un documento
senza testo estraibile ferma il comando PRIMA di creare la cartella.

    python tools/run_init.py --title "Il costo della pioggia" --document report.pdf
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, add_trusted_paths_arg, bootstrap, documents_confined, emit, note, parse_args, run_tool  # noqa: E402
from src.audio_assembler import slugify  # noqa: E402
from src.episode_setup import apply_changes, episode_view, load_document  # noqa: E402
from src.run_layout import make_run_dir  # noqa: E402
from src.script_session import FILENAME as SESSION_FILENAME  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True, help="Titolo provvisorio dell'episodio")
    parser.add_argument("--document", default=None, help="Documento di partenza (nome in documenti/ o percorso)")
    add_trusted_paths_arg(parser)
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    document = None
    if args.document:
        # Prima di creare la cartella: un PDF scansionato non deve lasciare
        # in Archivio una bozza vuota. Il Document estratto qui si passa poi
        # ad apply_changes, che non lo estrae una seconda volta.
        document = load_document(args.document, config, trusted_paths=not documents_confined(args))
    run_dir = make_run_dir(config.output_dir, args.title)
    note(f"Cartella episodio creata: {run_dir.name}")
    episode, _, _ = apply_changes(run_dir, config, title=args.title, document=document)

    emit(
        {
            "run": run_dir.name,
            "run_dir": str(run_dir),
            "slug": slugify(args.title),
            "session_file": str(run_dir / SESSION_FILENAME),
            "script_file": str(run_dir / "conversazione.json"),
            "episodio": episode_view(run_dir, config, episode),
        }
    )


if __name__ == "__main__":
    run_tool(main)
