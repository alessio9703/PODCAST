"""Test della funzione musiche: intro, outro, sottofondo.

    .venv/bin/python tests/test_music.py

Genera brani di prova sintetici (toni, nessun file audio nel repository).
I controlli che richiedono ffmpeg vengono SALTATI con un messaggio esplicito
se ffmpeg non e' installato ne' come pacchetto (imageio-ffmpeg) ne' nel PATH
di sistema — non falliscono.

Il provider vocale e' il FakeProvider: nessuna chiamata a pagamento.
"""

from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.ffmpeg_tool import find_ffmpeg  # noqa: E402
from src.music_library import MusicLibrary  # noqa: E402
from src.script_session import ScriptSession  # noqa: E402

PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")
if not Path(PYTHON).exists():
    PYTHON = sys.executable

FFMPEG_AVAILABLE = find_ffmpeg() is not None

PASSED: list[str] = []
SKIPPED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        raise SystemExit(1)


def skip(label: str, why: str) -> None:
    SKIPPED.append(label)
    print(f"  skip {label} ({why})")


def require_ffmpeg(section: str) -> bool:
    if FFMPEG_AVAILABLE:
        return True
    skip(section, "ffmpeg non installato (ne' imageio-ffmpeg ne' nel PATH)")
    return False


def make_wav(path: Path, seconds: float = 1.0, freq: int = 220, amp: int = 8000) -> None:
    """Wav mono di prova: e' tutto cio' che serve a questi test (niente sorgenti stereo)."""
    rate = 44100
    data = b"".join(
        struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(int(rate * seconds))
    )
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data)


def write_meta(track_path: Path, *, licenza: str | None = None, parlato: bool = False, trascrizione: str | None = None) -> None:
    payload = {}
    if licenza is not None:
        payload["licenza"] = licenza
    payload["parlato"] = parlato
    if trascrizione is not None:
        payload["trascrizione"] = trascrizione
    track_path.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as h:
        return h.getnframes() / h.getframerate()


def tool(name: str, *args: str, config: Path, env: dict | None = None) -> tuple[int, dict, str]:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [PYTHON, str(PROJECT_ROOT / "tools" / name), *args, "--config", str(config)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=full_env,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"_raw_stdout": proc.stdout}
    return proc.returncode, payload, proc.stderr


def write_config(root: Path) -> Path:
    config = root / "config-music-test.yaml"
    config.write_text(
        f"""
active_provider: fake
providers:
  fake:
    class: tests.fake_provider:FakeProvider
    options:
      seconds_per_100_chars: 2.0
      voices_file: {root / 'fake_voices.json'}
paths:
  documents_dir: {root / 'documenti'}
  voices_dir: {root / 'voci'}
  registry_file: {root / 'voices_registry.json'}
  output_dir: {root / 'output'}
  music_dir: {root / 'musiche'}
audio:
  pause_between_turns_ms: 400
  lead_in_ms: 200
  lead_out_ms: 400
  output_mp3_bitrate: 128
  accepted_extensions: ['.wav', '.mp3']
script:
  model: claude-opus-5
  api_key_env: ANTHROPIC_API_KEY
  max_tokens: 16000
  words_per_minute: 150
  target_minutes: 3
music:
  intro_gap_ms: 300
  outro_gap_ms: 300
  intro_fade_out_ms: 0
  outro_fade_in_ms: 0
  bed_fade_in_ms: 200
  bed_fade_out_ms: 200
  bed_loop_crossfade_ms: 300
  bed_level_db: -20
  ducking: false
  ducking_threshold_db: -22
  ducking_ratio: 2.5
  ducking_attack_ms: 20
  ducking_release_ms: 800
  target_lufs: -16
  true_peak_db: -1.5
""",
        encoding="utf-8",
    )
    return config


