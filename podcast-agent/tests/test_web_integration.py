"""Test dell'integrazione web: cio' che serve all'adattatore che chiama tools/*.py.

    .venv/bin/python tests/test_web_integration.py

Stesso stile di tests/test_tools_cli.py: exit code e chiavi JSON dei tool,
provati come sottoprocessi, piu' qualche verifica diretta dei moduli di src/.
Nessuna chiamata reale: il provider vocale e' tests/fake_provider.py, il
modello che scrive il copione e' tests/fake_anthropic.py.

Le sezioni seguono le fasi del piano (C0 trasversale, C1 copione, ...).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.fileio import LockTimeout, file_lock, is_locked, write_json_atomic  # noqa: E402
from tests.fake_anthropic import queue_responses, read_log  # noqa: E402
from tests.test_tools_cli import PYTHON, check, make_wav  # noqa: E402

SKIPPED: list[str] = []


# ----------------------------------------------------------------------
# Infrastruttura
# ----------------------------------------------------------------------


def clean_env() -> dict[str, str]:
    """Ambiente senza chiavi API: se un tool provasse a chiamare un servizio
    vero fallirebbe invece di spendere."""
    env = dict(os.environ)
    for key in ("ANTHROPIC_API_KEY", "FISH_AUDIO_API_KEY"):
        env.pop(key, None)
    return env


def tool(name: str, *args: str, config: Path) -> tuple[int, dict, str]:
    proc = subprocess.run(
        [PYTHON, str(PROJECT_ROOT / "tools" / name), *args, "--config", str(config)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=clean_env(),
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"_raw_stdout": proc.stdout}
    return proc.returncode, payload, proc.stderr


def run_python(code: str, *, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=clean_env(),
        timeout=timeout,
    )


def write_config(root: Path) -> Path:
    config = root / "config-web.yaml"
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
  accepted_extensions: ['.wav', '.mp3']
script:
  model: claude-opus-5
  api_key_env: ANTHROPIC_API_KEY
  max_tokens: 16000
  words_per_minute: 150
  target_minutes: 3
  client_factory: tests.fake_anthropic:build
  client_options:
    responses_file: {root / 'anthropic_risposte.json'}
    log_file: {root / 'anthropic_richieste.jsonl'}
""",
        encoding="utf-8",
    )
    return config


SCRIPT = {
    "titolo": "Il trimestre in tre minuti",
    "turni": [
        {"speaker": "Mario Rossi", "testo": "Ciao a tutti e benvenuti."},
        {"speaker": "Giulia Bianchi", "testo": "Oggi parliamo del report trimestrale."},
        {"speaker": "Mario Rossi", "testo": "I ricavi sono cresciuti del dodici per cento."},
    ],
}


def setup(root: Path) -> Path:
    (root / "voci").mkdir()
    make_wav(root / "voci" / "Mario Rossi.wav", 1.0, 220)
    make_wav(root / "voci" / "Giulia Bianchi.wav", 1.0, 330)
    make_wav(root / "voci" / "Anna Neri.wav", 1.0, 260)
    (root / "documenti").mkdir()
    (root / "documenti" / "report.txt").write_text(
        "I ricavi del trimestre sono cresciuti del dodici per cento.", encoding="utf-8"
    )
    (root / "musiche" / "intro").mkdir(parents=True)
    make_wav(root / "musiche" / "intro" / "Piano calmo.wav", 0.5, 440)
    make_wav(root / "musiche" / "intro" / "Piano forte.wav", 0.5, 550)
    (root / "output").mkdir()
    return write_config(root)


# ----------------------------------------------------------------------
# C0 · trasversale
# ----------------------------------------------------------------------


def test_atomic_write(root: Path) -> None:
    print("\n[C0] scrittura atomica")
    target = root / "atomico" / "dati.json"
    write_json_atomic(target, {"a": 1})
    write_json_atomic(target, {"a": 2})
    check("il file contiene l'ultima scrittura", json.loads(target.read_text())["a"] == 2)
    leftovers = [p.name for p in target.parent.iterdir() if p.name != "dati.json"]
    check("nessun temporaneo lasciato in giro", leftovers == [], str(leftovers))


def test_lock_timeout(root: Path) -> None:
    print("\n[C0] lock su file")
    target = root / "condiviso.json"
    check("libero prima di prenderlo", not is_locked(target))
    with file_lock(target):
        check("is_locked lo vede preso", is_locked(target))
        proc = run_python(
            f"""
            from src.fileio import file_lock, LockTimeout
            try:
                with file_lock({str(target)!r}, timeout=0.3):
                    print("PRESO")
            except LockTimeout as exc:
                print("TIMEOUT", exc.kind, exc.details["timeout_seconds"])
            """
        )
        check(
            "un altro processo va in LockTimeout con error_type stabile",
            proc.stdout.strip() == "TIMEOUT LockTimeout 0.3",
            proc.stdout + proc.stderr,
        )
    check("libero dopo il rilascio", not is_locked(target))
    try:
        with file_lock(target, timeout=0):
            pass
        ok = True
    except LockTimeout:
        ok = False
    check("dopo il rilascio si riprende subito", ok)


def test_registry_concurrency(root: Path) -> None:
    """Il caso reale: piu' processi clonano voci diverse nello stesso momento.

    Ogni processo carica il registro ALL'INIZIO (come voices_prepare.py) e poi
    registra le sue voci a distanza di tempo. Senza la rilettura sotto lock
    l'ultimo a scrivere cancellerebbe le voci degli altri.
    """
    print("\n[C0] registro delle voci con piu' processi")
    registry_file = root / "concorrenza" / "voices_registry.json"
    registry_file.parent.mkdir()
    audio = root / "concorrenza" / "campione.wav"
    make_wav(audio, 0.2, 300)

    workers, per_worker = 6, 5
    code = f"""
        import sys, time
        from src.registry import VoiceRegistry
        worker = int(sys.argv[1])
        registry = VoiceRegistry({str(registry_file)!r})
        time.sleep(0.2)  # tutti hanno gia' caricato un registro vecchio
        for i in range({per_worker}):
            registry.record(f"Voce {{worker}}-{{i}}", {str(audio)!r}, "fake", f"id-{{worker}}-{{i}}")
            time.sleep(0.01)
    """
    procs = [
        subprocess.Popen(
            [PYTHON, "-c", textwrap.dedent(code), str(w)],
            cwd=str(PROJECT_ROOT),
            env=clean_env(),
            stderr=subprocess.PIPE,
            text=True,
        )
        for w in range(workers)
    ]
    errors = [p.communicate(timeout=60)[1] for p in procs]
    check("tutti i processi finiscono senza errori", all(p.returncode == 0 for p in procs), "\n".join(errors))

    data = json.loads(registry_file.read_text(encoding="utf-8"))
    check(
        f"nessuna voce persa ({workers * per_worker} attese)",
        len(data) == workers * per_worker,
        f"trovate {len(data)}",
    )
    check(
        "ogni voce ha il suo voice_id",
        all(data[f"Voce {w}-{i}"]["voice_id"] == f"id-{w}-{i}" for w in range(workers) for i in range(per_worker)),
    )
    stray = [p.name for p in registry_file.parent.iterdir() if p.name.endswith(".tmp")]
    check("nessun temporaneo rimasto", stray == [], str(stray))


def test_structured_needs_user(root: Path, config: Path) -> None:
    print("\n[C0] esito 3 strutturato (nome ambiguo)")
    code, out, _ = tool("music_list.py", "--resolve", "intro", "piano", config=config)
    check("music_list: nome ambiguo -> exit 3", code == 3, str(out))
    check("error_type stabile AmbiguousName", out.get("error_type") == "AmbiguousName", str(out))
    details = out.get("details") or {}
    check(
        "details porta i candidati",
        sorted(details.get("candidates", [])) == ["Piano calmo", "Piano forte"],
        str(details),
    )
    check("details porta categoria e richiesta", details.get("category") == "intro" and details.get("query") == "piano")

    run_dir = root / "output" / "run-ambigua"
    run_dir.mkdir()
    code, out, _ = tool("music_set.py", "--run-dir", str(run_dir), "--intro", "piano", config=config)
    check("music_set: nome ambiguo -> exit 3 AmbiguousName", code == 3 and out.get("error_type") == "AmbiguousName", str(out))
    check("music_set: nessuna scelta scritta", not (run_dir / "musiche.json").exists())

    code, out, _ = tool("music_list.py", "--resolve", "intro", "inesistente", config=config)
    check("nome inesistente resta exit 1 senza details", code == 1 and "details" not in out, str(out))


def test_interrupt_contract() -> None:
    print("\n[C0] interruzione: sempre un JSON su stdout")
    for sig in ("SIGTERM", "SIGINT"):
        proc = run_python(
            f"""
            import os, signal, sys, time
            sys.path.insert(0, "tools")
            from _common import run_tool

            def main():
                os.kill(os.getpid(), signal.{sig})
                time.sleep(10)

            run_tool(main)
            """,
            timeout=30,
        )
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            out = {"_raw": proc.stdout}
        check(f"{sig}: exit 1", proc.returncode == 1, f"rc={proc.returncode} {proc.stderr}")
        check(f"{sig}: error_type Interrupted", out.get("error_type") == "Interrupted", str(out))


def test_fake_anthropic(root: Path, config: Path) -> None:
    print("\n[C0] scrittura del copione con il client finto")
    responses = root / "anthropic_risposte.json"
    log = root / "anthropic_richieste.jsonl"

    code, out, err = tool("run_init.py", "--title", "Prova client finto", config=config)
    check("run_init ok", code == 0, err)
    run_dir = out["run_dir"]

    queue_responses(responses, {"json": SCRIPT})
    code, out, err = tool(
        "script_write.py",
        "--document", str(root / "documenti" / "report.txt"),
        "--speakers", "Mario Rossi", "Giulia Bianchi",
        "--run-dir", run_dir,
        config=config,
    )
    check("script_write senza ANTHROPIC_API_KEY: exit 0", code == 0, err + str(out))
    check("il copione e' quello del client finto", out.get("turni") == SCRIPT["turni"], str(out))
    calls = read_log(log)
    check("una sola chiamata al modello", len(calls) == 1, str(len(calls)))
    check(
        "il documento e' nel prompt inviato",
        "dodici per cento" in calls[0]["messages"][0]["content"],
    )

    revised = {**SCRIPT, "turni": SCRIPT["turni"][:2]}
    queue_responses(responses, {"json": revised})
    code, out, err = tool(
        "script_revise.py",
        "--session", str(Path(run_dir) / "_script_session.json"),
        "--instructions", "Togli l'ultima battuta",
        config=config,
    )
    check("script_revise: exit 0", code == 0, err + str(out))
    check("la revisione e' salvata", out.get("turn_count") == 2, str(out))
    calls = read_log(log)
    check("la revisione ha inviato l'istruzione", "Togli l'ultima battuta" in calls[-1]["messages"][-1]["content"])

    queue_responses(responses, {"stop_reason": "refusal"})
    code, out, _ = tool(
        "script_revise.py",
        "--session", str(Path(run_dir) / "_script_session.json"),
        "--instructions", "Riscrivi tutto",
        config=config,
    )
    check("rifiuto del modello -> exit 1 ScriptWriterError", code == 1 and out.get("error_type") == "ScriptWriterError", str(out))

    queue_responses(responses, {"text": "non e' json"})
    code, out, _ = tool(
        "script_revise.py",
        "--session", str(Path(run_dir) / "_script_session.json"),
        "--instructions", "Riscrivi tutto",
        config=config,
    )
    check("risposta non JSON -> exit 1 ScriptWriterError", code == 1 and out.get("error_type") == "ScriptWriterError", str(out))
    session = json.loads((Path(run_dir) / "_script_session.json").read_text(encoding="utf-8"))
    check("le risposte fallite non creano versioni", len(session["versions"]) == 2, str(len(session["versions"])))


# ----------------------------------------------------------------------
# C1 · copione (passi 1-4 e Confronto)
# ----------------------------------------------------------------------

PRESENTER, INTERVIEWER, GUEST = "Anna Neri", "Mario Rossi", "Giulia Bianchi"

INTERVISTA = {
    "titolo": "Il trimestre in tre minuti",
    "turni": [
        {"speaker": PRESENTER, "testo": "[caldo] Benvenuti. Oggi con noi Mario Rossi, giornalista economico, e Giulia Bianchi, analista finanziaria."},
        {"speaker": INTERVIEWER, "testo": "[curioso] Giulia, cosa dice il report sui ricavi del trimestre?"},
        {"speaker": GUEST, "testo": "Che sono cresciuti, e in modo netto rispetto all'anno scorso."},
        {"speaker": INTERVIEWER, "testo": "E perché è successo, secondo te?"},
        {"speaker": GUEST, "testo": "[enfasi] Soprattutto per le vendite all'estero, che hanno trainato tutto."},
    ],
}
#: Stesso copione, ma il presentatore non nomina l'ospite: errore BLOCCANTE.
INTERVISTA_SENZA_OSPITE = {
    "titolo": INTERVISTA["titolo"],
    "turni": [
        {"speaker": PRESENTER, "testo": "[caldo] Benvenuti. Oggi con noi Mario Rossi, giornalista economico."},
        *INTERVISTA["turni"][1:],
    ],
}


def make_blank_pdf(path: Path, password: str | None = None) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if password:
        writer.encrypt(password)
    with path.open("wb") as handle:
        writer.write(handle)


def test_doc_extract(root: Path, config: Path) -> None:
    print("\n[C1] doc_extract: esiti strutturati")
    docs = root / "documenti"
    code, out, err = tool("doc_extract.py", "report.txt", config=config)
    check("testo: exit 0", code == 0, err)
    check("nome, sha256, dimensione, parole", out.get("name") == "report.txt" and out["sha256"].startswith("sha256:") and out["size_bytes"] > 0 and out["words"] == 10, str(out))
    check("pages null per un testo", out.get("pages") is None)

    make_blank_pdf(docs / "scansione.pdf")
    code, out, _ = tool("doc_extract.py", "scansione.pdf", config=config)
    check("PDF senza testo -> exit 1 DocumentNotExtractable", code == 1 and out.get("error_type") == "DocumentNotExtractable", str(out))
    check("motivo: scansione", (out.get("details") or {}).get("motivo") == "scansione", str(out))

    make_blank_pdf(docs / "protetto.pdf", password="segreta")
    code, out, _ = tool("doc_extract.py", "protetto.pdf", config=config)
    check("PDF protetto -> motivo protetto", code == 1 and (out.get("details") or {}).get("motivo") == "protetto", str(out))

    (docs / "foglio.xyz").write_text("x", encoding="utf-8")
    code, out, _ = tool("doc_extract.py", "foglio.xyz", config=config)
    check("estensione non gestita -> UnsupportedDocument", code == 1 and out.get("error_type") == "UnsupportedDocument" and ".pdf" in out["details"]["ammesse"], str(out))


def test_media_duration(root: Path) -> None:
    print("\n[C1] durata degli mp3 senza ffprobe (G4)")
    import imageio_ffmpeg

    wav = root / "durata.wav"
    mp3 = root / "durata.mp3"
    make_wav(wav, 2.0, 300)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(wav), str(mp3)], check=True)
    proc = run_python(
        f"""
        import os, shutil
        os.environ["PATH"] = "/usr/bin:/bin"   # niente ffprobe di sistema
        assert shutil.which("ffprobe") is None
        from src.ffmpeg_tool import media_duration_seconds
        print(round(media_duration_seconds({str(mp3)!r}), 1))
        """
    )
    check("mp3 misurato con il solo ffmpeg di imageio", proc.stdout.strip() in ("2.0", "2.1"), proc.stdout + proc.stderr)


def episode(run_dir: str, config: Path, *args: str) -> tuple[int, dict, str]:
    return tool("episode_config.py", "--run-dir", run_dir, *args, config=config)


