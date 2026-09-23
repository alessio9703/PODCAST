"""Il mix delle musiche: intro (opzionale) + sezione parlata + outro (opzionale).

Nessun modulo al di fuori di questo costruisce comandi ffmpeg per il mix (vedi
LEGENDA.md). Il montaggio della voce resta quello di
`src/audio_assembler.py`, invariato: questo modulo parte da un wav voce gia'
montato e ci sovrappone le musiche, DOPO, come passo separato.

Perche' un mix in Python e non tutto in un filtergraph ffmpeg: looping con
dissolvenza incrociata, dissolvenze, guadagni e somma dei segnali si fanno qui
campione per campione (stesso stile di `_trim_edges`/`_cap_internal_silence`
in audio_assembler.py), cosi' il risultato e' deterministico e ispezionabile.
ffmpeg resta per le tre cose che in Python non ha senso reimplementare:
decodifica/ricampionamento di formati compressi, normalizzazione loudness
(`loudnorm`) e compressione sidechain per il ducking.

Riproducibilita': a parita' di segmenti, brani scelti e parametri di
config.yaml, il wav prodotto PRIMA della codifica mp3 e' lo stesso — ffmpeg
viene chiamato sempre con gli stessi argomenti sugli stessi byte in ingresso,
e la parte in puro Python non ha nessuna sorgente di casualita'.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import wave
from array import array
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .audio_assembler import AudioAssembler, slugify
from .ffmpeg_tool import find_ffmpeg, require_ffmpeg
from .music_library import (
    CATEGORIES,
    MusicLibrary,
    MusicTrack,
    has_any_music,
    load_music_choice,
)
from .script_session import FILENAME as SESSION_FILENAME
from .script_session import ScriptSession, ScriptSessionError
from .script_writer import PodcastScript

MUSICHE_USATE_FILENAME = "musiche_usate.json"


class MusicMixerError(RuntimeError):
    """Errore di dominio durante la preparazione o l'esecuzione del mix."""


class MusicChangedError(MusicMixerError):
    """Un brano gia' registrato in musiche_usate.json ha uno sha256 diverso.

    Serve --accept-changed-music per procedere: un episodio vecchio non deve
    cambiare sigla in silenzio solo perche' il file su disco e' stato
    sostituito.
    """


@dataclass(frozen=True)
class ChosenMusic:
    intro: MusicTrack | None = None
    outro: MusicTrack | None = None
    sottofondo: MusicTrack | None = None

    def any(self) -> bool:
        return bool(self.intro or self.outro or self.sottofondo)


@dataclass
class MixResult:
    path: Path
    duration_seconds: float
    voice_offset_seconds: float
    voice_duration_seconds: float
    warnings: list[str]
    musiche_usate_path: Path
    #: Esito del montaggio voce-sola (segment_seconds/turn_timeline), lo
    #: stesso che synthesize.py scrive in _tempi_turni.json: il file e'
    #: riferito al master DI SOLA VOCE anche quando l'episodio ha musiche,
    #: per costruzione non serve rimontarlo una seconda volta per ottenerlo.
    voice_assembly: Any = None


# ----------------------------------------------------------------------
# Preflight — PRIMA di qualunque sintesi
# ----------------------------------------------------------------------


def preflight(
    run_dir: Path | str, config: Any, accept_changed_music: bool = False
) -> tuple[ChosenMusic | None, list[str]]:
    """Valida senza costruire nulla. Va chiamata PRIMA di ogni sintesi.

    Ritorna (None, []) se non e' stata scelta nessuna musica: il chiamante
    procede come oggi, senza bisogno di ffmpeg. Solleva MusicMixerError se
    manca ffmpeg pur avendo scelto una musica, o se un brano scelto non e'
    piu' valido; MusicChangedError se un brano gia' registrato in
    musiche_usate.json e' stato sostituito e non e' stato passato
    --accept-changed-music.
    """
    run_dir = Path(run_dir)
    choice_raw = load_music_choice(run_dir)
    if not has_any_music(choice_raw):
        return None, []

    if find_ffmpeg() is None:
        raise MusicMixerError(
            "Questo episodio ha almeno una musica scelta (musiche.json) ma ffmpeg "
            "non e' stato trovato. Mi fermo QUI, PRIMA di qualunque sintesi: "
            "nessuna chiamata al provider vocale e' stata fatta.\n"
            "Esegui '.venv/bin/pip install -r requirements.txt' (include "
            "imageio-ffmpeg) e riprova, oppure rimuovi le musiche con "
            "tools/music_set.py --none intro outro sottofondo. "
            "tools/doctor.py dice esattamente cosa manca."
        )

    library = MusicLibrary(config.music_dir, config.accepted_extensions)
    library.scan()
    resolved: dict[str, MusicTrack | None] = {}
    for category in CATEGORIES:
        name = choice_raw.get(category)
        resolved[category] = library.get_usable(category, name) if name else None

    choice = ChosenMusic(
        intro=resolved["intro"], outro=resolved["outro"], sottofondo=resolved["sottofondo"]
    )
    warnings = _check_registry_drift(run_dir, choice, accept_changed_music)
    return choice, warnings


