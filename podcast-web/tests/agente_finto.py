"""Uno script che fa la parte di un tool dell'agente, per i test del controller.

Copiato in `<agente finto>/tools/<nome>.py`. Legge `scenari.json` dalla cartella
corrente (che deve essere quella dell'agente) e risponde come indicato:

    {"<tool>.py": {"exit": 0, "json": {...}, "sleep": 0, "stdout": "...grezzo..."}}

oppure una lista di risposte, usate in ordine (l'ultima si ripete). Ogni chiamata
si registra in `chiamate.jsonl` con argomenti, ambiente e cartella corrente.
"""

import json
import os
import sys
import time
from pathlib import Path

tool = Path(sys.argv[0]).name
cwd = Path.cwd()
with open(cwd / "chiamate.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"tool": tool, "argv": sys.argv[1:], "env": dict(os.environ), "cwd": str(cwd)}) + "\n")

scenari = {}
if (cwd / "scenari.json").exists():
    scenari = json.loads((cwd / "scenari.json").read_text(encoding="utf-8"))
risposta = scenari.get(tool)

if isinstance(risposta, list):
    contatori_file = cwd / "contatori.json"
    contatori = json.loads(contatori_file.read_text()) if contatori_file.exists() else {}
    n = contatori.get(tool, 0)
    contatori[tool] = n + 1
    contatori_file.write_text(json.dumps(contatori))
    risposta = risposta[min(n, len(risposta) - 1)]

if risposta is None:
    if tool == "doctor.py":
        web = os.environ.get("PODCAST_AGENT_WEB") == "1"
        risposta = {"exit": 0 if web else 1, "json": {"ok": web, "checks": [
            {"name": "web_mode", "ok": web, "required": True, "detail": "finto"}]}}
    elif tool == "settings_show.py":
        risposta = {"exit": 0, "json": {"ok": True, "impostazioni": {
            "documenti": {"max_bytes": 20971520},
            "audio": {"accepted_extensions": [".wav", ".mp3"]},
            "cartelle": {"documenti": "documenti", "voci": "voci_disponibili", "musiche": "musiche", "archivio": "output"},
        }}}
    else:
        risposta = {"exit": 0, "json": {"ok": True, "tool": tool}}

if risposta.get("sleep"):
    time.sleep(risposta["sleep"])
if "stdout" in risposta:
    sys.stdout.write(risposta["stdout"])
else:
    sys.stdout.write(json.dumps(risposta.get("json", {})))
sys.stderr.write(risposta.get("stderr", ""))
sys.exit(risposta.get("exit", 0))
