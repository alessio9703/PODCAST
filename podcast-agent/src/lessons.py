"""Lezioni dagli episodi precedenti: aggregazione, proposta, approvazione.

Tre fasi, tre livelli di fiducia diversi:

1. `aggregate_evidence()` e' DETERMINISTICA: stessi `_segnali.json`, stesse
   prove, nessuna chiamata a modelli. E' quello che `--evidence` mostra.
2. `propose()` chiama un modello, ma solo sulle PROVE aggregate (mai sui
   copioni interi), e ogni lezione che restituisce deve puntare a uno schema
   che l'aggregazione ha davvero trovato sopra soglia — non una
   combinazione inventata. Finisce in `prompts/_lezioni.json` con
   `stato: "proposta"`, mai in `lezioni.md`.
3. `accept()` e' l'UNICA funzione che scrive in `lezioni.md`. Nessun altro
   percorso di questo modulo lo tocca: e' cosi' che "nessuna riga entra nel
   prompt senza approvazione esplicita" resta vero anche a livello di codice,
   non solo di procedura.

`prompts/_lezioni.json` e' l'unico database: proposte, rifiutate, attive e
rimosse restano tutte li', distinte da `stato`. `lezioni.md` e' sempre un
RENDER di quello che e' `attiva`, mai una fonte a se'.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .episode_signals import SCHEMA_VERSION

DB_FILENAME = "_lezioni.json"
MD_FILENAME = "lezioni.md"

#: Vocabolario CHIUSO delle categorie di pattern: condiviso da aggregazione,
#: prompt di proposta e validazione. Se una categoria non e' qui, --review non
#: la ritrovera' mai e la lezione resterebbe non verificabile per sempre —
#: per questo una proposta fuori vocabolario viene rifiutata dal tool, non
#: accettata con un avviso.
PATTERN_CATEGORIES: dict[str, tuple[str, ...]] = {
    "tag_rimossa": ("tag", "posizione"),
    "tag_aggiunta": ("tag", "posizione"),
    "tag_sostituita": ("da", "a"),
    "retake_caratteristica": ("caratteristica",),
    "correzione_post_ascolto": ("tipo",),
}

#: Lunghezza oltre la quale un turno rifatto conta come "lungo" in
#: retake_caratteristica. Meta' di MAX_TURN_CHARS (600) di script_save.py:
#: un turno gia' sopra la meta' del tetto consigliato e' un candidato
#: plausibile per "spezzalo", non un numero misurato.
LONG_TURN_CHARS = 300

#: Temi su cui una lezione non puo' MAI intervenire, qualunque cosa dica il
#: modello o l'utente in --text: allentarli per lo "stile" aggirerebbe
#: esattamente le regole che il gate di approvazione, il consenso vocale e i
#: costi dichiarati esistono per far rispettare.
_FORBIDDEN_TOPICS = re.compile(
    r"\b(approvazion\w*|consens\w*|costo|costi|prezzo|spend\w*|gate|"
    r"no-approval-gate|clonazion\w*)\b",
    re.IGNORECASE,
)

_PROPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lezioni": {
            "type": "array",
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "testo": {"type": "string"},
                    "pattern": {
                        "type": "object",
                        "properties": {
                            "categoria": {"type": "string", "enum": sorted(PATTERN_CATEGORIES)},
                            "dettaglio": {"type": "object"},
                        },
                        "required": ["categoria", "dettaglio"],
                        "additionalProperties": False,
                    },
                    "conflitto_copione": {"type": ["string", "null"]},
                },
                "required": ["testo", "pattern", "conflitto_copione"],
                "additionalProperties": False,
            },
        },
        "motivo_se_vuoto": {"type": ["string", "null"]},
    },
    "required": ["lezioni", "motivo_se_vuoto"],
    "additionalProperties": False,
}


class LessonsError(RuntimeError):
    """DB illeggibile, categoria fuori vocabolario, lezione non trovata, ecc."""


class NeedsRemoval(LessonsError):
    """Tetto di righe raggiunto: --accept deve indicare quale lezione togliere."""

    def __init__(self, candidates: list[dict[str, Any]]):
        super().__init__(
            "prompts/lezioni.md supererebbe il tetto di righe: indica quale "
            "lezione attiva togliere con --remove-id."
        )
        self.candidates = candidates


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _truncate(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


# ----------------------------------------------------------------------
# Database delle lezioni
# ----------------------------------------------------------------------


def load_db(prompts_dir: Path) -> list[dict[str, Any]]:
    target = Path(prompts_dir) / DB_FILENAME
    if not target.is_file():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise LessonsError(f"{target} non e' un JSON valido: {exc}") from exc
    if not isinstance(raw, list):
        raise LessonsError(f"{target} dovrebbe contenere una lista di lezioni.")
    return raw


def save_db(prompts_dir: Path, entries: list[dict[str, Any]]) -> Path:
    target = Path(prompts_dir) / DB_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def _next_id(entries: list[dict[str, Any]]) -> str:
    numbers = [int(e["id"][1:]) for e in entries if re.fullmatch(r"L\d+", str(e.get("id", "")))]
    return f"L{max(numbers, default=0) + 1}"


def active_lessons_summary(prompts_dir: Path) -> list[dict[str, str]]:
    """{"id", "data"} delle lezioni attive: quello che ScriptSession registra
    alla creazione, per sapere fra mesi con quali regole e' nato un episodio.
    """
    return [
        {"id": e["id"], "data": e.get("accettata_il", "")}
        for e in load_db(prompts_dir)
        if e.get("stato") == "attiva"
    ]


def render_lezioni_md(entries: list[dict[str, Any]]) -> str:
    """RENDER deterministico delle lezioni attive. Chiamata SOLO da accept():
    e' l'unico punto del modulo che scrive su lezioni.md."""
    active = [e for e in entries if e.get("stato") == "attiva"]
    if not active:
        return ""
    active = sorted(active, key=lambda e: e.get("accettata_il", ""))
    lines = [
        "# Lezioni dagli episodi precedenti",
        "",
        "Regole nate dall'osservazione di episodi passati e approvate una per una.",
        "In caso di conflitto con `copione.md`, vince sempre `copione.md`.",
        "",
    ]
    for e in active:
        lines.append(f"## {e['id']} — {e.get('accettata_il', '')}")
        lines.append(f"_Episodi: {', '.join(e.get('episodi', [])) or 'n/d'}_")
        lines.append("")
        lines.append(str(e.get("testo", "")).strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _line_count(text: str) -> int:
    return len([ln for ln in text.splitlines() if ln.strip()])


# ----------------------------------------------------------------------
# Aggregazione — deterministica
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class _EvidenceKey:
    categoria: str
    dettaglio: tuple[tuple[str, str], ...]

    def dettaglio_dict(self) -> dict[str, str]:
        return dict(self.dettaglio)


def _key(categoria: str, dettaglio: dict[str, Any]) -> _EvidenceKey:
    return _EvidenceKey(categoria, tuple(sorted((k, str(v)) for k, v in dettaglio.items())))


def _episode_id(signals: dict[str, Any]) -> str:
    run_dir = str(signals.get("run_dir", ""))
    return Path(run_dir).name or run_dir or "sconosciuto"


def _retake_caratteristiche_presenti(c: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if (c.get("lunghezza_caratteri") or 0) > LONG_TURN_CHARS:
        out.append("lungo")
    if c.get("ha_tag"):
        out.append("con_tag")
    if c.get("ha_numeri"):
        out.append("con_numeri")
    if c.get("ha_sigle"):
        out.append("con_sigle")
    if c.get("e_domanda"):
        out.append("domanda")
    if c.get("punteggiatura_complessa"):
        out.append("punteggiatura_complessa")
    return out


def _evento_nota(evento: dict[str, Any]) -> str:
    if evento.get("evento") == "sostituita":
        return f"[{evento.get('da')}] -> [{evento.get('a')}]"
    return f"[{evento.get('tag')}] in {evento.get('posizione')}"


def aggregate_evidence(
    signals_list: list[dict[str, Any]],
    *,
    min_occurrences: int,
    min_episodes: int,
    max_examples_per_pattern: int = 5,
    max_example_chars: int = 200,
) -> dict[str, Any]:
    """Aggrega piu' `_segnali.json` negli schemi ricorrenti. Deterministica:
    stessi ingressi, stesse prove, nessuna chiamata a modelli.

    Episodi con `schema_version` diverso da quello atteso vengono saltati
    (non aggregabili alla cieca), non trattati come "zero segnali".
    """
    occorrenze: dict[_EvidenceKey, list[dict[str, Any]]] = {}
    episodi_per_chiave: dict[_EvidenceKey, set[str]] = {}
    episodi_validi = 0

    for signals in signals_list:
        if signals.get("schema_version") != SCHEMA_VERSION:
            continue
        episodi_validi += 1
        episodio = _episode_id(signals)

        tag = signals.get("tag") or {}
        for evento in tag.get("eventi") or []:
            genere = evento.get("evento")
            if genere == "rimossa":
                k = _key("tag_rimossa", {"tag": evento.get("tag"), "posizione": evento.get("posizione")})
            elif genere == "aggiunta":
                k = _key("tag_aggiunta", {"tag": evento.get("tag"), "posizione": evento.get("posizione")})
            elif genere == "sostituita":
                k = _key("tag_sostituita", {"da": evento.get("da"), "a": evento.get("a")})
            else:
                continue
            occorrenze.setdefault(k, []).append(
                {"episodio": episodio, "turno": evento.get("turno"), "nota": _evento_nota(evento)}
            )
            episodi_per_chiave.setdefault(k, set()).add(episodio)

        retake = signals.get("retake") or {}
        if retake.get("disponibile"):
            for t in retake.get("turni") or []:
                for car in _retake_caratteristiche_presenti(t.get("caratteristiche") or {}):
                    k = _key("retake_caratteristica", {"caratteristica": car})
                    occorrenze.setdefault(k, []).append(
                        {
                            "episodio": episodio,
                            "turno": t.get("turno"),
                            "nota": f"rifatto {t.get('tentativi')} volte, scelto il take {t.get('take_scelto')}",
                        }
                    )
                    episodi_per_chiave.setdefault(k, set()).add(episodio)

        post_listen = signals.get("correzioni_post_ascolto") or {}
        if post_listen.get("disponibile"):
            for corr in post_listen.get("correzioni") or []:
                k = _key("correzione_post_ascolto", {"tipo": corr.get("tipo", "")})
                occorrenze.setdefault(k, []).append(
                    {
                        "episodio": episodio,
                        "turno": corr.get("speaker"),
                        "nota": _truncate(corr.get("nuovo_testo") or "", max_example_chars),
                    }
                )
                episodi_per_chiave.setdefault(k, set()).add(episodio)

    schemi: list[dict[str, Any]] = []
    for k, esempi in occorrenze.items():
        episodi = episodi_per_chiave[k]
        if len(esempi) < min_occurrences or len(episodi) < min_episodes:
            continue
        schemi.append(
            {
                "categoria": k.categoria,
                "dettaglio": k.dettaglio_dict(),
                "occorrenze": len(esempi),
                "episodi": sorted(episodi),
                "esempi": esempi[:max_examples_per_pattern],
            }
        )
    schemi.sort(key=lambda s: (-s["occorrenze"], s["categoria"]))

    return {
        "episodi_analizzati": episodi_validi,
        "soglia_occorrenze": min_occurrences,
        "soglia_episodi": min_episodes,
        "schemi": schemi,
    }


def _find_schema(schemi: list[dict[str, Any]], pattern: dict[str, Any]) -> dict[str, Any]:
    categoria = pattern.get("categoria")
    dettaglio = {k: str(v) for k, v in (pattern.get("dettaglio") or {}).items()}
    for s in schemi:
        if s["categoria"] == categoria and s["dettaglio"] == dettaglio:
            return s
    return {}


# ----------------------------------------------------------------------
# Proposta — chiama un modello, solo sulle prove aggregate
# ----------------------------------------------------------------------


def validate_proposal(item: dict[str, Any], schemi: list[dict[str, Any]]) -> str | None:
    """None = proposta valida. Altrimenti il motivo, esplicito, del rifiuto."""
    pattern = item.get("pattern") or {}
    categoria = pattern.get("categoria")
    dettaglio = pattern.get("dettaglio") or {}

    if categoria not in PATTERN_CATEGORIES:
        return (
            f"categoria '{categoria}' fuori dal vocabolario chiuso "
            f"({', '.join(sorted(PATTERN_CATEGORIES))})."
        )
    allowed_keys = set(PATTERN_CATEGORIES[categoria])
    if set(dettaglio) != allowed_keys:
        return (
            f"'dettaglio' per la categoria '{categoria}' deve avere esattamente "
            f"le chiavi {sorted(allowed_keys)}, ricevuto {sorted(dettaglio)}."
        )
    if not _find_schema(schemi, pattern):
        return "il pattern non corrisponde a nessuno schema sopra soglia nelle prove fornite."

    testo = str(item.get("testo", ""))
    if not testo.strip():
        return "testo della lezione vuoto."
    if _FORBIDDEN_TOPICS.search(testo):
        return "la lezione tocca costi, approvazione o consenso vocale: non e' materia di una lezione di stile."
    return None


def _duplicate_of_rejected(item: dict[str, Any], entries: list[dict[str, Any]]) -> bool:
    testo = str(item.get("testo", "")).strip()
    pattern = item.get("pattern")
    return any(
        e.get("stato") == "rifiutata"
        and e.get("pattern") == pattern
        and str(e.get("testo", "")).strip() == testo
        for e in entries
    )


def estimate_propose_cost(
    prompt_text: str,
    lessons_config: dict[str, Any],
    *,
    expected_output_tokens: int = 800,
) -> dict[str, Any]:
    """Costo stimato PRIMA di chiamare il modello. ~4 caratteri/token in
    italiano: una stima grezza, dichiarata come tale (stesso principio di
    cost_estimate.py sul TTS: un numero indovinato e' peggio di nessun numero,
    per questo cost_usd resta None se i prezzi non sono in config.yaml).
    """
    input_tokens = max(1, len(prompt_text) // 4)
    in_rate = lessons_config.get("input_cost_per_million_tokens_usd")
    out_rate = lessons_config.get("output_cost_per_million_tokens_usd")
    cost_usd = None
    cost_basis = "non disponibile: compila lessons.input/output_cost_per_million_tokens_usd in config.yaml"
    if in_rate is not None and out_rate is not None:
        cost_usd = (
            input_tokens / 1_000_000 * float(in_rate)
            + expected_output_tokens / 1_000_000 * float(out_rate)
        )
        cost_basis = f"${float(in_rate):.2f}/1M input + ${float(out_rate):.2f}/1M output (stima)"
    return {
        "input_tokens_stimati": input_tokens,
        "output_tokens_stimati_max": expected_output_tokens,
        "cost_usd": round(cost_usd, 4) if cost_usd is not None else None,
        "cost_basis": cost_basis,
    }


def build_propose_prompt(
    aggregated: dict[str, Any],
    *,
    copione_text: str,
    active_lessons_text: str,
    max_examples_per_pattern: int,
    max_example_chars: int,
) -> str:
    schemi = []
    for s in aggregated["schemi"]:
        esempi = [
            {
                "episodio": e.get("episodio"),
                "turno": e.get("turno"),
                "nota": _truncate(str(e.get("nota", "")), max_example_chars),
            }
            for e in (s.get("esempi") or [])[:max_examples_per_pattern]
        ]
        schemi.append(
            {
                "categoria": s["categoria"],
                "dettaglio": s["dettaglio"],
                "occorrenze": s["occorrenze"],
                "episodi": s["episodi"],
                "esempi": esempi,
            }
        )
    prove_json = json.dumps(schemi, ensure_ascii=False, indent=2)

    return f"""Analizzi le prove aggregate di piu' episodi di un podcast, per proporre al massimo DUE lezioni di stile su come si scrive e si rende espressivo il copione.