def _check_registry_drift(
    run_dir: Path, choice: ChosenMusic, accept_changed_music: bool
) -> list[str]:
    usate_path = run_dir / MUSICHE_USATE_FILENAME
    if not usate_path.is_file():
        return []
    try:
        previous = json.loads(usate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []  # file corrotto: non e' questo il posto per bloccare

    changed: list[tuple[str, str]] = []
    for category in CATEGORIES:
        prev_entry = previous.get(category)
        track = getattr(choice, category)
        if prev_entry and track and prev_entry.get("name") == track.name:
            if prev_entry.get("sha256") != track.sha256():
                changed.append((category, track.name))

    if not changed:
        return []
    if not accept_changed_music:
        detail = "; ".join(f"{cat} ('{name}')" for cat, name in changed)
        raise MusicChangedError(
            f"Il file di uno o piu' brani gia' registrati in {MUSICHE_USATE_FILENAME} "
            f"e' cambiato: {detail}.\n"
            "Il file in musiche/ e' stato sostituito DOPO che questo episodio e' gia' "
            "stato montato: un episodio vecchio non deve cambiare sigla in silenzio.\n"
            "Se e' voluto, richiama con --accept-changed-music."
        )
    return [
        f"'{name}' ({cat}) e' stato sostituito rispetto all'ultimo mix: accettato "
        f"esplicitamente con --accept-changed-music, {MUSICHE_USATE_FILENAME} aggiornato."
        for cat, name in changed
    ]


def _check_script_music_mismatch(run_dir: Path, choice: ChosenMusic) -> list[str]:
    """Il copione era stato scritto per un'intro/outro diversa? Solo un avviso.

    Confronta lo SHA256 (non il nome): rinominare il file senza cambiarne il
    contenuto non deve generare un falso avviso, e viceversa sostituire il
    file lasciando lo stesso nome deve essere rilevato.
    """
    session_path = run_dir / SESSION_FILENAME
    if not session_path.is_file():
        return []
    try:
        session = ScriptSession.load(session_path)
    except ScriptSessionError:
        return []

    context = session.music_context or {}
    warnings: list[str] = []
    for category in ("intro", "outro"):
        recorded = context.get(category)
        if not recorded:
            continue
        track = getattr(choice, category)
        if track is None:
            warnings.append(
                f"Il copione era stato scritto tenendo conto di un {category} "
                f"('{recorded.get('name')}'), ma ora non ne e' stato scelto nessuno."
            )
        elif recorded.get("sha256") != track.sha256():
            warnings.append(
                f"Il copione era stato scritto per un {category} diverso da quello "
                f"scelto ora ('{track.name}'): se contiene parlato, il copione "
                "potrebbe ripetere quello che dice gia' l'intro/outro."
            )
    return warnings


# ----------------------------------------------------------------------
# Mix — la funzione unica chiamata da tools/synthesize.py e tools/mix.py
# ----------------------------------------------------------------------


def mix_episode(
    *,
    assembler: AudioAssembler,
    segments: list[Path],
    script: PodcastScript,
    run_dir: Path | str,
    config: Any,
    accept_changed_music: bool = False,
    choice: ChosenMusic | None = None,
) -> MixResult:
    """Rimonta la voce dai segmenti e ci sovrappone intro/outro/sottofondo.

    E' la STESSA funzione chiamata da tools/synthesize.py (dopo una sintesi
    fresca) e da tools/mix.py (rimontando dalla cache, zero chiamate TTS):
    nessuno dei due ha una copia propria della logica di mix.

    Se `choice` e' None viene ricalcolato qui con preflight(): il chiamante
    puo' pero' passare quello gia' validato PRIMA della sintesi, cosi' non si
    rifanno due volte gli stessi controlli (e i loro warning non si
    duplicano: e' compito del chiamante unire i warning del proprio
    preflight() con quelli di questo risultato).
    """
    run_dir = Path(run_dir)
    music_cfg = config.music

    warnings: list[str] = []
    if choice is None:
        choice, drift_warnings = preflight(
            run_dir, config, accept_changed_music=accept_changed_music
        )
        warnings.extend(drift_warnings)
    if choice is None or not choice.any():
        raise MusicMixerError(
            "mix_episode() chiamato senza nessuna musica scelta: per un episodio "
            "senza musiche usa assembler.assemble() direttamente."
        )

    segments_dir = run_dir / "segmenti"
    voice_master_path = segments_dir / assembler.VOICE_MASTER_FILENAME
    voice_assembly = assembler.assemble_voice_master(segments, voice_master_path)

    with wave.open(str(voice_master_path), "rb") as handle:
        rate = handle.getframerate()
        voice_channels = handle.getnchannels()
        voice_duration = handle.getnframes() / rate

    dynamic_pieces: list[str] = []
    target_lufs = float(music_cfg.get("target_lufs", -16))
    true_peak_db = float(music_cfg.get("true_peak_db", -1.5))

    with tempfile.TemporaryDirectory(prefix="lil-mix-") as tmp_name:
        tmp = Path(tmp_name)
        channels = _decide_channels(voice_channels, choice, tmp, rate)

        speech_path, _speech_seconds, is_dyn = build_speech_section(
            voice_wav=voice_master_path,
            choice=choice,
            rate=rate,
            channels=channels,
            music_cfg=music_cfg,
            tmp_dir=tmp,
        )
        if is_dyn:
            dynamic_pieces.append("sezione parlata (voce)")

        speech_norm = tmp / "sezione_norm.wav"
        if _loudnorm(speech_path, speech_norm, target_lufs, true_peak_db, rate):
            dynamic_pieces.append("sezione parlata (con sottofondo)")

        pieces: list[Path] = []
        intro_duration = 0.0
        if choice.intro:
            intro_conv = tmp / "intro_conv.wav"
            _convert(choice.intro.path, intro_conv, rate, channels)
            intro_faded = _fade_file(
                intro_conv, channels, rate, 0,
                int(music_cfg.get("intro_fade_out_ms", 0)), tmp, "intro",
            )
            intro_norm = tmp / "intro_norm.wav"
            if _loudnorm(intro_faded, intro_norm, target_lufs, true_peak_db, rate):
                dynamic_pieces.append("intro")
            pieces.append(intro_norm)
            intro_duration = _wav_duration(intro_norm)

        pieces.append(speech_norm)

        if choice.outro:
            outro_gap_s = float(music_cfg.get("outro_gap_ms", 600)) / 1000.0
            pieces.append(_silence_wav(tmp / "outro_gap.wav", channels, rate, outro_gap_s))
            outro_conv = tmp / "outro_conv.wav"
            _convert(choice.outro.path, outro_conv, rate, channels)
            outro_faded = _fade_file(
                outro_conv, channels, rate,
                int(music_cfg.get("outro_fade_in_ms", 0)), 0, tmp, "outro",
            )
            outro_norm = tmp / "outro_norm.wav"
            if _loudnorm(outro_faded, outro_norm, target_lufs, true_peak_db, rate):
                dynamic_pieces.append("outro")
            pieces.append(outro_norm)

        concatenated = tmp / "concat.wav"
        _concat_wavs(pieces, concatenated)

        final_wav = tmp / "finale.wav"
        if _loudnorm(concatenated, final_wav, target_lufs, true_peak_db, rate):
            dynamic_pieces.append("file finale")

        if dynamic_pieces:
            warnings.append(
                "La normalizzazione e' ricaduta su 'dynamic' invece di 'linear' per: "
                + ", ".join(dynamic_pieces) + ". Il guadagno lineare richiesto avrebbe "
                "superato il picco vero consentito: il livello su queste parti puo' "
                "variare piu' del solito."
            )

        warnings.extend(_check_script_music_mismatch(run_dir, choice))

        intro_gap_s = float(music_cfg.get("intro_gap_ms", 800)) / 1000.0 if choice.intro else 0.0
        voice_offset = intro_duration + intro_gap_s

        usate_path = _write_musiche_usate(
            run_dir, choice, dict(music_cfg), voice_offset, voice_duration
        )

        final_mp3 = run_dir / f"{slugify(script.titolo)}.mp3"
        bitrate = int(config.audio.get("output_mp3_bitrate", 192))
        _encode_mp3(final_wav, final_mp3, bitrate)

        duration = _wav_duration(final_wav)

    return MixResult(
        path=final_mp3,
        duration_seconds=duration,
        voice_offset_seconds=round(voice_offset, 3),
        voice_duration_seconds=round(voice_duration, 3),
        warnings=warnings,
        musiche_usate_path=usate_path,
        voice_assembly=voice_assembly,
    )


def _write_musiche_usate(
    run_dir: Path, choice: ChosenMusic, params: dict[str, Any],
    voice_offset: float, voice_duration: float,
) -> Path:
    def entry(track: MusicTrack | None) -> dict[str, Any] | None:
        if track is None:
            return None
        return {
            "name": track.name,
            "path": str(track.path),
            "sha256": track.sha256(),
            "duration_seconds": track.duration_seconds(),
            "licenza": track.metadata.licenza,
        }

    payload = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "intro": entry(choice.intro),
        "outro": entry(choice.outro),
        "sottofondo": entry(choice.sottofondo),
        "params": params,
        # dove inizia la sezione parlata nel file finale: durata dell'intro
        # (se c'e') piu' intro_gap_ms, altrimenti 0. Lo legge turn_at.py.
        "voice_offset_seconds": round(voice_offset, 3),
        # durata del master di sola voce (_tempi_turni.json e' riferito a
        # questo): oltre voice_offset + voice_duration_seconds si e' nell'outro.
        "voice_duration_seconds": round(voice_duration, 3),
    }
    target = run_dir / MUSICHE_USATE_FILENAME
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


