"""Test degli entry-point CLI in tools/ — la superficie che usa il subagent.

Verifica exit code e **chiavi JSON**: se una chiave cambia nome, l'agente si
rompe in silenzio. Questo test e' l'unica cosa che se ne accorge.

    python tests/test_tools_cli.py

Non chiama nessuna API a pagamento: il provider vocale e' il FakeProvider, e i
due tool che parlano con Claude vengono saltati se manca ANTHROPIC_API_KEY.
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

from src.script_session import ScriptSession  # noqa: E402
from src.script_writer import PodcastScript  # noqa: E402

PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")
if not Path(PYTHON).exists():
    PYTHON = sys.executable

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


def make_wav(path: Path, seconds: float = 1.0, freq: int = 220) -> None:
    rate = 44100
    data = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(int(rate * seconds))
    )
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data)


def tool(name: str, *args: str, config: Path) -> tuple[int, dict, str]:
    """Esegue un tool e ritorna (exit_code, json_stdout, stderr)."""
    proc = subprocess.run(
        [PYTHON, str(PROJECT_ROOT / "tools" / name), *args, "--config", str(config)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"_raw_stdout": proc.stdout}
    return proc.returncode, payload, proc.stderr


def write_config(
    root: Path,
    *,
    fail_clone: bool = False,
    fingerprint: str = "{}",
    fail_tts_after: str = "null",
    name: str = "config-test.yaml",
) -> Path:
    """config.yaml di test: percorsi assoluti nel tempdir, provider finto."""
    config = root / name
    config.write_text(
        f"""
active_provider: fake
providers:
  fake:
    class: tests.fake_provider:FakeProvider
    options:
      seconds_per_100_chars: 2.0
      fail_clone: {str(fail_clone).lower()}
      # le voci clonate sopravvivono fra un processo e l'altro, come sul provider vero
      voices_file: {root / 'fake_voices.json'}
      fingerprint: {fingerprint}
      fail_tts_after: {fail_tts_after}
paths:
  documents_dir: {root / 'documenti'}
  voices_dir: {root / 'voci'}
  registry_file: {root / 'voices_registry.json'}
  output_dir: {root / 'output'}
audio:
  pause_between_turns_ms: 400
  lead_in_ms: 200
  lead_out_ms: 400
  output_mp3_bitrate: 192
  accepted_extensions: ['.wav', '.mp3']
