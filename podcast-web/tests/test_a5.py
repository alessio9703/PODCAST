"""A5: le rotte aggiunte (confronto versioni; poi librerie, retake e forma d'onda, lezioni e diagnostica)."""

from __future__ import annotations


def argv(agente, tool):
    return agente.chiamate(tool)[-1]["argv"]


# --- Confronto versioni -----------------------------------------------------

def test_confronto_con_la_corrente(client, agente):
    r = client.get("/api/episodi/run-uno/copione/confronto?da=1&solo_modifiche=1")
    assert r.status_code == 200
    assert argv(agente, "script_versions.py") == ["--run-dir=run-uno", "--diff", "1", "--only-changes"]


def test_confronto_fra_due_versioni_tutto_il_copione(client, agente):
    assert client.get("/api/episodi/run-uno/copione/confronto?da=1&a=3").status_code == 200
    assert argv(agente, "script_versions.py") == ["--run-dir=run-uno", "--diff", "1", "3"]


def test_confronto_con_valori_non_interi(client, agente):
    for q in ("da=1;rm", "da=-1", "da=1&a=--restore", "da=0"):
        r = client.get(f"/api/episodi/run-uno/copione/confronto?{q}")
        assert r.status_code in (400, 422), q
    assert agente.chiamate("script_versions.py") == []


def test_mostra_versione(client, agente):
    assert client.get("/api/episodi/run-uno/copione/versioni/2").status_code == 200
    assert argv(agente, "script_versions.py") == ["--run-dir=run-uno", "--show=2"]


def test_confronto_di_un_episodio_inesistente(client, agente):
    assert client.get("/api/episodi/nessuno/copione/confronto?da=1").status_code == 404
    assert agente.chiamate("script_versions.py") == []


# --- Librerie: caricamento di voci e brani ------------------------------------

import pytest  # noqa: E402

from app.file.caricamenti import firma_audio  # noqa: E402
from app.ingresso.cms import codifica_header  # noqa: E402
from tests.conftest import client_per, righe_registro  # noqa: E402

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64


def carica(client, url, nome, dati, gettone=None):
    h = {"Content-Type": "application/octet-stream", "X-LIL-Nome-File": codifica_header(nome)}
    if gettone:
        h["X-LIL-Gettone"] = gettone
    return client.post(url, content=dati, headers=h)


