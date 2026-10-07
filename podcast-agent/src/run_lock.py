"""Lock di una run: chi sta lavorando su un episodio, e chi deve aspettare.

Due lock distinti, perche' proteggono cose diverse:

  - **lock di lavoro** (`_lavoro.lock`): un'operazione LUNGA in corso sulla
    run (scrittura del copione via API, produzione). Si prende senza attesa:
    se e' gia' preso, la seconda operazione si ferma subito con `RunBusy`
    invece di accodarsi per minuti. E' anche quello da cui l'Archivio DERIVA
    lo stato "in corso": un processo morto rilascia il lock da solo, un campo
    `in_corso: true` scritto su disco resterebbe vero per sempre. Accanto,
    `_lavoro.json` dice QUALE operazione e da quando, e viene letto solo
    mentre il lock e' tenuto (altrimenti e' un residuo e si ignora);
  - **lock della sessione** (`_script_session.json.lock`): le modifiche al
    copione (salva, modifica una battuta, ripristina, approva). Sono brevi:
    si aspetta il proprio turno. Il contenuto va riletto DENTRO il lock.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .errors import DomainError
from .fileio import LockTimeout, file_lock, is_locked, write_json_atomic
from .script_session import FILENAME as SESSION_FILENAME

WORK_LOCK_NAME = "_lavoro"
WORK_INFO_FILENAME = "_lavoro.json"


class RunBusy(DomainError):
    """Sulla run e' gia' in corso un'altra operazione lunga."""


def _work_target(run_dir: Path) -> Path:
    return Path(run_dir) / WORK_LOCK_NAME


@contextmanager
def work_lock(run_dir: Path | str, operation: str, **info: Any) -> Iterator[None]:
    """Tiene il lock di lavoro per tutta la durata di `operation`."""
    run_dir = Path(run_dir)
    with ExitStack() as stack:
        # Solo l'ACQUISIZIONE si traduce in RunBusy: un LockTimeout sollevato
        # dentro l'operazione (es. sul registro delle voci) e' un altro
        # problema e deve arrivare al chiamante cosi' com'e'.
        try:
            stack.enter_context(file_lock(_work_target(run_dir), timeout=0))
        except LockTimeout as exc:
            current = current_work(run_dir) or {}
            raise RunBusy(
                "Su questo episodio e' gia' in corso un'altra operazione"
                + (f" ({current['operazione']}, dal {current.get('dal')})" if current.get("operazione") else "")
                + ": aspetta che finisca.",
                details={"in_corso": current or None},
            ) from exc
        write_json_atomic(
            run_dir / WORK_INFO_FILENAME,
            {
                "operazione": operation,
                "dal": datetime.now().replace(microsecond=0).isoformat(),
                "pid": os.getpid(),
                **info,
            },
        )
        yield


#: Campi di `_lavoro.json` che restano nel file (servono a chi indaga sul
#: server) ma non vanno nella vista: la UI non deve vedere i pid dei processi.
_PRIVATE_WORK_FIELDS = ("pid",)


def current_work(run_dir: Path | str) -> dict[str, Any] | None:
    """L'operazione lunga in corso ORA sulla run, o None.

    Deriva dal lock, non dal file: `_lavoro.json` conta solo se il lock e'
    effettivamente tenuto da qualcuno. Il `pid` resta nel file e non viene
    restituito (revisione di sicurezza C0+C1, BASSA-2).
    """
    run_dir = Path(run_dir)
    if not is_locked(_work_target(run_dir)):
        return None
    info_file = run_dir / WORK_INFO_FILENAME
    try:
        info = json.loads(info_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        info = {}
    if not isinstance(info, dict) or not info:
        return {"operazione": "sconosciuta"}
    return {key: value for key, value in info.items() if key not in _PRIVATE_WORK_FIELDS}


@contextmanager
def session_lock(run_dir: Path | str) -> Iterator[None]:
    """Serializza le modifiche alla sessione del copione."""
    with file_lock(Path(run_dir) / SESSION_FILENAME):
        yield


__all__ = ["RunBusy", "WORK_INFO_FILENAME", "current_work", "session_lock", "work_lock"]
