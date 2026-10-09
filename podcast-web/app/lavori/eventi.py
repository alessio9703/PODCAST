"""Lettura di `output/<run>/_eventi.jsonl` (scritto da `produce.py --events`).

Punti K13-K14 di A2-piano.md: il file si apre senza seguire link, tramite il
descrittore della cartella della run; si leggono solo righe complete; righe troppo
lunghe o non JSON si saltano (contando comunque il numero di riga, che fa da id
dell'evento); ogni evento passa dal `Pulitore`. Il file viene svuotato a ogni
nuova produzione: una dimensione minore di quella gia' letta vuol dire "si riparte".
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.servizi.esiti import Pulitore

log = logging.getLogger("podcast_web.eventi")

NOME_FILE = "_eventi.jsonl"
RIGA_MAX = 64 * 1024
RIGHE_MAX = 20_000
LETTURA_MAX = 4 * 1024 * 1024
EVENTI_FINALI = frozenset({"finita", "interrotta", "errore"})


@dataclass
class Lettore:
    """Stato di lettura di uno stream: posizione nel file e numero di riga."""

    cartella_run: Path
    pulitore: Pulitore
    posizione: int = 0
    riga: int = 0
    azzerato: bool = False
    ultimo: dict[str, Any] | None = field(default=None)

    def leggi_nuovi(self) -> list[tuple[int, dict[str, Any]]]:
        """Eventi nuovi come (id, evento). `azzerato` diventa vero se il file e'
        stato svuotato da una nuova produzione (si riparte da 1)."""
        self.azzerato = False
        try:
            dir_fd = os.open(self.cartella_run, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return []
        try:
            try:
                fd = os.open(NOME_FILE, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
            except OSError:
                return []
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    return []
                if info.st_size < self.posizione:
                    self.posizione, self.riga, self.azzerato, self.ultimo = 0, 0, True, None
                if info.st_size == self.posizione:
                    return []
                dati = os.pread(fd, min(LETTURA_MAX, info.st_size - self.posizione), self.posizione)
            finally:
                os.close(fd)
        finally:
            os.close(dir_fd)
        fine = dati.rfind(b"\n")
        if fine < 0:
            if len(dati) >= LETTURA_MAX:  # una riga enorme senza a capo: la si salta
                self.posizione += len(dati)
            return []
        completo = dati[: fine + 1]
        self.posizione += len(completo)
        eventi: list[tuple[int, dict[str, Any]]] = []
        for grezza in completo.split(b"\n")[:-1]:
            self.riga += 1
            if self.riga > RIGHE_MAX or len(grezza) > RIGA_MAX:
                continue
            try:
                evento = json.loads(grezza.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if not isinstance(evento, dict) or not isinstance(evento.get("evento"), str):
                continue
            pulito = self.pulitore.pulisci(evento)
            self.ultimo = pulito
            eventi.append((self.riga, pulito))
        return eventi

    def salta_fino_a(self, dopo: int) -> list[tuple[int, dict[str, Any]]]:
        """Legge tutto e restituisce solo gli eventi con id > dopo."""
        tutti: list[tuple[int, dict[str, Any]]] = []
        while True:
            nuovi = self.leggi_nuovi()
            if not nuovi:
                break
            tutti.extend(nuovi)
        return [(n, e) for n, e in tutti if n > dopo]

    @property
    def finito(self) -> bool:
        return bool(self.ultimo) and self.ultimo.get("evento") in EVENTI_FINALI