def test_run_init_and_episode(root: Path, config: Path) -> str:
    print("\n[C1] passo 1: run_init con documento")
    before = set((root / "output").iterdir())
    code, out, _ = tool("run_init.py", "--title", "Scansione", "--document", "scansione.pdf", config=config)
    check("documento senza testo: exit 1 e nessuna bozza creata", code == 1 and set((root / "output").iterdir()) == before, str(out))

    code, out, err = tool("run_init.py", "--title", "Il trimestre", "--document", "report.txt", config=config)
    check("run_init con documento: exit 0", code == 0, err)
    run_dir = out["run_dir"]
    ep = out["episodio"]
    check("titolo e documento registrati", ep["titolo"] == "Il trimestre" and ep["documento"]["nome"] == "report.txt", str(ep))
    check("documento con sha256 e parole", ep["documento"]["sha256"].startswith("sha256:") and ep["documento"]["parole"] == 10)
    check("testo estratto salvato nella run", (Path(run_dir) / "documento_estratto.txt").is_file())
    check("mancano formato (e quindi il cast)", {"campo": "formato"} in ep["mancanti"] and not ep["cast_completo"], str(ep["mancanti"]))

    print("\n[C1] passo 2: cast e formato")
    code, out, _ = episode(run_dir, config, "--voce", f"presentatore={PRESENTER}")
    check("ruolo prima del formato -> exit 1", code == 1 and (out.get("details") or {}).get("campo") == "formato", str(out))

    for bad in ("abc", "5:00"):
        code, out, _ = episode(run_dir, config, "--durata", bad)
        check(f"durata '{bad}' rifiutata (exit 1)", code == 1 and out["details"]["campo"] == "durata", str(out))

    code, out, err = episode(run_dir, config, "--formato", "intervista", "--durata", "0:15")
    check("formato e durata: exit 0", code == 0 and out["formato"] == "intervista" and out["durata_minuti"] == 15, err + str(out))
    check("ruoli dell'intervista, nell'ordine", [r["ruolo"] for r in out["ruoli"]] == ["presentatore", "intervistatore", "ospite"], str(out["ruoli"]))

    code, out, _ = episode(run_dir, config, "--voce", "intervistatore=mario")
    check("voce scritta in modo incerto -> exit 3 AmbiguousName", code == 3 and out.get("error_type") == "AmbiguousName", str(out))
    check("candidati e ruolo in details", out["details"]["candidates"] == [INTERVIEWER] and out["details"]["ruolo"] == "intervistatore", str(out))

    code, out, _ = episode(run_dir, config, "--voce", f"presentatore={PRESENTER}", "--voce", f"intervistatore={PRESENTER}")
    check("stessa voce su due ruoli -> exit 1", code == 1 and out["details"]["gia_in"] == "presentatore", str(out))
    code, out, _ = episode(run_dir, config)
    check("...e niente e' stato salvato (tutto o niente)", all(r["voce"] is None for r in out["ruoli"]), str(out["ruoli"]))

    code, out, _ = episode(
        run_dir, config,
        "--voce", f"presentatore={PRESENTER}", "--voce", f"intervistatore={INTERVIEWER}", "--voce", f"ospite={GUEST}",
        "--nome", "intervistatore=Mario",
    )
    check("nome senza cognome -> exit 3 SpokenNameIncomplete", code == 3 and out.get("error_type") == "SpokenNameIncomplete", str(out))
    check("details: ruolo e nome", out["details"] == {"campo": "nome_pronunciato", "ruolo": "intervistatore", "nome": "Mario"}, str(out))

    code, out, err = episode(
        run_dir, config,
        "--voce", f"presentatore={PRESENTER}", "--voce", f"intervistatore={INTERVIEWER}", "--voce", f"ospite={GUEST}",
        "--nome", f"presentatore={PRESENTER}", "--nome", f"intervistatore={INTERVIEWER}", "--nome", f"ospite={GUEST}",
        "--descrizione", "intervistatore=giornalista economico", "--descrizione", "ospite=analista finanziaria",
    )
    check("cast completo: exit 0", code == 0 and out["cast_completo"], err + str(out))
    check("speaker nell'ordine: presentatore, poi il dialogo", out["speakers"] == [PRESENTER, INTERVIEWER, GUEST], str(out["speakers"]))
    check("participants come per script_save", out["participants"] == [
        {"speaker": INTERVIEWER, "nome": INTERVIEWER, "ruolo": "giornalista economico"},
        {"speaker": GUEST, "nome": GUEST, "ruolo": "analista finanziaria"},
    ], str(out["participants"]))
    check("stato della voce: non ancora clonata", all(r["clonata"] is False for r in out["ruoli"]), str(out["ruoli"]))
    check("nome proposto dal file, mai salvato da solo", out["ruoli"][0]["nome_proposto"] == PRESENTER)

    code, out, _ = episode(run_dir, config, "--formato", "conversazione")
    check("cambio formato: le voci restano (voce_a, voce_b)", [(r["ruolo"], r["voce"]) for r in out["ruoli"]] == [
        ("presentatore", PRESENTER), ("voce_a", INTERVIEWER), ("voce_b", GUEST)
    ], str(out["ruoli"]))
    code, out, _ = episode(run_dir, config, "--formato", "intervista")
    check("e tornando indietro anche", out["interviewer"] == INTERVIEWER and out["cast_completo"], str(out))

    print("\n[C1] passo 3: durata indicativa con le musiche")
    code, out, err = tool("music_set.py", "--run-dir", run_dir, "--intro", "Piano calmo", config=config)
    check("music_set: exit 0", code == 0, err)
    code, out, _ = episode(run_dir, config)
    durata = out["durata_indicativa"]
    check("parlato = durata obiettivo", durata["parlato_seconds"] == 900, str(durata))
    check("totale = parlato + intro + pausa", abs(durata["totale_seconds"] - (900 + 0.5 + 0.8)) < 0.2, str(durata))
    return run_dir


def test_script_write_api(root: Path, config: Path, run_dir: str) -> None:
    print("\n[C1] passo 4: scrittura via API con formato e presentatore")
    responses = root / "anthropic_risposte.json"
    log = root / "anthropic_richieste.jsonl"
    calls_before = len(read_log(log))

    # cast incompleto: si ferma PRIMA di chiamare il modello
    other = tool("run_init.py", "--title", "Incompleto", "--document", "report.txt", config=config)[1]["run_dir"]
    episode(other, config, "--formato", "intervista", "--minuti", "5", "--voce", f"presentatore={PRESENTER}")
    code, out, _ = tool("script_write.py", "--run-dir", other, config=config)
    check("cast incompleto -> exit 1 senza chiamare il modello", code == 1 and len(read_log(log)) == calls_before, str(out))

    queue_responses(responses, {"json": INTERVISTA_SENZA_OSPITE})
    code, out, err = tool("script_write.py", "--run-dir", run_dir, "--by", "Utente A", config=config)
    check("solo --run-dir: exit 0", code == 0, err + str(out))
    prompt = read_log(log)[-1]["messages"][0]["content"]
    check("prompt: formato intervista e chi fa le domande", "Formato: intervista" in prompt and f"Fa le domande (intervistatore): {INTERVIEWER}" in prompt, prompt[:600])
    check("prompt: voce di presentazione e nomi da pronunciare", f"Voce di presentazione: {PRESENTER}" in prompt and f'"{GUEST}" (speaker {GUEST}): analista finanziaria' in prompt)
    check("prompt: durata da episodio.json (15 minuti)", "circa 15 minuti" in prompt)
    check("prompt: il documento della run", "dodici per cento" in prompt)
    check("copione con errore bloccante: salvato ma non approvabile", out["errors"] and out["approvable"] is False and out["version"] == 1, str(out.get("errors")))
    check("problems (chiave storica) = errori", out["problems"] == out["errors"])
    code, out, _ = tool("script_approve.py", "--run-dir", run_dir, "--version", "1", config=config)
    check("approvare una versione con errori -> exit 1 ScriptHasErrors", code == 1 and out.get("error_type") == "ScriptHasErrors", str(out))

    print("\n[C1] modifica diretta di una battuta")
    fixed = INTERVISTA["turni"][0]["testo"]
    code, out, _ = tool("script_edit.py", "--run-dir", run_dir, "--turn", "1", "--text", fixed, "--expected-version", "7", config=config)
    check("versione attesa sbagliata -> exit 1 VersionConflict", code == 1 and out.get("error_type") == "VersionConflict" and out["details"] == {"expected_version": 7, "current_version": 1}, str(out))
    code, out, _ = tool("script_edit.py", "--run-dir", run_dir, "--turn", "9", "--text", "x", config=config)
    check("battuta inesistente -> exit 1 TurnNotFound", code == 1 and out.get("error_type") == "TurnNotFound", str(out))
    code, out, err = tool("script_edit.py", "--run-dir", run_dir, "--turn", "1", "--text", fixed, "--expected-version", "1", "--by", "Utente A", config=config)
    check("modifica: exit 0, versione 2, ora approvabile", code == 0 and out["version"] == 2 and out["approvable"] and not out["errors"], err + str(out))
    check("battuta modificata riportata", out["edited_turn"] == 1)

    turn1 = out["turni_strutturati"][0]
    check("battuta 1: presentatore", turn1["ruolo"] == "presentatore" and turn1["presentatore"] is True, str(turn1))
    check("nome pronunciato del presentatore da episodio.json", turn1["nome_pronunciato"] == PRESENTER, str(turn1))
    check("tag con posizioni", turn1["tag"] == ["[caldo]"] and turn1["parti"][0] == {"tipo": "tag", "testo": "[caldo]", "inizio": 0, "fine": 7}, str(turn1["parti"][:2]))
    check("le parti ricompongono il testo", "".join(p["testo"] for p in turn1["parti"]) == turn1["testo"])
    turn2 = out["turni_strutturati"][1]
    check("battuta 2: intervistatore con nome pronunciato", turn2["ruolo"] == "intervistatore" and turn2["nome_pronunciato"] == INTERVIEWER, str(turn2))
    check("battuta 3: ospite", out["turni_strutturati"][2]["ruolo"] == "ospite")

    code, out, _ = tool("script_edit.py", "--run-dir", run_dir, "--turn", "1", "--text", fixed, config=config)
    check("stesso testo -> exit 1 NothingToChange, nessuna versione", code == 1 and out.get("error_type") == "NothingToChange", str(out))
    code, out, _ = tool("script_edit.py", "--run-dir", run_dir, "--turn", "1", "--text", "Benvenuti a tutti.", config=config)
    check("modifica che rompe la presentazione -> exit 1 ScriptInvalid, non salvata", code == 1 and out.get("error_type") == "ScriptInvalid" and out["details"]["errors"], str(out))

    print("\n[C1] approvazione")
    code, out, _ = tool("script_approve.py", "--run-dir", run_dir, "--version", "1", config=config)
    check("approvare una versione non letta -> VersionConflict", code == 1 and out.get("error_type") == "VersionConflict", str(out))
    code, out, err = tool("script_approve.py", "--run-dir", run_dir, "--version", "2", "--by", "Utente A", config=config)
    check("approvazione: exit 0 con autore", code == 0 and out["approved"] and out["version"] == 2 and out["by"] == "Utente A", err + str(out))
    code, out, _ = tool("script_show.py", "--run-dir", run_dir, config=config)
    check("script_show: approvato, da chi, versione", out["approved"] and out["approved_by"] == "Utente A" and out["approved_version"] == 2, str({k: out.get(k) for k in ("approved", "approved_by", "approved_version")}))
    check("script_show: elenco versioni con la corrente approvata", [(v["version"], v["current"], v["approved"]) for v in out["versions"]] == [(1, False, False), (2, True, True)], str(out["versions"]))

    edited = INTERVISTA["turni"][3]["testo"].replace("secondo te", "secondo i dati")
    code, out, _ = tool("script_edit.py", "--run-dir", run_dir, "--turn", "4", "--text", edited, config=config)
    check("modifica dopo l'approvazione: l'approvazione decade da sola", code == 0 and out["approved"] is False and out["approval_problem"], str(out.get("approval_problem")))
    code, out, _ = tool("script_versions.py", "--run-dir", run_dir, "--list", config=config)
    v2 = out["versions"][1]
    check("v2 resta segnata come approvata ma superata", v2["approved"] is False and v2["approved_but_outdated"] is True, str(v2))
    check("tipi di versione", [v["kind"] for v in out["versions"]] == ["prima_stesura", "modifica_manuale", "modifica_manuale"], str(out["versions"]))
    check("istruzione registrata", out["versions"][2]["instruction"] == "modifica manuale battuta 4" and out["versions"][1]["by"] == "Utente A")

    print("\n[C1] confronto e ripristino")
    code, out, err = tool("script_versions.py", "--run-dir", run_dir, "--diff", "1", config=config)
    check("diff v1 -> corrente: exit 0", code == 0, err)
    check("conteggi: 2 modificate, 3 invariate", out["conteggi"] == {"invariata": 3, "modificata": 2, "aggiunta": 0, "rimossa": 0}, str(out["conteggi"]))
    modified = [r for r in out["righe"] if r["stato"] == "modificata"]
    check(
        "pezzi cambiati parola per parola, punteggiatura a parte",
        modified[1]["segmenti"] == [
            {"tipo": "uguale", "testo": "E perché è successo, secondo "},
            {"tipo": "rimosso", "testo": "te"},
            {"tipo": "aggiunto", "testo": "i dati"},
            {"tipo": "uguale", "testo": "?"},
        ],
        str(modified[1]["segmenti"]),
    )
    check("richiesta che ha creato la versione di arrivo", out["to"]["instruction"] == "modifica manuale battuta 4")
    code, out, _ = tool("script_versions.py", "--run-dir", run_dir, "--diff", "1", "3", "--only-changes", config=config)
    check("solo modifiche: invariate raggruppate", [r["stato"] for r in out["righe"]] == ["modificata", "invariate", "modificata", "invariate"], str([r["stato"] for r in out["righe"]]))
    check("gruppo di invariate con conteggio", out["righe"][1] == {"stato": "invariate", "conteggio": 2, "da_n": 2, "a_n": 3}, str(out["righe"][1]))

    code, out, _ = tool("script_versions.py", "--run-dir", run_dir, "--show", "1", config=config)
    check("show v1: contenuto e errori di allora", code == 0 and out["version"] == 1 and out["errors"] and len(out["turni_strutturati"]) == 5, str(out.get("errors")))

    session_before = json.loads((Path(run_dir) / "_script_session.json").read_text(encoding="utf-8"))
    code, out, err = tool("script_versions.py", "--run-dir", run_dir, "--restore", "2", "--expected-version", "3", "--by", "Utente B", config=config)
    check("ripristino della v2: nuova versione 4", code == 0 and out["version"] == 4 and out["restored_from"] == 2, err + str(out))
    check("ripristino: va riapprovato anche se il testo era approvato", out["approved"] is False and out["approved_version"] is None, str(out))
    session_after = json.loads((Path(run_dir) / "_script_session.json").read_text(encoding="utf-8"))
    check("la storia non si riscrive (v1-v3 identiche)", session_after["versions"][:3] == session_before["versions"])
    check("il ripristino non entra fra le revisioni date al modello", session_after["revisions"] == session_before["revisions"])
    code, out, _ = tool("script_versions.py", "--run-dir", run_dir, "--restore", "4", config=config)
    check("ripristinare la versione corrente -> NothingToChange", code == 1 and out.get("error_type") == "NothingToChange", str(out))

    print("\n[C1] revisione a parole e stato 'in corso'")
    queue_responses(responses, {"json": INTERVISTA})
    code, out, err = tool("script_revise.py", "--run-dir", run_dir, "--instructions", "Rendi piu' diretta la domanda 4", config=config)
    check("revisione: exit 0, versione 5 valida", code == 0 and out["version"] == 5 and not out["errors"], err + str(out))
    check("istruzione registrata fra le revisioni", out["revisions"][-1] == "Rendi piu' diretta la domanda 4", str(out["revisions"]))

    from src.run_lock import work_lock

    with work_lock(Path(run_dir), "produzione"):
        code, out, _ = tool("script_write.py", "--run-dir", run_dir, config=config)
        check("operazione lunga gia' in corso -> exit 1 RunBusy", code == 1 and out.get("error_type") == "RunBusy", str(out))
        check("details dice quale operazione", out["details"]["in_corso"]["operazione"] == "produzione", str(out))
        code, out, _ = tool("script_show.py", "--run-dir", run_dir, config=config)
        check("script_show vede l'operazione in corso", out["in_corso"]["operazione"] == "produzione", str(out.get("in_corso")))
    code, out, _ = tool("script_show.py", "--run-dir", run_dir, config=config)
    check("finita l'operazione, in_corso torna null", out["in_corso"] is None)

    fresh = tool("run_init.py", "--title", "Vuota", config=config)[1]["run_dir"]
    code, out, _ = tool("script_show.py", "--run-dir", fresh, config=config)
    check("run senza copione: exit 0, has_script false", code == 0 and out["has_script"] is False, str(out))


def test_revise_concurrent_edit(root: Path, config: Path, run_dir: str) -> None:
    """Mentre il modello scrive, un altro utente modifica una battuta."""
    print("\n[C1] revisione con una modifica concorrente")
    from src.config import load_config
    from src.script_authoring import revise_draft
    from src.script_editing import edit_turn
    from src.script_writer import PodcastScript

    run = Path(run_dir)

    class SlowWriter:
        model, effort = "finto", "low"

        def run_messages(self, messages):  # noqa: ANN001
            # l'altro utente salva una modifica PRIMA che la revisione finisca
            edit_turn(run, 3, "Che sono cresciuti molto, e in modo netto rispetto all'anno scorso.")
            return PodcastScript.from_dict(INTERVISTA)

    before = json.loads((run / "_script_session.json").read_text(encoding="utf-8"))["versions"]
    result = revise_draft(run, load_config(config), instructions="Accorcia", writer=SlowWriter())
    after = json.loads((run / "_script_session.json").read_text(encoding="utf-8"))["versions"]
    check("entrambe le versioni restano nella storia", len(after) == len(before) + 2)
    check("la revisione dichiara da quale versione e' partita", after[-1]["base_version"] == len(before))
    check("e lo segnala negli avvisi", any("nel frattempo" in w for w in result.warnings), str(result.warnings))