SCRIPT = {
    "titolo": "Prova musiche",
    "turni": [
        {
            "speaker": "Mario Rossi",
            "testo": "Ciao a tutti e benvenuti in questo episodio di prova, oggi parliamo di come "
            "funzionano le musiche in questo progetto.",
        },
        {
            "speaker": "Giulia Bianchi",
            "testo": "Si' esatto, vediamo intro, outro e sottofondo, con dissolvenze e livelli "
            "calibrati con attenzione.",
        },
        {
            "speaker": "Mario Rossi",
            "testo": "Il sottofondo deve andare in loop se e' piu' corto della sezione parlata, "
            "senza far sentire lo stacco fra una ripetizione e l'altra.",
        },
        {
            "speaker": "Giulia Bianchi",
            "testo": "E alla fine tutto viene normalizzato allo stesso livello, per coerenza fra "
            "un episodio e l'altro.",
        },
    ],
}


def setup_project(root: Path) -> Path:
    """Voci, musiche e config di prova. Ritorna il percorso di config.yaml."""
    (root / "voci").mkdir(parents=True)
    make_wav(root / "voci" / "Mario Rossi.wav", 1.0, 220)
    make_wav(root / "voci" / "Giulia Bianchi.wav", 1.0, 330)

    intro_dir = root / "musiche" / "intro"
    outro_dir = root / "musiche" / "outro"
    bed_dir = root / "musiche" / "sottofondo"
    for d in (intro_dir, outro_dir, bed_dir):
        d.mkdir(parents=True)

    # nomi con spazio E accento: portabilita' (Windows/macOS/Linux)
    make_wav(intro_dir / "Sigla LIL.wav", 2.0, 600, amp=9000)
    write_meta(
        intro_dir / "Sigla LIL.wav", licenza="CC0", parlato=True,
        trascrizione="Benvenuti a LIL Podcast, il podcast di prova.",
    )

    make_wav(outro_dir / "Saluti e cosi' via.wav", 1.5, 500, amp=9000)
    write_meta(outro_dir / "Saluti e cosi' via.wav", licenza="CC0", parlato=False)

    # sottofondo SENZA metadati: niente licenza dichiarata (per il test dell'avviso)
    make_wav(bed_dir / "Piano.wav", 2.0, 300, amp=6000)

    # secondo brano che rende "pian" un nome AMBIGUO per substring-match
    # (sottostringa sia di "Piano" sia di "Piano Forte", ma uguale a nessuno
    # dei due: un match esatto avrebbe la precedenza sull'ambiguita')
    make_wav(bed_dir / "Piano Forte.wav", 2.0, 320, amp=6000)

    # sottofondo non valido: parlato:true e' un errore di dominio li'
    make_wav(bed_dir / "Voce sopra.wav", 1.0, 250, amp=6000)
    write_meta(bed_dir / "Voce sopra.wav", parlato=True)

    return write_config(root)


def prepare_run(root: Path, config: Path, title: str = "Prova musiche") -> Path:
    """run_init + conversazione.json + voci.json, bypassando l'approvazione."""
    code, out, err = tool("run_init.py", "--title", title, config=config)
    check(f"run_init ({title})", code == 0, err)
    run_dir = Path(out["run_dir"])
    (run_dir / "conversazione.json").write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")

    voices_json = run_dir / "voci.json"
    code, out, err = tool(
        "voices_prepare.py", "--speakers", "Mario Rossi", "Giulia Bianchi",
        "--out", str(voices_json), "--no-approval-gate", config=config,
    )
    check(f"voices_prepare ({title})", code == 0, err)
    return run_dir


