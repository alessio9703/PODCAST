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
    code, out, _ = tool("script_approve.py", "--run-dir", run_dir, config=config)
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

    from tests.test_tools_cli import PASSED

    print(
        f"\n{len(PASSED)} controlli superati, {len(SKIPPED)} saltati "
        f"({time.monotonic() - started:.1f}s)."
    )
    print(f"Artefatti in: {root}")


if __name__ == "__main__":
    main()
