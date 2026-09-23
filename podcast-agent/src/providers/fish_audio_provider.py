"""Implementazione concreta di VoiceProvider per Fish Audio.

API di riferimento: https://docs.fish.audio/api-reference/introduction
  - GET  /model?self=true   elenco dei modelli vocali dell'utente
  - POST /model             creazione (clone) di un modello TTS, multipart
  - POST /v1/tts            sintesi vocale, ritorna i byte audio in streaming

Questo e' l'UNICO file che conosce Fish Audio. Il resto dell'agente vede
solo l'interfaccia VoiceProvider.
"""

from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import requests

from ..voice_provider import (
    ProviderAudioQualityError,
    ProviderAuthError,
    ProviderPricing,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    VoiceInfo,
    VoiceProvider,
    VoiceProviderError,
)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

#: Parametri di sintesi accettati sotto `options.tts` in config.yaml e passati
#: nel body di POST /v1/tts. Sono la leva "di macchina" sulla naturalezza
#: della dizione; la leva "di scrittura" sono le inline tag [fra quadre] che
#: stanno gia' dentro il testo del turno e che qui non richiedono nessun
#: trattamento speciale: viaggiano come testo normale.
_TTS_PARAMS = frozenset(
    {"normalize", "speed", "volume", "temperature", "top_p", "repetition_penalty"}
)

#: Di questi, quelli che l'API vuole ANNIDATI dentro l'oggetto `prosody` invece
#: che al primo livello del body. E' una differenza che non da' errore: i campi
#: sconosciuti al primo livello vengono ignorati in silenzio, quindi speed e
#: volume scritti piatti semplicemente non hanno effetto. Vedi lo schema
#: TTSRequest in https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech
#: (prosody: {speed, volume, normalize_loudness}; normalize, temperature,
#: top_p, repetition_penalty restano al primo livello).
_PROSODY_PARAMS = frozenset({"speed", "volume"})

#: Usati quando config.yaml non dice niente: la resa storica dell'agente.
_TTS_DEFAULTS: dict[str, Any] = {"normalize": True}