# ----------------------------------------------------------------------
# Sezione parlata: voce (+ sottofondo). Riusata anche dalle anteprime
# di retake.py (--with-music), su un frammento breve.
# ----------------------------------------------------------------------


def build_speech_section(
    *,
    voice_wav: Path,
    choice: ChosenMusic,
    rate: int,
    channels: int,
    music_cfg: dict[str, Any],
    tmp_dir: Path,
) -> tuple[Path, float, bool]:
    """Silenzio(intro_gap, se c'e' intro) + voce normalizzata, + sottofondo.

    Il silenzio anticipato serve perche' la dissolvenza in entrata del
    sottofondo parte all'inizio della pausa dopo l'intro, non sulla prima
    parola: cosi' quando l'intro finisce il sottofondo e' gia' presente, sia
    pure debole, invece di entrare di scatto a meta' della pausa.

    Ritorna (percorso wav NON ancora ri-normalizzato dopo la somma col
    sottofondo, durata totale in secondi, se la normalizzazione della voce e'
    caduta su 'dynamic').
    """
    voice_conv = tmp_dir / "voce_conv.wav"
    _convert(voice_wav, voice_conv, rate, channels)
    voice_norm = tmp_dir / "voce_norm.wav"
    is_dynamic = _loudnorm(
        voice_conv, voice_norm,
        float(music_cfg.get("target_lufs", -16)),
        float(music_cfg.get("true_peak_db", -1.5)),
        rate,
    )

    _, _, voice_samples = _read_pcm(voice_norm)
    intro_gap_s = (
        float(music_cfg.get("intro_gap_ms", 800)) / 1000.0 if choice.intro else 0.0
    )
    if intro_gap_s > 0:
        voice_samples = _silence_samples(channels, rate, intro_gap_s) + voice_samples

    total_seconds = len(voice_samples) / channels / rate
    speech_path = tmp_dir / "sezione_voce.wav"
    _write_pcm(speech_path, channels, rate, voice_samples)

    if choice.sottofondo:
        bed_path, _gain = _build_bed(
            choice.sottofondo, tmp_dir, total_seconds, rate, channels, music_cfg
        )
        if bool(music_cfg.get("ducking", False)):
            ducked_path = tmp_dir / "bed_ducked.wav"
            _apply_ducking(bed_path, speech_path, ducked_path, rate, music_cfg)
            bed_path = ducked_path
        mixed_path = tmp_dir / "sezione_mixata.wav"
        _mix_pcm(speech_path, bed_path, mixed_path)
    else:
        mixed_path = speech_path

    return mixed_path, total_seconds, is_dynamic


