import time

import pytest

from app.servizi.errori import ErroreController
from app.servizi.gettoni import Gettoni
from tests.conftest import client_per, header_firmati, righe_registro

INCOMPLETO = {"exit": 3, "json": {"ok": False, "error_type": "SpokenNameIncomplete", "error": "Manca il cognome", "exit_code": 3,
                                  "details": {"campo": "nome", "ruolo": "ospite", "nome": "Giulia"}}}
FATTO = {"exit": 0, "json": {"ok": True, "cast_completo": True}}


def test_gettone_monouso_e_legato_all_utente():
    g = Gettoni(300)
    t = g.crea(utente_id="a", azione="x", run="r", error_type="MusicChanged", details={}, parametri={"run": "r"})
    with pytest.raises(ErroreController):
        g.consuma(t.valore, "b")
    assert g.consuma(t.valore, "a").parametri == {"run": "r"}
    with pytest.raises(ErroreController):
        g.consuma(t.valore, "a")


def test_gettone_scaduto(monkeypatch):
    g = Gettoni(30)
    t = g.crea(utente_id="a", azione="x", run=None, error_type="MusicChanged", details={}, parametri={})
    ora = time.monotonic()
    monkeypatch.setattr("app.servizi.gettoni.time.monotonic", lambda: ora + 31)
    with pytest.raises(ErroreController):
        g.consuma(t.valore, "a")


def test_limite_per_utente():
    g = Gettoni(300)
    primo = g.crea(utente_id="a", azione="x", run=None, error_type="E", details={}, parametri={})
    for _ in range(60):
        g.crea(utente_id="a", azione="x", run=None, error_type="E", details={}, parametri={})
    assert len(g) == 50
    with pytest.raises(ErroreController):
        g.consuma(primo.valore, "a")


def test_conferma_aggiunge_il_flag_solo_con_il_gettone(client, agente, ambiente):
    agente.scenario("episode_config.py", [INCOMPLETO, FATTO])
    r = client.post("/api/prova/modifica_configurazione", json={"run": "run-uno", "nomi": {"ospite": "Giulia"}})
    assert r.status_code == 409
    corpo = r.json()
    assert corpo["esito"] == "decisione" and corpo["error_type"] == "SpokenNameIncomplete" and corpo["gettone"]
    assert "--accetta-nome-incompleto" not in agente.chiamate("episode_config.py")[0]["argv"]

    r = client.post(f"/api/conferme/{corpo['gettone']}", json={})
    assert r.status_code == 200, r.text
    argv = agente.chiamate("episode_config.py")[1]["argv"]
    assert argv == ["--run-dir=run-uno", "--nome=ospite=Giulia", "--accetta-nome-incompleto"]
    # monouso
    assert client.post(f"/api/conferme/{corpo['gettone']}", json={}).json()["error_type"] == "GettoneNonValido"
    registro = righe_registro(ambiente)
    assert registro[-1]["dettagli"]["conferme"] == ["--accetta-nome-incompleto"] and registro[-1]["esito"] == "ok"


def test_il_browser_non_puo_cambiare_gli_argomenti(client, agente):
    agente.scenario("episode_config.py", [INCOMPLETO, FATTO])
    gettone = client.post("/api/prova/modifica_configurazione", json={"run": "run-uno", "nomi": {"ospite": "Giulia"}}).json()["gettone"]
    r = client.post(f"/api/conferme/{gettone}", json={"run": "altra", "nomi": {"ospite": "X"}})
    assert r.status_code == 400  # campi in piu' rifiutati
    r = client.post(f"/api/conferme/{gettone}", json={})
    assert r.status_code == 200
    assert agente.chiamate("episode_config.py")[-1]["argv"][1] == "--nome=ospite=Giulia"


