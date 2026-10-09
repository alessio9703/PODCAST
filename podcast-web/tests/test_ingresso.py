import time

import pytest

from app.ingresso.cms import HEADER_FIRMA, calcola_firma, stringa_canonica
from tests.conftest import BASE_URL, LOOPBACK, SEGRETO_VECCHIO, client_per, header_firmati

CORPO_401 = {"ok": False, "esito": "controller", "error_type": "NonAutenticato", "messaggio": "Accesso non autorizzato.", "riprovabile": False}


@pytest.fixture
def cms(ambiente_cms):
    with client_per(ambiente_cms) as c:
        yield c


def test_stringa_canonica_documentata():
    assert stringa_canonica("1700000000", "get", "/api/io?x=1", "mrossi", "autore,approvatore", "Mario%20Rossi") == (
        b"v1\n1700000000\nGET\n/api/io?x=1\nmrossi\nautore,approvatore\nMario%20Rossi")
    firma = calcola_firma(b"k" * 32, "1700000000", "GET", "/api/io", "u", "autore", "U")
    assert firma.startswith("v1=") and len(firma) == 67


def test_firma_valida(cms):
    r = cms.get("/api/io", headers=header_firmati("GET", "/api/io", nome="Zoë D'Amico", ruoli="autore,approvatore"))
    assert r.status_code == 200, r.text
    dati = r.json()["dati"]
    assert dati["utente"] == "mrossi" and dati["nome"] == "Zoë D'Amico"
    assert dati["ruoli"] == ["approvatore", "autore"]
    assert "copione_approva" in dati["permessi"] and "voce_consenso" not in dati["permessi"]


def test_firma_con_query(cms):
    r = cms.get("/api/io?a=1&b=%C3%A8", headers=header_firmati("GET", "/api/io?a=1&b=%C3%A8"))
    assert r.status_code == 200
    r = cms.get("/api/io?a=2", headers=header_firmati("GET", "/api/io?a=1"))
    assert r.status_code == 401


def test_segreto_precedente_accettato(cms):
    r = cms.get("/api/io", headers=header_firmati("GET", "/api/io", segreto=SEGRETO_VECCHIO))
    assert r.status_code == 200


@pytest.mark.parametrize("campo, valore", [
    ("X-LIL-Utente", "altro"),
    ("X-LIL-Ruoli", "amministratore"),
    ("X-LIL-Nome", "Altro%20Nome"),
    ("X-LIL-Timestamp", None),
    (HEADER_FIRMA, "v1=" + "0" * 64),
    (HEADER_FIRMA, "sbagliata"),
])
def test_ogni_campo_alterato_da_401(cms, campo, valore):
    h = header_firmati("GET", "/api/io")
    h[campo] = valore if valore is not None else str(int(h[campo]) + 1)
    r = cms.get("/api/io", headers=h)
    assert r.status_code == 401 and r.json() == CORPO_401


def test_metodo_e_percorso_firmati(cms):
    assert cms.get("/api/io", headers=header_firmati("POST", "/api/io")).status_code == 401
    assert cms.get("/api/io", headers=header_firmati("GET", "/api/altro")).status_code == 401


def test_timestamp_fuori_tolleranza(cms):
    vecchio = int(time.time()) - 61
    assert cms.get("/api/io", headers=header_firmati("GET", "/api/io", timestamp=vecchio)).status_code == 401
    futuro = int(time.time()) + 61
    assert cms.get("/api/io", headers=header_firmati("GET", "/api/io", timestamp=futuro)).status_code == 401
    quasi = int(time.time()) - 50
    assert cms.get("/api/io", headers=header_firmati("GET", "/api/io", timestamp=quasi)).status_code == 200


@pytest.mark.parametrize("manca", ["X-LIL-Utente", "X-LIL-Nome", "X-LIL-Ruoli", "X-LIL-Timestamp", HEADER_FIRMA])
def test_header_mancante(cms, manca):
    h = header_firmati("GET", "/api/io")
    del h[manca]
    r = cms.get("/api/io", headers=h)
    assert r.status_code == 401 and r.json() == CORPO_401


def test_ruoli_sconosciuti_ignorati(cms):
    r = cms.get("/api/io", headers=header_firmati("GET", "/api/io", ruoli="autore,superuser"))
    assert r.json()["dati"]["ruoli"] == ["autore"]


def test_nome_non_utf8_rifiutato(cms):
    h = header_firmati("GET", "/api/io")
    h["X-LIL-Nome"] = "%FF"
    ts, utente, ruoli = h["X-LIL-Timestamp"], h["X-LIL-Utente"], h["X-LIL-Ruoli"]
    from tests.conftest import SEGRETO
    h[HEADER_FIRMA] = calcola_firma(SEGRETO.encode(), ts, "GET", "/api/io", utente, ruoli, "%FF")
    assert cms.get("/api/io", headers=h).status_code == 401


def test_anche_le_pagine_vogliono_la_firma(cms):
    assert cms.get("/").status_code == 401


def test_salute_senza_firma(cms):
    assert cms.get("/salute").status_code == 200


def test_demo_rifiuta_chi_non_e_in_loopback(ambiente):
    from fastapi.testclient import TestClient

    from app.config import carica_config
    from app.main import crea_app

    with TestClient(crea_app(carica_config(ambiente)), base_url=BASE_URL, client=("10.0.0.5", 4000)) as c:
        assert c.get("/api/io").status_code == 401
    with TestClient(crea_app(carica_config(ambiente)), base_url=BASE_URL, client=LOOPBACK) as c:
        assert c.get("/api/io").json()["dati"]["ruoli"] == ["amministratore", "approvatore", "autore", "gestore_voci"]


def test_esempio_di_deploy_md():
    """L'esempio di DEPLOY.md resta corretto."""
    from pathlib import Path
    testo = (Path(__file__).resolve().parents[1] / "DEPLOY.md").read_text(encoding="utf-8")
    firma = calcola_firma(b"0123456789abcdef0123456789abcdef", "1760000000", "GET", "/api/episodi?gruppo=bozze",
                          "mrossi", "autore,approvatore", "Mario%20Rossi")
    assert f"X-LIL-Firma: {firma}" in testo


@pytest.mark.parametrize("host", ["evil.example:8765", "127.0.0.1:9999", "localhost", "", "127.0.0.1.evil.example:8765"])
def test_demo_rifiuta_host_non_di_loopback(client, host):
    """DNS rebinding (A1-sicurezza, MEDIA-1)."""
    assert client.get("/api/io", headers={"Host": host}).status_code == 401
    assert client.get("/api/io", headers={"Host": "localhost:8765"}).status_code == 200
    assert client.get("/api/io", headers={"Host": "[::1]:8765"}).status_code == 200


def test_demo_rifiuta_origin_di_un_altro_sito(client):
    p = "/api/prova/copione"
    assert client.post(p, json={"run": "run-uno"}, headers={"Origin": "http://evil.example:8765"}).status_code == 401
    assert client.post(p, json={"run": "run-uno"}, headers={"Origin": "http://localhost:8765"}).status_code == 200
    assert client.post(p, json={"run": "run-uno"}).status_code == 200
