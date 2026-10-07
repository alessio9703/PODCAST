#!/usr/bin/env python3
"""podcast-agent — trasforma un documento in un episodio podcast con voci clonate.

Uso:
    python run.py                       # chiede il documento in modo interattivo
    python run.py documento.pdf         # cercato prima in documenti/
    python run.py ~/altrove/report.pdf
    python run.py documento.pdf --config config.yaml
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import NoReturn

from src.audio_assembler import (
    AssemblyError,
    AudioAssembler,
    MissingSpeakerVoice,
    slugify,
)
from src.config import Config, ConfigError, PROJECT_ROOT, load_config, load_dotenv
from src.cost_estimate import estimate
from src.music_context import context_from_tracks, spoken_tracks_for_choice
from src.music_library import CATEGORIES, MusicLibrary, MusicTrack, has_any_music, write_music_choice
from src.music_mixer import MusicMixerError, mix_episode, preflight
from src.pdf_reader import Document, DocumentError, extract_text
from src.registry import VoiceRegistry
from src.run_layout import (
    archive_source_document,
    make_run_dir,
    write_voices_used,
)
from src.script_writer import PodcastScript, ScriptWriter, ScriptWriterError
from src.errors import DomainError, NeedsUserDecision
from src.fileio import LockTimeout
from src.voice_library import AvailableVoice, VoiceLibrary, VoiceLibraryError
from src.voice_meta import CONSENT_GRANTED, CONSENT_OUTDATED, CONSENT_REVOKED, VoiceMetaError
from src.voice_prep import ACTION_CLONE, grant_consent, plan_voices, prepare_voices
from src.voice_provider import VoiceProvider, VoiceProviderError

SEPARATOR = "-" * 72


# ----------------------------------------------------------------------
# Utilita' di interfaccia
# ----------------------------------------------------------------------


def heading(step: str, title: str) -> None:
    print(f"\n{SEPARATOR}\n{step}  {title}\n{SEPARATOR}")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        raise SystemExit("\nInput chiuso: operazione annullata.")
    return answer or (default or "")


def ask_yes_no(prompt: str, default: bool | None = None) -> bool:
    hint = "s/n" if default is None else ("S/n" if default else "s/N")
    while True:
        answer = ask(f"{prompt} ({hint})").lower()
        if not answer and default is not None:
            return default
        if answer in {"s", "si", "sì", "y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("  Rispondi 's' oppure 'n'.")


def fail(message: str) -> NoReturn:
    print(f"\nERRORE: {message}", file=sys.stderr)
    raise SystemExit(1)


# ----------------------------------------------------------------------
# 1. Documento
# ----------------------------------------------------------------------


def step_document(path_arg: str | None, documents_dir: Path) -> Document:
    heading("[1/9]", "Documento di partenza")
    raw = path_arg
    if not raw:
        available = sorted(
            p.name
            for p in documents_dir.glob("*")
            if p.is_file() and not p.name.startswith(".")
        )
        if available:
            print(f"  In {documents_dir.name}/: " + ", ".join(available))
            print("  Basta il nome del file; altrimenti indica un percorso completo.")
        else:
            print(f"  La cartella {documents_dir.name}/ e' vuota: indica un percorso.")
        raw = ask(f"Documento (nome in {documents_dir.name}/ o percorso)")
    if not raw:
        fail("Nessun documento indicato.")
    try:
        document = extract_text(raw, search_dir=documents_dir)
    except DocumentError as exc:
        fail(str(exc))

    pages = f", {document.pages} pagine" if document.pages else ""
    print(
        f"  Letto: {document.path.name} ({document.kind}{pages}) — "
        f"{document.word_count:,} parole, {document.char_count:,} caratteri".replace(
            ",", "."
        )
    )
    return document


# ----------------------------------------------------------------------
# 2. Numero di speaker
# ----------------------------------------------------------------------


def step_speaker_count(max_available: int) -> int:
    heading("[2/9]", "Quante voci vuoi nel podcast?")
    print("  1 = monologo | 2 = dialogo | 3+ = tavola rotonda")
    print(f"  Voci disponibili nella cartella: {max_available}")
    while True:
        answer = ask("Numero di speaker", "2")
        if not answer.isdigit() or int(answer) < 1:
            print("  Inserisci un numero intero maggiore o uguale a 1.")
            continue
        count = int(answer)
        if count > max_available:
            print(
                f"  Ne hai chieste {count} ma nella cartella ci sono solo "
                f"{max_available} registrazioni utilizzabili."
            )
            continue
        return count


# ----------------------------------------------------------------------
# 3. Selezione delle voci
# ----------------------------------------------------------------------


def step_select_voices(library: VoiceLibrary, count: int) -> list[AvailableVoice]:
    heading("[3/9]", "Scelta delle voci")
    for index, voice in enumerate(library.voices, start=1):
        size_mb = voice.size_bytes / (1024 * 1024)
        print(f"  {index:2d}. {voice.name}  ({voice.extension}, {size_mb:.1f} MB)")
    if library.rejected_files:
        listed = ", ".join(p.name for p in library.rejected_files[:5])
        print(
            f"\n  Ignorati {len(library.rejected_files)} file in formato non "
            f"riconosciuto: {listed}"
        )

    selected: list[AvailableVoice] = []
    while len(selected) < count:
        position = len(selected) + 1
        query = ask(f"\n  Voce {position} di {count} (nome o numero)")
        if not query:
            continue

        match, candidates = library.resolve(query)

        if match is None and candidates:
            print("  Non ho trovato una corrispondenza esatta. Intendevi:")
            for index, candidate in enumerate(candidates, start=1):
                print(f"    {index}. {candidate.name}")
            choice = ask("  Numero della voce giusta (invio per riprovare)")
            if choice.isdigit() and 1 <= int(choice) <= len(candidates):
                match = candidates[int(choice) - 1]
            else:
                continue
        elif match is None:
            print(
                f"  Nessun file corrisponde a '{query}'. "
                "Usa uno dei nomi elencati sopra oppure il suo numero."
            )
            continue

        if any(v.path == match.path for v in selected):
            print(f"  '{match.name}' e' gia' stata scelta: seleziona un'altra voce.")
            continue

        selected.append(match)
        print(f"  OK: {match.name}")

    print("\n  Speaker dell'episodio: " + ", ".join(v.name for v in selected))
    return selected


# ----------------------------------------------------------------------
# 3-bis. Musiche (opzionali) — prima del copione, perche' il contesto
# musicale (intro/outro con parlato) deve poter influenzare cosa scrive
# ScriptWriter.
# ----------------------------------------------------------------------


def step_choose_music(library: MusicLibrary) -> dict[str, str | None]:
    """Chiede quale musica usare per ciascuna categoria NON vuota.

    Se musiche/ e' assente o del tutto vuota non chiede niente: la funzione
    musica e' semplicemente non disponibile, non un passo da saltare a mano.
    """
    if not library.available():
        return {category: None for category in CATEGORIES}

    heading("[3-bis/9]", "Musiche (opzionali)")
    chosen: dict[str, str | None] = {}
    for category in CATEGORIES:
        tracks = library.tracks(category)
        if not tracks:
            chosen[category] = None
            continue

        print(f"\n  {category}:")
        for index, track in enumerate(tracks, start=1):
            duration = track.duration_seconds()
            duration_label = f"{duration:.0f}s" if duration is not None else "durata sconosciuta"
            flags = []
            if not track.metadata.licenza:
                flags.append("SENZA licenza dichiarata")
            if track.metadata_error:
                flags.append(f"NON UTILIZZABILE: {track.metadata_error}")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            print(f"    {index}. {track.name} ({duration_label}){suffix}")

        while True:
            answer = ask(f"  Quale {category}? (numero, nome, invio per nessuno)")
            if not answer:
                chosen[category] = None
                break
            match, candidates = library.resolve(category, answer)
            if match is None and candidates:
                print("  Non e' un nome certo. Intendevi:")
                for index, candidate in enumerate(candidates, start=1):
                    print(f"    {index}. {candidate.name}")
                continue
            if match is None:
                print(f"  Nessuna corrispondenza per '{answer}' in {category}/.")
                continue
            if match.metadata_error:
                print(f"  '{match.name}' non e' utilizzabile: {match.metadata_error}")
                continue
            chosen[category] = match.name
            if not match.metadata.licenza:
                print(
                    f"  ATTENZIONE: '{match.name}' non ha una licenza dichiarata nei "
                    "metadati (vedi la nota etica sulle musiche in README.md)."
                )
            print(f"  OK: {match.name}")
            break

    return chosen


def _music_context(library: MusicLibrary, chosen: dict[str, str | None]) -> dict[str, dict] | None:
    """Contesto per ScriptWriter: solo intro/outro con `parlato: true`.

    La logica e' quella condivisa con i tool (src/music_context.py); qui resta
    solo l'avviso stampato come prima.
    """
    tracks = spoken_tracks_for_choice(chosen, library)
    for _category, track in tracks:
        if not track.metadata.trascrizione:
            print(
                f"  ATTENZIONE: '{track.name}' ha parlato ma nessuna trascrizione "
                "nei metadati: il copione potrebbe ripetere quello che dice."
            )
    context, _warnings = context_from_tracks(tracks)
    return context


# ----------------------------------------------------------------------
# 4. Script + checkpoint di revisione
# ----------------------------------------------------------------------


def step_write_script(
    config: Config,
    document: Document,
    speakers: list[str],
    music_context: dict[str, dict] | None = None,
) -> PodcastScript:
    heading("[4/9]", "Scrittura della conversazione")
    settings = config.script
    writer = ScriptWriter(
        api_key=config.anthropic_api_key(),
        model=str(settings.get("model", "claude-opus-5")),
        max_tokens=int(settings.get("max_tokens", 16000)),
        effort=str(settings.get("effort", "high")),
        language=str(settings.get("language", "italiano")),
        target_minutes=int(settings.get("target_minutes", 8)),
        words_per_minute=int(settings.get("words_per_minute", 150)),
        max_document_chars=int(settings.get("max_document_chars", 120000)),
    )

    print(f"  Modello: {writer.model} — analizzo il documento e scrivo il copione...")
    try:
        script = writer.write(document, speakers, music_context)
    except ScriptWriterError as exc:
        fail(str(exc))

    while True:
        problems = script.validate(speakers)
        heading("[5/9]", "Revisione dello script — CHECKPOINT")
        print(script.render())
        print(
            f"\n  {len(script.turni)} turni, {script.total_words} parole, "
            f"{script.total_chars} caratteri."
        )
        if problems:
            print("\n  Problemi rilevati:")
            for problem in problems:
                print(f"    - {problem}")

        print(
            "\n  Nulla verra' sintetizzato finche' non dai un OK esplicito "
            "(la sintesi costa tempo e credito API)."
        )
        if not problems and ask_yes_no("  Approvi questo script?", default=None):
            return script
        if problems:
            print("  Con questi problemi non posso procedere: serve una revisione.")

        instructions = ask(
            "  Cosa vuoi cambiare? (invio vuoto per annullare tutto)"
        )
        if not instructions:
            fail("Script non approvato: operazione annullata, nessuna sintesi avviata.")

        print("  Riscrivo lo script...")
        try:
            script = writer.revise(instructions)
        except ScriptWriterError as exc:
            fail(str(exc))


# ----------------------------------------------------------------------
# 6. Voci clonate (registro / cache)
# ----------------------------------------------------------------------


def step_prepare_voices(
    config: Config,
    provider: VoiceProvider,
    selected: list[AvailableVoice],
) -> dict[str, str]:
    """Riuso o clonazione, con le stesse protezioni dei tool (src/voice_prep.py).

    Revisione finale, B1: lock per voce, marcatore di clonazione dall'esito
    incerto (nessun retry, nemmeno dopo un errore lasciato dall'interfaccia
    web), sha256 dei byte effettivamente clonati. Sul consenso:
      - una voce con il consenso REVOCATO non si usa mai (ne' clonata ne' riusata);
      - a una voce da clonare senza consenso registrato lo si chiede qui, e lo si
        registra come farebbe l'interfaccia (voice_meta, legato allo sha256 della
        registrazione mostrata); senza un si' esplicito non si clona.
    """
    heading("[6/9]", "Preparazione delle voci clonate")
    speakers = [voice.name for voice in selected]
    try:
        plans = plan_voices(config, speakers, reuse_requires_consent=False)
    except (VoiceLibraryError, VoiceMetaError) as exc:
        fail(str(exc))

    revoked = [plan.speaker for plan in plans if plan.consent["stato"] == CONSENT_REVOKED]
    if revoked:
        fail(
            "Consenso revocato per: " + ", ".join(revoked) + ".\n"
            "  Queste voci non si clonano e non si riusano. Scegli altre voci, oppure "
            "registra di nuovo il consenso (tools/voice_meta.py --consent) se la persona "
            "lo ha dato di nuovo."
        )

    for plan in plans:
        if plan.action != ACTION_CLONE or plan.consent["stato"] == CONSENT_GRANTED:
            continue
        why = (
            "la registrazione e' cambiata dopo il consenso"
            if plan.consent["stato"] == CONSENT_OUTDATED
            else "il consenso non e' registrato"
        )
        print(f"\n  {plan.speaker} ({plan.voice.path.name}) va clonata, ma {why}.")
        print("  Si clona solo la voce di una persona che ha dato il consenso esplicito.")
        if not ask_yes_no(f"  {plan.speaker} ha dato il consenso alla clonazione della sua voce?"):
            fail(f"Consenso di {plan.speaker} non confermato: nessuna voce clonata, nessuna sintesi avviata.")
        by = ""
        while not by:
            by = " ".join(ask("  Chi conferma il consenso (nome e cognome)").split())
        try:
            grant_consent(config, plan.voice, by=by, expect_sha256=plan.consent["audio_sha256"])
        except (DomainError, NeedsUserDecision) as exc:
            fail(str(exc))
        print(f"  Consenso di {plan.speaker} registrato (confermato da {by}).")

    try:
        prepared = prepare_voices(config, speakers, provider=provider, note=lambda message: print(f"  {message}"))
    except VoiceProviderError as exc:
        fail(f"Clonazione fallita.\n  {exc}\n  Nessuna sintesi e' stata avviata. Non ritento da solo.")
    except (DomainError, NeedsUserDecision, VoiceLibraryError, LockTimeout) as exc:
        fail(f"{exc}\n  Nessuna sintesi e' stata avviata.")
    for name, result in prepared.results.items():
        print(f"  {name}: {result['action']} ({result['voice_id']})")
    return prepared.voice_map


# ----------------------------------------------------------------------
# 7-9. Stima, sintesi, montaggio, output
# ----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trasforma un documento in un episodio podcast con voci clonate."
    )
    parser.add_argument("document", nargs="?", help="Percorso del documento di partenza")
    parser.add_argument("--config", default=None, help="Percorso di config.yaml")
    parser.add_argument(
        "--purge-segments",
        action="store_true",
        help="Cancella i segmenti audio dopo il montaggio. Di norma non si usa: "
        "sono la cache che permette di rifare una battuta sola",
    )
    parser.add_argument(
        "--keep-segments",
        action="store_true",
        help=argparse.SUPPRESS,  # i segmenti ora si tengono sempre: alias senza effetto
    )
    args = parser.parse_args()

    load_dotenv()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        fail(str(exc))

    print(f"podcast-agent — provider attivo: {config.active_provider}")

    # 1. documento
    document = step_document(args.document, config.documents_dir)

    # 2-3. voci disponibili
    library = VoiceLibrary(config.voices_dir, config.accepted_extensions)
    try:
        library.scan()
        if not library.voices:
            library.require(1)
    except VoiceLibraryError as exc:
        fail(str(exc))

    speaker_count = step_speaker_count(len(library.voices))
    try:
        library.require(speaker_count)
    except VoiceLibraryError as exc:
        fail(str(exc))
    selected = step_select_voices(library, speaker_count)
    speaker_names = [v.name for v in selected]

    # 3-bis. musiche (opzionali) — PRIMA del copione: se intro/outro hanno
    # parlato, ScriptWriter deve saperlo per non farlo ripetere nel copione.
    music_library = MusicLibrary(config.music_dir, config.accepted_extensions)
    music_library.scan()
    music_choice = step_choose_music(music_library)
    music_context = _music_context(music_library, music_choice)

    # 4-5. script + checkpoint
    script = step_write_script(config, document, speaker_names, music_context)

    # cartella della run
    run_dir = make_run_dir(config.output_dir, script.titolo)
    script.save(run_dir / "conversazione.json")
    print(f"\n  Cartella episodio: {run_dir}")
    print(f"  Script salvato   : {run_dir / 'conversazione.json'}")
    if has_any_music(music_choice):
        musiche_file = write_music_choice(run_dir, music_choice)
        print(f"  Musiche scelte   : {musiche_file}")

    # 6. voci clonate
    try:
        provider = config.build_provider()
    except (ConfigError, VoiceProviderError) as exc:
        fail(str(exc))
    voice_map = step_prepare_voices(config, provider, selected)

    assembler = AudioAssembler.from_config(provider, config.audio)

    # blocco prima della sintesi se manca una voce
    try:
        assembler.check_voice_coverage(script, voice_map)
    except MissingSpeakerVoice as exc:
        fail(str(exc))

    # 7. stima
    heading("[7/9]", "Stima costo e durata")
    prediction = estimate(
        script,
        provider.pricing(),
        chars_per_second=getattr(provider, "chars_per_second", None),
        words_per_minute=int(config.script.get("words_per_minute", 150)),
        pause_between_turns_ms=assembler.pause_between_turns_ms,
        lead_in_ms=assembler.lead_in_ms,
        lead_out_ms=assembler.lead_out_ms,
        trim_silence=bool(config.audio.get("trim_silence", False)),
        trim_margin_head_ms=int(config.audio.get("trim_margin_head_ms", 0)),
        trim_margin_tail_ms=int(config.audio.get("trim_margin_tail_ms", 0)),
    )
    print(prediction.render())

    # Musica: valida tutto (ffmpeg, nomi, sha256 registrati) PRIMA di
    # spendere in sintesi. Se manca ffmpeg pur avendo scelto una musica, ci
    # si ferma qui: nessuna chiamata al provider vocale.
    try:
        music_choice_resolved, music_warnings = preflight(run_dir, config)
    except MusicMixerError as exc:
        fail(str(exc))

    # 8. sintesi
    heading("[8/9]", "Sintesi vocale")

    def progress(index: int, total: int, speaker: str, cached: bool) -> None:
        suffix = " (in cache)" if cached else "..."
        print(f"  [{index:>3}/{total}] {speaker}{suffix}", flush=True)

    try:
        synthesis = assembler.synthesize(
            script, voice_map, run_dir / "segmenti", progress=progress
        )
    except AssemblyError as exc:
        fail(str(exc))
    segments = synthesis.paths

    # 9. montaggio e output
    heading("[9/9]", "Montaggio finale")
    if music_choice_resolved is not None and music_choice_resolved.any():
        print("  Monto la voce e sovrappongo le musiche scelte...")
        try:
            result = mix_episode(
                assembler=assembler, segments=segments, script=script,
                run_dir=run_dir, config=config, choice=music_choice_resolved,
            )
        except (AssemblyError, MusicMixerError) as exc:
            fail(str(exc))
        result.warnings = [*synthesis.warnings, *music_warnings, *result.warnings]
        print(f"  Musiche usate    : {result.musiche_usate_path}")
    else:
        try:
            result = assembler.assemble(segments, run_dir / slugify(script.titolo))
        except AssemblyError as exc:
            fail(str(exc))
        result.warnings = [*synthesis.warnings, *result.warnings]

    # Il registro si legge ORA: le voci clonate al passo 6 ci sono gia'.
    registry = VoiceRegistry(config.registry_file, project_root=PROJECT_ROOT)
    voices_file = write_voices_used(
        run_dir,
        provider.name,
        voice_map,
        {v.name: v.path for v in selected},
        registry,
    )

    source_note = archive_source_document(document.path, run_dir)

    # I segmenti restano: sono la cache che permette di correggere una battuta
    # senza risintetizzare l'episodio. Si cancellano solo su richiesta esplicita.
    if args.purge_segments:
        shutil.rmtree(run_dir / "segmenti", ignore_errors=True)

    print(f"\n  Episodio    : {result.path}")
    if result.duration_seconds:
        minutes, seconds = divmod(int(result.duration_seconds), 60)
        print(f"  Durata reale: {minutes}m {seconds:02d}s")
    print(f"  Script      : {run_dir / 'conversazione.json'}")
    print(f"  Voci usate  : {voices_file}")
    print(f"  Documento   : {source_note}")

    for warning in result.warnings:
        print(f"\n  ATTENZIONE: {warning}")

    print(f"\nFatto. Tutto tracciato in {run_dir}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrotto dall'utente.", file=sys.stderr)
        raise SystemExit(130)
