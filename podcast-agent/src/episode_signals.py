"""Raccolta dei segnali di un episodio, per imparare come scrivere meglio il prossimo.

Deterministico: nessuna chiamata a modelli, nessun costo. Legge solo quello
che il progetto gia' salva — `_script_session.json`, `segmenti/scelte.json`,
`_sintesi_log.json` — e scrive `output/<run>/_segnali.json`. Chi vuole
aggregare piu' episodi e proporre delle lezioni usa `src/lessons.py`, che
consuma l'output di questo modulo e non rilegge mai i file grezzi.

Tre cose salvate qui meritano una nota:

- L'allineamento bozza/testo-approvato NON usa la posizione del turno (un
  turno aggiunto o tolto sposta tutti quelli dopo): usa la somiglianza
  testuale di `align_turns()`.
- "Corretto dopo l'ascolto" non si deduce da un mtime — un mtime cambia
  copiando la cartella, rimontando o ripristinando un backup. Si registra un
  FATTO esplicito con `record_synthesis_event()`, chiamato da
  `tools/synthesize.py` a ogni montaggio riuscito.
- "Non disponibile" e "zero" sono due cose diverse (vedi `retake_signals()`):
  confonderle nell'aggregazione farebbe sembrare "senza problemi" un episodio
  di cui in realta' non si sa niente perche' i segmenti sono stati cancellati.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .script_session import FILENAME as SESSION_FILENAME
from .script_session import script_hash
from .script_writer import INLINE_TAG, PodcastScript
from .segment_cache import RunCache, SegmentCacheError
from .voice_provider import VoiceProvider

SCHEMA_VERSION = 1
SIGNALS_FILENAME = "_segnali.json"
SYNTHESIS_LOG_FILENAME = "_sintesi_log.json"

#: Sotto questa somiglianza (difflib.SequenceMatcher.ratio(), 0-1) due turni
#: non si considerano lo stesso turno riscritto. Sovrascrivibile da
#: config.yaml (lessons.turn_similarity_threshold): questo e' solo il default
#: per chi chiama le funzioni di questo modulo senza passare la config.
DEFAULT_SIMILARITY_THRESHOLD = 0.35

#: Sotto questo rapporto lunghezza-nuovo/lunghezza-vecchio il turno e'
#: "accorciato"; sopra "allungato"; in mezzo "riscritto".
_SHORTER_RATIO = 0.8
_LONGER_RATIO = 1.2

_QUESTION_RE = re.compile(r"\?")
#: Sigle: 2-6 lettere maiuscole consecutive (PIL, ISTAT, UE...). Grossolano
#: di proposito, come _PORTA_CONTENUTO in script_save.py: qui serve solo a
#: contare una caratteristica del turno, non a bloccare niente.
_SIGLA_RE = re.compile(r"\b[A-Z]{2,6}\b")
_NUMERO_RE = re.compile(r"\d")
_PUNTEGGIATURA_COMPLESSA_RE = re.compile(r"[;:—–]|\.\.\.|«|»")


class SignalsError(RuntimeError):
    """La run non contiene quello che serve per raccogliere i segnali."""


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _write_atomic_json(target: Path, payload: Any) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def _load_json(target: Path) -> Any | None:
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ----------------------------------------------------------------------
# Evento di sintesi: il fatto esplicito che sostituisce l'mtime
# ----------------------------------------------------------------------


def record_synthesis_event(run_dir: Path, script: PodcastScript) -> Path:
    """Registra CHE un montaggio e' andato a buon fine, con lo sha256 del copione.

    Log append-only: ogni sintesi riuscita aggiunge una riga, non sovrascrive
    quella di prima. Serve a `post_listen_signals()` per sapere, versione per
    versione, se era stata salvata PRIMA o DOPO l'evento di sintesi piu'
    vicino — cioe' se e' una revisione qualunque o una correzione nata
    dopo aver sentito l'episodio.
    """
    target = run_dir / SYNTHESIS_LOG_FILENAME
    events = _load_json(target)
    if not isinstance(events, list):
        events = []
    events.append(
        {
            "schema_version": SCHEMA_VERSION,
            "at": _now(),
            "script_sha256": script_hash(script.to_dict()),
        }
    )
    return _write_atomic_json(target, events)


def load_synthesis_log(run_dir: Path) -> list[dict[str, Any]] | None:
    """None = il file non esiste (episodio mai sintetizzato con questa funzione
    attiva, o run precedente a questa funzione): va dichiarato non disponibile,
    non trattato come "zero eventi". Vedi post_listen_signals()."""
    raw = _load_json(Path(run_dir) / SYNTHESIS_LOG_FILENAME)
    if raw is None:
        return None
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


# ----------------------------------------------------------------------
# Allineamento dei turni: MAI per posizione
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class TurnChange:
    tipo: str  # invariato | riscritto | accorciato | allungato | spostato | eliminato | aggiunto
    vecchio_indice: int | None
    nuovo_indice: int | None
    speaker: str
    vecchio_testo: str | None
    nuovo_testo: str | None
    similarita: float | None


def align_turns(
    old: list[dict[str, str]],
    new: list[dict[str, str]],
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[TurnChange]:
    """Accoppia i turni di `old` e `new` per SOMIGLIANZA TESTUALE, non per indice.

    Un turno aggiunto o tolto sposta la posizione di tutti quelli dopo: usare
    l'indice per l'allineamento scambierebbe "turno 5 invariato" con "turno 5
    riscritto" ogni volta che qualcosa cambia prima. Qui invece:

    1. prima passata: `difflib.SequenceMatcher` sulle due liste di testi
       trova i blocchi "equal" — turni identici, ANCHE se non contigui — e li
       segna invariati;
    2. seconda passata: sui turni rimasti, la somiglianza testuale a coppie
       decide chi e' "lo stesso turno cambiato" (sopra soglia) e chi invece
       e' un turno nuovo/tolto senza corrispondenza (sotto soglia). Un testo
       identico trovato qui (non nei blocchi "equal" del punto 1) e' un
       turno SPOSTATO: stesso contenuto, ordine diverso rispetto agli altri.
    """
    old_texts = [t.get("testo", "") for t in old]
    new_texts = [t.get("testo", "") for t in new]

    matcher = SequenceMatcher(None, old_texts, new_texts, autojunk=False)
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    changes: list[TurnChange] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(i2 - i1):
            oi, ni = i1 + offset, j1 + offset
            matched_old.add(oi)
            matched_new.add(ni)
            changes.append(
                TurnChange(
                    tipo="invariato",
                    vecchio_indice=oi,
                    nuovo_indice=ni,
                    speaker=new[ni].get("speaker", ""),
                    vecchio_testo=old_texts[oi],
                    nuovo_testo=new_texts[ni],
                    similarita=1.0,
                )
            )

    remaining_old = [i for i in range(len(old)) if i not in matched_old]
    remaining_new = [j for j in range(len(new)) if j not in matched_new]

    pairs: list[tuple[float, int, int]] = []
    for oi in remaining_old:
        for nj in remaining_new:
            ratio = SequenceMatcher(None, old_texts[oi], new_texts[nj], autojunk=False).ratio()
            if ratio >= similarity_threshold:
                pairs.append((ratio, oi, nj))
    # Le coppie piu' somiglianti vincono per prime: e' un abbinamento
    # goloso, non ottimo globalmente, ma deterministico e sufficiente per
    # poche decine di turni per episodio.
    pairs.sort(key=lambda p: p[0], reverse=True)

    used_old: set[int] = set()
    used_new: set[int] = set()
    for ratio, oi, nj in pairs:
        if oi in used_old or nj in used_new:
            continue
        used_old.add(oi)
        used_new.add(nj)
        old_text, new_text = old_texts[oi], new_texts[nj]
        if old_text == new_text:
            tipo = "spostato"
        else:
            len_ratio = len(new_text) / len(old_text) if old_text else float("inf")
            if len_ratio < _SHORTER_RATIO:
                tipo = "accorciato"
            elif len_ratio > _LONGER_RATIO:
                tipo = "allungato"
            else:
                tipo = "riscritto"
        changes.append(
            TurnChange(
                tipo=tipo,
                vecchio_indice=oi,
                nuovo_indice=nj,
                speaker=new[nj].get("speaker", ""),
                vecchio_testo=old_text,
                nuovo_testo=new_text,
                similarita=round(ratio, 3),
            )
        )

    for oi in remaining_old:
        if oi in used_old:
            continue
        changes.append(
            TurnChange(
                tipo="eliminato",
                vecchio_indice=oi,
                nuovo_indice=None,
                speaker=old[oi].get("speaker", ""),
                vecchio_testo=old_texts[oi],
                nuovo_testo=None,
                similarita=None,
            )
        )
    for nj in remaining_new:
        if nj in used_new:
            continue
        changes.append(
            TurnChange(
                tipo="aggiunto",
                vecchio_indice=None,
                nuovo_indice=nj,
                speaker=new[nj].get("speaker", ""),
                vecchio_testo=None,
                nuovo_testo=new_texts[nj],
                similarita=None,
            )
        )

    changes.sort(
        key=lambda c: (
            c.nuovo_indice if c.nuovo_indice is not None else 10**9,
            c.vecchio_indice if c.vecchio_indice is not None else 10**9,
        )
    )
    return changes


def _turn_change_to_dict(c: TurnChange) -> dict[str, Any]:
    return {
        "tipo": c.tipo,
        "vecchio_turno": (c.vecchio_indice + 1) if c.vecchio_indice is not None else None,
        "nuovo_turno": (c.nuovo_indice + 1) if c.nuovo_indice is not None else None,
        "speaker": c.speaker,
        "vecchio_testo": c.vecchio_testo,
        "nuovo_testo": c.nuovo_testo,
        "similarita": c.similarita,
    }


# ----------------------------------------------------------------------
# Tag: posizione, rimozione, aggiunta, sostituzione
# ----------------------------------------------------------------------


def _tag_position(testo: str, tag_start: int) -> str:
    """'apertura' se la tag e' il primo elemento non-spazio della battuta."""
    return "apertura" if testo[:tag_start].strip() == "" else "interna"


