"""Estrazione del testo dal documento di partenza (PDF, txt, md, docx)."""

from __future__ import annotations

import hashlib
import io
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".text"}


class DocumentError(RuntimeError):
    """Documento mancante, illeggibile o in un formato non supportato."""


class DocumentNotExtractable(DocumentError):
    """Il file c'e' ed e' del formato giusto, ma non se ne ricava testo.

    `details["motivo"]` e' uno fra:
      - "scansione": PDF fatto di sole immagini (serve OCR o un PDF con testo);
      - "protetto":  PDF protetto da password;
      - "vuoto":     DOCX/testo senza contenuto;
      - "illeggibile": file corrotto o non decodificabile.
    La UI mostra lo stesso blocco ("Questo PDF non contiene testo") per tutti,
    con il testo di `error` che dice cosa fare.
    """

    def __init__(self, message: str, *, motivo: str, file: Path) -> None:
        super().__init__(message)
        self.details = {"motivo": motivo, "file": file.name}


class UnsupportedDocument(DocumentError):
    """Estensione non gestita."""

    def __init__(self, message: str, *, extension: str, supported: list[str]) -> None:
        super().__init__(message)
        self.details = {"estensione": extension, "ammesse": supported}


class DocumentTooLarge(DocumentError):
    """Il documento supera uno dei limiti di `documents:` in config.yaml.

    Il controllo avviene PRIMA di leggere il contenuto (dimensione del file,
    numero di pagine, dimensione scompattata di un DOCX) oppure durante
    l'estrazione (caratteri): un file enorme non deve occupare memoria e
    tempo, ne' tenere preso il lock di una bozza, solo per sentirsi dire che
    e' troppo grande. `details`: {"limite", "massimo", "valore", "file"}.
    """

    def __init__(self, message: str, *, limit: str, maximum: int, value: int, file: Path) -> None:
        super().__init__(message)
        self.details = {"limite": limit, "massimo": maximum, "valore": value, "file": file.name}


@dataclass(frozen=True)
class DocumentLimits:
    """Limiti sui documenti di partenza (sezione `documents:` di config.yaml).

    - `max_bytes`: dimensione del file;
    - `max_pages`: pagine di un PDF;
    - `max_chars`: caratteri del testo estratto;
    - `max_unpacked_bytes`: somma delle dimensioni scompattate delle parti di
      un DOCX (che e' uno zip: un file piccolo puo' contenere un XML enorme);
    - `max_xml_bytes`: la stessa somma, sulle sole parti XML di un DOCX (sono
      quelle che python-docx analizza: un oggetto per ogni paragrafo);
    - `max_zip_entries`: numero di parti dello zip di un DOCX;
    - `max_content_bytes`: byte dei content stream decodificati di un PDF,
      sommati pagina per pagina (uno stream condiviso conta a ogni pagina):
      e' il lavoro che l'estrazione deve fare, indipendente dal testo che ne
      esce;
    - `max_seconds`: tempo massimo dell'estrazione, controllato fra una pagina
      e l'altra (una pagina sola non si interrompe da dentro: per quello
      serve il timeout del sottoprocesso, a carico dell'adattatore).

    Revisioni di sicurezza C0+C1 (MEDIA-1) e del diff delle correzioni
    (MEDIA-1, MEDIA-2).
    """

    max_bytes: int = 20 * 1024 * 1024
    max_pages: int = 500
    max_chars: int = 2_000_000
    max_unpacked_bytes: int = 100 * 1024 * 1024
    max_xml_bytes: int = 15 * 1024 * 1024
    max_zip_entries: int = 2000
    max_content_bytes: int = 50 * 1024 * 1024
    max_seconds: int = 60

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> "DocumentLimits":
        settings = settings or {}
        defaults = cls()
        values = {}
        for key in (
            "max_bytes",
            "max_pages",
            "max_chars",
            "max_unpacked_bytes",
            "max_xml_bytes",
            "max_zip_entries",
            "max_content_bytes",
            "max_seconds",
        ):
            raw = settings.get(key)
            value = getattr(defaults, key) if raw is None else int(raw)
            if value <= 0:
                raise ValueError(f"documents.{key} deve essere un numero positivo.")
            values[key] = value
        return cls(**values)


