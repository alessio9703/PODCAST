#!/usr/bin/env python3
"""Valida e salva un copione scritto dall'agente.

    python tools/script_save.py --run-dir output/<run> --script-json bozza.json \
        --speakers "Mario Rossi" "Giulia Bianchi" [--instructions "cosa hai cambiato"]

Sostituisce script_write.py quando il copione lo scrive il subagent invece
dell'API (nessuna ANTHROPIC_API_KEY necessaria). Python qui non scrive niente:
**valida e persiste**.

Il percorso API garantiva la forma del JSON "per costruzione" con gli structured
output. Qui quella garanzia non c'e' piu', quindi i controlli che lo schema
faceva implicitamente vengono rifatti esplicitamente: vedi _validate().

Ogni salvataggio crea una nuova versione nella sessione. L'approvazione
dell'utente resta legata allo sha256 del copione approvato, quindi salvare una
revisione dopo un "si'" fa decadere il via libera.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from _common import PROJECT_ROOT, add_config_arg, bootstrap, emit, note, resolve_path, run_tool  # noqa: E402
from src.lessons import active_lessons_summary  # noqa: E402
from src.music_library import MusicLibrary, load_music_choice  # noqa: E402
from src.script_session import FILENAME as SESSION_FILENAME  # noqa: E402
from src.script_session import ScriptSession  # noqa: E402
from src.script_writer import INLINE_TAG as _INLINE_TAG  # noqa: E402
from src.script_writer import PodcastScript  # noqa: E402

FORMATS = ("conversazione", "intervista")

#: Un turno dell'intervistatore deve CHIEDERE. Il punto interrogativo e' il
#: segnale, ma non l'unico: "dimmi perche'" e' una domanda senza "?".
_DOMANDA = re.compile(r"\?|\b(dimmi|spiegami|raccontami|chiarisci)\b", re.IGNORECASE)

#: Spie di un intervistatore che ha iniziato a spiegare invece di chiedere:
#: cifre e percentuali sono contenuto, non domanda. Deliberatamente grossolano,
#: perche' produce un AVVISO e non un blocco (vedi il commento in _validate).
_PORTA_CONTENUTO = re.compile(r"\d+(?:[.,]\d+)?\s*(?:per cento|%)|\bvirgola\b", re.IGNORECASE)

ROOT_KEYS = {"titolo", "turni"}
TURN_KEYS = {"speaker", "testo"}
MAX_TURN_CHARS = 600

_MARKDOWN = re.compile(r"(\*\*|__|^#{1,6}\s|\*\w|\w\*)", re.MULTILINE)

#: Le inline tag di espressivita' ([esitante], [ride], ...) sono ATTESE nel
#: testo: il provider le interpreta, non le legge. Qui non sono piu' un avviso
#: di per se' — lo diventano solo se sono troppe (vedi MAX_TAGS_PER_TURN) o se
#: sono indicazioni di regia travestite (_STAGE_DIRECTION).
#:
#: Il pattern vive in src/script_writer.py (INLINE_TAG): la stessa definizione
#: serve alla stima della durata, e due copie finirebbero per divergere.
MAX_TAGS_PER_TURN = 2

#: Cose che una tag non deve fare: descrivere l'episodio invece della voce.
_STAGE_DIRECTION = re.compile(
    r"\[\s*(musica|sigla|stacco|jingle|intro|outro|effetto|sfx|audio|"
    r"fade|stop|fine|silenzio lungo)\b[^\]]*\]",
    re.IGNORECASE,
)
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f0ff]"
)

#: Accento tonico scritto con l'apostrofo invece che con la lettera accentata
#: ("e'" al posto di "è", "piu'" al posto di "più"). Non e' un errore di stile:
#: l'accento grafico e' l'informazione con cui il sintetizzatore decide dove
#: cade l'accento tonico, quindi toglierlo cambia la pronuncia. Avviso e non
#: blocco, perche' si vede prima di sintetizzare — cioe' prima di pagare.
#:
#: Il discriminante NON e' la parola, e' cosa segue l'apostrofo: l'elisione
#: legittima dell'italiano ha sempre una lettera subito dopo (dell'anno,
#: un'idea, l'altro), l'accento sbagliato ha spazio o punteggiatura. Con quella
#: condizione i falsi positivi dell'elisione non si presentano nemmeno.
#:
#: Restano fuori dalla lista di proposito le forme tronche e gli imperativi in
#: cui l'apostrofo e' corretto: po', be', mo', da', fa', va', sta', di'. "da'"
#: in particolare e' anche ambiguo (il verbo "da'" vuole l'apostrofo, "dà"
#: l'accento), quindi segnalarlo produrrebbe solo rumore.
_ACCENTO_APOSTROFO = re.compile(
    # Forme tronche e imperativi in cui l'apostrofo e' CORRETTO: restano fuori.
    r"\b(?!(?:po|mo|da|fa|va|sta|di|be|to)['\u2019])"
    r"("
    # Classi produttive: i nomi in -ta' (citta', qualita', disponibilita'),
    # i futuri in -ra'/-ro' (sara', potra', andro') e le congiunzioni in -che'
    # (perche', poiche', benche', anziche'). Enumerarle a mano lascerebbe
    # sempre fuori la prossima parola.
    r"\w*(?:ta|ra|ro|che)"
    # Monosillabi e parole isolate che non rientrano in nessuna classe.
    r"|e|ne|se|si|la|li|gia|piu|cosi|puo|cioe|caffe|ventitre"
    r"|lunedi|martedi|mercoledi|giovedi|venerdi"
    r")"
    # Il discriminante: una LETTERA dopo l'apostrofo e' elisione legittima
    # (dell'anno, un'idea), spazio o punteggiatura e' un accento mancato.
    r"['\u2019](?![^\W\d_])",
    re.IGNORECASE | re.UNICODE,
)


def _validate(
    payload: Any,
    speakers: list[str],
    script_format: str = "conversazione",
    interviewer: str | None = None,
) -> tuple[list[str], list[str]]:
    """Ritorna (errori bloccanti, avvisi).

    I primi sei controlli sono quelli che lo schema JSON garantiva per
    costruzione; dal settimo in poi sono quelli di PodcastScript.validate().
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. e' un oggetto
    if not isinstance(payload, dict):
        return [f"Il copione deve essere un oggetto JSON, ricevuto {type(payload).__name__}."], []

    # 6a. nessuna chiave extra alla radice
    extra_root = sorted(set(payload) - ROOT_KEYS)
    if extra_root:
        errors.append(f"Chiavi non ammesse alla radice: {extra_root}. Ammesse: {sorted(ROOT_KEYS)}.")

    # 2. titolo presente ed e' una stringa
    if "titolo" not in payload:
        errors.append("Manca il campo obbligatorio 'titolo'.")
    elif not isinstance(payload["titolo"], str):
        errors.append(f"'titolo' deve essere una stringa, e' {type(payload['titolo']).__name__}.")
    elif not payload["titolo"].strip():
        # 7 + 12: non vuoto e non fatto di soli spazi
        errors.append("'titolo' e' vuoto o fatto di soli spazi.")

    # 3. turni presente ed e' una lista
    if "turni" not in payload:
        errors.append("Manca il campo obbligatorio 'turni'.")
        return errors, warnings
    if not isinstance(payload["turni"], list):
        errors.append(f"'turni' deve essere una lista, e' {type(payload['turni']).__name__}.")
        return errors, warnings

    # 8. almeno un turno
    if not payload["turni"]:
        errors.append("'turni' e' vuoto: il copione non contiene nessuna battuta.")
        return errors, warnings

    allowed = set(speakers)
    seen: list[str] = []

    for index, turn in enumerate(payload["turni"], start=1):
        # 4. ogni turno e' un oggetto
        if not isinstance(turn, dict):
            errors.append(f"Turno {index}: deve essere un oggetto, e' {type(turn).__name__}.")
            continue

        # 6b. nessuna chiave extra nel turno
        extra = sorted(set(turn) - TURN_KEYS)
        if extra:
            errors.append(f"Turno {index}: chiavi non ammesse {extra}. Ammesse: {sorted(TURN_KEYS)}.")

        # 5. speaker e testo presenti e stringhe
        for key in ("speaker", "testo"):
            if key not in turn:
                errors.append(f"Turno {index}: manca '{key}'.")
            elif not isinstance(turn[key], str):
                errors.append(
                    f"Turno {index}: '{key}' deve essere una stringa, "
                    f"e' {type(turn[key]).__name__}."
                )

        speaker = turn.get("speaker")
        testo = turn.get("testo")
        if not isinstance(speaker, str) or not isinstance(testo, str):
            continue

        # 11 + 12: testo non vuoto ne' di soli spazi
        if not testo.strip():
            errors.append(f"Turno {index}: 'testo' e' vuoto o fatto di soli spazi.")

        # 9. speaker ammesso
        if speaker.strip() not in allowed:
            errors.append(
                f"Turno {index}: speaker '{speaker}' non e' fra quelli selezionati "
                f"({', '.join(speakers)})."
            )
        elif speaker.strip() not in seen:
            seen.append(speaker.strip())

        # avvisi di stile (non bloccano)
        if _MARKDOWN.search(testo):
            warnings.append(f"Turno {index}: sembra contenere markdown, che verra' letto ad alta voce.")
        regia = _STAGE_DIRECTION.search(testo)
        if regia:
            warnings.append(
                f"Turno {index}: {regia.group(0)} sembra un'indicazione di regia, "
                "non un'intenzione di voce. Le tag descrivono come si parla, non "
                "cosa succede nell'episodio."
            )
        tag_count = len(_INLINE_TAG.findall(testo))
        if tag_count > MAX_TAGS_PER_TURN:
            warnings.append(
                f"Turno {index}: {tag_count} tag di espressivita', sopra le "
                f"{MAX_TAGS_PER_TURN} consigliate. Troppe accentazioni di fila "
                "suonano caricaturali: tieni le due che servono davvero."
            )
        if _EMOJI.search(testo):
            warnings.append(f"Turno {index}: contiene emoji.")
        accenti = _ACCENTO_APOSTROFO.findall(testo)
        if accenti:
            campione = ", ".join(sorted({f"{a}'" for a in accenti})[:5])
            warnings.append(
                f"Turno {index}: accento scritto con l'apostrofo ({campione}). "
                "Usa le lettere accentate (e', piu', cosi' -> è, più, così): "
                "il sintetizzatore legge l'accento grafico per decidere dove "
                "cade l'accento tonico."
            )
        if len(testo) > MAX_TURN_CHARS:
            warnings.append(
                f"Turno {index}: {len(testo)} caratteri, sopra i {MAX_TURN_CHARS} "
                "consigliati. Valuta di spezzarlo."
            )

    # 10. ogni speaker selezionato ha almeno una battuta
    silent = [s for s in speakers if s not in seen]
    if silent:
        errors.append("Questi speaker selezionati non hanno nessuna battuta: " + ", ".join(silent))

    warnings.extend(_check_format(payload["turni"], script_format, interviewer))
    return errors, warnings