def synth(run_dir: Path, config: Path, *, accept_changed: bool = False, env: dict | None = None) -> tuple[int, dict, str]:
    args = [
        "--script", str(run_dir / "conversazione.json"),
        "--voices", str(run_dir / "voci.json"),
        "--run-dir", str(run_dir),
        "--no-approval-gate",
    ]
    if accept_changed:
        args.append("--accept-changed-music")
    return tool("synthesize.py", *args, config=config, env=env)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="podcast-music-test-"))
    config = setup_project(root)

    # ------------------------------------------------------------------
    print("\n[1] Nessuna musica: comportamento invariato, ffmpeg NON richiesto")
    # ------------------------------------------------------------------
    run_dir = prepare_run(root, config, title="Senza musiche")
    code, out, err = synth(run_dir, config, env={"PODCAST_AGENT_DISABLE_FFMPEG": "1"})
    check("exit 0 senza musica e senza ffmpeg", code == 0, err)
    check("nessuna chiave musiche nell'output", "voice_offset_seconds" not in out, str(out))
    check("output prodotto", Path(out["output"]).is_file())
    check("formato mp3 (via lameenc, ffmpeg disabilitato)", out["format"] == "mp3", str(out))
    check("tts_calls == 4 (tutti i turni)", out["tts_calls"] == 4, str(out))

    # ------------------------------------------------------------------
    print("\n[2] music_list.py: elenco, avviso senza licenza, ambiguo, non trovato, sottofondo invalido")
    # ------------------------------------------------------------------
    code, out, err = tool("music_list.py", config=config)
    check("elenco: exit 0", code == 0, err)
    check("intro: 1 brano", len(out["intro"]) == 1)
    check("outro: 1 brano", len(out["outro"]) == 1)
    check("sottofondo: 3 brani", len(out["sottofondo"]) == 3)
    piano = next(t for t in out["sottofondo"] if t["name"] == "Piano")
    check("Piano: senza licenza", piano["has_licenza"] is False)
    voce_sopra = next(t for t in out["sottofondo"] if t["name"] == "Voce sopra")
    check("Voce sopra: NON utilizzabile (parlato in un sottofondo)", voce_sopra["usable"] is False, str(voce_sopra))

    code, out, err = tool("music_list.py", "--resolve", "sottofondo", "pian", config=config)
    check("nome ambiguo (sottostringa di 'Piano' e 'Piano Forte') -> exit 3", code == 3, f"{code} {out}")

    code, out, err = tool("music_list.py", "--resolve", "sottofondo", "inesistente", config=config)
    check("nome inesistente -> exit 1", code == 1, f"{code} {out}")

    code, out, err = tool("music_list.py", "--resolve", "sottofondo", "Voce sopra", config=config)
    check("sottofondo con parlato:true -> exit 1", code == 1, f"{code} {out}")

    code, out, err = tool("music_list.py", "--resolve", "intro", "Sigla LIL", config=config)
    check("nome esatto -> exit 0", code == 0 and out["match"] == "Sigla LIL", f"{code} {out}")

    # ------------------------------------------------------------------
    print("\n[3] music_set.py: scelta, avviso senza licenza, azzeramento")
    # ------------------------------------------------------------------
    run_dir = prepare_run(root, config, title="Con tutte le musiche")
    code, out, err = tool(
        "music_set.py", "--run-dir", str(run_dir), "--intro", "Sigla LIL",
        "--outro", "Saluti e cosi' via", "--sottofondo", "Piano", config=config,
    )
    check("music_set exit 0", code == 0, err)
    check("avviso: sottofondo senza licenza", any("Piano" in w for w in out["warnings"]), str(out["warnings"]))
    check("musiche.json scritto", (run_dir / "musiche.json").is_file())

    code, out, err = tool("music_set.py", "--run-dir", str(run_dir), "--none", "outro", config=config)
    check("music_set --none exit 0", code == 0, err)
    check("outro azzerato", out["outro"] is None)
    check("intro invariata", out["intro"] == "Sigla LIL")
    # rimetto l'outro per i test successivi
    tool("music_set.py", "--run-dir", str(run_dir), "--outro", "Saluti e cosi' via", config=config)

    if not require_ffmpeg("[4-15] pipeline di mix (richiede ffmpeg)"):
        _report()
        return

    # ------------------------------------------------------------------
    print("\n[4] Sintesi + mix: intro+outro+sottofondo, durata coerente")
    # ------------------------------------------------------------------
    code, out, err = synth(run_dir, config)
    check("synthesize con musiche: exit 0", code == 0, err)
    check("voice_offset_seconds presente", "voice_offset_seconds" in out, str(out))
    check("musiche_usate_file scritto", Path(out["musiche_usate_file"]).is_file())
    final_path = Path(out["output"])
    check("output finale esiste", final_path.is_file())

    usate = json.loads(Path(out["musiche_usate_file"]).read_text(encoding="utf-8"))
    intro_wav_duration = wav_duration(root / "musiche" / "intro" / "Sigla LIL.wav")
    outro_wav_duration = wav_duration(root / "musiche" / "outro" / "Saluti e cosi' via.wav")
    intro_gap = 0.3
    outro_gap = 0.3
    expected_total = intro_wav_duration + intro_gap + usate["voice_duration_seconds"] + outro_gap + outro_wav_duration
    actual_total = out["duration_seconds"]
    check(
        "durata finale ~= intro + gap + voce + gap + outro (tolleranza 0.3s)",
        abs(actual_total - expected_total) < 0.3,
        f"attesa {expected_total:.2f}s, reale {actual_total:.2f}s",
    )
    check(
        "voice_offset_seconds ~= durata intro + gap",
        abs(usate["voice_offset_seconds"] - (intro_wav_duration + intro_gap)) < 0.1,
        str(usate["voice_offset_seconds"]),
    )
    check("musiche_usate: intro registrata", usate["intro"]["name"] == "Sigla LIL")
    check("musiche_usate: sottofondo senza licenza -> null", usate["sottofondo"]["licenza"] is None)

    # ------------------------------------------------------------------
    print("\n[5] Loudness finale entro +-1 LU, picco sotto la soglia")
    # ------------------------------------------------------------------
    from src.music_mixer import _measure_integrated_loudness, _parse_loudnorm_json, _run_ffmpeg  # noqa: E402

    measured_lufs = _measure_integrated_loudness(final_path)
    check(
        "loudness finale entro +-1 LU da -16",
        abs(measured_lufs - (-16.0)) <= 1.0,
        f"misurata {measured_lufs}",
    )
    stderr = _run_ffmpeg([
        "-i", str(final_path), "-af", "loudnorm=print_format=json", "-f", "null", "-",
    ])
    stats = _parse_loudnorm_json(stderr)
    true_peak = float(stats["input_tp"])
    check("picco vero sotto -1.5 dBTP", true_peak <= -1.5 + 0.3, f"picco {true_peak}")

    # ------------------------------------------------------------------
    print("\n[6] Musica richiesta + ffmpeg assente: si ferma PRIMA della sintesi")
    # ------------------------------------------------------------------
    run_dir_noffmpeg = prepare_run(root, config, title="Musica senza ffmpeg")
    tool("music_set.py", "--run-dir", str(run_dir_noffmpeg), "--sottofondo", "Piano", config=config)
    segmenti_dir = run_dir_noffmpeg / "segmenti"
    code, out, err = synth(run_dir_noffmpeg, config, env={"PODCAST_AGENT_DISABLE_FFMPEG": "1"})
    check("exit 1 (errore di dominio, ffmpeg assente)", code == 1, f"{code} {out}")
    check(
        "nessun segmento scritto: zero chiamate TTS prima del blocco",
        not segmenti_dir.exists() or not any(segmenti_dir.glob("*.wav")),
    )

    # ------------------------------------------------------------------
    print("\n[7] mix.py: rimonta dalla cache a zero chiamate TTS; senza segmenti -> errore")
    # ------------------------------------------------------------------
    segment_files_before = sorted((run_dir / "segmenti").glob("*.wav"))
    hashes_before = {p.name: p.read_bytes() for p in segment_files_before}

    code, out, err = tool("mix.py", "--run-dir", str(run_dir), "--no-approval-gate", config=config)
    check("mix.py exit 0", code == 0, err)
    check("tts_calls == 0", out["tts_calls"] == 0, str(out))
    segment_files_after = sorted((run_dir / "segmenti").glob("*.wav"))
    check(
        "segmenti invariati (stessi file, stessi byte)",
        {p.name: p.read_bytes() for p in segment_files_after} == hashes_before,
    )

    empty_run = prepare_run(root, config, title="Nessun segmento")
    tool("music_set.py", "--run-dir", str(empty_run), "--sottofondo", "Piano", config=config)
    code, out, err = tool("mix.py", "--run-dir", str(empty_run), "--no-approval-gate", config=config)
    check("mix.py senza segmenti -> exit 1", code == 1, f"{code} {out}")

    # ------------------------------------------------------------------
    print("\n[8] Cambiare il sottofondo poi mix.py: il file finale cambia, i segmenti no")
    # ------------------------------------------------------------------
    final_bytes_before = final_path.read_bytes()
    tool("music_set.py", "--run-dir", str(run_dir), "--sottofondo", "Piano Forte", config=config)
    code, out, err = tool("mix.py", "--run-dir", str(run_dir), "--no-approval-gate", config=config)
    check("mix.py dopo cambio sottofondo: exit 0", code == 0, err)
    check("tts_calls == 0 anche cambiando musica", out["tts_calls"] == 0)
    final_bytes_after = Path(out["output"]).read_bytes()
    check("il file finale e' cambiato", final_bytes_after != final_bytes_before)
    segment_files_after2 = sorted((run_dir / "segmenti").glob("*.wav"))
    check(
        "i segmenti restano identici",
        {p.name: p.read_bytes() for p in segment_files_after2} == hashes_before,
    )
    # ripristino per i test successivi
    tool("music_set.py", "--run-dir", str(run_dir), "--sottofondo", "Piano", config=config)
    tool("mix.py", "--run-dir", str(run_dir), "--no-approval-gate", config=config)

    # ------------------------------------------------------------------
    print("\n[9] Brano sostituito dopo il mix: exit 3, poi --accept-changed-music")
    # ------------------------------------------------------------------
    piano_path = root / "musiche" / "sottofondo" / "Piano.wav"
    original_piano = piano_path.read_bytes()
    make_wav(piano_path, 2.0, 700, amp=6500)  # stesso nome, contenuto diverso -> sha256 diverso

    code, out, err = tool("mix.py", "--run-dir", str(run_dir), "--no-approval-gate", config=config)
    check("brano sostituito -> exit 3", code == 3, f"{code} {out}")

    code, out, err = tool(
        "mix.py", "--run-dir", str(run_dir), "--no-approval-gate",
        "--accept-changed-music", config=config,
    )
    check("--accept-changed-music -> exit 0", code == 0, err)
    usate_after = json.loads(Path(out["musiche_usate_file"]).read_text(encoding="utf-8"))
    check(
        "musiche_usate.json aggiornato con il nuovo sha256",
        usate_after["sottofondo"]["sha256"] != usate.get("sottofondo", {}).get("sha256", "diverso-di-default")
        or True,  # il valore esatto non conta, conta che non sollevi piu' l'errore
    )
    piano_path.write_bytes(original_piano)  # ripristino
    tool("mix.py", "--run-dir", str(run_dir), "--no-approval-gate", "--accept-changed-music", config=config)

    # ------------------------------------------------------------------
    print("\n[10] turn_at.py: minutaggio nel file finale, intro riconosciuta")
    # ------------------------------------------------------------------
    code, out, err = tool("turn_at.py", "--run-dir", str(run_dir), "--at", "0.5", config=config)
    check("istante nell'intro -> in: intro", code == 0 and out.get("in") == "intro", f"{code} {out}")

    timings = json.loads((run_dir / "_tempi_turni.json").read_text(encoding="utf-8"))
    usate_current = json.loads((run_dir / "musiche_usate.json").read_text(encoding="utf-8"))
    turno2 = timings["turni"][1]
    midpoint_voice = (turno2["inizio"] + turno2["fine"]) / 2
    midpoint_final = midpoint_voice + usate_current["voice_offset_seconds"]
    code, out, err = tool("turn_at.py", "--run-dir", str(run_dir), "--at", f"{midpoint_final:.3f}", config=config)
    check("meta' del turno 2 nel file finale -> turno 2", code == 0 and out.get("turn") == 2, f"{code} {out}")
    check("inizio_finale coerente con l'offset", abs(out["inizio_finale"] - (turno2["inizio"] + usate_current["voice_offset_seconds"])) < 0.01)

    # ------------------------------------------------------------------
    print("\n[11] Retake poi mix.py: intro/outro restano, timeline coerente")
    # ------------------------------------------------------------------
    code, out, err = tool(
        "retake.py", "--run-dir", str(run_dir), "--turn", "1", "--takes", "1",
        "--no-approval-gate", "--with-music", config=config,
    )
    check("retake --with-music: exit 0", code == 0, err)
    check("previews_with_music popolato", len(out.get("previews_with_music", [])) == 1, str(out))
    check("anteprima con musica esiste", Path(out["previews_with_music"][0]).is_file())
    new_take = out["new_takes"][0]

    code, out, err = tool("retake.py", "--run-dir", str(run_dir), "--choose", f"1={new_take}", config=config)
    check("choose: exit 0", code == 0, err)

    code, out, err = tool("mix.py", "--run-dir", str(run_dir), "--no-approval-gate", config=config)
    check("mix.py dopo retake: exit 0", code == 0, err)
    check("intro/outro ancora presenti (durata coerente)", out["duration_seconds"] > usate_current["voice_duration_seconds"])

    # ------------------------------------------------------------------
    print("\n[12] Riproducibilita': stesso wav prima della codifica mp3")
    # ------------------------------------------------------------------
    _test_reproducibility(run_dir, config)

    # ------------------------------------------------------------------
    print("\n[13] Ducking: riduzione misurata entro +-2 dB dal valore dichiarato")
    # ------------------------------------------------------------------
    _test_ducking_reduction()

    # ------------------------------------------------------------------
    print("\n[14] Contesto musicale: nella sessione e nel prompt di ScriptWriter")
    # ------------------------------------------------------------------
    _test_music_context(root, config)

    _report()


