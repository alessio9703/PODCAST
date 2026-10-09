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
from typing import Any, Mapping

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


# Azioni che spendono e vanno legate a una stima: azione -> (azione della stima,
# parametri della stima dai parametri dell'azione, impronta).
STIME = {
    "produci": ("stima", lambda p: {"run": p["run"]}, impronta_stima),
    "genera_retake": ("stima_retake", lambda p: {k: p[k] for k in ("run", "battuta", "take", "indicazioni") if k in p},
                      impronta_retake),
}
