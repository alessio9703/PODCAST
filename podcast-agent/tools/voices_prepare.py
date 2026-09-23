#!/usr/bin/env python3
"""Prepara i voice_id degli speaker: riusa dalla cache o clona.

    python tools/voices_prepare.py --speakers "Mario Rossi" "Giulia Bianchi" \
        --out output/<run>/voci.json

REGOLA: nessun retry sulla clonazione. Se fallisce, esce con codice 2 e
l'agente si ferma. Un retry cieco creerebbe voci duplicate a pagamento sul
provider.

Ogni voce con action="cloned" e' una voce clonata ORA per la prima volta:
l'agente deve dirlo esplicitamente all'utente (consenso vocale).
"""

from __future__ import annotations

import argparse
import json

from _approval import add_gate_args, enforce  # noqa: E402
from _common import add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.registry import VoiceRegistry  # noqa: E402
from src.voice_library import VoiceLibrary, VoiceLibraryError  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speakers", nargs="+", required=True, help="Nomi esatti")
    parser.add_argument("--out", default=None, help="Dove salvare la mappa speaker->voice_id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dice cosa farebbe senza clonare nulla (nessun costo)",
    )
    add_gate_args(parser, run_dir_required=False)
    add_config_arg(parser)
    args = parser.parse_args()

    # Il dry-run non spende: non ha bisogno dell'approvazione.
    gate = {"approval_gate": "not_needed"} if args.dry_run else enforce(args)

    config = bootstrap(args)
    library = VoiceLibrary(config.voices_dir, config.accepted_extensions)
    library.scan()
    registry = VoiceRegistry(config.registry_file, project_root=config.registry_file.parent)

    # I nomi devono essere esatti: qui non si indovina.
    by_name = {v.name: v for v in library.voices}
    unknown = [s for s in args.speakers if s not in by_name]
    if unknown:
        raise VoiceLibraryError(
            "Questi speaker non corrispondono a nessun file nella cartella voci: "
            + ", ".join(unknown)
            + ".\nDisponibili: "
            + ", ".join(by_name)
            + ".\nUsa voices_list.py --resolve per trovare il nome giusto e "
            "fattelo confermare dall'utente."
        )

    provider = None
    results: dict[str, dict[str, str | None]] = {}
    voice_map: dict[str, str] = {}

    for name in args.speakers:
        voice = by_name[name]
        voice_id, reason = registry.lookup(name, voice.path, config.active_provider)

        if voice_id:
            results[name] = {"voice_id": voice_id, "action": "reused", "reason": reason}
            voice_map[name] = voice_id
            note(f"{name}: {reason}")
            continue

        if args.dry_run:
            results[name] = {"voice_id": None, "action": "would_clone", "reason": reason}
            note(f"{name}: da clonare ({reason})")
            continue

        if provider is None:
            provider = config.build_provider()

        note(f"{name}: clono la voce su '{provider.name}' ({reason})...")
        # Nessun retry: un fallimento qui esce con codice 2.
        new_id = provider.clone_voice(name, str(voice.path))
        entry = registry.record(name, voice.path, provider.name, new_id)
        results[name] = {
            "voice_id": entry.voice_id,
            "action": "cloned",
            "reason": reason,
        }
        voice_map[name] = entry.voice_id

    audio_paths = {n: str(by_name[n].path) for n in args.speakers}
    out_file = None
    audio_paths_file = None
    if args.out and not args.dry_run:
        target = resolve_path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(voice_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        out_file = str(target)
        # accanto alla voice_map salva anche i file audio di riferimento:
        # servono a run_finalize.py e l'agente non deve costruirli a mano
        paths_target = target.parent / "_audio_paths.json"
        paths_target.write_text(
            json.dumps(audio_paths, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        audio_paths_file = str(paths_target)

    newly_cloned = [n for n, r in results.items() if r["action"] == "cloned"]
    if newly_cloned:
        note(
            "Voci clonate ORA per la prima volta: "
            + ", ".join(newly_cloned)
            + " — assicurati che queste persone abbiano dato il consenso."
        )

    emit(
        {
            "provider": config.active_provider,
            "speakers": results,
            "voice_map": voice_map,
            "newly_cloned": newly_cloned,
            "reused": [n for n, r in results.items() if r["action"] == "reused"],
            "audio_paths": audio_paths,
            "voice_map_file": out_file,
            "audio_paths_file": audio_paths_file,
            "dry_run": args.dry_run,
            **gate,
        }
    )


if __name__ == "__main__":
    run_tool(main)
