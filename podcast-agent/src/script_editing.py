"""Modifiche al copione senza il modello: una battuta a mano, o un ripristino.

Due regole, le stesse di ogni altra modifica:

  - ogni modifica crea una NUOVA versione nella sessione. Il ripristino della
    versione 2 non riporta indietro la storia: crea la versione N+1 con il
    contenuto della 2, da approvare di nuovo;
  - l'approvazione non si tocca: decade da sola, perche' il gate confronta lo
    sha256 del copione approvato con quello corrente.

Il copione modificato si valida con src/script_rules.py come gli altri: una
modifica che rompe una regola bloccante (es. il presentatore non nomina piu'
un ospite) non viene salvata.

`expected_version` protegge dal caso di due persone sullo stesso copione: chi
modifica dice quale versione stava guardando, e se nel frattempo ne e' nata
un'altra il comando si ferma (VersionConflict) invece di costruire sopra un
testo che l'utente non ha visto.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import DomainError
from .run_lock import session_lock
from .script_rules import validate_script
from .script_session import FILENAME as SESSION_FILENAME
from .script_session import ScriptSession, script_hash
from .script_writer import PodcastScript

SCRIPT_FILENAME = "conversazione.json"


class VersionConflict(DomainError):
    """La versione corrente non e' quella su cui l'utente stava lavorando."""


class NothingToChange(DomainError):
    """La modifica richiesta lascerebbe il copione identico."""


def check_expected_version(session: ScriptSession, expected: int | None) -> None:
    if expected is not None and expected != session.version_count:
        raise VersionConflict(
            f"Stavi lavorando sulla versione {expected}, ma nel frattempo il copione e' "
            f"passato alla versione {session.version_count}. Ricarica il copione e "
            "riprova sulla versione attuale.",
            details={"expected_version": expected, "current_version": session.version_count},
        )


def _validate_or_raise(payload: dict[str, Any], session: ScriptSession) -> list[str]:
    errors, warnings = validate_script(
        payload,
        session.speakers,
        session.script_format,
        session.interviewer,
        session.presenter,
        session.participants,
    )
    if errors:
        raise DomainError(
            "La modifica non e' stata salvata perche' renderebbe il copione non valido:\n  - "
            + "\n  - ".join(errors),
            error_type="ScriptInvalid",
            details={"errors": errors},
        )
    return warnings


def _load(run_dir: Path) -> ScriptSession:
    session = ScriptSession.load(Path(run_dir) / SESSION_FILENAME)
    if session.current_script is None:
        raise DomainError("La sessione non contiene ancora un copione da modificare.")
    return session


def edit_turn(
    run_dir: Path,
    numero: int,
    testo: str,
    *,
    expected_version: int | None = None,
    by: str | None = None,
) -> tuple[ScriptSession, PodcastScript, Path, list[str]]:
    """Sostituisce il testo (tag comprese) della battuta `numero`. Lo speaker resta."""
    run_dir = Path(run_dir)
    with session_lock(run_dir):
        session = _load(run_dir)
        check_expected_version(session, expected_version)
        payload = {
            "titolo": session.current_script["titolo"],
            "turni": [dict(t) for t in session.current_script["turni"]],
        }
        if not 1 <= numero <= len(payload["turni"]):
            raise DomainError(
                f"La battuta {numero} non esiste: il copione ne ha {len(payload['turni'])}.",
                error_type="TurnNotFound",
                details={"turn": numero, "turn_count": len(payload["turni"])},
            )
        new_text = " ".join(str(testo).split())
        if payload["turni"][numero - 1]["testo"] == new_text:
            raise NothingToChange(
                f"Il testo della battuta {numero} e' gia' questo: nessuna nuova versione creata.",
                details={"turn": numero},
            )
        payload["turni"][numero - 1]["testo"] = new_text
        warnings = _validate_or_raise(payload, session)

        script = PodcastScript.from_dict(payload)
        session.record(script, instruction=f"modifica manuale battuta {numero}", kind="modifica_manuale", by=by)
        session.save()
        script_path = script.save(run_dir / SCRIPT_FILENAME)
    return session, script, script_path, warnings


def restore_version(
    run_dir: Path,
    number: int,
    *,
    expected_version: int | None = None,
    by: str | None = None,
) -> tuple[ScriptSession, PodcastScript, Path, list[str]]:
    """Crea una nuova versione con il contenuto della versione `number`."""
    run_dir = Path(run_dir)
    with session_lock(run_dir):
        session = _load(run_dir)
        check_expected_version(session, expected_version)
        source = session.version(number)
        payload = source["script"]
        if script_hash(payload) == session.current_hash:
            raise NothingToChange(
                f"La versione {number} e' identica a quella corrente: nessuna nuova versione creata.",
                details={"version": number},
            )
        warnings = _validate_or_raise(payload, session)
        script = PodcastScript.from_dict(payload)
        session.record(
            script,
            instruction=f"ripristino della versione {number}",
            kind="ripristino",
            by=by,
            add_to_revisions=False,
        )
        session.versions[-1]["restored_from"] = number
        # Il gate e' sullo sha256: ripristinare proprio il testo approvato lo
        # lascerebbe "approvato". Il ripristino invece chiede sempre una nuova
        # approvazione (wireframe: "Crea la versione N, da approvare di
        # nuovo"): e' un passo in piu', mai uno in meno.
        session.approved = None
        session.save()
        script_path = script.save(run_dir / SCRIPT_FILENAME)
    return session, script, script_path, warnings


def versions_list(session: ScriptSession) -> list[dict[str, Any]]:
    """L'elenco breve per il riquadro "Versioni" (senza i copioni interi)."""
    current = session.version_count
    approved = (session.approved or {}).get("version")
    approved_valid = session.approval_view()["approved"]
    out = []
    for entry in session.versions:
        number = entry.get("version")
        script = entry.get("script") or {}
        out.append(
            {
                "version": number,
                "at": entry.get("at"),
                "instruction": entry.get("instruction"),
                "kind": entry.get("kind") or ("prima_stesura" if number == 1 else "revisione"),
                "by": entry.get("by"),
                "turn_count": len(script.get("turni") or []),
                "errors": entry.get("errors") or [],
                "restored_from": entry.get("restored_from"),
                "current": number == current,
                # "approvata" solo se l'approvazione e' ancora valida: una
                # approvazione decaduta non deve far sembrare approvata la v3.
                "approved": approved_valid and number == approved,
                "approved_but_outdated": (not approved_valid) and number == approved,
            }
        )
    return out


__all__ = [
    "NothingToChange",
    "VersionConflict",
    "check_expected_version",
    "edit_turn",
    "restore_version",
    "versions_list",
]
