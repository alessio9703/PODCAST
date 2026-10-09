"""K13-K24: coda, interruzione, eventi (SSE e polling), disconnessione (A2-piano.md)."""

import asyncio
import json
import os
import signal
import time
from pathlib import Path

import pytest

from app.lavori.eventi import Lettore
from app.servizi.esiti import Pulitore
from app.servizi.stima import impronta_stima
from tests.conftest import client_per, header_firmati, righe_registro

# Un produce.py finto: scrive eventi, aspetta finche' esiste `fermo`, e al SIGTERM
# esce come l'agente (ProductionInterrupted). Registra quanti SIGTERM ha ricevuto.
PRODUCE = r'''
import json, os, signal, sys, time, pathlib
cwd = pathlib.Path.cwd()
run = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--run-dir=")][0]
ev = cwd / "output" / run / "_eventi.jsonl"
ev.write_text("")
segnali = []
def scrivi(e):
    with open(ev, "a") as f:
        f.write(json.dumps(e) + "\n")
def termina(*_):
    segnali.append(1)
    (cwd / "sigterm.log").open("a").write("1\n")
signal.signal(signal.SIGTERM, termina)
(cwd / "argv.json").write_text(json.dumps(sys.argv[1:]))
scrivi({"evento": "iniziata", "at": "t", "totale": 3, "fasi": ["preparazione_voci", "sintesi"], "nota": "/Users/segreto/x"})
scrivi({"evento": "fase_iniziata", "at": "t", "fase": "sintesi"})
for n in range(1, 4):
    while (cwd / "fermo").exists() and not segnali:
        time.sleep(0.05)
    if segnali:
        scrivi({"evento": "interrotta", "at": "t", "done": n - 1, "total": 3, "missing": list(range(n, 4))})
        print(json.dumps({"ok": False, "error_type": "ProductionInterrupted", "error": "interrotta", "exit_code": 1,
                          "details": {"done": n - 1, "total": 3, "missing": list(range(n, 4))}}))
        sys.exit(1)
    scrivi({"evento": "battuta", "at": "t", "n": n, "totale": 3, "speaker": "A", "cached": False, "eta_seconds": None})
scrivi({"evento": "finita", "at": "t", "file": "ep.mp3", "duration_seconds": 3.0, "tts_calls": 3})
print(json.dumps({"ok": True, "run": run, "file": "ep.mp3", "output": "output/x/ep.mp3"}))
'''

STIMA = {"exit": 0, "json": {"ok": True, "cost_usd": 2.5, "cost_eur": 2.25, "turns": 3}}
PRODUCI = {"impronta": impronta_stima(STIMA["json"])}  # la stima che l'utente ha visto


@pytest.fixture
def produce(agente):
    (agente.radice / "tools" / "produce.py").write_text(PRODUCE)
    agente.scenario("estimate.py", STIMA)
    (agente.radice / "output" / "run-due").mkdir()
    return agente


def aspetta(client, condizione, timeout=15):
    fine = time.monotonic() + timeout
    while time.monotonic() < fine:
        dati = client.get("/api/lavori").json()["dati"]
        if condizione(dati):
            return dati
        time.sleep(0.05)
    raise AssertionError(f"condizione non raggiunta: {dati}")


def finito(id_lavoro):
    return lambda d: any(l["lavoro"] == id_lavoro and l["stato"] in ("finito", "annullato") for l in d["recenti"])


def test_produzione_in_coda_con_stima_e_registro(client, produce, ambiente):
    r = client.post("/api/episodi/run-uno/produzione", json=PRODUCI)
    assert r.status_code == 202, r.text
    lavoro = r.json()["dati"]["lavoro"]
    dati = aspetta(client, finito(lavoro))
    esito = dati["recenti"][0]["esito"]
    assert esito["ok"] and "output" not in esito["dati"]
    argv = json.loads((produce.radice / "argv.json").read_text())
    assert argv == ["--run-dir=run-uno", "--events"]
    righe = [r for r in righe_registro(ambiente) if r.get("lavoro") == lavoro]
    assert [r["esito"] for r in righe] == ["accodato", "ok"]
    assert righe[0]["costo_stimato_usd"] == 2.5 and righe[0]["costo_stimato_eur"] == 2.25
    assert produce.chiamate("estimate.py")[0]["argv"] == ["--run-dir=run-uno"]


