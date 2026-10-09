"""Controlli di avvio: se uno non passa, il controller non parte.

1. versione dell'agente: `<AGENT_DIR>/VERSION` uguale ad AGENT_VERSIONE_ATTESA;
2. `doctor.py --web`: exit 0 e controllo `web_mode` superato;
3. `settings_show.py`: limiti dei documenti ed estensioni audio, cosi' i
   caricamenti usano gli stessi numeri dell'agente.

`/salute` ricontrolla la versione ogni `INTERVALLO_SALUTE_S` (un aggiornamento
dell'agente a controller acceso fa diventare la salute 503).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from app.agente import esecutore
from app.config import Config

log = logging.getLogger("podcast_web.avvio")

VERSIONE_MAX_BYTE = 100
INTERVALLO_SALUTE_S = 300


class ErroreAvvio(Exception):
    """Un controllo di avvio non e' passato."""


def leggi_versione(config: Config) -> str:
    percorso = config.agent_dir / "VERSION"
    try:
        fd = os.open(percorso, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ErroreAvvio(f"file VERSION dell'agente non leggibile ({exc.__class__.__name__})") from None
    try:
        dati = os.read(fd, VERSIONE_MAX_BYTE + 1)
    finally:
        os.close(fd)
    if len(dati) > VERSIONE_MAX_BYTE:
        raise ErroreAvvio("file VERSION dell'agente troppo lungo")
    return dati.decode("utf-8", errors="replace").strip()


def verifica_versione(config: Config) -> None:
    trovata = leggi_versione(config)
    if trovata != config.agent_versione_attesa:
        raise ErroreAvvio(f"versione dell'agente {trovata!r}, attesa {config.agent_versione_attesa!r}")


async def verifica_avvio(config: Config) -> dict[str, Any]:
    """Esegue i controlli; restituisce le impostazioni dell'agente."""
    verifica_versione(config)

    doctor = await esecutore.esegui(config, "diagnostica", {})
    if doctor.timeout or doctor.json is None:
        raise ErroreAvvio("doctor.py --web non ha risposto con un JSON")
    controlli = doctor.json.get("checks") if isinstance(doctor.json.get("checks"), list) else []
    web_mode = next((c for c in controlli if isinstance(c, dict) and c.get("name") == "web_mode"), None)
    if web_mode is None or web_mode.get("ok") is not True:
        raise ErroreAvvio("doctor.py --web: PODCAST_AGENT_WEB non arriva ai sottoprocessi (controllo web_mode)")
    if doctor.exit_code != 0 or doctor.json.get("ok") is not True:
        falliti = [c.get("name") for c in controlli if isinstance(c, dict) and c.get("required") and not c.get("ok")]
        raise ErroreAvvio(f"doctor.py --web: controlli obbligatori non superati: {falliti}")

    impostazioni = await esecutore.esegui(config, "impostazioni", {})
    dati = (impostazioni.json or {}).get("impostazioni") if impostazioni.ok else None
    if not isinstance(dati, dict):
        raise ErroreAvvio("settings_show.py non ha dato le impostazioni")
    documenti = dati.get("documenti") or {}
    audio = dati.get("audio") or {}
    if not isinstance(documenti.get("max_bytes"), int) or not isinstance(audio.get("accepted_extensions"), list):
        raise ErroreAvvio("settings_show.py: mancano documenti.max_bytes o audio.accepted_extensions")
    verifica_cartelle(config, dati.get("cartelle") or {})
    log.info("avvio: agente %s, doctor --web superato", config.agent_versione_attesa)
    return dati


CARTELLE_LIBRERIE = ("documenti", "voci", "musiche", "archivio")


def verifica_cartelle(config: Config, cartelle: dict[str, Any]) -> None:
    """Le librerie e l'archivio devono essere cartelle vere DENTRO AGENT_DIR (non link):
    e' li' che il controller carica e scarica i file (A2 NOTA-5). `settings_show` le da'
    per nome: una cartella configurata fuori dall'agente non si trova qui."""
    for chiave in CARTELLE_LIBRERIE:
        nome = cartelle.get(chiave)
        if not isinstance(nome, str) or not nome or "/" in nome or "\\" in nome or nome.startswith(".") or ".." in nome:
            raise ErroreAvvio(f"cartella '{chiave}' dell'agente non valida in settings_show: {nome!r}")
        percorso = config.agent_dir / nome
        if percorso.is_symlink() or not percorso.is_dir():
            raise ErroreAvvio(f"la cartella '{chiave}' ({nome}) deve esistere dentro AGENT_DIR e non essere un link")


class StatoSalute:
    """Stato per /salute: avvio superato + versione ricontrollata ogni tanto."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._pronto = False
        self._ultimo = 0.0
        self._ultimo_ok = False
        self._lock = threading.Lock()

    def segna_pronto(self) -> None:
        with self._lock:
            self._pronto = True
            self._ultimo = time.monotonic()
            self._ultimo_ok = True

    def verifica(self) -> bool:
        with self._lock:
            if not self._pronto:
                return False
            if time.monotonic() - self._ultimo >= INTERVALLO_SALUTE_S:
                try:
                    verifica_versione(self._config)
                    self._ultimo_ok = True
                except ErroreAvvio as exc:
                    log.error("salute: %s", exc)
                    self._ultimo_ok = False
                self._ultimo = time.monotonic()
            return self._ultimo_ok