def extract_tags(testo: str) -> list[dict[str, str]]:
    return [
        {"tag": m.group(0)[1:-1].strip().lower(), "posizione": _tag_position(testo, m.start())}
        for m in INLINE_TAG.finditer(testo)
    ]


def is_question(testo: str) -> bool:
    return bool(_QUESTION_RE.search(testo))


def diff_tags(old_testo: str, new_testo: str) -> dict[str, list[dict[str, str]]]:
    """Tag rimosse, aggiunte o sostituite fra due testi dello STESSO turno.

    Una tag ritrovata identica (stesso testo, stessa posizione) non e'
    cambiata e non compare nel risultato. Se resta esattamente una tag tolta
    e una aggiunta, e' una SOSTITUZIONE, non due eventi indipendenti — e' il
    caso piu' interessante per le lezioni ("questa tag qui non funziona,
    quest'altra si'").
    """
    old_pool = list(extract_tags(old_testo))
    new_pool = list(extract_tags(new_testo))

    for ot in list(old_pool):
        match = next((nt for nt in new_pool if nt == ot), None)
        if match is not None:
            old_pool.remove(ot)
            new_pool.remove(match)

    removed, added = old_pool, new_pool
    sostituite: list[dict[str, str]] = []
    if len(removed) == 1 and len(added) == 1:
        sostituite = [
            {
                "da": removed[0]["tag"],
                "a": added[0]["tag"],
                "posizione_da": removed[0]["posizione"],
                "posizione_a": added[0]["posizione"],
            }
        ]
        removed, added = [], []

    return {"rimosse": removed, "aggiunte": added, "sostituite": sostituite}


