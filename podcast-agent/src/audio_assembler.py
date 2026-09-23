"""Sintesi dei turni e montaggio finale dell'episodio.

Due percorsi, scelti dal formato che il provider restituisce (config.yaml,
providers.<nome>.options.audio_format):

  wav  -> montaggio con la libreria standard `wave`, pause precise fra i turni,
          poi codifica mp3 con ffmpeg (o con il pacchetto `lameenc`).
  mp3  -> concatenazione diretta dei segmenti, senza ri-codifica ma anche senza
          pause calibrate.

I segmenti sono indirizzati per CONTENUTO, non per posizione: il nome del file
e' `<chiave>_t<take>.<ext>`, dove la chiave riassume tutto cio' che determina
l'audio (testo, voce, formato, impronta di sintesi del provider). Da qui
discendono due cose che prima non c'erano:

  - correggere una battuta costa solo quella battuta, perche' le altre chiavi
    non cambiano e i loro file vengono riusati;
  - cambiare modello TTS o parametri invalida tutto da solo, senza che nessuno
    debba ricordarsi di svuotare una cartella.

Il montaggio resta integrale: rilegge SEMPRE tutti i wav grezzi e ri-codifica
l'mp3 da zero. Non si taglia e non si ricuce un mp3 esistente.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .ffmpeg_tool import find_ffmpeg
from .script_writer import PodcastScript, spoken_text
from .voice_provider import VoiceProvider, VoiceProviderError

#: (indice, totale, speaker, gia_in_cache)
ProgressCallback = Callable[[int, int, str, bool], None]

#: Scelte dei take dentro `segmenti/`: {chiave: numero_take}, default 1.
CHOICES_FILENAME = "scelte.json"
#: Vista derivata leggibile (turno -> speaker, chiave, take, file). La fonte di
#: verita' resta la chiave: questo file si puo' cancellare senza perdere nulla.
INDEX_FILENAME = "indice.json"
#: Anteprime dei take generate da tools/retake.py.
PREVIEWS_DIRNAME = "anteprime"


class AssemblyError(RuntimeError):
    """Errore durante la sintesi o il montaggio."""


class MissingSpeakerVoice(AssemblyError):
    """Uno speaker dello script non ha una voce assegnata."""


@dataclass
class AssemblyResult:
    path: Path
    audio_format: str
    duration_seconds: float | None
    segments: list[Path]
    warnings: list[str] = field(default_factory=list)
    #: Durata di ogni singolo turno DOPO il ritaglio, cioe' quella che finisce
    #: davvero nell'episodio. Serve solo a misurare la velocita' di lettura per
    #: singola voce (vedi tools/synthesize.py); la stima non la usa. Vuota sul
    #: percorso mp3, dove i segmenti non vengono decodificati.
    segment_seconds: list[float] = field(default_factory=list)
    #: Dove sta ogni turno DENTRO il master, in secondi: [{"inizio", "fine"}].
    #: E' quello che permette di risalire dal minutaggio alla battuta
    #: (tools/turn_at.py). Vuota sul percorso mp3: li' i segmenti non vengono
    #: decodificati, quindi le durate non si conoscono.
    turn_timeline: list[dict[str, float]] = field(default_factory=list)


@dataclass
class SynthesisResult:
    """Esito della sintesi: cosa e' stato pagato e cosa no."""

    paths: list[Path]
    #: chiave di contenuto di ogni turno, nello stesso ordine del copione
    keys: list[str]
    #: take effettivamente usato per ogni turno
    takes: list[int]
    #: numeri di turno (da 1) sintetizzati ORA, cioe' quelli che sono costati
    generated_turns: list[int] = field(default_factory=list)
    #: numeri di turno (da 1) ripresi dalla cache, a costo zero
    reused_turns: list[int] = field(default_factory=list)
    #: segmenti rimasti sospetti (sintesi troncata) anche dopo i retry automatici
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Cache dei segmenti, indirizzata per contenuto
# ----------------------------------------------------------------------


def segment_key(
    text: str, voice_id: str, provider: VoiceProvider, occurrence: int = 1
) -> str:
    """Chiave di un segmento: tutto cio' che ne determina l'audio.

    `occurrence` e' la n-esima volta che quella voce dice esattamente quel
    testo nel copione. Senza, due turni identici ("Esatto.", "Si'.")
    condividerebbero un solo file: si risparmierebbe una chiamata, ma lo stesso
    identico audio ripetuto si sente come un nastro, e un retake sull'uno
    cambierebbe di nascosto anche l'altro. Contando le occorrenze invece che le
    posizioni, inserire o togliere un turno DIVERSO non sposta le chiavi degli
    altri, che e' il motivo per cui la cache regge alle revisioni.

    Troncata a 16 cifre esadecimali: sono 64 bit, abbondanti per le poche
    decine di segmenti di un episodio, e tengono i nomi dei file leggibili.
    """
    payload = {
        "text": text,
        "voice_id": voice_id,
        "provider": provider.name,
        "audio_format": provider.audio_format,
        "fingerprint": provider.synthesis_fingerprint(),
        "occurrence": occurrence,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def script_segment_keys(
    script: PodcastScript, voice_map: dict[str, str], provider: VoiceProvider
) -> list[str]:
    """Le chiavi di tutti i turni, nell'ordine del copione."""
    seen: dict[tuple[str, str], int] = {}
    keys: list[str] = []
    for turn in script.turni:
        voice_id = voice_map.get(turn["speaker"], "")
        identity = (voice_id, turn["testo"])
        seen[identity] = seen.get(identity, 0) + 1
        keys.append(segment_key(turn["testo"], voice_id, provider, seen[identity]))
    return keys


def segment_path(segments_dir: Path, key: str, take: int, extension: str) -> Path:
    return segments_dir / f"{key}_t{take}.{extension}"


def existing_takes(segments_dir: Path, key: str, extension: str) -> list[int]:
    """Numeri dei take gia' presenti su disco per quella chiave, ordinati."""
    takes: list[int] = []
    for candidate in segments_dir.glob(f"{key}_t*.{extension}"):
        suffix = candidate.stem.split("_t")[-1]
        if suffix.isdigit():
            takes.append(int(suffix))
    return sorted(takes)


def load_choices(segments_dir: Path) -> dict[str, int]:
    """Legge scelte.json. Un file illeggibile vale come 'nessuna scelta'.

    Deliberatamente indulgente: la scelta di un take e' una preferenza, non un
    dato da cui dipende la correttezza. Perderla significa tornare al take 1,
    non rompere l'episodio.
    """
    target = segments_dir / CHOICES_FILENAME
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): int(v) for k, v in raw.items() if str(v).isdigit()}


