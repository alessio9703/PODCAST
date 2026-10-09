"""Prepara una copia dell'agente con i SERVIZI FINTI, per provare l'interfaccia in locale.

    .venv/bin/python -m strumenti.demo [cartella]        (default: ./dati/demo-agente)

La copia contiene src/, tools/, prompts/, VERSION dell'agente e i suoi due finti di
tests/ (provider vocale e client Anthropic): sono COPIATI come file, mai importati. La
.venv e' un link a quella dell'agente. `config.yaml` punta solo ai finti: nessuna
chiamata reale, nessuna chiave. Ci sono tre voci, due brani e un documento d'esempio.

Il client finto non legge il documento: risponde con un copione preparato per
il formato Intervista con Anna Neri (presentatore), Mario Rossi (intervistatore) e
Giulia Bianchi (ospite). Per provare la stesura scegli quel formato e quel cast.
Le risposte si consumano: `--ricarica` ne rimette dieci in coda.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import sys
import wave
from pathlib import Path

RADICE_WEB = Path(__file__).resolve().parent.parent
AGENTE = RADICE_WEB.parent / "podcast-agent"
PRESENTATORE, INTERVISTATORE, OSPITE = "Anna Neri", "Mario Rossi", "Giulia Bianchi"
INTERVISTA = {
    "titolo": "Il trimestre in tre minuti",
    "turni": [
        {"speaker": PRESENTATORE, "testo": "[caldo] Benvenuti. Oggi con noi Mario Rossi, giornalista economico, e Giulia Bianchi, analista finanziaria."},
        {"speaker": INTERVISTATORE, "testo": "[curioso] Giulia, cosa dice il report sui ricavi del trimestre?"},
        {"speaker": OSPITE, "testo": "Che sono cresciuti, e in modo netto rispetto all'anno scorso."},
        {"speaker": INTERVISTATORE, "testo": "E perché è successo, secondo te?"},
        {"speaker": OSPITE, "testo": "[enfasi] Soprattutto per le vendite all'estero, che hanno trainato tutto."},
    ],
}


def wav(percorso: Path, secondi: float, freq: int) -> None:
    rate = 22050
    dati = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(int(rate * secondi)))
    with wave.open(str(percorso), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(dati)


def ricarica_risposte(radice: Path, quante: int = 10) -> None:
    (radice / "anthropic_risposte.json").write_text(
        json.dumps([{"json": INTERVISTA}] * quante, ensure_ascii=False), encoding="utf-8")


def prepara(radice: Path, *, tts_delay: float = 0.5, hold: bool = False, documento: bool = True) -> Path:
    """Crea la copia in `radice` (che non deve esistere) e restituisce la cartella."""
    if radice.exists():
        raise FileExistsError(f"{radice} esiste gia'")
    radice.mkdir(parents=True)
    ignora = shutil.ignore_patterns("__pycache__", "*.pyc")
    for nome in ("src", "tools", "prompts"):
        shutil.copytree(AGENTE / nome, radice / nome, ignore=ignora)
    shutil.copy2(AGENTE / "VERSION", radice / "VERSION")
    (radice / "tests").mkdir()
    for nome in ("__init__.py", "fake_provider.py", "fake_anthropic.py"):
        shutil.copy2(AGENTE / "tests" / nome, radice / "tests" / nome)
    (radice / ".venv").symlink_to(AGENTE / ".venv")
    (radice / "voci").mkdir()
    for nome, freq in ((PRESENTATORE, 260), (INTERVISTATORE, 220), (OSPITE, 330)):
        wav(radice / "voci" / f"{nome}.wav", 1.0, freq)
    (radice / "documenti").mkdir()
    if documento:
        (radice / "documenti" / "report-trimestre.txt").write_text(
            "I ricavi del trimestre sono cresciuti del dodici per cento, soprattutto grazie alle vendite all'estero.\n",
            encoding="utf-8")
    for categoria in ("intro", "outro", "sottofondo"):
        (radice / "musiche" / categoria).mkdir(parents=True)
    wav(radice / "musiche" / "intro" / "Piano calmo.wav", 0.5, 440)
    wav(radice / "musiche" / "outro" / "Chiusura.wav", 0.5, 330)
    (radice / "output").mkdir()
    ricarica_risposte(radice)
    opzioni = {
        "seconds_per_100_chars": 2.0, "voices_file": str(radice / "fake_voices.json"),
        "clone_log_file": str(radice / "clonazioni.log"), "tts_log_file": str(radice / "tts.log"),
        "tts_delay_seconds": tts_delay,
    }
    if hold:
        opzioni.update({"hold_file": str(radice / "fermo-sintesi"), "hold_after": 2})
    righe = "".join(f"      {k}: {json.dumps(v)}\n" for k, v in opzioni.items())
    (radice / "config.yaml").write_text(
        "# Configurazione DEMO: provider vocale e client Anthropic finti, nessuna chiamata reale.\n"
        "active_provider: fake\nproviders:\n  fake:\n    class: tests.fake_provider:FakeProvider\n    options:\n"
        + righe + "    clone_cost_usd: 0.5\n"
        "currency:\n  eur_per_usd: 0.9\n  rate_date: \"2026-10-01\"\n"
        f"paths:\n  documents_dir: {radice / 'documenti'}\n  voices_dir: {radice / 'voci'}\n"
        f"  registry_file: {radice / 'voices_registry.json'}\n  output_dir: {radice / 'output'}\n  music_dir: {radice / 'musiche'}\n"
        "audio:\n  pause_between_turns_ms: 400\n  lead_in_ms: 200\n  lead_out_ms: 400\n  accepted_extensions: ['.wav', '.mp3']\n"
        "script:\n  model: claude-opus-5\n  api_key_env: ANTHROPIC_API_KEY\n  max_tokens: 16000\n  words_per_minute: 150\n"
        "  target_minutes: 3\n  client_factory: tests.fake_anthropic:build\n  client_options:\n"
        f"    responses_file: {radice / 'anthropic_risposte.json'}\n    log_file: {radice / 'anthropic_richieste.jsonl'}\n",
        encoding="utf-8",
    )
    return radice


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cartella", nargs="?", default=str(RADICE_WEB / "dati" / "demo-agente"))
    parser.add_argument("--ricarica", action="store_true", help="Rimette in coda dieci copioni finti")
    args = parser.parse_args()
    radice = Path(args.cartella).resolve()
    if args.ricarica:
        ricarica_risposte(radice)
        print(f"Dieci copioni finti in coda in {radice}")
        return 0
    if not (AGENTE / ".venv" / "bin" / "python").exists():
        print("Manca podcast-agent/.venv: crea prima la venv dell'agente.", file=sys.stderr)
        return 1
    try:
        prepara(radice)
    except FileExistsError:
        print(f"{radice} esiste gia': cancellala per ricominciare da zero, oppure usala cosi'.", file=sys.stderr)
    print(f"""Agente con servizi finti pronto in {radice}

Avvio del controller (demo, solo 127.0.0.1):

  AUTH_MODE=demo AGENT_DIR={radice} AGENT_VERSIONE_ATTESA=1.0-web \\
    REGISTRO_FILE={RADICE_WEB / 'dati' / 'registro-demo' / 'registro.jsonl'} .venv/bin/python -m app

poi apri http://127.0.0.1:8765/""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