def _turn_characteristics(testo: str) -> dict[str, Any]:
    tags = extract_tags(testo)
    return {
        "lunghezza_caratteri": len(testo),
        "ha_tag": bool(tags),
        "tag": [t["tag"] for t in tags],
        "ha_numeri": bool(_NUMERO_RE.search(testo)),
        "ha_sigle": bool(_SIGLA_RE.search(testo)),
        "e_domanda": is_question(testo),
        "punteggiatura_complessa": bool(_PUNTEGGIATURA_COMPLESSA_RE.search(testo)),
    }


# ----------------------------------------------------------------------
# Sezioni del report
# ----------------------------------------------------------------------


def _bozza_vs_approvato(session_raw: dict[str, Any], *, similarity_threshold: float) -> list[TurnChange] | None:
    """Confronta la PRIMA stesura con il testo approvato, non l'ultima bozza
    prima del si': cosi' il confronto cattura l'effetto CUMULATIVO di tutte
    le revisioni, che e' quello che dice come scrivo io rispetto a come lo
    vuole l'utente. None se l'episodio non ha (ancora) un'approvazione: non
    c'e' un "prima e dopo" da confrontare per un episodio in lavorazione.
    """
    approved = session_raw.get("approved")
    versions = session_raw.get("versions") or []
    if not approved or not versions:
        return None

    approved_entry = next((v for v in versions if v.get("version") == approved.get("version")), None)
    if approved_entry is None:
        return None

    bozza_entry = versions[0]
    bozza_turni = (bozza_entry.get("script") or {}).get("turni") or []
    approvato_turni = (approved_entry.get("script") or {}).get("turni") or []
    return align_turns(bozza_turni, approvato_turni, similarity_threshold=similarity_threshold)


