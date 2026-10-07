#!/usr/bin/env python3
"""Scrive il copione con l'API e apre (o aggiorna) la sessione di revisione.

    # con la configurazione dell'episodio (tools/episode_config.py): niente altro
    python tools/script_write.py --run-dir output/<run>

    # a mano, come prima
    python tools/script_write.py --text-file T --speakers "Mario Rossi" "Giulia Bianchi" \
        --run-dir output/2026-09-13_10-30_titolo [--format intervista --interviewer "Mario Rossi"]

Scrive `conversazione.json` e `_script_session.json` nella cartella della run.
Lo stato della conversazione va su disco perche' la revisione avviene in un
processo diverso (vedi src/script_session.py).

Da dove vengono le scelte, in ordine di precedenza:
  1. i flag espliciti (--speakers, --format, --interviewer, --presenter,
     --participant, --target-minutes, --document/--text-file);
  2. episodio.json della run, se il cast e' completo;
  3. config.yaml (durata, formato di default).

Il cast si controlla PRIMA di chiamare il modello. Il copione prodotto si
valida con le stesse regole di script_save.py: se ha errori bloccanti viene
salvato lo stesso come versione (la chiamata e' pagata), con `errors` non
vuoto e `approvable: false`, e script_approve.py rifiuta di approvarlo.

Se la run ha gia' un copione, questo ne crea una NUOVA versione
("riscrittura"): la storia non si cancella.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import add_config_arg, add_trusted_paths_arg, bootstrap, documents_confined, emit, file_arg, note, run_dir_arg, run_tool  # noqa: E402
from src.episode_config import EXTRACTED_TEXT_FILENAME, EpisodeConfig  # noqa: E402
from src.pdf_reader import Document, extract_text  # noqa: E402
from src.script_authoring import Cast, write_draft, writer_from_config  # noqa: E402
from src.script_rules import FORMATS, parse_participant  # noqa: E402
from src.script_view import script_payload  # noqa: E402


def build_writer(config):  # type: ignore[no-untyped-def]
    """Compatibilita': il writer configurato (vedi src/script_authoring.py)."""
    return writer_from_config(config)


def report(result, config, extra=None) -> None:  # type: ignore[no-untyped-def]
    """JSON di risposta comune a script_write.py e script_revise.py."""
    payload = script_payload(
        result.script,
        result.session,
        script_file=result.script_path,
        warnings=result.warnings,
        words_per_minute=int(config.script.get("words_per_minute", 150)),
    )
    # `problems`: chiave storica di questo tool, ora = errori bloccanti.
    payload["problems"] = result.errors
    payload.update(extra or {})
    emit(payload)


def _document(args, run_dir: Path, episode: EpisodeConfig | None, config) -> Document:  # type: ignore[no-untyped-def]
    if args.text_file:
        text_path = file_arg(args.text_file, config, args, "text_file")
        return Document(path=text_path, text=text_path.read_text(encoding="utf-8"), kind="text")
    if args.document:
        # Stessa ricerca di doc_extract.py: un nome nudo si cerca in documenti/.
        return extract_text(args.document, search_dir=config.documents_dir, confined=documents_confined(args))
    extracted = run_dir / EXTRACTED_TEXT_FILENAME
    if episode and episode.documento and extracted.is_file():
        return Document(
            path=Path(episode.documento.get("percorso") or extracted),
            text=extracted.read_text(encoding="utf-8"),
            kind=str(episode.documento.get("tipo") or "text"),
            pages=episode.documento.get("pagine"),
        )
    raise ValueError(
        "Manca il documento: passa --document o --text-file, oppure registralo "
        "nella run con tools/episode_config.py --documento."
    )


def _cast(args, episode: EpisodeConfig | None) -> tuple[Cast, int | None]:  # type: ignore[no-untyped-def]
    base: dict = {}
    if episode is not None and episode.cast_complete and not args.speakers:
        base = {
            "speakers": episode.speakers,
            "script_format": episode.formato,
            "interviewer": episode.interviewer,
            "presenter": episode.presenter,
            "participants": episode.participants,
            "target_minutes": episode.durata_minuti,
        }
    speakers = list(args.speakers or base.get("speakers") or [])
    if not speakers:
        raise ValueError(
            "Mancano gli speaker: passa --speakers, oppure completa il cast con "
            "tools/episode_config.py."
        )
    participants = list(base.get("participants") or [])
    for spec in args.participant:
        person = parse_participant(spec)
        participants = [p for p in participants if p.get("speaker") != person["speaker"]] + [person]
    cast = Cast(
        speakers=speakers,
        script_format=args.script_format or base.get("script_format"),
        interviewer=args.interviewer or base.get("interviewer"),
        presenter=args.presenter or base.get("presenter"),
        participants=participants,
    )
    return cast, args.target_minutes or base.get("target_minutes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text-file", help="File di testo prodotto da doc_extract.py")
    source.add_argument("--document", help="Documento originale (estratto al volo; cercato anche in documenti/)")
    parser.add_argument("--speakers", nargs="+", default=None, help="Nomi degli speaker")
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--target-minutes", type=int, default=None)
    parser.add_argument("--format", dest="script_format", choices=FORMATS, default=None)
    parser.add_argument("--interviewer", default=None)
    parser.add_argument("--presenter", default=None)
    parser.add_argument("--participant", action="append", default=[], metavar='"SPEAKER|NOME COGNOME|RUOLO"')
    parser.add_argument("--by", default=None, help="Chi ha chiesto la stesura (per l'elenco versioni)")
    add_trusted_paths_arg(parser)
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    run_dir = run_dir_arg(args.run_dir, config)

    episode = EpisodeConfig.load(run_dir)
    cast, target_minutes = _cast(args, episode)
    document = _document(args, run_dir, episode, config)

    result = write_draft(
        run_dir,
        config,
        document=document,
        cast=cast,
        target_minutes=target_minutes,
        by=args.by,
        note=note,
    )
    note(f"Copione salvato in {result.script_path} (versione {result.session.version_count})")
    for warning in result.warnings:
        note(f"avviso: {warning}")
    if result.errors:
        note("ATTENZIONE: il copione ha errori bloccanti e non si potra' approvare finche' non vengono corretti.")

    report(result, config)


if __name__ == "__main__":
    run_tool(main)
