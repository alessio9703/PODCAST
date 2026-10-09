"""K1-K12: caricamenti e download (A2-piano.md)."""

import json
import os
from pathlib import Path

import pytest

from app.file.download import intervallo
from app.file.nomi import chiave_nome
from app.ingresso.cms import codifica_header
from tests.conftest import client_per, header_firmati, righe_registro

PDF = b"%PDF-1.7\n" + b"x" * 100


def carica(client, nome, dati, gettone=None, **extra_headers):
    h = {"Content-Type": "application/octet-stream", "X-LIL-Nome-File": codifica_header(nome), **extra_headers}
    if gettone:
        h["X-LIL-Gettone"] = gettone
    return client.post("/api/documenti", content=dati, headers=h)


def residui(agente):
    return sorted(p.name for p in (agente.radice / "documenti").iterdir())


# --- K1-K2: nome ed estensione ---------------------------------------------

@pytest.mark.parametrize("nome", [
    "../evil.pdf", "a/b.pdf", "a\\b.pdf", ".nascosto.pdf", "-x.pdf", " x.pdf", "x.pdf ", "x\x00.pdf",
    "x‮.pdf", "x​.pdf", "a" * 151 + ".pdf", "x.json", "x.lock", "x.tmp", "x.exe", ".pdf", "senza",
    "x..pdf",
])
def test_nomi_rifiutati(client, agente, nome):
    r = carica(client, nome, PDF)
    assert r.status_code == 415 and r.json()["error_type"] == "FileNonAmmesso", (nome, r.text)
    assert residui(agente) == []


