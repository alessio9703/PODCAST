"""Cosa il copione deve sapere delle musiche scelte per la run.

Due viste diverse dello stesso fatto (intro/outro con `parlato: true`):

  - `capture_music_context()`: la FOTOGRAFIA {nome, sha256} salvata nella
    sessione alla prima stesura. Serve solo a mix_episode() per accorgersi
    che il copione era stato scritto per un'altra intro/outro;
  - `prompt_music_context()`: le TRASCRIZIONI da mettere nel prompt, perche'
    il modello non ripeta quello che l'intro dice gia'.

Entrambe leggono musiche.json della run. Una lettura che fallisce (nessuna
musica, brano sparito) non blocca la scrittura del copione: quel problema e'
di music_set.py / mix.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .music_library import MusicLibrary, has_any_music, load_music_choice

_SPOKEN_CATEGORIES = ("intro", "outro")


def _spoken_tracks(run_dir: Path, config: Any) -> list[tuple[str, Any]]:
    try:
        choice = load_music_choice(run_dir)
        library = MusicLibrary(config.music_dir, config.accepted_extensions)
        library.scan()
    except Exception:  # noqa: BLE001 - contesto facoltativo, vedi docstring del modulo
        return []

    found = []
    for category in _SPOKEN_CATEGORIES:
        name = choice.get(category)
        if not name:
            continue
        try:
            track = library.get_usable(category, name)
        except Exception:  # noqa: BLE001
            continue
        if track.metadata.parlato:
            found.append((category, track))
    return found


def capture_music_context(run_dir: Path, config: Any) -> dict[str, dict[str, str]] | None:
    """{"intro": {"name", "sha256"}, ...} per le sole categorie con parlato."""
    context = {
        category: {"name": track.name, "sha256": track.sha256()}
        for category, track in _spoken_tracks(run_dir, config)
    }
    return context or None


def prompt_music_context(run_dir: Path, config: Any) -> tuple[dict[str, dict[str, str]] | None, list[str]]:
    """({"intro": {"trascrizione"}, ...}, avvisi) per ScriptWriter.build_prompt()."""
    context: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    for category, track in _spoken_tracks(run_dir, config):
        if not track.metadata.trascrizione:
            warnings.append(
                f"'{track.name}' ({category}) ha parlato ma nessuna trascrizione nei "
                "metadati: il copione potrebbe ripetere quello che dice."
            )
        context[category] = {"trascrizione": track.metadata.trascrizione or ""}
    return (context or None), warnings


def music_durations(run_dir: Path, config: Any) -> dict[str, object]:
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


__all__ = ["capture_music_context", "music_durations", "prompt_music_context"]
