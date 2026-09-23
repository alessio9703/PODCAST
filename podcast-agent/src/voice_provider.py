"""Interfaccia astratta per i provider di voice cloning / text-to-speech.

Tutto il resto dell'agente parla SOLO con questa interfaccia: per sostituire
Fish Audio con un altro servizio basta scrivere una nuova sottoclasse e
cambiare `active_provider` in config.yaml.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------
# Errori — l'agente li intercetta per riportare il problema in modo chiaro
# invece di fallire in silenzio a meta' montaggio.
# --------------------------------------------------------------------------


class VoiceProviderError(RuntimeError):
    """Errore generico del provider vocale."""


class ProviderAuthError(VoiceProviderError):
    """API key mancante, non valida o senza permessi."""


class ProviderRateLimitError(VoiceProviderError):
    """Troppe richieste: rate limit del provider."""


class ProviderQuotaError(VoiceProviderError):
    """Credito esaurito / quota superata."""


class ProviderAudioQualityError(VoiceProviderError):
    """Audio di riferimento rifiutato (qualita' insufficiente, formato, durata)."""


class ProviderTimeoutError(VoiceProviderError):
    """Il provider non ha risposto entro il timeout configurato."""


# --------------------------------------------------------------------------
# Modelli dati
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceInfo:
    """Una voce gia' clonata sul provider."""

    id: str
    name: str
    provider: str
    created_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderPricing:
    """Costo indicativo dichiarato dal provider (puo' essere assente)."""

    cost_per_million_chars_usd: float | None = None
    currency: str = "USD"

    @property
    def is_known(self) -> bool:
        return self.cost_per_million_chars_usd is not None


# --------------------------------------------------------------------------
# Interfaccia
# --------------------------------------------------------------------------


class VoiceProvider(abc.ABC):
    """Contratto minimo che ogni provider vocale deve rispettare."""

    #: identificatore stabile scritto nel registro delle voci clonate
    name: str = "abstract"

    #: estensione dei byte restituiti da text_to_speech ("wav" o "mp3")
    audio_format: str = "wav"

    #: Quanti caratteri al secondo pronuncia questo motore, usato SOLO per
    #: stimare la durata. E' una proprieta' del modello TTS, non del copione:
    #: lo stesso testo letto da due modelli diversi dura diversamente. None =
    #: non dichiarato, e la stima ripiega sulle parole al minuto.
    chars_per_second: float | None = None

    # -- voci ---------------------------------------------------------------

    @abc.abstractmethod
    def list_cloned_voices(self) -> list[VoiceInfo]:
        """Elenca le voci gia' clonate sul provider."""

    @abc.abstractmethod
    def clone_voice(self, name: str, audio_path: str) -> str:
        """Clona una voce da un file audio e ritorna l'ID assegnato dal provider."""

    # -- sintesi ------------------------------------------------------------

    @abc.abstractmethod
    def text_to_speech(self, text: str, voice_id: str) -> bytes:
        """Sintetizza una battuta con la voce indicata e ritorna i byte audio."""

    # -- opzionale ----------------------------------------------------------

    def pricing(self) -> ProviderPricing:
        """Costo indicativo per la stima. Default: sconosciuto."""
        return ProviderPricing()

    def synthesis_fingerprint(self) -> dict[str, Any]:
        """Cosa, oltre al testo e alla voce, determina l'audio prodotto.

        Entra nella chiave dei segmenti in cache (`segment_key` in
        audio_assembler): se cambia il modello TTS o un parametro di sintesi,
        la chiave cambia e i segmenti vecchi smettono di essere riusati. Senza
        questo, un cambio di `temperature` in config.yaml non si sentirebbe
        nell'episodio, perche' verrebbe rimontato l'audio di prima.

        Default vuoto: un provider che non dichiara niente si comporta come se
        la sola coppia testo+voce bastasse a descrivere l'audio. Deve
        contenere solo valori serializzabili in JSON, e **mai** la API key.
        """
        return {}

    def __repr__(self) -> str:  # pragma: no cover - solo diagnostica
        return f"<{type(self).__name__} name={self.name!r} format={self.audio_format!r}>"