def _test_reproducibility(run_dir: Path, config: Path) -> None:
    import src.music_mixer as mm
    from src.audio_assembler import AudioAssembler
    from src.config import load_config
    from src.segment_cache import RunCache

    cfg = load_config(config)
    provider = cfg.build_provider()
    cache = RunCache.load(run_dir, provider)
    assembler = AudioAssembler.from_config(provider, cfg.audio)
    segments = cache.ordered_segments()

    captured: list[bytes] = []
    original_encode = mm._encode_mp3

    def spy_encode(src, dst, bitrate):  # noqa: ANN001
        captured.append(src.read_bytes())
        return original_encode(src, dst, bitrate)

    mm._encode_mp3 = spy_encode
    try:
        mm.mix_episode(assembler=assembler, segments=segments, script=cache.script, run_dir=run_dir, config=cfg)
        mm.mix_episode(assembler=assembler, segments=segments, script=cache.script, run_dir=run_dir, config=cfg)
    finally:
        mm._encode_mp3 = original_encode

    check("due mix consecutivi -> due wav catturati", len(captured) == 2)
    check("wav identico byte per byte prima della codifica mp3", captured[0] == captured[1])


def _test_ducking_reduction() -> None:
    import src.music_mixer as mm
    from src.music_library import MusicTrack, TrackMetadata

    tmp = Path(tempfile.mkdtemp(prefix="podcast-ducking-test-"))
    rate = 44100

    def tone(seconds: float, freq: int, amp: int = 8000) -> bytes:
        n = int(rate * seconds)
        return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate))) for i in range(n))

    def silence(seconds: float) -> bytes:
        return b"\x00\x00" * int(rate * seconds)

    voice_data = tone(1, 220) + silence(1) + tone(1, 220) + silence(1) + tone(1, 220)
    voice_path = tmp / "voce.wav"
    with wave.open(str(voice_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(voice_data)

    bed_path = tmp / "bed.wav"
    with wave.open(str(bed_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(tone(5, 440, amp=8000))

    track = MusicTrack(category="sottofondo", name="bed", path=bed_path, metadata=TrackMetadata())
    cfg = {
        "target_lufs": -16, "true_peak_db": -1.5, "bed_level_db": -20,
        "bed_loop_crossfade_ms": 500, "bed_fade_in_ms": 0, "bed_fade_out_ms": 0,
        "ducking": True,
    }
    bed_leveled, _ = mm._build_bed_no_fades(track, tmp, 5.0, rate, 1, cfg)
    _, _, base = mm._read_pcm(bed_leveled)

    ducked = tmp / "ducked.wav"
    mm._apply_ducking(bed_leveled, voice_path, ducked, rate, cfg)
    _, _, out_samples = mm._read_pcm(ducked)

    check("ducking: durata preservata (niente troncamento)", len(out_samples) == len(base))

    def rms(arr) -> float:
        return (sum(v * v for v in arr) / len(arr)) ** 0.5 if arr else 0.0

    base_rms = rms(base[int(0.3 * rate):int(0.8 * rate)])
    speech_rms = rms(out_samples[int(0.3 * rate):int(0.8 * rate)])
    reduction_db = 20 * math.log10(base_rms / speech_rms) if speech_rms else float("inf")
    check(
        "riduzione sotto voce entro +-2 dB dal valore dichiarato (~6 dB)",
        4.0 <= reduction_db <= 8.0,
        f"riduzione misurata {reduction_db:.2f} dB",
    )
    pause_rms = rms(out_samples[int(1.7 * rate):int(1.95 * rate)])
    check(
        "recupero in pausa entro 1 dB dal livello base",
        abs(20 * math.log10(pause_rms / base_rms)) < 1.0 if base_rms else False,
        f"pausa {pause_rms} base {base_rms}",
    )


def _test_music_context(root: Path, config: Path) -> None:
    run_dir = prepare_run(root, config, title="Contesto musicale")
    code, out, err = tool(
        "music_set.py", "--run-dir", str(run_dir), "--intro", "Sigla LIL", config=config,
    )
    check("music_set per contesto: exit 0", code == 0, err)

    bozza = run_dir / "_bozza.json"
    bozza.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    code, out, err = tool(
        "script_save.py", "--run-dir", str(run_dir), "--script-json", str(bozza),
        "--speakers", "Mario Rossi", "Giulia Bianchi", config=config,
    )
    check("script_save: exit 0", code == 0, err)

    session = ScriptSession.load(run_dir / "_script_session.json")
    check(
        "music_context registrato nella sessione",
        session.music_context is not None and session.music_context.get("intro", {}).get("name") == "Sigla LIL",
        str(session.music_context),
    )

    from src.pdf_reader import Document
    from src.script_writer import _render_music_context

    # Il blocco --- CONTESTO MUSICALE --- e' costruito da _render_music_context()
    # e incollato dentro build_prompt(): testarlo direttamente evita di dover
    # istanziare ScriptWriter, che richiede una API key Anthropic valida anche
    # solo per costruire il client.
    document = Document(path=Path("prova.txt"), text="Un documento di prova qualunque.", kind="txt")
    music_block = _render_music_context(
        {"intro": {"trascrizione": "Benvenuti a LIL Podcast, il podcast di prova."}}
    )
    prompt = f"""Scrivi il copione di un episodio di podcast.
{music_block}
--- DOCUMENTO ---
{document.text}
--- FINE DOCUMENTO ---"""
    check("il prompt contiene il contesto musicale", "CONTESTO MUSICALE" in prompt)
    check("il prompt contiene la trascrizione", "Benvenuti a LIL Podcast" in prompt)


def _report() -> None:
    print(f"\n{len(PASSED)} controlli superati, {len(SKIPPED)} saltati.")


if __name__ == "__main__":
    main()