def test_un_lavoro_per_run_e_un_worker(client, produce):
    (produce.radice / "fermo").write_text("")
    primo = client.post("/api/episodi/run-uno/produzione", json=PRODUCI).json()["dati"]["lavoro"]
    r = client.post("/api/episodi/run-uno/produzione", json=PRODUCI)
    assert r.status_code == 409 and r.json()["error_type"] == "LavoroGiaInCorso"
    secondo = client.post("/api/episodi/run-due/produzione", json=PRODUCI).json()["dati"]
    assert secondo["stato"] == "in_coda" and secondo["posizione"] == 2
    dati = client.get("/api/lavori").json()["dati"]
    assert dati["in_corso"]["lavoro"] == primo and [l["lavoro"] for l in dati["in_attesa"]] == [secondo["lavoro"]]
    (produce.radice / "fermo").unlink()
    aspetta(client, finito(secondo["lavoro"]))


def test_coda_piena(ambiente, produce):
    with client_per(ambiente, CODA_MAX="1") as c:
        c.headers.update({"X-LIL-Richiesta": "1"})
        (produce.radice / "fermo").write_text("")
        (produce.radice / "output" / "run-tre").mkdir()
        assert c.post("/api/episodi/run-uno/produzione", json=PRODUCI).status_code == 202
        aspetta(c, lambda d: d["in_corso"] is not None)
        assert c.post("/api/episodi/run-due/produzione", json=PRODUCI).status_code == 202
        r = c.post("/api/episodi/run-tre/produzione", json=PRODUCI)
        assert r.status_code == 503 and r.json()["error_type"] == "CodaPiena"
        (produce.radice / "fermo").unlink()


def test_argomenti_e_permessi_prima_di_accodare(ambiente_cms, produce):
    with client_per(ambiente_cms) as c:
        p = "/api/episodi/run-uno/produzione"
        h = {**header_firmati("POST", p, ruoli="autore"), "X-LIL-Richiesta": "1"}
        assert c.post(p, json=PRODUCI, headers=h).status_code == 403
        p = "/api/episodi/manca/produzione"
        h = {**header_firmati("POST", p, ruoli="approvatore"), "X-LIL-Richiesta": "1"}
        assert c.post(p, json=PRODUCI, headers=h).status_code == 404
        assert produce.chiamate("estimate.py") == []


def test_interruzione_un_solo_sigterm(client, produce, ambiente):
    (produce.radice / "fermo").write_text("")
    lavoro = client.post("/api/episodi/run-uno/produzione", json=PRODUCI).json()["dati"]["lavoro"]
    aspetta(client, lambda d: d["in_corso"] and d["in_corso"]["stato"] == "in_corso")
    ev = produce.radice / "output" / "run-uno" / "_eventi.jsonl"
    fine = time.monotonic() + 10
    while time.monotonic() < fine and not (ev.exists() and "fase_iniziata" in ev.read_text()):
        time.sleep(0.05)
    r1 = client.post("/api/episodi/run-uno/interruzione")
    r2 = client.post("/api/episodi/run-uno/interruzione")
    assert r1.json()["dati"]["messaggio"] == "Interruzione richiesta."
    assert r2.json()["dati"]["messaggio"] == "Interruzione gia' richiesta."
    dati = aspetta(client, finito(lavoro))
    esito = dati["recenti"][0]["esito"]
    assert esito["error_type"] == "ProductionInterrupted" and esito["details"]["missing"] == [1, 2, 3]
    assert (produce.radice / "sigterm.log").read_text() == "1\n"
    azioni = [(r["azione"], r["esito"]) for r in righe_registro(ambiente)]
    assert ("interrompi", "richiesta") in azioni and ("produci", "ProductionInterrupted") in azioni


def test_interrompere_un_lavoro_in_attesa(client, produce):
    (produce.radice / "fermo").write_text("")
    client.post("/api/episodi/run-uno/produzione", json=PRODUCI)
    secondo = client.post("/api/episodi/run-due/produzione", json=PRODUCI).json()["dati"]["lavoro"]
    r = client.post("/api/episodi/run-due/interruzione")
    assert r.json()["dati"]["stato"] == "annullato"
    assert client.get(f"/api/lavori/{secondo}").json()["dati"]["stato"] == "annullato"
    (produce.radice / "fermo").unlink()