def _tag_signals(changes: list[TurnChange] | None, approved_turni: list[dict[str, str]]) -> dict[str, Any]:
    if changes is None:
        return {"disponibile": False, "motivo": "nessun copione approvato.", "eventi": [], "densita": None}

    eventi: list[dict[str, Any]] = []
    for c in changes:
        if c.tipo not in ("riscritto", "accorciato", "allungato"):
            continue
        diff = diff_tags(c.vecchio_testo or "", c.nuovo_testo or "")
        domanda = is_question(c.nuovo_testo or c.vecchio_testo or "")
        turno = (c.nuovo_indice + 1) if c.nuovo_indice is not None else None
        for t in diff["rimosse"]:
            eventi.append({"evento": "rimossa", "tag": t["tag"], "posizione": t["posizione"], "turno": turno, "domanda": domanda})
        for t in diff["aggiunte"]:
            eventi.append({"evento": "aggiunta", "tag": t["tag"], "posizione": t["posizione"], "turno": turno, "domanda": domanda})
        for s in diff["sostituite"]:
            eventi.append(
                {
                    "evento": "sostituita",
                    "da": s["da"],
                    "a": s["a"],
                    "posizione_da": s["posizione_da"],
                    "posizione_a": s["posizione_a"],
                    "turno": turno,
                    "domanda": domanda,
                }
            )

    tag_totali = 0
    domande_con_tag = 0
    domande_totali = 0
    for turn in approved_turni:
        testo = turn.get("testo", "")
        tags = extract_tags(testo)
        tag_totali += len(tags)
        if is_question(testo):
            domande_totali += 1
            if tags:
                domande_con_tag += 1

    n_turni = len(approved_turni) or 1
    densita = {
        "tag_per_turno": round(tag_totali / n_turni, 3),
        "tag_totali": tag_totali,
        "turni_totali": len(approved_turni),
        "domande_totali": domande_totali,
        "domande_con_tag": domande_con_tag,
    }
    return {"disponibile": True, "eventi": eventi, "densita": densita}


