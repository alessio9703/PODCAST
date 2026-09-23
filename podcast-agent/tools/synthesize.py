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
from pathlib import Path

from _approval import add_gate_args, enforce  # noqa: E402
from _common import add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.audio_assembler import AudioAssembler, slugify  # noqa: E402
from src.episode_signals import collect_and_write, record_synthesis_event  # noqa: E402
from src.music_mixer import mix_episode, preflight  # noqa: E402
from src.script_writer import PodcastScript, spoken_text  # noqa: E402


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
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    run_dir = resolve_path(args.run_dir)
    script = PodcastScript.from_dict(
        json.loads(resolve_path(args.script).read_text(encoding="utf-8"))
    )
    voice_map = json.loads(resolve_path(args.voices).read_text(encoding="utf-8"))

    # Checkpoint: l'utente deve aver approvato ESATTAMENTE questo copione.
    gate = enforce(args, script.to_dict())

    # Musica: valida TUTTO (ffmpeg, nomi, sha256 registrati) PRIMA di
    # spendere in sintesi. Se manca ffmpeg pur avendo scelto una musica, il
    # comando si ferma qui: tts_calls resta 0.
    music_choice, music_warnings = preflight(
        run_dir, config, accept_changed_music=args.accept_changed_music
    )

    provider = config.build_provider()
    assembler = AudioAssembler.from_config(provider, config.audio)

    # Blocco esplicito prima di spendere: nessuno speaker senza voce.
    assembler.check_voice_coverage(script, voice_map)

    total = len(script.turni)

    def progress(index: int, count: int, speaker: str, cached: bool) -> None:
        note(f"  [{index:>3}/{count}] {speaker}{' (in cache)' if cached else ''}")

    note(f"Sintesi di {total} turni con '{provider.name}'...")
    synthesis = assembler.synthesize(script, voice_map, run_dir / "segmenti", progress)
    segments = synthesis.paths
    if synthesis.reused_turns:
        note(
            f"{len(synthesis.reused_turns)} turni ripresi dalla cache, "
            f"{len(synthesis.generated_turns)} sintetizzati ora."
        )

    music_payload: dict[str, object] = {}
    if music_choice is not None and music_choice.any():
        note("Montaggio della voce e mix con le musiche scelte...")
        mix_result = mix_episode(
            assembler=assembler, segments=segments, script=script, run_dir=run_dir,
            config=config, accept_changed_music=args.accept_changed_music,
            choice=music_choice,
        )
        voice_result = mix_result.voice_assembly
        output_path = mix_result.path
        output_format = "mp3"
        duration_seconds = mix_result.duration_seconds
        all_warnings = [*synthesis.warnings, *music_warnings, *mix_result.warnings]
        music_payload = {
            "voice_offset_seconds": mix_result.voice_offset_seconds,
            "voice_duration_seconds": mix_result.voice_duration_seconds,
            "musiche_usate_file": str(mix_result.musiche_usate_path),
        }
    else:
        note("Montaggio...")
        voice_result = assembler.assemble(segments, run_dir / slugify(script.titolo))
        output_path = voice_result.path
        output_format = voice_result.audio_format
        duration_seconds = voice_result.duration_seconds
        all_warnings = [*synthesis.warnings, *voice_result.warnings]

    # RACCOLTA DATI (non entra nella stima). La velocita' di lettura misurata
    # per run varia del 10% senza che si sappia perche': il modello TTS e le
    # cifre da normalizzare sono gia' stati esclusi. L'ipotesi rimasta e' che
    # ogni voce clonata legga a velocita' sua. Qui si scrive un turno per riga
    # con caratteri parlati e durata reale: dopo qualche run bastano a dire se
    # l'ipotesi regge. Si misura PRIMA di cancellare i segmenti, cosi' il dato
    # resta anche senza tenersi decine di MB di wav per ogni episodio.
    # Riferito SEMPRE al master di sola voce, anche con le musiche (vedi
    # README.md, "Correggere una battuta").
    timings_file = _write_timings(run_dir, script, voice_result)

    # Segnali per le lezioni (src/episode_signals.py): un FATTO esplicito che
    # questo copione e' stato sintetizzato ORA, piu' l'aggiornamento di
    # _segnali.json. Va PRIMA di un eventuale --purge-segments, altrimenti i
    # segnali sui retake di QUESTA run risulterebbero "non disponibili" per
    # colpa della cancellazione appena fatta, non di una run precedente.
    # Mai bloccante: la sintesi e' gia' andata a buon fine a questo punto.
    try:
        record_synthesis_event(run_dir, script)
        collect_and_write(run_dir, provider=provider)
    except Exception as exc:  # noqa: BLE001 - raccolta dati, non deve mai far fallire la sintesi
        note(f"Raccolta segnali non riuscita (non blocca l'episodio): {exc}")

    # I segmenti NON si cancellano piu': sono la cache che permette di
    # correggere una battuta senza risintetizzare (e senza rimettere in gioco
    # le battute venute bene, visto che il TTS non e' deterministico). Chi
    # vuole liberare spazio lo dice esplicitamente.
    if args.purge_segments:
        shutil.rmtree(run_dir / "segmenti", ignore_errors=True)
    if args.keep_segments:
        note(
            "--keep-segments non serve piu': i segmenti si tengono sempre. "
            "Per cancellarli si usa --purge-segments."
        )

    for warning in all_warnings:
        note(f"ATTENZIONE: {warning}")

    emit(
        {
            "output": str(output_path),
            "format": output_format,
            "duration_seconds": round(duration_seconds, 1) if duration_seconds else None,
            "duration_label": _label(duration_seconds),
            "segments": len(segments),
            "generated_turns": synthesis.generated_turns,
            "reused_turns": synthesis.reused_turns,
            "tts_calls": len(synthesis.generated_turns),
            "segments_kept": not args.purge_segments,
            "segments_dir": None if args.purge_segments else str(run_dir / "segmenti"),
            "timings_file": str(timings_file) if timings_file else None,
            "warnings": all_warnings,
            **music_payload,
            **gate,
        }
    )