def test_api_errors(root: Path, config: Path, run_dir: str) -> None:
    print("\n[C1] errori dell'API Anthropic: esiti stabili")
    responses = root / "anthropic_risposte.json"
    session_file = Path(run_dir) / "_script_session.json"
    versions_before = len(json.loads(session_file.read_text(encoding="utf-8"))["versions"])
    cases = [
        ("auth", None, 2, "ModelAuthError", None),
        ("permission", None, 2, "ModelAuthError", None),
        ("credit", None, 2, "ModelCreditExhausted", None),
        ("rate_limit", 7, 1, "ModelRateLimited", 7.0),
        ("overloaded", None, 1, "ModelOverloaded", None),
        ("unavailable", 3, 1, "ModelOverloaded", 3.0),
        ("connection", None, 1, "ModelUnreachable", None),
    ]
    for kind, retry_after, exit_code, error_type, expected_retry in cases:
        answer = {"raise": kind}
        if retry_after is not None:
            answer["retry_after"] = retry_after
        queue_responses(responses, answer)
        code, out, err = tool("script_revise.py", "--run-dir", run_dir, "--instructions", "Accorcia", config=config)
        check(f"{kind}: exit {exit_code} {error_type}", code == exit_code and out.get("error_type") == error_type, str(out))
        if exit_code == 1:
            check(f"{kind}: retry_after in details", out["details"]["retry_after_seconds"] == expected_retry, str(out.get("details")))
        check(f"{kind}: nessuna intestazione o chiave nel JSON", "x-api-key" not in json.dumps(out).lower() and "authorization" not in json.dumps(out).lower())
    versions_after = len(json.loads(session_file.read_text(encoding="utf-8"))["versions"])
    check("nessuna versione creata dalle chiamate fallite", versions_after == versions_before)
    from src.run_lock import current_work

    check("il lock di lavoro e' stato rilasciato", current_work(Path(run_dir)) is None)


def test_cast_change_revokes(root: Path, config: Path, run_dir: str) -> None:
    print("\n[C1] cambio di cast dopo l'approvazione")
    current = json.loads((Path(run_dir) / "_script_session.json").read_text(encoding="utf-8"))
    version = str(len(current["versions"]))
    code, out, err = tool("script_approve.py", "--run-dir", run_dir, "--version", version, "--by", "Utente A", config=config)
    check("approvazione: exit 0", code == 0, err + str(out))

    code, out, _ = episode(run_dir, config, "--titolo", "Il trimestre, versione breve", "--durata", "0:20")
    check("titolo e durata: l'approvazione resta", out["approval_revoked"] is None, str(out.get("approval_revoked")))
    code, out, _ = tool("script_show.py", "--run-dir", run_dir, config=config)
    check("...e il copione risulta ancora approvato", out["approved"] is True)

    code, out, _ = episode(run_dir, config, "--descrizione", "ospite=analista finanziaria")
    check("stessa descrizione di prima: nessun cambio, nessun ritiro", out["approval_revoked"] is None)

    code, out, _ = episode(run_dir, config, "--descrizione", "ospite=analista finanziaria senior")
    check("descrizione del ruolo cambiata: approvazione ritirata", out["approval_revoked"] and out["approval_revoked"]["version"] == int(version), str(out.get("approval_revoked")))
    check("...con un avviso", any("ritirata" in w for w in out["warnings"]), str(out["warnings"]))
    code, out, _ = tool("script_show.py", "--run-dir", run_dir, config=config)
    check("il copione non risulta piu' approvato", out["approved"] is False and out["approved_version"] is None, str(out.get("approved")))

    for args in (("--nome", f"ospite={GUEST}"), ("--formato", "conversazione")):
        tool("script_approve.py", "--run-dir", run_dir, "--version", version, config=config)
        new_value = (args[0], f"ospite=Giulia Maria Bianchi") if args[0] == "--nome" else args
        code, out, _ = episode(run_dir, config, *new_value)
        check(f"{args[0]} cambiato: approvazione ritirata", out["approval_revoked"] is not None, str(out.get("approval_revoked")))
    episode(run_dir, config, "--formato", "intervista", "--nome", f"ospite={GUEST}", "--descrizione", "ospite=analista finanziaria")


def test_docx(root: Path, config: Path) -> None:
    print("\n[C1] documenti DOCX")
    import importlib.util

    if importlib.util.find_spec("docx") is None:
        SKIPPED.append("docx")
        print("  skip estrazione DOCX (python-docx non installato nella .venv)")
        return
    import docx

    document = docx.Document()
    document.add_paragraph("I ricavi del trimestre sono cresciuti del dodici per cento.")
    document.save(str(root / "documenti" / "nota.docx"))
    code, out, err = tool("doc_extract.py", "nota.docx", config=config)
    check("DOCX: exit 0, parole contate, pagine null", code == 0 and out["kind"] == "docx" and out["words"] == 10 and out["pages"] is None, err + str(out))
    (root / "documenti" / "rotto.docx").write_bytes(b"non e' uno zip")
    code, out, _ = tool("doc_extract.py", "rotto.docx", config=config)
    check("DOCX corrotto -> DocumentNotExtractable illeggibile", code == 1 and out["details"]["motivo"] == "illeggibile", str(out))


def test_path_confinement(root: Path, config: Path, run_dir: str) -> None:
    """Revisione di sicurezza C0+C1: ALTA-1 (documenti) e ALTA-2 (cartelle delle run)."""
    print("\n[sicurezza] documenti solo dalla libreria documenti/")
    docs = root / "documenti"
    (docs / ".env").write_text("FISH_AUDIO_API_KEY=fa-finta-123\n", encoding="utf-8")
    (root / "segreto.txt").write_text("SEGRETO nel server\n", encoding="utf-8")
    (docs / "collegamento.txt").symlink_to(root / "segreto.txt")
    (docs / "senza_estensione").write_text("testo\n", encoding="utf-8")

    for name in (".env", "senza_estensione"):
        code, out, _ = tool("doc_extract.py", name, config=config)
        check(f"'{name}' rifiutato (exit 1, nessun contenuto)", code == 1 and "preview" not in out and "fa-finta" not in json.dumps(out), str(out))
    for value in ("../segreto.txt", str(root / "segreto.txt"), "collegamento.txt"):
        code, out, _ = tool("doc_extract.py", value, config=config)
        check(f"fuori dalla libreria ({value}) -> exit 1", code == 1 and "SEGRETO" not in json.dumps(out), str(out))
    code, out, _ = tool("doc_extract.py", "config.yaml", config=config)
    check("un file della cartella corrente non si raggiunge per nome", code == 1 and "preview" not in out, str(out))
    code, out, _ = tool("doc_extract.py", str(root / "segreto.txt"), "--trusted-paths", config=config)
    check("con --trusted-paths (uso locale) il percorso libero funziona", code == 0 and "SEGRETO" in out["preview"], str(out))
    code, out, _ = tool("doc_extract.py", "report.txt", "--save-to", str(root / "altrove"), config=config)
    check("--save-to fuori da output/ -> exit 1 PathNotAllowed", code == 1 and out.get("error_type") == "PathNotAllowed" and not (root / "altrove").exists(), str(out))
    code, out, _ = episode(run_dir, config, "--documento", ".env")
    check("episode_config --documento .env -> exit 1", code == 1, str(out))
    code, out, _ = tool("run_init.py", "--title", "Furbo", "--document", "../segreto.txt", config=config)
    check("run_init --document fuori libreria -> exit 1", code == 1, str(out))

    print("\n[sicurezza] --run-dir e --session solo dentro output/")
    outside = root / "fuori"
    outside.mkdir()
    (root / "output" / "link-fuori").symlink_to(outside)
    for value in (str(outside), str(root / "output" / ".." / "fuori"), str(root / "output" / "link-fuori"), str(root / "output")):
        code, out, _ = episode(value, config, "--titolo", "x")
        check(f"episode_config --run-dir {Path(value).name or value} -> PathNotAllowed", code == 1 and out.get("error_type") == "PathNotAllowed", str(out))
    check("niente scritto fuori da output/", list(outside.iterdir()) == [], str(list(outside.iterdir())))
    missing = root / "output" / "non-esiste" / "ancora-sotto"
    code, out, _ = tool("script_approve.py", "--session", str(missing / "_script_session.json"), config=config)
    check("script_approve --session fuori: exit 1 e nessuna cartella creata", code == 1 and not (root / "output" / "non-esiste").exists(), str(out))
    code, out, _ = tool("script_approve.py", "--run-dir", str(root / "output" / "non-esiste"), config=config)
    check("run inesistente in output/: exit 1, nessuna cartella creata", code == 1 and not (root / "output" / "non-esiste").exists(), str(out))
    for name, extra in (
        ("script_show.py", []),
        ("script_edit.py", ["--turn", "1", "--text", "x"]),
        ("script_versions.py", ["--list"]),
        ("turn_at.py", ["--at", "0:01"]),
        ("music_set.py", ["--none", "intro"]),
        ("signals.py", []),
    ):
        code, out, _ = tool(name, "--run-dir", str(outside), *extra, config=config)
        check(f"{name} --run-dir fuori -> PathNotAllowed", code == 1 and out.get("error_type") == "PathNotAllowed", str(out))
    code, out, _ = tool("script_show.py", "--run-dir", Path(run_dir).name, config=config)
    check("il solo nome della cartella dell'Archivio funziona", code == 0 and out["has_script"], str(out))


def test_legacy_paths(root: Path, config: Path) -> None:
    print("\n[C1] percorsi di prima invariati")
    responses = root / "anthropic_risposte.json"
    log = root / "anthropic_richieste.jsonl"
    run_dir = tool("run_init.py", "--title", "Legacy", config=config)[1]["run_dir"]
    (root / "testo.txt").write_text("I ricavi sono cresciuti.", encoding="utf-8")
    queue_responses(responses, {"json": SCRIPT})
    code, out, err = tool(
        "script_write.py", "--text-file", str(root / "testo.txt"),
        "--speakers", "Mario Rossi", "Giulia Bianchi", "--run-dir", run_dir, "--trusted-paths", config=config,
    )
    check("script_write senza formato: exit 0", code == 0 and not out["problems"], err + str(out))
    prompt = read_log(log)[-1]["messages"][0]["content"]
    check("prompt di sempre (dialogo fra 2 speaker)", "Formato: un dialogo fra 2 speaker." in prompt and "Voce di presentazione" not in prompt)

    from src.script_diff import diff_scripts

    a = {"titolo": "t", "turni": [{"speaker": "A", "testo": "uno"}, {"speaker": "B", "testo": "due"}, {"speaker": "A", "testo": "tre"}]}
    b = {"titolo": "t", "turni": [{"speaker": "A", "testo": "uno"}, {"speaker": "A", "testo": "una cosa del tutto diversa"}, {"speaker": "B", "testo": "nuova"}]}
    rows = diff_scripts(a, b)["righe"]
    check("diff: battute non simili = rimossa + aggiunta, non 'modificata'", [r["stato"] for r in rows].count("modificata") == 0 and len(rows) == 5, str([r["stato"] for r in rows]))


# ----------------------------------------------------------------------
# Sicurezza C0+C1: medie e basse (MEDIA-1, BASSA-1, BASSA-2, BASSA-3)
# ----------------------------------------------------------------------


def write_limits_config(root: Path, name: str = "config-limiti.yaml", **overrides: int) -> Path:
    """Come config-web.yaml, con limiti sui documenti piccoli da superare nei test."""
    limits = {
        "max_bytes": 100000,
        "max_pages": 2,
        "max_chars": 200,
        "max_unpacked_bytes": 5000000,
        "max_xml_bytes": 1000000,
        "max_zip_entries": 50,
        "max_content_bytes": 50000000,
        "max_seconds": 60,
        **overrides,
    }
    text = (root / "config-web.yaml").read_text(encoding="utf-8")
    text += "documents:\n" + "".join(f"  {key}: {value}\n" for key, value in limits.items())
    text = text.replace("  target_minutes: 3\n", "  target_minutes: 3\n  max_document_chars: 20\n")
    config = root / name
    config.write_text(text, encoding="utf-8")
    return config


def make_pdf_pages(path: Path, pages: int) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)