def _too_large(path: Path, limit: str, maximum: int, value: int, what: str) -> DocumentTooLarge:
    return DocumentTooLarge(
        f"Il documento {path.name} e' troppo grande: {what} ({value}) oltre il massimo "
        f"consentito ({maximum}). Usa un estratto piu' breve.",
        limit=limit,
        maximum=maximum,
        value=value,
        file=path,
    )


@dataclass
class Document:
    path: Path
    text: str
    kind: str
    pages: int | None = None
    #: sha256 dei byte da cui e' stato estratto il testo ("sha256:<hex>").
    #: Calcolato sugli stessi byte, non rileggendo il file dopo: un file
    #: sostituito nel frattempo non da' sha256 e testo che non corrispondono.
    sha256: str | None = None
    #: Dimensione degli stessi byte (C6, BASSA-3: non un secondo stat()).
    size_bytes: int | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def truncated(self, max_chars: int) -> tuple[str, bool]:
        if max_chars and self.char_count > max_chars:
            return self.text[:max_chars], True
        return self.text, False


SUPPORTED_EXTENSIONS = [".pdf", ".docx", *sorted(TEXT_EXTENSIONS)]


def locate_document(
    path: str | Path,
    search_dir: str | Path | None = None,
    *,
    confined: bool = False,
) -> Path:
    """Risolve il documento.

    Se `path` e' **solo un nome di file** (nessuna cartella davanti) viene
    cercato prima dentro `search_dir` — la cartella `documenti/` del progetto —
    e solo dopo come percorso relativo alla directory corrente.

    Con `confined=True` (i tool, per default: e' quello che usa l'interfaccia
    web) il documento DEVE stare dentro `search_dir`, una volta risolti i link
    simbolici; un nome nudo si cerca solo li', mai nella cartella corrente;
    file nascosti e file senza un'estensione gestita sono rifiutati. Senza,
    un nome come `.env` veniva cercato nella cartella corrente e letto come
    testo. run.py e il subagent, che lavorano con percorsi scelti in locale,
    usano la modalita' libera (--trusted-paths nei tool).
    """
    raw = Path(str(path).strip().strip('"').strip("'")).expanduser()
    bare_name = raw.parent == Path(".")

    if confined:
        return _locate_confined(raw, bare_name, search_dir)

    if bare_name and search_dir is not None:
        candidate = Path(search_dir).expanduser() / raw.name
        if candidate.is_file():
            return candidate.resolve()

    resolved = raw.resolve()
    if resolved.is_file():
        return resolved

    if bare_name and search_dir is not None:
        raise DocumentError(
            f"Documento non trovato: ne' '{raw.name}' dentro "
            f"{Path(search_dir).name}/, ne' come percorso."
        )
    raise DocumentError(f"Documento non trovato: '{raw.name}'.")


def _locate_confined(raw: Path, bare_name: bool, search_dir: str | Path | None) -> Path:
    if search_dir is None:
        raise DocumentError("Cartella dei documenti non configurata (paths.documents_dir).")
    root = Path(search_dir).expanduser().resolve()
    candidate = (root / raw.name) if bare_name else raw
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise DocumentError(
            f"Il documento deve stare nella libreria Documenti ({root.name}/)."
        ) from None
    if resolved.name.startswith(".") or resolved.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UnsupportedDocument(
            f"'{raw.name}' non e' un documento utilizzabile. Formati gestiti: PDF, DOCX, "
            f"{', '.join(sorted(TEXT_EXTENSIONS))}.",
            extension=resolved.suffix.lower(),
            supported=SUPPORTED_EXTENSIONS,
        )
    if not resolved.is_file():
        raise DocumentError(f"Documento non trovato nella libreria Documenti: '{raw.name}'.")
    return resolved