def test_interrompere_vuole_il_permesso(ambiente_cms, produce):
    (produce.radice / "fermo").write_text("")
    with client_per(ambiente_cms) as c:
        p = "/api/episodi/run-uno/produzione"
        c.post(p, json=PRODUCI, headers={**header_firmati("POST", p, ruoli="approvatore"), "X-LIL-Richiesta": "1"})
        p = "/api/episodi/run-uno/interruzione"
        r = c.post(p, json=PRODUCI, headers={**header_firmati("POST", p, ruoli="autore"), "X-LIL-Richiesta": "1"})
        assert r.status_code == 403
        (produce.radice / "fermo").unlink()


def test_esito_3_del_lavoro_con_gettone_e_conferma_in_coda(client, produce, ambiente):
    produce.scenario("produce.py", None)
    (produce.radice / "tools" / "produce.py").write_text(
        (Path(__file__).parent / "agente_finto.py").read_text())
    produce.scenario("produce.py", [
        {"exit": 3, "json": {"ok": False, "error_type": "MusicChanged", "error": "brano sostituito", "exit_code": 3}},
        {"exit": 0, "json": {"ok": True}},
    ])
    lavoro = client.post("/api/episodi/run-uno/produzione", json=PRODUCI).json()["dati"]["lavoro"]
    esito = aspetta(client, finito(lavoro))["recenti"][0]["esito"]
    assert esito["esito"] == "decisione" and esito["gettone"]
    r = client.post(f"/api/conferme/{esito['gettone']}", json={})
    assert r.status_code == 202
    nuovo = r.json()["dati"]["lavoro"]
    aspetta(client, finito(nuovo))
    assert produce.chiamate("produce.py")[-1]["argv"] == ["--run-dir=run-uno", "--events", "--accept-changed-music"]
    riga = [x for x in righe_registro(ambiente) if x.get("lavoro") == nuovo][-1]
    assert riga["dettagli"]["conferme"] == ["--accept-changed-music"] and riga["dettagli"]["risposta_a"] == "MusicChanged"


def test_stesura_e_revisione_in_coda_con_by(client, agente):
    agente.scenario("script_write.py", {"exit": 0, "json": {"ok": True, "version": 1}})
    lavoro = client.post("/api/episodi/run-uno/copione/stesura").json()["dati"]["lavoro"]
    aspetta(client, finito(lavoro))
    assert agente.chiamate("script_write.py")[0]["argv"] == ["--run-dir=run-uno", "--by=Utente Demo"]
    lavoro = client.post("/api/episodi/run-uno/copione/revisione", json={"istruzioni": "-più breve"}).json()["dati"]["lavoro"]
    aspetta(client, finito(lavoro))
    assert agente.chiamate("script_revise.py")[0]["argv"] == ["--run-dir=run-uno", "--instructions=-più breve", "--by=Utente Demo"]


def test_esito_visibile_solo_a_chi_ha_lanciato(ambiente_cms, produce):
    with client_per(ambiente_cms) as c:
        p = "/api/episodi/run-uno/produzione"
        lavoro = c.post(p, json=PRODUCI, headers={**header_firmati("POST", p, ruoli="approvatore"), "X-LIL-Richiesta": "1"}).json()["dati"]["lavoro"]
        fine = time.monotonic() + 15
        while time.monotonic() < fine:
            v = c.get(f"/api/lavori/{lavoro}", headers=header_firmati("GET", f"/api/lavori/{lavoro}", utente="altro")).json()["dati"]
            if v["stato"] == "finito":
                break
            time.sleep(0.05)
        assert set(v["esito"]) <= {"ok", "esito", "error_type"}
        mio = c.get(f"/api/lavori/{lavoro}", headers=header_firmati("GET", f"/api/lavori/{lavoro}", ruoli="approvatore")).json()["dati"]
        assert "dati" in mio["esito"]


# --- Eventi -----------------------------------------------------------------