def _write_timings(run_dir: Path, script: PodcastScript, result) -> Path | None:
    """Scrive _tempi_turni.json: caratteri parlati e durata reale, per turno."""
    if not result.segment_seconds:
        return None  # percorso mp3: i segmenti non vengono decodificati

    turni = []
    timeline = result.turn_timeline or [{} for _ in script.turni]
    for turn, seconds, offsets in zip(script.turni, result.segment_seconds, timeline):
        chars = len(spoken_text(turn["testo"]))
        turni.append(
            {
                "speaker": turn["speaker"],
                "spoken_chars": chars,
                "seconds": round(seconds, 3),
                "chars_per_second": round(chars / seconds, 2) if seconds > 0 else None,
                # Dove cade il turno DENTRO l'episodio montato: e' quello che
                # permette di risalire da un minutaggio alla battuta
                # (tools/turn_at.py). Lo calcola il montaggio, non questo file.
                "inizio": offsets.get("inizio"),
                "fine": offsets.get("fine"),
            }
        )

    per_voce: dict[str, dict[str, float]] = {}
    for row in turni:
        acc = per_voce.setdefault(row["speaker"], {"spoken_chars": 0, "seconds": 0.0})
        acc["spoken_chars"] += row["spoken_chars"]
        acc["seconds"] += row["seconds"]
    for acc in per_voce.values():
        acc["seconds"] = round(acc["seconds"], 3)
        acc["chars_per_second"] = (
            round(acc["spoken_chars"] / acc["seconds"], 2) if acc["seconds"] > 0 else None
        )

    target = run_dir / "_tempi_turni.json"
    target.write_text(
        json.dumps(
            {
                "nota": "Misure per turno, al netto delle pause e delle inline tag. "
                        "Raccolta dati: la stima non le usa. inizio/fine sono "
                        "invece la posizione del turno dentro l'episodio montato.",
                "per_voce": per_voce,
                "turni": turni,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def _label(seconds: float | None) -> str | None:
    if not seconds:
        return None
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}m {rest:02d}s"


if __name__ == "__main__":
    run_tool(main)
