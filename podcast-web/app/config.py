"""Configurazione del controller: tutto da variabili d'ambiente.

`carica_config()` legge l'ambiente, controlla i valori e restituisce un oggetto
immutabile. Un valore mancante o sbagliato ferma l'avvio con un messaggio chiaro
(`ErroreConfigurazione`), mai con un default pericoloso.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

RADICE_WEB = Path(__file__).resolve().parent.parent

MODI_INGRESSO = ("demo", "cms")
HOST_LOOPBACK = ("127.0.0.1",)


class ErroreConfigurazione(Exception):
    """Configurazione non valida: il controller non parte."""


@dataclass(frozen=True)
class Config:
    auth_mode: str
    host: str
    port: int
    agent_dir: Path
    agent_python: Path
    agent_path_extra: tuple[str, ...]
    agent_versione_attesa: str
    segreto: bytes | None
    segreto_precedente: bytes | None
    firma_tolleranza_s: int
    demo_utente: str
    demo_nome: str
    permessi_file: Path | None
    registro_file: Path
    gettone_durata_s: int
    coda_max: int
    max_voce_bytes: int
    max_brano_bytes: int
    log_livello: str

    @property
    def output_dir(self) -> Path:
        return self.agent_dir / "output"


def _intero(env: Mapping[str, str], nome: str, default: int, minimo: int, massimo: int) -> int:
    grezzo = env.get(nome, "").strip()
    if not grezzo:
        return default
    if not grezzo.isdigit():
        raise ErroreConfigurazione(f"{nome} deve essere un numero intero, non {grezzo!r}")
    valore = int(grezzo)
    if not minimo <= valore <= massimo:
        raise ErroreConfigurazione(f"{nome} deve stare fra {minimo} e {massimo}")
    return valore


def _segreto(env: Mapping[str, str], nome: str) -> bytes | None:
    grezzo = env.get(nome, "")
    if not grezzo:
        return None
    valore = grezzo.encode("utf-8")
    if len(valore) < 32:
        raise ErroreConfigurazione(f"{nome} deve essere lungo almeno 32 byte")
    return valore


def carica_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env

    auth_mode = env.get("AUTH_MODE", "").strip()
    if auth_mode not in MODI_INGRESSO:
        raise ErroreConfigurazione(f"AUTH_MODE deve essere uno di {', '.join(MODI_INGRESSO)}")

    host = env.get("HOST", "127.0.0.1").strip()
    if auth_mode == "demo" and host not in HOST_LOOPBACK:
        raise ErroreConfigurazione("con AUTH_MODE=demo il server ascolta solo su 127.0.0.1")
    port = _intero(env, "PORT", 8765, 1, 65535)

    grezzo_dir = env.get("AGENT_DIR", "").strip()
    if not grezzo_dir:
        raise ErroreConfigurazione("AGENT_DIR mancante: cartella di podcast-agent")
    agent_dir = Path(grezzo_dir)
    if not agent_dir.is_absolute():
        raise ErroreConfigurazione("AGENT_DIR deve essere un percorso assoluto")
    agent_dir = agent_dir.resolve()
    if not agent_dir.is_dir():
        raise ErroreConfigurazione("AGENT_DIR non e' una cartella")
    # Sempre il python della venv dell'agente: non e' configurabile.
    agent_python = agent_dir / ".venv" / "bin" / "python"
    if not agent_python.exists():
        raise ErroreConfigurazione("manca .venv/bin/python dentro AGENT_DIR")

    extra = tuple(p for p in env.get("AGENT_PATH_EXTRA", "").split(":") if p)
    for parte in extra:
        if not Path(parte).is_absolute():
            raise ErroreConfigurazione("AGENT_PATH_EXTRA accetta solo cartelle assolute")

    versione = env.get("AGENT_VERSIONE_ATTESA", "").strip()
    if not versione:
        raise ErroreConfigurazione("AGENT_VERSIONE_ATTESA mancante")

    segreto = _segreto(env, "LIL_SEGRETO")
    segreto_precedente = _segreto(env, "LIL_SEGRETO_PRECEDENTE")
    if auth_mode == "cms" and segreto is None:
        raise ErroreConfigurazione("con AUTH_MODE=cms serve LIL_SEGRETO (almeno 32 byte)")

    demo_utente = env.get("DEMO_UTENTE", "demo").strip() or "demo"
    demo_nome = env.get("DEMO_NOME", "Utente demo").strip() or demo_utente

    permessi = env.get("PERMESSI_FILE", "").strip()
    registro = env.get("REGISTRO_FILE", "").strip()
    registro_file = Path(registro) if registro else RADICE_WEB / "dati" / "registro_attivita.jsonl"
    if not registro_file.is_absolute():
        raise ErroreConfigurazione("REGISTRO_FILE deve essere un percorso assoluto")

    livello = env.get("LOG_LIVELLO", "INFO").strip().upper()
    if livello not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ErroreConfigurazione("LOG_LIVELLO non valido")

    return Config(
        auth_mode=auth_mode,
        host=host,
        port=port,
        agent_dir=agent_dir,
        agent_python=agent_python,
        agent_path_extra=extra,
        agent_versione_attesa=versione,
        segreto=segreto,
        segreto_precedente=segreto_precedente,
        firma_tolleranza_s=_intero(env, "FIRMA_TOLLERANZA_S", 60, 5, 300),
        demo_utente=demo_utente,
        demo_nome=demo_nome,
        permessi_file=Path(permessi) if permessi else None,
        registro_file=registro_file,
        gettone_durata_s=_intero(env, "GETTONE_DURATA_S", 300, 30, 1800),
        coda_max=_intero(env, "CODA_MAX", 20, 1, 500),
        max_voce_bytes=_intero(env, "MAX_VOCE_BYTES", 20 * 1024 * 1024, 1024, 200 * 1024 * 1024),
        max_brano_bytes=_intero(env, "MAX_BRANO_BYTES", 50 * 1024 * 1024, 1024, 500 * 1024 * 1024),
        log_livello=livello,
    )
