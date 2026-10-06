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

from _common import add_config_arg, bootstrap, emit, note, run_tool  # noqa: E402
from src.audio_assembler import slugify  # noqa: E402
from src.episode_setup import apply_changes, episode_view  # noqa: E402
from src.pdf_reader import extract_text  # noqa: E402
from src.run_layout import make_run_dir  # noqa: E402
from src.script_session import FILENAME as SESSION_FILENAME  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True, help="Titolo provvisorio dell'episodio")
    parser.add_argument("--document", default=None, help="Documento di partenza (nome in documenti/ o percorso)")
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    if args.document:
        # Prima di creare la cartella: un PDF scansionato non deve lasciare
        # in Archivio una bozza vuota.
        extract_text(args.document, search_dir=config.documents_dir)
    run_dir = make_run_dir(config.output_dir, args.title)
    note(f"Cartella episodio creata: {run_dir}")
    episode, _ = apply_changes(run_dir, config, title=args.title, document=args.document)

    emit(
        {
            "run_dir": str(run_dir),
            "slug": slugify(args.title),
            "session_file": str(run_dir / SESSION_FILENAME),
            "script_file": str(run_dir / "conversazione.json"),
            "episodio": episode_view(run_dir, config, episode),
        }
    )


if __name__ == "__main__":
    run_tool(main)
