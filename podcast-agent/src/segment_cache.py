"""Lettura della cache dei segmenti di un episodio gia' prodotto.

Estratto da src/retake.py: sia `tools/retake.py` sia `tools/mix.py` (e la
funzione di mix in `src/music_mixer.py`) hanno bisogno di ricaricare lo
stesso copione, la stessa voice_map DELLA RUN e le chiavi dei segmenti che ne
discendono, senza mai toccare il provider. Prima viveva solo in retake.py;
ora che anche il mix delle musiche rimonta la voce dalla cache, il caricamento
sta in un posto solo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .audio_assembler import (
    AssemblyError,
    existing_takes,
    load_choices,
    script_segment_keys,
    segment_path,
)
from .script_writer import PodcastScript
from .voice_provider import VoiceProvider

SCRIPT_FILENAME = "conversazione.json"
VOICES_FILENAME = "voci.json"


class SegmentCacheError(AssemblyError):
    """La run non contiene quello che serve per leggere/riusare i segmenti."""


@dataclass
class RunCache:
    """La cache dei segmenti di un episodio gia' prodotto.

    Tiene insieme le tre cose che devono restare coerenti: il copione, le voci
    usate DA QUELLA RUN e le chiavi dei segmenti che ne discendono.
    """

    run_dir: Path
    script: PodcastScript
    voice_map: dict[str, str]
    provider: VoiceProvider
    keys: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, run_dir: Path | str, provider: VoiceProvider) -> "RunCache":
        run_dir = Path(run_dir).expanduser().resolve()
        script_file = run_dir / SCRIPT_FILENAME
        voices_file = run_dir / VOICES_FILENAME

        if not script_file.is_file():
            raise SegmentCacheError(
                f"Nessun copione in {script_file}: non e' una cartella di episodio, "
                "oppure il copione non e' mai stato salvato con script_save.py."
            )
        if not voices_file.is_file():
            raise SegmentCacheError(
                f"Manca {voices_file}, cioe' le voci usate da QUESTA run.\n"
                "Senza non si puo' rileggere la cache: la voce va ripresa da qui e "
                "non dal registro, altrimenti una voce riclonata nel frattempo "
                "cambierebbe timbro a meta' episodio."
            )

        script = PodcastScript.from_dict(
            json.loads(script_file.read_text(encoding="utf-8"))
        )
        voice_map = json.loads(voices_file.read_text(encoding="utf-8"))
        cache = cls(run_dir=run_dir, script=script, voice_map=voice_map, provider=provider)
        cache.keys = script_segment_keys(script, voice_map, provider)
        return cache

    # -- accessi ----------------------------------------------------------

    @property
    def segments_dir(self) -> Path:
        return self.run_dir / "segmenti"

    @property
    def extension(self) -> str:
        return self.provider.audio_format

    def turn(self, numero: int) -> dict[str, str]:
        if numero < 1 or numero > len(self.script.turni):
            raise SegmentCacheError(
                f"Il turno {numero} non esiste: il copione ne ha "
                f"{len(self.script.turni)} (da 1 a {len(self.script.turni)})."
            )
        return self.script.turni[numero - 1]

    def key(self, numero: int) -> str:
        self.turn(numero)
        return self.keys[numero - 1]

    def voice_id(self, numero: int) -> str:
        speaker = self.turn(numero)["speaker"]
        voice_id = self.voice_map.get(speaker)
        if not voice_id:
            raise SegmentCacheError(
                f"Lo speaker '{speaker}' non ha un voice_id in {VOICES_FILENAME}: "
                "questa run non e' stata prodotta con questo copione."
            )
        return str(voice_id)

    def takes(self, numero: int) -> list[int]:
        return existing_takes(self.segments_dir, self.key(numero), self.extension)

    def chosen(self, numero: int) -> int:
        return load_choices(self.segments_dir).get(self.key(numero), 1)

    def path(self, numero: int, take: int | None = None) -> Path:
        take = self.chosen(numero) if take is None else take
        return segment_path(self.segments_dir, self.key(numero), take, self.extension)

    def missing_turns(self) -> list[int]:
        """I turni il cui segmento NON e' in cache: quelli che costeranno."""
        choices = load_choices(self.segments_dir)
        missing: list[int] = []
        for index, key in enumerate(self.keys, start=1):
            take = choices.get(key, 1)
            if not segment_path(self.segments_dir, key, take, self.extension).is_file():
                missing.append(index)
        return missing

    def ordered_segments(self) -> list[Path]:
        """Il percorso del segmento scelto per ogni turno, nell'ordine del copione.

        Presuppone che `missing_turns()` sia vuoto: usato per rimontare la
        voce dalla cache senza chiamare il provider (tools/mix.py).
        """
        return [self.path(i) for i in range(1, len(self.script.turni) + 1)]

    def any_segment_on_disk(self) -> bool:
        """True se la cartella segmenti/ contiene almeno un file audio.

        Distingue "cartella vuota o assente" (episodio vecchio, o
        --purge-segments) da "segmenti presenti ma nessuno combacia con le
        chiavi attuali" (impronta di sintesi cambiata in config.yaml dopo la
        sintesi): sono due cause diverse e meritano un messaggio diverso.
        """
        if not self.segments_dir.is_dir():
            return False
        return any(self.segments_dir.glob(f"*.{self.extension}"))


__all__ = [
    "RunCache",
    "SegmentCacheError",
    "SCRIPT_FILENAME",
    "VOICES_FILENAME",
]