def render_bed_under(
    voice_wav: Path,
    sottofondo: MusicTrack,
    rate: int,
    channels: int,
    music_cfg: dict[str, Any],
    tmp_dir: Path,
) -> Path:
    """Sottofondo mixato sotto un frammento breve di voce (retake --with-music).

    Stesso bed_level_db/ducking del mix vero, ma SENZA le dissolvenze
    bed_fade_in_ms/bed_fade_out_ms: quelle hanno senso solo sui bordi della
    sezione parlata intera, e un'anteprima e' quasi sempre presa a meta'
    episodio. Per lo stesso motivo la voce qui NON viene rinormalizzata: su
    un frammento di due o tre turni la misura di loudness integrata non e'
    affidabile (loudnorm e' pensato per materiale piu' lungo), quindi si
    lascia la voce al suo livello naturale e si porta il sottofondo a
    (target_lufs + bed_level_db) — la stessa distanza assoluta che avrebbe
    nel mix vero, dove la voce E' esattamente a target_lufs per costruzione.
    """
    voice_conv = tmp_dir / "voce_local_conv.wav"
    _convert(voice_wav, voice_conv, rate, channels)
    _, _, voice_samples = _read_pcm(voice_conv)
    total_seconds = len(voice_samples) / channels / rate

    bed_path, _gain = _build_bed_no_fades(
        sottofondo, tmp_dir, total_seconds, rate, channels, music_cfg
    )

    if bool(music_cfg.get("ducking", False)):
        ducked = tmp_dir / "bed_ducked_local.wav"
        _apply_ducking(bed_path, voice_conv, ducked, rate, music_cfg)
        bed_path = ducked

    out = tmp_dir / "anteprima_con_musica.wav"
    _mix_pcm(voice_conv, bed_path, out)
    return out