def test_document_limits(root: Path) -> None:
    print("\n[sicurezza] MEDIA-1: limiti sui documenti")
    config = write_limits_config(root)
    docs = root / "documenti"

    (docs / "enorme.txt").write_text("parola " * 20000, encoding="utf-8")  # 140.000 byte
    code, out, _ = tool("doc_extract.py", "enorme.txt", config=config)
    check(
        "file oltre max_bytes -> exit 1 DocumentTooLarge con details",
        code == 1 and out.get("error_type") == "DocumentTooLarge"
        and out["details"] == {"limite": "byte", "massimo": 100000, "valore": 140000, "file": "enorme.txt"},
        str(out),
    )

    make_pdf_pages(docs / "tre_pagine.pdf", 3)
    code, out, _ = tool("doc_extract.py", "tre_pagine.pdf", config=config)
    check(
        "PDF oltre max_pages -> DocumentTooLarge prima di estrarre (non 'scansione')",
        code == 1 and out.get("error_type") == "DocumentTooLarge" and out["details"]["limite"] == "pagine"
        and out["details"]["valore"] == 3,
        str(out),
    )
    make_pdf_pages(docs / "due_pagine.pdf", 2)
    code, out, _ = tool("doc_extract.py", "due_pagine.pdf", config=config)
    check("PDF entro il limite di pagine: si arriva all'estrazione (scansione)", code == 1 and out["details"].get("motivo") == "scansione", str(out))

    (docs / "lungo.txt").write_text("a" * 300, encoding="utf-8")
    code, out, _ = tool("doc_extract.py", "lungo.txt", config=config)
    check(
        "testo oltre max_chars -> DocumentTooLarge caratteri",
        code == 1 and out.get("error_type") == "DocumentTooLarge" and out["details"]["limite"] == "caratteri"
        and out["details"]["massimo"] == 200,
        str(out),
    )

    import zipfile

    import docx

    normal = docx.Document()
    normal.add_paragraph("Un DOCX normale.")
    normal.save(str(docs / "normale.docx"))
    code, out, err = tool("doc_extract.py", "normale.docx", config=config)
    check("DOCX normale entro i limiti: exit 0", code == 0, err + str(out))
    bomb = docs / "bomba.docx"
    with zipfile.ZipFile(docs / "normale.docx") as source, zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "word/document.xml":
                data = data.replace(b"Un DOCX normale.", b"x" + b" " * 6_000_000)
            target.writestr(info.filename, data)
    check("la bomba e' piccola su disco", bomb.stat().st_size < 100000, str(bomb.stat().st_size))
    code, out, _ = tool("doc_extract.py", "bomba.docx", config=config)
    check(
        "DOCX che si scompatta oltre max_unpacked_bytes -> DocumentTooLarge prima di aprirlo",
        code == 1 and out.get("error_type") == "DocumentTooLarge" and out["details"]["limite"] == "byte_scompattati",
        str(out),
    )

    def docx_variant(target: Path, extra_xml: int = 0, extra_entries: int = 0) -> None:
        with zipfile.ZipFile(docs / "normale.docx") as source, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "word/document.xml" and extra_xml:
                    data = data.replace(b"Un DOCX normale.", b"x" + b" " * extra_xml)
                out.writestr(info.filename, data)
            for n in range(extra_entries):
                out.writestr(f"word/extra{n}.bin", b"")

    docx_variant(docs / "xml_grande.docx", extra_xml=500000)
    code, out, _ = tool("doc_extract.py", "xml_grande.docx", config=config)
    check(
        "DOCX con parti XML oltre max_xml_bytes (entro i byte scompattati) -> byte_xml",
        code == 1 and out.get("error_type") == "DocumentTooLarge" and out["details"]["limite"] == "byte_xml",
        str(out),
    )
    docx_variant(docs / "tante_parti.docx", extra_entries=60)
    code, out, _ = tool("doc_extract.py", "tante_parti.docx", config=config)
    check(
        "DOCX con troppe parti nello zip -> parti_zip",
        code == 1 and out.get("error_type") == "DocumentTooLarge" and out["details"]["limite"] == "parti_zip",
        str(out),
    )

    # PDF piccolo le cui pagine condividono un content stream grande di soli
    # spazi: nessun carattere estratto, ma molto lavoro (revisione del diff, MEDIA-1).
    def shared_stream_pdf(target: Path, pages: int, repeats: int) -> None:
        from pypdf import PdfWriter
        from pypdf.generic import NameObject, StreamObject

        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=200, height=200)
        stream = StreamObject()
        stream.set_data(b"BT /F1 1 Tf ( ) Tj ET\n" * repeats)
        ref = writer._add_object(stream.flate_encode())  # noqa: SLF001
        for page in writer.pages:
            page[NameObject("/Contents")] = ref
        with target.open("wb") as handle:
            writer.write(handle)

    shared_stream_pdf(docs / "spazi.pdf", 2, 100000)  # ~2,2 MB decodificati per pagina
    check("il PDF con lo stream condiviso e' piccolo su disco", (docs / "spazi.pdf").stat().st_size < 100000, str((docs / "spazi.pdf").stat().st_size))
    config_content = write_limits_config(root, "config-contenuto.yaml", max_content_bytes=3000000)
    started = time.monotonic()
    code, out, _ = tool("doc_extract.py", "spazi.pdf", config=config_content)
    check(
        "PDF con content stream condiviso oltre max_content_bytes -> contenuto_pagine, alla seconda pagina",
        code == 1 and out.get("error_type") == "DocumentTooLarge" and out["details"]["limite"] == "contenuto_pagine"
        and out["details"]["valore"] > 3000000,
        f"{out} in {time.monotonic() - started:.1f}s",
    )
    # La scadenza si controlla fra una pagina e l'altra: qui l'orologio
    # avanza di 10 secondi a ogni lettura, cosi' il test non dipende dalla
    # velocita' della macchina.
    proc = run_python(
        f"""
        import itertools
        import src.pdf_reader as reader
        ticks = itertools.count(0, 10)
        reader.time.monotonic = lambda: next(ticks)
        try:
            reader.extract_text({str(docs / "spazi.pdf")!r}, limits=reader.DocumentLimits(max_seconds=5))
            print("NESSUN LIMITE")
        except reader.DocumentTooLarge as exc:
            print(exc.details["limite"], exc.details["massimo"])
        """
    )
    check("estrazione oltre max_seconds -> DocumentTooLarge secondi", proc.stdout.strip() == "secondi 5", proc.stdout + proc.stderr)

    from src.config import ConfigError, load_config

    check("config.yaml del progetto: limiti dichiarati", load_config().document_limits.max_pages == 500)
    project_limits = load_config().document_limits
    check(
        "config.yaml del progetto: limiti nuovi dichiarati",
        (project_limits.max_xml_bytes, project_limits.max_zip_entries, project_limits.max_content_bytes, project_limits.max_seconds)
        == (15728640, 2000, 52428800, 60),
    )
    bad = root / "config-limiti-rotti.yaml"
    bad.write_text((root / "config-web.yaml").read_text(encoding="utf-8") + "documents:\n  max_pages: 0\n", encoding="utf-8")
    try:
        load_config(bad).document_limits
        ok = False
    except ConfigError:
        ok = True
    check("limite non positivo -> ConfigError", ok)

    before = set((root / "output").iterdir())
    code, out, _ = tool("run_init.py", "--title", "Troppo", "--document", "enorme.txt", config=config)
    check("run_init con documento troppo grande: exit 1, nessuna bozza", code == 1 and out.get("error_type") == "DocumentTooLarge" and set((root / "output").iterdir()) == before, str(out))

    print("\n[sicurezza] MEDIA-1: estrazione fuori dal lock, una volta sola")
    run_dir = tool("run_init.py", "--title", "Lock e documenti", config=config)[1]["run_dir"]
    from src.episode_config import locked_update

    with locked_update(Path(run_dir)):
        started = time.monotonic()
        code, out, _ = episode(run_dir, config, "--documento", "enorme.txt")
        elapsed = time.monotonic() - started
    check(
        "con episodio.json bloccato, un documento troppo grande si rifiuta subito (estratto prima del lock)",
        code == 1 and out.get("error_type") == "DocumentTooLarge" and elapsed < 10,
        f"{out} in {elapsed:.1f}s",
    )

    proc = run_python(
        f"""
        import runpy, sys
        sys.path.insert(0, "tools")
        import src.episode_setup as setup
        calls = []
        original = setup.extract_text
        def counting(*args, **kwargs):
            calls.append(args[0])
            return original(*args, **kwargs)
        setup.extract_text = counting
        sys.argv = ["run_init.py", "--title", "Una volta", "--document", "report.txt", "--config", {str(config)!r}]
        try:
            runpy.run_path("tools/run_init.py", run_name="__main__")
        except SystemExit as exc:
            print("EXIT", exc.code, file=sys.stderr)
        print("CHIAMATE", len(calls), file=sys.stderr)
        """
    )
    check("run_init --document estrae il documento una volta sola", "CHIAMATE 1" in proc.stderr, proc.stderr[-500:])

    print("\n[sicurezza] MEDIA-1: nella sessione solo il testo troncato")
    responses = root / "anthropic_risposte.json"
    long_text = "I ricavi sono cresciuti del dodici per cento nel trimestre."
    run_dir = tool("run_init.py", "--title", "Sessione corta", config=config)[1]["run_dir"]
    text_file = Path(run_dir) / "testo.txt"
    text_file.write_text(long_text, encoding="utf-8")
    queue_responses(responses, {"json": SCRIPT})
    code, out, err = tool(
        "script_write.py", "--text-file", str(text_file), "--speakers", "Mario Rossi", "Giulia Bianchi",
        "--run-dir", run_dir, config=config,
    )
    check("scrittura con max_document_chars 20: exit 0", code == 0, err + str(out))
    session_file = Path(run_dir) / "_script_session.json"
    session = json.loads(session_file.read_text(encoding="utf-8"))
    check("document_text nella sessione = i 20 caratteri visti dal modello", session["document_text"] == long_text[:20], repr(session["document_text"]))

    session["document_text"] = "testo intero di una sessione vecchia " * 50
    session_file.write_text(json.dumps(session), encoding="utf-8")
    code, out, err = tool("script_show.py", "--run-dir", run_dir, config=config)
    check("una sessione vecchia con il testo intero si legge ancora", code == 0 and out["has_script"], err + str(out))

    big = Path(run_dir) / "grande.txt"
    big.write_text("x" * 150000, encoding="utf-8")
    code, out, _ = tool("script_write.py", "--text-file", str(big), "--speakers", "Mario Rossi", "Giulia Bianchi", "--run-dir", run_dir, config=config)
    check("--text-file oltre max_bytes -> DocumentTooLarge", code == 1 and out.get("error_type") == "DocumentTooLarge", str(out))


def test_approve_requires_version(root: Path, config: Path) -> None:
    print("\n[sicurezza] BASSA-1: approvare richiede --version (o --current)")
    responses = root / "anthropic_risposte.json"
    run_dir = tool("run_init.py", "--title", "Approva con versione", config=config)[1]["run_dir"]
    (Path(run_dir) / "testo.txt").write_text("I ricavi sono cresciuti.", encoding="utf-8")
    queue_responses(responses, {"json": SCRIPT})
    tool("script_write.py", "--text-file", str(Path(run_dir) / "testo.txt"), "--speakers", "Mario Rossi", "Giulia Bianchi", "--run-dir", run_dir, config=config)

    code, out, _ = tool("script_approve.py", "--run-dir", run_dir, config=config)
    check(
        "senza --version ne' --current -> exit 1 VersionRequired, niente approvato",
        code == 1 and out.get("error_type") == "VersionRequired" and out["details"] == {"current_version": 1},
        str(out),
    )
    code, out, _ = tool("script_show.py", "--run-dir", run_dir, config=config)
    check("la sessione resta non approvata", out["approved"] is False)
    code, out, err = tool("script_approve.py", "--run-dir", run_dir, "--current", config=config)
    check("--current approva la versione corrente", code == 0 and out["approved"] and out["version"] == 1, err + str(out))
    code, out, _ = tool("script_approve.py", "--run-dir", run_dir, "--revoke", config=config)
    check("--revoke non richiede la versione", code == 0 and out["approved"] is False, str(out))
    code, out, _ = tool("script_approve.py", "--run-dir", run_dir, "--version", "1", "--by", "Utente A", config=config)
    check("--version V approva come prima", code == 0 and out["approved"] and out["by"] == "Utente A", str(out))
    proc = subprocess.run(
        [PYTHON, str(PROJECT_ROOT / "tools" / "script_approve.py"), "--run-dir", run_dir, "--version", "1", "--current", "--config", str(config)],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), env=clean_env(),
    )
    check("--version e --current insieme: rifiutati da argparse", proc.returncode != 0)


def test_ui_fields(root: Path, config: Path) -> None:
    print("\n[sicurezza] BASSA-2: nomi relativi per la UI, niente pid")
    code, out, _ = tool("run_init.py", "--title", "Campi UI", "--document", "report.txt", config=config)
    run_dir = out["run_dir"]
    check("run_init: 'run' = nome della cartella", out["run"] == Path(run_dir).name, str(out.get("run")))
    check("vista dell'episodio: 'run' e documento.nome", out["episodio"]["run"] == Path(run_dir).name and out["episodio"]["documento"]["nome"] == "report.txt")
    code, out, _ = episode(run_dir, config, "--titolo", "Campi UI 2")
    check("episode_config: 'run'", code == 0 and out["run"] == Path(run_dir).name, str(out.get("run")))
    code, out, _ = tool("script_show.py", "--run-dir", run_dir, config=config)
    check("script_show senza copione: 'run'", out["run"] == Path(run_dir).name)

    from src.run_lock import work_lock

    with work_lock(Path(run_dir), "produzione"):
        info = json.loads((Path(run_dir) / "_lavoro.json").read_text(encoding="utf-8"))
        code, out, _ = tool("script_show.py", "--run-dir", run_dir, config=config)
        check("in_corso senza pid", out["in_corso"]["operazione"] == "produzione" and "pid" not in out["in_corso"], str(out["in_corso"]))
        proc = run_python(
            f"""
            from src.run_lock import RunBusy, work_lock
            try:
                with work_lock({run_dir!r}, "altro"):
                    print("PRESO")
            except RunBusy as exc:
                print(sorted(exc.details["in_corso"]))
            """
        )
        check("RunBusy.details.in_corso senza pid", proc.stdout.strip() == "['dal', 'operazione']", proc.stdout + proc.stderr)
    check("il pid resta nel file _lavoro.json", "pid" in info)

    proc = run_python(
        f"""
        from src.fileio import file_lock, LockTimeout
        from pathlib import Path
        target = Path({str(root / "condiviso2.json")!r})
        with file_lock(target):
            import subprocess, sys
            code = "from src.fileio import file_lock, LockTimeout\\ntry:\\n    with file_lock({str(root / "condiviso2.json")!r}, timeout=0.2): pass\\nexcept LockTimeout as e:\\n    print(e.details['file'])"
            print(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip())
        """
    )
    check("LockTimeout.details.file: solo il nome", proc.stdout.strip() == "condiviso2.json", proc.stdout + proc.stderr)

    (root / "doc_fuori.txt").write_text("x", encoding="utf-8")
    code, out, _ = tool("doc_extract.py", str(root / "manca.txt"), "--trusted-paths", config=config)
    check("documento non trovato (percorso libero): il messaggio ha solo il nome", code == 1 and str(root) not in out["error"] and "manca.txt" in out["error"], out.get("error", ""))
    code, out, _ = tool("doc_extract.py", "manca.txt", config=config)
    check("documento non trovato nella libreria: solo il nome", code == 1 and str(root) not in json.dumps(out), str(out))

    broken = Path(tool("run_init.py", "--title", "Sessione rotta", config=config)[1]["run_dir"])
    (broken / "_script_session.json").write_text("{non json", encoding="utf-8")
    code, out, _ = tool("script_show.py", "--run-dir", str(broken), config=config)
    check("sessione illeggibile: errore senza percorso assoluto", code == 1 and str(root) not in out["error"], out.get("error", ""))

    proc = run_python(
        f"""
        import sys
        from pathlib import Path
        sys.path.insert(0, "tools")
        import _common
        root = str(_common.PROJECT_ROOT)
        home = str(Path.home())
        print(_common._redact(root + "/output/run-x/conversazione.json"))
        print(_common._redact({{"file": root + "/documenti/a.pdf", "lista": [home + "/altrove/b.txt"]}}))
        """
    )
    lines = proc.stdout.strip().splitlines()
    check("errori: il percorso del progetto diventa relativo", lines[:1] == ["output/run-x/conversazione.json"], proc.stdout + proc.stderr)
    check("errori: dentro details e fuori dal progetto (home -> ~)", len(lines) > 1 and "'documenti/a.pdf'" in lines[1] and "'~/altrove/b.txt'" in lines[1], proc.stdout)


def test_run_dir_race(root: Path) -> None:
    print("\n[sicurezza] BASSA-3: creazione atomica della cartella della run")
    target = root / "corsa"
    target.mkdir()
    start_at = time.time() + 1.5
    procs = [
        subprocess.Popen(
            [PYTHON, "-c", textwrap.dedent(
                f"""
                import time
                from pathlib import Path
                from src.run_layout import make_run_dir
                time.sleep(max(0.0, {start_at} - time.time()))
                print(make_run_dir(Path({str(target)!r}), "Stesso titolo").name)
                """
            )],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT), env=clean_env(),
        )
        for _ in range(8)
    ]
    results = [p.communicate(timeout=60) + (p.returncode,) for p in procs]
    names = [out.strip() for out, _, _ in results]
    check("8 processi con lo stesso titolo: tutti exit 0", all(code == 0 for _, _, code in results), str([e[-300:] for _, e, _ in results if e]))
    check("8 cartelle diverse", len(set(names)) == 8 and len(list(target.iterdir())) == 8, str(names))

    from src.run_layout import make_run_dir

    taken = root / "corsa2"
    first = make_run_dir(taken, "Doppio")
    second = make_run_dir(taken, "Doppio")
    third = make_run_dir(taken, "Doppio")
    check("suffissi -2, -3 sulla cartella occupata", second.name.endswith("-2") and third.name.endswith("-3") and first.name.endswith("Doppio".lower()), str([first.name, second.name, third.name]))


# ----------------------------------------------------------------------
# Modalita' web (PODCAST_AGENT_WEB=1)
# ----------------------------------------------------------------------


def run_tool_env(name: str, args: list[str], *, web: bool) -> tuple[int, dict, str]:
    """Come tool(), ma con gli argomenti esatti (niente --config aggiunto)."""
    env = clean_env()
    env.pop("PODCAST_AGENT_WEB", None)
    if web:
        env["PODCAST_AGENT_WEB"] = "1"
    proc = subprocess.run(
        [PYTHON, str(PROJECT_ROOT / "tools" / name), *args],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), env=env,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"_raw_stdout": proc.stdout}
    return proc.returncode, payload, proc.stderr


#: Argomenti obbligatori minimi di ogni tool: servono perche' argparse
#: arrivi fino al controllo della modalita' web.
REQUIRED_ARGS = {
    "doc_extract.py": ["report.txt"],
    "episode_config.py": ["--run-dir", "R"],
    "estimate.py": [],
    "lessons.py": ["--list"],
    "mix.py": ["--run-dir", "R"],
    "music_list.py": [],
    "music_set.py": ["--run-dir", "R"],
    "retake.py": ["--run-dir", "R", "--list", "1"],
    "run_finalize.py": ["--run-dir", "R", "--document", "report.txt", "--voices", "V"],
    "run_init.py": ["--title", "Modalita web"],
    "script_approve.py": ["--run-dir", "R", "--version", "1"],
    "script_edit.py": ["--run-dir", "R", "--turn", "1", "--text", "x"],
    "script_revise.py": ["--run-dir", "R", "--instructions", "x"],
    "script_save.py": ["--run-dir", "R", "--script-json", "S"],
    "script_show.py": ["--run-dir", "R"],
    "script_versions.py": ["--run-dir", "R", "--list"],
    "script_write.py": ["--run-dir", "R"],
    "signals.py": ["--run-dir", "R"],
    "synthesize.py": ["--voices", "V", "--run-dir", "R"],
    "turn_at.py": ["--run-dir", "R", "--at", "0:01"],
    "voices_list.py": [],
    "voices_prepare.py": ["--speakers", "Mario Rossi"],
    "voice_meta.py": ["--name", "Mario Rossi"],
    "produce.py": ["--run-dir", "R"],
    "episode_info.py": ["--run-dir", "R"],
    "waveform.py": ["--run-dir", "R"],
}


