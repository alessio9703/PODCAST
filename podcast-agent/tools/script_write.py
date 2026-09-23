#!/usr/bin/env python3
"""Scrive la prima stesura del copione e apre la sessione di revisione.

    python tools/script_write.py --text-file T --speakers "Mario Rossi" "Giulia Bianchi" \
        --run-dir output/2026-09-13_10-30_titolo

Scrive `conversazione.json` e `_script_session.json` nella cartella della run.
Lo stato della conversazione va su disco perche' la revisione avviene in un
processo diverso (vedi src/script_session.py).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import PROJECT_ROOT, add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.lessons import active_lessons_summary  # noqa: E402
from src.pdf_reader import Document, extract_text  # noqa: E402
from src.script_session import FILENAME as SESSION_FILENAME  # noqa: E402
from src.script_session import ScriptSession  # noqa: E402
from src.script_writer import ScriptWriter  # noqa: E402


def build_writer(config) -> ScriptWriter:  # type: ignore[no-untyped-def]
    settings = config.script
    return ScriptWriter(
        api_key=config.anthropic_api_key(),
        model=str(settings.get("model", "claude-opus-5")),
        max_tokens=int(settings.get("max_tokens", 16000)),
        effort=str(settings.get("effort", "high")),
        language=str(settings.get("language", "italiano")),
        target_minutes=int(settings.get("target_minutes", 8)),
        words_per_minute=int(settings.get("words_per_minute", 150)),
        max_document_chars=int(settings.get("max_document_chars", 120000)),
    )


def report(script, session_path: Path, script_path: Path, speakers: list[str], extra=None):  # type: ignore[no-untyped-def]
    payload = {
        "titolo": script.titolo,
        "turni": script.turni,
        "speakers": script.speakers,
        "turn_count": len(script.turni),
        "chars": script.total_chars,
        "words": script.total_words,
        "problems": script.validate(speakers),
        "script_file": str(script_path),
        "session_file": str(session_path),
        "rendered": script.render(),
    }
    payload.update(extra or {})
    emit(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text-file", help="File di testo prodotto da doc_extract.py")
    source.add_argument("--document", help="Documento originale (estratto al volo)")
    parser.add_argument("--speakers", nargs="+", required=True, help="Nomi degli speaker")
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--target-minutes", type=int, default=None)
    add_config_arg(parser)
    args = parser.parse_args()

    config = bootstrap(args)
    run_dir = resolve_path(args.run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(
            f"Cartella della run inesistente: {run_dir}. Chiama prima run_init.py."
        )

    if args.text_file:
        text_path = resolve_path(args.text_file)
        document = Document(
            path=text_path, text=text_path.read_text(encoding="utf-8"), kind="text"
        )
    else:
        document = extract_text(args.document)

    writer = build_writer(config)
    if args.target_minutes:
        writer.target_minutes = args.target_minutes

    prompt, truncated = writer.build_prompt(document, args.speakers)
    if truncated:
        note("Il documento e' stato troncato per lunghezza (script.max_document_chars).")

    note(f"Scrivo il copione con {writer.model} (effort={writer.effort})...")
    script = writer.run(prompt)

    session = ScriptSession.create(
        document_path=str(document.path),
        document_text=document.text,
        speakers=list(args.speakers),
        prompt=prompt,
        path=run_dir / SESSION_FILENAME,
        active_lessons=active_lessons_summary(PROJECT_ROOT / "prompts"),
    )
    session.record(script)
    session.save()

    script_path = script.save(run_dir / "conversazione.json")
    note(f"Copione salvato in {script_path}")

    report(script, session.path, script_path, args.speakers, {"revision_count": 0})


if __name__ == "__main__":
    run_tool(main)