def test_nome_non_utf8_e_mancante(client, agente):
    r = client.post("/api/documenti", content=PDF, headers={"Content-Type": "application/octet-stream", "X-LIL-Nome-File": "%FF.pdf"})
    assert r.status_code == 415
    r = client.post("/api/documenti", content=PDF, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 415


def test_caricamento_riuscito_nfc_e_registro(client, agente, ambiente):
    nome_nfd = "Relazione è.pdf"  # è scomposta
    r = carica(client, nome_nfd, PDF)
    assert r.status_code == 201, r.text
    dati = r.json()["dati"]
    assert dati["nome"] == "Relazione è.pdf" and dati["dimensione_bytes"] == len(PDF) and dati["sha256"].startswith("sha256:")
    assert residui(agente) == ["Relazione è.pdf"]
    f = agente.radice / "documenti" / "Relazione è.pdf"
    assert f.read_bytes() == PDF and (f.stat().st_mode & 0o077) == 0
    riga = righe_registro(ambiente)[-1]
    assert riga["azione"] == "carica_documento" and riga["dettagli"]["nome"] == "Relazione è.pdf"


def test_multipart_e_form_rifiutati(client):
    r = client.post("/api/documenti", files={"f": ("x.pdf", PDF)})
    assert r.status_code == 415


# --- K3: firma --------------------------------------------------------------

@pytest.mark.parametrize("nome, dati", [
    ("x.pdf", b"ffconcat version 1.0\nfile /etc/passwd\n"),
    ("x.docx", b"%PDF-1.4 non e' un docx"),
    ("x.pdf", b"%PD"),
    ("x.txt", b"ciao\x00mondo"),
    ("x.md", "ciao".encode("utf-16")),
    ("x.txt", b"ok" + b"\xff\xfe"),
    ("x.txt", b""),
])
def test_firma_dei_primi_byte(client, agente, nome, dati):
    r = carica(client, nome, dati)
    assert r.status_code == 415, r.text
    assert residui(agente) == []


def test_testo_utf8_e_docx(client, agente):
    assert carica(client, "nota.md", "Perché è così".encode()).status_code == 201
    assert carica(client, "doc.docx", b"PK\x03\x04" + b"\x00" * 50).status_code == 201


# --- K4: limite in streaming ------------------------------------------------

def test_limite_dichiarato(client, agente):
    r = client.post("/api/documenti", content=PDF, headers={
        "Content-Type": "application/octet-stream", "X-LIL-Nome-File": "x.pdf", "Content-Length": str(30 * 1024 * 1024)})
    assert r.status_code in (413,)


def test_limite_contato_senza_content_length(client, agente, monkeypatch):
    client.app.state.ctx.impostazioni["documenti"]["max_bytes"] = 1000

    def pezzi():
        yield b"%PDF-1.7\n"
        for _ in range(100):
            yield b"x" * 100
    r = client.post("/api/documenti", content=pezzi(), headers={"Content-Type": "application/octet-stream", "X-LIL-Nome-File": "x.pdf"})
    assert r.status_code == 413
    assert residui(agente) == []


# --- K5: temporaneo, link simbolici -----------------------------------------

def test_cartella_documenti_link_simbolico(client, agente, tmp_path):
    vera = tmp_path / "altrove"
    vera.mkdir()
    (agente.radice / "documenti").rmdir()
    os.symlink(vera, agente.radice / "documenti")
    r = carica(client, "x.pdf", PDF)
    assert r.status_code == 404 and list(vera.iterdir()) == []


def test_link_simbolico_al_posto_del_file(client, agente, tmp_path):
    bersaglio = tmp_path / "bersaglio.pdf"
    bersaglio.write_bytes(b"originale")
    os.symlink(bersaglio, agente.radice / "documenti" / "x.pdf")
    r = carica(client, "x.pdf", PDF)
    assert r.status_code == 409  # collide: serve la conferma
    g = r.json()["gettone"]
    r = carica(client, "x.pdf", PDF, gettone=g)
    assert r.status_code == 415  # un link non si sostituisce
    assert bersaglio.read_bytes() == b"originale"
    assert [p for p in residui(agente) if p.endswith(".tmp")] == []


# --- K6-K7: sostituzione ------------------------------------------------------

def test_sostituzione_con_gettone(client, agente, ambiente):
    assert carica(client, "Report Q3.pdf", PDF).status_code == 201
    r = carica(client, "report_q3.docx", b"PK\x03\x04" + b"0" * 20)
    assert r.status_code == 409 and r.json()["error_type"] == "FileEsistente"
    assert r.json()["details"]["esistenti"] == ["Report Q3.pdf"]
    g = r.json()["gettone"]
    assert residui(agente) == ["Report Q3.pdf"]
    # un gettone di file non vale su /api/conferme (e si consuma)
    altro = carica(client, "report_q3.docx", b"PK\x03\x04").json()["gettone"]
    assert client.post(f"/api/conferme/{altro}", json={}).json()["error_type"] == "GettoneNonValido"
    # il gettone vale solo per quel nome
    assert carica(client, "altro.pdf", PDF, gettone=g).json()["error_type"] == "GettoneNonValido"
    g = carica(client, "report_q3.docx", b"PK\x03\x04").json()["gettone"]
    r = carica(client, "report_q3.docx", b"PK\x03\x04" + b"1" * 20, gettone=g)
    assert r.status_code == 201 and r.json()["dati"]["sostituiti"] == ["Report Q3.pdf"]
    assert residui(agente) == ["report_q3.docx"]
    assert righe_registro(ambiente)[-1]["azione"] == "sostituisci_documento"
    # monouso
    assert carica(client, "report_q3.docx", PDF, gettone=g).json()["error_type"] == "GettoneNonValido"


def test_gettone_di_un_altro_utente(ambiente_cms, agente):
    with client_per(ambiente_cms) as c:
        def h(utente):
            return {**header_firmati("POST", "/api/documenti", utente=utente, ruoli="autore"), "X-LIL-Richiesta": "1",
                    "Content-Type": "application/octet-stream", "X-LIL-Nome-File": "x.pdf"}
        assert c.post("/api/documenti", content=PDF, headers=h("a")).status_code == 201
        g = c.post("/api/documenti", content=PDF, headers=h("a")).json()["gettone"]
        assert c.post("/api/documenti", content=PDF, headers={**h("b"), "X-LIL-Gettone": g}).json()["error_type"] == "GettoneNonValido"


def test_permesso_di_caricare(ambiente_cms, agente):
    with client_per(ambiente_cms) as c:
        h = {**header_firmati("POST", "/api/documenti", ruoli="approvatore"), "X-LIL-Richiesta": "1",
             "Content-Type": "application/octet-stream", "X-LIL-Nome-File": "x.pdf"}
        assert c.post("/api/documenti", content=PDF, headers=h).status_code == 403
        assert residui(agente) == []


def test_niente_sostituzione_durante_un_lavoro(client, agente):
    assert carica(client, "x.pdf", PDF).status_code == 201
    g = carica(client, "x.pdf", PDF).json()["gettone"]

    class Finto:
        run = "run-uno"
    coda = client.app.state.ctx.coda
    coda._in_corso = Finto()
    try:
        assert carica(client, "x.pdf", PDF, gettone=g).json()["error_type"] == "LavoroGiaInCorso"
    finally:
        coda._in_corso = None


def test_chiave_come_i_comandi():
    assert chiave_nome("Mario_Rossi.wav") == chiave_nome("mario-rossì.mp3") == "mario rossi"


# --- K9-K12: download -------------------------------------------------------

INFO = {"exit": 0, "json": {"ok": True, "run": "run-uno", "file_finale": {"nome": "ep.mp3", "percorso": "output/run-uno/ep.mp3"},
                            "file": [{"nome": "trascrizione.txt", "percorso": "output/run-uno/trascrizione.txt", "interno": False},
                                     {"nome": "_tempi.json", "percorso": "output/run-uno/_tempi.json", "interno": True},
                                     {"nome": "episodio.json", "interno": False}]}}


@pytest.fixture
def episodio(agente):
    run = agente.radice / "output" / "run-uno"
    (run / "ep.mp3").write_bytes(bytes(range(256)) * 4)
    (run / "trascrizione.txt").write_text("testo")
    (run / "_tempi.json").write_text("{}")
    (run / "episodio.json").write_text('{"segreto": "/srv"}')
    agente.scenario("episode_info.py", INFO)
    return run


def test_solo_file_elencati(client, episodio):
    assert client.get("/api/episodi/run-uno/file/ep.mp3").status_code == 200
    assert client.get("/api/episodi/run-uno/file/trascrizione.txt").status_code == 200
    assert client.get("/api/episodi/run-uno/file/_tempi.json").status_code == 404  # interno
    assert client.get("/api/episodi/run-uno/file/altro.mp3").status_code == 404
    assert client.get("/api/episodi/run-uno/file/..%2Fep.mp3").status_code in (400, 404)


def test_episodio_json_mai(client, episodio, agente):
    """episodio.json ha percorsi del server: anche se un elenco lo segnasse non interno, si rifiuta."""
    assert client.get("/api/episodi/run-uno/file/episodio.json").status_code == 404


def test_link_simbolico_non_servito(client, episodio, tmp_path):
    (tmp_path / "segreto.txt").write_text("segreto")
    (episodio / "trascrizione.txt").unlink()
    os.symlink(tmp_path / "segreto.txt", episodio / "trascrizione.txt")
    assert client.get("/api/episodi/run-uno/file/trascrizione.txt").status_code == 404


def test_range(client, episodio):
    dati = (episodio / "ep.mp3").read_bytes()
    r = client.get("/api/episodi/run-uno/file/ep.mp3", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == dati[10:20] and r.headers["content-range"] == f"bytes 10-19/{len(dati)}"
    r = client.get("/api/episodi/run-uno/file/ep.mp3", headers={"Range": "bytes=-5"})
    assert r.status_code == 206 and r.content == dati[-5:]
    r = client.get("/api/episodi/run-uno/file/ep.mp3", headers={"Range": "bytes=1000-"})
    assert r.status_code == 206 and r.content == dati[1000:]
    for cattivo in ("bytes=5000-6000", "bytes=20-10", "bytes=-0"):
        r = client.get("/api/episodi/run-uno/file/ep.mp3", headers={"Range": cattivo})
        assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{len(dati)}"
    for ignorato in ("bytes=0-1,5-6", "righe=1-2", "bytes=99999999999999999999-"):
        r = client.get("/api/episodi/run-uno/file/ep.mp3", headers={"Range": ignorato})
        assert r.status_code == 200 and r.content == dati
    assert r.headers["content-type"] == "audio/mpeg" and r.headers["accept-ranges"] == "bytes"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_intervallo_puro():
    assert intervallo("bytes=0-0", 10) == (0, 0)
    assert intervallo("bytes=5-100", 10) == (5, 9)
    assert intervallo("bytes=-100", 10) == (0, 9)
    assert intervallo(None, 10) is None


def test_content_disposition(client, agente, episodio):
    nome = 'ep "è";\r\nx.mp3'
    INFO2 = json.loads(json.dumps(INFO))
    INFO2["json"]["file_finale"]["nome"] = "ep è.mp3"
    (episodio / "ep è.mp3").write_bytes(b"abc")
    agente.scenario("episode_info.py", INFO2)
    r = client.get("/api/episodi/run-uno/file/ep%20%C3%A8.mp3?scarica=1")
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;") and "filename*=UTF-8''ep%20%C3%A8.mp3" in cd and '"ep _.mp3"' in cd
    assert "\r" not in cd and "\n" not in cd
    assert client.get("/api/episodi/run-uno/file/ep%20%C3%A8.mp3").headers["content-disposition"].startswith("inline;")
    _ = nome


def test_documento_solo_se_elencato(client, agente):
    (agente.radice / "documenti" / "a.pdf").write_bytes(PDF)
    (agente.radice / "documenti" / "b.pdf").write_bytes(PDF)
    agente.scenario("documents_list.py", {"exit": 0, "json": {"ok": True, "documenti": [{"nome": "a.pdf"}]}})
    assert client.get("/api/documenti/a.pdf/file").content == PDF
    assert client.get("/api/documenti/b.pdf/file").status_code == 404
    assert client.get("/api/documenti/.estrazioni/file").status_code in (400, 404)