# ----------------------------------------------------------------------
# Costruzione del sottofondo: loop con dissolvenza incrociata, dissolvenze
# ai bordi, livello relativo alla voce.
# ----------------------------------------------------------------------


def _build_bed(
    track: MusicTrack, tmp_dir: Path, total_seconds: float, rate: int,
    channels: int, music_cfg: dict[str, Any],
) -> tuple[Path, float]:
    shaped_path, channels_out = _loop_and_fade_bed(
        track, tmp_dir, total_seconds, rate, channels, music_cfg,
        fade_in_ms=int(music_cfg.get("bed_fade_in_ms", 1500)),
        fade_out_ms=int(music_cfg.get("bed_fade_out_ms", 2000)),
    )
    return _level_bed(shaped_path, tmp_dir, channels_out, rate, music_cfg)


def _build_bed_no_fades(
    track: MusicTrack, tmp_dir: Path, total_seconds: float, rate: int,
    channels: int, music_cfg: dict[str, Any],
) -> tuple[Path, float]:
    shaped_path, channels_out = _loop_and_fade_bed(
        track, tmp_dir, total_seconds, rate, channels, music_cfg,
        fade_in_ms=0, fade_out_ms=0,
    )
    return _level_bed(shaped_path, tmp_dir, channels_out, rate, music_cfg)


def _loop_and_fade_bed(
    track: MusicTrack, tmp_dir: Path, total_seconds: float, rate: int,
    channels: int, music_cfg: dict[str, Any], *, fade_in_ms: int, fade_out_ms: int,
) -> tuple[Path, int]:
    raw = tmp_dir / f"bed_raw_{track.name[:20]}.wav".replace(" ", "_")
    _convert(track.path, raw, rate, channels)
    _, _, samples = _read_pcm(raw)

    bed_frames = len(samples) // channels
    bed_duration = bed_frames / rate
    crossfade_ms = int(music_cfg.get("bed_loop_crossfade_ms", 2000))

    if bed_duration < total_seconds and bed_duration < (2 * crossfade_ms / 1000):
        raise MusicMixerError(
            f"Il sottofondo '{track.name}' dura {bed_duration:.1f}s: troppo poco per "
            f"andare in loop con una dissolvenza di {crossfade_ms}ms (servirebbero "
            f"almeno {2 * crossfade_ms / 1000:.1f}s di materiale). Scegli un brano "
            "piu' lungo, oppure abbassa music.bed_loop_crossfade_ms."
        )

    needed_frames = max(1, int(round(total_seconds * rate)))
    looped = _loop_to_length(samples, channels, rate, needed_frames, crossfade_ms)

    faded = _apply_fade(looped, channels, rate, fade_in_ms, "in")
    faded = _apply_fade(faded, channels, rate, fade_out_ms, "out")

    shaped_path = tmp_dir / f"bed_shaped_{id(track)}.wav"
    _write_pcm(shaped_path, channels, rate, faded)
    return shaped_path, channels


def _level_bed(
    shaped_path: Path, tmp_dir: Path, channels: int, rate: int, music_cfg: dict[str, Any]
) -> tuple[Path, float]:
    """Porta il sottofondo a `bed_level_db` sotto la voce, per GUADAGNO misurato.

    La voce e' assunta a `target_lufs` esatta: e' vero per costruzione nel
    mix intero (la si normalizza prima), un'approssimazione accettabile
    nell'anteprima breve (vedi render_bed_under). Non e' un guadagno fisso
    sul file: si misura la loudness del sottofondo COSI' COM'E' (dopo
    loop/dissolvenze) e si calcola il guadagno che lo porta esattamente alla
    distanza voluta — coerente anche cambiando brano o voce.
    """
    measured = _measure_integrated_loudness(shaped_path)
    target_lufs = float(music_cfg.get("target_lufs", -16))
    # bed_level_db e' negativo (es. -20 = "20 dB sotto la voce"): il target
    # del sottofondo e' target_lufs + bed_level_db, non - bed_level_db.
    bed_level_db = float(music_cfg.get("bed_level_db", -20))
    bed_target_lufs = target_lufs + bed_level_db
    gain = bed_target_lufs - measured

    _, _, samples = _read_pcm(shaped_path)
    leveled = _apply_gain_db(samples, gain)
    leveled_path = tmp_dir / f"bed_leveled_{shaped_path.stem}.wav"
    _write_pcm(leveled_path, channels, rate, leveled)
    return leveled_path, gain