def _check_format(
    turni: list[Any], script_format: str, interviewer: str | None
) -> list[str]:
    """Controlla che il formato dichiarato sia rispettato.

    AVVISI, mai errori. Una domanda retorica dell'intervistato e una battuta
    dell'intervistatore senza punto interrogativo sono scelte di scrittura
    legittime: il gate dell'approvazione non deve bloccarsi su di esse. Qui si
    rende visibile uno scarto fra il formato chiesto e il copione scritto, e
    decide chi legge.
    """
    if script_format != "intervista" or not interviewer:
        return []

    out: list[str] = []
    for index, turn in enumerate(turni, start=1):
        if not isinstance(turn, dict):
            continue
        speaker = turn.get("speaker")
        testo = turn.get("testo")
        if not isinstance(speaker, str) or not isinstance(testo, str):
            continue

        if speaker.strip() == interviewer:
            if not _DOMANDA.search(testo):
                out.append(
                    f"Turno {index} ({speaker}): l'intervistatore non fa nessuna "
                    "domanda. In formato intervista ogni suo turno deve chiedere "
                    "qualcosa, altrimenti non ha motivo di esistere."
                )
            if _PORTA_CONTENUTO.search(testo):
                out.append(
                    f"Turno {index} ({speaker}): l'intervistatore sembra portare "
                    "dati invece di chiederli. Il contenuto e' dell'intervistato: "
                    "trasformalo in domanda o spostalo."
                )
            if len(_INLINE_TAG.findall(testo)) > 1:
                out.append(
                    f"Turno {index} ({speaker}): piu' di una tag su un turno di "
                    "domanda. La deroga del formato ne ammette una sola, variata "
                    "da un turno all'altro."
                )
        elif "?" in testo:
            out.append(
                f"Turno {index} ({speaker}): l'intervistato fa una domanda. Se e' "
                "retorica, o e' una citazione che finisce con un punto "
                "interrogativo, va bene cosi'; altrimenti i ruoli si stanno "
                "scambiando."
            )

    return out


