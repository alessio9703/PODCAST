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
from .errors import DomainError
from .fileio import write_json_atomic
from .music_context import music_durations
from .music_mixer import mix_episode, preflight
from .pdf_reader import DocumentError, locate_document
from .registry import VoiceRegistry
from .run_layout import archive_source_document, write_voices_used
from .run_lock import work_lock
from .script_session import FILENAME as SESSION_FILENAME
from .script_session import ScriptSession
from .script_writer import PodcastScript, spoken_text
from .segment_cache import SCRIPT_FILENAME, VOICES_FILENAME
from .voice_prep import ACTION_CLONE, plan_voices, prepare_voices

AUDIO_PATHS_FILENAME = "_audio_paths.json"
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
            self._handle = self.path.open("w", encoding="utf-8")
        line = json.dumps(
            {"evento": evento, "at": datetime.now().replace(microsecond=0).isoformat(), **data},
            ensure_ascii=False,
        )
        self._handle.write(line + "\n")
        self._handle.flush()

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
    synthesis: SynthesisResult
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
    segments = synthesis.paths
    if synthesis.reused_turns:
        note(
            f"{len(synthesis.reused_turns)} turni ripresi dalla cache, "
            f"{len(synthesis.generated_turns)} sintetizzati ora."
        )

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
            choice=music_choice, on_mix_start=mix_started,
        )
        phase("mix", "finita")
        voice_result = mix_result.voice_assembly
        output_path = mix_result.path
        output_format = "mp3"
        duration_seconds = mix_result.duration_seconds
        warnings = [*synthesis.warnings, *music_warnings, *mix_result.warnings]
        music_payload = {
            "voice_offset_seconds": mix_result.voice_offset_seconds,
            "voice_duration_seconds": mix_result.voice_duration_seconds,
            "musiche_usate_file": str(mix_result.musiche_usate_path),
        }
    else:
        note("Montaggio...")
        voice_result = assembler.assemble(segments, run_dir / slugify(script.titolo))
        phase("montaggio", "finita")
        output_path = voice_result.path
        output_format = voice_result.audio_format
        duration_seconds = voice_result.duration_seconds
        warnings = [*synthesis.warnings, *voice_result.warnings]

    # RACCOLTA DATI (non entra nella stima): velocita' di lettura per turno e
    # per voce, riferita SEMPRE al master di sola voce (vedi README.md,
    # "Correggere una battuta"). Va misurata PRIMA di un'eventuale
    # cancellazione dei segmenti.
    timings_file = write_timings(run_dir, script, voice_result)

    # Segnali per le lezioni (src/episode_signals.py). Mai bloccante: la
    # sintesi e' gia' andata a buon fine a questo punto.
    try:
        record_synthesis_event(run_dir, script)
        collect_and_write(run_dir, provider=provider)
    except Exception as exc:  # noqa: BLE001 - raccolta dati, non deve mai far fallire la sintesi
        note(f"Raccolta segnali non riuscita (non blocca l'episodio): {exc}")

    return RenderResult(
        output_path=output_path,
        output_format=output_format,
        duration_seconds=duration_seconds,
        synthesis=synthesis,
        warnings=warnings,
        music_payload=music_payload,
        timings_file=timings_file,
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
    """Il passo 6 per intero. Vedi la docstring del modulo."""
    run_dir = Path(run_dir)
    with work_lock(run_dir, "produzione"):
        script = load_run_script(run_dir)
        approval = approval_state(run_dir, script)
        if not approval["approved"]:
            raise ApprovalRequired(
                f"Passo bloccato: {approval['approval_problem']}",
                details={"approval_problem": approval["approval_problem"]},
            )
        total = len(script.turni)
        events.emit("iniziata", totale=total, fasi=list(PHASES))

        # Musiche: tutto validato PRIMA di spendere (clonazione compresa).
        music_choice, music_warnings = preflight(run_dir, config, accept_changed_music=accept_changed_music)
        for warning in music_warnings:
            events.emit("avviso", testo=warning)

        provider = config.build_provider()

        # -- voci ----------------------------------------------------------
        events.emit("fase_iniziata", fase="preparazione_voci")
        prepared = prepare_voices(
            config,
            list(script.speakers),
            provider=provider,
            on_voice=lambda speaker, action: events.emit("voce", speaker=speaker, azione=action),
            should_stop=should_stop,
            note=note,
        )
        if len(prepared.voice_map) < len(script.speakers):
            # Fermata fra una clonazione e l'altra: nessuna battuta toccata.
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
                should_stop=should_stop,
                on_phase=phase,
                note=note,
            )
        except SynthesisStopped as stop:
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
            "output": str(render.output_path),
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
            "timings_file": str(render.timings_file) if render.timings_file else None,
            "warnings": warnings,
            **render.music_payload,
        }
        events.emit(
            "finita",
            file=render.output_path.name,
            duration_seconds=payload["duration_seconds"],
            tts_calls=payload["tts_calls"],
        )
        return payload


__all__ = [
    "AUDIO_PATHS_FILENAME",
    "ApprovalRequired",
    "EventLog",
    "PHASES",
    "ProductionInterrupted",
    "RenderResult",
    "approval_state",
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