# ----------------------------------------------------------------------
# Canali: mono -> stereo se una musica scelta e' stereo e la voce e' mono
# ----------------------------------------------------------------------


def _decide_channels(
    voice_channels: int, choice: ChosenMusic, tmp_dir: Path, rate: int
) -> int:
    """Se la voce e' mono e una musica scelta e' stereo, promuove a stereo.

    Scelta di progetto (vedi il piano): sommare un sottofondo stereo a una
    voce mono scaricandolo a mono gli farebbe perdere la spazialita' senza
    motivo. Se tutto e' mono, resta mono. La conversione avviene PRIMA di
    qualunque misura di loudness: cambiare canali dopo aver misurato
    sposterebbe il valore misurato.
    """
    if voice_channels >= 2:
        return voice_channels
    for index, track in enumerate(
        t for t in (choice.intro, choice.outro, choice.sottofondo) if t
    ):
        probe = tmp_dir / f"_probe_{index}.wav"
        _run_ffmpeg([
            "-y", "-loglevel", "error", "-i", str(track.path),
            "-sample_fmt", "s16", str(probe),
        ])
        with wave.open(str(probe), "rb") as handle:
            if handle.getnchannels() >= 2:
                return 2
    return voice_channels


# ----------------------------------------------------------------------
# ffmpeg: decodifica/ricampionamento, loudnorm, ducking, codifica finale
# ----------------------------------------------------------------------


def _run_ffmpeg(args: list[str]) -> str:
    """Esegue ffmpeg. `args` NON contiene il nome dell'eseguibile: lo risolve
    qui, con `src.ffmpeg_tool`, l'unico punto del progetto che sa dove
    trovarlo (pacchetto imageio-ffmpeg, poi PATH di sistema)."""
    exe = require_ffmpeg()
    try:
        result = subprocess.run([exe, *args], capture_output=True, text=True)
    except OSError as exc:
        raise MusicMixerError(f"Impossibile eseguire ffmpeg ({exe}): {exc}") from exc
    if result.returncode != 0:
        raise MusicMixerError(
            f"ffmpeg ha fallito (exit {result.returncode}): "
            f"{result.stderr.strip()[-500:]}"
        )
    return result.stderr


def _convert(src: Path, dst: Path, rate: int, channels: int) -> None:
    """Decodifica/ricampiona/adatta i canali qualunque sia il formato sorgente."""
    _run_ffmpeg([
        "-y", "-loglevel", "error", "-i", str(src),
        "-ar", str(rate), "-ac", str(channels), "-sample_fmt", "s16", str(dst),
    ])


_LOUDNORM_KEY = '"input_i"'


def _parse_loudnorm_json(stderr: str) -> dict[str, Any]:
    """Estrae il blocco JSON che loudnorm scrive su stderr (misura o applicazione)."""
    best: dict[str, Any] | None = None
    for match in re.finditer(r"\{[^{}]*\}", stderr, re.DOTALL):
        text = match.group(0)
        if _LOUDNORM_KEY not in text:
            continue
        try:
            best = json.loads(text)
        except json.JSONDecodeError:
            continue
    if best is None:
        raise MusicMixerError(
            "Non riesco a leggere l'esito di loudnorm dall'output di ffmpeg:\n"
            + stderr[-500:]
        )
    return best


def _loudnorm(src: Path, dst: Path, target_i: float, target_tp: float, rate: int) -> bool:
    """Normalizza a due passaggi (misura poi applica), ricampionando a `rate`.

    ffmpeg elabora loudnorm internamente a 192kHz: il secondo passaggio
    incatena `aresample=<rate>` nello STESSO filtro, cosi' il file scritto e'
    gia' alla frequenza dei segmenti, non a 192kHz.

    Ritorna True se ffmpeg e' dovuto ricadere su normalizzazione 'dynamic'
    invece di 'linear' (il guadagno lineare richiesto avrebbe fatto superare
    il picco vero consentito): e' un'informazione, non un errore, e il
    chiamante la riporta nei warning.
    """
    measure_stderr = _run_ffmpeg([
        "-i", str(src),
        "-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA=11:print_format=json",
        "-f", "null", "-",
    ])
    stats = _parse_loudnorm_json(measure_stderr)

    apply_filter = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA=11:"
        f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
        f"linear=true:print_format=json,aresample={rate}"
    )
    apply_stderr = _run_ffmpeg([
        "-y", "-i", str(src), "-af", apply_filter,
        "-ar", str(rate), "-sample_fmt", "s16", str(dst),
    ])
    applied = _parse_loudnorm_json(apply_stderr)
    return str(applied.get("normalization_type", "")).lower() == "dynamic"