def test_web_mode(root: Path, config: Path, run_dir: str) -> None:
    print("\n[modalita' web] argomenti riservati all'uso locale")
    tools_dir = PROJECT_ROOT / "tools"
    all_tools = sorted(p.name for p in tools_dir.glob("*.py") if not p.name.startswith("_") and p.name != "doctor.py")
    check("ogni tool ha i suoi argomenti minimi in questo test", sorted(REQUIRED_ARGS) == all_tools, str(set(all_tools) ^ set(REQUIRED_ARGS)))
    check(
        "nessun tool chiama ancora parser.parse_args() direttamente",
        all("parser.parse_args()" not in (tools_dir / name).read_text(encoding="utf-8") for name in all_tools),
    )

    R = str(Path(run_dir))
    V = str(root / "output" / "voci.json")
    S = str(Path(run_dir) / "conversazione.json")

    def required(name: str) -> list[str]:
        return [R if a == "R" else V if a == "V" else S if a == "S" else a for a in REQUIRED_ARGS[name]]

    def with_script(name: str) -> list[str]:
        # estimate.py e synthesize.py hanno --script obbligatorio.
        return ["--script", S] if name in ("estimate.py", "synthesize.py") else []

    # 1) --config: ogni tool lo rifiuta in modalita' web, prima di fare qualunque cosa.
    before = sorted(str(p) for p in (root / "output").iterdir())
    for name in all_tools:
        args = required(name) + with_script(name) + ["--config", str(config)]
        code, out, _ = run_tool_env(name, args, web=True)
        expected = ["--config"] + [a for a in args if a in ("--script", "--script-json", "--voices", "--out", "--audio-paths")]
        check(
            f"web: {name} --config -> exit 1 NotAllowedInWebMode",
            code == 1 and out.get("error_type") == "NotAllowedInWebMode" and sorted(out["details"]["argomenti"]) == sorted(expected),
            str(out)[:300],
        )
    check("web: nessuna cartella creata dai comandi rifiutati", sorted(str(p) for p in (root / "output").iterdir()) == before)

    # 2) Gli altri argomenti vietati, tool per tool (con --config fuori: in
    #    modalita' web si usa sempre config.yaml del progetto).
    cases = [
        ("doc_extract.py", ["--trusted-paths"]),
        ("doc_extract.py", ["--save-to", str(root / "output")]),
        ("run_init.py", ["--trusted-paths"]),
        ("episode_config.py", ["--trusted-paths"]),
        ("script_write.py", ["--trusted-paths"]),
        ("script_write.py", ["--text-file", str(root / "testo.txt")]),
        ("script_write.py", ["--document", "report.txt"]),
        ("script_approve.py", ["--session", str(Path(run_dir) / "_script_session.json")]),
        ("script_revise.py", ["--session", str(Path(run_dir) / "_script_session.json")]),
        ("synthesize.py", ["--no-approval-gate"]),
        ("synthesize.py", ["--trusted-paths"]),
        ("estimate.py", ["--trusted-paths"]),
        ("voices_prepare.py", ["--no-approval-gate"]),
        ("voices_prepare.py", ["--trusted-paths"]),
        ("mix.py", ["--no-approval-gate"]),
        ("retake.py", ["--no-approval-gate"]),
        ("run_finalize.py", ["--trusted-paths"]),
        ("run_finalize.py", ["--audio-paths", str(root / "output" / "audio.json")]),
        ("voices_prepare.py", ["--out", str(root / "output" / "voci.json")]),
        ("voices_prepare.py", ["--no-consent-gate"]),
        ("script_approve.py", ["--current"]),
    ]
    for name, extra in cases:
        base = required(name)
        if "--session" in extra:
            base = [a for a in base if a not in ("--run-dir", R)]
        if "--current" in extra:
            base = ["--run-dir", R]
        args = base + extra + with_script(name)
        code, out, _ = run_tool_env(name, args, web=True)
        check(
            f"web: {name} {extra[0]} -> NotAllowedInWebMode",
            code == 1 and out.get("error_type") == "NotAllowedInWebMode" and extra[0] in out["details"]["argomenti"],
            str(out)[:300],
        )
        code, out, _ = run_tool_env(name, args + ["--config", str(config)], web=False)
        check(
            f"fuori dal web: {name} {extra[0]} non e' rifiutato dal controllo",
            out.get("error_type") != "NotAllowedInWebMode" and "ok" in out,
            str(out)[:300],
        )

    # 3) Abbreviazioni e forma --opzione=valore: il controllo guarda i valori
    #    interpretati da argparse, non il testo.
    for args, label in (
        (["report.txt", "--trusted"], "--trusted (abbreviato)"),
        (["report.txt", f"--conf={config}"], "--conf=... (abbreviato con =)"),
        (["report.txt", f"--save-to={root / 'output'}"], "--save-to=..."),
    ):
        code, out, _ = run_tool_env("doc_extract.py", args, web=True)
        check(f"web: {label} -> NotAllowedInWebMode", code == 1 and out.get("error_type") == "NotAllowedInWebMode", str(out)[:300])

    # 4) Accettati fuori dal web, con esito vero.
    code, out, _ = run_tool_env("doc_extract.py", [str(root / "segreto.txt"), "--trusted-paths", "--config", str(config)], web=False)
    check("fuori dal web: --trusted-paths funziona", code == 0 and "SEGRETO" in out["preview"], str(out)[:300])
    code, out, _ = run_tool_env("doc_extract.py", ["report.txt", "--save-to", str(root / "output" / "estratti"), "--config", str(config)], web=False)
    check("fuori dal web: --save-to dentro output/ funziona", code == 0 and out["text_file"], str(out)[:300])
    code, out, _ = run_tool_env("script_approve.py", ["--session", str(Path(run_dir) / "_script_session.json"), "--revoke", "--config", str(config)], web=False)
    check("fuori dal web: --session funziona", code == 0, str(out)[:300])

    # 5) In modalita' web un comando senza argomenti vietati parte normalmente.
    code, out, _ = run_tool_env("doc_extract.py", ["non-esiste-di-sicuro.pdf"], web=True)
    check("web: senza argomenti vietati il controllo lascia passare", out.get("error_type") not in ("NotAllowedInWebMode", None) and code == 1, str(out)[:300])

    # 6) Valori della variabile e controllo di riserva in bootstrap().
    proc = run_python(
        """
        import argparse, os, sys
        sys.path.insert(0, "tools")
        import _common
        for value in ("", "0", "false", "no", "1", "true", "si", "web"):
            os.environ["PODCAST_AGENT_WEB"] = value
            print(repr(value), _common.web_mode())
        os.environ["PODCAST_AGENT_WEB"] = "1"
        try:
            _common.bootstrap(argparse.Namespace(config=None))
            print("BOOTSTRAP PARTITO")
        except _common.NotAllowedInWebMode as exc:
            print("BOOTSTRAP RIFIUTATO", exc.kind)
        """
    )
    lines = proc.stdout.strip().splitlines()
    check(
        "web_mode(): spenta con '', 0, false, no; accesa con qualunque altro valore",
        lines[:8] == ["'' False", "'0' False", "'false' False", "'no' False", "'1' True", "'true' True", "'si' True", "'web' True"],
        proc.stdout + proc.stderr,
    )
    check("bootstrap() senza parse_args() in modalita' web: rifiutato", lines[-1:] == ["BOOTSTRAP RIFIUTATO NotAllowedInWebMode"], proc.stdout + proc.stderr)

    print("\n[sicurezza] argomenti non validi: exit 1 con JSON")
    code, out, _ = run_tool_env("script_show.py", ["--run-dir", R, "--sconosciuto", "--config", str(config)], web=False)
    check("opzione sconosciuta -> exit 1 InvalidArguments (non exit 2 senza JSON)", code == 1 and out.get("error_type") == "InvalidArguments" and out["details"]["messaggio"], str(out))
    code, out, _ = run_tool_env("script_show.py", ["--config", str(config)], web=False)
    check("argomento obbligatorio mancante -> InvalidArguments", code == 1 and out.get("error_type") == "InvalidArguments", str(out))
    code, out, _ = run_tool_env("script_edit.py", ["--run-dir", R, "--turn", "2", "--text", "-Davvero?", "--config", str(config)], web=False)
    check("--text \"-Davvero?\" (valore che sembra un'opzione) -> InvalidArguments", code == 1 and out.get("error_type") == "InvalidArguments" and "--opzione=valore" in out["error"], str(out)[:300])
    code, out, err = run_tool_env("script_edit.py", ["--run-dir", R, "--turn", "2", "--text=-Davvero? E di quanto?", "--config", str(config)], web=False)
    check("--text=-Davvero? funziona", code == 0 and out["turni"][1]["testo"] == "-Davvero? E di quanto?", err[-300:] + str(out)[:300])

    print("\n[sicurezza] percorsi assoluti nei messaggi (stderr compreso)")
    code, out, err = run_tool_env("doc_extract.py", ["report.txt", "--save-to", str(root / "output" / "estratti2"), "--config", str(config)], web=False)
    check("stderr di successo: niente percorso assoluto della cartella output", code == 0 and str(root) not in err and "output/estratti2" in err, err)
    proc = run_python(
        """
        import sys
        from pathlib import Path, PureWindowsPath
        sys.path.insert(0, "tools")
        import _common
        import src.redaction as redaction
        redaction.KNOWN_ROOTS["/srv/podcast/output"] = "output"
        print(_common._redact("Manca /srv/podcast/output/run-a/voci.json."))
        home = str(Path.home())
        print(_common._redact(home + "2/altro"))
        redaction.PROJECT_ROOT = Path(r"C:\\Users\\me\\podcast-agent")
        print(_common._redact(r"[Errno 2] No such file: 'C:\\\\Users\\\\me\\\\podcast-agent\\\\output\\\\r\\\\x.json'"))
        """
    )
    lines = proc.stdout.strip().splitlines()
    check("cartella configurata fuori dal progetto -> il suo nome", lines[:1] == ["Manca output/run-a/voci.json."], proc.stdout + proc.stderr)
    check("una cartella accanto alla home non diventa ~", len(lines) > 1 and "~" not in lines[1], proc.stdout)
    check("percorso Windows in forma repr (barre raddoppiate) tolto", len(lines) > 2 and "Users" not in lines[2] and "output" in lines[2], proc.stdout)


# ----------------------------------------------------------------------
# C2 · Produzione (passi 5 e 6)
# ----------------------------------------------------------------------


def write_c2_config(root: Path, name: str, *, clone_cost: float | None = None, eur: tuple[float, str] | None = None, **options: object) -> Path:
    """Config con il provider finto, le opzioni date e i costi del passo 5."""
    opts = {"seconds_per_100_chars": 2.0, "voices_file": str(root / "fake_voices.json"), "clone_log_file": str(root / "clonazioni.log"), **options}
    text = (root / "config-web.yaml").read_text(encoding="utf-8")
    head, _, rest = text.partition("paths:")
    provider = "active_provider: fake\nproviders:\n  fake:\n    class: tests.fake_provider:FakeProvider\n    options:\n"
    provider += "".join(f"      {key}: {json.dumps(value)}\n" for key, value in opts.items())
    provider += f"    clone_cost_usd: {json.dumps(clone_cost)}\n"
    currency = f"currency:\n  eur_per_usd: {json.dumps(eur[0] if eur else None)}\n  rate_date: {json.dumps(eur[1] if eur else None)}\n"
    config = root / name
    config.write_text(provider + currency + "paths:" + rest, encoding="utf-8")
    return config


SCRIPT_C2 = {
    "titolo": "Produzione di prova",
    "turni": [
        {"speaker": "Mario Rossi", "testo": "Ciao a tutti, oggi parliamo di ricavi."},
        {"speaker": "Giulia Bianchi", "testo": "I ricavi del trimestre sono cresciuti."},
        {"speaker": "Mario Rossi", "testo": "Di quanto, esattamente?"},
        {"speaker": "Giulia Bianchi", "testo": "Del dodici per cento rispetto all'anno scorso."},
        {"speaker": "Mario Rossi", "testo": "E da cosa dipende questa crescita?"},
        {"speaker": "Giulia Bianchi", "testo": "Soprattutto dalle vendite all'estero."},
    ],
}


def approved_run(root: Path, config: Path, title: str, script: dict) -> str:
    """run_init con documento + copione salvato e approvato (versione 1)."""
    code, out, err = tool("run_init.py", "--title", title, "--document", "report.txt", config=config)
    check(f"run '{title}' creata", code == 0, err)
    run_dir = out["run_dir"]
    draft = Path(run_dir) / "bozza.json"
    draft.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    speakers = sorted({t["speaker"] for t in script["turni"]})
    code, out, err = tool("script_save.py", "--run-dir", run_dir, "--script-json", str(draft), "--speakers", *speakers, config=config)
    check(f"copione '{title}' salvato", code == 0, err[-300:] + str(out)[:300])
    code, out, _ = tool("script_approve.py", "--run-dir", run_dir, "--version", "1", "--by", "Utente A", config=config)
    check(f"copione '{title}' approvato", code == 0, str(out)[:300])
    return run_dir


def read_events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def clone_attempts(root: Path) -> list[str]:
    log = root / "clonazioni.log"
    return log.read_text(encoding="utf-8").splitlines() if log.is_file() else []


def test_c2_consent(root: Path, config: Path) -> None:
    print("\n[C2] consenso: voice_meta.py")
    code, out, _ = tool("voice_meta.py", "--name", "mario rossi", config=config)
    check("nome a meno di maiuscole: la voce giusta, consenso mancante", code == 0 and out["voce"] == "Mario Rossi" and out["consenso"]["stato"] == "mancante", str(out))
    code, out, _ = tool("voice_meta.py", "--name", "Mario", config=config)
    check("nome incompleto -> exit 3 AmbiguousName con candidati", code == 3 and out.get("error_type") == "AmbiguousName" and out["details"]["candidates"] == ["Mario Rossi"], str(out))
    code, out, _ = tool("voice_meta.py", "--name", "1", config=config)
    check("indice numerico non vale come nome", code != 0, str(out))
    code, out, _ = tool("voice_meta.py", "--name", "Mario Rossi", "--consent", config=config)
    check("consenso senza --by -> exit 1 ConsentByRequired", code == 1 and out.get("error_type") == "ConsentByRequired", str(out))
    code, out, _ = tool("voice_meta.py", "--name", "Mario Rossi", "--consent", "--by", "Utente A", config=config)
    check("consenso registrato con chi e quando", code == 0 and out["consenso"]["stato"] == "registrato" and out["consenso"]["da"] == "Utente A" and out["consenso"]["il"], str(out))
    meta = json.loads((root / "voci" / "Mario Rossi.json").read_text(encoding="utf-8"))
    check("metadati accanto all'audio, con lo sha256 della registrazione", meta["consenso"]["audio_sha256"].startswith("sha256:") and meta["consenso"]["file"] == "Mario Rossi.wav", str(meta))
    code, out, _ = tool("voices_list.py", config=config)
    check("i .json accanto alle voci non sono voci ne' file scartati", code == 0 and out["count"] == 3 and out["rejected"] == [], str({k: out.get(k) for k in ("count", "rejected")}))
    code, out, _ = tool("voice_meta.py", "--name", "Mario Rossi", "--revoke-consent", "--by", "Utente B", config=config)
    check("revoca: stato revocato, da chi, consenso precedente restituito", code == 0 and out["consenso"]["stato"] == "revocato" and out["consenso"]["revocato_da"] == "Utente B" and out["consenso_revocato"]["da"] == "Utente A", str(out))
    tool("voice_meta.py", "--name", "Mario Rossi", "--consent", "--by", "Utente A", config=config)

    proc = run_python(
        f"""
        import threading
        from pathlib import Path
        from src.voice_meta import record_consent, load_meta
        audio = Path({str(root / "voci" / "Anna Neri.wav")!r})
        threads = [threading.Thread(target=record_consent, args=(audio,), kwargs={{"voice": "Anna Neri", "by": f"U{{i}}"}}) for i in range(8)]
        [t.start() for t in threads]; [t.join() for t in threads]
        print(load_meta(audio)["consenso"]["da"].startswith("U"))
        """
    )
    check("scritture concorrenti dei metadati: file sempre valido", proc.stdout.strip() == "True", proc.stdout + proc.stderr)
    tool("voice_meta.py", "--name", "Anna Neri", "--revoke-consent", "--by", "Utente A", config=config)


