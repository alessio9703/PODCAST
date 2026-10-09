"""Correzioni dopo la revisione di A2 (basse 1, 2, 4, 5, 6, 7, 8 e note 4, 5)."""

import asyncio
import json
import os
import signal
import time

import pytest

from app.agente import tabella
from app.agente.tabella import Comando, ErroreTabella, verifica_tabella
from app.ingresso.cms import codifica_header
from tests.conftest import client_per, header_firmati, righe_registro
from tests.test_caricamenti_download import PDF, carica, residui
from tests.test_lavori import PRODUCE, STIMA, aspetta, finito


# --- BASSA-1: mai .json, .lock, .tmp in una sostituzione ---------------------

def test_sostituzione_non_tocca_json_lock_tmp(client, agente):
    cartella = agente.radice / "documenti"
    assert carica(client, "Mario Rossi.pdf", PDF).status_code == 201
    for nome in ("Mario Rossi.json", "Mario Rossi.lock", "Mario_Rossi.tmp"):
        (cartella / nome).write_text("{}")
    r = carica(client, "mario-rossi.docx", b"PK\x03\x04" + b"0" * 20)
    assert r.json()["details"]["esistenti"] == ["Mario Rossi.pdf"]
    r = carica(client, "mario-rossi.docx", b"PK\x03\x04" + b"0" * 20, gettone=r.json()["gettone"])
    assert r.status_code == 201 and r.json()["dati"]["sostituiti"] == ["Mario Rossi.pdf"]
    assert residui(agente) == ["Mario Rossi.json", "Mario Rossi.lock", "Mario_Rossi.tmp", "mario-rossi.docx"]


def test_un_json_con_la_stessa_chiave_non_e_una_collisione(client, agente):
    (agente.radice / "documenti" / "report.json").write_text("{}")
    assert carica(client, "report.pdf", PDF).status_code == 201


# --- BASSA-2: controllo al rename, gettone consumato dopo i controlli ---------

def test_gettone_non_consumato_se_il_caricamento_fallisce(client, agente):
    assert carica(client, "x.pdf", PDF).status_code == 201
    g = carica(client, "x.pdf", PDF).json()["gettone"]
    assert carica(client, "x.pdf", b"non e' un pdf", gettone=g).status_code == 415
    assert carica(client, "x.pdf", PDF + b"!", gettone=g).status_code == 201  # lo stesso gettone vale ancora
    assert carica(client, "x.pdf", PDF, gettone=g).json()["error_type"] == "GettoneNonValido"  # ora e' consumato


def test_lavoro_partito_durante_il_caricamento_blocca_il_rename(client, agente):
    assert carica(client, "x.pdf", PDF).status_code == 201
    g = carica(client, "x.pdf", PDF).json()["gettone"]
    coda = client.app.state.ctx.coda

    def pezzi():
        yield PDF[:10]
        coda._in_corso = type("Finto", (), {"run": "run-uno"})()  # un lavoro parte mentre il file arriva
        yield b"nuovo" * 10
    try:
        r = client.post("/api/documenti", content=pezzi(), headers={
            "Content-Type": "application/octet-stream", "X-LIL-Nome-File": "x.pdf", "X-LIL-Gettone": g})
        assert r.json()["error_type"] == "LavoroGiaInCorso"
    finally:
        coda._in_corso = None
    assert (agente.radice / "documenti" / "x.pdf").read_bytes() == PDF
    assert [p for p in residui(agente) if p.endswith(".tmp")] == []
    assert carica(client, "x.pdf", PDF + b"2", gettone=g).status_code == 201  # gettone non consumato


# --- BASSA-4: limite in byte ------------------------------------------------

def test_nome_lungo_in_byte(client, agente):
    nome = "è" * 140 + ".pdf"  # 144 caratteri, 284 byte
    r = carica(client, nome, PDF)
    assert r.status_code == 415 and residui(agente) == []
    assert carica(client, "è" * 98 + ".pdf", PDF).status_code == 201  # 200 byte


# --- BASSA-5: SIGKILL dopo il SIGTERM solo per stesura, revisione, proposte -----

OSTINATO = r'''
import signal, time, pathlib
signal.signal(signal.SIGTERM, lambda *_: pathlib.Path("sigterm.log").open("a").write("1\n"))
time.sleep(120)
'''


