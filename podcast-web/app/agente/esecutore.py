"""L'UNICO punto che lancia l'agente.

Sempre: python di `<AGENT_DIR>/.venv`, cartella corrente = AGENT_DIR,
`PODCAST_AGENT_WEB=1`, ambiente minimo costruito da zero (nessuna chiave: l'agente
le legge dal suo `.env`), argomenti in lista (mai shell), stdin chiuso, nuova
sessione di processo. La riga di comando viene SOLO da `tabella.costruisci_argomenti`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from typing import Any, Mapping

from app.agente.tabella import TABELLA, ErroreTabella, costruisci_argomenti
from app.config import Config

log = logging.getLogger("podcast_web.agente")

PATH_BASE = ("/usr/local/bin", "/usr/bin", "/bin")
STDOUT_MAX = 32 * 1024 * 1024
STDERR_LOG_MAX = 4000
ATTESA_DOPO_SIGTERM_S = 10


@dataclass
class Risultato:
    azione: str
    exit_code: int | None
    json: dict[str, Any] | None
    stderr: str
    durata_s: float
    timeout: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and isinstance(self.json, dict) and self.json.get("ok") is True


def ambiente_minimo(config: Config) -> dict[str, str]:
    path = ":".join((*config.agent_path_extra, *PATH_BASE))
    return {
        "PODCAST_AGENT_WEB": "1",
        "PATH": path,
        "HOME": os.environ.get("HOME", str(config.agent_dir)),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def riga_di_comando(
    config: Config,
    azione: str,
    parametri: Mapping[str, Any],
    *,
    by: str | None = None,
    flag_conferme: tuple[str, ...] = (),
    parametri_nascosti: Mapping[str, str] | None = None,
) -> list[str]:
    cmd = TABELLA.get(azione)
    if cmd is None:
        raise ErroreTabella(f"azione fuori tabella: {azione}")
    argomenti = costruisci_argomenti(
        azione, parametri, output_dir=config.output_dir, by=by,
        flag_conferme=flag_conferme, parametri_nascosti=parametri_nascosti,
    )
    return [str(config.agent_python), f"tools/{cmd.tool}", *argomenti]


async def avvia_processo(config: Config, argv: list[str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(config.agent_dir),
        env=ambiente_minimo(config),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        limit=STDOUT_MAX,
    )


def interpreta(azione: str, exit_code: int | None, stdout: bytes, stderr: bytes, durata: float, timeout: bool) -> Risultato:
    dati: dict[str, Any] | None = None
    if stdout and len(stdout) <= STDOUT_MAX:
        try:
            letto = json.loads(stdout.decode("utf-8"))
            dati = letto if isinstance(letto, dict) else None
        except (UnicodeDecodeError, ValueError):
            dati = None
    testo_err = stderr.decode("utf-8", errors="replace")
    if dati is None and not timeout:
        log.error("%s: stdout non e' un oggetto JSON (exit %s); stderr: %s", azione, exit_code, testo_err[-STDERR_LOG_MAX:])
    elif testo_err.strip():
        log.debug("%s stderr: %s", azione, testo_err[-STDERR_LOG_MAX:])
    return Risultato(azione=azione, exit_code=exit_code, json=dati, stderr=testo_err, durata_s=durata, timeout=timeout)


async def termina(processo: asyncio.subprocess.Process, attesa: float = ATTESA_DOPO_SIGTERM_S) -> None:
    """Per i comandi brevi oltre il timeout: un SIGTERM, poi SIGKILL se non esce.
    (I lavori lunghi hanno la loro interruzione, con un solo SIGTERM: `app/lavori`.)"""
    if processo.returncode is not None:
        return
    try:
        processo.send_signal(signal.SIGTERM)
        await asyncio.wait_for(processo.wait(), attesa)
    except (ProcessLookupError, asyncio.TimeoutError):
        try:
            os.killpg(processo.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        await processo.wait()


async def esegui(
    config: Config,
    azione: str,
    parametri: Mapping[str, Any],
    *,
    by: str | None = None,
    flag_conferme: tuple[str, ...] = (),
    parametri_nascosti: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> Risultato:
    """Lancia un'azione breve e aspetta il risultato (stdout letto per intero a
    processo finito). `timeout` di default: quello della tabella."""
    argv = riga_di_comando(config, azione, parametri, by=by, flag_conferme=flag_conferme,
                           parametri_nascosti=parametri_nascosti)
    limite = TABELLA[azione].timeout if timeout is None else timeout
    inizio = time.monotonic()
    processo = await avvia_processo(config, argv)
    try:
        stdout, stderr = await asyncio.wait_for(processo.communicate(), limite)
    except asyncio.TimeoutError:
        log.error("%s: oltre il timeout di %ss, fermato", azione, limite)
        await termina(processo)
        return Risultato(azione=azione, exit_code=None, json=None, stderr="", durata_s=time.monotonic() - inizio, timeout=True)
    except asyncio.CancelledError:
        await termina(processo)
        raise
    return interpreta(azione, processo.returncode, stdout, stderr, time.monotonic() - inizio, False)
