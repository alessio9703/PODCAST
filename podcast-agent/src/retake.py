"""Rifare una singola battuta senza rigenerare l'episodio.

Due tipi di correzione, e solo il secondo passa di qui:

  - il TESTO e' sbagliato -> si riscrive il copione, si rifa' approvare, e la
    sintesi incrementale ripaga da sola la sola battuta cambiata: la chiave del
    segmento dipende dal testo, quindi cambia solo quella;
  - la RESA e' sbagliata (intonazione, pronuncia, un artefatto) -> il testo va
    bene, serve un altro tentativo. Il TTS non e' deterministico: chiedere di
    nuovo lo stesso testo produce un audio diverso. E' quello che fa questo
    modulo, mettendo i tentativi uno accanto all'altro come take numerati
    invece di sovrascrivere quello di prima.

La scelta fra i take resta all'utente. Il codice genera, monta le anteprime e
segnala gli sbalzi di livello; non decide quale sia il take buono, perche'
"buono" qui vuol dire "suona giusto", e non e' una cosa che si misura.
"""

from __future__ import annotations

import math

from .audio_assembler import (
    PREVIEWS_DIRNAME,
    AssemblyError,
    AudioAssembler,
    existing_takes,
    load_choices,
    save_choices,
    segment_path,
    segment_rms,
    synthesize_segment,
    _write_atomic,
)
from .segment_cache import (
    SCRIPT_FILENAME,
    VOICES_FILENAME,
    RunCache,
    SegmentCacheError,
)
from .voice_provider import VoiceProvider

#: Oltre questo scarto il take nuovo, montato accanto agli altri dello stesso
#: speaker, si sente come uno sbalzo di volume. 3 dB e' il raddoppio di potenza:
#: sotto si nota a malapena, sopra si nota e basta.
RMS_WARNING_DB = 3.0


class RetakeError(SegmentCacheError):
    """La run non contiene quello che serve per rifare una battuta."""


# ----------------------------------------------------------------------
# Stima — sempre prima di spendere
# ----------------------------------------------------------------------


def estimate_retake(cache: RunCache, numero: int, takes: int) -> dict[str, object]:
    """Quanto costa rifare quella battuta, senza chiamare il provider.

    I caratteri sono quelli GREZZI: le inline tag [fra quadre] vengono inviate
    all'API e quindi pagate, anche se non vengono pronunciate (stessa regola di
    cost_estimate.py).
    """
    turn = cache.turn(numero)
    chars = len(turn["testo"]) * takes
    pricing = cache.provider.pricing()
    cost = None
    basis = None
    if pricing.is_known:
        rate = float(pricing.cost_per_million_chars_usd or 0.0)
        cost = chars / 1_000_000 * rate
        basis = f"${rate:.2f} / 1M caratteri"
    return {
        "turn": numero,
        "speaker": turn["speaker"],
        "takes": takes,
        "chars": chars,
        "cost_usd": round(cost, 4) if cost is not None else None,
        "cost_basis": basis,
    }


# ----------------------------------------------------------------------
# Generazione dei take
# ----------------------------------------------------------------------


def verify_voice(provider: VoiceProvider, voice_id: str) -> None:
    """Controlla che la voce della run esista ANCORA sul provider.

    Se non c'e' piu' ci si ferma: **non si riclona**. Riclonare qui darebbe un
    voice_id nuovo e un timbro leggermente diverso da quello del resto
    dell'episodio, e lo scarto si sentirebbe solo sulla battuta corretta —
    cioe' esattamente dove si sta cercando di migliorare le cose. La regola
    "nessun retry sulla clonazione" vale anche qui: e' l'utente a decidere se
    riclonare e rifare l'episodio.
    """
    known = {v.id for v in provider.list_cloned_voices()}
    if voice_id not in known:
        raise RetakeError(
            f"La voce '{voice_id}' usata da questa run non esiste piu' su "
            f"'{provider.name}': e' stata cancellata o rigenerata.\n"
            "Mi fermo qui e NON riclono: una voce nuova avrebbe un timbro "
            "diverso dal resto dell'episodio, e lo stacco si sentirebbe proprio "
            "sulla battuta che stai correggendo.\n"
            "Se vuoi procedere, riclona la voce e rigenera l'episodio per intero."
        )


