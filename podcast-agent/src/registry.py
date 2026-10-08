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

Due chiavi riservate, che non sono voci:

  "__storico__": [   # ogni clonazione registrata, anche quelle poi sovrascritte
    {"voce": "Mario Rossi", "audio_sha256": "sha256:...", "voice_id": "abc123",
     "provider": "fish_audio", "cloned_at": "2026-10-07T10:00:00"}
  ],
  "__revoche__": [   # UNA revoca per registrazione (sha256)
    {"voce": "Mario Rossi", "audio_sha256": "sha256:...", "voice_id": ["abc123"],
     "provider": "fish_audio", "da": "Utente A", "il": "2026-10-07T18:00:00"}
  ]

Una revoca riguarda una registrazione (il suo sha256) e i voice_id clonati DA
QUELLA registrazione. Resta anche se si cancellano l'audio, il suo .json e la
riga della voce: un voice_id revocato non torna a parlare in un retake di una
run vecchia, e la stessa registrazione ricaricata risulta ancora revocata
(revisione finale, N1). Si toglie solo registrando di nuovo il consenso per
QUELLA registrazione (`lift_revocations`): il consenso su una registrazione
diversa con lo stesso nome non rende usabili i voice_id della vecchia
(revisione di sicurezza C7, ALTA-1). Lo storico serve a conoscere anche i
voice_id di clonazioni precedenti, che una riclonazione ha tolto dalla riga
della voce (C7, MEDIA-1).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import DomainError
from .fileio import file_lock, write_json_atomic

_CHUNK = 1024 * 1024

#: Chiavi riservate (revoche, storico delle clonazioni): non sono nomi di voci.
REVOCATIONS_KEY = "__revoche__"
HISTORY_KEY = "__storico__"
RESERVED_NAMES = frozenset({REVOCATIONS_KEY, HISTORY_KEY})


