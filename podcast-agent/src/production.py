"""Passi 5 e 6 del wireframe: stima e consenso, produzione dell'episodio.

Tre funzioni, usate dai tool:

  - `render_episode()`: sintesi incrementale, montaggio (con o senza musiche),
    tempi per battuta e segnali. E' la logica che stava in tools/synthesize.py,
    che ora la chiama e resta sottile; la usa anche `produce()`;
  - `estimate_run()`: la stima del passo 5 per una run, anche quando le voci
    non sono ancora pronte (nessun voci.json): quali voci si riusano e quali si
    clonano, con lo stato del consenso, quante battute restano da generare,
    il costo in USD (e in EUR se c'e' un cambio datato in config.yaml);
  - `produce()`: tutto il passo 6 in un comando, con il lock di lavoro tenuto
    per l'intera durata (lo stato "in generazione" si DERIVA dal lock):

        approvazione -> musiche -> voci (consenso, nessun retry) -> sintesi
        -> montaggio e mix -> tempi e segnali -> chiusura della run

    Avanza scrivendo eventi JSONL (`EventLog`), e si ferma in modo pulito fra
    una battuta e l'altra quando `should_stop()` diventa vero (SIGTERM/SIGINT
    in tools/produce.py): esito ProductionInterrupted con le battute pronte e
    quelle mancanti. Rilanciando si pagano solo le mancanti.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .audio_assembler import (
    AudioAssembler,
    SynthesisResult,
    SynthesisStopped,
    load_choices,
    script_segment_keys,
    segment_path,
    slugify,
)
from .cost_estimate import Estimate, estimate
from .episode_config import EpisodeConfig
from .episode_signals import collect_and_write, record_synthesis_event
from .error_catalog import public_error
from .errors import DomainError
from .fileio import write_json_atomic
from .music_context import music_durations
from .music_mixer import mix_episode, preflight
from .paths import PathNotAllowed
from .redaction import redact
from .pdf_reader import DocumentError, locate_document
from .registry import VoiceRegistry
from .run_layout import archive_source_document, write_voices_used
from .run_lock import work_lock
from .script_session import FILENAME as SESSION_FILENAME
from .script_session import ScriptSession, script_hash
from .script_writer import PodcastScript, spoken_text
from .segment_cache import SCRIPT_FILENAME, VOICES_FILENAME
from .voice_meta import consent_revoked
from .voice_prep import ACTION_CLONE, ConsentRequired, plan_voices, prepare_voices

AUDIO_PATHS_FILENAME = "_audio_paths.json"
#: Descrizione del file finale, scritta dal montaggio (vedi write_montage).
MONTAGE_FILENAME = "_montaggio.json"
#: Il file degli eventi di produce.py: nome fisso, dentro la run.
EVENTS_FILENAME = "_eventi.jsonl"
TIMINGS_FILENAME = "_tempi_turni.json"

#: Le fasi del passo 6, nell'ordine dello schermo "Generazione".
PHASES = ("preparazione_voci", "sintesi", "montaggio", "mix", "chiusura")


class ApprovalRequired(DomainError):
    """Il copione da produrre non e' quello approvato (o non e' approvato)."""


class ProductionInterrupted(DomainError):
    """La produzione si e' fermata su richiesta: si riprende rilanciandola."""


Note = Callable[[str], None]


def _noop(_message: str) -> None:
    return None


# ----------------------------------------------------------------------
# Eventi
# ----------------------------------------------------------------------


