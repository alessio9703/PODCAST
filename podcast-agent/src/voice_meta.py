"""Metadati di una voce, accanto alla registrazione: consenso (e, in C4, nome).

    voci_disponibili/Mario Rossi.wav
    voci_disponibili/Mario Rossi.json   <- questo file

Stessa forma dei `.json` accanto ai brani delle musiche (decisione D2): il file
si sposta e si cancella con l'audio, quindi revocare il consenso e cancellare
la registrazione avvengono nello stesso posto, e non c'e' un registro unico
conteso da tutti gli utenti.

Il consenso vale per QUELLA registrazione: insieme a chi lo ha registrato e
quando, si salva lo sha256 del file audio. Se la registrazione viene
sostituita (un altro file con lo stesso nome, magari di un'altra persona) il
consenso non vale piu' e va registrato di nuovo.

Stati del consenso (`consent_state`):
  - "registrato": c'e', e vale per il file audio attuale;
  - "mancante":   non e' mai stato registrato (o il file dei metadati manca);
  - "superato":   registrato su una registrazione diversa da quella attuale;
  - "revocato":   registrato e poi revocato esplicitamente.

Ogni scrittura e' atomica e sotto lock, con rilettura DENTRO il lock: piu'
utenti possono registrare consenso e nome della stessa voce nello stesso
momento (src/fileio.py).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import DomainError, NeedsUserDecision
from .fileio import file_lock, write_json_atomic
from .registry import file_hash

SCHEMA_VERSION = 1

CONSENT_GRANTED = "registrato"
CONSENT_MISSING = "mancante"
CONSENT_OUTDATED = "superato"
CONSENT_REVOKED = "revocato"


class VoiceMetaError(DomainError):
    """File dei metadati di una voce illeggibile o richiesta non valida."""


class RecordingChanged(NeedsUserDecision):
    """La registrazione non e' quella che l'utente ha visto: consenso non registrato.

    Revisione di sicurezza C2, MEDIA-3: fra la stima (dove l'utente vede la
    voce) e il clic sulla casella del consenso il file puo' essere stato
    sostituito. Il consenso si registra solo sullo sha256 atteso.
    """


def meta_path(audio_path: Path | str) -> Path:
    """Il .json dei metadati accanto alla registrazione."""
    return Path(audio_path).with_suffix(".json")


def load_meta(audio_path: Path | str) -> dict[str, Any]:
    """Metadati della voce. File assente = metadati vuoti, non un errore."""
    path = meta_path(audio_path)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VoiceMetaError(
            f"{path.name} non e' un JSON leggibile: {exc}. Correggilo o cancellalo "
            "(il consenso andra' registrato di nuovo).",
            details={"file": path.name},
        ) from exc
    if not isinstance(raw, dict):
        raise VoiceMetaError(f"{path.name} deve contenere un oggetto JSON.", details={"file": path.name})
    _validate(raw, path)
    return raw


def _validate(meta: dict[str, Any], path: Path) -> None:
    """Tipi attesi: un file scritto a mano male e' un errore stabile, non un crash.

    Revisione di sicurezza C2, BASSA-4.
    """
    for key in ("consenso", "consenso_revocato"):
        value = meta.get(key)
        if value is not None and not isinstance(value, dict):
            raise VoiceMetaError(
                f"{path.name}: '{key}' deve essere un oggetto o null.", details={"file": path.name, "campo": key}
            )
    consent = meta.get("consenso")
    if consent is not None:
        digest = consent.get("audio_sha256")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise VoiceMetaError(
                f"{path.name}: il consenso non ha uno sha256 valido della registrazione.",
                details={"file": path.name, "campo": "consenso.audio_sha256"},
            )


def consent_state(
    audio_path: Path | str, meta: dict[str, Any] | None = None, *, audio_sha256: str | None = None
) -> str:
    """Lo stato del consenso per la registrazione ATTUALE (vedi il modulo).

    `audio_sha256`: lo sha256 dei byte che si stanno per usare, gia' calcolato
    da chi chiama (src/voice_prep.py lo calcola sui byte che manda al
    provider). Senza, si calcola sul file ora.
    """
    meta = load_meta(audio_path) if meta is None else meta
    consent = meta.get("consenso")
    if not consent:
        return CONSENT_REVOKED if meta.get("consenso_revocato") else CONSENT_MISSING
    if consent.get("audio_sha256") != (audio_sha256 or file_hash(audio_path)):
        return CONSENT_OUTDATED
    return CONSENT_GRANTED


def consent_revoked(audio_path: Path | str) -> bool:
    """True se il consenso e' stato revocato esplicitamente. Non calcola hash.

    Serve a produce() per accorgersi di una revoca fra una battuta e l'altra.
    Un file dei metadati illeggibile conta come revocato: nel dubbio ci si
    ferma.
    """
    try:
        meta = load_meta(audio_path)
    except VoiceMetaError:
        return True
    return not meta.get("consenso") and bool(meta.get("consenso_revocato"))


def consent_view(audio_path: Path | str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Cio' che la UI mostra del consenso: stato, chi e quando."""
    meta = load_meta(audio_path) if meta is None else meta
    consent = meta.get("consenso") or {}
    revoked = meta.get("consenso_revocato") or {}
    current = file_hash(audio_path)
    return {
        "stato": consent_state(audio_path, meta, audio_sha256=current),
        # Lo sha256 della registrazione ATTUALE: e' quello che l'interfaccia
        # ripassa a voice_meta.py --consent --expect-sha256.
        "audio_sha256": current,
        "da": consent.get("da"),
        "il": consent.get("il"),
        "revocato_da": revoked.get("da") if not consent else None,
        "revocato_il": revoked.get("il") if not consent else None,
    }


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _require_by(by: str | None) -> str:
    if not by or not by.strip():
        raise VoiceMetaError(
            "Serve il nome di chi registra (--by): il consenso dice chi lo ha confermato.",
            error_type="ConsentByRequired",
            details={"campo": "by"},
        )
    return " ".join(by.split())