def test_c2_estimate_and_produce(root: Path, config: Path) -> str:
    print("\n[C2] passo 5: stima per la run, senza voci.json (G2)")
    run_dir = approved_run(root, config, "Produzione", SCRIPT_C2)
    check("nessun voci.json prima della produzione", not (Path(run_dir) / "voci.json").exists())
    code, out, err = tool("estimate.py", "--run-dir", run_dir, config=config)
    check("estimate --run-dir senza voci.json: exit 0", code == 0, err[-300:] + str(out)[:300])
    by_speaker = {v["speaker"]: v for v in out["voices"]}
    check("entrambe le voci da clonare", {v["azione"] for v in out["voices"]} == {"clone"} and sorted(out["voices_to_clone"]) == ["Giulia Bianchi", "Mario Rossi"], str(out["voices"]))
    check("consenso per voce: Mario registrato, Giulia mancante", by_speaker["Mario Rossi"]["consenso"]["stato"] == "registrato" and by_speaker["Giulia Bianchi"]["consenso"]["stato"] == "mancante")
    check("consent_missing = le voci da confermare", out["consent_missing"] == ["Giulia Bianchi"], str(out["consent_missing"]))
    check("tutte le battute da generare", out["turns_to_generate"] == 6 and out["turns_to_generate_list"] == [1, 2, 3, 4, 5, 6])
    chars = sum(len(t["testo"]) for t in SCRIPT_C2["turni"])
    check("caratteri e costo della sintesi (USD)", out["chars_to_generate"] == chars and out["cost_tts_usd"] == round(chars * 10 / 1_000_000, 4), str(out["cost_tts_usd"]))
    check("costo di clonazione non noto: dichiarato, costo incompleto", out["clone_cost_usd"] is None and out["clone_cost_known"] is False and out["cost_complete"] is False and out["cost_usd"] == out["cost_tts_usd"], str({k: out[k] for k in ("clone_cost_usd", "cost_usd", "cost_complete")}))
    check("senza cambio in config: niente euro", out["cost_eur"] is None and out["eur_per_usd"] is None)
    check("copione approvato, versione 1", out["approved"] is True and out["approved_version"] == 1, str(out.get("approval_problem")))
    check("durata con le chiavi di sempre", "duration_label" in out and "duration_with_music_seconds" in out and out["turns"] == 6)

    priced = write_c2_config(root, "config-c2-prezzi.yaml", clone_cost=0.5, eur=(0.9, "2026-10-01"))
    code, out, _ = tool("estimate.py", "--run-dir", run_dir, config=priced)
    expected = round(chars * 10 / 1_000_000 + 1.0, 4)
    check("con il costo di clonazione: sintesi + 2 clonazioni", out["clone_cost_total_usd"] == 1.0 and out["cost_usd"] == expected and out["cost_complete"] is True, str({k: out[k] for k in ("clone_cost_total_usd", "cost_usd")}))
    check("con il cambio datato: euro e data del tasso", out["cost_eur"] == round((chars * 10 / 1_000_000 + 1.0) * 0.9, 4) and out["eur_rate_date"] == "2026-10-01", str(out["cost_eur"]))

    cast_run = tool("run_init.py", "--title", "Solo cast", config=config)[1]["run_dir"]
    code, out, _ = episode(cast_run, config, "--formato", "conversazione", "--voce", "voce_a=Mario Rossi")
    roles = {r["ruolo"]: r for r in out["ruoli"]}
    check(
        "vista dei ruoli: consenso per voce assegnata, null per ruolo vuoto",
        code == 0 and roles["voce_a"]["consenso"]["stato"] == "registrato" and roles["voce_b"]["consenso"] is None,
        str({k: v.get("consenso") for k, v in roles.items()}),
    )

    print("\n[C2] passo 6: produzione con consenso mancante")
    events = Path(run_dir) / "_eventi.jsonl"
    code, out, _ = tool("produce.py", "--run-dir", run_dir, "--events", config=config)
    check("consenso mancante -> exit 3 ConsentRequired con le voci", code == 3 and out.get("error_type") == "ConsentRequired" and [v["speaker"] for v in out["details"]["voci"]] == ["Giulia Bianchi"] and out["details"]["voci"][0]["stato"] == "mancante", str(out))
    check("nessun tentativo di clonazione, nemmeno per la voce con il consenso", clone_attempts(root) == [], str(clone_attempts(root)))
    check("nessuna voce nel registro", not (root / "voices_registry.json").exists() or "Mario Rossi" not in json.loads((root / "voices_registry.json").read_text(encoding="utf-8")))

    tool("voice_meta.py", "--name", "Giulia Bianchi", "--consent", "--by", "Utente A", config=config)
    wav = root / "voci" / "Giulia Bianchi.wav"
    original = wav.read_bytes()
    make_wav(wav, 1.2, 345)
    code, out, _ = tool("produce.py", "--run-dir", run_dir, config=config)
    check("registrazione sostituita dopo il consenso -> ConsentRequired 'superato'", code == 3 and out["details"]["voci"][0]["stato"] == "superato", str(out))
    wav.write_bytes(original)

    print("\n[C2] passo 6: produzione completa con eventi")
    code, out, err = tool("produce.py", "--run-dir", run_dir, "--events", config=config)
    check("produce: exit 0", code == 0, err[-500:] + str(out)[:300])
    check("due voci clonate, tutte le battute sintetizzate", sorted(out["voices"]["clonate"]) == ["Giulia Bianchi", "Mario Rossi"] and out["tts_calls"] == 6 and out["reused_turns"] == [], str(out)[:400])
    check("una clonazione per voce", sorted(clone_attempts(root)) == ["Giulia Bianchi", "Mario Rossi"], str(clone_attempts(root)))
    check(
        "file finale nella run, percorsi relativi per la UI (output/<run>/...)",
        (root / out["output"]).is_file() and out["output"] == f"output/{Path(run_dir).name}/{out['file']}"
        and out["timings_file"] == f"output/{Path(run_dir).name}/_tempi_turni.json"
        and out["events_file"] == f"output/{Path(run_dir).name}/_eventi.jsonl" and str(root) not in json.dumps(out),
        str({k: out.get(k) for k in ("output", "timings_file", "events_file")}),
    )
    for name in ("voci.json", "_audio_paths.json", "voci_usate.json", "documento_originale.txt", "_tempi_turni.json"):
        check(f"scritto {name}", (Path(run_dir) / name).is_file())
    check("versione approvata nel JSON", out["approved_version"] == 1)
    rows = read_events(events)
    kinds = [r["evento"] for r in rows]
    check("eventi: inizia e finisce", kinds[0] == "iniziata" and kinds[-1] == "finita" and rows[0]["totale"] == 6, str(kinds))
    phases = [(r["evento"], r["fase"]) for r in rows if r["evento"].startswith("fase_")]
    check(
        "fasi nell'ordine dello schermo (senza musiche: niente mix)",
        phases == [("fase_iniziata", "preparazione_voci"), ("fase_finita", "preparazione_voci"), ("fase_iniziata", "sintesi"), ("fase_finita", "sintesi"),
                   ("fase_iniziata", "montaggio"), ("fase_finita", "montaggio"), ("fase_iniziata", "chiusura"), ("fase_finita", "chiusura")],
        str(phases),
    )
    voices_ev = [r for r in rows if r["evento"] == "voce"]
    check("eventi voce: due clonate", sorted((r["speaker"], r["azione"]) for r in voices_ev) == [("Giulia Bianchi", "clonata"), ("Mario Rossi", "clonata")], str(voices_ev))
    prep_done = next(r for r in rows if r["evento"] == "fase_finita" and r["fase"] == "preparazione_voci")
    check("fine preparazione: 0 riusate, 2 clonate", prep_done["riusate"] == 0 and prep_done["clonate"] == 2)
    turns_ev = [r for r in rows if r["evento"] == "battuta"]
    check("una riga per battuta, i/N", [(r["n"], r["totale"]) for r in turns_ev] == [(i, 6) for i in range(1, 7)], str(turns_ev[:2]))
    check("eta: null alla prima, poi un numero", turns_ev[0]["eta_seconds"] is None and all(isinstance(r["eta_seconds"], (int, float)) for r in turns_ev[1:]), str([r["eta_seconds"] for r in turns_ev]))
    check("ogni evento ha l'ora", all(r.get("at") for r in rows))

    code, out, _ = tool("produce.py", "--run-dir", run_dir, "--events", config=config)
    check("rilancio: niente da pagare, voci riusate", code == 0 and out["tts_calls"] == 0 and sorted(out["voices"]["riusate"]) == ["Giulia Bianchi", "Mario Rossi"], str(out)[:300])
    check("rilancio: nessuna nuova clonazione", len(clone_attempts(root)) == 2)
    check("il file eventi riparte da capo a ogni produzione", read_events(events)[0]["evento"] == "iniziata" and sum(1 for r in read_events(events) if r["evento"] == "iniziata") == 1)
    code, out, _ = tool("estimate.py", "--run-dir", run_dir, config=config)
    check("stima dopo la produzione: 0 battute, voci da riusare, costo 0", out["turns_to_generate"] == 0 and {v["azione"] for v in out["voices"]} == {"reuse"} and out["cost_usd"] == 0 and all(v["clonata_il"] for v in out["voices"]), str(out)[:300])

    print("\n[C2] consenso revocato blocca anche il riuso")
    tool("voice_meta.py", "--name", "Mario Rossi", "--revoke-consent", "--by", "Utente B", config=config)
    code, out, _ = tool("produce.py", "--run-dir", run_dir, config=config)
    check("voce gia' clonata ma consenso revocato -> ConsentRequired 'revocato'", code == 3 and out["details"]["voci"] == [{"speaker": "Mario Rossi", "azione": "reuse", "stato": "revocato", "file": "Mario Rossi.wav"}], str(out))
    tool("voice_meta.py", "--name", "Mario Rossi", "--consent", "--by", "Utente A", config=config)

    print("\n[C2] produzione: controlli prima di spendere")
    outside = root / "fuori.jsonl"
    outside.write_text("NON TOCCARE\n", encoding="utf-8")
    saved_events = events.read_bytes()
    events.unlink()
    events.symlink_to(outside)
    code, out, _ = tool("produce.py", "--run-dir", run_dir, "--events", config=config)
    check("_eventi.jsonl link simbolico -> PathNotAllowed, il file puntato resta intatto", code == 1 and out.get("error_type") == "PathNotAllowed" and outside.read_text(encoding="utf-8") == "NON TOCCARE\n", str(out))
    events.unlink()
    events.write_bytes(saved_events)
    proc = subprocess.run(
        [PYTHON, str(PROJECT_ROOT / "tools" / "produce.py"), "--run-dir", run_dir, "--events", str(Path(run_dir) / "conversazione.json"), "--config", str(config)],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), env=clean_env(),
    )
    check("--events non accetta piu' un percorso (il nome lo fissa il tool)", proc.returncode == 1 and json.loads(proc.stdout)["error_type"] == "InvalidArguments" and json.loads((Path(run_dir) / "conversazione.json").read_text(encoding="utf-8"))["turni"], proc.stdout[-300:])
    edited = Path(run_dir) / "conversazione.json"
    saved = edited.read_text(encoding="utf-8")
    payload = json.loads(saved)
    payload["turni"][0]["testo"] = "Testo cambiato a mano."
    edited.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    code, out, _ = tool("produce.py", "--run-dir", run_dir, config=config)
    check("copione diverso da quello approvato -> exit 1 ApprovalRequired", code == 1 and out.get("error_type") == "ApprovalRequired" and out["details"]["approval_problem"], str(out)[:300])
    edited.write_text(saved, encoding="utf-8")

    from src.run_lock import work_lock

    events.write_text("CONTENUTO DELLA PRODUZIONE IN CORSO\n", encoding="utf-8")
    with work_lock(Path(run_dir), "produzione"):
        code, out, _ = tool("produce.py", "--run-dir", run_dir, "--events", config=config)
        check("produzione gia' in corso -> RunBusy", code == 1 and out.get("error_type") == "RunBusy", str(out))
        code, out, _ = tool("script_show.py", "--run-dir", run_dir, config=config)
        check("lo stato 'in generazione' si deriva dal lock", out["in_corso"]["operazione"] == "produzione", str(out.get("in_corso")))
    check("RunBusy non tocca il file eventi della produzione in corso", events.read_text(encoding="utf-8") == "CONTENUTO DELLA PRODUZIONE IN CORSO\n")
    return run_dir


