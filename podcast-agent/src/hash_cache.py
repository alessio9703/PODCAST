"""sha256 (e durata) dei file di una cartella, senza rileggerli a ogni elenco.

Gli elenchi delle librerie mostrano lo sha256 dei documenti e la durata delle
registrazioni: ricalcolarli a ogni chiamata vuol dire rileggere ogni file (e
lanciare ffmpeg per ogni voce) a ogni apertura della pagina (revisione di
sicurezza C4, MEDIA-3). Qui si tengono in `<cartella>/.cache_file.json`,
indicizzati per nome e validi finche' dimensione, data di modifica (in
nanosecondi) e inode non cambiano: un file sostituito, anche con lo stesso
nome, si ricalcola.

La cache e' un'ottimizzazione, mai una fonte di verita': se non si legge o
non si scrive, si calcola e basta. Dove conta davvero (consenso, gate,
clonazione) lo sha256 si calcola sempre sui byte.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from .fileio import file_lock, write_json_atomic
from .registry import file_hash

CACHE_FILENAME = ".cache_file.json"


def _signature(stat: os.stat_result) -> list[int]:
    return [stat.st_size, stat.st_mtime_ns, stat.st_ino]


class HashCache:
    """Cache per UNA cartella. Si carica una volta, si salva alla fine (save())."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.target = self.directory / CACHE_FILENAME
        self._entries: dict[str, Any] = self._load()
        self._dirty: dict[str, Any] = {}

    def _load(self) -> dict[str, Any]:
        if not self.target.is_file() or self.target.is_symlink():
            return {}
        try:
            raw = json.loads(self.target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _valid(self, path: Path) -> tuple[dict[str, Any] | None, list[int]]:
        signature = _signature(path.stat())
        entry = self._entries.get(path.name)
        if isinstance(entry, dict) and entry.get("firma") == signature:
            return entry, signature
        return None, signature

    def sha256(self, path: Path) -> str:
        entry, signature = self._valid(path)
        if entry and isinstance(entry.get("sha256"), str):
            return entry["sha256"]
        digest = file_hash(path)
        # La firma si rilegge DOPO il calcolo: se il file e' cambiato nel
        # frattempo, il valore non si salva (vale solo per questa chiamata).
        if _signature(path.stat()) == signature:
            self._store(path.name, signature, sha256=digest)
        return digest

    def value(self, path: Path, key: str, compute: Callable[[Path], Any]) -> Any:
        """Un valore derivato dal file (es. la durata), con la stessa validita'."""
        entry, signature = self._valid(path)
        if entry and key in entry:
            return entry[key]
        result = compute(path)
        if _signature(path.stat()) == signature:
            self._store(path.name, signature, **{key: result})
        return result

    def _store(self, name: str, signature: list[int], **values: Any) -> None:
        current = self._entries.get(name)
        entry = dict(current) if isinstance(current, dict) and current.get("firma") == signature else {"firma": signature}
        entry.update(values)
        self._entries[name] = entry
        self._dirty[name] = entry

    def save(self) -> None:
        """Scrive le voci nuove, rileggendo il file sotto lock (piu' processi)."""
        if not self._dirty or self.target.is_symlink() or not self.directory.is_dir():
            return
        try:
            with file_lock(self.target, timeout=5):
                current = self._load()
                current.update(self._dirty)
                existing = {p.name for p in self.directory.iterdir()}
                write_json_atomic(self.target, {k: v for k, v in current.items() if k in existing})
            self._dirty = {}
        except Exception:  # noqa: BLE001 - la cache e' un'ottimizzazione: se non si scrive, pazienza
            pass


__all__ = ["CACHE_FILENAME", "HashCache"]