def extract_text(
    path: str | Path,
    search_dir: str | Path | None = None,
    *,
    confined: bool = False,
    limits: DocumentLimits | None = None,
) -> Document:
    """Estrae il testo, rispettando `limits` (default: DocumentLimits())."""
    limits = limits or DocumentLimits()
    doc_path = locate_document(path, search_dir, confined=confined)
    size = doc_path.stat().st_size
    if size > limits.max_bytes:
        raise _too_large(doc_path, "byte", limits.max_bytes, size, "dimensione in byte")
    suffix = doc_path.suffix.lower()
    if suffix not in (".pdf", ".docx") and suffix not in TEXT_EXTENSIONS and suffix != "":
        raise UnsupportedDocument(
            f"Estensione '{suffix}' non supportata. Formati gestiti: PDF, DOCX, "
            f"{', '.join(sorted(TEXT_EXTENSIONS))}.",
            extension=suffix,
            supported=SUPPORTED_EXTENSIONS,
        )
    # I byte si leggono UNA volta (con il limite: il file puo' essere
    # cresciuto dopo il controllo), e da li' si estrae e si calcola lo sha256.
    with doc_path.open("rb") as handle:
        data = handle.read(limits.max_bytes + 1)
    if len(data) > limits.max_bytes:
        raise _too_large(doc_path, "byte", limits.max_bytes, len(data), "dimensione in byte")
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if suffix == ".pdf":
        document = _read_pdf(doc_path, limits, data)
    elif suffix == ".docx":
        document = _read_docx(doc_path, limits, data)
    else:
        document = _read_plain(doc_path, data)
    document.sha256 = digest
    document.size_bytes = len(data)
    if document.char_count > limits.max_chars:
        raise _too_large(doc_path, "caratteri", limits.max_chars, document.char_count, "caratteri del testo")
    return document


def _read_pdf(path: Path, limits: DocumentLimits, data: bytes | None = None) -> Document:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise DocumentError(
            "Per leggere i PDF serve il pacchetto 'pypdf': pip install pypdf"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data) if data is not None else str(path))
    except Exception as exc:
        raise DocumentNotExtractable(
            f"Impossibile aprire il PDF {path.name}: {exc}", motivo="illeggibile", file=path
        ) from exc

    if getattr(reader, "is_encrypted", False):
        # Un PDF cifrato con password utente vuota si apre; con una password
        # vera pypdf NON solleva qui: decrypt() restituisce "non decifrato"
        # (0) e l'errore arriverebbe solo leggendo le pagine, con un tipo
        # interno di pypdf. Si controlla il risultato.
        try:
            decrypted = reader.decrypt("")
        except Exception:  # noqa: BLE001
            decrypted = 0
        if not decrypted:
            raise DocumentNotExtractable(
                f"Il PDF {path.name} e' protetto da password: rimuovi la protezione "
                "o fornisci una versione sbloccata.",
                motivo="protetto",
                file=path,
            )

    try:
        page_count = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 - albero delle pagine rotto
        raise DocumentNotExtractable(
            f"Impossibile leggere le pagine del PDF {path.name}: {exc}", motivo="illeggibile", file=path
        ) from exc
    if page_count > limits.max_pages:
        raise _too_large(path, "pagine", limits.max_pages, page_count, "numero di pagine")

    chunks: list[str] = []
    chars = 0
    content_bytes = 0
    started = time.monotonic()
    for number, page in enumerate(reader.pages, start=1):
        elapsed = time.monotonic() - started
        if elapsed > limits.max_seconds:
            raise _too_large(path, "secondi", limits.max_seconds, int(elapsed), "tempo di estrazione in secondi")
        # Il lavoro dell'estrazione dipende dai content stream, non dal testo
        # che ne esce: uno stream di 75 MB di soli spazi, condiviso da tutte
        # le pagine, non produce caratteri ma costa minuti per pagina.
        content_bytes += _page_content_bytes(page)
        if content_bytes > limits.max_content_bytes:
            raise _too_large(
                path, "contenuto_pagine", limits.max_content_bytes, content_bytes, "contenuto delle pagine in byte"
            )
        try:
            content = page.extract_text() or ""
        except Exception:
            content = ""
        if content.strip():
            chunks.append(f"[pagina {number}]\n{content.strip()}")
            chars += len(chunks[-1])
            # Fermarsi appena si supera il limite: e' inutile estrarre il resto.
            if chars > limits.max_chars:
                raise _too_large(path, "caratteri", limits.max_chars, chars, "caratteri del testo")

    text = "\n\n".join(chunks).strip()
    if not text:
        raise DocumentNotExtractable(
            f"Dal PDF {path.name} non e' stato estratto alcun testo: probabilmente "
            "e' un documento scansionato (immagini). Serve una versione con testo "
            "selezionabile oppure un passaggio OCR.",
            motivo="scansione",
            file=path,
        )
    return Document(path=path, text=text, kind="pdf", pages=page_count)