def post_listen_signals(
    session_raw: dict[str, Any], synthesis_log: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Correzioni testuali arrivate DOPO che l'episodio era gia' stato sintetizzato
    almeno una volta: il segnale piu' forte, perche' nasce dall'ascolto e non
    dalla lettura. Distingue "non disponibile" (nessun _sintesi_log.json, run
    precedente a questa funzione o mai sintetizzata) da "disponibile con zero
    correzioni" (sintetizzata, mai corretta dopo): sono due fatti diversi.
    """
    if synthesis_log is None:
        return {
            "disponibile": False,
            "motivo": "nessun _sintesi_log.json: episodio non ancora sintetizzato, "
            "oppure prodotto prima che questa funzione esistesse.",
            "correzioni": [],
        }
    if not synthesis_log:
        return {"disponibile": True, "correzioni": []}

    versions = session_raw.get("versions") or []
    by_sha = {v.get("sha256"): v for v in versions}
    events = sorted(synthesis_log, key=lambda e: str(e.get("at", "")))

    corrections: list[dict[str, Any]] = []
    for version in versions:
        v_at = str(version.get("at", ""))
        prior_events = [e for e in events if str(e.get("at", "")) < v_at]
        if not prior_events:
            continue  # nessuna sintesi prima di questa versione: revisione pre-ascolto
        last_event = prior_events[-1]
        synth_version = by_sha.get(last_event.get("script_sha256"))
        if synth_version is None or synth_version is version:
            continue

        old_turni = (synth_version.get("script") or {}).get("turni") or []
        new_turni = (version.get("script") or {}).get("turni") or []
        changes = align_turns(old_turni, new_turni)
        for c in changes:
            if c.tipo in ("invariato", "spostato", "aggiunto"):
                # "aggiunto" non e' la correzione di un turno gia' sentito.
                continue
            corrections.append(
                {
                    "versione": version.get("version"),
                    "a": version.get("at"),
                    "tipo": c.tipo,
                    "speaker": c.speaker,
                    "vecchio_testo": c.vecchio_testo,
                    "nuovo_testo": c.nuovo_testo,
                }
            )
    return {"disponibile": True, "correzioni": corrections}


def retake_signals(
    run_dir: Path, script: PodcastScript | None, provider: VoiceProvider | None
) -> dict[str, Any]:
    """Turni rifatti, quante volte, quale take scelto. "disponibile: false" con
    un motivo quando non si puo' sapere — segmenti cancellati con
    --purge-segments, episodio mai sintetizzato, cache illeggibile — MAI
    "turni: []" per quei casi: quello varrebbe "zero retake", che e' un fatto
    diverso da "non lo so".
    """
    run_dir = Path(run_dir)
    if script is None:
        return {"disponibile": False, "motivo": "conversazione.json mancante.", "turni": []}

    segments_dir = run_dir / "segmenti"
    synthesized_before = (run_dir / "_tempi_turni.json").is_file() or (
        run_dir / SYNTHESIS_LOG_FILENAME
    ).is_file()

    if not segments_dir.is_dir():
        motivo = (
            "cartella segmenti/ assente ma l'episodio risulta gia' sintetizzato: "
            "probabile --purge-segments. Sui retake non si puo' dire niente."
            if synthesized_before
            else "episodio non ancora sintetizzato."
        )
        return {"disponibile": False, "motivo": motivo, "turni": []}

    if provider is None:
        return {
            "disponibile": False,
            "motivo": "nessun provider vocale disponibile per ricostruire le chiavi dei segmenti.",
            "turni": [],
        }

    try:
        cache = RunCache.load(run_dir, provider)
    except SegmentCacheError as exc:
        return {"disponibile": False, "motivo": f"cache dei segmenti non leggibile: {exc}", "turni": []}

    turni: list[dict[str, Any]] = []
    for numero, turn in enumerate(script.turni, start=1):
        takes = cache.takes(numero)
        if len(takes) <= 1:
            continue  # nessun retake su questo turno
        chosen = cache.chosen(numero)
        testo = turn["testo"]
        turni.append(
            {
                "turno": numero,
                "speaker": turn["speaker"],
                "tentativi": len(takes),
                "take_scelto": chosen,
                "primo_take_scelto": chosen == 1,
                "caratteristiche": _turn_characteristics(testo),
            }
        )
    return {"disponibile": True, "turni": turni}


def episode_indicators(
    session_raw: dict[str, Any],
    bozza_changes: list[TurnChange] | None,
    retake: dict[str, Any],
    post_listen: dict[str, Any],
) -> dict[str, Any]:
    """Indicatori per episodio, per vedere se le cose migliorano nel tempo.

    Sono numeri grezzi, non un giudizio: dipendono anche da quanto era
    difficile il documento e da quanto l'utente ha ascoltato con attenzione
    (vedi l'avvertenza in tools/lessons.py --trend).
    """
    revisions = session_raw.get("revisions") or []
    approved = session_raw.get("approved")

    revisioni_prima_approvazione = len(revisions)
    if approved:
        revisioni_prima_approvazione = len(
            [r for r in revisions if str(r.get("at", "")) < str(approved.get("at", ""))]
        )

    percentuale_turni_modificati = None
    tag_modificate = None
    if bozza_changes is not None:
        versions = session_raw.get("versions") or []
        bozza_turni = (versions[0].get("script") or {}).get("turni") or [] if versions else []
        approvato_entry = next((v for v in versions if v.get("version") == approved.get("version")), None) if approved else None
        approvato_turni = (approvato_entry.get("script") or {}).get("turni") or [] if approvato_entry else []
        denom = max(len(bozza_turni), len(approvato_turni)) or 1
        modificati = [c for c in bozza_changes if c.tipo != "invariato"]
        percentuale_turni_modificati = round(len(modificati) / denom * 100, 1)

        tag_events = 0
        for c in bozza_changes:
            if c.tipo not in ("riscritto", "accorciato", "allungato"):
                continue
            diff = diff_tags(c.vecchio_testo or "", c.nuovo_testo or "")
            tag_events += len(diff["rimosse"]) + len(diff["aggiunte"]) + len(diff["sostituite"])
        tag_modificate = tag_events

    retake_totali = len(retake.get("turni") or []) if retake.get("disponibile") else None
    percentuale_primi_take = None
    if retake.get("disponibile") and retake.get("turni"):
        primi = sum(1 for t in retake["turni"] if t["primo_take_scelto"])
        percentuale_primi_take = round(primi / len(retake["turni"]) * 100, 1)

    correzioni_post_ascolto = len(post_listen.get("correzioni") or []) if post_listen.get("disponibile") else None

    return {
        "revisioni_totali": len(revisions),
        "revisioni_prima_approvazione": revisioni_prima_approvazione,
        "percentuale_turni_modificati": percentuale_turni_modificati,
        "tag_modificate_dall_utente": tag_modificate,
        "retake_totali": retake_totali,
        "percentuale_primi_take_accettati": percentuale_primi_take,
        "correzioni_post_ascolto": correzioni_post_ascolto,
    }


# ----------------------------------------------------------------------
# Orchestratore
# ----------------------------------------------------------------------


def collect_signals(
    run_dir: Path,
    *,
    provider: VoiceProvider | None = None,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    session_path = run_dir / SESSION_FILENAME
    session_raw = _load_json(session_path)
    if session_raw is None:
        raise SignalsError(
            f"Nessuna sessione leggibile in {session_path}: niente da cui estrarre segnali."
        )

    script_raw = _load_json(run_dir / "conversazione.json")
    script = PodcastScript.from_dict(script_raw) if script_raw else None

    synthesis_log = load_synthesis_log(run_dir)

    bozza_changes = _bozza_vs_approvato(session_raw, similarity_threshold=similarity_threshold)
    approved_turni: list[dict[str, str]] = []
    if session_raw.get("approved"):
        versions = session_raw.get("versions") or []
        approved_entry = next(
            (v for v in versions if v.get("version") == session_raw["approved"].get("version")), None
        )
        if approved_entry:
            approved_turni = (approved_entry.get("script") or {}).get("turni") or []

    tag_section = _tag_signals(bozza_changes, approved_turni)
    post_listen = post_listen_signals(session_raw, synthesis_log)
    retake = retake_signals(run_dir, script, provider)
    indicators = episode_indicators(session_raw, bozza_changes, retake, post_listen)

    return {
        "schema_version": SCHEMA_VERSION,
        "run_dir": str(run_dir),
        "generato_il": _now(),
        "sessione": {
            "revisioni": len(session_raw.get("revisions") or []),
            "versioni": len(session_raw.get("versions") or []),
            "approvato": session_raw.get("approved") is not None,
        },
        "turni_bozza_vs_approvato": (
            [_turn_change_to_dict(c) for c in bozza_changes if c.tipo != "invariato"]
            if bozza_changes is not None
            else None
        ),
        "tag": tag_section,
        "correzioni_post_ascolto": post_listen,
        "retake": retake,
        "indicatori": indicators,
    }


def collect_and_write(
    run_dir: Path,
    *,
    provider: VoiceProvider | None = None,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> Path:
    payload = collect_signals(run_dir, provider=provider, similarity_threshold=similarity_threshold)
    return _write_atomic_json(Path(run_dir) / SIGNALS_FILENAME, payload)


__all__ = [
    "SCHEMA_VERSION",
    "SIGNALS_FILENAME",
    "SYNTHESIS_LOG_FILENAME",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "SignalsError",
    "TurnChange",
    "align_turns",
    "extract_tags",
    "is_question",
    "diff_tags",
    "record_synthesis_event",
    "load_synthesis_log",
    "post_listen_signals",
    "retake_signals",
    "episode_indicators",
    "collect_signals",
    "collect_and_write",
]