def test_sigkill_dopo_il_sigterm_per_la_stesura(client, agente, monkeypatch):
    (agente.radice / "tools" / "script_write.py").write_text(OSTINATO)
    monkeypatch.setitem(tabella.TABELLA, "scrivi_copione",
                        Comando("script_write.py", (tabella._run(),), timeout=0.5, lungo=True, permesso="copione_scrivi",
                                by=True, registra=True, sigkill_dopo_s=0.5))
    lavoro = client.post("/api/episodi/run-uno/copione/stesura").json()["dati"]["lavoro"]
    v = aspetta(client, finito(lavoro), timeout=20)
    esito = [l for l in v["recenti"] if l["lavoro"] == lavoro][0]["esito"]
    assert esito["error_type"] in ("Timeout", "ErroreInterno")
    assert (agente.radice / "sigterm.log").read_text() == "1\n"  # un solo SIGTERM, poi SIGKILL


def test_mai_sigkill_per_produzione_retake_voci():
    for nome, cmd in tabella.TABELLA.items():
        if cmd.tool in tabella.TOOL_CON_CLONAZIONE:
            assert cmd.sigkill_dopo_s is None, nome
    assert {n for n, c in tabella.TABELLA.items() if c.sigkill_dopo_s} == {"scrivi_copione", "revisiona_copione", "lezioni_proponi"}


def test_verifica_tabella_rifiuta_sigkill_sulla_produzione(monkeypatch):
    monkeypatch.setitem(tabella.TABELLA, "produci", Comando("produce.py", (tabella._run(),), fissi=("--events",),
                                                            timeout=None, lungo=True, permesso="produrre", sigkill_dopo_s=60))
    with pytest.raises(ErroreTabella):
        verifica_tabella()


def test_interruzione_della_produzione_senza_sigkill(client, agente, monkeypatch):
    (agente.radice / "tools" / "produce.py").write_text(OSTINATO)
    agente.scenario("estimate.py", STIMA)
    inviati = []
    monkeypatch.setattr(os, "killpg", lambda *a: inviati.append(a))
    client.post("/api/episodi/run-uno/produzione")
    aspetta(client, lambda d: d["in_corso"] and d["in_corso"]["stato"] == "in_corso")
    time.sleep(0.5)
    client.post("/api/episodi/run-uno/interruzione")
    time.sleep(1.0)
    assert inviati == []  # niente SIGKILL al gruppo
    lavoro = client.app.state.ctx.coda.lavoro_in_corso()
    lavoro.processo.kill()  # pulizia del test


# --- BASSA-6: registro anche per errori del controller e allo spegnimento ------

def test_registro_su_errore_del_controller(client, agente, ambiente, monkeypatch):
    import app.lavori.coda as modulo

    async def guasto(*a, **k):
        raise RuntimeError("guasto")
    monkeypatch.setattr(modulo.esecutore, "avvia_processo", guasto)
    lavoro = client.post("/api/episodi/run-uno/copione/stesura").json()["dati"]["lavoro"]
    aspetta(client, finito(lavoro))
    righe = [r for r in righe_registro(ambiente) if r.get("lavoro") == lavoro]
    assert [r["esito"] for r in righe] == ["accodato", "ErroreInterno"]


def test_registro_allo_spegnimento(ambiente, agente, monkeypatch):
    (agente.radice / "tools" / "produce.py").write_text(OSTINATO)
    agente.scenario("estimate.py", STIMA)
    monkeypatch.setattr("app.lavori.coda.ATTESA_SPEGNIMENTO_S", 0.5)
    with client_per(ambiente) as c:
        c.post("/api/episodi/run-uno/produzione", headers={"X-LIL-Richiesta": "1"})
        fine = time.monotonic() + 10
        while time.monotonic() < fine and not (agente.radice / "chiamate.jsonl").read_text().count("produce.py"):
            time.sleep(0.05)
        time.sleep(0.3)
        lavoro = c.app.state.ctx.coda.lavoro_in_corso()
    righe = [r for r in righe_registro(ambiente) if r.get("lavoro") == lavoro.id]
    assert righe[-1]["esito"] == "LavoroAnnullato" and "spegnimento" in righe[-1]["dettagli"]["motivo"]
    lavoro.processo.kill() if lavoro.processo else None