def save_choices(segments_dir: Path, choices: dict[str, int]) -> Path:
    segments_dir.mkdir(parents=True, exist_ok=True)
    target = segments_dir / CHOICES_FILENAME
    _write_atomic(
        target,
        (json.dumps(choices, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    return target


def write_segment_index(
    segments_dir: Path,
    script: PodcastScript,
    keys: list[str],
    takes: list[int],
    paths: list[Path],
) -> Path:
    """Scrive indice.json: a cosa corrisponde ogni file, per chi apre la cartella.

    E' una VISTA, non uno stato: si ricalcola per intero a ogni sintesi e
    cancellarla non perde niente. Esiste perche' una cartella di nomi
    esadecimali, da sola, non si legge.
    """
    turni = [
        {
            "turno": index,
            "speaker": turn["speaker"],
            "chiave": key,
            "take": take,
            "file": path.name,
            "testo": turn["testo"],
        }
        for index, (turn, key, take, path) in enumerate(
            zip(script.turni, keys, takes, paths), start=1
        )
    ]
    target = segments_dir / INDEX_FILENAME
    payload = {
        "nota": "Vista derivata della cache dei segmenti: rigenerata a ogni "
                "sintesi. La fonte di verita' e' la chiave nel nome del file.",
        "turni": turni,
    }
    _write_atomic(
        target,
        (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    return target


@dataclass
class AudioAssembler:
    provider: VoiceProvider
    pause_between_turns_ms: int = 450
    lead_in_ms: int = 300
    lead_out_ms: int = 600
    output_mp3_bitrate: int = 192
    #: Ritaglia il silenzio che il TTS mette ai bordi di ogni segmento prima di
    #: montarlo. Senza, la pausa percepita fra due turni non e'
    #: `pause_between_turns_ms` ma `coda + pausa + testa`, che varia da turno a
    #: turno: misurata su drama-3-preview la coda e' 905 ms mediani (160-1490),
    #: cioe' il vuoto reale era 3 volte quello configurato e mai costante.
    #: Vedi LEGENDA.md, sezione sulla dizione italiana.
    trim_silence: bool = True
    #: Soglia di ampiezza, in percentuale del 95esimo percentile del picco DEL
    #: SEGMENTO: relativa perche' le voci hanno livelli diversi.
    trim_threshold_pct: float = 1.5
    #: Margini lasciati dentro il taglio. In coda e' piu' generoso di proposito:
    #: la fine di un'enunciazione ha un rilascio d'aria che, tagliato, si sente
    #: come un troncamento; 80 ms di silenzio in piu' non si sentono.
    trim_margin_head_ms: int = 30
    trim_margin_tail_ms: int = 80
    #: Comprime i vuoti INTERNI a un turno (un punto fermo o una domanda nel
    #: mezzo del testo) che superano `cap_internal_silence_ms`, senza toccare
    #: il parlato. A differenza del ritaglio dei bordi, qui non c'e' un
    #: `pause_between_turns_ms` a governare la durata: il modello (specie in
    #: modalita' preview) tratta una frase interna come un punto in cui
    #: inserire una pausa di durata imprevedibile, da poche centinaia di
    #: millisecondi a oltre un secondo e mezzo. Misurato sull'episodio "Il
    #: costo della pioggia" (16/09/2026): su 42 turni, 36 avevano almeno un
    #: vuoto interno >=250ms. Le tag [fra quadre] ci stanno spesso vicino
    #: (copione.md le vuole nel punto in cui cambia l'intenzione, che di
    #: solito e' un confine di frase), ma sono la frase spezzata a produrre
    #: il vuoto, non la tag: capitava anche nei turni senza nessuna tag.
    cap_internal_silence: bool = True
    #: Tetto oltre il quale un vuoto interno viene accorciato. Va tenuto
    #: sopra pause_between_turns_ms: un vuoto interno a un turno (stesso
    #: speaker che continua) puo' essere leggermente piu' lungo di quello
    #: fra due turni (cambio di chi parla) senza sembrare innaturale.
    cap_internal_silence_ms: int = 300
    #: Rileva sintesi troncate a meta' frase (vedi detect_dropped_audio) e le
    #: ritenta prima di accettarle in cache. Richiede audio_format: wav (solo
    #: li' il segmento e' decodificabile) e un chars_per_second dichiarato dal
    #: provider (senza, non c'e' un tempo atteso con cui confrontare).
    dropout_detection: bool = True
    #: Sotto questa frazione del tempo di lettura atteso, il segmento e'
    #: sospetto. Tarata larga apposta: il parlato reale, pause fra le frasi
    #: comprese, non riempie mai il 100% del tempo atteso.
    dropout_min_ratio: float = 0.5
    #: Tentativi AGGIUNTIVI oltre al primo quando un segmento risulta sospetto.
    #: Il glitch osservato (il modello si ferma a meta' frase e restituisce
    #: rumore di fondo per il resto del segmento) e' probabilistico: ritentare
    #: lo stesso testo di norma basta a risolverlo senza intervento manuale.
    dropout_max_retries: int = 2
    #: Vuoto CONTIGUO oltre il quale un segmento e' sospetto anche se il
    #: parlato totale torna. `dropout_min_ratio` non basta da solo: misura il
    #: parlato con la soglia ASSOLUTA di `trim_threshold_pct`, e un vuoto
    #: pieno di rumore di fondo la supera, quindi conta come parlato. Caso
    #: reale (episodio "Il fotovoltaico dell'Alma Mater", 18/09/2026, turno
    #: 10): 46,1s di segmento per 149 caratteri, con 36s di rumore a ~-50 dBFS
    #: in mezzo. Il parlato vero copriva il tempo atteso, quindi il rapporto
    #: era ~1 e nessun controllo scattava; in montaggio nemmeno
    #: `_cap_internal_silence` lo accorciava, per la stessa ragione. 8s e'
    #: misurato: sui 47 segmenti di quell'episodio il vuoto contiguo piu'
    #: lungo di un segmento SANO era 5,4s, quello del difettoso 36,1s.
    dropout_max_gap_seconds: float = 8.0
    #: Soglia del vuoto, in percentuale del picco MASSIMO del segmento. E'
    #: relativa al picco e non al 95esimo percentile come `trim_threshold_pct`
    #: proprio perche' deve stare sopra il rumore di fondo: e' la differenza
    #: fra "non c'e' segnale" e "non c'e' voce".
    dropout_gap_relative_pct: float = 2.0

    # ------------------------------------------------------------------
    # Costruzione
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, provider: VoiceProvider, audio: dict[str, Any]) -> "AudioAssembler":
        """Costruisce l'assembler dalla sezione `audio:` di config.yaml.

        Sta qui perche' i valori di default sono parte del comportamento del
        montaggio: ripetuti nei tre chiamanti (run.py, synthesize.py,
        retake.py) prima o poi divergono, e un'anteprima montata con pause
        diverse da quelle dell'episodio farebbe giudicare un take sbagliato.
        """
        return cls(
            provider=provider,
            pause_between_turns_ms=int(audio.get("pause_between_turns_ms", 450)),
            lead_in_ms=int(audio.get("lead_in_ms", 300)),
            lead_out_ms=int(audio.get("lead_out_ms", 600)),
            output_mp3_bitrate=int(audio.get("output_mp3_bitrate", 192)),
            trim_silence=bool(audio.get("trim_silence", True)),
            trim_threshold_pct=float(audio.get("trim_threshold_pct", 1.5)),
            trim_margin_head_ms=int(audio.get("trim_margin_head_ms", 30)),
            trim_margin_tail_ms=int(audio.get("trim_margin_tail_ms", 80)),
            cap_internal_silence=bool(audio.get("cap_internal_silence", True)),
            cap_internal_silence_ms=int(audio.get("cap_internal_silence_ms", 300)),
            dropout_detection=bool(audio.get("dropout_detection", True)),
            dropout_min_ratio=float(audio.get("dropout_min_ratio", 0.5)),
            dropout_max_retries=int(audio.get("dropout_max_retries", 2)),
            dropout_max_gap_seconds=float(audio.get("dropout_max_gap_seconds", 8.0)),
            dropout_gap_relative_pct=float(audio.get("dropout_gap_relative_pct", 2.0)),
        )

    # ------------------------------------------------------------------
    # Pre-volo
    # ------------------------------------------------------------------

    @staticmethod
    def check_voice_coverage(script: PodcastScript, voice_map: dict[str, str]) -> None:
        """Blocca PRIMA di iniziare la sintesi se manca una voce."""
        missing = [s for s in script.speakers if not voice_map.get(s)]
        if missing:
            raise MissingSpeakerVoice(
                "Sintesi non avviata: questi speaker dello script non hanno una "
                "voce assegnata: " + ", ".join(missing) + ".\n"
                "Correggi lo script oppure assegna una voce a ciascuno."
            )

    # ------------------------------------------------------------------
    # Sintesi
    # ------------------------------------------------------------------

    def synthesize(
        self,
        script: PodcastScript,
        voice_map: dict[str, str],
        segments_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> SynthesisResult:
        """Sintetizza solo i turni che non sono gia' in cache.

        La sintesi e' INCREMENTALE: per ogni turno calcola la chiave di
        contenuto, guarda che take e' stato scelto e, se quel file c'e' gia',
        lo riusa senza chiamare il TTS. Correggere una battuta costa quindi
        solo quella battuta — e, altrettanto importante, non rimette in gioco
        le battute venute bene: il TTS non e' deterministico.

        "Il file esiste" significa "il segmento e' valido" solo perche' la
        scrittura e' atomica (vedi `_write_atomic`): un crash a meta'
        download lascia al massimo un `.tmp`, mai un wav troncato che alla
        sintesi dopo verrebbe riusato come se fosse buono.
        """
        self.check_voice_coverage(script, voice_map)
        segments_dir.mkdir(parents=True, exist_ok=True)
        extension = self.provider.audio_format
        keys = script_segment_keys(script, voice_map, self.provider)
        choices = load_choices(segments_dir)

        paths: list[Path] = []
        takes: list[int] = []
        generated: list[int] = []
        reused: list[int] = []
        warnings: list[str] = []
        total = len(script.turni)

        for index, (turn, key) in enumerate(zip(script.turni, keys), start=1):
            speaker = turn["speaker"]
            take = choices.get(key, 1)
            target = segment_path(segments_dir, key, take, extension)

            if target.is_file():
                if progress:
                    progress(index, total, speaker, True)
                reused.append(index)
            else:
                if progress:
                    progress(index, total, speaker, False)
                try:
                    audio, defect = synthesize_segment(
                        self.provider,
                        turn["testo"],
                        voice_map[speaker],
                        dropout_detection=self.dropout_detection,
                        dropout_min_ratio=self.dropout_min_ratio,
                        dropout_max_retries=self.dropout_max_retries,
                        trim_threshold_pct=self.trim_threshold_pct,
                        dropout_max_gap_seconds=self.dropout_max_gap_seconds,
                        dropout_gap_relative_pct=self.dropout_gap_relative_pct,
                    )
                except VoiceProviderError as exc:
                    raise AssemblyError(
                        f"Sintesi interrotta al turno {index}/{total} ({speaker}).\n"
                        f"  Causa: {exc}\n"
                        f"  I segmenti gia' scaricati restano in {segments_dir}: "
                        "rilanciando la sintesi dopo aver risolto il problema "
                        "vengono riusati, e si pagano solo i turni mancanti."
                    ) from exc
                if defect is not None:
                    warnings.append(
                        f"Turno {index} ({speaker}): {defect.describe()}, anche "
                        f"dopo {1 + self.dropout_max_retries} tentativi. Ascoltalo "
                        f"e, se serve, rifallo con tools/retake.py --turn {index}."
                    )
                _write_atomic(target, audio)
                generated.append(index)

            paths.append(target)
            takes.append(take)

        write_segment_index(segments_dir, script, keys, takes, paths)

        return SynthesisResult(
            paths=paths,
            keys=keys,
            takes=takes,
            generated_turns=generated,
            reused_turns=reused,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Montaggio
    # ------------------------------------------------------------------

    def assemble(self, segments: list[Path], output_basename: Path) -> AssemblyResult:
        if not segments:
            raise AssemblyError("Nessun segmento audio da montare.")

        if self.provider.audio_format == "wav":
            return self._assemble_from_wav(segments, output_basename)
        return self._assemble_from_mp3(segments, output_basename)

    # -- percorso wav ------------------------------------------------------

    def _concat_wav(self, segments: list[Path]) -> tuple[Any, list[bytes], list[float]]:
        """Ritaglia e concatena i segmenti: lead-in, pause fra i turni, lead-out.

        Estratto dal montaggio perche' lo usa anche l'anteprima di un retake
        (`preview`): un take va ascoltato con le stesse pause e lo stesso
        ritaglio dell'episodio, altrimenti si giudica un audio che non e'
        quello che finira' dentro.

        Ritorna i parametri wav, i blocchi da scrivere e la durata RITAGLIATA
        di ogni segmento.
        """
        params = None
        frames: list[bytes] = []
        segment_seconds: list[float] = []

        for segment in segments:
            try:
                with wave.open(str(segment), "rb") as handle:
                    current = handle.getparams()
                    data = handle.readframes(handle.getnframes())
            except wave.Error as exc:
                raise AssemblyError(
                    f"Il segmento {segment.name} non e' un WAV leggibile: {exc}"
                ) from exc

            if self.trim_silence:
                data = _trim_edges(
                    data,
                    current,
                    threshold_pct=self.trim_threshold_pct,
                    margin_head_ms=self.trim_margin_head_ms,
                    margin_tail_ms=self.trim_margin_tail_ms,
                )

            if self.cap_internal_silence:
                data = _cap_internal_silence(
                    data,
                    current,
                    threshold_pct=self.trim_threshold_pct,
                    cap_ms=self.cap_internal_silence_ms,
                )

            if params is None:
                params = current
                frames.append(_silence(params, self.lead_in_ms))
            elif (
                current.nchannels,
                current.sampwidth,
                current.framerate,
            ) != (params.nchannels, params.sampwidth, params.framerate):
                raise AssemblyError(
                    f"Il segmento {segment.name} ha parametri audio diversi dagli altri "
                    f"({current.framerate} Hz / {current.nchannels} canali / "
                    f"{current.sampwidth * 8} bit contro {params.framerate} Hz / "
                    f"{params.nchannels} canali / {params.sampwidth * 8} bit). "
                    "Non posso concatenarli senza ricampionare."
                )
            else:
                frames.append(_silence(params, self.pause_between_turns_ms))

            frames.append(data)
            segment_seconds.append(
                len(data)
                / (current.framerate * current.nchannels * current.sampwidth)
            )

        assert params is not None
        frames.append(_silence(params, self.lead_out_ms))
        return params, frames, segment_seconds

    def _timeline(self, segment_seconds: list[float]) -> list[dict[str, float]]:
        """Dove cade ogni turno dentro il master, in secondi.

        Discende direttamente da come `_concat_wav` impila i blocchi: l'inizio
        del turno i-esimo (da 1) e' lead-in + le durate ritagliate dei turni
        precedenti + (i-1) pause. Sta qui e non in tools/ perche' e' una
        proprieta' del montaggio: chi cambia le pause cambia la timeline, e
        deve trovarsela sotto le mani.
        """
        timeline: list[dict[str, float]] = []
        cursor = self.lead_in_ms / 1000.0
        for position, seconds in enumerate(segment_seconds):
            if position:
                cursor += self.pause_between_turns_ms / 1000.0
            timeline.append({"inizio": round(cursor, 3), "fine": round(cursor + seconds, 3)})
            cursor += seconds
        return timeline

    #: Nome fisso del wav voce-sola dentro segmenti/, scritto da
    #: assemble_voice_master(). Un percorso unico letto sia da chi scrive
    #: _tempi_turni.json sia dal mix delle musiche, cosi' _concat_wav() non
    #: viene rieseguito due volte sugli stessi segmenti.
    VOICE_MASTER_FILENAME = "_voce_master.wav"

    def assemble_voice_master(
        self, segments: list[Path], target: Path
    ) -> AssemblyResult:
        """Monta SOLO il wav voce (nessuna codifica mp3), nel percorso indicato.

        E' il montaggio di `assemble()` senza il passo di codifica: serve al
        mix delle musiche (src/music_mixer.py), che ha bisogno del wav voce
        grezzo prima di sovrapporci intro/outro/sottofondo. `assemble()`
        stesso non cambia: per l'episodio senza musiche il flusso resta
        esattamente quello di prima.

        `target` e' esplicito (non un nome fisso dentro una cartella) perche'
        le anteprime di un retake con `--with-music` devono poter montare un
        master DIVERSO da quello canonico — con il take candidato al posto
        di quello scelto per un turno — senza toccare
        `segmenti/_voce_master.wav`, che deve continuare a riflettere solo la
        scelta corrente registrata in scelte.json.
        """
        if not segments:
            raise AssemblyError("Nessun segmento audio da montare.")
        if self.provider.audio_format != "wav":
            raise AssemblyError(
                "Il wav voce-sola si monta solo dai segmenti wav: con "
                "audio_format: mp3 i segmenti non vengono decodificati."
            )

        params, frames, segment_seconds = self._concat_wav(segments)
        timeline = self._timeline(segment_seconds)

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with wave.open(str(tmp), "wb") as out:
            out.setnchannels(params.nchannels)
            out.setsampwidth(params.sampwidth)
            out.setframerate(params.framerate)
            out.writeframes(b"".join(frames))
        tmp.replace(target)

        total_bytes = sum(len(chunk) for chunk in frames)
        duration = total_bytes / (params.framerate * params.nchannels * params.sampwidth)

        return AssemblyResult(
            path=target,
            audio_format="wav",
            duration_seconds=duration,
            segments=segments,
            segment_seconds=segment_seconds,
            turn_timeline=timeline,
        )

    def preview(self, segments: list[Path], target: Path) -> Path:
        """Monta alcuni segmenti in un wav di anteprima, senza codificare nulla."""
        if not segments:
            raise AssemblyError("Nessun segmento da montare nell'anteprima.")
        if self.provider.audio_format != "wav":
            raise AssemblyError(
                "Le anteprime si montano solo dai segmenti wav. Con "
                "audio_format: mp3 i segmenti non vengono decodificati, quindi "
                "non si possono ne' ritagliare ne' impaginare con le pause."
            )
        params, frames, _ = self._concat_wav(segments)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with wave.open(str(tmp), "wb") as out:
            out.setnchannels(params.nchannels)
            out.setsampwidth(params.sampwidth)
            out.setframerate(params.framerate)
            out.writeframes(b"".join(frames))
        tmp.replace(target)
        return target

    def _assemble_from_wav(
        self, segments: list[Path], output_basename: Path
    ) -> AssemblyResult:
        warnings: list[str] = []
        params, frames, segment_seconds = self._concat_wav(segments)
        timeline = self._timeline(segment_seconds)

        master_wav = output_basename.with_suffix(".wav")
        master_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(master_wav), "wb") as out:
            out.setnchannels(params.nchannels)
            out.setsampwidth(params.sampwidth)
            out.setframerate(params.framerate)
            out.writeframes(b"".join(frames))

        total_bytes = sum(len(chunk) for chunk in frames)
        duration = total_bytes / (params.framerate * params.nchannels * params.sampwidth)

        mp3_path = output_basename.with_suffix(".mp3")
        encoded = self._encode_mp3(master_wav, mp3_path, params, warnings)

        if encoded:
            master_wav.unlink(missing_ok=True)
            return AssemblyResult(
                path=mp3_path,
                audio_format="mp3",
                duration_seconds=duration,
                segments=segments,
                warnings=warnings,
                segment_seconds=segment_seconds,
                turn_timeline=timeline,
            )

        warnings.append(
            "Episodio salvato in WAV invece che in MP3: nessun encoder disponibile.\n"
            "    '.venv/bin/pip install -r requirements.txt' include gia' imageio-ffmpeg "
            "(dovrebbe bastare); in alternativa 'pip install lameenc'. "
            "tools/doctor.py dice esattamente cosa manca. Poi rilancia il montaggio."
        )
        return AssemblyResult(
            path=master_wav,
            audio_format="wav",
            duration_seconds=duration,
            segments=segments,
            warnings=warnings,
            segment_seconds=segment_seconds,
            turn_timeline=timeline,
        )

    def _encode_mp3(
        self,
        source_wav: Path,
        target_mp3: Path,
        params: wave._wave_params,  # type: ignore[name-defined]
        warnings: list[str],
    ) -> bool:
        ffmpeg = find_ffmpeg()
        if ffmpeg:
            command = [
                ffmpeg, "-y", "-loglevel", "error",
                "-i", str(source_wav),
                "-codec:a", "libmp3lame",
                "-b:a", f"{self.output_mp3_bitrate}k",
                str(target_mp3),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0 and target_mp3.is_file():
                return True
            warnings.append(
                f"ffmpeg ha fallito la codifica mp3 (exit {result.returncode}): "
                f"{result.stderr.strip()[:300]}"
            )

        try:
            import lameenc  # type: ignore
        except ImportError:
            return False

        if params.sampwidth != 2:
            warnings.append(
                f"lameenc accetta solo PCM a 16 bit, i segmenti sono a "
                f"{params.sampwidth * 8} bit."
            )
            return False

        with wave.open(str(source_wav), "rb") as handle:
            pcm = handle.readframes(handle.getnframes())

        encoder = lameenc.Encoder()
        encoder.set_bit_rate(self.output_mp3_bitrate)
        encoder.set_in_sample_rate(params.framerate)
        encoder.set_channels(params.nchannels)
        encoder.set_quality(2)
        target_mp3.write_bytes(encoder.encode(pcm) + encoder.flush())
        return True

    # -- percorso mp3 ------------------------------------------------------

    def _assemble_from_mp3(
        self, segments: list[Path], output_basename: Path
    ) -> AssemblyResult:
        target = output_basename.with_suffix(".mp3")
        target.parent.mkdir(parents=True, exist_ok=True)

        with target.open("wb") as out:
            for segment in segments:
                out.write(_strip_id3(segment.read_bytes()))

        return AssemblyResult(
            path=target,
            audio_format="mp3",
            duration_seconds=None,
            segments=segments,
            warnings=[
                "Segmenti gia' in mp3: concatenati senza ri-codifica, quindi "
                "SENZA le pause fra un turno e l'altro.\n"
                "    Per avere le pause imposta audio_format: wav in config.yaml.",
                "Timeline dei turni non disponibile su questo percorso: i "
                "segmenti mp3 non vengono decodificati, quindi non si sa quanto "
                "duri ciascuno.\n"
                "    Di conseguenza non si puo' risalire dal minutaggio alla "
                "battuta (tools/turn_at.py). Serve audio_format: wav.",
            ],
        )


# ----------------------------------------------------------------------
# Helper
# ----------------------------------------------------------------------


def _write_atomic(target: Path, data: bytes) -> None:
    """Scrive passando da un .tmp, cosi' il file finale o c'e' intero o non c'e'.

    Serve perche' la cache decide "gia' fatto" dalla sola esistenza del file:
    un wav troncato da un crash o da una connessione caduta verrebbe riusato
    per sempre, e il difetto si sentirebbe nell'episodio senza che niente lo
    segnali.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)


def segment_rms(path: Path) -> float | None:
    """Livello medio (RMS) di un segmento wav, in unita' di ampiezza.

    Serve a confrontare un take nuovo con gli altri dello stesso speaker: se
    esce molto piu' piano o piu' forte, montato nell'episodio si sente come uno
    sbalzo. None quando il formato non e' analizzabile: meglio nessun giudizio
    che un giudizio sbagliato.
    """
    try:
        with wave.open(str(path), "rb") as handle:
            params = handle.getparams()
            data = handle.readframes(handle.getnframes())
    except (wave.Error, OSError):
        return None
    if params.sampwidth != 2 or not data:
        return None
    samples = array("h")
    samples.frombytes(data)
    mono = samples[:: params.nchannels] if params.nchannels > 1 else samples
    if not mono:
        return None
    return (sum(float(v) * v for v in mono) / len(mono)) ** 0.5


def _silence(params, milliseconds: int) -> bytes:  # type: ignore[no-untyped-def]
    if milliseconds <= 0:
        return b""
    frames = int(params.framerate * milliseconds / 1000)
    filler = b"\x80" if params.sampwidth == 1 else b"\x00"
    return filler * (frames * params.nchannels * params.sampwidth)


#: Risoluzione dell'analisi per il ritaglio. 5 ms e' abbastanza fine da non
#: sprecare silenzio e abbastanza grosso da non inseguire i singoli campioni.
_TRIM_FRAME_MS = 5


def _peak_profile(  # type: ignore[no-untyped-def]
    data: bytes, params, threshold_pct: float
) -> tuple[list[int], int, int] | None:
    """Picco per finestre di `_TRIM_FRAME_MS` e soglia relativa al 95esimo percentile.

    Stessa identica analisi usata da `_trim_edges`, `_cap_internal_silence` e
    `active_speech_seconds`: cambia solo cosa ciascuno fa con `peaks` e
    `threshold`. None quando il segmento non e' analizzabile (formato non a 16
    bit, o troppo corto per avere almeno 3 finestre) — mai un giudizio sbagliato
    su un formato che non si sa leggere.
    """
    if params.sampwidth != 2:
        # 8/24/32 bit: non li produce nessun provider in uso. Meglio non
        # analizzare che analizzare male.
        return None

    samples = array("h")
    samples.frombytes(data)
    mono = samples[:: params.nchannels] if params.nchannels > 1 else samples
    if not mono:
        return None

    hop = max(1, int(params.framerate * _TRIM_FRAME_MS / 1000))
    count = len(mono) // hop
    if count < 3:
        return None

    peaks = [max(abs(v) for v in mono[k * hop : (k + 1) * hop]) for k in range(count)]
    reference = sorted(peaks)[int(0.95 * (count - 1))]
    # pavimento assoluto: su un segmento sussurrato una soglia solo relativa
    # scenderebbe dentro il rumore di quantizzazione.
    threshold = max(int(reference * threshold_pct / 100), 5)
    return peaks, hop, threshold


def active_speech_seconds(  # type: ignore[no-untyped-def]
    data: bytes, params, threshold_pct: float = 1.5
) -> float | None:
    """Quanti secondi del segmento superano la soglia di silenzio.

    Serve a scoprire sintesi TRONCATE: il provider, specie in modalita'
    preview, a volte smette di parlare a meta' frase e restituisce un file
    lungo ma quasi tutto silenzio/rumore di fondo al posto del resto del
    testo — osservato sul turno 33 di "Il cuore elettrico" (18/09/2026): 2,2s
    di parlato reale dentro un file di 21,6s, con il resto della frase mai
    pronunciato. Il file resta un wav valido, quindi ne' `_write_atomic` ne'
    la cache per contenuto se ne accorgono da soli.
    """
    profile = _peak_profile(data, params, threshold_pct)
    if profile is None:
        return None
    peaks, hop, threshold = profile
    active_frames = sum(1 for p in peaks if p > threshold)
    return active_frames * hop / params.framerate


def _decode_wav_bytes(data: bytes):  # type: ignore[no-untyped-def]
    """Apre un wav dai byte grezzi restituiti dal provider (non ancora su disco)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            return handle.getparams(), handle.readframes(handle.getnframes())
    except (wave.Error, EOFError):
        return None, None


#: Sotto questa soglia di tempo atteso non vale la pena controllare: un turno
#: di poche parole ("Si'.", "Trenta volte al giorno?") puo' stare benissimo
#: sotto la frazione minima del SUO tempo atteso senza che sia un difetto —
#: il rumore statistico su un numero cosi' piccolo di secondi e' troppo alto
#: perche' il rapporto sia un segnale affidabile.
DROPOUT_MIN_EXPECTED_SECONDS = 1.5


def longest_quiet_gap_seconds(  # type: ignore[no-untyped-def]
    data: bytes, params, relative_pct: float
) -> float | None:
    """Il vuoto CONTIGUO piu' lungo dentro il segmento, in secondi.

    Differisce da `active_speech_seconds` in un punto solo, ed e' il punto che
    conta: la soglia e' una frazione del picco MASSIMO del segmento invece che
    del 95esimo percentile. Serve a distinguere il rumore di fondo dalla voce,
    non il silenzio digitale dal segnale: un provider che smette di parlare
    lascia rumore, e a ~-50 dBFS quel rumore sta ben sopra la soglia assoluta
    usata dal ritaglio (~-76 dBFS) ma ben sotto il picco della voce.

    None se il segmento non e' analizzabile: mai un giudizio su un formato che
    non si sa leggere.
    """
    profile = _peak_profile(data, params, relative_pct)
    if profile is None:
        return None
    peaks, hop, _ = profile
    ceiling = max(peaks)
    if ceiling <= 0:
        return None
    quiet = ceiling * relative_pct / 100
    longest = current = 0
    for peak in peaks:
        current = current + 1 if peak < quiet else 0
        longest = max(longest, current)
    return longest * hop / params.framerate


@dataclass(frozen=True)
class SegmentDefect:
    """Cosa non va in un segmento sintetizzato, in forma riportabile.

    Esiste per non far ricostruire la frase a ogni chiamante: i due difetti
    hanno cause diverse e vanno spiegati in modo diverso, ma il rimedio (un
    altro tentativo, poi un warning) e' lo stesso.
    """

    #: "troncato" (il provider ha smesso a meta' frase) oppure "vuoto"
    #: (il provider ha continuato a generare rumore al posto della voce).
    kind: str
    ratio: float | None = None
    gap_seconds: float | None = None

    def describe(self) -> str:
        if self.kind == "troncato":
            return (
                f"il parlato copre solo il {(self.ratio or 0) * 100:.0f}% del tempo "
                "di lettura atteso: probabile sintesi troncata a meta' frase"
            )
        return (
            f"contiene {self.gap_seconds:.0f}s di vuoto continuo in mezzo al turno: "
            "il provider ha smesso di parlare lasciando solo rumore di fondo"
        )


def detect_dropped_audio(
    audio: bytes,
    expected_seconds: float | None,
    *,
    min_ratio: float = 0.5,
    threshold_pct: float = 1.5,
    max_gap_seconds: float = 8.0,
    gap_relative_pct: float = 2.0,
) -> SegmentDefect | None:
    """Il difetto del segmento, se ce n'e' uno; None se va bene.

    Due controlli indipendenti, perche' il provider sbaglia in due modi che si
    escludono a vicenda nei sintomi:

      - **troncato**: smette a meta' frase, il parlato totale non copre il
        tempo atteso. Serve `expected_seconds`, quindi un provider che dichiara
        `chars_per_second`;
      - **vuoto**: dice tutto il testo ma con un buco lungo in mezzo, riempito
        di rumore. Il parlato totale torna, quindi il primo controllo non lo
        vede — e nemmeno il ritaglio, che al rumore non arriva.

    None anche quando il segmento non e' controllabile (formato non wav, turno
    troppo corto per essere un segnale affidabile): in nessun caso si blocca la
    sintesi per un dubbio.
    """
    params, frames = _decode_wav_bytes(audio)
    if params is None:
        return None

    if expected_seconds is not None and expected_seconds >= DROPOUT_MIN_EXPECTED_SECONDS:
        active = active_speech_seconds(frames, params, threshold_pct)
        if active is not None and active / expected_seconds < min_ratio:
            return SegmentDefect(kind="troncato", ratio=active / expected_seconds)

    if max_gap_seconds > 0:
        gap = longest_quiet_gap_seconds(frames, params, gap_relative_pct)
        if gap is not None and gap > max_gap_seconds:
            return SegmentDefect(kind="vuoto", gap_seconds=gap)

    return None


def synthesize_segment(
    provider: VoiceProvider,
    text: str,
    voice_id: str,
    *,
    dropout_detection: bool = True,
    dropout_min_ratio: float = 0.5,
    dropout_max_retries: int = 2,
    trim_threshold_pct: float = 1.5,
    dropout_max_gap_seconds: float = 8.0,
    dropout_gap_relative_pct: float = 2.0,
) -> tuple[bytes, "SegmentDefect | None"]:
    """Chiama il provider, RITENTANDO se il risultato sembra una sintesi troncata.

    Condivisa da `AudioAssembler.synthesize()` e da `retake.py`: stesso
    difetto, stesso rimedio, in entrambi i punti che chiamano
    `provider.text_to_speech()`. Il glitch osservato e' probabilistico —
    ritentare lo stesso testo di norma basta a risolverlo senza che l'utente
    se ne accorga.

    Ritorna `(audio, defect)`: `defect` e' None se il segmento e' passato i
    controlli (o non era controllabile), altrimenti il difetto rilevato
    all'ULTIMO tentativo — il chiamante lo riporta come warning invece di
    accettare il segmento in silenzio.
    """
    checkable = dropout_detection and provider.audio_format == "wav"
    expected_seconds = None
    if checkable and provider.chars_per_second:
        expected_seconds = len(spoken_text(text)) / provider.chars_per_second

    # Il controllo sul vuoto non ha bisogno di `chars_per_second`: un buco di
    # otto secondi in mezzo a un turno e' anomalo comunque sia lungo il testo.
    attempts = 1 + max(0, dropout_max_retries) if checkable else 1
    audio = b""
    defect: SegmentDefect | None = None
    for _ in range(attempts):
        audio = provider.text_to_speech(text, voice_id)
        defect = detect_dropped_audio(
            audio,
            expected_seconds,
            min_ratio=dropout_min_ratio,
            threshold_pct=trim_threshold_pct,
            max_gap_seconds=dropout_max_gap_seconds if checkable else 0.0,
            gap_relative_pct=dropout_gap_relative_pct,
        )
        if defect is None:
            break
    return audio, defect


def _trim_edges(  # type: ignore[no-untyped-def]
    data: bytes,
    params,
    *,
    threshold_pct: float = 1.5,
    margin_head_ms: int = 30,
    margin_tail_ms: int = 80,
) -> bytes:
    """Toglie il silenzio in testa e in coda a un segmento, lasciando un margine.

    Serve a rendere la pausa fra i turni ESATTAMENTE quella configurata: il
    TTS aggiunge silenzio ai bordi in quantita' che varia da un turno
    all'altro, e senza ritaglio quella variabilita' finisce dritta nel
    montaggio.

    Ritorna `data` invariato se non c'e' niente da togliere o se il formato non
    e' analizzabile: **in nessun caso restituisce audio troncato sul parlato**.
    Il margine in coda e' piu' largo di quello in testa perche' la fine di
    un'enunciazione ha un rilascio che, tagliato, si sente.
    """
    profile = _peak_profile(data, params, threshold_pct)
    if profile is None:
        return data
    peaks, hop, threshold = profile
    count = len(peaks)

    first = 0
    while first < count and peaks[first] <= threshold:
        first += 1
    if first >= count:
        return data  # nessun parlato rilevato: non toccare niente

    last = count - 1
    while last > first and peaks[last] <= threshold:
        last -= 1

    start = max(0, first - margin_head_ms // _TRIM_FRAME_MS)
    end = min(count - 1, last + margin_tail_ms // _TRIM_FRAME_MS)

    frame_bytes = params.nchannels * params.sampwidth
    begin = start * hop * frame_bytes
    # se il taglio arriva all'ultimo frame analizzato, tieni anche la coda di
    # campioni che non riempiva un frame intero: non e' silenzio, e' resto.
    finish = len(data) if end >= count - 1 else (end + 1) * hop * frame_bytes
    return data[begin:finish]


def _cap_internal_silence(  # type: ignore[no-untyped-def]
    data: bytes,
    params,
    *,
    threshold_pct: float = 1.5,
    cap_ms: int = 300,
) -> bytes:
    """Accorcia i vuoti INTERNI a un turno che superano `cap_ms`.

    `_trim_edges` toglie il silenzio ai bordi; questa funzione se ne occupa
    dentro, dove un punto fermo o una domanda a meta' turno puo' produrre una
    pausa che il TTS decide da solo, senza nessun parametro a controllarla
    (a differenza della pausa fra due turni, che e' `pause_between_turns_ms`
    ed e' sempre esatta). E' la stessa identica analisi di soglia di
    `_trim_edges` - hop di 5 ms, soglia relativa al 95esimo percentile del
    picco - applicata ai vuoti che stanno in mezzo invece che ai bordi.

    Il margine di testa/coda del turno (di competenza di `_trim_edges`) non
    viene toccato: un vuoto che tocca il primo o l'ultimo frame analizzato e'
    lasciato intatto. Il taglio, quando serve, cade dentro il vuoto stesso
    (sotto soglia su entrambi i lati), quindi non tronca ne' un attacco ne'
    un rilascio.
    """
    profile = _peak_profile(data, params, threshold_pct)
    if profile is None:
        return data
    peaks, hop, threshold = profile
    count = len(peaks)
    cap_frames = max(1, cap_ms // _TRIM_FRAME_MS)

    kept: list[tuple[int, int]] = []
    i = 0
    while i < count:
        is_silence = peaks[i] <= threshold
        j = i
        while j < count and (peaks[j] <= threshold) == is_silence:
            j += 1
        if is_silence and i > 0 and j < count and (j - i) > cap_frames:
            # vuoto interno oltre il tetto: tieni meta' tetto da ogni lato,
            # cosi' il taglio cade nel silenzio e non sull'attacco/rilascio.
            half = cap_frames // 2
            kept.append((i, i + half))
            kept.append((j - (cap_frames - half), j))
        else:
            # parlato, oppure un vuoto ai bordi (di competenza di
            # _trim_edges) o gia' sotto il tetto: non si tocca.
            kept.append((i, j))
        i = j

    frame_bytes = params.nchannels * params.sampwidth
    out = bytearray()
    for start, end in kept:
        out += data[start * hop * frame_bytes : end * hop * frame_bytes]
    # coda di campioni che non riempivano un frame intero: e' resto, non silenzio
    out += data[count * hop * frame_bytes :]
    return bytes(out)


def _strip_id3(data: bytes) -> bytes:
    """Rimuove un eventuale tag ID3v2 in testa: concatenarli romperebbe il file."""
    if len(data) > 10 and data[:3] == b"ID3":
        size_bytes = data[6:10]
        size = 0
        for byte in size_bytes:
            size = (size << 7) | (byte & 0x7F)
        return data[10 + size :]
    return data


def _slug(value: str) -> str:
    keep = [c if c.isalnum() else "-" for c in value.strip().lower()]
    return "".join(keep).strip("-").replace("--", "-") or "speaker"


def slugify(value: str, max_length: int = 60) -> str:
    """Slug riutilizzabile per i nomi delle cartelle di output."""
    return _slug(value)[:max_length].strip("-") or "episodio"


def total_bytes(paths: Iterable[Path]) -> int:
    return sum(p.stat().st_size for p in paths if p.is_file())


__all__ = [
    "AudioAssembler",
    "AssemblyResult",
    "AssemblyError",
    "MissingSpeakerVoice",
    "SynthesisResult",
    "CHOICES_FILENAME",
    "INDEX_FILENAME",
    "PREVIEWS_DIRNAME",
    "segment_key",
    "script_segment_keys",
    "segment_path",
    "existing_takes",
    "load_choices",
    "save_choices",
    "write_segment_index",
    "segment_rms",
    "active_speech_seconds",
    "detect_dropped_audio",
    "synthesize_segment",
    "slugify",
]
