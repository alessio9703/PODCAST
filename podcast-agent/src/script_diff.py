"""Confronto fra due versioni del copione, battuta per battuta.

Il pannello Confronto mostra ogni battuta come invariata, modificata, aggiunta
o rimossa, con il testo prima e dopo, e dentro una battuta modificata i pezzi
cambiati. Il confronto si fa in due livelli:

  1. **battute**: allineamento delle due liste con difflib sulle coppie
     (speaker, testo). Inserire o togliere una battuta non sposta le altre:
     restano "invariate" anche se il loro numero cambia;
  2. **dentro un blocco sostituito**: le battute si accoppiano in ordine, e
     due battute contano come "la stessa, modificata" solo se hanno lo stesso
     speaker e il testo e' abbastanza simile (SIMILARITY_THRESHOLD). Sotto
     soglia sono due fatti diversi: una rimossa e una aggiunta. E' la stessa
     idea di src/episode_signals.align_turns(), con una soglia propria
     perche' qui il fine e' mostrare, non misurare.

Per le battute modificate c'e' anche il confronto parola per parola
(`segmenti`), che e' quello che il wireframe evidenzia dentro la battuta.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

#: Sotto questa somiglianza (SequenceMatcher.ratio, 0-1) due battute dello
#: stesso speaker non sono "la stessa battuta riscritta" ma una tolta e una
#: aggiunta. Basso apposta: una battuta allungata del doppio resta la stessa.
SIMILARITY_THRESHOLD = 0.35

#: Parole, spazi e punteggiatura come pezzi separati: "te?" -> "i dati?" deve
#: risultare "te" -> "i dati" con il "?" invariato, non due parole diverse.
_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def _word_segments(before: str, after: str) -> list[dict[str, str]]:
    """[{"tipo": "uguale"|"aggiunto"|"rimosso", "testo"}] parola per parola."""
    a = _TOKEN.findall(before)
    b = _TOKEN.findall(after)
    out: list[dict[str, str]] = []

    def push(kind: str, text: str) -> None:
        if not text:
            return
        if out and out[-1]["tipo"] == kind:
            out[-1]["testo"] += text
        else:
            out.append({"tipo": kind, "testo": text})

    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            push("uguale", "".join(a[i1:i2]))
        else:
            push("rimosso", "".join(a[i1:i2]))
            push("aggiunto", "".join(b[j1:j2]))
    return out


def _similar(x: dict[str, str], y: dict[str, str]) -> bool:
    if x.get("speaker") != y.get("speaker"):
        return False
    ratio = difflib.SequenceMatcher(a=x.get("testo", ""), b=y.get("testo", ""), autojunk=False).ratio()
    return ratio >= SIMILARITY_THRESHOLD


def _row(state: str, before: dict | None, after: dict | None, n_before: int | None, n_after: int | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "stato": state,
        "n_prima": n_before,
        "n_dopo": n_after,
        "speaker": (after or before or {}).get("speaker"),
        "testo_prima": before.get("testo") if before else None,
        "testo_dopo": after.get("testo") if after else None,
    }
    if state == "modificata":
        row["speaker_prima"] = before.get("speaker") if before else None
        row["segmenti"] = _word_segments(before["testo"], after["testo"])  # type: ignore[index]
    return row


def diff_scripts(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Confronta due copioni ({"titolo", "turni"}). Ritorna righe e conteggi."""
    old = list(before.get("turni") or [])
    new = list(after.get("turni") or [])
    keys_old = [(t.get("speaker"), t.get("testo")) for t in old]
    keys_new = [(t.get("speaker"), t.get("testo")) for t in new]

    rows: list[dict[str, Any]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=keys_old, b=keys_new, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                rows.append(_row("invariata", old[i1 + k], new[j1 + k], i1 + k + 1, j1 + k + 1))
        elif op == "delete":
            for k in range(i1, i2):
                rows.append(_row("rimossa", old[k], None, k + 1, None))
        elif op == "insert":
            for k in range(j1, j2):
                rows.append(_row("aggiunta", None, new[k], None, k + 1))
        else:  # replace: accoppiamento in ordine, solo se simili
            i, j = i1, j1
            while i < i2 or j < j2:
                if i < i2 and j < j2 and _similar(old[i], new[j]):
                    rows.append(_row("modificata", old[i], new[j], i + 1, j + 1))
                    i += 1
                    j += 1
                elif i < i2 and (j >= j2 or not any(_similar(old[i], new[x]) for x in range(j, j2))):
                    rows.append(_row("rimossa", old[i], None, i + 1, None))
                    i += 1
                else:
                    rows.append(_row("aggiunta", None, new[j], None, j + 1))
                    j += 1

    counts = {state: sum(1 for r in rows if r["stato"] == state) for state in ("invariata", "modificata", "aggiunta", "rimossa")}
    return {
        "titolo_prima": before.get("titolo"),
        "titolo_dopo": after.get("titolo"),
        "titolo_cambiato": before.get("titolo") != after.get("titolo"),
        "conteggi": counts,
        "righe": rows,
    }


def only_changes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Le sole modifiche, con le battute invariate raggruppate in un segnaposto.

    Il segnaposto {"stato": "invariate", "conteggio", "da_n", "a_n"} e' la riga
    "2 battute invariate · mostra" del wireframe; da_n/a_n sono i numeri NELLA
    VERSIONE DOPO, per poterle espandere.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["stato"] != "invariata":
            out.append(row)
            continue
        if out and out[-1]["stato"] == "invariate":
            out[-1]["conteggio"] += 1
            out[-1]["a_n"] = row["n_dopo"]
        else:
            out.append({"stato": "invariate", "conteggio": 1, "da_n": row["n_dopo"], "a_n": row["n_dopo"]})
    return out


__all__ = ["SIMILARITY_THRESHOLD", "diff_scripts", "only_changes"]