class EventLog:
    """Eventi di avanzamento, uno per riga (JSONL), scritti appena succedono.

    L'adattatore legge il file mentre il comando gira (stato "In
    generazione", barra delle battute). Ogni riga e' un oggetto con almeno
    `evento` e `at`; una riga si scrive in una sola write() con flush, quindi
    chi legge vede righe intere. Senza percorso non scrive niente.

    Il file si apre (e si svuota) al PRIMO evento, che produce() scrive solo
    dopo aver preso il lock di lavoro: un secondo lancio respinto con RunBusy
    non tocca gli eventi della produzione in corso.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._handle: Any = None

    @property
    def opened(self) -> bool:
        return self._handle is not None

    def emit(self, evento: str, **data: Any) -> None:
        if self.path is None:
            return
        if self._handle is None:
            self._handle = self._open()
        # Gli eventi arrivano al browser: niente percorsi del server, nemmeno
        # dentro un avviso che riporta lo stderr di ffmpeg (revisione finale, N3).
        line = json.dumps(
            {"evento": evento, "at": datetime.now().replace(microsecond=0).isoformat(), **redact(data)},
            ensure_ascii=False,
        )
        self._handle.write(line + "\n")
        self._handle.flush()

    def _open(self) -> Any:
        """Svuota e apre il file, senza seguire un link simbolico.

        Il nome lo fissa il tool (`_eventi.jsonl` nella run): da fuori non si
        sceglie quale file della run svuotare (revisione di sicurezza C2,
        MEDIA-2). Un `_eventi.jsonl` che e' un link simbolico non si apre.
        """
        assert self.path is not None
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        if self.path.is_symlink():
            raise PathNotAllowed("Il file degli eventi non puo' essere un link simbolico.", details={"campo": "events"})
        try:
            fd = os.open(str(self.path), flags, 0o644)
        except OSError as exc:
            raise PathNotAllowed("Il file degli eventi non si puo' aprire.", details={"campo": "events"}) from exc
        return os.fdopen(fd, "w", encoding="utf-8")

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


# ----------------------------------------------------------------------
# Sintesi e montaggio (condivisi con tools/synthesize.py)
# ----------------------------------------------------------------------


@dataclass
class RenderResult:
    output_path: Path
    output_format: str
    duration_seconds: float | None
    #: None quando il montaggio e' partito da segmenti gia' pronti (mix.py).
    synthesis: SynthesisResult | None
    warnings: list[str]
    music_payload: dict[str, Any] = field(default_factory=dict)
    timings_file: Path | None = None


def missing_turns(run_dir: Path, script: PodcastScript, voice_map: dict[str, str], provider: Any) -> list[int]:
    """Numeri di battuta (da 1) il cui segmento scelto non e' in cache."""
    keys = script_segment_keys(script, voice_map, provider)
    segments_dir = Path(run_dir) / "segmenti"
    choices = load_choices(segments_dir)
    return [
        index
        for index, key in enumerate(keys, start=1)
        if not segment_path(segments_dir, key, choices.get(key, 1), provider.audio_format).is_file()
    ]


