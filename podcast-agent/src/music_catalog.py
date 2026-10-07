"""Libreria Musiche: ogni brano con quello che lo schermo "Musiche" mostra.

Durata, contenuto (solo strumentale / con parlato e trascrizione), licenza
(testo, chi l'ha dichiarata e quando) e in quanti episodi e' scelto (dalle
musiche.json delle run).
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from .music_library import CATEGORIES, MUSIC_CHOICE_FILENAME, MusicLibrary, MusicTrack

FILTERS = ("strumentali", "con_parlato", "licenza_mancante")


def music_usage(config: Any) -> dict[tuple[str, str], list[str]]:
    """{(categoria, nome): [run]} dalle musiche.json dell'Archivio."""
    usage: dict[tuple[str, str], list[str]] = {}
    root = Path(config.output_dir)
    if not root.is_dir():
        return usage
    for run in sorted(root.iterdir()):
        if run.name.startswith(".") or run.is_symlink() or not run.is_dir():
            continue
        choice_file = run / MUSIC_CHOICE_FILENAME
        if not choice_file.is_file() or choice_file.is_symlink():
            continue
        try:
            choice = json.loads(choice_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(choice, dict):
            continue
        for category in CATEGORIES:
            name = choice.get(category)
            if isinstance(name, str) and name:
                usage.setdefault((category, name), []).append(run.name)
    return usage


def _raw_meta(track: MusicTrack) -> dict[str, Any]:
    path = track.path.with_suffix(".json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if (path.is_file() and not path.is_symlink()) else {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    return raw if isinstance(raw, dict) else {}


def track_row(track: MusicTrack, config: Any, usage: dict[tuple[str, str], list[str]] | None = None) -> dict[str, Any]:
    usage = music_usage(config) if usage is None else usage
    raw = _raw_meta(track)
    runs = usage.get((track.category, track.name), [])
    duration = track.duration_seconds()
    error = track.metadata_error
    if error is None and duration is None:
        # Durata non leggibile con ffmpeg disponibile: il file non e' quello
        # che dice l'estensione (C6, BASSA-1).
        error = track.readability_error()
    return {
        "name": track.name,
        "extension": track.extension,
        "duration_seconds": duration,
        "has_licenza": bool(track.metadata.licenza),
        "licenza": track.metadata.licenza,
        "licenza_da": raw.get("licenza_da"),
        "licenza_il": raw.get("licenza_il"),
        "parlato": track.metadata.parlato,
        "trascrizione": track.metadata.trascrizione,
        "usable": error is None,
        "error": error,
        "usato_in": len(runs),
        "episodi": runs,
    }


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def keep(row: dict[str, Any], filter_: str | None, search: str | None) -> bool:
    if filter_ == "strumentali" and row["parlato"]:
        return False
    if filter_ == "con_parlato" and not row["parlato"]:
        return False
    if filter_ == "licenza_mancante" and row["has_licenza"]:
        return False
    if search and _normalize(search.strip()) not in _normalize(row["name"]):
        return False
    return True


__all__ = ["FILTERS", "keep", "music_usage", "track_row"]
