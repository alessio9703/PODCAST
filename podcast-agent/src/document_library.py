"""Libreria Documenti: cosa c'e' in documenti/, cosa contiene, dove e' usato.

Per ogni documento: nome, tipo, dimensione, data di caricamento, pagine e
parole, se il testo e' estraibile (e se no perche'), e in quali episodi e'
stato usato.

L'estrazione costa (un PDF lungo, secondi): il risultato si tiene in cache in
`documenti/.estrazioni/<sha256>.json`, legato al contenuto del file e non al
nome. Un file sostituito con lo stesso nome ha un altro sha256, quindi si
ri-estrae; due richieste insieme si serializzano sul lock della cache e la
seconda trova il risultato della prima.

"Usato in" si ricava dalle run: lo sha256 del documento registrato in
episodio.json (o quello della copia documento_originale.*); solo per le run
che non hanno uno sha256 si ripiega sul nome del file.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from .episode_config import EpisodeConfig
from .fileio import file_lock, write_json_atomic
from .pdf_reader import (
    SUPPORTED_EXTENSIONS,
    DocumentNotExtractable,
    DocumentTooLarge,
    extract_text,
)
from .registry import file_hash
from .script_session import FILENAME as SESSION_FILENAME

CACHE_DIRNAME = ".estrazioni"
CACHE_VERSION = 1

FILTERS = ("mai_usati", "gia_usati", "con_problemi")


def _cache_target(documents_dir: Path, digest: str) -> Path:
    return Path(documents_dir) / CACHE_DIRNAME / f"{digest.split(':', 1)[-1]}.json"


def _extraction_summary(path: Path, config: Any) -> dict[str, Any]:
    try:
        document = extract_text(path.name, search_dir=config.documents_dir, confined=True, limits=config.document_limits)
    except DocumentNotExtractable as exc:
        return {"estraibile": False, "motivo": exc.details.get("motivo"), "pagine": None, "parole": None, "caratteri": None}
    except DocumentTooLarge as exc:
        return {"estraibile": False, "motivo": "troppo_grande", "limite": exc.details.get("limite"), "pagine": None, "parole": None, "caratteri": None}
    return {
        "estraibile": True,
        "motivo": None,
        "pagine": document.pages,
        "parole": document.word_count,
        "caratteri": document.char_count,
    }


def extraction_info(path: Path, config: Any, digest: str) -> dict[str, Any]:
    """Pagine, parole, estraibile: dalla cache, o estraendo (sotto lock)."""
    target = _cache_target(config.documents_dir, digest)
    target.parent.mkdir(exist_ok=True)
    if target.is_file():
        try:
            cached = json.loads(target.read_text(encoding="utf-8"))
            if cached.get("versione") == CACHE_VERSION:
                return cached["estrazione"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    with file_lock(target, timeout=120):
        # Rilettura dentro il lock: un'altra richiesta puo' averlo appena fatto.
        if target.is_file():
            try:
                cached = json.loads(target.read_text(encoding="utf-8"))
                if cached.get("versione") == CACHE_VERSION:
                    return cached["estrazione"]
            except (OSError, json.JSONDecodeError, KeyError):
                pass
        summary = _extraction_summary(path, config)
        write_json_atomic(target, {"versione": CACHE_VERSION, "sha256": digest, "estrazione": summary})
    return summary


def _usage(config: Any) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """({sha256: run}, {nome: run}) per tutte le run dell'Archivio."""
    by_hash: dict[str, set[str]] = {}
    by_name: dict[str, set[str]] = {}
    root = Path(config.output_dir)
    if not root.is_dir():
        return by_hash, by_name
    for run in root.iterdir():
        if run.name.startswith(".") or run.is_symlink() or not run.is_dir():
            continue
        digest = None
        name = None
        try:
            episode = EpisodeConfig.load(run)
        except Exception:  # noqa: BLE001 - una run rotta non conta
            episode = None
        if episode is not None and episode.documento:
            digest = episode.documento.get("sha256")
            name = episode.documento.get("nome")
        if digest is None:
            copies = [p for p in run.glob("documento_originale.*") if p.is_file() and not p.is_symlink() and not p.name.endswith(".percorso.txt")]
            if copies:
                digest = file_hash(copies[0])
        if name is None:
            session = run / SESSION_FILENAME
            if session.is_file():
                try:
                    name = Path(json.loads(session.read_text(encoding="utf-8")).get("document_path") or "").name or None
                except (OSError, json.JSONDecodeError):
                    name = None
        if digest:
            by_hash.setdefault(digest, set()).add(run.name)
        elif name:
            by_name.setdefault(name, set()).add(run.name)
    return by_hash, by_name


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def list_documents(config: Any, *, filter_: str | None = None, search: str | None = None) -> dict[str, Any]:
    root = Path(config.documents_dir)
    if not root.is_dir():
        return {"documenti": [], "scartati": []}
    by_hash, by_name = _usage(config)
    rows = []
    rejected = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
            continue
        if entry.suffix.lower() not in SUPPORTED_EXTENSIONS:
            rejected.append(entry.name)
            continue
        digest = file_hash(entry)
        info = extraction_info(entry, config, digest)
        runs = sorted(by_hash.get(digest, set()) | by_name.get(entry.name, set()))
        stat = entry.stat()
        rows.append(
            {
                "nome": entry.name,
                "tipo": entry.suffix.lower().lstrip(".").upper(),
                "dimensione_bytes": stat.st_size,
                "caricato_il": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
                "sha256": digest,
                **info,
                "usato_in": len(runs),
                "episodi": runs,
            }
        )
    if filter_ == "mai_usati":
        rows = [r for r in rows if r["usato_in"] == 0]
    elif filter_ == "gia_usati":
        rows = [r for r in rows if r["usato_in"] > 0]
    elif filter_ == "con_problemi":
        rows = [r for r in rows if not r["estraibile"]]
    if search:
        needle = _normalize(search.strip())
        rows = [r for r in rows if needle in _normalize(r["nome"])]
    return {"documenti": rows, "scartati": rejected}


__all__ = ["CACHE_DIRNAME", "FILTERS", "extraction_info", "list_documents"]