# --- BASSA-7: senza costo la produzione non si accoda -------------------------

def test_stima_fallita_produzione_non_accodata(client, agente, ambiente):
    agente.scenario("estimate.py", {"exit": 1, "json": {"ok": False, "error_type": "ScriptMissing", "error": "manca il copione", "exit_code": 1}})
    r = client.post("/api/episodi/run-uno/produzione")
    assert r.status_code == 422 and r.json()["error_type"] == "ScriptMissing"
    assert agente.chiamate("produce.py") == [] and client.get("/api/lavori").json()["dati"]["in_attesa"] == []
    riga = righe_registro(ambiente)[-1]
    assert riga["azione"] == "produci" and riga["esito"] == "ScriptMissing"


def test_stima_senza_costo(client, agente):
    agente.scenario("estimate.py", {"exit": 0, "json": {"ok": True, "cost_usd": None}})
    r = client.post("/api/episodi/run-uno/produzione")
    assert r.status_code == 500 and agente.chiamate("produce.py") == []


# --- NOTA-4: chi gestisce le voci risolve una clonazione incerta ---------------

def test_gestore_voci_risolve_la_clonazione_incerta(ambiente_cms, agente):
    agente.scenario("voice_meta.py", [
        {"exit": 0, "json": {"ok": True, "voce": "Mario Rossi", "clonazione_incerta": True}},
        {"exit": 0, "json": {"ok": True, "voce": "Mario Rossi", "clonazione_incerta_tolta": True}},
    ])
    with client_per(ambiente_cms) as c:
        p = "/api/voci/mario%20rossi/clonazione-verificata"
        h = {**header_firmati("POST", p, utente="gv", nome="Gestore Voci", ruoli="gestore_voci"), "X-LIL-Richiesta": "1"}
        r = c.post(p, headers=h)
        assert r.status_code == 202, r.text
        lavoro = r.json()["dati"]["lavoro"]
        fine = time.monotonic() + 15
        while time.monotonic() < fine:
            if c.get(f"/api/lavori/{lavoro}", headers=header_firmati("GET", f"/api/lavori/{lavoro}", utente="gv")).json()["dati"]["stato"] == "finito":
                break
            time.sleep(0.05)
    argv = agente.chiamate("voice_meta.py")[-1]["argv"]
    assert argv == ["--name=Mario Rossi", "--confirm-clone-retry", "--by=Gestore Voci"]
    riga = righe_registro(ambiente_cms)[-1]
    assert riga["utente"] == "gv" and riga["azione"] == "ritenta_clonazione" and riga["dettagli"]["risposta_a"] == "CloneOutcomeUnknown"


def test_niente_conferma_se_la_clonazione_non_e_incerta(client, agente):
    agente.scenario("voice_meta.py", {"exit": 0, "json": {"ok": True, "voce": "Mario Rossi", "clonazione_incerta": False}})
    r = client.post("/api/voci/Mario%20Rossi/clonazione-verificata")
    assert r.status_code == 400
    assert all("--confirm-clone-retry" not in c["argv"] for c in agente.chiamate("voice_meta.py"))


def test_clonazione_verificata_vuole_il_permesso(ambiente_cms, agente):
    with client_per(ambiente_cms) as c:
        p = "/api/voci/Mario%20Rossi/clonazione-verificata"
        h = {**header_firmati("POST", p, ruoli="approvatore,autore"), "X-LIL-Richiesta": "1"}
        assert c.post(p, headers=h).status_code == 403
    assert agente.chiamate("voice_meta.py") == []


# --- NOTA-5: cartelle delle librerie all'avvio --------------------------------

@pytest.mark.parametrize("cartella", ["documenti", "voci_disponibili", "musiche", "output"])
def test_cartelle_delle_librerie_all_avvio(ambiente, agente, tmp_path, cartella):
    import shutil
    shutil.rmtree(agente.radice / cartella)
    with pytest.raises(Exception):
        with client_per(ambiente):
            pass
    (tmp_path / "fuori").mkdir()
    os.symlink(tmp_path / "fuori", agente.radice / cartella)
    with pytest.raises(Exception):
        with client_per(ambiente):
            pass