def record_consent(
    audio_path: Path | str, *, voice: str, by: str | None, expect_sha256: str | None = None
) -> dict[str, Any]:
    """Registra il consenso della persona per la registrazione attuale.

    `expect_sha256`: lo sha256 della registrazione che l'utente ha visto
    (consent_view()["audio_sha256"]). Se il file ora e' diverso, niente
    consenso: exit 3 RecordingChanged.
    """
    who = _require_by(by)
    audio_path = Path(audio_path)
    target = meta_path(audio_path)
    with file_lock(target):
        current = file_hash(audio_path)
        if expect_sha256 is not None and expect_sha256 != current:
            raise RecordingChanged(
                f"La registrazione di {voice} e' cambiata dopo che l'hai vista: il consenso non "
                "e' stato registrato. Ricarica la pagina, riascolta e conferma di nuovo.",
                details={"voce": voice, "atteso": expect_sha256, "attuale": current},
            )
        meta = load_meta(audio_path)
        meta["schema_version"] = SCHEMA_VERSION
        meta["voce"] = voice
        meta["consenso"] = {
            "da": who,
            "il": _now(),
            "audio_sha256": current,
            "file": audio_path.name,
        }
        meta.pop("consenso_revocato", None)
        write_json_atomic(target, meta)
    return meta


def revoke_consent(audio_path: Path | str, *, voice: str, by: str | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Revoca il consenso. Ritorna (metadati, consenso revocato o None).

    Una voce con il consenso revocato non si clona e non si riusa piu'
    (src/voice_prep.py), salvo il flag esplicito --no-consent-gate.
    """
    who = _require_by(by)
    audio_path = Path(audio_path)
    target = meta_path(audio_path)
    with file_lock(target):
        meta = load_meta(audio_path)
        previous = meta.get("consenso")
        meta["schema_version"] = SCHEMA_VERSION
        meta["voce"] = voice
        meta["consenso"] = None
        meta["consenso_revocato"] = {"da": who, "il": _now(), "consenso_precedente": previous}
        write_json_atomic(target, meta)
    return meta, previous


__all__ = [
    "CONSENT_GRANTED",
    "CONSENT_MISSING",
    "CONSENT_OUTDATED",
    "CONSENT_REVOKED",
    "RecordingChanged",
    "VoiceMetaError",
    "consent_revoked",
    "consent_state",
    "consent_view",
    "load_meta",
    "meta_path",
    "record_consent",
    "revoke_consent",
]