def render_episode(
    run_dir: Path,
    config: Any,
    script: PodcastScript,
    voice_map: dict[str, str],
    *,
    provider: Any,
    music_choice: Any,
    music_warnings: list[str],
    accept_changed_music: bool = False,
    progress: Callable[[int, int, str, bool], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_phase: Callable[[str, str], None] | None = None,
    note: Note = _noop,
) -> RenderResult:
    """Sintesi incrementale + montaggio + tempi + segnali.

    `on_phase(fase, "iniziata" | "finita")` per sintesi, montaggio e mix.
    SynthesisStopped arriva al chiamante cosi' com'e'.
    """
    run_dir = Path(run_dir)
    phase = on_phase or (lambda _name, _state: None)
    assembler = AudioAssembler.from_config(provider, config.audio)
    # Blocco esplicito prima di spendere: nessuno speaker senza voce.
    assembler.check_voice_coverage(script, voice_map)

    phase("sintesi", "iniziata")
    synthesis = assembler.synthesize(script, voice_map, run_dir / "segmenti", progress, should_stop=should_stop)
    phase("sintesi", "finita")
    if synthesis.reused_turns:
        note(
            f"{len(synthesis.reused_turns)} turni ripresi dalla cache, "
            f"{len(synthesis.generated_turns)} sintetizzati ora."
        )

    assembled = assemble_episode(
        run_dir,
        config,
        script,
        synthesis.paths,
        assembler=assembler,
        music_choice=music_choice,
        music_warnings=music_warnings,
        accept_changed_music=accept_changed_music,
        on_phase=on_phase,
        note=note,
    )

    # Segnali per le lezioni (src/episode_signals.py). Mai bloccante: la
    # sintesi e' gia' andata a buon fine a questo punto.
    try:
        record_synthesis_event(run_dir, script)
        collect_and_write(run_dir, provider=provider)
    except Exception as exc:  # noqa: BLE001 - raccolta dati, non deve mai far fallire la sintesi
        note(f"Raccolta segnali non riuscita (non blocca l'episodio): {exc}")

    assembled.synthesis = synthesis
    assembled.warnings = [*synthesis.warnings, *assembled.warnings]
    return assembled


def final_basename(run_dir: Path, script: PodcastScript) -> str:
    """Il nome (senza estensione) del file finale dell'episodio.

    G7: per le run nuove e' il titolo dell'episodio (episodio.json), non
    quello che il modello ha scritto nel copione. Una volta montato, il nome
    resta quello (registrato in _montaggio.json) anche se il titolo cambia:
    l'Archivio e i link al file non devono rompersi. Le run di prima, gia'
    montate con il titolo del copione, tengono il loro nome.
    """
    run_dir = Path(run_dir)
    record = load_montage(run_dir)
    if record and record.get("file"):
        stem = Path(str(record["file"])).stem
        # Solo un nome semplice, gia' nella forma di uno slug: un registro
        # manomesso ("file": ".") non deve far scrivere fuori dalla run
        # (revisione di sicurezza C3, BASSA-4).
        if stem and slugify(stem) == stem:
            return stem
    legacy = slugify(script.titolo)
    if any((run_dir / f"{legacy}{ext}").is_file() for ext in (".mp3", ".wav")):
        return legacy
    episode = EpisodeConfig.load(run_dir)
    if episode is not None and episode.titolo:
        return slugify(episode.titolo)
    return legacy


def assemble_episode(
    run_dir: Path,
    config: Any,
    script: PodcastScript,
    segments: list[Path],
    *,
    assembler: AudioAssembler,
    music_choice: Any,
    music_warnings: list[str],
    accept_changed_music: bool = False,
    on_phase: Callable[[str, str], None] | None = None,
    note: Note = _noop,
) -> RenderResult:
    """Montaggio (e mix, se ci sono musiche) da segmenti GIA' pronti. Zero TTS.

    Usato da render_episode() dopo la sintesi e da tools/mix.py per rimontare
    dalla cache (G1: anche senza musiche). Scrive _tempi_turni.json e
    _montaggio.json.
    """
    run_dir = Path(run_dir)
    phase = on_phase or (lambda _name, _state: None)
    basename = final_basename(run_dir, script)
    music_payload: dict[str, Any] = {}
    phase("montaggio", "iniziata")
    if music_choice is not None and music_choice.any():
        note("Montaggio della voce e mix con le musiche scelte...")

        def mix_started() -> None:
            phase("montaggio", "finita")
            phase("mix", "iniziata")

        mix_result = mix_episode(
            assembler=assembler, segments=segments, script=script, run_dir=run_dir,
            config=config, accept_changed_music=accept_changed_music,
            choice=music_choice, on_mix_start=mix_started, output_basename=basename,
        )
        phase("mix", "finita")
        voice_result = mix_result.voice_assembly
        output_path = mix_result.path
        output_format = "mp3"
        duration_seconds = mix_result.duration_seconds
        warnings = [*music_warnings, *mix_result.warnings]
        voice_offset = mix_result.voice_offset_seconds
        voice_duration = mix_result.voice_duration_seconds
        music_payload = {
            "voice_offset_seconds": mix_result.voice_offset_seconds,
            "voice_duration_seconds": mix_result.voice_duration_seconds,
            "musiche_usate_file": str(mix_result.musiche_usate_path),
        }
    else:
        note("Montaggio...")
        voice_result = assembler.assemble(segments, run_dir / basename)
        phase("montaggio", "finita")
        output_path = voice_result.path
        output_format = voice_result.audio_format
        duration_seconds = voice_result.duration_seconds
        warnings = list(voice_result.warnings)
        voice_offset = 0.0
        voice_duration = duration_seconds

    # RACCOLTA DATI (non entra nella stima): velocita' di lettura per turno e
    # per voce, riferita SEMPRE al master di sola voce (vedi README.md,
    # "Correggere una battuta"). Va misurata PRIMA di un'eventuale
    # cancellazione dei segmenti.
    timings_file = write_timings(run_dir, script, voice_result)
    write_montage(
        run_dir,
        config,
        script,
        output_path=output_path,
        output_format=output_format,
        duration_seconds=duration_seconds,
        music_choice=music_choice,
        voice_offset=voice_offset,
        voice_duration=voice_duration,
        segments=segments,
    )

    return RenderResult(
        output_path=output_path,
        output_format=output_format,
        duration_seconds=duration_seconds,
        synthesis=None,  # type: ignore[arg-type]
        warnings=warnings,
        music_payload=music_payload,
        timings_file=timings_file,
    )


# ----------------------------------------------------------------------
# Registro del montaggio
# ----------------------------------------------------------------------


def load_montage(run_dir: Path) -> dict[str, Any] | None:
    """_montaggio.json, o None (run mai montata, o montata prima di C3)."""
    target = Path(run_dir) / MONTAGE_FILENAME
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def write_montage(
    run_dir: Path,
    config: Any,
    script: PodcastScript,
    *,
    output_path: Path,
    output_format: str,
    duration_seconds: float | None,
    music_choice: Any,
    voice_offset: float,
    voice_duration: float | None,
    segments: list[Path],
) -> Path:
    """Cosa c'e' nel file finale, scritto dal montaggio che lo ha prodotto.

    Non e' uno stato: e' la descrizione di un file, scritta insieme a lui.
    Da qui si DERIVA se l'episodio e' ancora coerente (copione, take scelti
    e musiche di adesso contro quelli di allora: src/episode_info.py), e
    dove cadono le sezioni nel file finale. Sostituisce musiche_usate.json
    come fonte dell'offset della voce: quello resta da un mix precedente
    anche quando l'episodio viene rimontato senza musiche.
    """
    total = float(duration_seconds or 0.0)
    sections: list[dict[str, Any]] = []
    tracks: dict[str, Any] = {}
    if music_choice is not None and music_choice.any():
        gap = float(config.music.get("intro_gap_ms", 800)) / 1000.0
        for category in ("intro", "sottofondo", "outro"):
            track = getattr(music_choice, category)
            tracks[category] = {"nome": track.name, "sha256": track.sha256()} if track else None
        if music_choice.intro:
            sections.append({"tipo": "intro", "inizio": 0.0, "fine": round(max(0.0, voice_offset - gap), 3)})
        sections.append(
            {"tipo": "parlato", "inizio": round(voice_offset, 3), "fine": round(voice_offset + float(voice_duration or 0.0), 3)}
        )
        if music_choice.outro:
            outro_seconds = music_choice.outro.duration_seconds() or 0.0
            sections.append({"tipo": "outro", "inizio": round(max(0.0, total - outro_seconds), 3), "fine": round(total, 3)})
    else:
        sections.append({"tipo": "parlato", "inizio": 0.0, "fine": round(total, 3)})

    return write_json_atomic(
        Path(run_dir) / MONTAGE_FILENAME,
        {
            "file": Path(output_path).name,
            "formato": output_format,
            "montato_il": datetime.now().replace(microsecond=0).isoformat(),
            "duration_seconds": round(total, 3) if duration_seconds else None,
            "copione_sha256": script_hash(script.to_dict()),
            "segmenti": [Path(p).name for p in segments],
            # Le scelte dei take al momento del montaggio: se cambiano
            # (retake.py --choose) l'episodio e' da rimontare.
            "scelte_take": load_choices(Path(run_dir) / "segmenti"),
            "musiche": tracks or None,
            "voice_offset_seconds": round(voice_offset, 3),
            "voice_duration_seconds": round(float(voice_duration), 3) if voice_duration else None,
            "sezioni": sections,
        },
    )


def write_timings(run_dir: Path, script: PodcastScript, result: Any) -> Path | None:
    """Scrive _tempi_turni.json: caratteri parlati e durata reale, per turno."""
    if not result.segment_seconds:
        return None  # percorso mp3: i segmenti non vengono decodificati

    turni = []
    timeline = result.turn_timeline or [{} for _ in script.turni]
    for turn, seconds, offsets in zip(script.turni, result.segment_seconds, timeline):
        chars = len(spoken_text(turn["testo"]))
        turni.append(
            {
                "speaker": turn["speaker"],
                "spoken_chars": chars,
                "seconds": round(seconds, 3),
                "chars_per_second": round(chars / seconds, 2) if seconds > 0 else None,
                # Dove cade il turno DENTRO l'episodio montato: e' quello che
                # permette di risalire da un minutaggio alla battuta
                # (tools/turn_at.py). Lo calcola il montaggio, non questo file.
                "inizio": offsets.get("inizio"),
                "fine": offsets.get("fine"),
            }
        )

    per_voce: dict[str, dict[str, float]] = {}
    for row in turni:
        acc = per_voce.setdefault(row["speaker"], {"spoken_chars": 0, "seconds": 0.0})
        acc["spoken_chars"] += row["spoken_chars"]
        acc["seconds"] += row["seconds"]
    for acc in per_voce.values():
        acc["seconds"] = round(acc["seconds"], 3)
        acc["chars_per_second"] = (
            round(acc["spoken_chars"] / acc["seconds"], 2) if acc["seconds"] > 0 else None
        )

    return write_json_atomic(
        Path(run_dir) / TIMINGS_FILENAME,
        {
            "nota": "Misure per turno, al netto delle pause e delle inline tag. "
                    "Raccolta dati: la stima non le usa. inizio/fine sono "
                    "invece la posizione del turno dentro l'episodio montato.",
            "per_voce": per_voce,
            "turni": turni,
        },
    )


def duration_label(seconds: float | None) -> str | None:
    if not seconds:
        return None
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}m {rest:02d}s"