def test_lettore_righe_complete_link_e_pulizia(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    f = run / "_eventi.jsonl"
    f.write_bytes(b'{"evento": "iniziata", "nota": "/Users/x/y"}\n{"evento": "batt')
    l = Lettore(run, Pulitore(tmp_path, "/Users/x"))
    assert l.leggi_nuovi() == [(1, {"evento": "iniziata"})]
    with open(f, "ab") as h:
        h.write(b'uta", "n": 1}\nnon json\n' + b'{"evento": "' + b"x" * 70000 + b'"}\n{"evento": "finita"}\n')
    assert [(n, e["evento"]) for n, e in l.leggi_nuovi()] == [(2, "battuta"), (5, "finita")]
    assert l.finito
    f.write_text('{"evento": "iniziata"}\n')  # nuova produzione: file svuotato
    assert l.leggi_nuovi() == [(1, {"evento": "iniziata"})] and l.azzerato
    f.unlink()
    (tmp_path / "altro.jsonl").write_text('{"evento": "segreto"}\n')
    os.symlink(tmp_path / "altro.jsonl", f)
    assert Lettore(run, Pulitore(tmp_path, None)).leggi_nuovi() == []


def test_polling(client, produce):
    lavoro = client.post("/api/episodi/run-uno/produzione", json=PRODUCI).json()["dati"]["lavoro"]
    aspetta(client, finito(lavoro))
    dati = client.get("/api/episodi/run-uno/eventi/elenco").json()["dati"]
    assert [e["evento"] for e in dati["eventi"]] == ["iniziata", "fase_iniziata", "battuta", "battuta", "battuta", "finita"]
    assert "nota" not in dati["eventi"][0] and dati["finito"] and dati["ultimo_id"] == 6
    dati = client.get("/api/episodi/run-uno/eventi/elenco?dopo=4").json()["dati"]
    assert [e["id"] for e in dati["eventi"]] == [5, 6]
    for cattivo in ("-1", "abc", "²", "9999999"):
        assert client.get(f"/api/episodi/run-uno/eventi/elenco?dopo={cattivo}").status_code == 400


def leggi_sse(testo):
    eventi = []
    for blocco in testo.strip().split("\n\n"):
        campi = dict(r.split(": ", 1) for r in blocco.splitlines() if not r.startswith(":") and ": " in r)
        if "event" in campi:
            eventi.append((campi.get("id"), campi["event"], json.loads(campi["data"])))
    return eventi


def test_sse_segue_la_produzione_e_si_chiude(client, produce):
    client.post("/api/episodi/run-uno/produzione", json=PRODUCI)
    with client.stream("GET", "/api/episodi/run-uno/eventi") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-accel-buffering"] == "no"
        testo = "".join(r.iter_text())
    eventi = leggi_sse(testo)
    agente = [e[2]["evento"] for e in eventi if e[1] == "agente"]
    assert agente[-1] == "finita" and "battuta" in agente
    assert eventi[-1][1] == "fine" and any(e[1] == "lavoro" for e in eventi)
    assert client.app.state.ctx.sse_aperte == {}


def test_sse_last_event_id(client, produce):
    lavoro = client.post("/api/episodi/run-uno/produzione", json=PRODUCI).json()["dati"]["lavoro"]
    aspetta(client, finito(lavoro))
    with client.stream("GET", "/api/episodi/run-uno/eventi", headers={"Last-Event-ID": "4"}) as r:
        eventi = leggi_sse("".join(r.iter_text()))
    assert [e[0] for e in eventi if e[1] == "agente"] == ["5", "6"]
    assert client.get("/api/episodi/run-uno/eventi", headers={"Last-Event-ID": "x"}).status_code == 400


def test_sse_limite_per_utente(client, produce):
    client.app.state.ctx.sse_aperte["demo"] = 4
    r = client.get("/api/episodi/run-uno/eventi")
    assert r.status_code == 429 and r.json()["error_type"] == "TroppeConnessioni"
    client.app.state.ctx.sse_aperte.clear()


# --- K23-K24: disconnessione ----------------------------------------------

LENTO = r'''
import json, os, signal, sys, time, pathlib
cwd = pathlib.Path.cwd()
signal.signal(signal.SIGTERM, lambda *_: ((cwd / "sigterm.log").open("a").write("1\n"), sys.exit(1)))
time.sleep(1.0)
(cwd / "fatto.log").write_text("fatto")
print(json.dumps({"ok": True, "approved": True, "version": 2}))
'''


def test_azione_che_registra_non_si_ferma_se_il_client_va_via(client, agente, ambiente):
    (agente.radice / "tools" / "script_approve.py").write_text(LENTO)
    ctx = client.app.state.ctx
    from app.servizi.azioni import esegui_e_valuta

    async def scenario():
        utente = ctx.ingresso._utente
        compito = asyncio.ensure_future(esegui_e_valuta(ctx, utente, "approva_copione", {"run": "run-uno", "versione": 2}))
        await asyncio.sleep(0.4)
        compito.cancel()  # come una richiesta cancellata alla disconnessione
        with pytest.raises(asyncio.CancelledError):
            await compito
        await asyncio.sleep(1.5)

    client.portal.call(scenario)
    assert (agente.radice / "fatto.log").exists() and not (agente.radice / "sigterm.log").exists()
    riga = righe_registro(ambiente)[-1]
    assert riga["azione"] == "approva_copione" and riga["esito"] == "ok"


def test_lettura_si_ferma_se_il_client_va_via(client, agente):
    (agente.radice / "tools" / "script_show.py").write_text(LENTO)
    ctx = client.app.state.ctx
    from app.servizi.azioni import esegui_e_valuta

    async def scenario():
        compito = asyncio.ensure_future(esegui_e_valuta(ctx, ctx.ingresso._utente, "copione", {"run": "run-uno"}))
        await asyncio.sleep(0.4)
        compito.cancel()
        with pytest.raises(asyncio.CancelledError):
            await compito
        await asyncio.sleep(1.2)

    client.portal.call(scenario)
    assert (agente.radice / "sigterm.log").exists() and not (agente.radice / "fatto.log").exists()


# --- Correzioni dopo la revisione di A2 -------------------------------------

def test_produci_ravvicinati_sulla_stessa_run(client, produce, monkeypatch):
    """A2 MEDIA-1: fra i controlli e l'accodamento c'e' l'await della stima."""
    import app.lavori.coda as modulo
    (produce.radice / "fermo").write_text("")
    originale = modulo.esecutore.esegui

    async def stima_lenta(*a, **k):
        await asyncio.sleep(0.5)
        return await originale(*a, **k)
    monkeypatch.setattr(modulo.esecutore, "esegui", stima_lenta)
    ctx = client.app.state.ctx

    async def tre():
        u = ctx.ingresso._utente
        return await asyncio.gather(*(ctx.coda.accoda(u, "produci", {"run": "run-uno"}, impronta=PRODUCI["impronta"]) for _ in range(3)),
                                    return_exceptions=True)
    esiti = client.portal.call(tre)
    accodati = [e for e in esiti if not isinstance(e, Exception)]
    rifiutati = [e.error_type for e in esiti if isinstance(e, Exception)]
    assert len(accodati) == 1 and rifiutati == ["LavoroGiaInCorso", "LavoroGiaInCorso"]
    (produce.radice / "fermo").unlink()


def test_coda_piena_con_stime_in_volo(ambiente, produce, monkeypatch):
    import app.lavori.coda as modulo
    originale = modulo.esecutore.esegui

    async def stima_lenta(*a, **k):
        await asyncio.sleep(0.5)
        return await originale(*a, **k)
    with client_per(ambiente, CODA_MAX="1") as c:
        monkeypatch.setattr(modulo.esecutore, "esegui", stima_lenta)
        (produce.radice / "output" / "run-tre").mkdir()
        ctx = c.app.state.ctx
        ctx.coda._in_corso = type("Occupato", (), {"run": "altra"})()  # worker occupato

        async def due():
            u = ctx.ingresso._utente
            return await asyncio.gather(ctx.coda.accoda(u, "produci", {"run": "run-uno"}, impronta=PRODUCI["impronta"]),
                                        ctx.coda.accoda(u, "produci", {"run": "run-due"}, impronta=PRODUCI["impronta"]), return_exceptions=True)
        esiti = c.portal.call(due)
        assert sorted(type(e).__name__ for e in esiti) == ["ErroreController", "JSONResponse"]
        ctx.coda._attesa.clear()
        ctx.coda._in_corso = None


def test_interruzione_durante_l_avvio_del_processo(client, produce, monkeypatch):
    """A2 MEDIA-2: la richiesta arriva prima che il processo esista: il SIGTERM parte appena c'e'."""
    import app.lavori.coda as modulo
    (produce.radice / "fermo").write_text("")
    originale = modulo.esecutore.avvia_processo
    ctx = client.app.state.ctx

    async def avvio_lento(*a, **k):
        lavoro = ctx.coda.lavoro_in_corso()
        if lavoro is not None:  # (non per la stima, che non e' un lavoro)
            ctx.coda._sigterm(lavoro, motivo="test")  # l'interruzione arriva qui
            await asyncio.sleep(0.2)
        return await originale(*a, **k)
    monkeypatch.setattr(modulo.esecutore, "avvia_processo", avvio_lento)
    inviati = []
    originale_send = asyncio.subprocess.Process.send_signal
    monkeypatch.setattr(asyncio.subprocess.Process, "send_signal", lambda self, s: (inviati.append(s), originale_send(self, s)))
    lavoro = client.post("/api/episodi/run-uno/produzione", json=PRODUCI).json()["dati"]["lavoro"]
    esito = aspetta(client, finito(lavoro))["recenti"][0]["esito"]
    # il processo riceve il SIGTERM (uno solo) appena esiste: se non ha ancora i suoi
    # gestori esce subito (Interrupted), altrimenti si ferma fra una battuta e l'altra
    assert esito["error_type"] in ("Interrupted", "ProductionInterrupted"), esito
    assert inviati == [signal.SIGTERM]
    eventi = produce.radice / "output" / "run-uno" / "_eventi.jsonl"
    assert not eventi.exists() or '"finita"' not in eventi.read_text()


def test_interruzione_prima_dell_avvio_non_lancia(client, produce, ambiente):
    (produce.radice / "fermo").write_text("")
    client.post("/api/episodi/run-uno/produzione", json=PRODUCI)
    aspetta(client, lambda d: d["in_corso"] is not None)
    secondo = client.post("/api/episodi/run-due/produzione", json=PRODUCI).json()["dati"]["lavoro"]
    lavoro = client.app.state.ctx.coda.trova(secondo)
    client.app.state.ctx.coda._sigterm(lavoro, motivo="test")  # richiesta mentre e' ancora in coda
    (produce.radice / "fermo").unlink()
    v = aspetta(client, finito(secondo))
    esito = [l for l in v["recenti"] if l["lavoro"] == secondo][0]
    assert esito["stato"] == "annullato"
    assert json.loads((produce.radice / "argv.json").read_text()) == ["--run-dir=run-uno", "--events"]


def test_spegnimento_durante_l_avvio_aspetta_il_lavoro(ambiente, produce, monkeypatch):
    import app.lavori.coda as modulo
    (produce.radice / "fermo").write_text("")
    originale = modulo.esecutore.avvia_processo
    partito = {}

    async def avvio_lento(*a, **k):
        partito["si"] = True
        await asyncio.sleep(0.5)
        return await originale(*a, **k)
    monkeypatch.setattr(modulo.esecutore, "avvia_processo", avvio_lento)
    with client_per(ambiente) as c:
        c.post("/api/episodi/run-uno/produzione", json=PRODUCI, headers={"X-LIL-Richiesta": "1"})
        fine = time.monotonic() + 5
        while not partito and time.monotonic() < fine:
            time.sleep(0.02)
        coda = c.app.state.ctx.coda
    # uscendo dal client il lifespan chiude la coda: SIGTERM appena il processo esiste,
    # e attesa della fine del lavoro (non resta un processo orfano che continua)
    lavoro = coda._storico[-1]
    assert lavoro.sigterm_inviato and lavoro.fatto.is_set()
    assert lavoro.esito["error_type"] in ("Interrupted", "ProductionInterrupted"), lavoro.esito
    eventi = produce.radice / "output" / "run-uno" / "_eventi.jsonl"
    assert not eventi.exists() or '"finita"' not in eventi.read_text()


def test_octet_stream_solo_sui_caricamenti(client):
    """A2 MEDIA-3: un JSON grande mandato come binario a una rotta JSON."""
    grande = json.dumps({"intro": "x" * 40_000_000}).encode()
    r = client.put("/api/episodi/run-uno/musiche", content=grande, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code in (413, 415)
    r = client.put("/api/episodi/run-uno/musiche", content=b'{"intro": null}', headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 415
    r = client.post("/api/documenti", content=b"{}", headers={"Content-Type": "application/json", "X-LIL-Nome-File": "x.pdf"})
    assert r.status_code == 415
