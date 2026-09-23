"""Scansione della cartella delle registrazioni e risoluzione dei nomi.

I file devono chiamarsi "Nome Cognome.<estensione>". L'agente non inventa mai
un nome: se la cartella e' vuota, o se il nome scelto dall'utente non
corrisponde a nessun file, lo segnala e chiede istruzioni.
"""

from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass
from pathlib import Path


class VoiceLibraryError(RuntimeError):
    """Problema nella cartella delle voci disponibili."""


@dataclass(frozen=True)
class AvailableVoice:
    name: str
    path: Path

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size


def _normalize(value: str) -> str:
    """Minuscole, senza accenti e senza spazi multipli: solo per il matching."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.lower().replace("_", " ").replace("-", " ").split())


class VoiceLibrary:
    def __init__(self, voices_dir: Path, accepted_extensions: list[str]) -> None:
        self.dir = Path(voices_dir)
        self.accepted_extensions = [e.lower() for e in accepted_extensions]
        self._voices: list[AvailableVoice] = []
        self._rejected: list[Path] = []

    # -- scansione --------------------------------------------------------

    def scan(self) -> list[AvailableVoice]:
        if not self.dir.exists():
            raise VoiceLibraryError(
                f"La cartella delle voci non esiste: {self.dir}\n"
                "Crea la cartella e mettici dentro le registrazioni, "
                'un file per persona, chiamato "Nome Cognome.wav".'
            )
        if not self.dir.is_dir():
            raise VoiceLibraryError(f"{self.dir} non e' una cartella.")

        voices: list[AvailableVoice] = []
        rejected: list[Path] = []
        for entry in sorted(self.dir.iterdir()):
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if entry.suffix.lower() in self.accepted_extensions:
                voices.append(AvailableVoice(name=entry.stem.strip(), path=entry))
            else:
                rejected.append(entry)

        self._voices = voices
        self._rejected = rejected
        return voices

    @property
    def voices(self) -> list[AvailableVoice]:
        return list(self._voices)

    @property
    def rejected_files(self) -> list[Path]:
        """File presenti nella cartella ma in un formato audio non atteso."""
        return list(self._rejected)

    @property
    def names(self) -> list[str]:
        return [v.name for v in self._voices]

    def require(self, count: int) -> None:
        """Verifica che ci siano abbastanza voci per il numero di speaker richiesto."""
        if not self._voices:
            detail = ""
            if self._rejected:
                listed = ", ".join(p.name for p in self._rejected[:5])
                detail = (
                    f"\nNella cartella ci sono {len(self._rejected)} file in un formato "
                    f"non riconosciuto ({listed}). Formati accettati: "
                    f"{', '.join(self.accepted_extensions)}."
                )
            raise VoiceLibraryError(
                f"Nessuna registrazione utilizzabile in {self.dir}.{detail}\n"
                'Aggiungi i file audio (uno per persona, "Nome Cognome.wav") e riprova.'
            )
        if len(self._voices) < count:
            raise VoiceLibraryError(
                f"Servono {count} voci ma nella cartella {self.dir} ce ne sono solo "
                f"{len(self._voices)}: {', '.join(self.names)}.\n"
                "Aggiungi altre registrazioni oppure riduci il numero di speaker."
            )

    # -- risoluzione dei nomi ---------------------------------------------

    def resolve(self, query: str) -> tuple[AvailableVoice | None, list[AvailableVoice]]:
        """Risolve un nome digitato dall'utente.

        Ritorna (match_esatto, candidati). Se match_esatto e' None, `candidati`
        contiene le somiglianze da proporre: il chiamante deve CHIEDERE conferma,
        mai indovinare.
        """
        raw = query.strip()
        if not raw:
            return None, []

        # selezione per indice (1-based), comoda da CLI
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(self._voices):
                return self._voices[index - 1], []
            return None, []

        normalized = _normalize(raw)
        exact = [v for v in self._voices if _normalize(v.name) == normalized]
        if len(exact) == 1:
            return exact[0], []
        if len(exact) > 1:
            return None, exact  # omonimi: serve conferma esplicita

        substring = [v for v in self._voices if normalized in _normalize(v.name)]
        if len(substring) == 1:
            return None, substring
        if substring:
            return None, substring

        close = difflib.get_close_matches(
            normalized, [_normalize(v.name) for v in self._voices], n=3, cutoff=0.6
        )
        candidates = [v for v in self._voices if _normalize(v.name) in close]
        return None, candidates


__all__ = ["VoiceLibrary", "AvailableVoice", "VoiceLibraryError"]