# ----------------------------------------------------------------------
# Stima (passo 5)
# ----------------------------------------------------------------------


def predict(script: PodcastScript, provider: Any, config: Any) -> Estimate:
    """Durata e costo dell'intero copione (src/cost_estimate.py)."""
    audio = config.audio
    return estimate(
        script,
        provider.pricing(),
        chars_per_second=getattr(provider, "chars_per_second", None),
        words_per_minute=int(config.script.get("words_per_minute", 150)),
        pause_between_turns_ms=int(audio.get("pause_between_turns_ms", 450)),
        lead_in_ms=int(audio.get("lead_in_ms", 300)),
        lead_out_ms=int(audio.get("lead_out_ms", 600)),
        trim_silence=bool(audio.get("trim_silence", False)),
        trim_margin_head_ms=int(audio.get("trim_margin_head_ms", 0)),
        trim_margin_tail_ms=int(audio.get("trim_margin_tail_ms", 0)),
    )


def prediction_payload(prediction: Estimate, music: dict[str, Any]) -> dict[str, Any]:
    """Le chiavi di durata e costo comuni a estimate.py (per copione e per run)."""
    extra = float(music.get("music_extra_seconds", 0.0) or 0.0)
    return {
        "turns": prediction.turns,
        "speakers": prediction.speakers,
        "chars": prediction.total_chars,
        "words": prediction.total_words,
        "spoken_chars": prediction.spoken_chars,
        "speech_seconds": round(prediction.speech_seconds, 1),
        "pause_seconds": round(prediction.pause_seconds, 1),
        # Solo parlato + pause fra i turni: NON include intro/outro. Per
        # quello vedi duration_with_music_*, sotto.
        "duration_seconds": round(prediction.total_seconds, 1),
        "duration_low_seconds": round(prediction.low_seconds, 1),
        "duration_high_seconds": round(prediction.high_seconds, 1),
        "duration_label": prediction.duration_label,
        **music,
        # L'incertezza si applica solo alla parte parlata: intro e outro sono
        # misurate sui file, non stimate.
        "duration_with_music_seconds": round(prediction.total_seconds + extra, 1),
        "duration_with_music_low_seconds": round(prediction.low_seconds + extra, 1),
        "duration_with_music_high_seconds": round(prediction.high_seconds + extra, 1),
        "cost_basis": prediction.cost_basis,
        "rendered": prediction.render(),
    }


