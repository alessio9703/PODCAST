"""Provider finto: genera toni sinusoidali invece di chiamare un servizio reale.

Serve per i test e come esempio minimo di come si scrive un nuovo provider.
Per usarlo davvero, in config.yaml:

    active_provider: fake
    providers:
      fake:
        class: tests.fake_provider:FakeProvider
        options: {seconds_per_100_chars: 4.0}
"""

from __future__ import annotations

import io
import json
import math
import struct
import wave
from pathlib import Path
from typing import Any

from src.voice_provider import (
    ProviderAudioQualityError,
    ProviderQuotaError,
    ProviderPricing,
    VoiceInfo,
    VoiceProvider,
    VoiceProviderError,
)

SAMPLE_RATE = 44100


class FakeProvider(VoiceProvider):
    name = "fake"
    audio_format = "wav"

    def __init__(
        self,
        seconds_per_100_chars: float = 4.0,
        fail_clone: bool = False,
        voices_file: str | None = None,
        fingerprint: dict[str, Any] | None = None,
        fail_tts_after: int | None = None,
    ) -> None:
        self._fail_clone = fail_clone
        # Le voci clonate vivono sul provider, non nel processo: con
        # `voices_file` sopravvivono fra un'invocazione di un tool e l'altra,
        # che e' quello che serve per provare i percorsi in cui una voce deve
        # (o non deve) esistere ancora.
        self._voices_file = Path(voices_file) if voices_file else None
        self._voices: dict[str, VoiceInfo] = self._load_voices()
        self._counter = max(
            (int(v.split("-")[-1]) for v in self._voices if v.startswith("fake-")),
            default=0,
        )
        self._seconds_per_100 = seconds_per_100_chars
        self._fingerprint = dict(fingerprint or {})
        #: Dopo quante sintesi riuscite fingere un guasto del provider. Serve a
        #: provare che una sintesi interrotta a meta' lascia una cache usabile.
        self._fail_tts_after = fail_tts_after
        self.tts_calls: list[tuple[str, str]] = []

    def list_cloned_voices(self) -> list[VoiceInfo]:
        return list(self._voices.values())

    def synthesis_fingerprint(self) -> dict[str, Any]:
        return dict(self._fingerprint)

    # -- persistenza delle voci (solo per i test) --------------------------

    def _load_voices(self) -> dict[str, VoiceInfo]:
        if not self._voices_file or not self._voices_file.is_file():
            return {}
        raw = json.loads(self._voices_file.read_text(encoding="utf-8"))
        return {
            vid: VoiceInfo(id=vid, name=name, provider=self.name)
            for vid, name in raw.items()
        }

    def _save_voices(self) -> None:
        if not self._voices_file:
            return
        self._voices_file.parent.mkdir(parents=True, exist_ok=True)
        self._voices_file.write_text(
            json.dumps({v.id: v.name for v in self._voices.values()}, indent=2),
            encoding="utf-8",
        )

    def clone_voice(self, name: str, audio_path: str) -> str:
        if self._fail_clone:
            raise ProviderQuotaError(
                f"Credito esaurito: impossibile clonare '{name}' (simulato)."
            )
        path = Path(audio_path)
        if not path.is_file() or path.stat().st_size == 0:
            raise ProviderAudioQualityError(f"Audio non utilizzabile: {audio_path}")
        self._counter += 1
        voice_id = f"fake-{self._counter:04d}"
        self._voices[voice_id] = VoiceInfo(id=voice_id, name=name, provider=self.name)
        self._save_voices()
        return voice_id

    def text_to_speech(self, text: str, voice_id: str) -> bytes:
        if self._fail_tts_after is not None and len(self.tts_calls) >= self._fail_tts_after:
            raise VoiceProviderError(
                f"Guasto simulato dopo {self._fail_tts_after} sintesi."
            )
        # frequenza diversa per voce, cosi' i segmenti sono distinguibili a
        # orecchio; e diversa a ogni chiamata, perche' il TTS vero NON e'
        # deterministico. Senza questa variazione due take dello stesso testo
        # uscirebbero identici e un retake sarebbe indistinguibile da un no-op.
        freq = 180 + 40 * (int(voice_id.split("-")[-1]) % 6) + len(self.tts_calls)
        self.tts_calls.append((voice_id, text))
        seconds = max(0.4, len(text) / 100 * self._seconds_per_100)
        frames = int(SAMPLE_RATE * seconds)
        samples = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)))
            for i in range(frames)
        )
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(samples)
        return buffer.getvalue()

    def pricing(self) -> ProviderPricing:
        return ProviderPricing(cost_per_million_chars_usd=10.0)



def build(options: dict[str, Any]) -> FakeProvider:
    return FakeProvider(**options)
