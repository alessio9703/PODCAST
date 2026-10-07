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

Le revoche del consenso stanno sotto una chiave riservata, che non e' una voce:

  "__revoche__": [
    {"voce": "Mario Rossi", "audio_sha256": ["sha256:..."], "voice_id": ["abc123"],
     "provider": "fish_audio", "da": "Utente A", "il": "2026-10-07T18:00:00"}
  ]

Restano anche se si cancellano la registrazione, il suo .json e la riga della
voce: un voice_id revocato non torna a parlare in un retake di una run vecchia,
e la stessa registrazione ricaricata risulta ancora revocata (revisione
finale, N1). Una revoca si toglie solo registrando di nuovo il consenso per
quella registrazione (`lift_revocations`).
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

#: Chiave riservata delle revoche: non e' il nome di una voce.
REVOCATIONS_KEY = "__revoche__"


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

    def _voices(self) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self._data.items() if k != REVOCATIONS_KEY and isinstance(v, dict)}

    def lookup(
        self, name: str, audio_path: str | Path, provider: str, *, audio_hash: str | None = None
    ) -> tuple[str | None, str]:
        """Ritorna (voice_id_riusabile, motivo).

        voice_id e' None quando la voce va (ri)clonata; `motivo` e' una stringa
        leggibile da mostrare all'utente.
        """
        entry = self._voices().get(name)
        # `audio_hash` gia' calcolato da chi chiama (elenchi in cache): solo per
        # mostrare lo stato, mai per decidere una clonazione.
        current_hash = audio_hash or file_hash(audio_path)

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
        entry = self._voices().get(name)
        return dict(entry) if entry else None

    def revocations(self) -> list[dict[str, Any]]:
        raw = self._data.get(REVOCATIONS_KEY)
        return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []

    def revocation_for(self, *, voice_id: str | None = None, audio_hash: str | None = None) -> dict[str, Any] | None:
        """La revoca che riguarda questo voice_id o questa registrazione, o None."""
        for item in self.revocations():
            if voice_id and voice_id in (item.get("voice_id") or []):
                return item
            if audio_hash and audio_hash in (item.get("audio_sha256") or []):
                return item
        return None

    # -- mutazioni --------------------------------------------------------

    def record(
        self,
        name: str,
        audio_path: str | Path,
        provider: str,
        voice_id: str,
        *,
        audio_hash: str | None = None,
    ) -> RegistryEntry:
        """Registra una voce clonata.

        Il nome riservato alle revoche non e' accettato.

        `audio_hash`: lo sha256 dei byte effettivamente clonati, calcolato
        PRIMA della clonazione. Senza, si calcola sul file ora: se il file e'
        cambiato durante la clonazione, il voice_id verrebbe legato
        all'audio sbagliato (revisione di sicurezza C2, ALTA-1).
        """
        if name == REVOCATIONS_KEY:
            raise ValueError(f"'{REVOCATIONS_KEY}' e' riservato alle revoche del consenso, non e' un nome di voce.")
        entry = RegistryEntry(
            name=name,
            audio_file=self._relative(audio_path),
            audio_hash=audio_hash or file_hash(audio_path),
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

    def record_revocation(
        self,
        voice: str,
        *,
        audio_hashes: list[str],
        voice_ids: list[str],
        provider: str | None,
        by: str,
        when: str,
    ) -> dict[str, Any]:
        """Aggiunge una revoca (sotto lock, rileggendo dentro il lock)."""
        item = {
            "voce": voice,
            "audio_sha256": sorted({h for h in audio_hashes if h}),
            "voice_id": sorted({v for v in voice_ids if v}),
            "provider": provider,
            "da": by,
            "il": when,
        }
        with file_lock(self.path):
            self._data = self._load()
            self._data[REVOCATIONS_KEY] = [*self.revocations(), item]
            write_json_atomic(self.path, self._data)
        return item

    def lift_revocations(self, voice: str, audio_hash: str) -> list[dict[str, Any]]:
        """Toglie le revoche di questa voce per QUESTA registrazione.

        Si chiama solo quando il consenso viene registrato di nuovo per la
        stessa registrazione. Le revoche di registrazioni diverse (un altro
        file con lo stesso nome) restano: i loro voice_id non tornano usabili.
        """
        with file_lock(self.path):
            self._data = self._load()
            kept, lifted = [], []
            for item in self.revocations():
                (lifted if item.get("voce") == voice and audio_hash in (item.get("audio_sha256") or []) else kept).append(item)
            if lifted:
                if kept:
                    self._data[REVOCATIONS_KEY] = kept
                else:
                    self._data.pop(REVOCATIONS_KEY, None)
                write_json_atomic(self.path, self._data)
        return lifted

    def __len__(self) -> int:
        return len(self._voices())

    def __contains__(self, name: object) -> bool:
        return name in self._voices()


__all__ = ["REVOCATIONS_KEY", "VoiceRegistry", "RegistryEntry", "file_hash"]
