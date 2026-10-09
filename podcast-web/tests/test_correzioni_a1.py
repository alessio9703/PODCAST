"""Correzioni dopo la revisione di A1 (basse 1, 4, 5, 6, 8, 9, 10 e NOTA-1)."""

import asyncio
import logging
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.agente import esecutore, tabella
from app.agente.tabella import Comando, Conferma, ErroreArgomento, ErroreTabella, costruisci_argomenti, verifica_tabella
from app.config import carica_config
from app.registro import ErroreRegistro, Registro
from app.ingresso import Utente
from tests.conftest import client_per, header_firmati, righe_registro


# --- BASSA-1 ---------------------------------------------------------------

@pytest.mark.parametrize("doppio", ["X-LIL-Utente", "X-LIL-Ruoli", "X-LIL-Firma", "X-LIL-Nome", "X-LIL-Timestamp"])
def test_header_di_identita_duplicati(ambiente_cms, doppio):
    with client_per(ambiente_cms) as c:
        h = header_firmati("GET", "/api/io")
        lista = list(h.items()) + [(doppio, h[doppio])]  # anche se uguale
        assert c.get("/api/io", headers=lista).status_code == 401
        assert c.get("/api/io", headers=h).status_code == 200


# --- BASSA-4 ---------------------------------------------------------------

def test_header_di_sicurezza_anche_sui_500(client, monkeypatch):
    async def esplode(*a, **k):
        raise RuntimeError("guasto")
    monkeypatch.setattr("app.servizi.azioni.esecutore.esegui", esplode)
    r = client.post("/api/prova/copione", json={"run": "run-uno"})
    assert r.status_code == 500 and r.json()["error_type"] == "ErroreInterno"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]


# --- BASSA-5 ---------------------------------------------------------------

@pytest.mark.parametrize("valore", ["²", "١٢", "1e3", " 3", "3 ", "-1", "1234567890"])
def test_interi_solo_ascii(agente, valore):
    with pytest.raises(ErroreArgomento):
        costruisci_argomenti("approva_copione", {"run": "run-uno", "versione": valore},
                             output_dir=agente.radice / "output", by="A")


def test_intero_unicode_via_http_400(client):
    r = client.post("/api/prova/approva_copione", json={"run": "run-uno", "versione": "²"})
    assert r.status_code == 400 and r.json()["details"] == {"campo": "versione"}


# --- BASSA-6 ---------------------------------------------------------------

FIGLIO = """
import subprocess, sys, time, pathlib
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pathlib.Path("figlio.pid").write_text(str(p.pid))
time.sleep(60)
"""


def _vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # uno zombie non ancora raccolto conta come morto
    try:
        stato = os.popen(f"ps -o stat= -p {pid}").read().strip()
    except OSError:
        return True
    return bool(stato) and not stato.startswith("Z")


def test_timeout_ferma_anche_i_figli(ambiente, agente):
    (agente.radice / "tools" / "script_show.py").write_text(FIGLIO)
    config = carica_config(ambiente)
    risultato = asyncio.run(esecutore.esegui(config, "copione", {"run": "run-uno"}, timeout=1.5))
    assert risultato.timeout
    pid = int((agente.radice / "figlio.pid").read_text())
    for _ in range(50):
        if not _vivo(pid):
            break
        time.sleep(0.1)
    assert not _vivo(pid)


# --- BASSA-8 ---------------------------------------------------------------

def _con(monkeypatch, nome, cmd):
    monkeypatch.setitem(tabella.TABELLA, nome, cmd)


@pytest.mark.parametrize("cmd", [
    Comando("mix.py", (tabella.Arg("accetta", "--accept-changed-music", tabella.FLAG),)),
    Comando("mix.py", (), fissi=("--accept-changed-music",)),
    Comando("voice_meta.py", (), fissi=("--confirm-clone-retry",)),
    Comando("mix.py", (), conferme={"MusicChanged": Conferma(flag="--events")}),
    Comando("mix.py", (), conferme={"CloneOutcomeUnknown": Conferma(azione="copione")}),
])
def test_flag_di_conferma_solo_da_gettone(monkeypatch, cmd):
    verifica_tabella()
    _con(monkeypatch, "prova_sbagliata", cmd)
    with pytest.raises(ErroreTabella):
        verifica_tabella()


# --- BASSA-9 ---------------------------------------------------------------

def test_corpo_json_troppo_grande(client):
    grande = '{"run": "' + "x" * 300_000 + '"}'
    r = client.post("/api/prova/copione", content=grande, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error_type"] == "FileTroppoGrande"


def test_corpo_chunked_troppo_grande(client):
    def pezzi():
        yield b'{"run": "'
        for _ in range(40):
            yield b"x" * 10_000
        yield b'"}'
    r = client.post("/api/prova/copione", content=pezzi(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


# --- BASSA-10 --------------------------------------------------------------

def test_registro_non_scritto_avviso_e_log_critico(client, agente, ambiente, caplog, monkeypatch):
    def guasto(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr("app.registro.os.write", guasto)
    with caplog.at_level(logging.CRITICAL, logger="podcast_web.registro"):
        r = client.post("/api/prova/approva_copione", json={"run": "run-uno", "versione": 2})
    assert r.status_code == 200 and r.json()["ok"] is True  # l'esito vero dell'agente
    assert "registro" in r.json()["avviso"]
    assert "REGISTRO ATTIVITA' NON SCRITTO" in caplog.text and '"azione": "approva_copione"' in caplog.text
    assert '"utente": "demo"' in caplog.text


def test_permessi_larghi_del_registro_bloccano_l_avvio(tmp_path):
    cartella = tmp_path / "reg"
    cartella.mkdir(mode=0o700)
    file = cartella / "r.jsonl"
    file.write_text("")
    file.chmod(0o644)
    with pytest.raises(ErroreRegistro, match="0600"):
        Registro(file).verifica()
    file.chmod(0o600)
    cartella.chmod(0o755)
    with pytest.raises(ErroreRegistro, match="0700"):
        Registro(file).verifica()
    cartella.chmod(0o700)
    Registro(file).verifica()


def test_avvio_rifiutato_con_registro_aperto_agli_altri(ambiente):
    file = Path(ambiente["REGISTRO_FILE"])
    file.parent.mkdir(mode=0o700, parents=True)
    file.write_text("")
    file.chmod(0o640)
    with pytest.raises(Exception):
        with client_per(ambiente):
            pass


# --- NOTA-1 ----------------------------------------------------------------

def test_conferma_registra_la_domanda(client, agente, ambiente):
    agente.scenario("episode_config.py", [
        {"exit": 3, "json": {"ok": False, "error_type": "SpokenNameIncomplete", "error": "x", "exit_code": 3, "details": {"campo": "nome"}}},
        {"exit": 0, "json": {"ok": True}},
    ])
    g = client.post("/api/prova/modifica_configurazione", json={"run": "run-uno", "nomi": {"ospite": "Giulia"}}).json()["gettone"]
    client.post(f"/api/conferme/{g}", json={})
    assert righe_registro(ambiente)[-1]["dettagli"]["risposta_a"] == "SpokenNameIncomplete"
