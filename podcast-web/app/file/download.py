"""Download dei file che l'agente elenca (punti K9-K12 di A2-piano.md).

Chi chiama ha gia' verificato che il nome sia fra quelli dell'agente; qui si apre
il file dentro la cartella prevista (descrittore della cartella, `O_NOFOLLOW`, solo
file regolari) e lo si serve con il supporto di `Range` (un solo intervallo).
"""

from __future__ import annotations

import os
import re
import stat
from typing import Iterator
from urllib.parse import quote

from fastapi.responses import Response, StreamingResponse

from app.servizi.errori import ErroreController

PEZZO = 256 * 1024
_RANGE = re.compile(r"bytes=([0-9]{0,15})-([0-9]{0,15})")

TIPI = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".webm": "audio/webm",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8", ".markdown": "text/plain; charset=utf-8",
    ".rst": "text/plain; charset=utf-8", ".text": "text/plain; charset=utf-8", ".srt": "text/plain; charset=utf-8",
    ".json": "application/json",
}


def intervallo(header: str | None, dimensione: int) -> tuple[int, int] | None | str:
    """(inizio, fine inclusa), None per il file intero, "416" se non soddisfacibile."""
    if not header:
        return None
    m = _RANGE.fullmatch(header.strip())
    if not m:
        return None  # sintassi non valida o piu' intervalli: file intero (RFC 9110)
    a, b = m.group(1), m.group(2)
    if not a and not b:
        return None
    if not a:  # suffisso: gli ultimi n byte
        n = int(b)
        if n == 0 or dimensione == 0:
            return "416"
        return max(0, dimensione - n), dimensione - 1
    inizio = int(a)
    fine = int(b) if b else dimensione - 1
    if inizio >= dimensione or fine < inizio:
        return "416"
    return inizio, min(fine, dimensione - 1)


def disposizione(nome: str, scarica: bool) -> str:
    ascii_sicuro = "".join(c if c.isascii() and c.isprintable() and c not in '"\\;' else "_" for c in nome)
    tipo = "attachment" if scarica else "inline"
    return f"{tipo}; filename=\"{ascii_sicuro}\"; filename*=UTF-8''{quote(nome, safe='')}"


class _Lettore:
    """Legge [inizio, inizio+quanti) dal descrittore e lo chiude sempre: a fine
    lettura, su errore, o quando la risposta viene scartata senza essere letta
    (client disconnesso prima del primo byte)."""

    def __init__(self, fd: int, inizio: int, quanti: int) -> None:
        self._fd = fd
        self._inizio = inizio
        self._quanti = quanti

    def chiudi(self) -> None:
        if self._fd >= 0:
            fd, self._fd = self._fd, -1
            os.close(fd)

    def __iter__(self) -> Iterator[bytes]:
        try:
            posizione, restano = self._inizio, self._quanti
            while restano > 0 and self._fd >= 0:
                dati = os.pread(self._fd, min(PEZZO, restano), posizione)
                if not dati:
                    break
                posizione += len(dati)
                restano -= len(dati)
                yield dati
        finally:
            self.chiudi()

    def __del__(self) -> None:
        self.chiudi()


def servi(dir_fd: int, nome: str, *, range_header: str | None, scarica: bool) -> Response:
    """Serve `nome` dalla cartella `dir_fd` (che chi chiama chiude)."""
    if not nome or "/" in nome or "\\" in nome or nome.startswith(".") or "\x00" in nome:
        raise ErroreController("NonTrovato")
    try:
        fd = os.open(nome, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        raise ErroreController("NonTrovato") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ErroreController("NonTrovato")
    except BaseException:
        os.close(fd)
        raise
    dimensione = info.st_size
    tipo = TIPI.get(os.path.splitext(nome)[1].lower(), "application/octet-stream")
    intestazioni = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": disposizione(nome, scarica),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-cache",
    }
    scelta = intervallo(range_header, dimensione)
    if scelta == "416":
        os.close(fd)
        return Response(status_code=416, headers={**intestazioni, "Content-Range": f"bytes */{dimensione}"})
    if scelta is None:
        intestazioni["Content-Length"] = str(dimensione)
        return StreamingResponse(_Lettore(fd, 0, dimensione), media_type=tipo, headers=intestazioni)
    inizio, fine = scelta
    intestazioni["Content-Range"] = f"bytes {inizio}-{fine}/{dimensione}"
    intestazioni["Content-Length"] = str(fine - inizio + 1)
    return StreamingResponse(_Lettore(fd, inizio, fine - inizio + 1), status_code=206, media_type=tipo, headers=intestazioni)
