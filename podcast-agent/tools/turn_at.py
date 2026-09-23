#!/usr/bin/env python3
"""Dice quale battuta si sente a un dato minutaggio dell'episodio.

    python tools/turn_at.py --run-dir output/<run> --at 3:42
    python tools/turn_at.py --run-dir output/<run> --at 222

Serve perche' l'utente indica la battuta come la sente ("al tre e quaranta
c'e' un'intonazione sbagliata"), non come numero di turno. Non costa niente e
non chiama nessun provider: legge solo la timeline scritta dal montaggio.

Il minutaggio che passi e' SEMPRE quello del file finale, cioe' quello che
l'utente ascolta davvero. Se l'episodio ha un'intro, `_tempi_turni.json` resta
riferito al master di sola voce (non cambia mai): qui si legge
`musiche_usate.json`, se esiste, per sottrarre `voice_offset_seconds` prima di
cercare il turno. Se musiche_usate.json non esiste l'offset e' 0 e il
comportamento e' quello di sempre. Un istante che cade nell'intro o
nell'outro lo dice esplicitamente invece di restituire un turno a caso.
"""

from __future__ import annotations

import argparse
import json

from _common import add_config_arg, emit, note, resolve_path, run_tool  # noqa: E402
from src.episode_timeline import (  # noqa: E402
    attach_texts,
    label,
    load_timeline,
    parse_timecode,
    turn_at,
)
from src.music_mixer import MUSICHE_USATE_FILENAME  # noqa: E402
from src.segment_cache import SCRIPT_FILENAME  # noqa: E402
from src.script_writer import PodcastScript  # noqa: E402


def _load_offset(run_dir) -> tuple[float, float | None]:
    """(voice_offset_seconds, voice_duration_seconds). (0, None) se non c'e' musica."""
    target = run_dir / MUSICHE_USATE_FILENAME
    if not target.is_file():
        return 0.0, None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0.0, None
    return float(raw.get("voice_offset_seconds", 0.0)), raw.get("voice_duration_seconds")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--at", required=True, help="Minutaggio (3:42) o secondi (222), nel FILE FINALE")
    # accettato per uniformita' con gli altri tool: qui non serve nessun
    # provider, la timeline sta gia' nella cartella dell'episodio
    add_config_arg(parser)
    args = parser.parse_args()

    run_dir = resolve_path(args.run_dir)
    asked_seconds = parse_timecode(args.at)

    script_file = run_dir / SCRIPT_FILENAME
    if not script_file.is_file():
        raise FileNotFoundError(
            f"Nessun copione in {script_file}: non e' una cartella di episodio."
        )
    script = PodcastScript.from_dict(
        json.loads(script_file.read_text(encoding="utf-8"))
    )

    offset, voice_duration = _load_offset(run_dir)
    voice_seconds = asked_seconds - offset

    if voice_seconds < 0:
        note(
            f"A {label(asked_seconds)} nel file finale si e' ancora nell'intro "
            f"(che dura fino a {label(offset)}): nessun turno da correggere li'."
        )
        emit(
            {
                "asked_seconds": round(asked_seconds, 3),
                "in": "intro",
                "voice_offset_seconds": round(offset, 3),
            }
        )
        return

    if voice_duration is not None and voice_seconds > float(voice_duration):
        note(
            f"A {label(asked_seconds)} nel file finale si e' nell'outro (la "
            f"sezione parlata finisce a {label(offset + float(voice_duration))} "
            "nel file finale): nessun turno da correggere li'."
        )
        emit(
            {
                "asked_seconds": round(asked_seconds, 3),
                "in": "outro",
                "voice_offset_seconds": round(offset, 3),
                "voice_duration_seconds": round(float(voice_duration), 3),
            }
        )
        return

    turni = attach_texts(
        load_timeline(run_dir), [t["testo"] for t in script.turni]
    )
    found = turn_at(turni, voice_seconds)

    where = f"{label(found.inizio)}-{label(found.fine)}"
    if found.in_pausa:
        note(
            f"A {label(voice_seconds)} (nel master di sola voce) c'e' una pausa: "
            f"il turno piu' vicino e' il {found.numero} ({where}), a "
            f"{found.distanza:g}s di distanza."
        )
    else:
        note(f"A {label(voice_seconds)} parla {found.speaker}, turno {found.numero} ({where}).")
    note(f"  {found.testo}")

    emit(
        {
            "asked_seconds": round(asked_seconds, 3),
            "turn": found.numero,
            "speaker": found.speaker,
            "testo": found.testo,
            # posizione nel master di SOLA VOCE (quello che synthesize.py rimonta)
            "inizio": found.inizio,
            "fine": found.fine,
            "inizio_label": label(found.inizio),
            "fine_label": label(found.fine),
            # posizione nel file FINALE (quello che l'utente ascolta): coincide
            # con inizio/fine quando non c'e' nessuna musica (offset 0)
            "inizio_finale": round(found.inizio + offset, 3),
            "fine_finale": round(found.fine + offset, 3),
            "inizio_finale_label": label(found.inizio + offset),
            "fine_finale_label": label(found.fine + offset),
            "in_pausa": found.in_pausa,
            "distanza_seconds": found.distanza,
            "turns_total": len(turni),
        }
    )


if __name__ == "__main__":
    run_tool(main)
