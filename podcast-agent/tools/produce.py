#!/usr/bin/env python3
"""Produce l'episodio: il passo 6 dell'interfaccia ("Genera l'episodio").

    python tools/produce.py --run-dir output/<run> [--events output/<run>/_eventi.jsonl]

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
`cached` ed `eta_seconds`, avvisi, fine, interruzione, errore). Il file deve
stare dentro la cartella della run.

SIGTERM o Ctrl-C fermano la produzione fra una battuta e l'altra: le battute
pronte restano in cache, il comando esce con exit 1 ProductionInterrupted e
`details: {done, total, missing}`. Rilanciandolo si pagano solo le mancanti.
Un secondo segnale ferma subito (esito Interrupted).
"""

from __future__ import annotations

import argparse
import signal

from _common import _redact, add_config_arg, bootstrap, emit, note, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.paths import confine_to  # noqa: E402
from src.production import EventLog, ProductionInterrupted, produce  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--events", default=None, help="File JSONL degli eventi, dentro la cartella della run")
    parser.add_argument(
        "--accept-changed-music",
        action="store_true",
        help="Procede anche se un brano gia' usato risulta sostituito (sha256 diverso).",
    )
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    events_path = confine_to(args.events, run_dir, field="events") if args.events else None

    stop = {"requested": False}

    def request_stop(signum, frame) -> None:  # noqa: ANN001
        if stop["requested"]:
            raise KeyboardInterrupt  # secondo segnale: si esce subito
        stop["requested"] = True
        note("Interruzione richiesta: finisco la battuta in corso e mi fermo.")

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    events = EventLog(events_path)
    try:
        payload = produce(
            run_dir,
            config,
            events=events,
            should_stop=lambda: stop["requested"],
            accept_changed_music=args.accept_changed_music,
            note=note,
        )
    except ProductionInterrupted as exc:
        if events.opened:
            events.emit("interrotta", **(exc.details or {}))
        raise
    except BaseException as exc:
        if events.opened:
            kind = "Interrupted" if isinstance(exc, KeyboardInterrupt) else getattr(exc, "kind", None) or type(exc).__name__
            events.emit("errore", error_type=kind, error=_redact(str(exc)))
        raise
    finally:
        events.close()

    emit({**payload, "events_file": str(events_path) if events_path else None})


if __name__ == "__main__":
    run_tool(main)
