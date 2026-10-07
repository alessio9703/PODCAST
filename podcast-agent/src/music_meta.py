"""Metadati dei brani (libreria Musiche): licenza, parlato, trascrizione.

Stesso file che MusicLibrary legge, `<brano>.json` accanto al brano, e stesse
regole: un sottofondo non puo' avere parlato, la trascrizione ha senso solo se
il brano ha parlato. Scrittura atomica sotto lock, con rilettura dentro il
lock (piu' utenti sulla stessa libreria); le chiavi che questo modulo non
conosce restano come sono.

La licenza si registra con chi l'ha dichiarata e quando: e' la casella "Ho la
licenza per questo brano". Non si verifica, si rende visibile.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import AmbiguousName, DomainError
from .fileio import file_lock, write_json_atomic
from .music_library import CATEGORIES, MusicLibrary, MusicTrack

#: Lunghezza massima di licenza e trascrizione (testi scritti dall'utente).
MAX_TEXT = 2000


class MusicMetaError(DomainError):
    """Metadati non validi per questo brano (es. parlato in un sottofondo)."""


def resolve_track(library: MusicLibrary, category: str, query: str) -> MusicTrack:
    """Il brano esatto (a meno di maiuscole e accenti), senza indovinare."""
    if category not in CATEGORIES:
        raise MusicMetaError(
            f"Categoria '{category}' sconosciuta. Ammesse: {', '.join(CATEGORIES)}.",
            details={"campo": "categoria", "ammesse": list(CATEGORIES)},
        )
    match, candidates = library.resolve(category, query)
    if match is not None and not query.strip().isdigit():
        return match
    if candidates:
        raise AmbiguousName(
            f"'{query}' non e' un nome certo in {category}/: candidati " + ", ".join(c.name for c in candidates) + ".",
            details={"category": category, "query": query, "candidates": [c.name for c in candidates]},
        )
    raise MusicMetaError(
        f"'{query}' non corrisponde a nessun brano in {category}/.",
        details={"campo": "nome", "categoria": category},
    )


def _clean_text(value: str, field: str) -> str | None:
    text = str(value).strip()
    if len(text) > MAX_TEXT:
        raise MusicMetaError(f"'{field}' troppo lungo (massimo {MAX_TEXT} caratteri).", details={"campo": field})
    return text or None


def update_metadata(
    track: MusicTrack,
    *,
    by: str | None,
    licenza: str | None = None,
    clear_licenza: bool = False,
    parlato: bool | None = None,
    trascrizione: str | None = None,
) -> dict[str, Any]:
    """Aggiorna i metadati del brano. Tutto o niente: si valida prima di scrivere."""
    who = " ".join(by.split()) if by else None
    if not who:
        raise MusicMetaError("Serve il nome di chi modifica (--by).", details={"campo": "by"})
    target = track.path.with_suffix(".json")
    with file_lock(target):
        raw: dict[str, Any] = {}
        if target.is_file():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise MusicMetaError(
                    f"{target.name} non e' un JSON leggibile: correggilo o cancellalo.", details={"file": target.name}
                ) from exc
            if not isinstance(loaded, dict):
                raise MusicMetaError(f"{target.name} deve contenere un oggetto JSON.", details={"file": target.name})
            raw = loaded
        now = datetime.now().replace(microsecond=0).isoformat()
        if clear_licenza:
            raw["licenza"] = None
            raw.pop("licenza_da", None)
            raw.pop("licenza_il", None)
        elif licenza is not None:
            text = _clean_text(licenza, "licenza")
            if text is None:
                raise MusicMetaError("Il testo della licenza e' vuoto: per toglierla usa --no-licenza.", details={"campo": "licenza"})
            raw["licenza"] = text
            raw["licenza_da"] = who
            raw["licenza_il"] = now
        if parlato is not None:
            raw["parlato"] = parlato
            if not parlato:
                raw["trascrizione"] = None
        if trascrizione is not None:
            raw["trascrizione"] = _clean_text(trascrizione, "trascrizione")

        if track.category == "sottofondo" and raw.get("parlato"):
            raise MusicMetaError(
                "Un sottofondo deve essere solo musica: non puo' avere parlato. Usa questo brano "
                "come intro o outro.",
                details={"campo": "parlato", "categoria": track.category},
            )
        if raw.get("trascrizione") and not raw.get("parlato"):
            raise MusicMetaError(
                "La trascrizione ha senso solo per un brano con parlato.", details={"campo": "trascrizione"}
            )
        raw["modificato_da"] = who
        raw["modificato_il"] = now
        write_json_atomic(target, raw)
    return raw


__all__ = ["MAX_TEXT", "MusicMetaError", "resolve_track", "update_metadata"]
