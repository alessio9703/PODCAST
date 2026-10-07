"""Scritture atomiche e lock su file, per i file che piu' processi toccano.

Perche' esiste: con l'interfaccia web piu' utenti lanciano tool nello stesso
momento. Due problemi diversi, due strumenti diversi:

  - **scrittura atomica**: chi legge vede il file vecchio o quello nuovo, mai
    uno a meta'. Si scrive su un file temporaneo nella stessa cartella e poi
    lo si rinomina sopra quello vero (`os.replace` e' atomico sullo stesso
    filesystem). Il temporaneo ha un nome UNICO: con un nome fisso
    (`registro.json.tmp`) due processi scriverebbero sullo stesso temporaneo e
    uno rinominerebbe il contenuto dell'altro;
  - **lock**: la scrittura atomica non basta per un leggi-modifica-scrivi.
    Due processi che leggono il registro, aggiungono ciascuno la propria voce
    e lo riscrivono producono un file valido... con una voce sola: l'ultimo
    cancella il lavoro del primo. Il lock serializza l'intero ciclo, e il
    contenuto va RILETTO dentro il lock, non prima.

Il lock e' su un file accanto (`<nome>.lock`), non sul file stesso: il file
vero viene sostituito a ogni scrittura, e un lock su un inode che sparisce non
protegge niente. Funziona su macOS/Linux (fcntl) e su Windows (msvcrt).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import DomainError

if os.name == "nt":  # pragma: no cover - verificato solo su Windows
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


#: Quanto aspettare un lock prima di arrendersi. Le operazioni protette sono
#: brevi (leggere e riscrivere un JSON): 30 secondi di attesa vogliono dire
#: che qualcosa e' bloccato, non che c'e' coda.
DEFAULT_LOCK_TIMEOUT = 30.0
_POLL_SECONDS = 0.05


class LockTimeout(DomainError):
    """Il lock non si e' liberato entro il tempo massimo."""


def lock_path_for(target: Path | str) -> Path:
    target = Path(target)
    return target.with_name(target.name + ".lock")


@contextmanager
def file_lock(target: Path | str, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Iterator[None]:
    """Lock esclusivo fra processi su `target` (tramite `<target>.lock`).

    `timeout=0` prova una volta sola. Alla scadenza solleva LockTimeout
    (exit 1): meglio un errore che dice chi aspetta cosa, che un processo
    appeso per sempre.
    """
    lock_file = lock_path_for(target)
    # La cartella NON si crea qui: prendere un lock non deve far nascere
    # alberi di cartelle in un percorso arrivato da input (revisione di
    # sicurezza C0+C1, ALTA-2). Se manca, e' un errore di chi chiama.
    if not lock_file.parent.is_dir():
        raise FileNotFoundError(f"Cartella inesistente per il lock: {lock_file.parent.name}")
    fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if os.name == "nt":  # pragma: no cover
            # msvcrt blocca byte, non file: serve almeno un byte da bloccare.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
        deadline = time.monotonic() + max(0.0, timeout)
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"{Path(target).name} e' in uso da un'altra operazione da piu' di "
                    f"{timeout:g} secondi. Riprova fra poco; se resta bloccato, "
                    f"controlla che nessun comando sia rimasto appeso.",
                    details={"file": Path(target).name, "timeout_seconds": timeout},
                )
            time.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def is_locked(target: Path | str) -> bool:
    """True se un altro processo tiene ORA il lock su `target`.

    Serve a DERIVARE uno stato ("in generazione") invece di scriverlo in un
    campo: un processo morto rilascia il lock da solo, un campo `running: true`
    resterebbe vero per sempre.
    """
    lock_file = lock_path_for(target)
    if not lock_file.is_file():
        return False
    fd = os.open(str(lock_file), os.O_RDWR)
    try:
        if _try_lock(fd):
            _unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


def write_bytes_atomic(target: Path | str, data: bytes) -> Path:
    """Scrive `data` in `target` in modo atomico (temporaneo unico + rename)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def write_text_atomic(target: Path | str, text: str) -> Path:
    return write_bytes_atomic(target, text.encode("utf-8"))


def write_json_atomic(target: Path | str, payload: Any) -> Path:
    """JSON indentato, UTF-8, con newline finale: lo stesso formato di sempre."""
    return write_text_atomic(
        target, json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )


__all__ = [
    "DEFAULT_LOCK_TIMEOUT",
    "LockTimeout",
    "file_lock",
    "is_locked",
    "lock_path_for",
    "write_bytes_atomic",
    "write_text_atomic",
    "write_json_atomic",
]
