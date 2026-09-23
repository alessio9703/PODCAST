#!/usr/bin/env python3
"""Estrae il testo da un documento (PDF, DOCX, TXT, MD).

    python tools/doc_extract.py documento.pdf [--save-to CARTELLA]

Un nome di file senza percorso viene cercato prima in `paths.documents_dir`
(`documenti/`), poi come percorso relativo.

Con --save-to scrive il testo estratto su file e ne restituisce il percorso:
serve a script_write.py, che lavora sul testo, non sul PDF.
"""

from __future__ import annotations

import argparse

from _common import (  # noqa: E402
    add_config_arg,
    bootstrap,
    emit,
    note,
    resolve_path,
    run_tool,
)
from src.pdf_reader import extract_text  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", help="Percorso del documento")
    parser.add_argument("--save-to", default=None, help="Cartella dove salvare il testo")
    parser.add_argument(
        "--preview-chars", type=int, default=600, help="Lunghezza dell'anteprima"
    )
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    document = extract_text(args.document, search_dir=config.documents_dir)

    text_file = None
    if args.save_to:
        target_dir = resolve_path(args.save_to)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "documento_estratto.txt"
        target.write_text(document.text, encoding="utf-8")
        text_file = str(target)
        note(f"Testo estratto salvato in {target}")

    emit(
        {
            "path": str(document.path),
            "kind": document.kind,
            "pages": document.pages,
            "chars": document.char_count,
            "words": document.word_count,
            "preview": document.text[: args.preview_chars],
            "text_file": text_file,
        }
    )


if __name__ == "__main__":
    run_tool(main)