def file_hash(path: str | Path) -> str:
    """sha256 del contenuto del file, nel formato 'sha256:<hex>'."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class RegistryCorrupt(DomainError):
    """Le revoche o lo storico del registro non hanno la forma attesa.

    Nel dubbio ci si ferma (sicurezza C7, BASSA-2): ignorare le revoche
    illeggibili vorrebbe dire usare voci forse revocate.
    """


def _check_reserved(path: Path, key: str, data: dict[str, Any]) -> None:
    """RegistryCorrupt se la chiave riservata c'e' ma non e' una lista di oggetti."""
    if key not in data:
        return
    raw = data[key]
    if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
        raise RegistryCorrupt(
            f"{path.name}: '{key}' non e' una lista di oggetti. Mi fermo invece di ignorarla "
            "(potrebbe contenere revoche del consenso): va corretta a mano.",
            details={"file": path.name, "campo": key},
        )


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
        # Le chiavi riservate si validano SUBITO, alla lettura: una revoca o uno
        # storico illeggibili fermano anche la stima e la produzione prima di
        # spendere, non solo la scrittura dopo una clonazione pagata
        # (sicurezza C7 bis, MEDIA-2).
        for key in RESERVED_NAMES:
            _check_reserved(self.path, key, raw)
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
        return {k: v for k, v in self._data.items() if k not in RESERVED_NAMES and isinstance(v, dict)}

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

    def _reserved_list(self, key: str) -> list[dict[str, Any]]:
        """La lista sotto una chiave riservata; RegistryCorrupt se non e' ben formata."""
        _check_reserved(self.path, key, self._data)
        return list(self._data.get(key) or [])

    def revocations(self) -> list[dict[str, Any]]:
        return self._reserved_list(REVOCATIONS_KEY)

    def history(self) -> list[dict[str, Any]]:
        return self._reserved_list(HISTORY_KEY)

    def revocation_for(self, *, voice_id: str | None = None, audio_hash: str | None = None) -> dict[str, Any] | None:
        """La revoca che riguarda questo voice_id o questa registrazione, o None."""
        revocations = self.revocations()
        for item in revocations:
            if voice_id and voice_id in (item.get("voice_id") or []):
                return item
            if audio_hash and audio_hash == item.get("audio_sha256"):
                return item
        if voice_id:
            # Un voice_id che la revoca non elenca ma che lo storico lega a una
            # registrazione revocata e' revocato lo stesso (C7, terza revisione).
            for clone in self.history():
                if clone.get("voice_id") != voice_id:
                    continue
                for item in revocations:
                    if item.get("voce") == clone.get("voce") and item.get("audio_sha256") and item.get("audio_sha256") == clone.get("audio_sha256"):
                        return item
        return None

    def clones_of(self, voice: str) -> list[tuple[str | None, str]]:
        """(sha256 della registrazione, voice_id) di ogni clonazione nota della voce."""
        pairs = [(h.get("audio_sha256"), h["voice_id"]) for h in self.history() if h.get("voce") == voice and h.get("voice_id")]
        entry = self.get(voice)
        if entry and entry.get("voice_id"):
            pairs.append((entry.get("audio_hash"), entry["voice_id"]))
        return list(dict.fromkeys(pairs))

    def known_names(self) -> set[str]:
        """Tutti i nomi esatti di voce che il registro conosce (righe e storico)."""
        return set(self._voices()) | {h["voce"] for h in self.history() if isinstance(h.get("voce"), str)}

    def knows(self, voice: str) -> bool:
        """La voce e' (o e' stata) nel registro con questo nome esatto."""
        return self.get(voice) is not None or any(h.get("voce") == voice for h in self.history())

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
        if name in RESERVED_NAMES:
            raise ValueError(f"'{name}' e' un nome riservato del registro, non un nome di voce.")
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
            self._data[HISTORY_KEY] = [
                *self.history(),
                {"voce": name, "audio_sha256": entry.audio_hash, "voice_id": voice_id, "provider": provider, "cloned_at": entry.cloned_at},
            ]
            # Una revoca di QUESTA registrazione scritta mentre la clonazione era in
            # corso comprende anche il voice_id appena nato (sicurezza C7 bis, MEDIA-4).
            for item in self.revocations():
                if item.get("voce") == name and item.get("audio_sha256") == entry.audio_hash and voice_id not in (item.get("voice_id") or []):
                    item["voice_id"] = sorted({*(item.get("voice_id") or []), voice_id})
            write_json_atomic(self.path, self._data)
        return entry

    def record_revocation(
        self,
        voice: str,
        *,
        hashes: list[str | None],
        extra_clones: list[tuple[str | None, str]],
        provider: str | None,
        by: str,
        when: str,
    ) -> list[dict[str, Any]]:
        """Aggiunge le revoche, una per registrazione (sotto lock, rileggendo dentro).

        Le clonazioni note della voce (riga, storico) si leggono DENTRO il lock:
        una clonazione registrata un istante prima non resta fuori dalla revoca
        (sicurezza C7, terza revisione, MEDIA-2). `hashes`: registrazioni da
        revocare anche se mai clonate; `extra_clones`: (sha256, voice_id) trovati
        fuori dal registro (voci_usate.json delle run). Una chiave None raccoglie
        voice_id di cui non si conosce la registrazione: quella revoca non si
        toglie mai con un consenso.
        """
        with file_lock(self.path):
            self._data = self._load()
            clones: dict[str | None, list[str]] = {h: [] for h in hashes if h}
            for digest, voice_id in [*self.clones_of(voice), *extra_clones]:
                clones.setdefault(digest, []).append(voice_id)
            items = [
                {"voce": voice, "audio_sha256": digest, "voice_id": sorted(set(ids)), "provider": provider, "da": by, "il": when}
                for digest, ids in clones.items()
                if digest or ids
            ]
            self._data[REVOCATIONS_KEY] = [*self.revocations(), *items]
            write_json_atomic(self.path, self._data)
        return items

    def lift_revocations(self, voice: str, audio_hash: str) -> list[dict[str, Any]]:
        """Toglie le revoche di questa voce per QUESTA registrazione e basta.

        Si chiama solo quando il consenso viene registrato di nuovo per la
        stessa registrazione: tornano usabili lei e i voice_id clonati da lei.
        Le revoche di registrazioni diverse (un altro file con lo stesso nome)
        e i loro voice_id restano (revisione di sicurezza C7, ALTA-1).
        """
        with file_lock(self.path):
            self._data = self._load()
            kept, lifted = [], []
            for item in self.revocations():
                (lifted if item.get("voce") == voice and item.get("audio_sha256") == audio_hash else kept).append(item)
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


__all__ = ["HISTORY_KEY", "RESERVED_NAMES", "REVOCATIONS_KEY", "RegistryCorrupt", "VoiceRegistry", "RegistryEntry", "file_hash"]
