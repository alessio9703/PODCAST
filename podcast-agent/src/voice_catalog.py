"""Libreria Voci: ogni voce con tutto quello che lo schermo "Voci" mostra.

Per ogni registrazione in voci_disponibili/: durata del campione, se e' gia'
clonata (e quando), stato del consenso, nome pronunciato (confermato nei
metadati, o solo proposto dal nome del file) e in quanti episodi e' stata
usata (dalle voci.json delle run: le voci davvero prodotte).
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from .ffmpeg_tool import media_duration_seconds
from .registry import VoiceRegistry
from .voice_library import VoiceLibrary, spoken_name_guess
from .voice_meta import CONSENT_GRANTED, consent_view, load_meta

FILTERS = ("clonate", "da_clonare", "consenso_da_confermare")


def voice_usage(config: Any) -> dict[str, list[str]]:
    """{nome della voce: [run]} dalle voci.json dell'Archivio."""
    usage: dict[str, list[str]] = {}
    root = Path(config.output_dir)
    if not root.is_dir():
        return usage
    for run in sorted(root.iterdir()):
        if run.name.startswith(".") or run.is_symlink() or not run.is_dir():
            continue
        voices = run / "voci.json"
        if not voices.is_file() or voices.is_symlink():
            continue
        try:
            names = json.loads(voices.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(names, dict):
            for name in names:
                usage.setdefault(str(name), []).append(run.name)
    return usage


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def voice_rows(config: Any, library: VoiceLibrary) -> list[dict[str, Any]]:
    registry = VoiceRegistry(config.registry_file, project_root=config.registry_file.parent)
    usage = voice_usage(config)
    rows = []
    for voice in library.voices:
        voice_id, reason = registry.lookup(voice.name, voice.path, config.active_provider)
        meta = load_meta(voice.path)
        proposed, complete = spoken_name_guess(voice.name)
        confirmed = meta.get("nome_pronunciato")
        consent = consent_view(voice.path, meta)
        entry = registry.get(voice.name) or {}
        runs = usage.get(voice.name, [])
        try:
            duration = media_duration_seconds(voice.path)
        except Exception:  # noqa: BLE001 - una durata non misurabile non blocca l'elenco
            duration = None
        rows.append(
            {
                "name": voice.name,
                # Proposta per la presentazione iniziale, da far confermare.
                # name_complete=false: manca nome o cognome, va chiesto.
                "spoken_name": confirmed or proposed,
                "name_complete": True if confirmed else complete,
                "nome_pronunciato": confirmed,
                "nome_confermato": confirmed is not None,
                "path": str(voice.path),
                "file": voice.path.name,
                "extension": voice.extension,
                "size_mb": round(voice.size_bytes / (1024 * 1024), 2),
                "durata_campione_seconds": round(duration, 1) if duration else None,
                "cached": voice_id is not None,
                "voice_id": voice_id,
                "cache_reason": reason,
                "stato": "Già clonata" if voice_id else "Da clonare",
                "clonata_il": entry.get("cloned_at") if voice_id else None,
                "consenso": consent,
                "usata_in": len(runs),
                "episodi": runs,
            }
        )
    return rows


def filter_rows(rows: list[dict[str, Any]], filter_: str | None, search: str | None) -> list[dict[str, Any]]:
    if filter_ == "clonate":
        rows = [r for r in rows if r["cached"]]
    elif filter_ == "da_clonare":
        rows = [r for r in rows if not r["cached"]]
    elif filter_ == "consenso_da_confermare":
        rows = [r for r in rows if r["consenso"]["stato"] != CONSENT_GRANTED]
    if search:
        needle = _normalize(search.strip())
        rows = [r for r in rows if needle in _normalize(r["name"]) or needle in _normalize(r["spoken_name"] or "")]
    return rows


__all__ = ["FILTERS", "filter_rows", "voice_rows", "voice_usage"]
