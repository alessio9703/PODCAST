#!/usr/bin/env python3
"""Rimonta la voce dai segmenti in cache e rifa' il mix con le musiche scelte.

    python tools/mix.py --run-dir output/<run>
    python tools/mix.py --run-dir output/<run> --accept-changed-music

Zero chiamate TTS: e' il comando da usare dopo tools/music_set.py, o dopo
tools/retake.py --choose se si vuole rimontare senza toccare le musiche. Non
spende, ma passa comunque dal gate di approvazione (come tools/retake.py):
verifica che l'episodio sia stato approvato ALMENO una volta, non che
QUESTO mix lo sia — produce l'audio finale ascoltabile, quindi non deve
essere raggiungibile su una run mai approvata.

Se i segmenti non sono in cache si ferma con un errore di dominio che
distingue due cause diverse: cartella segmenti/ vuota o assente (episodio
vecchio, o --purge-segments) da segmenti presenti ma che non combaciano con
le chiavi attuali (l'impronta di sintesi e' cambiata in config.yaml dopo
l'ultima sintesi). In entrambi i casi serve una nuova sintesi.
"""

from __future__ import annotations

import argparse

from _approval import add_gate_args, enforce  # noqa: E402
from _common import add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.audio_assembler import AudioAssembler  # noqa: E402
from src.music_mixer import mix_episode, preflight  # noqa: E402
from src.segment_cache import RunCache  # noqa: E402


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
    args = parser.parse_args()

    config = bootstrap(args)
    run_dir = resolve_path(args.run_dir)

    # Non spende, ma produce l'output finale: passa comunque dal gate.
    gate = enforce(args)

    choice, drift_warnings = preflight(
        run_dir, config, accept_changed_music=args.accept_changed_music
    )
    if choice is None or not choice.any():
        raise ValueError(
            "Nessuna musica scelta per questa run (musiche.json assente o "
            "vuoto): tools/mix.py serve a rimontare CON le musiche. Scegline "
            "una con tools/music_set.py, oppure per un episodio senza musiche "
            "il montaggio si fa con tools/synthesize.py."
        )

    provider = config.build_provider()
    cache = RunCache.load(run_dir, provider)

    missing = cache.missing_turns()
    if missing:
        if not cache.any_segment_on_disk():
            raise ValueError(
                f"Nessun segmento audio in {cache.segments_dir}: episodio "
                "vecchio, oppure e' stato usato --purge-segments. Serve una "
                "nuova sintesi: tools/synthesize.py."
            )
        raise ValueError(
            f"{len(missing)} turni su {len(cache.script.turni)} non hanno un "
            f"segmento che combaci con le chiavi attuali (turni: {missing}). "
            "Probabile causa: l'impronta di sintesi (modello TTS o parametri "
            "in config.yaml) e' cambiata dopo l'ultima sintesi. Serve una "
            "nuova sintesi: tools/synthesize.py."
        )

    assembler = AudioAssembler.from_config(provider, config.audio)
    segments = cache.ordered_segments()

    note("Rimonto la voce dalla cache e rifaccio il mix (zero chiamate TTS)...")
    result = mix_episode(
        assembler=assembler,
        segments=segments,
        script=cache.script,
        run_dir=run_dir,
        config=config,
        accept_changed_music=args.accept_changed_music,
        choice=choice,
    )

    warnings = [*drift_warnings, *result.warnings]
    for warning in warnings:
        note(f"ATTENZIONE: {warning}")

    emit(
        {
            "output": str(result.path),
            "duration_seconds": round(result.duration_seconds, 1),
            "voice_offset_seconds": result.voice_offset_seconds,
            "voice_duration_seconds": result.voice_duration_seconds,
            "musiche_usate_file": str(result.musiche_usate_path),
            "tts_calls": 0,
            "warnings": warnings,
            **gate,
        }
    )


if __name__ == "__main__":
    run_tool(main)