def test_c2_interrupt(root: Path) -> None:
    print("\n[C2] interruzione pulita e ripresa")
    slow = write_c2_config(root, "config-c2-lenta.yaml", tts_delay_seconds=0.6)
    script = {"titolo": "Interrotta", "turni": [
        {"speaker": "Mario Rossi" if i % 2 == 0 else "Giulia Bianchi", "testo": f"Battuta numero {i + 1} di una produzione che verra' interrotta."}
        for i in range(8)
    ]}
    run_dir = approved_run(root, slow, "Interrotta", script)
    events = Path(run_dir) / "_eventi.jsonl"
    env = clean_env()
    proc = subprocess.Popen(
        [PYTHON, str(PROJECT_ROOT / "tools" / "produce.py"), "--run-dir", run_dir, "--events", "--config", str(slow)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT), env=env,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(r["evento"] == "battuta" and r["n"] >= 3 for r in read_events(events)):
            break
        time.sleep(0.1)
    import signal as _signal

    proc.send_signal(_signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=60)
    out = json.loads(stdout) if stdout.strip() else {}
    check("SIGTERM -> exit 1 ProductionInterrupted", proc.returncode == 1 and out.get("error_type") == "ProductionInterrupted", stdout[-400:] + stderr[-400:])
    details = out.get("details") or {}
    check("details: done/total/missing coerenti", details.get("total") == 8 and details["done"] + len(details["missing"]) == 8 and details["missing"] and details["done"] >= 2, str(details))
    check("evento 'interrotta' con gli stessi dati", read_events(events)[-1] == {**read_events(events)[-1], "evento": "interrotta", "done": details.get("done"), "missing": details.get("missing")})
    leftovers = [p.name for p in (Path(run_dir) / "segmenti").rglob("*") if p.name.endswith(".tmp") or p.name.startswith(".")]
    check("nessun segmento scritto a meta'", leftovers == [], str(leftovers))
    code, out2, _ = tool("script_show.py", "--run-dir", run_dir, config=slow)
    check("dopo l'interruzione il lock e' libero (nessuna operazione in corso)", out2["in_corso"] is None)

    code, out2, err = tool("produce.py", "--run-dir", run_dir, config=slow)
    check("ripresa: exit 0", code == 0, err[-300:])
    check("ripresa: paga solo le battute mancanti", out2["generated_turns"] == details.get("missing") and out2["tts_calls"] == len(details.get("missing", [])), str((out2.get("generated_turns"), details.get("missing"))))


def test_c2_clone_errors(root: Path, config: Path) -> None:
    print("\n[C2] errore di clonazione: exit 2, un solo tentativo")
    failing = write_c2_config(root, "config-c2-guasto.yaml", fail_clone=True)
    tool("voice_meta.py", "--name", "Anna Neri", "--consent", "--by", "Utente A", config=config)
    script = {"titolo": "Con Anna", "turni": [
        {"speaker": "Anna Neri", "testo": "Buongiorno, sono Anna."},
        {"speaker": "Mario Rossi", "testo": "Ciao Anna, benvenuta."},
    ]}
    run_dir = approved_run(root, failing, "Con Anna", script)
    events = Path(run_dir) / "_eventi.jsonl"
    before = len(clone_attempts(root))
    code, out, _ = tool("produce.py", "--run-dir", run_dir, "--events", config=failing)
    check("clonazione fallita -> exit 2 (errore del provider)", code == 2 and out.get("error_type") == "ProviderQuotaError", str(out))
    check("un solo tentativo di clonazione", clone_attempts(root)[before:] == ["Anna Neri"], str(clone_attempts(root)[before:]))
    check("evento 'errore' in fondo al file", read_events(events)[-1]["evento"] == "errore" and read_events(events)[-1]["error_type"] == "ProviderQuotaError")
    check("nessuna battuta sintetizzata", not (Path(run_dir) / "segmenti").exists() or not any((Path(run_dir) / "segmenti").glob("*.wav")))

    print("\n[sicurezza C2] clonazione dall'esito incerto: niente nuovo tentativo senza conferma")
    before = len(clone_attempts(root))
    code, out, _ = tool("produce.py", "--run-dir", run_dir, config=config)
    check("rilancio dopo l'errore -> exit 3 CloneOutcomeUnknown", code == 3 and out.get("error_type") == "CloneOutcomeUnknown" and out["details"]["speaker"] == "Anna Neri" and out["details"]["dal"], str(out))
    check("...senza nessun nuovo tentativo", clone_attempts(root)[before:] == [], str(clone_attempts(root)[before:]))
    code, out, _ = tool("estimate.py", "--run-dir", run_dir, config=config)
    check("la stima segnala la clonazione incerta", {v["speaker"]: v["clonazione_incerta"] for v in out["voices"]}["Anna Neri"] is True, str(out["voices"]))
    code, out, _ = tool("voice_meta.py", "--name", "Anna Neri", "--confirm-clone-retry", config=config)
    check("conferma senza --by -> InvalidArguments", code == 1 and out.get("error_type") == "InvalidArguments", str(out))
    code, out, _ = tool("voice_meta.py", "--name", "Anna Neri", "--confirm-clone-retry", "--by", "Utente A", config=config)
    check("conferma: marcatore tolto", code == 0 and out["clonazione_incerta_tolta"] and out["clonazione_incerta"] is None, str(out))

    slow_failing = write_c2_config(root, "config-c2-guasto-lento.yaml", fail_clone=True, clone_delay_seconds=1.5)
    before = len(clone_attempts(root))
    procs = [
        subprocess.Popen(
            [PYTHON, str(PROJECT_ROOT / "tools" / "voices_prepare.py"), "--speakers", "Anna Neri", "--no-approval-gate", "--config", str(slow_failing)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT), env=clean_env(),
        )
        for _ in range(2)
    ]
    outs = [json.loads(p.communicate(timeout=60)[0]) for p in procs]
    check(
        "due processi, la clonazione del primo fallisce: il secondo non ritenta (exit 2 + exit 3)",
        sorted(p.returncode for p in procs) == [2, 3] and sorted(o["error_type"] for o in outs) == ["CloneOutcomeUnknown", "ProviderQuotaError"],
        str(outs),
    )
    check("...un solo tentativo in tutto", clone_attempts(root)[before:] == ["Anna Neri"], str(clone_attempts(root)[before:]))
    tool("voice_meta.py", "--name", "Anna Neri", "--confirm-clone-retry", "--by", "Utente A", config=config)

    print("\n[C2] due produzioni insieme con la stessa voce nuova: una clonazione")
    slow_clone = write_c2_config(root, "config-c2-clone-lento.yaml", clone_delay_seconds=1.5)
    before = len(clone_attempts(root))
    procs = [
        subprocess.Popen(
            [PYTHON, str(PROJECT_ROOT / "tools" / "voices_prepare.py"), "--speakers", "Anna Neri", "--no-approval-gate", "--config", str(slow_clone)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT), env=clean_env(),
        )
        for _ in range(2)
    ]
    outs = [json.loads(p.communicate(timeout=60)[0]) for p in procs]
    check("entrambe exit 0", all(p.returncode == 0 for p in procs), str(outs))
    check("un solo tentativo di clonazione", clone_attempts(root)[before:] == ["Anna Neri"], str(clone_attempts(root)[before:]))
    check("stesso voice_id per entrambe", outs[0]["voice_map"]["Anna Neri"] == outs[1]["voice_map"]["Anna Neri"], str([o["voice_map"] for o in outs]))
    check("una clonata, l'altra riusata", sorted(o["speakers"]["Anna Neri"]["action"] for o in outs) == ["cloned", "reused"], str([o["speakers"] for o in outs]))

    print("\n[C2] voices_prepare: consenso e flag esplicito")
    tool("voice_meta.py", "--name", "Anna Neri", "--revoke-consent", "--by", "Utente A", config=config)
    code, out, _ = tool("voices_prepare.py", "--speakers", "Anna Neri", "--no-approval-gate", config=config)
    check("voices_prepare con consenso revocato -> exit 3 ConsentRequired", code == 3 and out.get("error_type") == "ConsentRequired", str(out))
    code, out, _ = tool("voices_prepare.py", "--speakers", "Anna Neri", "--no-approval-gate", "--no-consent-gate", config=config)
    check("--no-consent-gate (uso locale) passa e lo dichiara", code == 0 and out["consent_gate"] == "bypassed", str(out))
    code, out, _ = tool("voices_prepare.py", "--speakers", "Anna Neri", "Giulia Bianchi", "--dry-run", config=config)
    check("dry-run: dice il consenso senza bloccare", code == 0, str(out))


def test_c2_music(root: Path, config: Path) -> None:
    print("\n[C2] produzione con un'intro: fasi montaggio e mix")
    run_dir = approved_run(root, config, "Con musica", {"titolo": "Con musica", "turni": SCRIPT_C2["turni"][:2]})
    code, out, _ = tool("music_set.py", "--run-dir", run_dir, "--intro", "Piano calmo", config=config)
    check("intro scelta", code == 0, str(out)[:300])
    events = Path(run_dir) / "_eventi.jsonl"
    code, out, err = tool("produce.py", "--run-dir", run_dir, "--events", config=config)
    check("produce con musica: exit 0, mp3", code == 0 and out["format"] == "mp3" and out["musiche_usate_file"], err[-400:] + str(out)[:300])
    phases = [(r["evento"], r["fase"]) for r in read_events(events) if r["evento"].startswith("fase_")]
    check(
        "montaggio finito prima che il mix inizi",
        phases[4:8] == [("fase_iniziata", "montaggio"), ("fase_finita", "montaggio"), ("fase_iniziata", "mix"), ("fase_finita", "mix")],
        str(phases),
    )


def test_c2_consent_during_clone(root: Path, config: Path) -> None:
    """Revisione di sicurezza C2, ALTA-1: il consenso si ricontrolla prima di OGNI clonazione."""
    print("\n[sicurezza C2] consenso ricontrollato prima di ogni clonazione")
    from src.registry import file_hash

    for name, freq in (("Bruno Verdi", 410), ("Carla Neri", 420), ("Dario Gialli", 430), ("Elena Rosa", 440)):
        make_wav(root / "voci" / f"{name}.wav", 1.0, freq)
        tool("voice_meta.py", "--name", name, "--consent", "--by", "Utente A", config=config)
    slow_clone = write_c2_config(root, "config-c2-clone-lento2.yaml", clone_delay_seconds=1.5)

    def start(*speakers: str) -> subprocess.Popen:
        return subprocess.Popen(
            [PYTHON, str(PROJECT_ROOT / "tools" / "voices_prepare.py"), "--speakers", *speakers, "--no-approval-gate", "--config", str(slow_clone)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT), env=clean_env(),
        )

    def wait_for_clone(name: str) -> None:
        deadline = time.monotonic() + 30
        while name not in clone_attempts(root) and time.monotonic() < deadline:
            time.sleep(0.05)

    before = len(clone_attempts(root))
    proc = start("Bruno Verdi", "Carla Neri")
    wait_for_clone("Bruno Verdi")
    tool("voice_meta.py", "--name", "Carla Neri", "--revoke-consent", "--by", "Utente B", config=config)
    out = json.loads(proc.communicate(timeout=60)[0])
    check("revoca durante la clonazione di un'altra voce -> exit 3, stato revocato", proc.returncode == 3 and out["details"]["voci"][0]["speaker"] == "Carla Neri" and out["details"]["voci"][0]["stato"] == "revocato", str(out))
    check("la voce revocata non viene clonata", clone_attempts(root)[before:] == ["Bruno Verdi"], str(clone_attempts(root)[before:]))

    before = len(clone_attempts(root))
    proc = start("Dario Gialli", "Elena Rosa")
    wait_for_clone("Dario Gialli")
    make_wav(root / "voci" / "Elena Rosa.wav", 1.3, 999)
    out = json.loads(proc.communicate(timeout=60)[0])
    check("registrazione sostituita durante un'altra clonazione -> exit 3, stato superato", proc.returncode == 3 and out["details"]["voci"][0]["stato"] == "superato", str(out))
    check("la voce sostituita non viene clonata", clone_attempts(root)[before:] == ["Dario Gialli"], str(clone_attempts(root)[before:]))

    tool("voice_meta.py", "--name", "Elena Rosa", "--consent", "--by", "Utente A", config=config)
    consented = file_hash(root / "voci" / "Elena Rosa.wav")
    proc = start("Elena Rosa")
    wait_for_clone("Elena Rosa")
    make_wav(root / "voci" / "Elena Rosa.wav", 1.1, 777)
    out = json.loads(proc.communicate(timeout=60)[0])
    registry = json.loads((root / "voices_registry.json").read_text(encoding="utf-8"))
    check("file sostituito DURANTE la clonazione: il registro tiene lo sha256 dei byte clonati", proc.returncode == 0 and registry["Elena Rosa"]["audio_hash"] == consented, str(out)[:300])
    plan = run_python(
        f"""
        from src.config import load_config
        from src.voice_prep import plan_voices
        p = plan_voices(load_config({str(config)!r}), ["Elena Rosa"])[0]
        print(p.action, p.consent["stato"])
        """
    )
    check("...quindi la registrazione nuova non riusa il voice_id clonato dalla vecchia", plan.stdout.strip() == "clone superato", plan.stdout + plan.stderr)
    leftovers = [p.name for p in (root / "voci").iterdir() if ".clonazione." in p.name]
    check("nessuna copia temporanea lasciata nella libreria", leftovers == [], str(leftovers))


def test_c2_fixes(root: Path, config: Path) -> None:
    """Revisione di sicurezza C2: MEDIA-3, MEDIA-4, BASSA-2, BASSA-4, BASSA-5."""
    print("\n[sicurezza C2] consenso solo sulla registrazione vista (--expect-sha256)")
    make_wav(root / "voci" / "Fabio Neri.wav", 1.0, 450)
    code, out, _ = tool("voice_meta.py", "--name", "Fabio Neri", config=config)
    seen = out["consenso"]["audio_sha256"]
    check("lo stato del consenso dice lo sha256 della registrazione attuale", seen.startswith("sha256:"), str(out))
    make_wav(root / "voci" / "Fabio Neri.wav", 1.2, 460)
    code, out, _ = tool("voice_meta.py", "--name", "Fabio Neri", "--consent", "--by", "Utente A", "--expect-sha256", seen, config=config)
    check("file cambiato dopo che l'utente l'ha visto -> exit 3 RecordingChanged, niente registrato", code == 3 and out.get("error_type") == "RecordingChanged" and not (root / "voci" / "Fabio Neri.json").exists(), str(out))
    current = tool("voice_meta.py", "--name", "Fabio Neri", config=config)[1]["consenso"]["audio_sha256"]
    code, out, _ = tool("voice_meta.py", "--name", "Fabio Neri", "--consent", "--by", "Utente A", "--expect-sha256", current, config=config)
    check("con lo sha256 giusto: registrato", code == 0 and out["consenso"]["stato"] == "registrato", str(out))
    code, out, _ = run_tool_env("voice_meta.py", ["--name", "Mario Rossi", "--consent", "--by", "U"], web=True)
    check("in modalita' web --consent senza --expect-sha256 -> InvalidArguments", code == 1 and out.get("error_type") == "InvalidArguments", str(out))
    code, out, _ = tool("estimate.py", "--run-dir", tool("run_init.py", "--title", "Hash", config=config)[1]["run_dir"], config=config)
    check("stima di una run senza copione -> ScriptMissing", code == 1 and out.get("error_type") == "ScriptMissing", str(out))

    print("\n[sicurezza C2] metadati scritti male: errore stabile")
    meta = root / "voci" / "Fabio Neri.json"
    good = meta.read_text(encoding="utf-8")
    for bad in ({"consenso": "si"}, {"consenso": {"da": "x"}}, {"consenso_revocato": ["x"]}):
        meta.write_text(json.dumps(bad), encoding="utf-8")
        code, out, _ = tool("voice_meta.py", "--name", "Fabio Neri", config=config)
        check(f"metadati {bad} -> exit 1 VoiceMetaError", code == 1 and out.get("error_type") == "VoiceMetaError", str(out))
    meta.write_text(good, encoding="utf-8")

    print("\n[sicurezza C2] revoca durante la sintesi: ci si ferma")
    slow = write_c2_config(root, "config-c2-lenta2.yaml", tts_delay_seconds=0.6)
    script = {"titolo": "Revocata", "turni": [
        {"speaker": "Mario Rossi" if i % 2 == 0 else "Giulia Bianchi", "testo": f"Battuta {i + 1} di una produzione con una revoca a meta'."}
        for i in range(8)
    ]}
    run_dir = approved_run(root, slow, "Revocata", script)
    events = Path(run_dir) / "_eventi.jsonl"
    proc = subprocess.Popen(
        [PYTHON, str(PROJECT_ROOT / "tools" / "produce.py"), "--run-dir", run_dir, "--events", "--config", str(slow)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(PROJECT_ROOT), env=clean_env(),
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not any(r["evento"] == "battuta" and r["n"] >= 3 for r in read_events(events)):
        time.sleep(0.1)
    tool("voice_meta.py", "--name", "Giulia Bianchi", "--revoke-consent", "--by", "Utente B", config=config)
    stdout, stderr = proc.communicate(timeout=60)
    out = json.loads(stdout) if stdout.strip() else {}
    details = out.get("details") or {}
    check(
        "revoca a sintesi in corso -> exit 3 ConsentRequired 'revocato', con le battute pronte",
        proc.returncode == 3 and out.get("error_type") == "ConsentRequired" and details["voci"][0]["speaker"] == "Giulia Bianchi"
        and details["voci"][0]["stato"] == "revocato" and details["missing"] and details["done"] + len(details["missing"]) == 8,
        stdout[-400:] + stderr[-300:],
    )
    check("evento 'errore' ConsentRequired scritto ancora sotto il lock", read_events(events)[-1]["evento"] == "errore" and read_events(events)[-1]["error_type"] == "ConsentRequired")
    tool("voice_meta.py", "--name", "Giulia Bianchi", "--consent", "--by", "Utente A", config=config)
    code, out, _ = tool("produce.py", "--run-dir", run_dir, config=slow)
    check("riconfermato il consenso, la ripresa paga solo le mancanti", code == 0 and out["generated_turns"] == details.get("missing"), str(out.get("generated_turns")))

    print("\n[sicurezza C2] file finali scritti in modo atomico, ffmpeg con timeout")
    proc = run_python(
        f"""
        from pathlib import Path
        from src.fileio import atomic_output
        target = Path({str(root / "atomico.mp3")!r})
        target.write_bytes(b"EPISODIO BUONO")
        try:
            with atomic_output(target) as partial:
                partial.write_bytes(b"MEZZO EPIS")
                raise KeyboardInterrupt
        except KeyboardInterrupt:
            pass
        print(target.read_bytes().decode(), sorted(p.name for p in target.parent.glob(".atomico*")))
        with atomic_output(target) as partial:
            partial.write_bytes(b"EPISODIO NUOVO")
        print(target.read_bytes().decode())
        import src.music_mixer as mixer
        mixer.FFMPEG_TIMEOUT_SECONDS = 0.001
        try:
            mixer._run_ffmpeg(["-f", "lavfi", "-i", "sine=duration=30", "-f", "null", "-"])
            print("NESSUN TIMEOUT")
        except mixer.MusicMixerError as exc:
            print("TIMEOUT" if "secondi" in str(exc) else str(exc))
        """
    )
    lines = proc.stdout.strip().splitlines()
    check("interruzione a meta' scrittura: resta il file vecchio intero, nessun temporaneo", lines[:1] == ["EPISODIO BUONO []"], proc.stdout + proc.stderr)
    check("scrittura completa: il file nuovo prende il nome finale", lines[1:2] == ["EPISODIO NUOVO"], proc.stdout)
    check("ffmpeg oltre il timeout -> MusicMixerError", lines[2:3] == ["TIMEOUT"], proc.stdout + proc.stderr)
    produced = Path(run_dir)
    check("nella run nessun file parziale rimasto", not [p.name for p in produced.iterdir() if ".parziale" in p.name])

    print("\n[sicurezza C2] voices_prepare e synthesize non si usano dall'interfaccia")
    for name, args in (("voices_prepare.py", ["--speakers", "Mario Rossi", "--run-dir", "x"]), ("synthesize.py", ["--run-dir", "x"])):
        code, out, _ = run_tool_env(name, args, web=True)
        check(f"web: {name} rifiutato per intero", code == 1 and out.get("error_type") in ("NotAllowedInWebMode", "InvalidArguments"), str(out)[:200])
    code, out, _ = run_tool_env("voices_prepare.py", ["--speakers", "Mario Rossi"], web=True)
    check("web: voices_prepare senza argomenti vietati -> NotAllowedInWebMode con il comando", code == 1 and out.get("error_type") == "NotAllowedInWebMode" and out["details"]["comando"] == "voices_prepare.py", str(out))


def test_c2() -> None:
    root = Path(tempfile.mkdtemp(prefix="podcast-web-c2-"))
    setup(root)
    config = write_c2_config(root, "config-c2.yaml")
    test_c2_consent(root, config)
    test_c2_estimate_and_produce(root, config)
    test_c2_music(root, config)
    test_c2_interrupt(root)
    test_c2_clone_errors(root, config)
    test_c2_consent_during_clone(root, config)
    test_c2_fixes(root, config)


# ----------------------------------------------------------------------
# C3 · Episodio e correzioni (passo 7)
# ----------------------------------------------------------------------


def tts_texts(root: Path) -> list[str]:
    log = root / "tts.log"
    return [json.loads(line)["text"] for line in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []


def test_c3() -> None:
    root = Path(tempfile.mkdtemp(prefix="podcast-web-c3-"))
    setup(root)
    (root / "musiche" / "outro").mkdir(parents=True)
    make_wav(root / "musiche" / "outro" / "Chiusura.wav", 0.7, 330)
    config = write_c2_config(root, "config-c3.yaml", tts_log_file=str(root / "tts.log"))
    for name in ("Mario Rossi", "Giulia Bianchi"):
        tool("voice_meta.py", "--name", name, "--consent", "--by", "Utente A", config=config)

    print("\n[C3] G7: il titolo dell'episodio da' il nome al file")
    run_dir = approved_run(root, config, "Episodio C3", SCRIPT_C2)
    tool("music_set.py", "--run-dir", run_dir, "--intro", "Piano calmo", "--outro", "Chiusura", config=config)
    code, out, err = tool("produce.py", "--run-dir", run_dir, config=config)
    check("produzione con intro e outro: exit 0", code == 0, err[-300:])
    check("file dal titolo di episodio.json, non da quello del copione", out["file"] == "episodio-c3.mp3", out.get("file"))
    montage = json.loads((Path(run_dir) / "_montaggio.json").read_text(encoding="utf-8"))
    check("_montaggio.json: file, sha256 del copione, musiche, sezioni", montage["file"] == "episodio-c3.mp3" and montage["copione_sha256"].startswith("sha256:") and montage["musiche"]["intro"]["nome"] == "Piano calmo" and [x["tipo"] for x in montage["sezioni"]] == ["intro", "parlato", "outro"], str(montage)[:400])

    print("\n[C3] episode_info: intestazione, sezioni, trascrizione")
    code, info, err = tool("episode_info.py", "--run-dir", run_dir, config=config)
    check("episode_info: exit 0, pronto, verificabile", code == 0 and info["stato"] == "pronto" and info["verificabile"] and info["motivi"] == [], err[-300:] + str(info)[:300])
    check("intestazione: titolo, durata, voci, data di montaggio", info["titolo"] == "Episodio C3" and info["durata_seconds"] and info["durata_label"] and info["numero_voci"] == 2 and info["montato_il"], str({k: info[k] for k in ("titolo", "durata_seconds", "numero_voci", "montato_il")}))
    check("file finale con percorso relativo", info["file_finale"]["nome"] == "episodio-c3.mp3" and info["file_finale"]["percorso"] == f"output/{Path(run_dir).name}/episodio-c3.mp3", str(info["file_finale"]))
    sections = {x["tipo"]: x for x in info["sezioni"]}
    check("sezioni intro, parlato (6 battute), outro in ordine e dentro il file", sections["intro"]["inizio"] == 0 and sections["intro"]["fine"] < sections["parlato"]["inizio"] < sections["parlato"]["fine"] <= sections["outro"]["inizio"] < sections["outro"]["fine"] <= info["durata_seconds"] + 0.01 and sections["parlato"]["battute"] == 6, str(info["sezioni"]))
    trans = info["trascrizione"]
    check("trascrizione: 6 battute con inizio/fine nel file finale", len(trans) == 6 and all(t["inizio_finale"] is not None and t["fine_finale"] > t["inizio_finale"] for t in trans), str(trans[:1]))
    check("le battute cadono nella sezione parlata, in ordine", trans[0]["inizio_finale"] >= sections["parlato"]["inizio"] and all(a["fine_finale"] <= b["inizio_finale"] for a, b in zip(trans, trans[1:])) and trans[-1]["fine_finale"] <= sections["parlato"]["fine"] + 0.01)
    check("ogni battuta con speaker, testo e testo parlato", all(t["speaker"] and t["testo"] and "testo_parlato" in t for t in trans))
    check("elenco dei file della run, senza lock ne' file nascosti", {"conversazione.json", "episodio-c3.mp3", "_montaggio.json"} <= {f["nome"] for f in info["file"]} and not any(f["nome"].endswith(".lock") or f["nome"].startswith(".") or f["nome"] == "_lavoro.json" for f in info["file"]) and {f["nome"]: f["interno"] for f in info["file"]}["_montaggio.json"] is True, str([f["nome"] for f in info["file"]]))
    middle = (trans[2]["inizio_finale"] + trans[2]["fine_finale"]) / 2
    code, out, _ = tool("turn_at.py", "--run-dir", run_dir, "--at", f"{middle:.2f}", config=config)
    check("turn_at e episode_info usano lo stesso orologio (battuta 3)", code == 0 and out.get("turn") == 3, str(out)[:300])

    print("\n[C3] forma d'onda")
    code, wave_out, err = tool("waveform.py", "--run-dir", run_dir, "--points", "50", config=config)
    check("50 picchi fra 0 e 1, calcolati", code == 0 and len(wave_out["peaks"]) == 50 and all(0 <= v <= 1 for v in wave_out["peaks"]) and max(wave_out["peaks"]) > 0 and wave_out["cached"] is False, err[-300:])
    check("legata allo sha256 del file finale", wave_out["sha256"].startswith("sha256:") and wave_out["file"] == "episodio-c3.mp3" and (Path(run_dir) / "_forma_onda_50.json").is_file())
    code, out, _ = tool("waveform.py", "--run-dir", run_dir, "--points", "50", config=config)
    check("seconda richiesta: dalla cache", out["cached"] is True and out["peaks"] == wave_out["peaks"])
    code, out, _ = tool("waveform.py", "--run-dir", run_dir, "--points", "5", config=config)
    check("punti fuori scala -> exit 1 WaveformError", code == 1 and out.get("error_type") == "WaveformError" and out["details"]["minimo"] == 10, str(out))
    empty = tool("run_init.py", "--title", "Senza file", config=config)[1]["run_dir"]
    (Path(empty) / "conversazione.json").write_text(json.dumps(SCRIPT_C2), encoding="utf-8")
    code, out, _ = tool("waveform.py", "--run-dir", empty, config=config)
    check("episodio non prodotto -> EpisodeNotProduced", code == 1 and out.get("error_type") == "EpisodeNotProduced", str(out))
    code, out, _ = tool("episode_info.py", "--run-dir", empty, config=config)
    check("episode_info di un episodio non prodotto: stato non_prodotto, tempi null", code == 0 and out["stato"] == "non_prodotto" and out["file_finale"] is None and out["trascrizione"][0]["inizio_finale"] is None, str(out)[:300])

    print("\n[C3] retake: indicazioni di resa dalla whitelist (D1)")
    approved_before = tool("script_show.py", "--run-dir", run_dir, config=config)[1]
    script_before = (Path(run_dir) / "conversazione.json").read_text(encoding="utf-8")
    testo3 = SCRIPT_C2["turni"][2]["testo"]
    code, out, _ = tool("retake.py", "--run-dir", run_dir, "--turn", "3", "--takes", "2", "--direction", "Più calmo", "--dry-run", config=config)
    check("dry-run: le tag si pagano (caratteri con la tag)", code == 0 and out["chars"] == 2 * len(f"[calmo] {testo3}") and out["indicazioni"] == ["Più calmo"] and out["cost_usd"] is not None, str(out))
    for args, kind in (
        (["--direction", "Più rauco"], "UnknownDirection"),
        (["--direction", "Pronuncia diversa"], "DirectionNeedsTextEdit"),
        (["--direction", "Più calmo", "--direction", "Più energico"], "ConflictingDirections"),
        (["--takes", "4"], "TooManyTakes"),
    ):
        code, out, _ = tool("retake.py", "--run-dir", run_dir, "--turn", "3", *args, config=config)
        check(f"retake {args} -> exit 1 {kind}", code == 1 and out.get("error_type") == kind, str(out)[:300])
    check("...e nessuna sintesi", len(tts_texts(root)) == 6, str(len(tts_texts(root))))
    code, out, err = tool("retake.py", "--run-dir", run_dir, "--turn", "3", "--takes", "2", "--direction", "Più calmo", "--direction", "Più lento", "--by", "Utente A", config=config)
    check("retake con due indicazioni: exit 0, 2 take nuovi (2 e 3)", code == 0 and out["new_takes"] == [2, 3] and out["tts_calls"] == 2, err[-300:] + str(out)[:300])
    check("al TTS arriva il testo approvato con SOLO le tag della whitelist davanti", tts_texts(root)[-2:] == [f"[calmo] [lentamente] {testo3}"] * 2, str(tts_texts(root)[-2:]))
    check("il copione e l'approvazione non cambiano", (Path(run_dir) / "conversazione.json").read_text(encoding="utf-8") == script_before and tool("script_show.py", "--run-dir", run_dir, config=config)[1]["approved"] is True and approved_before["approved"] is True)
    check("anteprime con percorso relativo", all(t["preview_ui"] and t["preview_ui"].startswith("output/") for t in out["takes"]), str([t.get("preview_ui") for t in out["takes"]]))
    code, info, _ = tool("episode_info.py", "--run-dir", run_dir, config=config)
    check("take generati ma non scelti: l'episodio resta 'pronto'", info["stato"] == "pronto", str(info["motivi"]))

    print("\n[C3] retake --list: tutti i take con l'anteprima in contesto (G5)")
    calls = len(tts_texts(root))
    for preview in (Path(run_dir) / "segmenti" / "anteprime").glob("*"):
        preview.unlink()
    code, out, err = tool("retake.py", "--run-dir", run_dir, "--list", "3", config=config)
    takes = out.get("take_list") or []
    check("take originale + 2, etichette del wireframe", code == 0 and [(t["take"], t["label"]) for t in takes] == [(1, "Take originale"), (2, "Take 1"), (3, "Take 2")], err[-300:] + str(takes)[:300])
    check("anteprima in contesto per ognuno, originale compreso, senza sintesi", all(t["preview"] and Path(t["preview"]).is_file() for t in takes) and len(tts_texts(root)) == calls and out["tts_calls"] == 0)
    check("indicazioni registrate per i take nuovi, scelto l'originale", takes[1]["indicazioni"] == ["Più calmo", "Più lento"] and takes[0]["indicazioni"] == [] and takes[0]["scelto"] and not takes[1]["scelto"], str(takes))
    code, out, _ = tool("retake.py", "--run-dir", run_dir, "--list", "3", "--with-music", config=config)
    check("--list --with-music: nessun sottofondo scelto, nessuna anteprima in piu' ma exit 0", code == 0)

    print("\n[C3] usa il take e rimonta (zero sintesi)")
    code, out, _ = tool("retake.py", "--run-dir", run_dir, "--choose", "3=3", config=config)
    check("scelta del take: exit 0", code == 0 and out["chosen_take"] == 3, str(out))
    code, info, _ = tool("episode_info.py", "--run-dir", run_dir, config=config)
    check("dopo la scelta: 'da_aggiornare' per i take", info["stato"] == "da_aggiornare" and info["motivi"] == ["take"], str(info["motivi"]))
    code, out, err = tool("mix.py", "--run-dir", run_dir, config=config)
    check("mix.py: exit 0, zero sintesi, stesso file", code == 0 and out["tts_calls"] == 0 and out["file"] == "episodio-c3.mp3" and len(tts_texts(root)) == calls, err[-300:])
    code, info, _ = tool("episode_info.py", "--run-dir", run_dir, config=config)
    check("dopo il rimontaggio: di nuovo 'pronto'", info["stato"] == "pronto", str(info["motivi"]))
    code, out, _ = tool("waveform.py", "--run-dir", run_dir, "--points", "50", config=config)
    check("forma d'onda ricalcolata sul file rimontato", out["cached"] is False and out["sha256"] != wave_out["sha256"])

    print("\n[C3] retake con il consenso revocato: niente")
    tool("voice_meta.py", "--name", "Mario Rossi", "--revoke-consent", "--by", "Utente B", config=config)
    code, out, _ = tool("retake.py", "--run-dir", run_dir, "--turn", "3", "--takes", "1", config=config)
    check("consenso revocato -> exit 3 ConsentRequired, nessuna sintesi", code == 3 and out.get("error_type") == "ConsentRequired" and len(tts_texts(root)) == calls, str(out)[:300])
    tool("voice_meta.py", "--name", "Mario Rossi", "--consent", "--by", "Utente A", config=config)

    print("\n[C3] cambia musiche: music_set + mix")
    code, out, _ = tool("music_set.py", "--run-dir", run_dir, "--none", "intro", "outro", config=config)
    code, info, _ = tool("episode_info.py", "--run-dir", run_dir, config=config)
    check("musiche cambiate dopo il montaggio: 'da_aggiornare' per le musiche", info["stato"] == "da_aggiornare" and info["motivi"] == ["musiche"], str(info["motivi"]))
    code, out, err = tool("mix.py", "--run-dir", run_dir, config=config)
    check("G1: mix.py senza musiche rimonta la sola voce, zero sintesi", code == 0 and out["with_music"] is False and out["tts_calls"] == 0 and len(tts_texts(root)) == calls, err[-300:] + str(out)[:200])
    code, info, _ = tool("episode_info.py", "--run-dir", run_dir, config=config)
    check("senza musiche: solo la sezione parlata, da 0", [x["tipo"] for x in info["sezioni"]] == ["parlato"] and info["sezioni"][0]["inizio"] == 0 and info["stato"] == "pronto", str(info["sezioni"]))
    check("musiche_usate.json del mix precedente resta, ma non sposta i tempi", (Path(run_dir) / "musiche_usate.json").is_file() and info["trascrizione"][0]["inizio_finale"] < 1.0, str(info["trascrizione"][0]["inizio_finale"]))
    code, out, _ = tool("turn_at.py", "--run-dir", run_dir, "--at", f"{(info['trascrizione'][1]['inizio_finale'] + info['trascrizione'][1]['fine_finale']) / 2:.2f}", config=config)
    check("turn_at senza musiche: offset dal montaggio, non dal vecchio musiche_usate.json", out.get("turn") == 2, str(out)[:300])
    tool("music_set.py", "--run-dir", run_dir, "--intro", "Piano calmo", config=config)
    code, out, _ = tool("mix.py", "--run-dir", run_dir, config=config)
    code, info, _ = tool("episode_info.py", "--run-dir", run_dir, config=config)
    check("intro rimessa: sezione intro di nuovo presente", [x["tipo"] for x in info["sezioni"]] == ["intro", "parlato"] and info["stato"] == "pronto", str(info["sezioni"]))

    print("\n[C3] correggi il testo -> approva -> produce: paga solo la battuta cambiata")
    code, out, _ = tool("script_edit.py", "--run-dir", run_dir, "--turn", "2", "--text", "I ricavi del trimestre sono cresciuti, e parecchio.", "--expected-version", "1", config=config)
    check("battuta 2 corretta: versione 2", code == 0 and out["version"] == 2, str(out)[:300])
    code, info, _ = tool("episode_info.py", "--run-dir", run_dir, config=config)
    check("copione cambiato: 'da_aggiornare' per il copione, da approvare", info["stato"] == "da_aggiornare" and "copione" in info["motivi"] and info["copione_approvato"] is False, str(info["motivi"]))
    code, out, _ = tool("produce.py", "--run-dir", run_dir, config=config)
    check("senza la nuova approvazione la produzione non parte", code == 1 and out.get("error_type") == "ApprovalRequired")
    tool("script_approve.py", "--run-dir", run_dir, "--version", "2", "--by", "Utente A", config=config)
    code, out, err = tool("estimate.py", "--run-dir", run_dir, config=config)
    check("la stima dice 1 battuta da generare", out["turns_to_generate"] == 1 and out["turns_to_generate_list"] == [2], str(out.get("turns_to_generate_list")))
    code, out, err = tool("produce.py", "--run-dir", run_dir, config=config)
    check("produce: una sola battuta sintetizzata, la 2, stesso file", code == 0 and out["generated_turns"] == [2] and out["tts_calls"] == 1 and out["file"] == "episodio-c3.mp3", err[-300:] + str(out)[:300])
    check("...e al TTS e' arrivato solo il testo nuovo", tts_texts(root)[calls:] == ["I ricavi del trimestre sono cresciuti, e parecchio."], str(tts_texts(root)[calls:]))
    code, info, _ = tool("episode_info.py", "--run-dir", run_dir, config=config)
    check("di nuovo 'pronto', con il take scelto della battuta 3 mantenuto", info["stato"] == "pronto" and tool("retake.py", "--run-dir", run_dir, "--list", "3", config=config)[1]["chosen_take"] == 3, str(info["motivi"]))

    print("\n[C3] il titolo cambiato dopo il montaggio non rinomina il file")
    episode(run_dir, config, "--titolo", "Un altro titolo")
    tool("script_approve.py", "--run-dir", run_dir, "--version", "2", "--by", "Utente A", config=config)
    code, out, _ = tool("mix.py", "--run-dir", run_dir, config=config)
    check("il nome del file resta quello del primo montaggio", code == 0 and out["file"] == "episodio-c3.mp3", str(out)[:200])


# ----------------------------------------------------------------------


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="podcast-web-test-"))
    config = setup(root)
    started = time.monotonic()

    test_atomic_write(root)
    test_lock_timeout(root)
    test_registry_concurrency(root)
    test_structured_needs_user(root, config)
    test_interrupt_contract()
    test_fake_anthropic(root, config)

    test_doc_extract(root, config)
    test_media_duration(root)
    run_dir = test_run_init_and_episode(root, config)
    test_script_write_api(root, config, run_dir)
    test_revise_concurrent_edit(root, config, run_dir)
    test_api_errors(root, config, run_dir)
    test_cast_change_revokes(root, config, run_dir)
    test_docx(root, config)
    test_path_confinement(root, config, run_dir)
    test_legacy_paths(root, config)

    test_document_limits(root)
    test_approve_requires_version(root, config)
    test_ui_fields(root, config)
    test_run_dir_race(root)
    test_web_mode(root, config, run_dir)

    test_c2()
    test_c3()

    from tests.test_tools_cli import PASSED

    print(
        f"\n{len(PASSED)} controlli superati, {len(SKIPPED)} saltati "
        f"({time.monotonic() - started:.1f}s)."
    )
    print(f"Artefatti in: {root}")


if __name__ == "__main__":
    main()
