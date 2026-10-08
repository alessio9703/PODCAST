"""Le musiche scelte per una run (musiche.json): lettura per la UI e scrittura.

    {"intro": "Sigla LIL", "outro": null, "sottofondo": "Piano calmo"}

Lettura (`music_choice_view`, revisione finale B2): per ogni categoria
  - "non_scelta": musiche.json non esiste ancora (passo 3 non fatto);
  - null:         scelta fatta: nessuna musica in questa categoria;
  - {nome, sha256, disponibile}: il brano scelto. `disponibile: false` se nel
    frattempo e' sparito dalla libreria (sha256 null) o non e' utilizzabile.

Scrittura (`set_music_choice`, revisione finale N5): lettura e scrittura di
musiche.json sotto lock, con rilettura DENTRO il lock. Due scelte
contemporanee (intro da una scheda, outro da un'altra) non si cancellano a
vicenda. Il nome si risolve con la stessa regola delle voci: ambiguo ->
AmbiguousName (exit 3), inesistente -> errore.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import AmbiguousName, DomainError
from .fileio import file_lock
from .music_library import (
    CATEGORIES,
    MUSIC_CHOICE_FILENAME,
    MusicLibrary,
    load_music_choice,
    write_music_choice,
)

NOT_CHOSEN = "non_scelta"


class MusicChoiceError(DomainError):
    """Scelta delle musiche non valida (brano inesistente o inutilizzabile, argomenti in conflitto)."""


def _library(config: Any) -> MusicLibrary:
    library = MusicLibrary(config.music_dir, config.accepted_extensions)
    library.scan()
    return library


def music_choice_view(run_dir: Path | str, config: Any) -> dict[str, Any]:
    """Le musiche scelte per la run, come le mostra la UI (passi 3 e 5, episodio)."""
    run_dir = Path(run_dir)
    if not (run_dir / MUSIC_CHOICE_FILENAME).is_file():
        return {category: NOT_CHOSEN for category in CATEGORIES}
    choice = load_music_choice(run_dir)
    library = _library(config) if any(choice.values()) else None
    view: dict[str, Any] = {}
    for category in CATEGORIES:
        name = choice.get(category)
        if not name:
            view[category] = None
            continue
        track = next((t for t in library.tracks(category) if t.name == name), None)  # type: ignore[union-attr]
        usable = track is not None and not track.metadata_error and not track.readability_error()
        view[category] = {
            "nome": name,
            "sha256": track.sha256() if track is not None else None,
            "disponibile": usable,
        }
    return view


def set_music_choice(
    run_dir: Path | str,
    config: Any,
    requested: dict[str, str | None],
    none: list[str],
    *,
    note: Any = None,
) -> tuple[dict[str, str | None], list[str], Path]:
    """Aggiorna musiche.json: ritorna (scelta aggiornata, avvisi, file scritto)."""
    for category in none:
        if requested.get(category) is not None:
            raise MusicChoiceError(
                f"--{category} e '--none {category}' sono in conflitto: scegli uno dei due.",
                details={"campo": category},
            )
    if not any(requested.values()) and not none:
        raise MusicChoiceError(
            "Nessuna scelta indicata: passa almeno uno fra --intro/--outro/--sottofondo, "
            "oppure --none CATEGORIA per azzerarne una."
        )
    run_dir = Path(run_dir)
    library = _library(config)
    warnings: list[str] = []
    resolved: dict[str, str] = {}
    for category, name in requested.items():
        if name is None:
            continue
        match, candidates = library.resolve(category, name)
        if match is None and candidates:
            names = ", ".join(c.name for c in candidates)
            raise AmbiguousName(
                f"'{name}' ({category}) non e' un nome certo: candidati {names}.",
                details={"category": category, "query": name, "candidates": [c.name for c in candidates]},
            )
        if match is None:
            available = ", ".join(t.name for t in library.tracks(category)) or "nessuno"
            raise MusicChoiceError(
                f"'{name}' non corrisponde a nessun brano in {category}/. Disponibili: {available}.",
                details={"category": category, "query": name},
            )
        problem = match.metadata_error or match.readability_error()
        if problem:
            raise MusicChoiceError(
                f"'{match.name}' ({category}) non e' utilizzabile: {problem}",
                details={"category": category, "nome": match.name},
            )
        resolved[category] = match.name
        if not match.metadata.licenza:
            warnings.append(
                f"'{match.name}' ({category}) non ha una licenza dichiarata nei metadati: "
                "verifica di poterlo pubblicare (vedi la nota etica sulle musiche in README.md)."
            )
        if note:
            note(f"{category}: '{match.name}'" + ("" if match.metadata.licenza else " (SENZA licenza dichiarata nei metadati)"))

    target = run_dir / MUSIC_CHOICE_FILENAME
    with file_lock(target):
        # Riletto DENTRO il lock: le categorie non toccate restano quelle
        # scritte, anche un istante fa, da un'altra richiesta.
        updated = load_music_choice(run_dir)
        for category in none:
            updated[category] = None
            if note:
                note(f"{category}: nessuna musica.")
        updated.update(resolved)
        written = write_music_choice(run_dir, updated)
    return updated, warnings, written


__all__ = ["NOT_CHOSEN", "MusicChoiceError", "music_choice_view", "set_music_choice"]
