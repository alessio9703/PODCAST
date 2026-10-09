"""End-to-end: il flusso principale via HTTP, con l'agente VERO e i suoi servizi finti.

Come `test_full_flow(web=True)` di podcast-agent/tests/test_web_integration.py: una
copia temporanea dell'agente (src/, tools/, prompts/, VERSION e i due finti di
tests/, copiati come file, mai importati), con un config.yaml che usa il provider
vocale finto e il client Anthropic finto. La .venv e' un link a quella dell'agente.
Nessuna chiamata reale: l'ambiente dei sottoprocessi non ha chiavi, e la
configurazione punta solo ai finti.

documento caricato -> episodio -> cast -> intro -> stesura in coda -> battuta
modificata -> approvazione -> stima -> consensi -> produzione interrotta alla
battuta 3 -> ripresa (SSE) -> episodio -> download con Range -> Archivio -> registro.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import time
import wave
from pathlib import Path
from typing import Any

import pytest

from app.ingresso.cms import codifica_header
from tests.conftest import client_per, righe_registro

AGENTE = Path(__file__).resolve().parents[2] / "podcast-agent"
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

pytestmark = pytest.mark.skipif(not (AGENTE / ".venv" / "bin" / "python").exists(), reason="manca la .venv dell'agente")


def wav(percorso: Path, secondi: float, freq: int) -> None:
    rate = 22050
    dati = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(int(rate * secondi)))
    with wave.open(str(percorso), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(dati)


def copia_agente(radice: Path) -> Path:
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
    (radice / "musiche" / "intro").mkdir(parents=True)
    wav(radice / "musiche" / "intro" / "Piano calmo.wav", 0.5, 440)
    (radice / "output").mkdir()
    (radice / "anthropic_risposte.json").write_text(json.dumps([{"json": INTERVISTA}], ensure_ascii=False))
    opzioni = {
        "seconds_per_100_chars": 2.0, "voices_file": str(radice / "fake_voices.json"),
        "clone_log_file": str(radice / "clonazioni.log"), "tts_log_file": str(radice / "tts.log"),
        "tts_delay_seconds": 0.2, "hold_file": str(radice / "fermo-sintesi"), "hold_after": 2,
    }
    righe = "".join(f"      {k}: {json.dumps(v)}\n" for k, v in opzioni.items())
    (radice / "config.yaml").write_text(
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


@pytest.fixture
def flusso(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FISH_AUDIO_API_KEY", raising=False)
    radice = copia_agente(tmp_path / "agente")
    ambiente = {
        "AUTH_MODE": "demo", "HOST": "127.0.0.1", "AGENT_DIR": str(radice), "AGENT_VERSIONE_ATTESA": "1.0-web",
        "REGISTRO_FILE": str(tmp_path / "dati" / "registro.jsonl"), "DEMO_UTENTE": "ua", "DEMO_NOME": "Utente A",
    }
    with client_per(ambiente) as client:
        client.headers.update({"X-LIL-Richiesta": "1"})
        yield client, radice, ambiente


class Risposte:
    """Raccoglie ogni JSON ricevuto, per controllare alla fine che nessuno contenga percorsi del server."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.tutte: list[str] = []

    def __call__(self, metodo: str, url: str, atteso: int = 200, **kw: Any) -> dict[str, Any]:
        r = self.client.request(metodo, url, **kw)
        assert r.status_code == atteso, f"{metodo} {url}: {r.status_code} {r.text[:600]}"
        self.tutte.append(r.text)
        return r.json()


def aspetta_lavoro(api: Risposte, lavoro: str, timeout: float = 120) -> dict[str, Any]:
    fine = time.monotonic() + timeout
    while time.monotonic() < fine:
        v = api("GET", f"/api/lavori/{lavoro}")["dati"]
        if v["stato"] in ("finito", "annullato"):
            return v
        time.sleep(0.2)
    raise AssertionError(f"lavoro {lavoro} non finito")