class FishAudioProvider(VoiceProvider):
    name = "fish_audio"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.fish.audio",
        tts_model: str = "s2.1-pro",
        audio_format: str = "wav",
        mp3_bitrate: int = 128,
        latency: str = "normal",
        visibility: str = "private",
        timeout_seconds: int = 180,
        max_retries: int = 3,
        cost_per_million_chars_usd: float | None = None,
        chars_per_second: float | None = None,
        tts: dict[str, Any] | None = None,
    ) -> None:
        if not api_key:
            raise ProviderAuthError(
                "API key di Fish Audio mancante. Generane una su "
                "https://fish.audio/app/api-keys/ e mettila in .env "
                "(FISH_AUDIO_API_KEY=...)."
            )
        if audio_format not in {"wav", "mp3"}:
            raise VoiceProviderError(
                f"audio_format '{audio_format}' non supportato dall'agente: usa 'wav' o 'mp3'."
            )

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._tts_model = tts_model
        self.audio_format = audio_format
        self._mp3_bitrate = mp3_bitrate
        self._latency = latency
        self._visibility = visibility
        self._timeout = timeout_seconds
        self._max_retries = max(1, max_retries)
        self._cost_per_million_chars = cost_per_million_chars_usd
        self.chars_per_second = chars_per_second

        tts_options = dict(tts or {})
        unknown = sorted(set(tts_options) - _TTS_PARAMS)
        if unknown:
            raise VoiceProviderError(
                f"Parametri sconosciuti in providers.fish_audio.options.tts: "
                f"{', '.join(unknown)}. Ammessi: {', '.join(sorted(_TTS_PARAMS))}."
            )
        self._tts_options = {**_TTS_DEFAULTS, **tts_options}

        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {self._api_key}"})

    # ------------------------------------------------------------------
    # VoiceProvider
    # ------------------------------------------------------------------

    def list_cloned_voices(self) -> list[VoiceInfo]:
        voices: list[VoiceInfo] = []
        page = 1
        while True:
            payload = self._request_json(
                "GET",
                "/model",
                params={"self": "true", "page_size": 100, "page_number": page},
            )
            items = payload.get("items") or []
            for item in items:
                voices.append(
                    VoiceInfo(
                        id=item.get("_id", ""),
                        name=item.get("title", ""),
                        provider=self.name,
                        created_at=item.get("created_at"),
                        extra={
                            "state": item.get("state"),
                            "visibility": item.get("visibility"),
                            "languages": item.get("languages"),
                        },
                    )
                )
            if len(items) < 100 or not payload.get("has_more", False):
                break
            page += 1
        return voices

    def clone_voice(self, name: str, audio_path: str) -> str:
        path = Path(audio_path)
        if not path.is_file():
            raise VoiceProviderError(f"File audio non trovato: {audio_path}")
        if path.stat().st_size == 0:
            raise ProviderAudioQualityError(f"Il file audio e' vuoto: {audio_path}")

        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            files = {"voices": (path.name, handle, mime)}
            data = {
                "type": "tts",
                "title": name,
                "train_mode": "fast",
                "visibility": self._visibility,
                "description": f"Voce clonata da podcast-agent ({path.name})",
            }
            payload = self._request_json(
                "POST", "/model", data=data, files=files, retry=False
            )

        voice_id = payload.get("_id")
        if not voice_id:
            raise VoiceProviderError(
                f"Fish Audio non ha restituito un ID per la voce '{name}': {payload}"
            )
        state = payload.get("state")
        if state == "failed":
            raise ProviderAudioQualityError(
                f"Fish Audio ha rifiutato l'audio di riferimento per '{name}' "
                f"(state=failed). Prova con una registrazione piu' pulita e lunga."
            )
        return str(voice_id)

    def build_tts_body(self, text: str, voice_id: str) -> dict[str, Any]:
        """Costruisce il body di POST /v1/tts.

        Separato da `text_to_speech()` perche' la FORMA del body e' la cosa
        che si rompe in silenzio (vedi _PROSODY_PARAMS) e cosi' e' verificabile
        senza chiamare l'API. I test controllano questo metodo.

        Le inline tag [fra quadre] eventualmente presenti nel testo restano
        dentro `text`: le interpreta il modello, non noi. Non ripulirle.
        """
        body: dict[str, Any] = {
            "text": text,
            "reference_id": voice_id,
            "format": self.audio_format,
            "latency": self._latency,
        }
        prosody = {
            key: value
            for key, value in self._tts_options.items()
            if key in _PROSODY_PARAMS
        }
        if prosody:
            body["prosody"] = prosody
        body.update(
            {
                key: value
                for key, value in self._tts_options.items()
                if key not in _PROSODY_PARAMS
            }
        )
        if self.audio_format == "mp3":
            body["mp3_bitrate"] = self._mp3_bitrate
        return body

    def text_to_speech(self, text: str, voice_id: str) -> bytes:
        if not text.strip():
            raise VoiceProviderError("Testo vuoto: niente da sintetizzare.")
        if not voice_id:
            raise VoiceProviderError("voice_id mancante per la sintesi.")

        body = self.build_tts_body(text, voice_id)

        response = self._request(
            "POST",
            "/v1/tts",
            json=body,
            headers={"model": self._tts_model, "Content-Type": "application/json"},
            stream=True,
        )
        audio = response.content
        if not audio:
            raise VoiceProviderError(
                "Fish Audio ha restituito una risposta audio vuota: "
                f"testo={text[:60]!r} voice_id={voice_id}"
            )
        return audio

    def pricing(self) -> ProviderPricing:
        return ProviderPricing(cost_per_million_chars_usd=self._cost_per_million_chars)

    def synthesis_fingerprint(self) -> dict[str, Any]:
        """Cosa di Fish Audio cambia l'audio a parita' di testo e di voce.

        Il modello e i parametri di `options.tts`: sono esattamente i campi che
        `build_tts_body()` mette nel body, meno il testo e la voce. Il
        `latency` non c'e' perche' riguarda il trasporto, non la resa.

        Serve alla cache dei segmenti: cambiare `tts_model` da s2.1-pro a
        drama-3-preview senza invalidarla rimonterebbe l'episodio con l'audio
        del modello vecchio, e la differenza si sentirebbe solo a orecchio.
        """
        return {
            "tts_model": self._tts_model,
            "tts": dict(sorted(self._tts_options.items())),
        }

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise VoiceProviderError(
                f"Risposta non-JSON da Fish Audio ({method} {path}): "
                f"{response.text[:200]}"
            ) from exc

    def _request(
        self, method: str, path: str, *, retry: bool = True, **kwargs: Any
    ) -> requests.Response:
        url = f"{self._base_url}{path}"
        attempts = self._max_retries if retry else 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = self._session.request(
                    method, url, timeout=self._timeout, **kwargs
                )
            except requests.Timeout as exc:
                last_error = ProviderTimeoutError(
                    f"Timeout ({self._timeout}s) su {method} {path} di Fish Audio."
                )
                if attempt == attempts:
                    raise last_error from exc
            except requests.RequestException as exc:
                last_error = VoiceProviderError(
                    f"Errore di rete su {method} {path} di Fish Audio: {exc}"
                )
                if attempt == attempts:
                    raise last_error from exc
            else:
                if response.status_code < 400:
                    return response
                if response.status_code in _RETRYABLE_STATUS and attempt < attempts:
                    time.sleep(self._backoff(response, attempt))
                    continue
                raise self._to_error(response, method, path)

            time.sleep(2**attempt)

        assert last_error is not None  # pragma: no cover
        raise last_error

    @staticmethod
    def _backoff(response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return float(2**attempt)

    @staticmethod
    def _to_error(
        response: requests.Response, method: str, path: str
    ) -> VoiceProviderError:
        detail = response.text.strip()[:400] or "(nessun dettaglio)"
        status = response.status_code
        prefix = f"Fish Audio {method} {path} -> HTTP {status}"

        if status in (401, 403):
            return ProviderAuthError(
                f"{prefix}: API key non valida o senza permessi. {detail}"
            )
        if status == 402:
            return ProviderQuotaError(
                f"{prefix}: credito esaurito / pagamento richiesto. {detail}"
            )
        if status == 429:
            return ProviderRateLimitError(f"{prefix}: rate limit superato. {detail}")
        if status in (413, 415, 422):
            return ProviderAudioQualityError(
                f"{prefix}: file audio o richiesta non accettata "
                f"(formato, dimensione o qualita'). {detail}"
            )
        if status >= 500:
            return VoiceProviderError(f"{prefix}: errore del servizio. {detail}")
        return VoiceProviderError(f"{prefix}: {detail}")


def build(options: dict[str, Any]) -> FishAudioProvider:
    """Factory usata dal loader: traduce le opzioni di config.yaml in argomenti."""
    options = dict(options)
    api_key_env = options.pop("api_key_env", "FISH_AUDIO_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ProviderAuthError(
            f"Variabile d'ambiente {api_key_env} non impostata.\n"
            "Genera una API key su https://fish.audio/app/api-keys/ e scrivila "
            "nel file .env del progetto:\n"
            f"    {api_key_env}=fa-xxxxxxxxxxxxxxxx"
        )
    return FishAudioProvider(api_key=api_key, **options)
