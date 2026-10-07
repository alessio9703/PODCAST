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

Costo dell'elenco (revisioni di sicurezza C4, MEDIA-3, e C5, MEDIA-2): gli
sha256 sono in cache (src/hash_cache.py); l'elenco estrae da solo solo i file
di testo (veloci, entro un budget di tempo) e non aspetta il lock di
un'estrazione in corso. PDF e DOCX non ancora estratti tornano con
`in_attesa: true` e si estraggono uno alla volta con --name, che puo'
durare quanto un'estrazione intera.
"""

from __future__ import annotations

import json
import os
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from .episode_config import EpisodeConfig
from .fileio import LockTimeout, file_lock, write_json_atomic
from .hash_cache import HashCache
from .paths import PathNotAllowed
from .pdf_reader import (
    SUPPORTED_EXTENSIONS,
    TEXT_EXTENSIONS,
    DocumentError,
    DocumentNotExtractable,
    DocumentTooLarge,
    extract_text,
)
from .script_session import FILENAME as SESSION_FILENAME

CACHE_DIRNAME = ".estrazioni"
CACHE_VERSION = 1

FILTERS = ("mai_usati", "gia_usati", "con_problemi")

#: Secondi di estrazioni NUOVE per chiamata dell'elenco (le altre restano in attesa).
EXTRACTION_BUDGET_SECONDS = 20


def _cache_dir(documents_dir: Path) -> Path:
    """La cartella della cache: mai un link simbolico (C4, BASSA-4)."""
    target = Path(documents_dir) / CACHE_DIRNAME
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise PathNotAllowed(
            f"{CACHE_DIRNAME} nella libreria Documenti non e' una cartella normale.", details={"campo": "cache"}
        )
    target.mkdir(exist_ok=True)
    return target


def _cache_target(documents_dir: Path, digest: str) -> Path:
    return _cache_dir(documents_dir) / f"{digest.split(':', 1)[-1]}.json"


def _read_cached(target: Path) -> dict[str, Any] | None:
    if not target.is_file() or target.is_symlink():
        return None
    try:
        cached = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(cached, dict) and cached.get("versione") == CACHE_VERSION and isinstance(cached.get("estrazione"), dict):
        return cached["estrazione"]
    return None


def cached_extraction(config: Any, digest: str) -> dict[str, Any] | None:
    return _read_cached(_cache_target(config.documents_dir, digest))


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


def _stat_signature(path: Path) -> tuple[int, int, int]:
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def extraction_info(path: Path, config: Any, digest: str, *, lock_timeout: float = 120) -> dict[str, Any]:
    """Pagine, parole, estraibile: dalla cache, o estraendo (sotto lock)."""
    target = _cache_target(config.documents_dir, digest)
    cached = _read_cached(target)
    if cached is not None:
        return cached
    with file_lock(target, timeout=lock_timeout):
        # Rilettura dentro il lock: un'altra richiesta puo' averlo appena fatto.
        cached = _read_cached(target)
        if cached is not None:
            return cached
        before = _stat_signature(path)
        summary = _extraction_summary(path, config)
        # Se il file e' cambiato durante l'estrazione, il risultato non e'
        # dello sha256 calcolato prima: si usa, ma non si salva (C4, BASSA-1).
        if _stat_signature(path) == before:
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
                try:
                    cache = HashCache(run)
                    digest = cache.sha256(copies[0])
                    cache.save()
                except OSError:
                    digest = None
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


_PENDING = {"estraibile": None, "motivo": None, "pagine": None, "parole": None, "caratteri": None}


def list_documents(
    config: Any,
    *,
    filter_: str | None = None,
    search: str | None = None,
    only: str | None = None,
    budget_seconds: float = EXTRACTION_BUDGET_SECONDS,
) -> dict[str, Any]:
    """L'elenco della libreria. `only`: un solo documento, estratto senza budget."""
    root = Path(config.documents_dir)
    if not root.is_dir():
        return {"documenti": [], "scartati": [], "in_attesa": 0}
    by_hash, by_name = _usage(config)
    hashes = HashCache(root)
    started = time.monotonic()
    rows = []
    rejected = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if only is not None and entry.name != only:
            continue
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
            continue
        if entry.suffix.lower() not in SUPPORTED_EXTENSIONS:
            rejected.append(entry.name)
            continue
        try:
            rows.append(_row(entry, config, hashes, by_hash, by_name, started, None if only else budget_seconds))
        except FileNotFoundError:
            continue  # sparito durante l'elenco
        except Exception as exc:  # noqa: BLE001 - un file rotto non fa fallire l'elenco (C4, BASSA-2)
            rows.append({"nome": entry.name, "tipo": entry.suffix.lower().lstrip(".").upper(), **_PENDING,
                         "estraibile": False, "motivo": "errore", "errore": type(exc).__name__, "usato_in": 0, "episodi": []})
    hashes.save()
    if only is not None and not rows:
        raise DocumentError(f"Documento non trovato nella libreria Documenti: '{only}'.")
    if filter_ == "mai_usati":
        rows = [r for r in rows if r["usato_in"] == 0]
    elif filter_ == "gia_usati":
        rows = [r for r in rows if r["usato_in"] > 0]
    elif filter_ == "con_problemi":
        rows = [r for r in rows if r["estraibile"] is False]
    if search:
        needle = _normalize(search.strip())
        rows = [r for r in rows if needle in _normalize(r["nome"])]
    return {"documenti": rows, "scartati": rejected, "in_attesa": sum(1 for r in rows if r.get("in_attesa"))}


def _row(entry: Path, config: Any, hashes: HashCache, by_hash: dict, by_name: dict, started: float, budget: float | None) -> dict[str, Any]:
    digest = hashes.sha256(entry)
    info = cached_extraction(config, digest)
    pending = False
    if info is None:
        if budget is None:
            info = extraction_info(entry, config, digest)
        elif entry.suffix.lower() in TEXT_EXTENSIONS and time.monotonic() - started < budget:
            try:
                info = extraction_info(entry, config, digest, lock_timeout=0)
            except LockTimeout:
                info, pending = dict(_PENDING), True
        else:
            info, pending = dict(_PENDING), True
    runs = sorted(by_hash.get(digest, set()) | by_name.get(entry.name, set()))
    stat = entry.stat()
    return {
        "nome": entry.name,
        "tipo": entry.suffix.lower().lstrip(".").upper(),
        "dimensione_bytes": stat.st_size,
        "caricato_il": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
        "sha256": digest,
        **info,
        "in_attesa": pending,
        "usato_in": len(runs),
        "episodi": runs,
    }


__all__ = ["CACHE_DIRNAME", "FILTERS", "extraction_info", "list_documents"]