def load_run_script(run_dir: Path) -> PodcastScript:
    script_file = Path(run_dir) / SCRIPT_FILENAME
    if not script_file.is_file():
        raise DomainError(
            "Questo episodio non ha ancora un copione: scrivilo e approvalo prima di stimare o produrre.",
            error_type="ScriptMissing",
        )
    return PodcastScript.from_dict(json.loads(script_file.read_text(encoding="utf-8")))


def approval_state(run_dir: Path, script: PodcastScript) -> dict[str, Any]:
    """Approvato o no, e perche': stessa regola del gate (sha256)."""
    session_path = Path(run_dir) / SESSION_FILENAME
    if not session_path.is_file():
        return {
            "approved": False,
            "approved_version": None,
            "approval_problem": "Non risulta nessun copione approvato per questo episodio.",
        }
    session = ScriptSession.load(session_path)
    problem = session.approval_problem(script.to_dict())
    return {
        "approved": problem is None,
        "approved_version": (session.approved or {}).get("version") if problem is None else None,
        "approval_problem": problem,
    }


def _roles_by_voice(run_dir: Path) -> dict[str, str]:
    episode = EpisodeConfig.load(run_dir)
    if episode is None:
        return {}
    return {row["voce"]: row["ruolo"] for row in episode.roles_view() if row.get("voce")}


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def estimate_run(run_dir: Path, config: Any, provider: Any) -> dict[str, Any]:
    """La stima del passo 5 per una run: voci, consenso, battute, costi.

    Funziona anche senza voci.json (G2): per le voci gia' clonate le chiavi
    dei segmenti si ricavano dal registro, e le battute delle voci ancora da
    clonare contano tutte come da generare (la loro chiave dipende da un
    voice_id che non esiste ancora). E' la stessa scelta che fara' produce():
    le voci si prendono dal registro.
    """
    run_dir = Path(run_dir)
    script = load_run_script(run_dir)
    plans = plan_voices(config, list(script.speakers))
    roles = _roles_by_voice(run_dir)

    to_clone = {p.speaker for p in plans if p.action == ACTION_CLONE}
    # Le voci da clonare non hanno un voice_id: un segnaposto basta a
    # calcolare le chiavi delle altre, e le loro battute sono comunque da fare.
    voice_map = {p.speaker: p.voice_id or f"__da_clonare__:{p.speaker}" for p in plans}
    cloned_later = {i for i, turn in enumerate(script.turni, start=1) if turn["speaker"] in to_clone}
    missing = sorted(set(missing_turns(run_dir, script, voice_map, provider)) | cloned_later)

    chars_to_generate = sum(len(script.turni[i - 1]["testo"]) for i in missing)
    pricing = provider.pricing()
    tts_cost = (
        chars_to_generate / 1_000_000 * float(pricing.cost_per_million_chars_usd or 0.0)
        if pricing.is_known
        else None
    )
    unit_clone_cost = config.clone_cost_usd
    clone_total = unit_clone_cost * len(to_clone) if unit_clone_cost is not None else None
    if not to_clone:
        clone_total = 0.0
    if tts_cost is None:
        cost = None
    elif clone_total is None:
        cost = tts_cost  # dichiarato incompleto in cost_complete
    else:
        cost = tts_cost + clone_total
    rate = config.eur_rate

    music = music_durations(run_dir, config)
    music.pop("_note_parts", None)
    prediction = predict(script, provider, config)

    voices = []
    for plan in plans:
        view = plan.view()
        view.pop("voice_id", None)
        view["ruolo"] = roles.get(plan.speaker)
        voices.append(view)

    return {
        "run": run_dir.name,
        **approval_state(run_dir, script),
        **prediction_payload(prediction, music),
        "voices": voices,
        "voices_to_clone": sorted(to_clone),
        "consent_missing": [p.speaker for p in plans if p.blocked_by_consent],
        "turns_to_generate": len(missing),
        "turns_to_generate_list": missing,
        "chars_to_generate": chars_to_generate,
        "cost_tts_usd": _round(tts_cost),
        "clone_cost_usd": unit_clone_cost,
        "clone_cost_known": unit_clone_cost is not None,
        "clone_cost_total_usd": _round(clone_total),
        # Costo di QUESTA produzione: battute da generare + voci da clonare.
        # cost_complete false = manca il costo della clonazione (non noto).
        "cost_usd": _round(cost),
        "cost_complete": cost is not None and (clone_total is not None),
        "cost_eur": _round(cost * rate[0]) if (cost is not None and rate) else None,
        "eur_per_usd": rate[0] if rate else None,
        "eur_rate_date": rate[1] if rate else None,
    }


