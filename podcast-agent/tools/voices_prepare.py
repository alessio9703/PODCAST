#!/usr/bin/env python3
"""Prepara i voice_id degli speaker: riusa dalla cache o clona.

    python tools/voices_prepare.py --speakers "Mario Rossi" "Giulia Bianchi" \
        --out output/<run>/voci.json --run-dir output/<run>

REGOLA: nessun retry sulla clonazione. Se fallisce, esce con codice 2 e
l'agente si ferma. Un retry cieco creerebbe voci duplicate a pagamento sul
provider.

CONSENSO: una voce da clonare deve avere il consenso registrato
(tools/voice_meta.py --name NOME --consent --by CHI). Se manca anche per una
sola voce non si clona niente: exit 3, error_type ConsentRequired, le voci in
`details.voci`. Una voce gia' clonata si riusa senza chiederlo di nuovo, a meno
che il consenso sia stato revocato. --no-consent-gate salta il controllo: solo
per uso manuale e CI, mai dall'interfaccia (rifiutato in modalita' web).

Ogni voce con action="cloned" e' una voce clonata ORA per la prima volta.
"""

from __future__ import annotations

import argparse

from _approval import add_gate_args, enforce  # noqa: E402
from _common import add_config_arg, add_trusted_paths_arg, bootstrap, emit, file_arg, note, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.fileio import write_json_atomic  # noqa: E402
from src.voice_prep import prepare_voices  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speakers", nargs="+", required=True, help="Nomi esatti")
    parser.add_argument("--out", default=None, help="Dove salvare la mappa speaker->voice_id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dice cosa farebbe senza clonare nulla (nessun costo)",
    )
    parser.add_argument(
        "--no-consent-gate",
        action="store_true",
        help="Clona anche senza consenso registrato. Solo per uso manuale e CI: "
        "mai in una sessione con l'utente, mai dall'interfaccia web.",
    )
    add_gate_args(parser, run_dir_required=False)
    add_trusted_paths_arg(parser)
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    if args.run_dir:
        args.run_dir = str(run_dir_arg(args.run_dir, config))

    # Il dry-run non spende: non ha bisogno dell'approvazione.
    gate = {"approval_gate": "not_needed"} if args.dry_run else enforce(args)
    prepared = prepare_voices(
        config,
        args.speakers,
        dry_run=args.dry_run,
        consent_gate=not args.no_consent_gate,
        note=note,
    )

    out_file = None
    audio_paths_file = None
    if args.out and not args.dry_run:
        # Atomico: voci.json decide le chiavi dei segmenti in cache. Un file
        # scritto a meta' farebbe sembrare "da rigenerare" (a pagamento)
        # battute gia' pronte.
        target = write_json_atomic(file_arg(args.out, config, args, "out"), prepared.voice_map)
        out_file = str(target)
        # accanto alla voice_map salva anche i file audio di riferimento:
        # servono a run_finalize.py e l'agente non deve costruirli a mano
        paths_target = write_json_atomic(target.parent / "_audio_paths.json", prepared.audio_paths)
        audio_paths_file = str(paths_target)

    if prepared.newly_cloned:
        note("Voci clonate ORA per la prima volta: " + ", ".join(prepared.newly_cloned) + ".")
    if args.no_consent_gate:
        note("ATTENZIONE: controllo del consenso saltato (--no-consent-gate).")

    emit(
        {
            "provider": config.active_provider,
            "speakers": prepared.results,
            "voice_map": prepared.voice_map,
            "newly_cloned": prepared.newly_cloned,
            "reused": prepared.reused,
            "audio_paths": prepared.audio_paths,
            "voice_map_file": out_file,
            "audio_paths_file": audio_paths_file,
            "dry_run": args.dry_run,
            "consent_gate": "bypassed" if args.no_consent_gate else "checked",
            **gate,
        }
    )


if __name__ == "__main__":
    run_tool(main)
