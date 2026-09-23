#!/usr/bin/env python3
"""Stima caratteri, durata e costo PRIMA di spendere in sintesi.

    python tools/estimate.py --script output/<run>/conversazione.json

Con --run-dir la stima diventa INCREMENTALE: conta solo i turni il cui
segmento non e' gia' in cache. Dopo aver corretto una battuta e' quello che
dice quanto costa davvero rigenerare l'episodio, che di norma e' una battuta
sola e non tutto il copione.

    python tools/estimate.py --script output/<run>/conversazione.json \
        --run-dir output/<run>
"""

from __future__ import annotations

import argparse
import json

from _common import add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.audio_assembler import load_choices, script_segment_keys, segment_path  # noqa: E402
from src.cost_estimate import estimate as compute  # noqa: E402
from src.music_library import MusicLibrary, has_any_music, load_music_choice  # noqa: E402
from src.segment_cache import VOICES_FILENAME  # noqa: E402
from src.script_writer import PodcastScript  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, help="conversazione.json")
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Cartella dell'episodio: conta solo i turni non ancora in cache",
    )
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    script = PodcastScript.from_dict(
        json.loads(resolve_path(args.script).read_text(encoding="utf-8"))
    )
    provider = config.build_provider()
    audio = config.audio

    prediction = compute(
        script,
        provider.pricing(),
        chars_per_second=getattr(provider, "chars_per_second", None),
        words_per_minute=int(config.script.get("words_per_minute", 150)),
        pause_between_turns_ms=int(audio.get("pause_between_turns_ms", 450)),
        lead_in_ms=int(audio.get("lead_in_ms", 300)),
        lead_out_ms=int(audio.get("lead_out_ms", 600)),
        trim_silence=bool(audio.get("trim_silence", False)),
        trim_margin_head_ms=int(audio.get("trim_margin_head_ms", 0)),
        trim_margin_tail_ms=int(audio.get("trim_margin_tail_ms", 0)),
    )
    note(prediction.render())

    incremental: dict[str, object] = {}
    if args.run_dir:
        incremental = _incremental(resolve_path(args.run_dir), script, provider)

    # Le musiche non passano dal provider: il costo non cambia. Ma la durata
    # si stima dove si trova musiche.json — nella run, se c'e' un --run-dir,
    # altrimenti nella stessa cartella dello script (conversazione.json e
    # musiche.json vivono sempre insieme).
    music_dir_for_choice = resolve_path(args.run_dir) if args.run_dir else resolve_path(args.script).parent
    music = _music_durations(music_dir_for_choice, config)
    if music:
        note(
            "  Musiche           : "
            + ", ".join(
                f"{label} {value:.1f}s" for label, value in music["_note_parts"]
            )
        )
    music.pop("_note_parts", None)
    extra = float(music.get("music_extra_seconds", 0.0))

    emit(
        {
            **incremental,
            "turns": prediction.turns,
            "speakers": prediction.speakers,
            "chars": prediction.total_chars,
            "words": prediction.total_words,
            "spoken_chars": prediction.spoken_chars,
            "speech_seconds": round(prediction.speech_seconds, 1),
            "pause_seconds": round(prediction.pause_seconds, 1),
            # Solo parlato + pause fra i turni: NON include intro/outro. Per
            # quello vedi duration_with_music_*, sotto — le due grandezze non
            # vanno confuse, e' per questo che restano entrambe.
            "duration_seconds": round(prediction.total_seconds, 1),
            "duration_low_seconds": round(prediction.low_seconds, 1),
            "duration_high_seconds": round(prediction.high_seconds, 1),
            "duration_label": prediction.duration_label,
            **music,
            # Con intro/outro/pause aggiunte: l'incertezza (DURATION_UNCERTAINTY)
            # si applica solo alla parte parlata, perche' le durate di
            # intro/outro sono misurate sui file, non stimate.
            "duration_with_music_seconds": round(prediction.total_seconds + extra, 1),
            "duration_with_music_low_seconds": round(prediction.low_seconds + extra, 1),
            "duration_with_music_high_seconds": round(prediction.high_seconds + extra, 1),
            "cost_usd": round(prediction.cost_usd, 4) if prediction.cost_usd is not None else None,
            "cost_basis": prediction.cost_basis,
            "rendered": prediction.render(),
        }
    )


