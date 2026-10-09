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

import time
from pathlib import Path
from typing import Any

import pytest

from app.ingresso.cms import codifica_header
from strumenti.demo import prepara
from tests.conftest import client_per, righe_registro

AGENTE = Path(__file__).resolve().parents[2] / "podcast-agent"
PRESENTATORE, INTERVISTATORE, OSPITE = "Anna Neri", "Mario Rossi", "Giulia Bianchi"

pytestmark = pytest.mark.skipif(not (AGENTE / ".venv" / "bin" / "python").exists(), reason="manca la .venv dell'agente")


def copia_agente(radice: Path) -> Path:
    # La stessa copia della demo (strumenti/demo.py), con il fermo per l'interruzione.
    return prepara(radice, tts_delay=0.2, hold=True, documento=False)


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