def _page_content_bytes(page: Any) -> int:
    """Byte del content stream decodificato di una pagina (0 se illeggibile)."""
    try:
        contents = page.get_contents()
        return len(contents.get_data()) if contents is not None else 0
    except Exception:  # noqa: BLE001 - pagina rotta: extract_text la salta comunque
        return 0


def _check_docx_unpacked(path: Path, limits: DocumentLimits, data: bytes | None = None) -> None:
    """Somma le dimensioni scompattate dichiarate nello zip, PRIMA di aprirlo.

    python-docx carica in memoria tutte le parti del pacchetto: un DOCX di
    pochi KB con un document.xml molto comprimibile (bomba zip) occuperebbe
    GB. La dimensione dichiarata nell'indice dello zip e' quella che
    zipfile fa poi rispettare in lettura (un'entrata che si scompatta oltre
    la dimensione dichiarata da' errore).
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data) if data is not None else path) as archive:
            entries = archive.infolist()
            unpacked = sum(info.file_size for info in entries)
            xml = sum(
                info.file_size for info in entries if info.filename.lower().endswith((".xml", ".rels"))
            )
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocumentNotExtractable(
            f"Impossibile aprire il DOCX {path.name}: non e' un file DOCX valido.",
            motivo="illeggibile",
            file=path,
        ) from exc
    if len(entries) > limits.max_zip_entries:
        raise _too_large(path, "parti_zip", limits.max_zip_entries, len(entries), "numero di parti del DOCX")
    if unpacked > limits.max_unpacked_bytes:
        raise _too_large(
            path, "byte_scompattati", limits.max_unpacked_bytes, unpacked, "dimensione scompattata"
        )
    if xml > limits.max_xml_bytes:
        raise _too_large(path, "byte_xml", limits.max_xml_bytes, xml, "dimensione delle parti XML")


def _read_docx(path: Path, limits: DocumentLimits, data: bytes | None = None) -> Document:
    try:
        import docx  # type: ignore
    except ImportError as exc:
        raise DocumentError(
            "Per leggere i .docx serve il pacchetto 'python-docx', che e' fra le "
            "dipendenze obbligatorie: .venv/bin/pip install -r requirements.txt "
            "(tools/doctor.py dice cosa manca)."
        ) from exc

    _check_docx_unpacked(path, limits, data)
    try:
        document = docx.Document(io.BytesIO(data) if data is not None else str(path))
    except Exception as exc:
        raise DocumentNotExtractable(
            f"Impossibile aprire il DOCX {path.name}: {exc}", motivo="illeggibile", file=path
        ) from exc
    text ="\n".join(p.text for p in document.paragraphs if p.text.strip()).strip()
    if not text:
        raise DocumentNotExtractable(
            f"Il documento {path.name} non contiene testo.", motivo="vuoto", file=path
        )
    return Document(path=path, text=text, kind="docx")


def _read_plain(path: Path, data: bytes | None = None) -> Document:
    raw = data if data is not None else path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n").strip()
        except UnicodeDecodeError:
            continue
        if not text:
            raise DocumentNotExtractable(f"Il file {path.name} e' vuoto.", motivo="vuoto", file=path)
        return Document(path=path, text=text, kind="text")
    raise DocumentNotExtractable(
        f"Impossibile decodificare {path.name} come file di testo.", motivo="illeggibile", file=path
    )


__all__ = [
    "Document",
    "DocumentError",
    "DocumentLimits",
    "DocumentNotExtractable",
    "DocumentTooLarge",
    "SUPPORTED_EXTENSIONS",
    "UnsupportedDocument",
    "extract_text",
    "locate_document",
]