def test_gettone_di_un_altro_utente(ambiente_cms, agente):
    agente.scenario("episode_config.py", [INCOMPLETO, FATTO])
    with client_per(ambiente_cms) as c:
        p = "/api/prova/modifica_configurazione"
        h = {**header_firmati("POST", p, utente="mrossi"), "X-LIL-Richiesta": "1"}
        gettone = c.post(p, json={"run": "run-uno", "nomi": {"ospite": "Giulia"}}, headers=h).json()["gettone"]
        pc = f"/api/conferme/{gettone}"
        h2 = {**header_firmati("POST", pc, utente="altro", ruoli="autore"), "X-LIL-Richiesta": "1"}
        assert c.post(pc, json={}, headers=h2).json()["error_type"] == "GettoneNonValido"
        h3 = {**header_firmati("POST", pc, utente="mrossi"), "X-LIL-Richiesta": "1"}
        assert c.post(pc, json={}, headers=h3).status_code == 200


def test_esito_3_senza_flag_non_si_conferma(client, agente):
    agente.scenario("voice_meta.py", {"exit": 3, "json": {"ok": False, "error_type": "AmbiguousName", "error": "ambiguo", "exit_code": 3,
                                                          "details": {"query": "mario", "candidates": ["Mario Rossi", "Mario Bianchi"]}}})
    corpo = client.post("/api/prova/stato_voce", json={"nome": "mario"}).json()
    assert corpo["gettone"] and corpo["details"]["candidates"] == ["Mario Rossi", "Mario Bianchi"]
    r = client.post(f"/api/conferme/{corpo['gettone']}", json={})
    assert r.status_code == 409 and r.json()["error_type"] == "GettoneNonValido"


def test_clonazione_incerta_lancia_ritenta_con_lo_speaker(client, agente, ambiente):
    """Il gettone di CloneOutcomeUnknown (esito di produce) lancia voice_meta --confirm-clone-retry
    sulla voce indicata dall'agente, non su una scelta del browser."""
    ctx = client.app.state.ctx
    from app.ingresso import Utente
    g = ctx.gettoni.crea(utente_id="demo", azione="produci", run="run-uno", error_type="CloneOutcomeUnknown",
                         details={"speaker": "Mario Rossi", "dal": "2026-10-08T10:00:00", "provider": "fish"}, parametri={"run": "run-uno"})
    r = client.post(f"/api/conferme/{g.valore}", json={})
    assert r.status_code == 202, r.text  # lavoro della coda (A2 BASSA-8)
    lavoro = r.json()["dati"]["lavoro"]
    fine = time.monotonic() + 15
    while client.get(f"/api/lavori/{lavoro}").json()["dati"]["stato"] != "finito" and time.monotonic() < fine:
        time.sleep(0.05)
    assert agente.chiamate("voice_meta.py")[-1]["argv"] == ["--name=Mario Rossi", "--confirm-clone-retry", "--by=Utente Demo"]
    riga = righe_registro(ambiente)[-1]
    assert riga["azione"] == "ritenta_clonazione" and riga["esito"] == "ok" and riga["dettagli"]["risposta_a"] == "CloneOutcomeUnknown"


def test_ritenta_clonazione_non_si_lancia_da_una_rotta(client, agente):
    r = client.post("/api/prova/ritenta_clonazione", json={"nome": "Mario Rossi"})
    assert r.status_code == 500 and agente.chiamate("voice_meta.py") == []


def test_scelta_fra_candidati(client, agente):
    ctx = client.app.state.ctx
    det = {"campo": "remove_id", "lezione": "L3", "candidates": [{"id": "L1", "motivo": "m", "testo": "t"}]}
    g = ctx.gettoni.crea(utente_id="demo", azione="lezioni_accetta", run=None, error_type="LessonRemovalRequired",
                         details=det, parametri={"id": "L3"})
    assert client.post(f"/api/conferme/{g.valore}", json={"scelta": "L9"}).status_code == 400
    g = ctx.gettoni.crea(utente_id="demo", azione="lezioni_accetta", run=None, error_type="LessonRemovalRequired",
                         details=det, parametri={"id": "L3"})
    assert client.post(f"/api/conferme/{g.valore}", json={"scelta": "L1"}).status_code == 200
    assert agente.chiamate("lessons.py")[-1]["argv"] == ["--accept=L3", "--remove-id=L1", "--by=Utente Demo"]