def test_flusso_completo(flusso):
    client, radice, ambiente = flusso
    api = Risposte(client)

    # 1 · Documento: caricamento a corpo grezzo, estrazione, bozza
    r = api("POST", "/api/documenti", 201, content="I ricavi del trimestre sono cresciuti del dodici per cento.".encode(),
            headers={"Content-Type": "application/octet-stream", "X-LIL-Nome-File": codifica_header("report.txt")})
    assert r["dati"]["nome"] == "report.txt"
    assert api("GET", "/api/documenti/report.txt")["dati"]["documenti"][0]["estraibile"] is True
    run = api("POST", "/api/episodi", json={"titolo": "Il trimestre, finale", "documento": "report.txt"})["dati"]["run"]
    assert api("GET", f"/api/episodi/{run}/stato")["dati"]["stato"] == "bozza_cast"

    # 2 · Cast e formato
    dati = api("PATCH", f"/api/episodi/{run}/configurazione", json={
        "formato": "intervista", "durata": "0:05",
        "voci": {"presentatore": PRESENTATORE, "intervistatore": INTERVISTATORE, "ospite": OSPITE},
        "nomi": {"presentatore": PRESENTATORE, "intervistatore": INTERVISTATORE, "ospite": OSPITE},
        "descrizioni": {"intervistatore": "giornalista economico", "ospite": "analista finanziaria"},
    })["dati"]
    assert dati["cast_completo"] is True

    # 3 · Musiche
    api("PUT", f"/api/episodi/{run}/musiche", json={"intro": "Piano calmo", "outro": None, "sottofondo": None})
    assert api("GET", f"/api/episodi/{run}/configurazione")["dati"]["musiche"]["intro"]["nome"] == "Piano calmo"

    # 4 · Copione: stesura in coda (client finto), modifica, approvazione
    lavoro = api("POST", f"/api/episodi/{run}/copione/stesura", 202)["dati"]["lavoro"]
    esito = aspetta_lavoro(api, lavoro)["esito"]
    assert esito["ok"] and esito["dati"]["version"] == 1, esito
    copione = api("GET", f"/api/episodi/{run}/copione")["dati"]
    assert copione["version"] == 1 and copione["approvable"] and copione["versions"][0]["by"] == "Utente A"
    dati = api("PATCH", f"/api/episodi/{run}/copione/battute/4", json={"testo": "E perché, secondo te, è successo?", "versione_attesa": 1})["dati"]
    assert dati["version"] == 2
    r = api("PATCH", f"/api/episodi/{run}/copione/battute/4", 409, json={"testo": "Altro", "versione_attesa": 1})
    assert r["error_type"] == "VersionConflict"
    assert api("POST", f"/api/episodi/{run}/copione/approvazione", json={"versione": 2})["dati"]["approved"] is True

    # 5 · Stima e consenso
    stima = api("GET", f"/api/episodi/{run}/stima")["dati"]
    assert stima["turns_to_generate"] == 5 and len(stima["consent_missing"]) == 3
    for voce in stima["voices"]:
        c = voce["consenso"]
        dati = api("POST", f"/api/voci/{voce['speaker']}/consenso",
                   json={"audio_sha256": c["audio_sha256"], "revisione": c["revisione"]})["dati"]
        assert dati["consenso"]["stato"] == "registrato" and dati["consenso"]["da"] == "Utente A"

    # 6 · Produzione interrotta alla battuta 3, poi ripresa
    fermo = radice / "fermo-sintesi"
    fermo.write_text("fermo")
    lavoro = api("POST", f"/api/episodi/{run}/produzione", 202)["dati"]["lavoro"]
    fine = time.monotonic() + 120
    while time.monotonic() < fine:
        eventi = api("GET", f"/api/episodi/{run}/eventi/elenco")["dati"]["eventi"]
        if any(e["evento"] == "battuta" and e["n"] >= 3 and not e["cached"] for e in eventi):
            break
        time.sleep(0.1)
    assert api("GET", f"/api/episodi/{run}/stato")["dati"]["stato"] == "in_corso"
    assert api("POST", f"/api/episodi/{run}/interruzione")["dati"]["messaggio"] == "Interruzione richiesta."
    time.sleep(0.3)
    fermo.unlink()
    esito = aspetta_lavoro(api, lavoro)["esito"]
    assert esito["error_type"] == "ProductionInterrupted" and esito["details"]["missing"] == [4, 5], esito
    assert api("GET", f"/api/episodi/{run}/stato")["dati"]["stato"] == "interrotta"

    lavoro = api("POST", f"/api/episodi/{run}/produzione", 202)["dati"]["lavoro"]
    with client.stream("GET", f"/api/episodi/{run}/eventi") as r:
        assert r.status_code == 200
        testo = "".join(r.iter_text())
    api.tutte.append(testo)
    assert '"evento": "finita"' in testo and "event: fine" in testo
    esito = aspetta_lavoro(api, lavoro)["esito"]
    assert esito["ok"] and esito["dati"]["generated_turns"] == [4, 5], esito

    # 7 · Episodio, ascolto con Range, download
    info = api("GET", f"/api/episodi/{run}/episodio")["dati"]
    assert info["stato"] == "pronto" and all(t["inizio_finale"] is not None for t in info["trascrizione"])
    nome = info["file_finale"]["nome"]
    r = client.get(f"/api/episodi/{run}/file/{nome}", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100 and r.headers["content-type"] == "audio/mpeg"
    r = client.get(f"/api/episodi/{run}/file/{nome}?scarica=1")
    assert r.status_code == 200 and r.content[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") and "attachment" in r.headers["content-disposition"]
    assert client.get(f"/api/episodi/{run}/file/episodio.json").status_code == 404

    # Archivio
    episodi = api("GET", "/api/episodi?gruppo=pronti")["dati"]["episodi"]
    assert [(e["run"], e["azione"]) for e in episodi] == [(run, "Ascolta")]

    # Registro attivita'
    righe = righe_registro(ambiente)
    azioni = [(r["azione"], r["esito"]) for r in righe]
    for attesa in [("carica_documento", "ok"), ("crea_episodio", "ok"), ("modifica_configurazione", "ok"),
                   ("scegli_musiche", "ok"), ("scrivi_copione", "accodato"), ("scrivi_copione", "ok"),
                   ("modifica_battuta", "ok"), ("modifica_battuta", "VersionConflict"), ("approva_copione", "ok"),
                   ("produci", "accodato"), ("interrompi", "richiesta"), ("produci", "ProductionInterrupted"), ("produci", "ok")]:
        assert attesa in azioni, attesa
    assert azioni.count(("registra_consenso", "ok")) == 3
    accodate = [r for r in righe if r["azione"] == "produci" and r["esito"] == "accodato"]
    assert accodate[0]["costo_stimato_usd"] > 0 and accodate[0]["costo_stimato_eur"] is not None
    assert all(r["utente"] == "ua" for r in righe)

    # Mai percorsi del server verso il browser
    for testo in api.tutte:
        assert str(radice) not in testo and str(Path.home()) not in testo
        assert "voice_id" not in testo