def generate_takes(
    cache: RunCache,
    assembler: AudioAssembler,
    numero: int,
    count: int,
    note=None,
) -> list[dict[str, object]]:
    """Genera `count` take nuovi per il turno, senza toccare quelli esistenti.

    I numeri dei take partono dal primo libero: i take vecchi restano al loro
    posto, perche' l'utente deve poter tornare su uno di quelli dopo averli
    riascoltati.
    """
    if count < 1:
        raise RetakeError("Servono almeno 1 take da generare.")

    turn = cache.turn(numero)
    voice_id = cache.voice_id(numero)
    key = cache.key(numero)
    segments_dir = cache.segments_dir
    segments_dir.mkdir(parents=True, exist_ok=True)

    verify_voice(cache.provider, voice_id)

    reference = _speaker_reference_rms(cache, numero)
    start = max(existing_takes(segments_dir, key, cache.extension), default=0) + 1
    results: list[dict[str, object]] = []

    for offset in range(count):
        take = start + offset
        target = segment_path(segments_dir, key, take, cache.extension)
        if note:
            note(f"  take {take}: sintesi del turno {numero} ({turn['speaker']})...")
        audio, defect = synthesize_segment(
            cache.provider,
            turn["testo"],
            voice_id,
            dropout_detection=assembler.dropout_detection,
            dropout_min_ratio=assembler.dropout_min_ratio,
            dropout_max_retries=assembler.dropout_max_retries,
            trim_threshold_pct=assembler.trim_threshold_pct,
            dropout_max_gap_seconds=assembler.dropout_max_gap_seconds,
            dropout_gap_relative_pct=assembler.dropout_gap_relative_pct,
        )
        _write_atomic(target, audio)

        warnings: list[str] = []
        if defect is not None:
            warnings.append(
                f"Questo take {defect.describe()}, anche dopo i tentativi "
                "automatici. Ascolta l'anteprima con attenzione prima di "
                "sceglierlo."
            )
        level = segment_rms(target)
        delta = None
        if level and reference:
            delta = 20 * math.log10(level / reference)
            if abs(delta) > RMS_WARNING_DB:
                direction = "piu' forte" if delta > 0 else "piu' piano"
                warnings.append(
                    f"Il take {take} e' {abs(delta):.1f} dB {direction} della media "
                    f"degli altri segmenti di {turn['speaker']}. Montato "
                    "nell'episodio si sentirebbe come uno sbalzo di volume: "
                    "ascolta l'anteprima prima di sceglierlo."
                )

        preview = _build_preview(cache, assembler, numero, take)
        results.append(
            {
                "take": take,
                "file": str(target),
                "preview": str(preview) if preview else None,
                "rms_delta_db": round(delta, 1) if delta is not None else None,
                "warnings": warnings,
            }
        )

    return results


def _speaker_reference_rms(cache: RunCache, numero: int) -> float | None:
    """RMS medio degli ALTRI segmenti dello stesso speaker gia' in cache."""
    speaker = cache.turn(numero)["speaker"]
    levels: list[float] = []
    for index, turn in enumerate(cache.script.turni, start=1):
        if index == numero or turn["speaker"] != speaker:
            continue
        path = cache.path(index)
        if path.is_file():
            level = segment_rms(path)
            if level:
                levels.append(level)
    if not levels:
        return None
    return sum(levels) / len(levels)


def _build_preview(
    cache: RunCache, assembler: AudioAssembler, numero: int, take: int
) -> Path | None:
    """Monta turno precedente + nuovo take + turno successivo.

    Un take giudicato da solo inganna: quello che conta e' come si incastra fra
    la battuta prima e quella dopo, con le pause vere dell'episodio. Per questo
    l'anteprima passa dallo stesso montaggio, ritaglio compreso.
    """
    if cache.provider.audio_format != "wav":
        return None

    pieces: list[Path] = []
    if numero > 1:
        previous = cache.path(numero - 1)
        if previous.is_file():
            pieces.append(previous)
    pieces.append(segment_path(cache.segments_dir, cache.key(numero), take, cache.extension))
    if numero < len(cache.script.turni):
        following = cache.path(numero + 1)
        if following.is_file():
            pieces.append(following)

    target = (
        cache.segments_dir / PREVIEWS_DIRNAME / f"turno-{numero:03d}_t{take}.wav"
    )
    try:
        return assembler.preview(pieces, target)
    except AssemblyError:
        # Un'anteprima che non si monta non deve buttare via il take appena
        # pagato: si segnala l'assenza e si va avanti.
        return None


# ----------------------------------------------------------------------
# Scelta
# ----------------------------------------------------------------------


def choose_take(cache: RunCache, numero: int, take: int) -> dict[str, object]:
    """Registra quale take va usato nel montaggio. Nessuna chiamata al provider."""
    available = cache.takes(numero)
    if take not in available:
        raise RetakeError(
            f"Il take {take} del turno {numero} non esiste. "
            f"Disponibili: {available or 'nessuno'}.\n"
            "Generane di nuovi con --takes, oppure scegline uno di questi."
        )
    choices = load_choices(cache.segments_dir)
    choices[cache.key(numero)] = take
    save_choices(cache.segments_dir, choices)
    return {"turn": numero, "chosen_take": take, "available_takes": available}


__all__ = [
    "RunCache",
    "RetakeError",
    "RMS_WARNING_DB",
    "SCRIPT_FILENAME",
    "VOICES_FILENAME",
    "estimate_retake",
    "generate_takes",
    "verify_voice",
    "choose_take",
]