# ----------------------------------------------------------------------
# Produzione (passo 6)
# ----------------------------------------------------------------------


def ui_path(path: Path, config: Any) -> str:
    """Un file della run come lo vede la UI: `output/<run>/<file>`, mai assoluto.

    produce.py e' il comando dell'interfaccia: il suo JSON non riporta la
    struttura delle cartelle del server (revisione di sicurezza C2, BASSA-3).
    """
    root = Path(config.output_dir)
    try:
        return f"{root.name}/{Path(path).resolve().relative_to(root.resolve()).as_posix()}"
    except ValueError:
        return Path(path).name


def _interrupted(done: int, total: int, missing: list[int]) -> ProductionInterrupted:
    return ProductionInterrupted(
        f"Produzione interrotta: {done} battute pronte su {total}. Le battute pronte restano "
        "salvate: rilanciando si generano solo le mancanti.",
        details={"done": done, "total": total, "missing": missing},
    )


def _archive_document(run_dir: Path, config: Any) -> str | None:
    """Copia il documento di partenza nella run, se e' ancora nella libreria."""
    episode = EpisodeConfig.load(run_dir)
    record = (episode.documento if episode else None) or {}
    source = record.get("percorso") or record.get("nome")
    if not source:
        return None
    located = locate_document(source, search_dir=config.documents_dir, confined=True)
    return archive_source_document(located, run_dir)


def produce(
    run_dir: Path,
    config: Any,
    *,
    events: EventLog,
    should_stop: Callable[[], bool] = lambda: False,
    accept_changed_music: bool = False,
    note: Note = _noop,
) -> dict[str, Any]:
    """Il passo 6 per intero. Vedi la docstring del modulo.

    Anche gli eventi finali (`interrotta`, `errore`) si scrivono qui, ancora
    sotto il lock di lavoro: scritti dopo, potrebbero finire nel file eventi
    della produzione successiva (revisione di sicurezza C2, BASSA-1).
    """
    run_dir = Path(run_dir)
    with work_lock(run_dir, "produzione"):
        try:
            return _produce_locked(
                run_dir, config, events=events, should_stop=should_stop,
                accept_changed_music=accept_changed_music, note=note,
            )
        except ProductionInterrupted as exc:
            if events.opened:
                events.emit("interrotta", **(exc.details or {}))
            raise
        except BaseException as exc:
            if events.opened:
                # Lo stesso error_type del JSON finale, dall'elenco unico
                # (sicurezza C8, BASSA-2): mai il nome grezzo di una classe.
                _, kind, _ = public_error(exc)
                events.emit("errore", error_type=kind, error=redact(str(exc)))
            raise
        finally:
            events.close()


