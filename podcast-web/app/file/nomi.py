"""Nomi di file in arrivo dal browser e cartelle delle librerie.

- `nome_caricato`: il nome di `X-LIL-Nome-File` (percent-encoded come `X-LIL-Nome`),
  con tutte le regole del contratto ("Caricamenti").
- `chiave_nome`: il confronto dei comandi dell'agente (senza estensione, minuscole,
  senza accenti, `_` e `-` come spazio): due file con la stessa chiave sono lo stesso
  elemento, e un caricamento che collide e' una sostituzione.
- `apri_cartella`: descrittore di una cartella dell'agente, senza seguire link.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from app.servizi.errori import ErroreController

NOME_FILE_MAX = 150
# Limite in byte UTF-8: il temporaneo `.<nome>.<12 hex>.tmp` deve restare sotto i 255 byte
# di un nome di file (ext4, APFS).
NOME_FILE_MAX_BYTE = 200
ESTENSIONI_VIETATE = frozenset({".json", ".lock", ".tmp"})
ESTENSIONI_DOCUMENTI = (".pdf", ".docx", ".txt", ".md", ".markdown", ".rst", ".text")
ESTENSIONI_TESTO = frozenset({".txt", ".md", ".markdown", ".rst", ".text"})
_CONTROLLO = re.compile(r"[\x00-\x1f\x7f]")


def _rifiuta(messaggio: str) -> ErroreController:
    return ErroreController("FileNonAmmesso", messaggio, {"campo": "nome"})


def ha_invisibili(testo: str) -> bool:
    """Caratteri di formato Unicode (Cf): invisibili, bidirezionali, a larghezza zero."""
    return any(unicodedata.category(c) == "Cf" for c in testo)


def nome_caricato(valore_header: str | None, estensioni: tuple[str, ...] | frozenset[str]) -> str:
    from app.ingresso.cms import decodifica_header

    if not valore_header:
        raise _rifiuta("Manca il nome del file.")
    try:
        nome = unicodedata.normalize("NFC", decodifica_header(valore_header))
    except ValueError:
        raise _rifiuta("Il nome del file non e' UTF-8 valido.") from None
    if not nome or nome != nome.strip() or len(nome) > NOME_FILE_MAX or len(nome.encode("utf-8")) > NOME_FILE_MAX_BYTE:
        raise _rifiuta(f"Nome del file vuoto, con spazi ai bordi o troppo lungo (massimo {NOME_FILE_MAX} caratteri).")
    if "/" in nome or "\\" in nome or ".." in nome or _CONTROLLO.search(nome) or ha_invisibili(nome):
        raise _rifiuta("Il nome del file contiene caratteri non ammessi.")
    if nome[0] in ".-":
        raise _rifiuta("Il nome del file non puo' cominciare con un punto o un trattino.")
    radice, estensione = os.path.splitext(nome)
    estensione = estensione.lower()
    if not radice.strip() or estensione in ESTENSIONI_VIETATE or estensione not in estensioni:
        raise _rifiuta(f"Estensione non ammessa. Ammesse: {', '.join(sorted(estensioni))}.")
    return nome


def chiave_nome(nome: str) -> str:
    radice = os.path.splitext(nome)[0]
    senza_accenti = "".join(c for c in unicodedata.normalize("NFKD", radice) if not unicodedata.combining(c))
    return " ".join(senza_accenti.lower().replace("_", " ").replace("-", " ").split())


def cartella_libreria(agent_dir: Path, nome_relativo: str) -> Path:
    """La cartella di una libreria dentro l'agente (nome da settings_show)."""
    if not nome_relativo or "/" in nome_relativo or nome_relativo.startswith(".") or ".." in nome_relativo:
        raise ErroreController("ErroreInterno")
    return agent_dir / nome_relativo


def apri_cartella(percorso: Path) -> int:
    """Descrittore della cartella, senza seguire link simbolici. Chi chiama lo chiude."""
    try:
        return os.open(percorso, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise ErroreController("NonTrovato") from None
