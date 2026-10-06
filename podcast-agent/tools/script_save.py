#!/usr/bin/env python3
"""Valida e salva un copione scritto dall'agente.

    python tools/script_save.py --run-dir output/<run> --script-json bozza.json \
        --speakers "Mario Rossi" "Giulia Bianchi" [--instructions "cosa hai cambiato"]

Sostituisce script_write.py quando il copione lo scrive il subagent invece
dell'API (nessuna ANTHROPIC_API_KEY necessaria). Python qui non scrive niente:
**valida e persiste**.

Il percorso API garantiva la forma del JSON "per costruzione" con gli structured
output. Qui quella garanzia non c'e' piu', quindi i controlli che lo schema
faceva implicitamente vengono rifatti esplicitamente: vedi
src/script_rules.validate_script(), condivisa con il percorso API e con la
modifica di una battuta.

Ogni salvataggio crea una nuova versione nella sessione. L'approvazione
dell'utente resta legata allo sha256 del copione approvato, quindi salvare una
revisione dopo un "si'" fa decadere il via libera.

`--speakers` si puo' omettere se la run ha gia' una sessione (si riusano i suoi
speaker) o un episodio.json completo (tools/episode_config.py): in quel caso
formato, intervistatore, presentatore e partecipanti vengono da li', e i flag
espliciti hanno la precedenza.
"""

from __future__ import annotations

import argparse
import json

from _common import PROJECT_ROOT, add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.episode_config import EpisodeConfig  # noqa: E402
from src.lessons import active_lessons_summary  # noqa: E402
from src.music_context import capture_music_context  # noqa: E402
from src.run_lock import session_lock  # noqa: E402
from src.script_rules import FORMATS, check_cast, parse_participant, validate_script  # noqa: E402
from src.script_session import FILENAME as SESSION_FILENAME  # noqa: E402
from src.script_session import ScriptSession  # noqa: E402
from src.script_view import script_payload  # noqa: E402
from src.script_writer import PodcastScript  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--script-json", required=True, help="File JSON col copione")
    parser.add_argument(
        "--speakers",
        nargs="+",
        default=None,
        help="Speaker ammessi (default: quelli della sessione o di episodio.json)",
    )
    parser.add_argument(
        "--instructions",
        default=None,
        help="Se e' una revisione: cosa e' stato chiesto di cambiare",
    )
    parser.add_argument("--document", default=None, help="Documento di partenza (prima stesura)")
    parser.add_argument(
        "--format",
        dest="script_format",
        choices=FORMATS,
        default=None,
        help="Formato del copione (default: quello della sessione, o script.default_format)",
    )
    parser.add_argument(
        "--interviewer",
        default=None,
        help="Con --format intervista: quale speaker fa le domande",
    )
    parser.add_argument(
        "--presenter",
        default=None,
        help="Voce di presentazione: terzo speaker, parla solo all'inizio e "
        "presenta chi prende parte. Va incluso anche in --speakers.",
    )
    parser.add_argument(
        "--participant",
        action="append",
        default=[],
        metavar='"SPEAKER|NOME COGNOME|RUOLO"',
        help="Chi viene presentato (ripetibile, uno per speaker del dialogo): "
        "nome come va pronunciato e chi e' / di cosa si occupa",
    )
    parser.add_argument("--by", default=None, help="Chi ha fatto questa versione (per l'elenco versioni)")
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    run_dir = resolve_path(args.run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(
            f"Cartella della run inesistente: {run_dir}. Chiama prima run_init.py."
        )

    source = resolve_path(args.script_json)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{source.name} non e' JSON valido: {exc}\n"
            "Riscrivi il file con un JSON ben formato e richiama il tool."
        ) from exc

    episode = EpisodeConfig.load(run_dir)

    # Tutto il leggi-modifica-scrivi della sessione avviene sotto lock: due
    # salvataggi concorrenti sulla stessa run non devono perdere una versione.
    with session_lock(run_dir):
        # La sessione va letta PRIMA della validazione: e' lei a ricordare il
        # formato scelto all'avvio, e il formato cambia quali controlli si fanno.
        session_path = run_dir / SESSION_FILENAME
        if session_path.is_file():
            session = ScriptSession.load(session_path)
        else:
            from_episode = episode.script_arguments() if (episode and episode.cast_complete and not args.speakers) else None
            session = ScriptSession.create(
                document_path=args.document or ((episode.documento or {}).get("percorso") if episode else "") or "",
                document_text="",
                speakers=list(args.speakers or (from_episode or {}).get("speakers") or []),
                prompt="(copione scritto dal subagent, non dall'API)",
                path=session_path,
                script_format=(from_episode or {}).get("script_format")
                or str(config.script.get("default_format", "conversazione")),
                interviewer=(from_episode or {}).get("interviewer"),
                presenter=(from_episode or {}).get("presenter"),
                participants=(from_episode or {}).get("participants"),
                music_context=capture_music_context(run_dir, config),
                active_lessons=active_lessons_summary(PROJECT_ROOT / "prompts"),
            )

        speakers = list(args.speakers or session.speakers)
        if not speakers:
            raise ValueError(
                "Mancano gli speaker: passa --speakers, oppure completa il cast con "
                "tools/episode_config.py."
            )

        # Precedenza: flag esplicito > quanto gia' deciso nella sessione. Una
        # revisione non deve far ricomparire il default e cambiare formato di
        # nascosto a meta' episodio.
        if args.script_format:
            session.script_format = args.script_format
        if args.interviewer:
            session.interviewer = args.interviewer

        if args.presenter:
            session.presenter = args.presenter
        for spec in args.participant:
            person = parse_participant(spec)
            session.participants = [
                p for p in session.participants if p.get("speaker") != person["speaker"]
            ] + [person]

        check_cast(speakers, session.script_format, session.interviewer, session.presenter)

        errors, warnings = validate_script(
            payload,
            speakers,
            session.script_format,
            session.interviewer,
            session.presenter,
            session.participants,
        )
        if errors:
            raise ValueError(
                "Il copione non e' valido e NON e' stato salvato:\n  - "
                + "\n  - ".join(errors)
            )

        script = PodcastScript.from_dict(payload)

        was_approved = session.approved is not None
        session.record(script, instruction=args.instructions, by=args.by)
        session.save()

        script_path = script.save(run_dir / "conversazione.json")

    note(f"Copione versione {session.version_count} salvato in {script_path}")

    approval_problem = session.approval_problem(script.to_dict())
    if was_approved and approval_problem:
        note("L'approvazione precedente e' decaduta: il copione e' cambiato.")

    for warning in warnings:
        note(f"avviso: {warning}")

    emit(
        script_payload(
            script,
            session,
            script_file=script_path,
            warnings=warnings,
            words_per_minute=int(config.script.get("words_per_minute", 150)),
        )
    )


if __name__ == "__main__":
    run_tool(main)