script:
  model: claude-opus-5
  api_key_env: ANTHROPIC_API_KEY
  max_tokens: 16000
  words_per_minute: 150
  target_minutes: 3
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


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="podcast-tools-test-"))
    (root / "voci").mkdir()
    make_wav(root / "voci" / "Mario Rossi.wav", 1.0, 220)
    make_wav(root / "voci" / "Giulia Bianchi.wav", 1.0, 330)
    doc = root / "documento.md"
    doc.write_text("# Report\n\nI ricavi sono cresciuti del 12%.\n", encoding="utf-8")
    config = write_config(root)

    print("\n[1] doc_extract.py")
    code, out, _ = tool("doc_extract.py", str(doc), "--save-to", str(root), config=config)
    check("exit 0", code == 0, str(out))
    check("chiavi", {"ok", "path", "kind", "chars", "words", "preview", "text_file"} <= set(out))
    check("testo salvato su file", Path(out["text_file"]).is_file())
    text_file = out["text_file"]

    code, out, _ = tool("doc_extract.py", str(root / "inesistente.pdf"), config=config)
    check("documento assente -> exit 1", code == 1 and out["ok"] is False)

    print("\n[2] voices_list.py")
    code, out, _ = tool("voices_list.py", config=config)
    check("exit 0", code == 0)
    check("due voci trovate", out["count"] == 2)
    check("nessuna in cache all'inizio", all(not v["cached"] for v in out["voices"]))

    code, out, _ = tool(
        "voices_list.py", "--resolve", "Mario Rossi", "--resolve", "mario rosi",
        "--resolve", "Sconosciuto", config=config,
    )
    resolved = {r["query"]: r for r in out["resolved"]}
    check("nome esatto -> match", resolved["Mario Rossi"]["match"] == "Mario Rossi")
    check(
        "typo -> match null + candidati (NON indovina)",
        resolved["mario rosi"]["match"] is None
        and resolved["mario rosi"]["candidates"] == ["Mario Rossi"],
        str(resolved["mario rosi"]),
    )
    check(
        "nome inesistente -> nessun candidato",
        resolved["Sconosciuto"]["match"] is None
        and resolved["Sconosciuto"]["candidates"] == [],
    )

    code, out, _ = tool("voices_list.py", "--require", "5", config=config)
    check("require oltre il disponibile -> exit 1", code == 1 and out["ok"] is False)

    print("\n[3] run_init.py")
    code, out, _ = tool("run_init.py", "--title", SCRIPT["titolo"], config=config)
    check("exit 0", code == 0)
    run_dir = Path(out["run_dir"])
    check("cartella creata", run_dir.is_dir())
    check("nome con data e slug", run_dir.name.endswith("_il-trimestre-in-tre-minuti"))
    check("chiavi", {"session_file", "script_file", "slug"} <= set(out))

    script_file = run_dir / "conversazione.json"
    script_file.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")

    print("\n[4] voices_prepare.py")
    code, out, _ = tool(
        "voices_prepare.py", "--speakers", "Mario Rossi", "Giulia Bianchi",
        "--dry-run", config=config,
    )
    check("dry-run exit 0", code == 0)
    check("dry-run non clona", all(v["action"] == "would_clone" for v in out["speakers"].values()))
    check("dry-run non scrive il registro", not (root / "voices_registry.json").exists())

    voices_json = run_dir / "voci.json"
    code, out, _ = tool(
        "voices_prepare.py", "--speakers", "Mario Rossi", "Giulia Bianchi",
        "--out", str(voices_json), "--no-approval-gate", config=config,
    )
    check("exit 0", code == 0)
    check("gate saltato esplicitamente", out["approval_gate"] == "bypassed")
    check("entrambe clonate", sorted(out["newly_cloned"]) == ["Giulia Bianchi", "Mario Rossi"])
    check("voice_map scritta", voices_json.is_file())
    check("audio_paths presente", set(out["audio_paths"]) == {"Mario Rossi", "Giulia Bianchi"})
    check("_audio_paths.json scritto dal tool", out["audio_paths_file"] is not None
          and Path(out["audio_paths_file"]).is_file())
    audio_paths_file = Path(out["audio_paths_file"])

    code, out, _ = tool(
        "voices_prepare.py", "--speakers", "Mario Rossi", "Giulia Bianchi",
        "--out", str(voices_json), "--no-approval-gate", config=config,
    )
    check("seconda volta: riuso dalla cache", sorted(out["reused"]) ==
          ["Giulia Bianchi", "Mario Rossi"] and out["newly_cloned"] == [])

    code, out, _ = tool("voices_prepare.py", "--speakers", "Nessuno",
                        "--no-approval-gate", config=config)
    check("speaker inesistente -> exit 1", code == 1 and out["ok"] is False)
    check("errore indica come risolvere", "voices_list" in out["error"])

    print("\n[5] voices_prepare.py — fallimento del provider")
    root2 = Path(tempfile.mkdtemp(prefix="podcast-tools-fail-"))
    (root2 / "voci").mkdir()
    make_wav(root2 / "voci" / "Mario Rossi.wav")
    config_fail = write_config(root2, fail_clone=True)
    code, out, err = tool("voices_prepare.py", "--speakers", "Mario Rossi",
                          "--no-approval-gate", config=config_fail)
    check("clonazione fallita -> exit 2 (fermati, non ritentare)", code == 2, f"exit={code}")
    check("tipo errore del provider", out["error_type"] == "ProviderQuotaError", str(out))

    print("\n[6] estimate.py")
    code, out, _ = tool("estimate.py", "--script", str(script_file), config=config)
    check("exit 0", code == 0)
    check("chiavi", {"chars", "words", "turns", "duration_seconds", "duration_label",
                     "cost_usd", "cost_basis", "rendered"} <= set(out))
    check("costo calcolato", out["cost_usd"] is not None)
    estimated_seconds = out["duration_seconds"]

    print("\n[7] synthesize.py")
    code, out, err = tool(
        "synthesize.py", "--script", str(script_file), "--voices", str(voices_json),
        "--run-dir", str(run_dir), "--no-approval-gate", config=config,
    )
    check("exit 0", code == 0, err[-400:])
    check("file audio prodotto", Path(out["output"]).is_file())
    check("un segmento per turno", out["segments"] == 3)
    check("durata reale riportata", out["duration_seconds"] > 0)
    check("chiavi", {"output", "format", "duration_seconds", "warnings"} <= set(out))
    real_seconds = out["duration_seconds"]

    incomplete = run_dir / "voci_incomplete.json"
    incomplete.write_text(json.dumps({"Mario Rossi": "fake-0001"}), encoding="utf-8")
    code, out, _ = tool(
        "synthesize.py", "--script", str(script_file), "--voices", str(incomplete),
        "--run-dir", str(run_dir), "--no-approval-gate", config=config,
    )
    check("speaker senza voce -> exit 1 PRIMA di sintetizzare", code == 1)
    check("errore nomina lo speaker scoperto", "Giulia Bianchi" in out["error"])

    print("\n[8] run_finalize.py")
    code, out, _ = tool(
        "run_finalize.py", "--run-dir", str(run_dir), "--document", str(doc),
        "--voices", str(voices_json), "--audio-paths", str(audio_paths_file),
        config=config,
    )
    check("exit 0", code == 0)
    check("voci_usate.json scritto", Path(out["voci_usate"]).is_file())
    voci_usate = json.loads(Path(out["voci_usate"]).read_text())
    check("provider e hash registrati",
          voci_usate["provider"] == "fake"
          and voci_usate["speakers"]["Mario Rossi"]["audio_hash"].startswith("sha256:"))
    check("documento archiviato", (run_dir / "documento_originale.md").is_file())

    print("\n[9] ScriptSession — stato delle revisioni su disco")
    session_path = run_dir / "_script_session.json"
    session = ScriptSession.create(
        document_path=str(doc), document_text="testo", speakers=["Mario Rossi", "Giulia Bianchi"],
        prompt="COMPITO INIZIALE", path=session_path,
    )
    session.record(PodcastScript.from_dict(SCRIPT))
    session.save()
    check("sessione salvata", session_path.is_file())

    reloaded = ScriptSession.load(session_path)
    check("ricaricata da disco (processo diverso)", reloaded.prompt == "COMPITO INIZIALE")
    check("copione conservato", reloaded.script().titolo == SCRIPT["titolo"])

    messages = reloaded.seed_messages("Accorcia l'introduzione")
    check("conversazione ricostruita a 3 messaggi", len(messages) == 3)
    check("ruoli corretti", [m["role"] for m in messages] == ["user", "assistant", "user"])
    check("compito iniziale in testa", messages[0]["content"] == "COMPITO INIZIALE")

    reloaded.record(PodcastScript.from_dict(SCRIPT), instruction="Accorcia l'introduzione")
    reloaded.save()
    again = ScriptSession.load(session_path)
    check("revisione registrata", again.revision_count == 1)
    messages = again.seed_messages("Aggiungi un esempio")
    check(
        "l'istruzione precedente resta nel contesto",
        "Accorcia l'introduzione" in messages[2]["content"],
    )
    check("solo l'ultima bozza in contesto (non tutte)", len(messages) == 3)

    bad = root / "sessione_rotta.json"
    bad.write_text("{}", encoding="utf-8")
    try:
        ScriptSession.load(bad)
        check("sessione invalida deve fallire", False)
    except Exception as exc:
        check("sessione invalida -> errore chiaro", "non e' un file di sessione" in str(exc))

    print("\n[10] script_write.py / script_revise.py")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        skip("script_write end-to-end", "ANTHROPIC_API_KEY assente")
        skip("script_revise end-to-end", "ANTHROPIC_API_KEY assente")
        code, out, _ = tool(
            "script_write.py", "--text-file", text_file, "--speakers", "Mario Rossi",
            "--run-dir", str(root / "inesistente"), config=config,
        )
        check("run-dir inesistente -> exit 1", code == 1 and "run_init" in out["error"])
        code, out, _ = tool(
            "script_revise.py", "--session", str(root / "nope.json"),
            "--instructions", "x", config=config,
        )
        check("sessione mancante -> exit 1", code == 1 and out["ok"] is False)
    else:
        code, out, err = tool(
            "script_write.py", "--text-file", text_file,
            "--speakers", "Mario Rossi", "Giulia Bianchi",
            "--run-dir", str(run_dir), config=config,
        )
        check("script_write exit 0", code == 0, err[-400:])
        check("chiavi", {"titolo", "turni", "problems", "script_file", "session_file",
                         "rendered", "revision_count"} <= set(out))
        code, out, err = tool(
            "script_revise.py", "--session", out["session_file"],
            "--instructions", "Accorcia l'introduzione a una frase", config=config,
        )
        check("script_revise exit 0", code == 0, err[-400:])
        check("contatore revisioni", out["revision_count"] == 1)

    print("\n[11] script_save.py — i controlli che lo schema JSON garantiva")
    code, out, _ = tool("run_init.py", "--title", "prova validazione", config=config)
    save_dir = Path(out["run_dir"])
    bozza = save_dir / "bozza.json"
    speakers = ["Mario Rossi", "Giulia Bianchi"]

    def save(payload, *extra, raw: str | None = None):
        bozza.write_text(raw if raw is not None else json.dumps(payload, ensure_ascii=False),
                         encoding="utf-8")
        return tool("script_save.py", "--run-dir", str(save_dir), "--script-json",
                    str(bozza), "--speakers", *speakers, *extra, config=config)

    code, out, _ = save(None, raw="{non json")
    check("1. JSON malformato -> exit 1", code == 1 and "non e' JSON valido" in out["error"])

    code, out, _ = save([{"speaker": "Mario Rossi", "testo": "x"}])
    check("2. radice non oggetto -> exit 1", code == 1 and "oggetto JSON" in out["error"])

    code, out, _ = save({"turni": [{"speaker": "Mario Rossi", "testo": "x"}]})
    check("3. titolo mancante -> exit 1", code == 1 and "'titolo'" in out["error"])

    code, out, _ = save({"titolo": 42, "turni": [{"speaker": "Mario Rossi", "testo": "x"}]})
    check("4. titolo non stringa -> exit 1", code == 1 and "deve essere una stringa" in out["error"])

    code, out, _ = save({"titolo": "   ", "turni": [{"speaker": "Mario Rossi", "testo": "x"}]})
    check("5. titolo di soli spazi -> exit 1", code == 1 and "soli spazi" in out["error"])

    code, out, _ = save({"titolo": "T"})
    check("6. turni mancante -> exit 1", code == 1 and "'turni'" in out["error"])

    code, out, _ = save({"titolo": "T", "turni": {}})
    check("7. turni non lista -> exit 1", code == 1 and "deve essere una lista" in out["error"])

    code, out, _ = save({"titolo": "T", "turni": []})
    check("8. nessun turno -> exit 1", code == 1 and "nessuna battuta" in out["error"])

    code, out, _ = save({"titolo": "T", "turni": ["battuta"]})
    check("9. turno non oggetto -> exit 1", code == 1 and "deve essere un oggetto" in out["error"])

    code, out, _ = save({"titolo": "T", "turni": [{"speaker": "Mario Rossi"}]})
    check("10. testo mancante nel turno -> exit 1", code == 1 and "manca 'testo'" in out["error"])

    code, out, _ = save({"titolo": "T", "turni": [{"speaker": "Mario Rossi", "testo": "  "}]})
    check("11. testo di soli spazi -> exit 1", code == 1 and "soli spazi" in out["error"])

    code, out, _ = save({"titolo": "T", "turni": [
        {"speaker": "Mario Rossi", "testo": "ciao", "nota": "extra"}]})
    check("12. chiave extra nel turno -> exit 1", code == 1 and "non ammesse" in out["error"])

    code, out, _ = save({"titolo": "T", "turni": [
        {"speaker": "Mario Rossi", "testo": "ciao"}], "durata": 3})
    check("13. chiave extra alla radice -> exit 1", code == 1 and "radice" in out["error"])

    code, out, _ = save({"titolo": "T", "turni": [{"speaker": "Luca Verdi", "testo": "ciao"}]})
    check("14. speaker non ammesso -> exit 1", code == 1 and "non e' fra quelli selezionati" in out["error"])

    code, out, _ = save({"titolo": "T", "turni": [{"speaker": "Mario Rossi", "testo": "ciao"}]})
    check("15. speaker senza battute -> exit 1", code == 1 and "Giulia Bianchi" in out["error"])

    check("nessun salvataggio dopo un errore", not (save_dir / "conversazione.json").exists())

    valid = {"titolo": "Prova valida", "turni": [
        {"speaker": "Mario Rossi", "testo": "Ciao a tutti."},
        {"speaker": "Giulia Bianchi", "testo": "Parliamo del report."},
    ]}
    code, out, _ = save(valid)
    check("copione valido -> exit 0", code == 0, str(out)[:200])
    check("salvato come versione 1", out["version"] == 1)
    check("non ancora approvato", out["approved"] is False)
    check("conversazione.json scritto", (save_dir / "conversazione.json").is_file())

    # Una tag di espressivita' da sola e' voluta: non deve generare avvisi.
    tagged = {"titolo": "Con le tag", "turni": [
        {"speaker": "Mario Rossi", "testo": "Il margine è cresciuto. [esitante] Almeno così dice la tabella."},
        {"speaker": "Giulia Bianchi", "testo": "Parliamo del report."},
    ]}
    code, out, _ = save(tagged, "--instructions", "prova tag")
    check("tag inline -> nessun avviso", code == 0 and out["warnings"] == [],
          str(out.get("warnings")))
    check("tag conservata nel testo salvato",
          "[esitante]" in out["turni"][0]["testo"], out["turni"][0]["testo"])
    check("tag conservata anche nel file",
          "[esitante]" in (save_dir / "conversazione.json").read_text(encoding="utf-8"))

    # L'accento scritto con l'apostrofo peggiora la pronuncia: avviso, non blocco.
    senza_accenti = {"titolo": "Senza accenti", "turni": [
        {"speaker": "Mario Rossi", "testo": "E' la citta' piu' grande, perche' la disponibilita' sara' alta."},
        {"speaker": "Giulia Bianchi", "testo": "Un po' di pazienza: dell'anno scorso non c'è un'idea chiara. Da' retta."},
    ]}
    code, out, _ = save(senza_accenti, "--instructions", "prova accenti")
    check("accento con l'apostrofo -> avviso, non blocco", code == 0, str(out)[:200])
    accent_warnings = [w for w in out["warnings"] if "apostrofo" in w]
    check("il turno senza accenti e' segnalato",
          len(accent_warnings) == 1 and accent_warnings[0].startswith("Turno 1:"),
          str(out["warnings"]))
    check("l'avviso elenca le parole colpevoli",
          all(x in accent_warnings[0] for x in ("E'", "citta'", "piu'")),
          accent_warnings[0])
    check("elisione legittima e imperativi NON segnalati (turno 2 pulito)",
          not any(w.startswith("Turno 2:") for w in out["warnings"]),
          str(out["warnings"]))

    noisy = {"titolo": "Con rumore", "turni": [
        {"speaker": "Mario Rossi", "testo": "Ciao **a tutti** [ride] [enfasi] [sottovoce] e [musica] benvenuti."},
        {"speaker": "Giulia Bianchi", "testo": "Parliamo del report."},
    ]}
    code, out, _ = save(noisy, "--instructions", "prova avvisi")
    check("markdown, regia e troppe tag -> avvisi NON bloccanti",
          code == 0 and len(out["warnings"]) >= 3, str(out.get("warnings")))
    check("salvata come versione 4", out["version"] == 4)
    check("istruzione registrata", out["revision_count"] == 3)

    print("\n[12] Il checkpoint di approvazione")
    voices_j = save_dir / "voci.json"
    voices_j.write_text(json.dumps({"Mario Rossi": "fake-0001", "Giulia Bianchi": "fake-0002"}),
                        encoding="utf-8")

    code, out, _ = tool("synthesize.py", "--script", str(save_dir / "conversazione.json"),
                        "--voices", str(voices_j), "--run-dir", str(save_dir), config=config)
    check("sintesi senza approvazione -> BLOCCATA", code == 1, f"exit={code}")
    check("l'errore spiega cosa fare", "script_approve" in out["error"])

    code, out, _ = tool("voices_prepare.py", "--speakers", *speakers,
                        "--run-dir", str(save_dir), config=config)
    check("clonazione senza approvazione -> BLOCCATA", code == 1)

    code, out, _ = tool("voices_prepare.py", "--speakers", *speakers, "--dry-run",
                        "--run-dir", str(save_dir), config=config)
    check("dry-run non ha bisogno di approvazione", code == 0
          and out["approval_gate"] == "not_needed")

    code, out, _ = tool("script_approve.py", "--run-dir", str(save_dir), config=config)
    check("approvazione registrata", code == 0 and out["approved"] is True)
    check("legata alla versione e allo sha256",
          out["version"] == 4 and out["sha256"].startswith("sha256:"))

    code, out, err = tool("synthesize.py", "--script", str(save_dir / "conversazione.json"),
                          "--voices", str(voices_j), "--run-dir", str(save_dir), config=config)
    check("dopo l'approvazione la sintesi parte", code == 0, err[-300:])
    check("il gate risulta superato", out["approval_gate"] == "passed")

    # il caso che conta: copione riscritto a mano dopo il si'
    tampered = dict(valid)
    tampered["turni"] = valid["turni"] + [{"speaker": "Mario Rossi", "testo": "Battuta aggiunta di nascosto."}]
    (save_dir / "conversazione.json").write_text(json.dumps(tampered, ensure_ascii=False),
                                                 encoding="utf-8")
    code, out, _ = tool("synthesize.py", "--script", str(save_dir / "conversazione.json"),
                        "--voices", str(voices_j), "--run-dir", str(save_dir), config=config)
    check("copione modificato DOPO il si' -> BLOCCATA", code == 1, f"exit={code}")
    check("l'errore dice che e' cambiato", "cambiato DOPO l'approvazione" in out["error"])

    # e anche una revisione regolare fa decadere l'approvazione
    (save_dir / "conversazione.json").write_text(json.dumps(valid, ensure_ascii=False),
                                                 encoding="utf-8")
    code, out, _ = save({"titolo": "Terza stesura", "turni": valid["turni"]},
                        "--instructions", "cambia il titolo")
    check("nuova versione salvata", code == 0 and out["version"] == 5)
    code, out, _ = tool("synthesize.py", "--script", str(save_dir / "conversazione.json"),
                        "--voices", str(voices_j), "--run-dir", str(save_dir), config=config)
    check("revisione dopo il si' -> serve una nuova approvazione", code == 1)

    code, out, _ = tool("script_approve.py", "--run-dir", str(save_dir), "--revoke", config=config)
    check("approvazione ritirabile", code == 0 and out["approved"] is False)

    print("\n[13] prompts/copione.md — fonte unica")
    from src.script_writer import SYSTEM_PROMPT, _FALLBACK_SYSTEM_PROMPT
    prompt_file = PROJECT_ROOT / "prompts" / "copione.md"
    check("il file esiste", prompt_file.is_file())
    check("script_writer legge da li'", SYSTEM_PROMPT == prompt_file.read_text(encoding="utf-8").strip())
    stile = SYSTEM_PROMPT.split("\n## Formato")[0].strip()
    check("stile identico al SYSTEM_PROMPT storico", stile == _FALLBACK_SYSTEM_PROMPT.strip())
    check("sezione Formato presente", "## Formato" in SYSTEM_PROMPT)

    print("\n[14] fish_audio: la FORMA del body di POST /v1/tts")
    # Perche' esiste questo test: speed e volume vanno ANNIDATI dentro
    # `prosody`, gli altri parametri no. Sbagliare l'annidamento non da'
    # errore — l'API ignora i campi sconosciuti — quindi il sintomo sarebbe
    # solo "i parametri non fanno niente", che si scopre a orecchio e a
    # pagamento. Qui si controlla la forma, non che la chiamata non esploda.
    from src.providers.fish_audio_provider import FishAudioProvider

    provider = FishAudioProvider(
        api_key="test-key",
        audio_format="wav",
        tts={
            "normalize": True,
            "speed": 0.95,
            "volume": -2,
            "temperature": 0.7,
            "top_p": 0.7,
            "repetition_penalty": 1.2,
        },
    )
    body = provider.build_tts_body("Perche\u0301 la citta\u0300 e\u0300 cosi\u0300.", "voice-123")

    check("speed e volume sono dentro prosody",
          body.get("prosody") == {"speed": 0.95, "volume": -2}, str(body))
    check("speed e volume NON sono al primo livello",
          "speed" not in body and "volume" not in body, str(body))
    check("normalize, temperature, top_p, repetition_penalty al primo livello",
          body["normalize"] is True and body["temperature"] == 0.7
          and body["top_p"] == 0.7 and body["repetition_penalty"] == 1.2, str(body))
    check("nessun parametro di prosody duplicato al primo livello",
          set(body) == {"text", "reference_id", "format", "latency", "prosody",
                        "normalize", "temperature", "top_p", "repetition_penalty"},
          str(sorted(body)))
    check("testo e voce passati come sono",
          body["reference_id"] == "voice-123" and "citt" in body["text"], str(body)[:200])

    # gli accenti devono arrivare fino ai byte spediti, non fermarsi prima
    import requests as _requests
    prepared = _requests.Request("POST", "https://example.invalid/v1/tts",
                                 json=body).prepare()
    check("il body JSON e' UTF-8 e riporta gli accenti intatti",
          json.loads(prepared.body)["text"] == body["text"], str(prepared.body)[:200])

    # L'impronta di sintesi decide quando la cache dei segmenti va buttata:
    # se il modello o un parametro non ci finiscono dentro, cambiarli non si
    # sente nell'episodio, perche' viene rimontato l'audio di prima.
    impronta = provider.synthesis_fingerprint()
    check("l'impronta contiene il modello e i parametri tts",
          impronta["tts_model"] == "s2.1-pro" and impronta["tts"]["speed"] == 0.95,
          str(impronta))
    check("l'impronta non contiene la API key",
          "test-key" not in json.dumps(impronta), str(impronta))
    diverso = FishAudioProvider(api_key="test-key", audio_format="wav",
                                tts={"normalize": True, "speed": 1.0, "volume": -2,
                                     "temperature": 0.7, "top_p": 0.7,
                                     "repetition_penalty": 1.2})
    check("cambiare un parametro cambia l'impronta",
          diverso.synthesis_fingerprint() != impronta)

    mp3_provider = FishAudioProvider(api_key="k", audio_format="mp3", mp3_bitrate=192)
    mp3_body = mp3_provider.build_tts_body("ciao", "v")
    check("mp3 aggiunge mp3_bitrate", mp3_body["mp3_bitrate"] == 192, str(mp3_body))
    check("senza tts: solo i default, nessun prosody vuoto",
          "prosody" not in mp3_body and mp3_body["normalize"] is True, str(mp3_body))


    print("\n[15] Cache dei segmenti — correggere una battuta senza rigenerare")
    # Il punto di tutta la sezione: correggere una battuta deve costare quella
    # battuta e basta, e soprattutto NON deve rimettere in gioco le battute
    # venute bene, visto che il TTS non e' deterministico.
    root15 = Path(tempfile.mkdtemp(prefix="podcast-cache-test-"))
    (root15 / "voci").mkdir()
    make_wav(root15 / "voci" / "Mario Rossi.wav", 1.0, 220)
    make_wav(root15 / "voci" / "Giulia Bianchi.wav", 1.0, 330)
    cfg = write_config(root15)

    script15 = {
        "titolo": "Correggere una battuta",
        "turni": [
            {"speaker": "Mario Rossi", "testo": "Ciao a tutti e benvenuti."},
            {"speaker": "Giulia Bianchi", "testo": "Oggi parliamo del report trimestrale."},
            {"speaker": "Mario Rossi", "testo": "Esatto."},
            {"speaker": "Giulia Bianchi", "testo": "I ricavi sono cresciuti del dodici per cento."},
            {"speaker": "Mario Rossi", "testo": "Esatto."},
        ],
    }

    code, out, _ = tool("run_init.py", "--title", "Correggere una battuta", config=cfg)
    rd = Path(out["run_dir"])
    bozza15 = rd / "_bozza.json"
    script_file15 = rd / "conversazione.json"
    voices15 = rd / "voci.json"
    segdir = rd / "segmenti"

    def save15(payload, *extra):
        bozza15.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return tool("script_save.py", "--run-dir", str(rd), "--script-json", str(bozza15),
                    "--speakers", "Mario Rossi", "Giulia Bianchi", *extra, config=cfg)

    def approve15():
        return tool("script_approve.py", "--run-dir", str(rd), config=cfg)

    def synth15(*extra, config_file=None):
        return tool("synthesize.py", "--script", str(script_file15), "--voices",
                    str(voices15), "--run-dir", str(rd), *extra,
                    config=config_file or cfg)

    def hashes(directory: Path) -> dict[str, str]:
        import hashlib
        return {
            f.name: hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(directory.glob("*.wav"))
        }

    code, out, _ = save15(script15)
    check("copione salvato", code == 0, str(out)[:200])
    code, out, _ = approve15()
    check("copione approvato", code == 0 and out["approved"] is True)
    code, out, _ = tool("voices_prepare.py", "--speakers", "Mario Rossi", "Giulia Bianchi",
                        "--out", str(voices15), "--run-dir", str(rd), config=cfg)
    check("voci preparate col gate superato", code == 0 and out["approval_gate"] == "passed")

    # -- 1. prima sintesi, poi la stessa sintesi: zero chiamate nuove --------
    code, out, err = synth15()
    check("prima sintesi: exit 0", code == 0, err[-400:])
    check("tutti i turni sintetizzati", out["tts_calls"] == 5
          and out["generated_turns"] == [1, 2, 3, 4, 5], str(out)[:300])
    check("segmenti conservati di default", out["segments_kept"] is True and segdir.is_dir())
    episodio = Path(out["output"])
    prima_mtime = episodio.stat().st_mtime_ns
    prima_hashes = hashes(segdir)
    check("un file per turno", len(prima_hashes) == 5, str(sorted(prima_hashes)))

    indice = json.loads((segdir / "indice.json").read_text(encoding="utf-8"))["turni"]
    check("indice.json descrive ogni turno", len(indice) == 5
          and indice[0]["speaker"] == "Mario Rossi")

    # due turni identici dello stesso speaker NON condividono il file: lo
    # stesso audio ripetuto si sente come un nastro, e un retake sull'uno
    # cambierebbe di nascosto anche l'altro
    check("turni identici -> due chiamate TTS distinte",
          len([t for t in indice if t["testo"] == "Esatto."]) == 2
          and indice[2]["chiave"] != indice[4]["chiave"], str(indice[2:5]))
    check("turni identici -> due file distinti",
          indice[2]["file"] != indice[4]["file"])
    duplicato_hash = prima_hashes[indice[4]["file"]]

    code, out, err = synth15()
    check("seconda sintesi identica: ZERO chiamate TTS", out["tts_calls"] == 0
          and out["reused_turns"] == [1, 2, 3, 4, 5], str(out)[:300])
    check("mp3 comunque rigenerato da zero",
          Path(out["output"]).stat().st_mtime_ns > prima_mtime)
    check("i segmenti non sono stati toccati", hashes(segdir) == prima_hashes)
    check("nessun file .tmp rimasto", list(segdir.glob("*.tmp")) == [])

    # -- 2. cambio il testo di un turno: esattamente 1 chiamata -------------
    corretto = json.loads(json.dumps(script15))
    corretto["turni"][1]["testo"] = "Oggi parliamo dei conti del trimestre."
    code, out, _ = save15(corretto, "--instructions", "riscritta la seconda battuta")
    check("nuova versione del copione", code == 0)
    code, out, _ = synth15()
    check("copione cambiato dopo il si' -> sintesi bloccata", code == 1, f"exit={code}")
    code, out, _ = approve15()
    check("nuova approvazione registrata", code == 0)

    code, out, _ = tool("estimate.py", "--script", str(script_file15),
                        "--run-dir", str(rd), config=cfg)
    check("stima incrementale: un solo turno da generare",
          out["turns_to_generate"] == 1 and out["turns_to_generate_list"] == [2],
          str(out.get("turns_to_generate_list")))
    check("stima incrementale: caratteri del solo turno cambiato",
          out["chars_to_generate"] == len(corretto["turni"][1]["testo"]),
          str(out.get("chars_to_generate")))

    code, out, err = synth15()
    check("testo corretto -> esattamente 1 chiamata TTS",
          out["tts_calls"] == 1 and out["generated_turns"] == [2], str(out)[:300])
    dopo_hashes = hashes(segdir)
    intatti = {k: v for k, v in dopo_hashes.items() if k in prima_hashes}
    check("le altre battute sono byte per byte identiche",
          all(prima_hashes[k] == v for k, v in intatti.items()), "un segmento riusato e' cambiato")
    check("il segmento vecchio resta in cache (si puo' tornare indietro)",
          len(dopo_hashes) == 6, str(sorted(dopo_hashes)))

    # -- 3. cambio un parametro di sintesi: si rigenera tutto ---------------
    cfg_fp = write_config(root15, fingerprint="{temperature: 0.9}",
                          name="config-fingerprint.yaml")
    code, out, err = synth15(config_file=cfg_fp)
    check("impronta di sintesi diversa -> tutti i turni rigenerati",
          out["tts_calls"] == 5 and out["reused_turns"] == [], str(out)[:300])
    code, out, _ = synth15()
    check("tornando all'impronta di prima la cache vale ancora",
          out["tts_calls"] == 0, str(out)[:200])

    # -- 4. sintesi interrotta a meta': si pagano solo i turni mancanti -----
    cfg_fail = write_config(root15, fail_tts_after="2", name="config-guasto.yaml")
    code, out, _ = tool("run_init.py", "--title", "Sintesi interrotta", config=cfg)
    rd2 = Path(out["run_dir"])
    bozza2 = rd2 / "_bozza.json"
    bozza2.write_text(json.dumps(script15, ensure_ascii=False), encoding="utf-8")
    tool("script_save.py", "--run-dir", str(rd2), "--script-json", str(bozza2),
         "--speakers", "Mario Rossi", "Giulia Bianchi", config=cfg)
    tool("script_approve.py", "--run-dir", str(rd2), config=cfg)
    voices2 = rd2 / "voci.json"
    tool("voices_prepare.py", "--speakers", "Mario Rossi", "Giulia Bianchi",
         "--out", str(voices2), "--run-dir", str(rd2), config=cfg)

    code, out, _ = tool("synthesize.py", "--script", str(rd2 / "conversazione.json"),
                        "--voices", str(voices2), "--run-dir", str(rd2), config=cfg_fail)
    check("guasto del provider a meta' sintesi -> exit 1", code == 1, f"exit={code}")
    check("l'errore dice che i segmenti restano riusabili",
          "riusati" in out["error"], out.get("error", "")[:200])
    check("nessun .tmp lasciato dal guasto",
          list((rd2 / "segmenti").glob("*.tmp")) == [])
    code, out, _ = tool("synthesize.py", "--script", str(rd2 / "conversazione.json"),
                        "--voices", str(voices2), "--run-dir", str(rd2), config=cfg)
    check("ripresa dopo il guasto: solo i turni mancanti",
          out["tts_calls"] == 3 and out["reused_turns"] == [1, 2], str(out)[:300])

    # -- 5. turn_at.py: dal minutaggio alla battuta ------------------------
    tempi = json.loads((rd / "_tempi_turni.json").read_text(encoding="utf-8"))["turni"]
    check("la timeline ha inizio e fine per ogni turno",
          all(t.get("inizio") is not None and t.get("fine") is not None for t in tempi))
    for numero, t in enumerate(tempi, start=1):
        meta = (t["inizio"] + t["fine"]) / 2
        code, out, _ = tool("turn_at.py", "--run-dir", str(rd), "--at", f"{meta:.3f}",
                            config=cfg)
        check(f"meta' del turno {numero} -> turno {numero}",
              code == 0 and out["turn"] == numero and out["in_pausa"] is False,
              str(out)[:200])
    pausa = (tempi[0]["fine"] + tempi[1]["inizio"]) / 2
    code, out, _ = tool("turn_at.py", "--run-dir", str(rd), "--at", f"{pausa:.3f}", config=cfg)
    check("istante dentro una pausa -> turno piu' vicino, segnalato come pausa",
          code == 0 and out["in_pausa"] is True and out["turn"] in (1, 2), str(out)[:200])
    code, out, _ = tool("turn_at.py", "--run-dir", str(rd), "--at", "0:02", config=cfg)
    check("il minutaggio si scrive anche come m:ss", code == 0, str(out)[:200])
    code, out, _ = tool("turn_at.py", "--run-dir", str(rd2 / "nessuna"), "--at", "1:00",
                        config=cfg)
    check("cartella senza episodio -> exit 1", code == 1 and out["ok"] is False)

    # -- 6. retake: nuovi take, anteprime, scelta --------------------------
    code, out, _ = tool("retake.py", "--run-dir", str(rd), "--turn", "3", "--takes", "2",
                        "--dry-run", config=cfg)
    check("dry-run del retake: costo dichiarato, nessuna chiamata",
          code == 0 and out["chars"] == len("Esatto.") * 2 and out["cost_usd"] is not None
          and out["approval_gate"] == "not_needed", str(out)[:250])
    cache_prima = hashes(segdir)

    code, out, err = tool("retake.py", "--run-dir", str(rd), "--turn", "3", "--takes", "2",
                          config=cfg)
    check("retake: exit 0 col gate superato",
          code == 0 and out["approval_gate"] == "passed", err[-300:])
    check("retake: 2 chiamate TTS", out["tts_calls"] == 2 and out["new_takes"] == [2, 3],
          str(out)[:250])
    check("retake: il take 1 resta intatto",
          hashes(segdir)[indice[2]["file"]] == cache_prima[indice[2]["file"]])
    check("retake: non ha toccato l'altro turno con lo stesso testo",
          hashes(segdir)[indice[4]["file"]] == duplicato_hash)
    check("retake: 2 anteprime montate", len(out["previews"]) == 2
          and all(Path(p).is_file() for p in out["previews"]), str(out["previews"]))
    check("retake: il take NON viene scelto dal tool", out["chosen_take"] == 1)

    code, out, _ = tool("retake.py", "--run-dir", str(rd), "--list", "3", config=cfg)
    check("--list elenca i take e quello scelto",
          out["takes"] == [1, 2, 3] and out["chosen_take"] == 1, str(out)[:200])

    episodio_prima = episodio.read_bytes()
    code, out, _ = tool("retake.py", "--run-dir", str(rd), "--choose", "3=3", config=cfg)
    check("--choose registra la scelta senza chiamare il provider",
          code == 0 and out["chosen_take"] == 3 and out["tts_calls"] == 0, str(out)[:200])
    code, out, err = synth15()
    check("dopo la scelta il rimontaggio non costa niente", out["tts_calls"] == 0, str(out)[:200])
    indice_dopo = json.loads((segdir / "indice.json").read_text(encoding="utf-8"))["turni"]
    check("l'episodio monta il take scelto",
          indice_dopo[2]["take"] == 3 and indice_dopo[2]["file"].endswith("_t3.wav"),
          str(indice_dopo[2]))
    check("e l'audio finale e' cambiato di conseguenza",
          Path(out["output"]).read_bytes() != episodio_prima)

    code, out, _ = tool("retake.py", "--run-dir", str(rd), "--choose", "3=9", config=cfg)
    check("scelta di un take inesistente -> exit 1", code == 1 and "non esiste" in out["error"])

    # -- 7. i due blocchi del retake ---------------------------------------
    import shutil as _shutil
    rd_noapp = rd.parent / "senza-approvazione"
    _shutil.copytree(rd, rd_noapp)
    tool("script_approve.py", "--run-dir", str(rd_noapp), "--revoke", config=cfg)
    code, out, _ = tool("retake.py", "--run-dir", str(rd_noapp), "--turn", "1", "--takes", "1",
                        config=cfg)
    check("retake senza approvazione -> BLOCCATO", code == 1, f"exit={code}")
    check("l'errore spiega cosa fare", "script_approve" in out["error"], out.get("error", "")[:200])

    rd_badvoice = rd.parent / "voce-sparita"
    _shutil.copytree(rd, rd_badvoice)
    (rd_badvoice / "voci.json").write_text(
        json.dumps({"Mario Rossi": "fake-9999", "Giulia Bianchi": "fake-9998"}),
        encoding="utf-8")
    voci_provider_prima = (root15 / "fake_voices.json").read_text(encoding="utf-8")
    code, out, _ = tool("retake.py", "--run-dir", str(rd_badvoice), "--turn", "1",
                        "--takes", "1", config=cfg)
    check("voice_id sparito dal provider -> exit 1", code == 1, f"exit={code}")
    check("e l'errore dice esplicitamente che NON riclona",
          "NON riclono" in out["error"], out.get("error", "")[:200])
    check("nessuna voce clonata di nascosto",
          (root15 / "fake_voices.json").read_text(encoding="utf-8") == voci_provider_prima)

    # -- 8. --purge-segments -----------------------------------------------
    code, out, _ = synth15("--purge-segments")
    check("con --purge-segments i segmenti spariscono",
          out["segments_kept"] is False and not segdir.exists(), str(out)[:200])
    check("l'episodio c'e' lo stesso", Path(out["output"]).is_file())
    code, out, _ = synth15()
    check("dopo il purge si ripaga tutto", out["tts_calls"] == 5, str(out)[:200])

    print(f"\nStima {estimated_seconds}s vs durata reale {real_seconds}s "
          "(diverse: il provider finto non parla a 150 parole/minuto)")
    print(f"\n{len(PASSED)} controlli superati, {len(SKIPPED)} saltati.")
    print(f"Artefatti in: {root}")


if __name__ == "__main__":
    main()