Regole:
- Solo stile, espressivita', ritmo e struttura del copione. MAI il contenuto di un documento specifico: quello varrebbe solo per quell'episodio.
- Ogni lezione e' una regola BREVE e OPERATIVA nello stile di copione.md, verificabile su una battuta futura: "usa meno tag" non va bene, "non mettere la tag in apertura di battuta se il turno non cambia registro per intero" va bene.
- Il campo `pattern` di ogni lezione deve avere `categoria` e `dettaglio` UGUALI, valore per valore, a uno degli schemi qui sotto: non inventare una categoria o un dettaglio diverso.
- Controlla esplicitamente se la lezione CONTRADDICE copione.md o una lezione gia' attiva: se si', scrivilo in `conflitto_copione` e valuta se proporla comunque ha senso.
- Se le prove non bastano per una regola davvero verificabile, l'esito corretto e' NON proporre niente: lascia `lezioni` vuoto e spiega il perche' in `motivo_se_vuoto`. Non inventare una regola pur di rispondere qualcosa.
- Non toccare MAI regole su costi, approvazione o consenso vocale: non sono materia di una lezione di stile.

--- COPIONE.MD (regole di base, vincolanti) ---
{copione_text}
--- FINE COPIONE.MD ---

--- LEZIONI GIA' ATTIVE ---
{active_lessons_text or "(nessuna)"}
--- FINE LEZIONI GIA' ATTIVE ---

