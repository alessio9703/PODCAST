"""Impronta della stima: lega la produzione (e i retake) alla stima che l'utente ha visto (A4 MEDIA-1).

`GET /api/episodi/{run}/stima` aggiunge `impronta`; `POST .../produzione` la deve rimandare.
Subito prima di accodare il controller rifa' la stima: se l'impronta e' diversa (un'altra
versione approvata, un costo diverso, una voce da clonare in piu') la produzione non parte
e la UI mostra la stima nuova. Non e' un segreto ne' un'autorizzazione: e' un controllo
di coerenza fra cio' che si e' confermato e cio' che si spende.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

CAMPI = ("approved_version", "cost_usd", "cost_eur", "clone_cost_total_usd", "turns", "turns_to_generate",
         "chars_to_generate", "voices_to_clone", "consent_missing")


def impronta_stima(dati: Mapping[str, Any]) -> str:
    scelti = {campo: dati.get(campo) for campo in CAMPI}
    for campo in ("voices_to_clone", "consent_missing"):
        valore = scelti.get(campo)
        if isinstance(valore, list):
            scelti[campo] = sorted(json.dumps(v, sort_keys=True, ensure_ascii=False) for v in valore)
    testo = json.dumps(scelti, sort_keys=True, ensure_ascii=False, default=str)
    return "stima:" + hashlib.sha256(testo.encode("utf-8")).hexdigest()[:32]


CAMPI_RETAKE = ("turn", "speaker", "takes", "chars", "indicazioni", "cost_usd", "cost_eur")


def impronta_retake(dati: Mapping[str, Any]) -> str:
    """Come `impronta_stima`, per la stima di un retake (`retake.py --dry-run`)."""
    scelti = {campo: dati.get(campo) for campo in CAMPI_RETAKE}
    testo = json.dumps(scelti, sort_keys=True, ensure_ascii=False, default=str)
    return "retake:" + hashlib.sha256(testo.encode("utf-8")).hexdigest()[:32]


def impronta_lezioni(dati: Mapping[str, Any]) -> str:
    """Per la stima delle proposte di lezioni (`lessons.py --propose --dry-run`)."""
    stima = dati.get("stima") if isinstance(dati.get("stima"), Mapping) else {}
    evidenza = dati.get("evidenza") if isinstance(dati.get("evidenza"), Mapping) else {}
    scelti = {"stima": {k: stima.get(k) for k in ("input_tokens_stimati", "output_tokens_stimati_max", "cost_usd")},
              "schemi": len(evidenza.get("schemi") or []), "episodi": evidenza.get("episodi_analizzati")}
    testo = json.dumps(scelti, sort_keys=True, ensure_ascii=False, default=str)
    return "lezioni:" + hashlib.sha256(testo.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Stima:
    """Un'azione che spende e va legata alla stima vista: quale stima rifare, con quali
    parametri, come calcolarne l'impronta, dove sta il costo e se senza costo si ferma."""

    azione: str
    parametri: Callable[[Mapping[str, Any]], dict[str, Any]]
    impronta: Callable[[Mapping[str, Any]], str]
    costo: Callable[[Mapping[str, Any]], tuple[Any, Any]] = lambda d: (d.get("cost_usd"), d.get("cost_eur"))
    costo_obbligatorio: bool = True


STIME = {
    "produci": Stima("stima", lambda p: {"run": p["run"]}, impronta_stima),
    "genera_retake": Stima("stima_retake", lambda p: {k: p[k] for k in ("run", "battuta", "take", "indicazioni") if k in p},
                           impronta_retake),
    # Il costo delle proposte c'e' solo se config.yaml ha i prezzi del modello: senza, la
    # pagina dice che non e' noto (come per le clonazioni) e la proposta si puo' confermare.
    "lezioni_proponi": Stima("lezioni_proponi_stima", lambda p: {}, impronta_lezioni,
                             costo=lambda d: ((d.get("stima") or {}).get("cost_usd"), None), costo_obbligatorio=False),
}
