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

    from tests.test_tools_cli import PASSED

    print(
        f"\n{len(PASSED)} controlli superati, {len(SKIPPED)} saltati "
        f"({time.monotonic() - started:.1f}s)."
    )
    print(f"Artefatti in: {root}")


if __name__ == "__main__":
    main()