--- PROVE AGGREGATE (schemi sopra soglia, al massimo {max_examples_per_pattern} esempi ciascuno, battute troncate) ---
{prove_json}
--- FINE PROVE AGGREGATE ---
"""


def call_model(
    prompt: str,
    *,
    api_key: str,
    model: str,
    caller: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """`caller(prompt=..., model=...) -> str` e' iniettabile per i test: senza,
    chiama davvero l'API Anthropic con temperature 0 (proposta ripetibile,
    non creativa) e output strutturato secondo _PROPOSE_SCHEMA.
    """
    if caller is not None:
        text = caller(prompt=prompt, model=model)
    else:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": _PROPOSE_SCHEMA},
            },
        )
        if response.stop_reason == "refusal":
            raise LessonsError("Il modello ha rifiutato di generare la proposta.")
        text = next((b.text for b in response.content if b.type == "text"), "")

    if not text.strip():
        raise LessonsError("Il modello ha restituito una risposta vuota.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LessonsError(f"La risposta del modello non e' JSON valido: {exc}") from exc


def propose(
    *,
    prompts_dir: Path,
    copione_path: Path,
    aggregated: dict[str, Any],
    api_key: str,
    model: str,
    max_examples_per_pattern: int,
    max_example_chars: int,
    caller: Callable[..., str] | None = None,
) -> dict[str, Any]:
    if not aggregated["schemi"]:
        return {"proposte": [], "scartate": [], "motivo_se_vuoto": "nessuno schema sopra soglia: niente da proporre."}

    entries = load_db(prompts_dir)
    active_text = "\n\n".join(
        f"{e['id']}: {e.get('testo', '')}" for e in entries if e.get("stato") == "attiva"
    )
    copione_text = copione_path.read_text(encoding="utf-8") if copione_path.is_file() else ""

    prompt = build_propose_prompt(
        aggregated,
        copione_text=copione_text,
        active_lessons_text=active_text,
        max_examples_per_pattern=max_examples_per_pattern,
        max_example_chars=max_example_chars,
    )
    raw = call_model(prompt, api_key=api_key, model=model, caller=caller)

    proposte: list[dict[str, Any]] = []
    scartate: list[dict[str, Any]] = []
    for item in (raw.get("lezioni") or [])[:2]:
        problema = validate_proposal(item, aggregated["schemi"])
        if problema:
            scartate.append({"testo": item.get("testo"), "motivo": problema})
            continue
        if _duplicate_of_rejected(item, entries):
            scartate.append(
                {"testo": item.get("testo"), "motivo": "identica a una proposta gia' rifiutata in precedenza."}
            )
            continue

        schema = _find_schema(aggregated["schemi"], item["pattern"])
        entry = {
            "id": _next_id(entries),
            "stato": "proposta",
            "testo": str(item["testo"]).strip(),
            "pattern": item["pattern"],
            "conflitto_copione": item.get("conflitto_copione"),
            "prove": schema.get("esempi", []),
            "episodi": schema.get("episodi", []),
            "proposta_il": _now(),
            "modello": model,
        }
        entries.append(entry)
        proposte.append(entry)

    # Scrive SOLO se e' stata aggiunta almeno una proposta: se il modello non
    # propone niente (esito valido) o tutte le proposte sono state scartate,
    # non ha senso creare un _lezioni.json dove prima non c'era niente.
    if proposte:
        save_db(prompts_dir, entries)
    return {"proposte": proposte, "scartate": scartate, "motivo_se_vuoto": raw.get("motivo_se_vuoto")}


# ----------------------------------------------------------------------
# Approvazione — sempre e solo dell'utente
# ----------------------------------------------------------------------


def list_lessons(prompts_dir: Path) -> dict[str, Any]:
    entries = load_db(prompts_dir)
    return {
        "attive": [e for e in entries if e.get("stato") == "attiva"],
        "proposte": [e for e in entries if e.get("stato") == "proposta"],
        "rifiutate": [e for e in entries if e.get("stato") == "rifiutata"],
        "rimosse": [e for e in entries if e.get("stato") == "rimossa"],
    }


def _removal_candidates(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = [e for e in entries if e.get("stato") == "attiva"]
    acquisite = [
        e
        for e in active
        if (e.get("occorrenze_dopo_attivazione") or 0) == 0 and (e.get("episodi_dall_attivazione") or 0) >= 1
    ]
    by_age = sorted(active, key=lambda e: e.get("accettata_il", ""))[:1]
    by_evidence = sorted(active, key=lambda e: len(e.get("prove", [])))[:1]

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group, motivo in (
        (acquisite, "sembra acquisita: lo schema non si ripresenta piu' da quando e' attiva"),
        (by_age, "e' la piu' vecchia"),
        (by_evidence, "e' quella con meno prove a supporto"),
    ):
        for e in group:
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            out.append({"id": e["id"], "testo": e.get("testo"), "motivo": motivo})
    return out


def accept(
    prompts_dir: Path,
    lezioni_md_path: Path,
    lesson_id: str,
    *,
    text_override: str | None,
    remove_id: str | None,
    max_lines: int,
) -> dict[str, Any]:
    entries = load_db(prompts_dir)
    entry = next((e for e in entries if e.get("id") == lesson_id), None)
    if entry is None:
        raise LessonsError(f"Nessuna lezione con id '{lesson_id}'.")
    if entry.get("stato") != "proposta":
        raise LessonsError(f"La lezione '{lesson_id}' non e' in stato 'proposta' (e': {entry.get('stato')}).")

    testo_finale = (text_override or entry["testo"]).strip()
    if _FORBIDDEN_TOPICS.search(testo_finale):
        raise LessonsError(
            "Il testo tocca costi, approvazione o consenso vocale: una lezione non puo' allentare quelle regole."
        )

    if remove_id and not any(e.get("id") == remove_id and e.get("stato") == "attiva" for e in entries):
        raise LessonsError(f"'{remove_id}' non e' una lezione attiva: non puo' essere rimossa.")

    # Simula lo stato "dopo" per controllare il tetto PRIMA di scrivere
    # qualunque cosa su disco: se sfora, --accept deve fermarsi senza aver
    # toccato ne' il DB ne' lezioni.md.
    trial = [dict(e) for e in entries]
    next(e for e in trial if e["id"] == lesson_id).update(stato="attiva", accettata_il=_now(), testo=testo_finale)
    if remove_id:
        next(e for e in trial if e["id"] == remove_id)["stato"] = "rimossa"

    candidate_md = render_lezioni_md(trial)
    if _line_count(candidate_md) > max_lines and not remove_id:
        raise NeedsRemoval(_removal_candidates(entries))

    accettata_il = _now()
    entry["stato"] = "attiva"
    entry["accettata_il"] = accettata_il
    entry["testo"] = testo_finale
    if text_override:
        entry["testo_riscritto_da_utente"] = True
    if remove_id:
        removed = next(e for e in entries if e.get("id") == remove_id)
        removed["stato"] = "rimossa"
        removed["rimossa_il"] = accettata_il
        removed["motivo_rimozione"] = f"tolta per far posto a {lesson_id}"

    lezioni_md_path.write_text(render_lezioni_md(entries), encoding="utf-8")
    save_db(prompts_dir, entries)
    return {
        "id": lesson_id,
        "testo": testo_finale,
        "lezioni_md": str(lezioni_md_path),
        "rimossa": remove_id,
    }


def reject(prompts_dir: Path, lesson_id: str, *, reason: str | None) -> dict[str, Any]:
    entries = load_db(prompts_dir)
    entry = next((e for e in entries if e.get("id") == lesson_id), None)
    if entry is None:
        raise LessonsError(f"Nessuna lezione con id '{lesson_id}'.")
    if entry.get("stato") != "proposta":
        raise LessonsError(f"La lezione '{lesson_id}' non e' in stato 'proposta' (e': {entry.get('stato')}).")
    entry["stato"] = "rifiutata"
    entry["rifiutata_il"] = _now()
    entry["motivo_rifiuto"] = reason or ""
    save_db(prompts_dir, entries)
    return {"id": lesson_id, "stato": "rifiutata", "motivo_rifiuto": entry["motivo_rifiuto"]}


def review(
    prompts_dir: Path, signals_by_episode: list[dict[str, Any]], *, after_episodes: int
) -> dict[str, Any]:
    """Ricontrolla, con lo STESSO aggregatore deterministico usato per
    --evidence, se lo schema che una lezione attiva voleva correggere si
    ripresenta ancora negli episodi successivi alla sua attivazione.
    """
    entries = load_db(prompts_dir)
    active = [e for e in entries if e.get("stato") == "attiva"]
    segnalate: list[dict[str, Any]] = []
    controllate = 0

    for e in active:
        accettata_il = str(e.get("accettata_il", ""))
        successivi = [s for s in signals_by_episode if str(s.get("generato_il", "")) > accettata_il]
        if len(successivi) < after_episodes:
            continue
        controllate += 1
        aggregated = aggregate_evidence(successivi, min_occurrences=1, min_episodes=1)
        match = _find_schema(aggregated["schemi"], e.get("pattern") or {})
        occorrenze = match.get("occorrenze", 0)

        e["episodi_dall_attivazione"] = len(successivi)
        e["occorrenze_dopo_attivazione"] = occorrenze
        e["ultima_review"] = _now()
        if occorrenze > 0:
            segnalate.append(
                {
                    "id": e["id"],
                    "testo": e.get("testo"),
                    "occorrenze_dopo": occorrenze,
                    "episodi_controllati": len(successivi),
                }
            )

    # Scrive SOLO se ha davvero aggiornato qualcosa: senza lezioni attive
    # abbastanza vecchie da poter essere controllate, --review non deve
    # lasciare un _lezioni.json vuoto dove prima non c'era niente.
    if controllate:
        save_db(prompts_dir, entries)
    return {"lezioni_non_funzionanti": segnalate, "lezioni_controllate_tutte": controllate}


_TREND_WARNING = (
    "Questi indicatori dipendono anche da quanto attentamente hai ascoltato e "
    "da quanto era difficile il documento di ogni episodio: NON sono una "
    "misura di qualita', solo un andamento nel tempo."
)


def trend(signals_by_episode: list[dict[str, Any]]) -> dict[str, Any]:
    episodi = [
        {"episodio": _episode_id(s), "generato_il": s.get("generato_il"), **(s.get("indicatori") or {})}
        for s in sorted(signals_by_episode, key=lambda s: str(s.get("generato_il", "")))
    ]
    return {"avvertenza": _TREND_WARNING, "episodi": episodi}


__all__ = [
    "DB_FILENAME",
    "MD_FILENAME",
    "PATTERN_CATEGORIES",
    "LONG_TURN_CHARS",
    "LessonsError",
    "NeedsRemoval",
    "load_db",
    "save_db",
    "active_lessons_summary",
    "render_lezioni_md",
    "aggregate_evidence",
    "validate_proposal",
    "estimate_propose_cost",
    "build_propose_prompt",
    "call_model",
    "propose",
    "list_lessons",
    "accept",
    "reject",
    "review",
    "trend",
]
