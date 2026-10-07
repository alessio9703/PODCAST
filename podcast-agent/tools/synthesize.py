#!/usr/bin/env python3
"""Sintetizza i turni e monta l'episodio.

    python tools/synthesize.py --script output/<run>/conversazione.json \
        --voices output/<run>/voci.json --run-dir output/<run>

E' il passo che COSTA: chiamalo solo dopo l'OK esplicito dell'utente sul
copione. check_voice_coverage() gira comunque prima di ogni sintesi, quindi
uno speaker senza voce blocca qui e non a meta' montaggio.
"""

from __future__ import annotations

import argparse
import json
import shutil

from _approval import add_gate_args, enforce  # noqa: E402
from _common import add_config_arg, add_trusted_paths_arg, bootstrap, emit, file_arg, note, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.music_mixer import preflight  # noqa: E402
from src.production import duration_label, render_episode  # noqa: E402
from src.script_writer import PodcastScript  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, help="conversazione.json")
    parser.add_argument("--voices", required=True, help="JSON speaker -> voice_id")
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument(
        "--purge-segments",
        action="store_true",
        help="Cancella segmenti/ dopo il montaggio. Di norma NON si usa: i "
        "segmenti sono la cache che permette di correggere una battuta sola.",
    )
    parser.add_argument(
        "--keep-segments",
        action="store_true",
        help=argparse.SUPPRESS,  # i segmenti ora si tengono sempre: alias senza effetto
    )
    parser.add_argument(
        "--accept-changed-music",
        action="store_true",
        help="Procede anche se un brano gia' registrato in musiche_usate.json "
        "risulta sostituito (sha256 diverso).",
    )
    add_gate_args(parser, run_dir_required=True)
    add_trusted_paths_arg(parser)
    add_config_arg(parser)
    # Dall'interfaccia si produce con produce.py.
    args = parse_args(parser, web_allowed=False)

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)
    script = PodcastScript.from_dict(
        json.loads(file_arg(args.script, config, args, "script").read_text(encoding="utf-8"))
    )
    voice_map = json.loads(file_arg(args.voices, config, args, "voices").read_text(encoding="utf-8"))

    # Checkpoint: l'utente deve aver approvato ESATTAMENTE questo copione.
    gate = enforce(args, script.to_dict())

    # Musica: valida TUTTO (ffmpeg, nomi, sha256 registrati) PRIMA di
    # spendere in sintesi. Se manca ffmpeg pur avendo scelto una musica, il
    # comando si ferma qui: tts_calls resta 0.
    music_choice, music_warnings = preflight(
        run_dir, config, accept_changed_music=args.accept_changed_music
    )

    provider = config.build_provider()

    def progress(index: int, count: int, speaker: str, cached: bool) -> None:
        note(f"  [{index:>3}/{count}] {speaker}{' (in cache)' if cached else ''}")

    note(f"Sintesi di {len(script.turni)} turni con '{provider.name}'...")
    render = render_episode(
        run_dir,
        config,
        script,
        voice_map,
        provider=provider,
        music_choice=music_choice,
        music_warnings=music_warnings,
        accept_changed_music=args.accept_changed_music,
        progress=progress,
        note=note,
    )
    synthesis = render.synthesis

    # I segmenti NON si cancellano piu': sono la cache che permette di
    # correggere una battuta senza risintetizzare (e senza rimettere in gioco
    # le battute venute bene, visto che il TTS non e' deterministico). Chi
    # vuole liberare spazio lo dice esplicitamente. Tempi e segnali sono gia'
    # stati raccolti da render_episode().
    if args.purge_segments:
        shutil.rmtree(run_dir / "segmenti", ignore_errors=True)
    if args.keep_segments:
        note(
            "--keep-segments non serve piu': i segmenti si tengono sempre. "
            "Per cancellarli si usa --purge-segments."
        )

    for warning in render.warnings:
        note(f"ATTENZIONE: {warning}")

    emit(
        {
            "output": str(render.output_path),
            "format": render.output_format,
            "duration_seconds": round(render.duration_seconds, 1) if render.duration_seconds else None,
            "duration_label": duration_label(render.duration_seconds),
            "segments": len(synthesis.paths),
            "generated_turns": synthesis.generated_turns,
            "reused_turns": synthesis.reused_turns,
            "tts_calls": len(synthesis.generated_turns),
            "segments_kept": not args.purge_segments,
            "segments_dir": None if args.purge_segments else str(run_dir / "segmenti"),
            "timings_file": str(render.timings_file) if render.timings_file else None,
            "warnings": render.warnings,
            **render.music_payload,
            **gate,
        }
    )


if __name__ == "__main__":
    run_tool(main)