@pytest.mark.parametrize("est, dati, ok", [
    (".wav", WAV, True), (".mp3", MP3, True), (".mp3", b"\xff\xfb\x90\x00" + b"\x00" * 20, True),
    (".m4a", b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 20, True), (".flac", b"fLaC" + b"\x00" * 20, True),
    (".ogg", b"OggS" + b"\x00" * 20, True), (".webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 20, True),
    (".mp3", b"ffconcat version 1.0\nfile x\n", False), (".wav", b"#EXTM3U\nhttp://x\n", False),
    (".m4a", WAV, False), (".exe", WAV, False),
])
def test_firme_audio(est, dati, ok):
    assert firma_audio(est, dati[:16]) is ok


def test_carica_voce_e_registro(client, agente, ambiente):
    r = carica(client, "/api/voci", "Anna Neri.wav", WAV)
    assert r.status_code == 201, r.text
    assert (agente.radice / "voci_disponibili" / "Anna Neri.wav").read_bytes() == WAV
    riga = righe_registro(ambiente)[-1]
    assert riga["azione"] == "carica_voce" and riga["dettagli"]["libreria"] == "voci"


@pytest.mark.parametrize("nome, dati", [
    ("Anna.json", b'{"consenso": {}}'), ("Anna.m4a", b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 20),
    ("Anna.mp3", b"ffconcat version 1.0\nfile /etc/passwd\n"), ("../Anna.wav", WAV), (".Anna.wav", WAV),
])
def test_voce_rifiutata(client, agente, nome, dati):
    # .m4a non e' fra le estensioni dell'agente finto (.wav, .mp3)
    r = carica(client, "/api/voci", nome, dati)
    assert r.status_code == 415, (nome, r.text)
    assert list((agente.radice / "voci_disponibili").iterdir()) == []


def test_voce_troppo_grande(client, agente, monkeypatch):
    from app.api import librerie
    monkeypatch.setattr(librerie, "MAX_VOCE", 100)
    r = carica(client, "/api/voci", "Anna Neri.wav", WAV + b"\x00" * 200)
    assert r.status_code == 413
    assert list((agente.radice / "voci_disponibili").iterdir()) == []


def test_sostituzione_di_una_voce_con_gettone(client, agente, ambiente):
    (agente.radice / "voci_disponibili" / "Anna Neri.mp3").write_bytes(MP3)
    (agente.radice / "voci_disponibili" / "Anna Neri.json").write_text('{"consenso": "x"}')
    r = carica(client, "/api/voci", "anna_neri.wav", WAV)
    assert r.status_code == 409 and r.json()["error_type"] == "FileEsistente"
    gettone = r.json()["gettone"]
    # un gettone per i documenti non vale per le voci
    r2 = carica(client, "/api/documenti", "Anna Neri.txt", b"ciao")
    assert r2.status_code == 201
    r = carica(client, "/api/voci", "anna_neri.wav", WAV, gettone=gettone)
    assert r.status_code == 201, r.text
    cartella = agente.radice / "voci_disponibili"
    # il .json (consenso) non si tocca mai: l'agente lo vedra' come "superato"
    assert sorted(p.name for p in cartella.iterdir()) == ["Anna Neri.json", "anna_neri.wav"]
    assert righe_registro(ambiente)[-1]["azione"] == "sostituisci_voce"


def test_gettone_di_un_documento_non_sostituisce_una_voce(client, agente):
    (agente.radice / "documenti" / "Anna Neri.txt").write_text("x")
    g = carica(client, "/api/documenti", "Anna Neri.txt", b"y").json()["gettone"]
    (agente.radice / "voci_disponibili" / "Anna Neri.mp3").write_bytes(MP3)
    r = carica(client, "/api/voci", "Anna Neri.wav", WAV, gettone=g)
    assert r.status_code in (400, 403, 409) and r.json()["error_type"] == "GettoneNonValido"
    assert (agente.radice / "voci_disponibili" / "Anna Neri.mp3").exists()


def test_carica_brano_per_categoria(client, agente, ambiente):
    (agente.radice / "musiche" / "intro").mkdir()
    r = carica(client, "/api/musiche/intro", "Sigla.mp3", MP3)
    assert r.status_code == 201, r.text
    assert (agente.radice / "musiche" / "intro" / "Sigla.mp3").exists()
    assert righe_registro(ambiente)[-1]["dettagli"]["libreria"] == "musiche/intro"
    # fuori dai caricamenti ammessi: il corpo binario si rifiuta prima della rotta
    assert carica(client, "/api/musiche/altro", "Sigla.mp3", MP3).status_code in (404, 415)
    assert carica(client, "/api/musiche/..", "Sigla.mp3", MP3).status_code in (404, 415)
    assert sorted(p.name for p in (agente.radice / "musiche").iterdir()) == ["intro"]


def test_caricamenti_audio_con_i_permessi(ambiente, agente):
    from tests.conftest import header_firmati, SEGRETO
    amb = {**ambiente, "AUTH_MODE": "cms", "LIL_SEGRETO": SEGRETO}
    (agente.radice / "musiche" / "intro").mkdir()
    with client_per(amb) as c:
        for url, ruoli, atteso in (("/api/voci", "autore", 403), ("/api/voci", "gestore_voci", 201),
                                   ("/api/musiche/intro", "autore", 403), ("/api/musiche/intro", "amministratore", 201)):
            h = {**header_firmati("POST", url, ruoli=ruoli), "X-LIL-Richiesta": "1",
                 "Content-Type": "application/octet-stream", "X-LIL-Nome-File": codifica_header(f"N{ruoli}.wav")}
            assert c.post(url, content=WAV, headers=h).status_code == atteso, (url, ruoli)


# --- Librerie: ascolto, nome pronunciato, metadati dei brani -----------------

VOCI = {"exit": 0, "json": {"ok": True, "voices": [
    {"name": "Anna Neri", "file": "Anna Neri.wav"}, {"name": "Furbo", "file": "Furbo.json"},
    {"name": "Fuori", "file": "../fuori.wav"}]}}


def test_campione_di_una_voce(client, agente):
    (agente.radice / "voci_disponibili" / "Anna Neri.wav").write_bytes(WAV)
    agente.scenario("voices_list.py", VOCI)
    r = client.get("/api/voci/Anna Neri/campione", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == b"RIFF" and r.headers["content-type"] == "audio/wav"


@pytest.mark.parametrize("nome", ["Furbo", "Fuori", "Nessuno"])
def test_campione_solo_di_file_audio_elencati(client, agente, nome):
    (agente.radice / "voci_disponibili" / "Furbo.json").write_text("{}")
    agente.scenario("voices_list.py", VOCI)
    assert client.get(f"/api/voci/{nome}/campione").status_code == 404


def test_anteprima_di_un_brano(client, agente):
    (agente.radice / "musiche" / "outro").mkdir()
    (agente.radice / "musiche" / "outro" / "Chiusura.mp3").write_bytes(MP3)
    agente.scenario("music_list.py", {"exit": 0, "json": {"ok": True, "outro": [{"name": "Chiusura", "extension": ".mp3"}],
                                                          "intro": [{"name": "Chiusura", "extension": ".json"}]}})
    r = client.get("/api/musiche/outro/Chiusura/file")
    assert r.status_code == 200 and r.content == MP3
    assert client.get("/api/musiche/intro/Chiusura/file").status_code == 404
    assert client.get("/api/musiche/sottofondo/Chiusura/file").status_code == 404


def test_nome_pronunciato_con_conferma(client, agente, ambiente):
    agente.scenario("voice_meta.py", [
        {"exit": 3, "json": {"ok": False, "error_type": "SpokenNameIncomplete", "error": "manca il cognome", "details": {}}},
        {"exit": 0, "json": {"ok": True, "voce": "Anna Neri", "nome_pronunciato": "Anna"}},
    ])
    r = client.patch("/api/voci/Anna Neri", json={"nome_pronunciato": "Anna"})
    assert r.status_code == 409 and r.json()["error_type"] == "SpokenNameIncomplete"
    argv = agente.chiamate("voice_meta.py")[-1]["argv"]
    assert argv == ["--name=Anna Neri", "--spoken-name=Anna", "--by=Utente Demo"] or argv[:2] == ["--name=Anna Neri", "--spoken-name=Anna"]
    assert "--accept-incomplete-name" not in argv
    r = client.post(f"/api/conferme/{r.json()['gettone']}", json={})
    assert r.status_code == 200, r.text
    assert "--accept-incomplete-name" in agente.chiamate("voice_meta.py")[-1]["argv"]
    assert righe_registro(ambiente)[-1]["azione"] == "nome_voce"


def test_metadati_brano(client, agente):
    assert client.patch("/api/musiche/intro/Piano calmo", json={"licenza": "CC-BY 4.0, Autore X"}).status_code == 200
    assert agente.chiamate("music_meta.py")[-1]["argv"][:3] == ["--category=intro", "--name=Piano calmo", "--licenza=CC-BY 4.0, Autore X"]
    client.patch("/api/musiche/outro/Chiusura", json={"parlato": True, "trascrizione": "Grazie"})
    assert agente.chiamate("music_meta.py")[-1]["argv"][:4] == ["--category=outro", "--name=Chiusura", "--parlato=true", "--trascrizione=Grazie"]
    client.patch("/api/musiche/outro/Chiusura", json={"senza_licenza": True})
    assert "--no-licenza" in agente.chiamate("music_meta.py")[-1]["argv"]
    n = len(agente.chiamate("music_meta.py"))
    assert client.patch("/api/musiche/outro/Chiusura", json={}).status_code == 400
    assert client.patch("/api/musiche/outro/Chiusura", json={"licenza": "x", "senza_licenza": True}).status_code == 400
    assert client.patch("/api/musiche/altro/Chiusura", json={"licenza": "x"}).status_code == 404
    assert client.patch("/api/musiche/outro/Chiusura", json={"by": "altro"}).status_code in (400, 422)
    assert len(agente.chiamate("music_meta.py")) == n


def test_anteprima_del_documento_come_testo(client, agente):
    agente.scenario("doc_extract.py", {"exit": 0, "json": {"ok": True, "name": "r.txt", "preview": "I ricavi crescono", "path": "/srv/x/r.txt"}})
    dati = client.get("/api/documenti/r.txt/anteprima?caratteri=100").json()["dati"]
    assert dati["anteprima"] == "I ricavi crescono" and "path" not in dati and "preview" not in dati
    agente.scenario("doc_extract.py", {"exit": 0, "json": {"ok": True, "name": "r.txt", "preview": "/srv/agente/documenti/r.txt"}})
    assert "anteprima" not in client.get("/api/documenti/r.txt/anteprima").json()["dati"]
