"""Regole di validazione del copione, condivise da TUTTI i percorsi di scrittura.

Il copione arriva da tre strade: scritto dal subagent e salvato con
tools/script_save.py, scritto dall'API con tools/script_write.py /
script_revise.py, modificato a mano battuta per battuta (tools/script_edit.py).
Le regole sono le stesse per tutte e tre e vivono qui, in un posto solo: due
copie finirebbero per divergere, e il gate di approvazione vale solo se cio'
che si approva e' stato controllato allo stesso modo.

Estratto da tools/script_save.py, dove era nato: i messaggi sono rimasti
identici.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .script_writer import INLINE_TAG as _INLINE_TAG

FORMATS = ("conversazione", "intervista")

#: Un turno dell'intervistatore deve CHIEDERE. Il punto interrogativo e' il
#: segnale, ma non l'unico: "dimmi perche'" e' una domanda senza "?".
_DOMANDA = re.compile(r"\?|\b(dimmi|spiegami|raccontami|chiarisci)\b", re.IGNORECASE)

#: Spie di un intervistatore che ha iniziato a spiegare invece di chiedere:
#: cifre e percentuali sono contenuto, non domanda. Deliberatamente grossolano,
#: perche' produce un AVVISO e non un blocco (vedi il commento in validate_script).
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


def fold_name(value: str) -> str:
    """Minuscole, senza accenti ne' punteggiatura: solo per cercare un nome nel testo."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w\s]", " ", stripped.lower()).split())


def parse_participant(spec: str) -> dict[str, str]:
    """"Speaker|Nome pronunciato|Ruolo" -> {"speaker", "nome", "ruolo"}."""
    parts = [p.strip() for p in spec.split("|")]
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            f"--participant '{spec}' non e' nella forma "
            '"Speaker|Nome Cognome pronunciato|Chi e\' e di cosa si occupa". '
            "Se manca un pezzo, chiedilo all'utente invece di inventarlo."
        )
    return {"speaker": parts[0], "nome": parts[1], "ruolo": parts[2]}


def check_presentation(
    turni: list[Any],
    presenter: str,
    participants: list[dict[str, str]],
    speakers: list[str],
) -> list[str]:
    """Errori BLOCCANTI sulla presentazione iniziale.

    Sono errori e non avvisi perche' la presentazione e' un requisito esplicito
    (nome e cognome di tutti, detti all'inizio), non una scelta di stile: un
    copione che la omette o la mette a meta' non e' quello che l'utente ha chiesto.
    """
    errors: list[str] = []
    dialogue = [s for s in speakers if s != presenter]
    by_speaker = {p["speaker"]: p for p in participants}

    for speaker in dialogue:
        if speaker not in by_speaker:
            errors.append(
                f"Manca nome pronunciato e ruolo di '{speaker}' (--participant): "
                "chiedili all'utente, non inventarli."
            )
    for speaker in by_speaker:
        if speaker not in dialogue:
            errors.append(
                f"--participant '{speaker}' non e' fra gli speaker del dialogo "
                f"({', '.join(dialogue)}): il presentatore non si presenta da solo."
            )

    # Il presentatore parla SOLO all'inizio: i suoi turni sono un blocco
    # contiguo che apre l'episodio.
    names = [t.get("speaker", "").strip() if isinstance(t, dict) else "" for t in turni]
    if not names or names[0] != presenter:
        errors.append(
            f"Il copione deve aprire con la presentazione: il primo turno e' di "
            f"'{presenter}' (voce di presentazione)."
        )
        return errors
    end = 0
    while end < len(names) and names[end] == presenter:
        end += 1
    if presenter in names[end:]:
        errors.append(
            f"'{presenter}' e' la voce di presentazione: parla solo nei turni "
            f"iniziali (1-{end}), non piu' avanti nell'episodio."
        )
    if end == len(names):
        errors.append("Dopo la presentazione manca il dialogo: tutti i turni sono del presentatore.")

    spoken = fold_name(" ".join(
        t.get("testo", "") for t in turni[:end] if isinstance(t, dict) and isinstance(t.get("testo"), str)
    ))
    for person in participants:
        if fold_name(person["nome"]) not in spoken:
            errors.append(
                f"La presentazione non nomina '{person['nome']}' per nome e cognome "
                "(deve dirlo esattamente cosi', nei turni iniziali del presentatore)."
            )
    return errors


def validate_script(
    payload: Any,
    speakers: list[str],
    script_format: str = "conversazione",
    interviewer: str | None = None,
    presenter: str | None = None,
    participants: list[dict[str, str]] | None = None,
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

    if presenter:
        errors.extend(
            check_presentation(payload["turni"], presenter, participants or [], speakers)
        )

    warnings.extend(check_format(payload["turni"], script_format, interviewer, presenter))
    return errors, warnings


def check_format(
    turni: list[Any],
    script_format: str,
    interviewer: str | None,
    presenter: str | None = None,
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

        if presenter and speaker.strip() == presenter:
            continue  # la voce di presentazione non e' ne' intervistatore ne' intervistato

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


def check_cast(
    speakers: list[str],
    script_format: str,
    interviewer: str | None,
    presenter: str | None,
) -> None:
    """Coerenza fra formato, ruoli e speaker, PRIMA di guardare il copione.

    Solleva ValueError al primo problema (exit 1): sono errori di chi chiama,
    non del copione, e il messaggio dice quale argomento correggere.
    """
    if presenter:
        if presenter not in speakers:
            raise ValueError(
                f"Il presentatore '{presenter}' non e' fra gli speaker "
                f"({', '.join(speakers)}): includilo in --speakers."
            )
        if presenter == interviewer:
            raise ValueError(
                "La voce di presentazione deve essere una terza voce, diversa "
                "dall'intervistatore."
            )

    if script_format not in FORMATS:
        raise ValueError(
            f"Formato '{script_format}' sconosciuto. Valori ammessi: "
            + ", ".join(FORMATS)
        )
    if script_format == "intervista":
        if not interviewer:
            raise ValueError(
                "Il formato 'intervista' richiede --interviewer: senza sapere chi "
                "fa le domande non posso controllare che i ruoli siano rispettati."
            )
        if interviewer not in speakers:
            raise ValueError(
                f"--interviewer '{interviewer}' non e' fra gli speaker "
                f"selezionati ({', '.join(speakers)})."
            )


__all__ = [
    "FORMATS",
    "MAX_TAGS_PER_TURN",
    "MAX_TURN_CHARS",
    "check_cast",
    "check_format",
    "check_presentation",
    "fold_name",
    "parse_participant",
    "validate_script",
]
