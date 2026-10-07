#!/usr/bin/env python3
"""Rimonta l'episodio dai segmenti in cache, con le musiche scelte (se ci sono).

    python tools/mix.py --run-dir output/<run>
    python tools/mix.py --run-dir output/<run> --accept-changed-music

Zero chiamate TTS: e' il comando da usare dopo tools/music_set.py ("Cambia
musiche") e dopo tools/retake.py --choose ("Usa il take e rimonta"). Senza
musiche scelte rimonta la sola voce (G1). Non spende, ma passa comunque dal
gate di approvazione: produce l'audio finale ascoltabile, quindi non deve
essere raggiungibile su una run il cui copione attuale non e' approvato.
Tiene il lock di lavoro della run mentre rimonta (operazione "montaggio").

Se i segmenti non sono in cache si ferma con un errore di dominio che
distingue due cause diverse: cartella segmenti/ vuota o assente (episodio
vecchio, o --purge-segments) da segmenti presenti ma che non combaciano con
le chiavi attuali (copione cambiato, o impronta di sintesi cambiata in
config.yaml). In entrambi i casi serve una nuova produzione.
"""

from __future__ import annotations

import argparse

from _approval import add_gate_args, enforce  # noqa: E402
from _common import add_config_arg, bootstrap, emit, note, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.audio_assembler import AudioAssembler  # noqa: E402
from src.errors import DomainError  # noqa: E402
from src.music_mixer import preflight  # noqa: E402
from src.production import assemble_episode, duration_label, ui_path  # noqa: E402
from src.run_lock import work_lock  # noqa: E402
from src.segment_cache import RunCache  # noqa: E402
from src.voice_prep import check_not_revoked  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument(
        "--accept-changed-music",
        action="store_true",
        help="Procede anche se un brano gia' registrato in musiche_usate.json "
        "risulta sostituito (sha256 diverso).",
    )
    add_gate_args(parser, run_dir_required=True)
    add_config_arg(parser)
    args = parse_args(parser)

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    # Il gate di approvazione legge args.run_dir: deve essere il percorso gia'
    # confinato, non il valore ricevuto (che puo' essere il solo nome).
    args.run_dir = str(run_dir)

    with work_lock(run_dir, "montaggio"):
        provider = config.build_provider()
        cache = RunCache.load(run_dir, provider)
        # Non spende, ma produce l'output finale: passa comunque dal gate, sul
        # copione che si sta per montare, letto una volta sola e dentro il
        # lock (revisione di sicurezza C3, MEDIA-1).
        gate = enforce(args, cache.script.to_dict())
        # Una voce con il consenso revocato non si rimonta (C3, MEDIA-2).
        check_not_revoked(config, list(cache.script.speakers), voice_map=cache.voice_map)
        choice, drift_warnings = preflight(
            run_dir, config, accept_changed_music=args.accept_changed_music
        )

        missing = cache.missing_turns()
        if missing:
            if not cache.any_segment_on_disk():
                raise DomainError(
                    f"Nessun segmento audio in {cache.segments_dir.name}/: episodio vecchio, oppure e' "
                    "stato usato --purge-segments. Serve una nuova produzione.",
                    error_type="SegmentsMissing",
                    details={"missing": missing},
                )
            raise DomainError(
                f"{len(missing)} battute su {len(cache.script.turni)} non hanno un segmento pronto "
                f"(battute: {missing}): il copione e' cambiato dopo l'ultima produzione, oppure "
                "l'impronta di sintesi (modello o parametri in config.yaml). Serve una nuova "
                "produzione, che paga solo queste battute.",
                error_type="SegmentsMissing",
                details={"missing": missing},
            )

        assembler = AudioAssembler.from_config(provider, config.audio)
        note("Rimonto dalla cache (zero chiamate TTS)...")
        result = assemble_episode(
            run_dir,
            config,
            cache.script,
            cache.ordered_segments(),
            assembler=assembler,
            music_choice=choice,
            music_warnings=drift_warnings,
            accept_changed_music=args.accept_changed_music,
            note=note,
        )

    for warning in result.warnings:
        note(f"ATTENZIONE: {warning}")

    emit(
        {
            "run": run_dir.name,
            "output": str(result.output_path),
            "file": result.output_path.name,
            "file_ui": ui_path(result.output_path, config),
            "format": result.output_format,
            "duration_seconds": round(result.duration_seconds, 1) if result.duration_seconds else None,
            "duration_label": duration_label(result.duration_seconds),
            "with_music": bool(choice is not None and choice.any()),
            "tts_calls": 0,
            "warnings": result.warnings,
            **result.music_payload,
            **gate,
        }
    )


if __name__ == "__main__":
    run_tool(main)
