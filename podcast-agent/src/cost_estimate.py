"""Stima di caratteri, durata e costo prima di chiamare l'API di sintesi.

Due grandezze diverse, che si contano in modo diverso:

- il **costo** si calcola sui caratteri GREZZI (`total_chars`), perche' le
  inline tag `[fra quadre]` vengono comunque inviate all'API e quindi pagate;
- la **durata** si calcola sui caratteri PARLATI (`spoken_chars`), perche' le
  stesse tag il provider le interpreta e non le pronuncia.

Confonderle e' l'errore facile: gonfia la durata di qualche punto percentuale
senza che si veda da nessuna parte.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .script_writer import PodcastScript
from .voice_provider import ProviderPricing

#: Incertezza dichiarata attorno alla stima, misurata sulle run reali.
#:
#: Su 5 episodi con drama-3-preview il modello a caratteri/secondo sbaglia in
#: media del 5,9% e nel caso peggiore del 10,1%, in entrambe le direzioni. Il
#: residuo non e' spiegato: non dipende dal modello TTS (isolato confrontando
#: lo stesso copione su s2.1-pro e drama-3), ne' dalle cifre da normalizzare,
#: ne' dalla coppia di voci. Finche' non lo sara', mostrare un valore al
#: secondo comunica una precisione che la formula non ha: da qui l'intervallo.
DURATION_UNCERTAINTY = 0.10

#: Granularita' con cui si arrotondano gli estremi dell'intervallo (secondi).
_RANGE_STEP_S = 30


def _label(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s"


@dataclass(frozen=True)
class Estimate:
    total_chars: int
    spoken_chars: int
    total_words: int
    spoken_words: int
    turns: int
    speakers: int
    pause_seconds: float
    speech_seconds: float
    rate_basis: str
    cost_usd: float | None
    cost_basis: str | None

    @property
    def total_seconds(self) -> float:
        """Stima puntuale. Per l'utente si usa l'intervallo, non questo valore."""
        return self.speech_seconds + self.pause_seconds

    @property
    def low_seconds(self) -> float:
        raw = self.total_seconds * (1 - DURATION_UNCERTAINTY)
        return max(0.0, math.floor(raw / _RANGE_STEP_S) * _RANGE_STEP_S)

    @property
    def high_seconds(self) -> float:
        raw = self.total_seconds * (1 + DURATION_UNCERTAINTY)
        return math.ceil(raw / _RANGE_STEP_S) * _RANGE_STEP_S

    @property
    def duration_label(self) -> str:
        """Intervallo, non un valore al secondo: vedi DURATION_UNCERTAINTY."""
        return f"{_label(self.low_seconds)} - {_label(self.high_seconds)}"

    def render(self) -> str:
        lines = [
            f"  Turni di parola   : {self.turns} ({self.speakers} speaker)",
            f"  Caratteri totali  : {self.total_chars:,}".replace(",", ".")
            + f" (parlati {self.spoken_chars:,})".replace(",", "."),
            f"  Parole totali     : {self.total_words:,}".replace(",", "."),
            f"  Durata stimata    : {self.duration_label} "
            f"(parlato ~{int(self.speech_seconds)}s + pause ~{int(self.pause_seconds)}s, "
            f"{self.rate_basis})",
            f"  Margine           : +/-{int(DURATION_UNCERTAINTY * 100)}% misurato "
            "sulle run reali; la durata esatta si sa solo dopo la sintesi",
        ]
        if self.cost_usd is not None:
            lines.append(f"  Costo indicativo  : ~${self.cost_usd:.2f} ({self.cost_basis})")
        else:
            lines.append(
                "  Costo indicativo  : non disponibile (il provider attivo non "
                "espone una tariffa nota in config.yaml)"
            )
        return "\n".join(lines)


def estimate(
    script: PodcastScript,
    pricing: ProviderPricing,
    *,
    chars_per_second: float | None = None,
    words_per_minute: int = 150,
    pause_between_turns_ms: int = 450,
    lead_in_ms: int = 300,
    lead_out_ms: int = 600,
    trim_silence: bool = False,
    trim_margin_head_ms: int = 0,
    trim_margin_tail_ms: int = 0,
) -> Estimate:
    """Stima durata e costo.

    `chars_per_second` e' il tasso dichiarato dal provider ed e' la via
    preferita. `words_per_minute` resta come ripiego per i provider che non
    dichiarano un tasso: e' meno affidabile perche' dipende dalla lunghezza
    media delle parole, che cambia sensibilmente da un documento all'altro.
    """
    turns = len(script.turni)

    if chars_per_second and chars_per_second > 0:
        speech_seconds = script.spoken_chars / chars_per_second
        rate_basis = f"a {chars_per_second:g} caratteri/secondo"
    elif words_per_minute > 0:
        speech_seconds = (script.spoken_words / words_per_minute) * 60
        rate_basis = (
            f"a {words_per_minute} parole/minuto - il provider non dichiara "
            "un tasso in caratteri/secondo"
        )
    else:
        speech_seconds = 0.0
        rate_basis = "nessun tasso disponibile"

    # Con trim_silence i margini lasciati dentro il taglio sono silenzio a
    # cavallo di ogni stacco: il vuoto percepito e' pausa + coda + testa, non
    # la sola pausa (lo dice anche il commento di audio.trim_* in config.yaml).
    # Senza ritaglio i bordi restano quelli naturali del TTS, variabili e non
    # prevedibili: li' non c'e' niente di sensato da aggiungere.
    gap_ms = pause_between_turns_ms
    if trim_silence:
        gap_ms += trim_margin_head_ms + trim_margin_tail_ms
    pause_seconds = (max(turns - 1, 0) * gap_ms + lead_in_ms + lead_out_ms) / 1000.0

    cost_usd: float | None = None
    cost_basis: str | None = None
    if pricing.is_known:
        rate = float(pricing.cost_per_million_chars_usd or 0.0)
        # Caratteri grezzi: le tag vengono inviate all'API, quindi si pagano.
        cost_usd = script.total_chars / 1_000_000 * rate
        cost_basis = f"${rate:.2f} / 1M caratteri"

    return Estimate(
        total_chars=script.total_chars,
        spoken_chars=script.spoken_chars,
        total_words=script.total_words,
        spoken_words=script.spoken_words,
        turns=turns,
        speakers=len(script.speakers),
        pause_seconds=pause_seconds,
        speech_seconds=speech_seconds,
        rate_basis=rate_basis,
        cost_usd=cost_usd,
        cost_basis=cost_basis,
    )


__all__ = ["Estimate", "estimate", "DURATION_UNCERTAINTY"]