def _measure_integrated_loudness(path: Path) -> float:
    stderr = _run_ffmpeg([
        "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-",
    ])
    stats = _parse_loudnorm_json(stderr)
    return float(stats["input_i"])


#: sidechaincompress (verificato empiricamente su ffmpeg 7.1, filtro
#: acompressor-derivato: stesso comportamento su versioni vicine) NON esce
#: alla durata dei suoi ingressi: tronca la coda di circa 0.7-1.1s a
#: prescindere dai parametri di attack/release (bug/limite del filtro, non
#: un errore di configurazione — riprodotto anche con i parametri di
#: default). Il rimedio: si accoda silenzio a ENTRAMBI gli ingressi prima
#: del filtro, e si ritaglia l'output alla lunghezza voluta dopo. 3s di
#: margine e' abbondante rispetto alla perdita osservata.
_DUCKING_PAD_SECONDS = 3.0


def _apply_ducking(bed_path: Path, sidechain_path: Path, out_path: Path, rate: int, cfg: dict[str, Any]) -> None:
    """Compressione sidechain: il sottofondo si abbassa quando la voce parla.

    La soglia si esprime in config.yaml in dB (leggibile) ma sidechaincompress
    la vuole in ampiezza lineare 0-1: si converte qui, non nel config.
    Il sidechain e' sempre la voce GIA' normalizzata (mai quella grezza).
    """
    threshold_db = float(cfg.get("ducking_threshold_db", -22))
    threshold_lin = max(0.000976563, min(1.0, 10 ** (threshold_db / 20)))
    ratio = float(cfg.get("ducking_ratio", 2.5))
    attack = max(1, int(cfg.get("ducking_attack_ms", 20)))
    release = max(1, int(cfg.get("ducking_release_ms", 1200)))
    filt = (
        f"[0:a][1:a]sidechaincompress=threshold={threshold_lin:.6f}:ratio={ratio}:"
        f"attack={attack}:release={release}:level_sc=1[out]"
    )

    channels, bed_rate, bed_samples = _read_pcm(bed_path)
    _, _, sidechain_samples = _read_pcm(sidechain_path)
    needed_frames = len(bed_samples) // channels

    pad = _silence_samples(channels, rate, _DUCKING_PAD_SECONDS)
    tmp_dir = out_path.parent
    bed_padded = tmp_dir / f"{out_path.stem}_bed_padded.wav"
    sidechain_padded = tmp_dir / f"{out_path.stem}_sidechain_padded.wav"
    _write_pcm(bed_padded, channels, rate, bed_samples + pad)
    _write_pcm(sidechain_padded, channels, rate, sidechain_samples + pad)

    padded_out = tmp_dir / f"{out_path.stem}_padded_out.wav"
    _run_ffmpeg([
        "-y", "-loglevel", "error",
        "-i", str(bed_padded), "-i", str(sidechain_padded),
        "-filter_complex", filt, "-map", "[out]",
        "-ar", str(rate), "-sample_fmt", "s16", str(padded_out),
    ])

    _, _, ducked = _read_pcm(padded_out)
    if len(ducked) < needed_frames * channels:
        raise MusicMixerError(
            "sidechaincompress ha prodotto meno audio del previsto anche dopo il "
            "margine di silenzio: aumenta _DUCKING_PAD_SECONDS in src/music_mixer.py."
        )
    _write_pcm(out_path, channels, rate, ducked[: needed_frames * channels])


def _encode_mp3(src: Path, dst: Path, bitrate: int) -> None:
    """Codifica finale via ffmpeg. Il percorso musica richiede gia' ffmpeg per il
    mix (verificato in preflight()): non c'e' bisogno del ripiego lameenc che
    usa invece assemble() quando non c'e' nessuna musica."""
    _run_ffmpeg([
        "-y", "-loglevel", "error", "-i", str(src),
        "-codec:a", "libmp3lame", "-b:a", f"{bitrate}k", str(dst),
    ])
    if not dst.is_file():
        raise MusicMixerError("ffmpeg non ha prodotto il file mp3 finale.")


# ----------------------------------------------------------------------
# PCM in puro Python: silenzio, dissolvenze, loop con crossfade, guadagno,
# somma, concatenazione. Stesso stile di audio_assembler._trim_edges().
# ----------------------------------------------------------------------


def _read_pcm(path: Path) -> tuple[int, int, array]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        data = handle.readframes(handle.getnframes())
    samples = array("h")
    samples.frombytes(data)
    return channels, rate, samples


