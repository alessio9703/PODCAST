"""Caricamenti nelle librerie dell'agente (A2: documenti; A5: voci e brani).

Il file arriva come corpo grezzo (`application/octet-stream`), il nome in
`X-LIL-Nome-File`. Regole del contratto ("Caricamenti"), punti K1-K7 di A2-piano.md:
nome controllato, estensione e firma dei primi byte, limite contato mentre arriva
(prima di scrivere oltre il limite), temporaneo nascosto `O_EXCL|O_NOFOLLOW` aperto
tramite il descrittore della cartella, `fsync` e `rename` atomico, nessuna
sovrascrittura senza un gettone `FileEsistente`, temporaneo tolto in ogni caso di
errore o di disconnessione.
"""

from __future__ import annotations

import codecs
import hashlib
import logging
import os
import secrets
import stat
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from app.file.nomi import ESTENSIONI_TESTO, ESTENSIONI_VIETATE, chiave_nome
from app.servizi.errori import ErroreController

log = logging.getLogger("podcast_web.file")

FIRMA_PRIMI_BYTE = 16


def firma_documento(estensione: str, inizio: bytes) -> bool:
    if estensione == ".pdf":
        return inizio.startswith(b"%PDF-")
    if estensione == ".docx":
        return inizio.startswith(b"PK\x03\x04")
    return estensione in ESTENSIONI_TESTO  # il testo si controlla per intero (UTF-8, niente NUL)


# Firma dei primi byte per le registrazioni e i brani (contratto, "Caricamenti").
def _firma_mp3(b: bytes) -> bool:
    return b.startswith(b"ID3") or (len(b) >= 2 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0)


FIRME_AUDIO: dict[str, Callable[[bytes], bool]] = {
    ".wav": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WAVE",
    ".mp3": _firma_mp3,
    ".m4a": lambda b: b[4:8] == b"ftyp",
    ".flac": lambda b: b.startswith(b"fLaC"),
    ".ogg": lambda b: b.startswith(b"OggS"),
    ".opus": lambda b: b.startswith(b"OggS"),
    ".webm": lambda b: b.startswith(b"\x1a\x45\xdf\xa3"),
}


def firma_audio(estensione: str, inizio: bytes) -> bool:
    controllo = FIRME_AUDIO.get(estensione)
    return controllo is not None and controllo(inizio)


def estensioni_audio(impostazioni: dict) -> tuple[str, ...]:
    """Le estensioni di `audio.accepted_extensions` dell'agente per cui il controller
    sa controllare la firma; mai `.json`, `.lock`, `.tmp`."""
    accettate = (impostazioni.get("audio") or {}).get("accepted_extensions") or []
    return tuple(sorted({e.lower() for e in accettate if isinstance(e, str)} & set(FIRME_AUDIO) - ESTENSIONI_VIETATE))


@dataclass
class Caricato:
    nome: str
    dimensione: int
    sha256: str
    sostituiti: list[str]


class _ControlloTesto:
    """UTF-8 valido e niente NUL, su tutto il file mentre arriva."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")

    def aggiorna(self, pezzo: bytes, fine: bool = False) -> None:
        if b"\x00" in pezzo:
            raise ErroreController("FileNonAmmesso", "Il file di testo contiene byte non ammessi.")
        try:
            self._decoder.decode(pezzo, final=fine)
        except UnicodeDecodeError:
            raise ErroreController("FileNonAmmesso", "Il file di testo non e' in UTF-8.") from None


def esistenti_con_chiave(dir_fd: int, chiave: str, estensioni: frozenset[str] | tuple[str, ...]) -> list[str]:
    """File (non nascosti) della cartella con la stessa chiave del nome e con
    un'estensione della libreria. MAI `.json`, `.lock`, `.tmp`: accanto a una voce
    il `.json` e' il suo consenso, accanto a un brano la sua licenza (A2 BASSA-1)."""
    trovati = []
    for voce in os.scandir(dir_fd):
        if voce.name.startswith("."):
            continue
        estensione = os.path.splitext(voce.name)[1].lower()
        if estensione in ESTENSIONI_VIETATE or estensione not in estensioni:
            continue
        if chiave_nome(voce.name) == chiave:
            trovati.append(voce.name)
    return sorted(trovati)


