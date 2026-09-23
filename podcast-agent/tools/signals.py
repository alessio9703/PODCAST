#!/usr/bin/env python3
"""Raccoglie i segnali di un episodio gia' sintetizzato e scrive _segnali.json.

    python tools/signals.py --run-dir output/<run>

Gira gia' in automatico alla fine di tools/synthesize.py: questo comando
serve per rilanciarla a mano (per esempio su una run vecchia, dopo aver
corretto la sessione) senza rifare la sintesi. Deterministico, nessuna
chiamata a modelli, nessun costo.
"""

from __future__ import annotations

import argparse
import json

from _common import add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.episode_signals import collect_and_write  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    run_dir = resolve_path(args.run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Cartella della run inesistente: {run_dir}.")

    threshold = float((config.lessons or {}).get("turn_similarity_threshold", 0.35))

    provider = None
    try:
        provider = config.build_provider()
    except Exception as exc:  # noqa: BLE001 - senza provider i segnali sui retake sono "non disponibili", non un errore fatale
        note(f"Provider vocale non disponibile ({exc}): i segnali sui retake saranno non disponibili.")

    target = collect_and_write(run_dir, provider=provider, similarity_threshold=threshold)
    note(f"Segnali scritti in {target}")

    payload = json.loads(target.read_text(encoding="utf-8"))
    emit({"segnali_file": str(target), "segnali": payload})


if __name__ == "__main__":
    run_tool(main)
