"""Scrittura e revisione del copione con l'API, come operazioni della run.

Usato da tools/script_write.py e tools/script_revise.py. Rispetto a chiamare
ScriptWriter direttamente aggiunge quello che serve quando il copione nasce
dentro una run condivisa:

  - il cast (formato, intervistatore, presentatore, partecipanti) arriva nel
    prompt, e viene controllato PRIMA della chiamata: un cast incoerente non
    deve costare una chiamata al modello;
  - il risultato si valida con src/script_rules.py, le stesse regole di
    tools/script_save.py. Un copione con errori bloccanti viene comunque
    salvato come versione (la chiamata e' gia' pagata), ma con i suoi errori,
    e non si puo' approvare finche' non viene corretto;
  - durante la chiamata la run tiene il lock di lavoro ("scrittura del
    copione" visibile dall'Archivio, e una seconda scrittura viene rifiutata);
  - la sessione si aggiorna sotto lock, rileggendola: se nel frattempo
    qualcuno ha creato un'altra versione, la nuova si aggiunge in coda (la
    storia non si riscrive mai) e lo si segnala.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .lessons import active_lessons_summary
from .music_context import capture_music_context, prompt_music_context
from .pdf_reader import Document
from .run_lock import session_lock, work_lock
from .script_rules import check_cast, validate_script
from .script_session import FILENAME as SESSION_FILENAME
from .script_session import ScriptSession
from .script_writer import PodcastScript, ScriptWriter

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
SCRIPT_FILENAME = "conversazione.json"


@dataclass
class Cast:
    speakers: list[str]
    script_format: str | None = None
    interviewer: str | None = None
    presenter: str | None = None
    participants: list[dict[str, str]] = field(default_factory=list)

    def check_before_spending(self) -> None:
        """Gli errori di cast che renderebbero inutile la chiamata al modello."""
        if not self.speakers:
            raise ValueError("Nessuno speaker indicato.")
        check_cast(self.speakers, self.script_format or "conversazione", self.interviewer, self.presenter)
        if self.presenter:
            dialogue = [s for s in self.speakers if s != self.presenter]
            known = {p.get("speaker") for p in self.participants}
            missing = [s for s in dialogue if s not in known]
            if missing:
                raise ValueError(
                    "Con la voce di presentazione servono nome pronunciato e ruolo di "
                    "ogni partecipante; mancano per: " + ", ".join(missing) + ". "
                    "Chiedili all'utente, non inventarli."
                )


@dataclass
class AuthoringResult:
    script: PodcastScript
    session: ScriptSession
    script_path: Path
    errors: list[str]
    warnings: list[str]


def writer_from_config(config: Any) -> ScriptWriter:
    settings = config.script
    return ScriptWriter(
        client=config.anthropic_client(),
        model=str(settings.get("model", "claude-opus-5")),
        max_tokens=int(settings.get("max_tokens", 16000)),
        effort=str(settings.get("effort", "high")),
        language=str(settings.get("language", "italiano")),
        target_minutes=int(settings.get("target_minutes", 8)),
        words_per_minute=int(settings.get("words_per_minute", 150)),
        max_document_chars=int(settings.get("max_document_chars", 120000)),
    )


def _validate(script: PodcastScript, session: ScriptSession) -> tuple[list[str], list[str]]:
    errors, warnings = validate_script(
        script.to_dict(),
        session.speakers,
        session.script_format,
        session.interviewer,
        session.presenter,
        session.participants,
    )
    # Il percorso API restituisce gia' la forma giusta per costruzione; i
    # controlli di PodcastScript.validate() restano quelli di sempre e si
    # sommano (senza doppioni) a quelli condivisi.
    for problem in script.validate(session.speakers):
        if problem not in errors:
            errors.append(problem)
    return errors, warnings


def write_draft(
    run_dir: Path,
    config: Any,
    *,
    document: Document,
    cast: Cast,
    target_minutes: int | None = None,
    writer: ScriptWriter | None = None,
    by: str | None = None,
    note=None,
) -> AuthoringResult:
    """Prima stesura (o nuova stesura da zero, se la run ha gia' un copione)."""
    run_dir = Path(run_dir)
    cast.check_before_spending()
    writer = writer or writer_from_config(config)
    if target_minutes:
        writer.target_minutes = int(target_minutes)

    music_for_prompt, warnings = prompt_music_context(run_dir, config)
    prompt, truncated = writer.build_prompt(
        document,
        cast.speakers,
        music_for_prompt,
        script_format=cast.script_format,
        interviewer=cast.interviewer,
        presenter=cast.presenter,
        participants=cast.participants,
    )
    if truncated:
        warnings.append("Il documento e' stato troncato per lunghezza (script.max_document_chars).")

    with work_lock(run_dir, "scrittura_copione"):
        if note:
            note(f"Scrivo il copione con {writer.model} (effort={writer.effort})...")
        script = writer.run(prompt)

    # Nella sessione va solo il testo che il modello ha visto (troncato a
    # script.max_document_chars): la sessione si riscrive a ogni modifica di
    # battuta, approvazione e ripristino, e il documento intero sta gia' in
    # documento_estratto.txt (revisione di sicurezza C0+C1, MEDIA-1).
    session_text, _ = document.truncated(writer.max_document_chars)

    with session_lock(run_dir):
        session_path = run_dir / SESSION_FILENAME
        music_snapshot = capture_music_context(run_dir, config)
        lessons = active_lessons_summary(PROMPTS_DIR)
        if session_path.is_file():
            # Nuova stesura su una run che ha gia' una storia: la storia resta.
            # Cambiano il compito (prompt, cast) e le revisioni "da non
            # annullare", che valevano per il copione precedente.
            session = ScriptSession.load(session_path)
            session.document_path = str(document.path)
            session.document_text = session_text
            session.speakers = list(cast.speakers)
            session.prompt = prompt
            session.music_context = music_snapshot
            session.active_lessons = lessons
            session.revisions = []
            kind = "riscrittura"
        else:
            session = ScriptSession.create(
                document_path=str(document.path),
                document_text=session_text,
                speakers=list(cast.speakers),
                prompt=prompt,
                path=session_path,
                music_context=music_snapshot,
                active_lessons=lessons,
            )
            kind = "prima_stesura"
        session.script_format = cast.script_format or "conversazione"
        session.interviewer = cast.interviewer
        session.presenter = cast.presenter
        session.participants = list(cast.participants)

        errors, more_warnings = _validate(script, session)
        session.record(script, kind=kind, errors=errors, by=by)
        session.save()
        script_path = script.save(run_dir / SCRIPT_FILENAME)

    return AuthoringResult(script, session, script_path, errors, warnings + more_warnings)


def revise_draft(
    run_dir: Path,
    config: Any,
    *,
    instructions: str,
    writer: ScriptWriter | None = None,
    by: str | None = None,
    note=None,
) -> AuthoringResult:
    """Revisione a parole del copione corrente ("Chiedi una modifica")."""
    run_dir = Path(run_dir)
    if not instructions or not instructions.strip():
        raise ValueError("L'istruzione di revisione e' vuota: scrivi cosa cambiare.")
    session_path = run_dir / SESSION_FILENAME
    session = ScriptSession.load(session_path)
    base_version = session.version_count
    messages = session.seed_messages(instructions)
    writer = writer or writer_from_config(config)

    with work_lock(run_dir, "revisione_copione", istruzione=instructions.strip()):
        if note:
            note(f"Revisione #{session.revision_count + 1} ({session.revision_count} gia' applicate)")
        script = writer.run_messages(messages)

    warnings: list[str] = []
    with session_lock(run_dir):
        session = ScriptSession.load(session_path)
        if session.version_count != base_version:
            warnings.append(
                f"La revisione e' partita dalla versione {base_version}, ma nel frattempo "
                f"e' stata creata la versione {session.version_count}: la nuova versione "
                "NON contiene quelle modifiche. Confronta le versioni prima di approvare."
            )
        errors, more_warnings = _validate(script, session)
        session.record(script, instruction=instructions, errors=errors, base_version=base_version, by=by)
        session.save()
        script_path = script.save(run_dir / SCRIPT_FILENAME)

    return AuthoringResult(script, session, script_path, errors, warnings + more_warnings)


__all__ = ["AuthoringResult", "Cast", "revise_draft", "write_draft", "writer_from_config"]
