#!/usr/bin/env python3
"""Stima caratteri, durata e costo PRIMA di spendere in sintesi.

Passo 5 dell'interfaccia ("Stima e consenso"), per una run:

    python tools/estimate.py --run-dir output/<run>

Legge il copione della run e funziona anche prima che le voci siano pronte
(nessun voci.json): dice quali voci si riusano e quali si clonano, con lo
stato del consenso (`voices[]`), quante battute restano da generare e il
costo di questa produzione (sintesi + clonazioni) in USD, e in EUR se
config.yaml ha un cambio datato. Gratis: non chiama il provider.

Uso locale (subagent), su un file di copione:

    python tools/estimate.py --script output/<run>/conversazione.json

Con --script e --run-dir la stima diventa INCREMENTALE sulle voci di voci.json:
conta solo i turni il cui segmento non e' gia' in cache.

    python tools/estimate.py --script output/<run>/conversazione.json \
        --run-dir output/<run>
"""

from __future__ import annotations

import argparse
import json

from _common import InvalidArguments, add_config_arg, add_trusted_paths_arg, bootstrap, emit, file_arg, note, parse_args, run_dir_arg, run_tool  # noqa: E402
from src.audio_assembler import load_choices, script_segment_keys, segment_path  # noqa: E402
from src.music_context import music_durations  # noqa: E402
from src.production import estimate_run, predict, prediction_payload  # noqa: E402
from src.segment_cache import VOICES_FILENAME  # noqa: E402
from src.script_writer import PodcastScript  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", default=None, help="conversazione.json (uso locale)")
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Cartella dell'episodio: senza --script, la stima del passo 5",
    )
    add_trusted_paths_arg(parser)
    add_config_arg(parser)
    args = parse_args(parser)
    if not args.script and not args.run_dir:
        raise InvalidArguments(
            "Indica la run (--run-dir) oppure, in locale, il copione (--script).",
            details={"messaggio": "manca --run-dir"},
        )

    config = bootstrap(args)
    provider = config.build_provider()

    if not args.script:
        payload = estimate_run(run_dir_arg(args.run_dir, config), config, provider)
        note(payload["rendered"])
        note(
            f"  Da generare       : {payload['turns_to_generate']} battute su {payload['turns']}"
            + (f", voci da clonare: {', '.join(payload['voices_to_clone'])}" if payload["voices_to_clone"] else "")
        )
        emit(payload)
        return

    script = PodcastScript.from_dict(
        json.loads(file_arg(args.script, config, args, "script").read_text(encoding="utf-8"))
    )
    prediction = predict(script, provider, config)
    note(prediction.render())

    incremental: dict[str, object] = {}
    if args.run_dir:
        incremental = _incremental(run_dir_arg(args.run_dir, config), script, provider)

    # Le musiche non passano dal provider: il costo non cambia. Ma la durata
    # si stima dove si trova musiche.json — nella run, se c'e' un --run-dir,
    # altrimenti nella stessa cartella dello script (conversazione.json e
    # musiche.json vivono sempre insieme).
    music_dir_for_choice = (
        run_dir_arg(args.run_dir, config) if args.run_dir else file_arg(args.script, config, args, "script").parent
    )
    music = music_durations(music_dir_for_choice, config)
    if music:
        note(
            "  Musiche           : "
            + ", ".join(
                f"{label} {value:.1f}s" for label, value in music["_note_parts"]
            )
        )
    music.pop("_note_parts", None)

    emit(
        {
            **incremental,
            **prediction_payload(prediction, music),
            "cost_usd": round(prediction.cost_usd, 4) if prediction.cost_usd is not None else None,
        }
    )


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
        "run": run_dir.name,
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
