#!/usr/bin/env python3
"""La libreria Documenti: i documenti caricati, pronti da trasformare in episodi.

    python tools/documents_list.py [--filter mai_usati|gia_usati|con_problemi] [--search TESTO]

Per ogni documento: tipo, dimensione, data, pagine e parole, se il testo e'
estraibile (`motivo`: scansione, protetto, vuoto, illeggibile, troppo_grande)
e in quali episodi e' stato usato. L'estrazione e' in cache, legata allo
sha256 del file: la prima volta puo' richiedere qualche secondo. Gratis.
"""

from __future__ import annotations

import argparse

from _common import add_config_arg, bootstrap, emit, parse_args, run_tool  # noqa: E402
from src.document_library import FILTERS, list_documents  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--filter", choices=FILTERS, default=None, help="Filtro della libreria")
    parser.add_argument("--search", default=None, help="Cerca nel nome del file")
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    result = list_documents(config, filter_=args.filter, search=args.search)
    emit({**result, "totale": len(result["documenti"])})


if __name__ == "__main__":
    run_tool(main)
