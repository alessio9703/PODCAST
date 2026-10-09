import pytest

from tests.conftest import client_per


def avvio_fallisce(ambiente, **extra):
    with pytest.raises(Exception):
        with client_per(ambiente, **extra):
            pass


def test_versione_diversa_blocca_l_avvio(ambiente, agente):
    (agente.radice / "VERSION").write_text("0.9\n")
    avvio_fallisce(ambiente)


def test_version_mancante_blocca_l_avvio(ambiente, agente):
    (agente.radice / "VERSION").unlink()
    avvio_fallisce(ambiente)


def test_doctor_senza_web_mode_blocca_l_avvio(ambiente, agente):
    agente.scenario("doctor.py", {"exit": 1, "json": {"ok": False, "checks": [{"name": "web_mode", "ok": False, "required": True}]}})
    avvio_fallisce(ambiente)


def test_doctor_con_controllo_obbligatorio_fallito(ambiente, agente):
    agente.scenario("doctor.py", {"exit": 1, "json": {"ok": False, "checks": [
        {"name": "web_mode", "ok": True, "required": True}, {"name": "ffmpeg", "ok": False, "required": True}]}})
    avvio_fallisce(ambiente)


def test_avvio_lancia_doctor_web(client, agente):
    chiamata = agente.chiamate("doctor.py")[0]
    assert chiamata["argv"] == ["--web"] and chiamata["env"]["PODCAST_AGENT_WEB"] == "1"


def test_salute_diventa_503_se_cambia_la_versione(client, agente, monkeypatch):
    assert client.get("/salute").status_code == 200
    (agente.radice / "VERSION").write_text("2.0\n")
    monkeypatch.setattr("app.servizi.avvio.INTERVALLO_SALUTE_S", 0)
    r = client.get("/salute")
    assert r.status_code == 503 and r.json() == {"ok": False}


def test_csrf_e_tipi(client):
    r = client.post("/api/prova/copione", json={"run": "run-uno"}, headers={"X-LIL-Richiesta": ""})
    assert r.status_code == 400
    r = client.post("/api/prova/copione", content="run=run-uno", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    r = client.post("/api/prova/copione", json={"run": "run-uno"})
    assert r.status_code == 200


def test_errori_in_formato_unico(client):
    r = client.get("/api/non-esiste")
    assert r.status_code == 404 and r.json()["error_type"] == "NonTrovato"
    r = client.post("/api/prova/copione", json={"run": "../x"})
    assert r.status_code == 400 and r.json()["details"] == {"campo": "run"}
    r = client.post("/api/prova/copione", json={"run": "manca"})
    assert r.status_code == 404
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_timeout_504(client, agente, monkeypatch):
    from app.agente import tabella
    from dataclasses import replace
    monkeypatch.setitem(tabella.TABELLA, "copione", replace(tabella.TABELLA["copione"], timeout=0.5))
    agente.scenario("script_show.py", {"sleep": 5, "json": {"ok": True}})
    r = client.post("/api/prova/copione", json={"run": "run-uno"})
    assert r.status_code == 504 and r.json()["error_type"] == "Timeout"