async def salva(
    *,
    dir_fd: int,
    nome: str,
    flusso: AsyncIterator[bytes],
    massimo: int,
    lunghezza_dichiarata: str | None,
    firma_ok: Callable[[str, bytes], bool],
    estensioni: frozenset[str] | tuple[str, ...],
    sostituzione_ammessa: bool,
    prima_del_rename: Callable[[list[str]], None] | None = None,
) -> Caricato:
    """Scrive il file nella cartella `dir_fd`. Con un nome che collide e senza
    `sostituzione_ammessa` solleva `FileEsistente` (prima di leggere il corpo:
    nessun byte su disco).

    `prima_del_rename(esistenti)` gira dopo tutti i controlli che possono fallire,
    subito prima del rename: e' il posto dei controlli che devono valere in quel
    momento (nessun lavoro in corso) e del consumo del gettone (A2 BASSA-2). Se
    solleva, il temporaneo si toglie e niente cambia."""
    if lunghezza_dichiarata is not None:
        if not lunghezza_dichiarata.isascii() or not lunghezza_dichiarata.isdigit():
            raise ErroreController("RichiestaNonValida", "Content-Length non valido.")
        if int(lunghezza_dichiarata) > massimo:
            raise ErroreController("FileTroppoGrande", f"Il file supera il limite di {massimo // (1024 * 1024)} MB.")
    estensione = os.path.splitext(nome)[1].lower()
    chiave = chiave_nome(nome)
    esistenti = esistenti_con_chiave(dir_fd, chiave, estensioni)
    if esistenti and not sostituzione_ammessa:
        raise ErroreController("FileEsistente", "Esiste gia' un file con questo nome: confermi la sostituzione?",
                               {"campo": "nome", "esistenti": esistenti})

    temporaneo = f".{nome}.{secrets.token_hex(6)}.tmp"
    fd = os.open(temporaneo, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    scritto = False
    try:
        totale = 0
        digest = hashlib.sha256()
        inizio = b""
        testo = _ControlloTesto() if estensione in ESTENSIONI_TESTO else None
        async for pezzo in flusso:
            if not pezzo:
                continue
            totale += len(pezzo)
            if totale > massimo:
                raise ErroreController("FileTroppoGrande", f"Il file supera il limite di {massimo // (1024 * 1024)} MB.")
            if len(inizio) < FIRMA_PRIMI_BYTE:
                inizio += pezzo[: FIRMA_PRIMI_BYTE - len(inizio)]
                if len(inizio) >= FIRMA_PRIMI_BYTE and not firma_ok(estensione, inizio):
                    raise ErroreController("FileNonAmmesso", "Il contenuto del file non corrisponde all'estensione.")
            if testo:
                testo.aggiorna(pezzo)
            digest.update(pezzo)
            vista = memoryview(pezzo)
            while vista:
                n = os.write(fd, vista)
                vista = vista[n:]
        if totale == 0:
            raise ErroreController("FileNonAmmesso", "Il file e' vuoto.")
        if len(inizio) < FIRMA_PRIMI_BYTE and not firma_ok(estensione, inizio):
            raise ErroreController("FileNonAmmesso", "Il contenuto del file non corrisponde all'estensione.")
        if testo:
            testo.aggiorna(b"", fine=True)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        # Ricontrollo dopo la lettura: un altro caricamento puo' essere arrivato nel frattempo.
        esistenti = esistenti_con_chiave(dir_fd, chiave, estensioni)
        if esistenti and not sostituzione_ammessa:
            raise ErroreController("FileEsistente", "Esiste gia' un file con questo nome: confermi la sostituzione?",
                                   {"campo": "nome", "esistenti": esistenti})
        try:
            info = os.stat(nome, dir_fd=dir_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ErroreController("FileNonAmmesso", "Al posto del file c'e' un elemento non sostituibile.")
        except FileNotFoundError:
            pass
        if prima_del_rename is not None:
            prima_del_rename(esistenti)
        os.rename(temporaneo, nome, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        scritto = True
        sostituiti = []
        for vecchio in esistenti:
            if vecchio != nome:
                try:
                    os.unlink(vecchio, dir_fd=dir_fd)
                    sostituiti.append(vecchio)
                except OSError as exc:
                    log.error("sostituzione: %s non tolto (%s)", vecchio, exc.__class__.__name__)
            else:
                sostituiti.append(vecchio)
        os.fsync(dir_fd)
        return Caricato(nome=nome, dimensione=totale, sha256="sha256:" + digest.hexdigest(), sostituiti=sostituiti)
    finally:
        if fd >= 0:
            os.close(fd)
        if not scritto:
            try:
                os.unlink(temporaneo, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