def _write_pcm(path: Path, channels: int, rate: int, samples: array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with wave.open(str(tmp), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())
    tmp.replace(path)


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def _silence_samples(channels: int, rate: int, seconds: float) -> array:
    n_frames = max(0, int(round(rate * seconds)))
    arr = array("h")
    arr.frombytes(bytes(2 * channels * n_frames))
    return arr


def _silence_wav(dst: Path, channels: int, rate: int, seconds: float) -> Path:
    _write_pcm(dst, channels, rate, _silence_samples(channels, rate, seconds))
    return dst


def _concat_wavs(paths: list[Path], dst: Path) -> None:
    channels = rate = None
    combined = array("h")
    for path in paths:
        ch, r, samples = _read_pcm(path)
        if channels is None:
            channels, rate = ch, r
        combined += samples
    assert channels is not None and rate is not None
    _write_pcm(dst, channels, rate, combined)


def _fade_file(
    src: Path, channels: int, rate: int, fade_in_ms: int, fade_out_ms: int,
    tmp_dir: Path, label: str,
) -> Path:
    if fade_in_ms <= 0 and fade_out_ms <= 0:
        return src
    _, _, samples = _read_pcm(src)
    if fade_in_ms > 0:
        samples = _apply_fade(samples, channels, rate, fade_in_ms, "in")
    if fade_out_ms > 0:
        samples = _apply_fade(samples, channels, rate, fade_out_ms, "out")
    out = tmp_dir / f"{label}_faded.wav"
    _write_pcm(out, channels, rate, samples)
    return out


def _clip16(value: float) -> int:
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return int(value)


def _apply_fade(samples: array, channels: int, rate: int, fade_ms: int, edge: str) -> array:
    """Dissolvenza lineare in ampiezza su fade_ms all'inizio ('in') o alla fine ('out')."""
    if fade_ms <= 0:
        return samples
    n_frames = len(samples) // channels
    fade_frames = min(n_frames, max(0, int(round(rate * fade_ms / 1000))))
    if fade_frames <= 0:
        return samples

    out = array("h", samples)
    for i in range(fade_frames):
        factor = i / fade_frames
        frame_index = i if edge == "in" else (n_frames - 1 - i)
        base = frame_index * channels
        for c in range(channels):
            out[base + c] = _clip16(out[base + c] * factor)
    return out


def _apply_gain_db(samples: array, db: float) -> array:
    if abs(db) < 1e-9:
        return samples
    factor = 10 ** (db / 20)
    return array("h", (_clip16(v * factor) for v in samples))


def _sum_clip(a: array, b: array) -> array:
    return array("h", (_clip16(x + y) for x, y in zip(a, b)))


def _mix_pcm(a_path: Path, b_path: Path, out_path: Path) -> None:
    channels, rate, a = _read_pcm(a_path)
    _, _, b = _read_pcm(b_path)
    n = min(len(a), len(b))
    mixed = _sum_clip(a[:n], b[:n])
    _write_pcm(out_path, channels, rate, mixed)


def _crossfade_pair(tail: array, head: array, frames: int, channels: int) -> array:
    n = frames * channels
    out = [0] * n
    for i in range(frames):
        fade_out = 1.0 - (i / frames)
        fade_in = i / frames
        base = i * channels
        for c in range(channels):
            idx = base + c
            out[idx] = _clip16(tail[idx] * fade_out + head[idx] * fade_in)
    return array("h", out)


def _loop_to_length(
    samples: array, channels: int, rate: int, needed_frames: int, crossfade_ms: int
) -> array:
    """Ripete il brano con dissolvenza incrociata sul punto di giunzione.

    Se il brano e' gia' lungo abbastanza, lo ritaglia e basta (nessun loop).
    Il chiamante ha gia' verificato che, se serve un loop, il brano e' almeno
    2x la dissolvenza (vedi _loop_and_fade_bed): qui si assume vero.
    """
    total_frames = len(samples) // channels
    if total_frames >= needed_frames:
        return samples[: needed_frames * channels]

    crossfade_frames = max(1, int(round(rate * crossfade_ms / 1000)))
    crossfade_frames = min(crossfade_frames, total_frames // 2)

    output = array("h", samples)
    while len(output) // channels < needed_frames:
        out_frames = len(output) // channels
        cf = min(crossfade_frames, out_frames, total_frames)
        tail_start = (out_frames - cf) * channels
        tail = output[tail_start:]
        head = samples[: cf * channels]
        blended = _crossfade_pair(tail, head, cf, channels)
        output = output[:tail_start] + blended + samples[cf * channels :]

    return output[: needed_frames * channels]


__all__ = [
    "MUSICHE_USATE_FILENAME",
    "MusicMixerError",
    "MusicChangedError",
    "ChosenMusic",
    "MixResult",
    "preflight",
    "mix_episode",
    "build_speech_section",
    "render_bed_under",
]