def _capture_music_context(run_dir: Path, config) -> dict[str, dict[str, str]] | None:
    """Intro/outro con parlato: true, al momento della PRIMA stesura del copione.

    Serve solo al confronto sha256 di mix_episode() ("il copione era stato
    scritto per un'altra intro/outro"): se intro/outro non hanno parlato non
    c'e' niente che il copione debba evitare di ripetere, quindi non c'e'
    niente da registrare. Se qualcosa nella lettura fallisce (musiche.json
    assente, brano non piu' valido) non blocca il salvataggio del copione:
    quel problema e' di competenza di music_set.py/mix.py, non di questo tool.
    """
    try:
        choice = load_music_choice(run_dir)
        library = MusicLibrary(config.music_dir, config.accepted_extensions)
        library.scan()
    except Exception:
        return None

    context: dict[str, dict[str, str]] = {}
    for category in ("intro", "outro"):
        name = choice.get(category)
        if not name:
            continue
        try:
            track = library.get_usable(category, name)
        except Exception:
            continue
        if track.metadata.parlato:
            context[category] = {"name": track.name, "sha256": track.sha256()}

    return context or None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Cartella dell'episodio")
    parser.add_argument("--script-json", required=True, help="File JSON col copione")
    parser.add_argument("--speakers", nargs="+", required=True, help="Speaker ammessi")
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

    # La sessione va letta PRIMA della validazione: e' lei a ricordare il
    # formato scelto all'avvio, e il formato cambia quali controlli si fanno.
    session_path = run_dir / SESSION_FILENAME
    if session_path.is_file():
        session = ScriptSession.load(session_path)
    else:
        session = ScriptSession.create(
            document_path=args.document or "",
            document_text="",
            speakers=list(args.speakers),
            prompt="(copione scritto dal subagent, non dall'API)",
            path=session_path,
            script_format=str(config.script.get("default_format", "conversazione")),
            music_context=_capture_music_context(run_dir, config),
            active_lessons=active_lessons_summary(PROJECT_ROOT / "prompts"),
        )

    # Precedenza: flag esplicito > quanto gia' deciso nella sessione. Una
    # revisione non deve far ricomparire il default e cambiare formato di
    # nascosto a meta' episodio.
    if args.script_format:
        session.script_format = args.script_format
    if args.interviewer:
        session.interviewer = args.interviewer

    if session.script_format not in FORMATS:
        raise ValueError(
            f"Formato '{session.script_format}' sconosciuto. Valori ammessi: "
            + ", ".join(FORMATS)
        )
    if session.script_format == "intervista":
        if not session.interviewer:
            raise ValueError(
                "Il formato 'intervista' richiede --interviewer: senza sapere chi "
                "fa le domande non posso controllare che i ruoli siano rispettati."
            )
        if session.interviewer not in args.speakers:
            raise ValueError(
                f"--interviewer '{session.interviewer}' non e' fra gli speaker "
                f"selezionati ({', '.join(args.speakers)})."
            )

    errors, warnings = _validate(
        payload, args.speakers, session.script_format, session.interviewer
    )
    if errors:
        raise ValueError(
            "Il copione non e' valido e NON e' stato salvato:\n  - "
            + "\n  - ".join(errors)
        )

    script = PodcastScript.from_dict(payload)

    was_approved = session.approved is not None
    session.record(script, instruction=args.instructions)
    session.save()

    script_path = script.save(run_dir / "conversazione.json")
    note(f"Copione versione {session.version_count} salvato in {script_path}")

    approval_problem = session.approval_problem(script.to_dict())
    if was_approved and approval_problem:
        note("L'approvazione precedente e' decaduta: il copione e' cambiato.")

    for warning in warnings:
        note(f"avviso: {warning}")

    emit(
        {
            "titolo": script.titolo,
            "turni": script.turni,
            "speakers": script.speakers,
            "format": session.script_format,
            "interviewer": session.interviewer,
            "turn_count": len(script.turni),
            "chars": script.total_chars,
            "words": script.total_words,
            "version": session.version_count,
            "revision_count": session.revision_count,
            "warnings": warnings,
            "approved": session.approved is not None and approval_problem is None,
            "approval_problem": approval_problem,
            "script_file": str(script_path),
            "session_file": str(session_path),
            "rendered": script.render(),
        }
    )


if __name__ == "__main__":
    run_tool(main)
