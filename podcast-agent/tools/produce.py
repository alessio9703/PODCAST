#!/usr/bin/env python3
"""Produce l'episodio: il passo 6 dell'interfaccia ("Genera l'episodio").

    python tools/produce.py --run-dir output/<run> [--events]

In un comando solo, tenendo il lock di lavoro della run per tutta la durata
(lo stato "in generazione" si deriva da quel lock):

  1. controlla che il copione sia quello approvato (sha256): altrimenti exit 1
     ApprovalRequired, nessuna spesa;
  2. valida le musiche scelte (ffmpeg, brani, sha256), PRIMA di spendere;
  3. prepara le voci: riuso dal registro o clonazione, solo con il consenso
     registrato (exit 3 ConsentRequired, nessuna clonazione fatta) e senza
     retry (un errore del provider esce con exit 2);
  4. sintetizza le battute che non sono gia' in cache, monta, mixa le musiche;
  5. scrive tempi, segnali, voci_usate.json e la copia del documento.

Con --events scrive l'avanzamento in JSONL (fasi, voci, battuta i/N con
`cached` ed `eta_seconds`, avvisi, fine, interruzione, errore) nel file
`_eventi.jsonl` della run. Il nome lo fissa il tool: da fuori non si sceglie
quale file della run svuotare.

SIGTERM o Ctrl-C fermano la produzione fra una battuta e l'altra: le battute
pronte restano in cache, il comando esce con exit 1 ProductionInterrupted e
`details: {done, total, missing}`. Rilanciandolo si pagano solo le mancanti.
Un secondo segnale ferma subito (esito Interrupted).
"""

from __future__ import annotations

import argparse
import signal

from _common import add_config_arg, bootstrap, emit, note, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.production import EVENTS_FILENAME, EventLog, produce, ui_path  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument(
        "--events", action="store_true", help=f"Scrive l'avanzamento in {EVENTS_FILENAME} nella cartella della run"
    )
    parser.add_argument(
        "--accept-changed-music",
        action="store_true",
        help="Procede anche se un brano gia' usato risulta sostituito (sha256 diverso).",
    )
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    events_path = run_dir / EVENTS_FILENAME if args.events else None

    stop = {"requested": False}

    def request_stop(signum, frame) -> None:  # noqa: ANN001
        if stop["requested"]:
            raise KeyboardInterrupt  # secondo segnale: si esce subito
        stop["requested"] = True
        note("Interruzione richiesta: finisco la battuta in corso e mi fermo.")

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    # Gli eventi finali (interrotta, errore) li scrive produce(), ancora sotto
    # il lock di lavoro.
    payload = produce(
        run_dir,
        config,
        events=EventLog(events_path),
        should_stop=lambda: stop["requested"],
        accept_changed_music=args.accept_changed_music,
        note=note,
    )

    emit({**payload, "events_file": ui_path(events_path, config) if events_path else None})


if __name__ == "__main__":
    run_tool(main)