def _produce_locked(
    run_dir: Path,
    config: Any,
    *,
    events: EventLog,
    should_stop: Callable[[], bool],
    accept_changed_music: bool,
    note: Note,
) -> dict[str, Any]:
    script = load_run_script(run_dir)
    approval = approval_state(run_dir, script)
    if not approval["approved"]:
        raise ApprovalRequired(
            f"Passo bloccato: {approval['approval_problem']}",
            details={"approval_problem": approval["approval_problem"]},
        )
    total = len(script.turni)

    # Musiche: tutto validato PRIMA di spendere (clonazione compresa). Un
    # errore qui e' solo nel JSON su stdout: 'iniziata' si scrive dopo, con le
    # fasi che ci saranno davvero (senza musiche niente 'mix', revisione
    # finale N14).
    music_choice, music_warnings = preflight(run_dir, config, accept_changed_music=accept_changed_music)
    phases = [p for p in PHASES if p != "mix" or music_choice is not None]
    events.emit("iniziata", totale=total, fasi=phases)
    for warning in music_warnings:
        events.emit("avviso", testo=warning)

    provider = config.build_provider()

    # Un'approvazione ritirata a produzione in corso (script_approve --revoke,
    # cambio di cast, ripristino di una versione) la ferma alla clonazione o
    # alla battuta successiva: il copione che si sta pagando non e' piu'
    # quello approvato (revisione finale, N2). Si rilegge solo la sessione.
    approval_lost: list[str] = []

    def stop_or_unapproved() -> bool:
        if should_stop():
            return True
        now = approval_state(run_dir, script)
        if not now["approved"]:
            approval_lost.append(now["approval_problem"] or "approvazione ritirata")
            return True
        return False

    def approval_withdrawn(done: int, missing: list[int]) -> ApprovalRequired:
        return ApprovalRequired(
            f"L'approvazione del copione e' stata ritirata durante la produzione: mi sono fermato "
            f"({done} battute pronte su {total}). {approval_lost[0]}",
            details={"approval_problem": approval_lost[0], "done": done, "total": total, "missing": missing},
        )

    # -- voci ----------------------------------------------------------
    events.emit("fase_iniziata", fase="preparazione_voci")
    prepared = prepare_voices(
        config,
        list(script.speakers),
        provider=provider,
        on_voice=lambda speaker, action: events.emit("voce", speaker=speaker, azione=action),
        should_stop=stop_or_unapproved,
        note=note,
    )
    if len(prepared.voice_map) < len(script.speakers):
        # Fermata fra una clonazione e l'altra: nessuna battuta toccata.
        if approval_lost:
            raise approval_withdrawn(0, list(range(1, total + 1)))
        raise _interrupted(0, total, list(range(1, total + 1)))
    write_json_atomic(run_dir / VOICES_FILENAME, prepared.voice_map)
    write_json_atomic(run_dir / AUDIO_PATHS_FILENAME, prepared.audio_paths)
    events.emit(
        "fase_finita",
        fase="preparazione_voci",
        riusate=len(prepared.reused),
        clonate=len(prepared.newly_cloned),
    )

    # -- sintesi, montaggio, mix ----------------------------------------
    to_generate = set(missing_turns(run_dir, script, prepared.voice_map, provider))
    timing = {"last_start": None, "durations": []}

    def progress(index: int, count: int, speaker: str, cached: bool) -> None:
        now = time.monotonic()
        if timing["last_start"] is not None:
            timing["durations"].append(now - timing["last_start"])
            timing["last_start"] = None
        remaining = len([n for n in to_generate if n >= index])
        durations = timing["durations"]
        eta = round(sum(durations) / len(durations) * remaining, 1) if durations else None
        events.emit("battuta", n=index, totale=count, speaker=speaker, cached=cached, eta_seconds=eta)
        note(f"  [{index:>3}/{count}] {speaker}{' (in cache)' if cached else ''}")
        if not cached:
            timing["last_start"] = now

    def phase(name: str, state: str) -> None:
        events.emit(f"fase_{state}", fase=name)

    # Una revoca del consenso a produzione in corso la ferma alla battuta
    # successiva (revisione di sicurezza C2, BASSA-2): basta rileggere i
    # metadati (piccoli) fra una battuta e l'altra, senza calcolare hash.
    revoked: list[str] = []

    def stop_or_revoked() -> bool:
        if stop_or_unapproved():
            return True
        # Anche le revoche tenute nel registro delle voci (revisione finale, N1).
        registry_now = VoiceRegistry(config.registry_file, project_root=config.registry_file.parent)
        revoked.extend(
            speaker
            for speaker, path in prepared.audio_paths.items()
            if consent_revoked(path) or registry_now.revocation_for(voice_id=prepared.voice_map.get(speaker))
        )
        return bool(revoked)

    try:
        render = render_episode(
            run_dir,
            config,
            script,
            prepared.voice_map,
            provider=provider,
            music_choice=music_choice,
            music_warnings=music_warnings,
            accept_changed_music=accept_changed_music,
            progress=progress,
            should_stop=stop_or_revoked,
            on_phase=phase,
            note=note,
        )
    except SynthesisStopped as stop:
        if approval_lost:
            raise approval_withdrawn(stop.done, stop.missing) from stop
        if revoked:
            speakers = sorted(set(revoked))
            raise ConsentRequired(
                "Il consenso di " + ", ".join(speakers) + " e' stato revocato durante la "
                f"produzione: mi sono fermato ({stop.done} battute pronte su {stop.total}). "
                "Le battute con questa voce non vanno usate.",
                details={
                    "voci": [
                        {"speaker": name, "azione": "reuse", "stato": "revocato", "file": Path(prepared.audio_paths[name]).name}
                        for name in speakers
                    ],
                    "done": stop.done,
                    "total": stop.total,
                    "missing": stop.missing,
                },
            ) from stop
        raise _interrupted(stop.done, stop.total, stop.missing) from stop

    for warning in render.warnings:
        if warning not in music_warnings:
            events.emit("avviso", testo=warning)

    # -- chiusura ---------------------------------------------------------
    events.emit("fase_iniziata", fase="chiusura")
    registry = VoiceRegistry(config.registry_file, project_root=config.registry_file.parent)
    write_voices_used(run_dir, config.active_provider, prepared.voice_map, prepared.audio_paths, registry)
    warnings = list(render.warnings)
    document_note = None
    try:
        document_note = _archive_document(run_dir, config)
    except DocumentError:
        warnings.append(
            "Il documento di partenza non e' piu' nella libreria Documenti: la copia "
            "nella cartella dell'episodio non e' stata fatta."
        )
        events.emit("avviso", testo=warnings[-1])
    events.emit("fase_finita", fase="chiusura")

    synthesis = render.synthesis
    payload = {
        "run": run_dir.name,
        "output": ui_path(render.output_path, config),
        "file": render.output_path.name,
        "format": render.output_format,
        "duration_seconds": round(render.duration_seconds, 1) if render.duration_seconds else None,
        "duration_label": duration_label(render.duration_seconds),
        "segments": len(synthesis.paths),
        "generated_turns": synthesis.generated_turns,
        "reused_turns": synthesis.reused_turns,
        "tts_calls": len(synthesis.generated_turns),
        "voices": {"riusate": prepared.reused, "clonate": prepared.newly_cloned},
        "approved_version": approval["approved_version"],
        "documento": document_note,
        "timings_file": ui_path(render.timings_file, config) if render.timings_file else None,
        "warnings": redact(warnings),
        **render.music_payload,
    }
    if payload.get("musiche_usate_file"):
        payload["musiche_usate_file"] = ui_path(Path(payload["musiche_usate_file"]), config)
    events.emit(
        "finita",
        file=render.output_path.name,
        duration_seconds=payload["duration_seconds"],
        tts_calls=payload["tts_calls"],
    )
    return payload


__all__ = [
    "AUDIO_PATHS_FILENAME",
    "MONTAGE_FILENAME",
    "assemble_episode",
    "final_basename",
    "load_montage",
    "write_montage",
    "EVENTS_FILENAME",
    "ApprovalRequired",
    "EventLog",
    "PHASES",
    "ProductionInterrupted",
    "RenderResult",
    "approval_state",
    "ui_path",
    "duration_label",
    "estimate_run",
    "load_run_script",
    "missing_turns",
    "predict",
    "prediction_payload",
    "produce",
    "render_episode",
    "write_timings",
]
