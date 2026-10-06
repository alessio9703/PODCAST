"""Registro locale delle voci clonate (cache anti-riclonazione).

Formato di voices_registry.json:

{
  "Mario Rossi": {
    "audio_file": "voci_disponibili/Mario Rossi.wav",
    "audio_hash": "sha256:...",
    "provider": "fish_audio",
    "voice_id": "abc123",
    "cloned_at": "2026-09-13T10:00:00"
  }
}

Il registro e' condiviso fra tutti i processi (piu' utenti dall'interfaccia
web): ogni modifica passa da `file_lock` e RILEGGE il file dentro il lock, cosi'
due clonazioni concorrenti non si cancellano a vicenda (vedi src/fileio.py).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .fileio import file_lock, write_json_atomic

_CHUNK = 1024 * 1024


def file_hash(path: str | Path) -> str:
    """sha256 del contenuto del file, nel formato 'sha256:<hex>'."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True)
class RegistryEntry:
    name: str
    audio_file: str
    audio_hash: str
    provider: str
    voice_id: str
    cloned_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "audio_file": self.audio_file,
            "audio_hash": self.audio_hash,
            "provider": self.provider,
            "voice_id": self.voice_id,
            "cloned_at": self.cloned_at,
        }


class VoiceRegistry:
    """Lettura/scrittura del registro, con lookup per (nome, hash, provider)."""

    def __init__(self, path: str | Path, project_root: Path | None = None) -> None:
        self.path = Path(path)
        self.project_root = project_root or self.path.parent
        self._data: dict[str, dict[str, Any]] = self._load()

    # -- IO ---------------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{self.path} non e' un JSON valido ({exc}). "
                "Correggilo o cancellalo: verra' ricreato (le voci saranno riclonate)."
            ) from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"{self.path} deve contenere un oggetto JSON.")
        return raw

    def save(self) -> None:
        """Riscrive il registro con il contenuto in memoria, sotto lock.

        ATTENZIONE: sovrascrive quello che altri processi hanno scritto dopo il
        caricamento. Per aggiungere una voce si usa `record()`, che rilegge.
        """
        with file_lock(self.path):
            write_json_atomic(self.path, self._data)

    def reload(self) -> None:
        """Rilegge il registro dal disco (utile dopo una lunga attesa)."""
        self._data = self._load()

    # -- query ------------------------------------------------------------

    def _relative(self, audio_path: str | Path) -> str:
        p = Path(audio_path).resolve()
        try:
            return p.relative_to(self.project_root).as_posix()
        except ValueError:
            return p.as_posix()

    def lookup(
        self, name: str, audio_path: str | Path, provider: str
    ) -> tuple[str | None, str]:
        """Ritorna (voice_id_riusabile, motivo).

        voice_id e' None quando la voce va (ri)clonata; `motivo` e' una stringa
        leggibile da mostrare all'utente.
        """
        entry = self._data.get(name)
        current_hash = file_hash(audio_path)

        if entry is None:
            return None, "non presente nel registro"
        if entry.get("provider") != provider:
            return None, (
                f"presente ma clonata su un altro provider "
                f"({entry.get('provider')!r} invece di {provider!r})"
            )
        if not entry.get("voice_id"):
            return None, "voce nel registro senza voice_id"
        if entry.get("audio_hash") != current_hash:
            return None, "il file audio e' cambiato rispetto all'ultima clonazione"
        return str(entry["voice_id"]), "gia' clonata, riuso il voice_id esistente"

    def get(self, name: str) -> dict[str, Any] | None:
        entry = self._data.get(name)
        return dict(entry) if entry else None

    # -- mutazioni --------------------------------------------------------

    def record(
        self, name: str, audio_path: str | Path, provider: str, voice_id: str
    ) -> RegistryEntry:
        entry = RegistryEntry(
            name=name,
            audio_file=self._relative(audio_path),
            audio_hash=file_hash(audio_path),
            provider=provider,
            voice_id=voice_id,
            cloned_at=datetime.now().replace(microsecond=0).isoformat(),
        )
        # Leggi-modifica-scrivi sotto lock, rileggendo DENTRO il lock: il
        # contenuto caricato all'avvio puo' essere vecchio di minuti (una
        # clonazione dura), e riscriverlo cancellerebbe le voci registrate
        # nel frattempo da un altro processo.
        with file_lock(self.path):
            self._data = self._load()
            self._data[name] = entry.to_dict()
            write_json_atomic(self.path, self._data)
        return entry

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, name: object) -> bool:
        return name in self._data


__all__ = ["VoiceRegistry", "RegistryEntry", "file_hash"]