def _music_durations(run_dir, config) -> dict[str, object]:
    """Durate di intro/outro/pause, per completare la stima. Il costo non cambia:
    le musiche non passano dal provider. Best-effort: un brano la cui durata
    non si conosce (formato compresso, ffmpeg assente) non blocca la stima,
    compare con durata None."""
    try:
        choice = load_music_choice(run_dir)
    except Exception:
        return {}
    if not has_any_music(choice):
        return {}

    library = MusicLibrary(config.music_dir, config.accepted_extensions)
    library.scan()
    music_cfg = config.music

    note_parts: list[tuple[str, float]] = []
    payload: dict[str, object] = {}
    extra_seconds = 0.0

    if choice.get("intro"):
        try:
            track = library.get_usable("intro", choice["intro"])
            duration = track.duration_seconds()
        except Exception:
            duration = None
        gap = float(music_cfg.get("intro_gap_ms", 800)) / 1000.0
        payload["music_intro_seconds"] = round(duration, 1) if duration is not None else None
        payload["music_intro_gap_seconds"] = round(gap, 1)
        if duration is not None:
            extra_seconds += duration + gap
            note_parts.append(("intro", duration))

    if choice.get("outro"):
        try:
            track = library.get_usable("outro", choice["outro"])
            duration = track.duration_seconds()
        except Exception:
            duration = None
        gap = float(music_cfg.get("outro_gap_ms", 600)) / 1000.0
        payload["music_outro_seconds"] = round(duration, 1) if duration is not None else None
        payload["music_outro_gap_seconds"] = round(gap, 1)
        if duration is not None:
            extra_seconds += duration + gap
            note_parts.append(("outro", duration))

    if choice.get("sottofondo"):
        payload["music_sottofondo"] = choice["sottofondo"]

    payload["music_extra_seconds"] = round(extra_seconds, 1)
    payload["_note_parts"] = note_parts
    return payload


def _incremental(run_dir, script: PodcastScript, provider) -> dict[str, object]:
    """Cosa resta davvero da sintetizzare, viste le chiavi gia' su disco.

    I caratteri sono quelli GREZZI dei soli turni mancanti: le inline tag si
    pagano anche se non si sentono (vedi src/cost_estimate.py).
    """
    keys = script_segment_keys(script, _voice_map(run_dir), provider)
    choices = load_choices(run_dir / "segmenti")
    missing = [
        index
        for index, key in enumerate(keys, start=1)
        if not segment_path(
            run_dir / "segmenti", key, choices.get(key, 1), provider.audio_format
        ).is_file()
    ]

    chars = sum(len(script.turni[i - 1]["testo"]) for i in missing)
    pricing = provider.pricing()
    cost = (
        chars / 1_000_000 * float(pricing.cost_per_million_chars_usd or 0.0)
        if pricing.is_known
        else None
    )
    note(
        f"  Da sintetizzare   : {len(missing)} turni su {len(script.turni)} "
        f"({chars} caratteri)"
        + (f", ~${cost:.4f}" if cost is not None else "")
        + " — gli altri sono gia' in cache"
    )
    return {
        "run_dir": str(run_dir),
        "turns_to_generate": len(missing),
        "turns_to_generate_list": missing,
        "chars_to_generate": chars,
        "cost_to_generate_usd": round(cost, 4) if cost is not None else None,
    }


def _voice_map(run_dir) -> dict[str, str]:
    """Le voci di QUESTA run: le chiavi dei segmenti dipendono dal voice_id."""
    voices_file = run_dir / VOICES_FILENAME
    if not voices_file.is_file():
        raise FileNotFoundError(
            f"Manca {voices_file}: senza le voci della run non si puo' sapere "
            "quali segmenti sono gia' in cache, perche' la chiave di un "
            "segmento dipende anche dalla voce."
        )
    return json.loads(voices_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    run_tool(main)
