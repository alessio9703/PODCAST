"""Il copione in forma strutturata, per un'interfaccia che deve mostrarlo.

`conversazione.json` resta com'e' ({"titolo", "turni": [{"speaker", "testo"}]}):
e' cio' che si approva e si sintetizza, e il suo sha256 e' il gate. Qui se ne
costruisce una VISTA con quello che la schermata Copione mostra e che dal
solo JSON non si ricava:

  - il ruolo di chi parla (presentatore, intervistatore, ospite, voce) e il
    nome con cui viene presentato;
  - il testo diviso in parti `testo` / `tag`, con le posizioni (offset in
    caratteri nel testo originale), per evidenziare le inline tag senza
    rifare il parsing lato UI con una regex che potrebbe divergere da quella
    usata dalla stima e dalla validazione (INLINE_TAG).

E' una vista: si ricalcola ogni volta e non si salva.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .script_writer import INLINE_TAG, spoken_text


def split_parts(text: str) -> list[dict[str, Any]]:
    """[{"tipo": "testo"|"tag", "testo", "inizio", "fine"}], senza buchi."""
    parts: list[dict[str, Any]] = []
    cursor = 0
    for match in INLINE_TAG.finditer(text):
        if match.start() > cursor:
            parts.append({"tipo": "testo", "testo": text[cursor:match.start()], "inizio": cursor, "fine": match.start()})
        parts.append({"tipo": "tag", "testo": match.group(0), "inizio": match.start(), "fine": match.end()})
        cursor = match.end()
    if cursor < len(text):
        parts.append({"tipo": "testo", "testo": text[cursor:], "inizio": cursor, "fine": len(text)})
    return parts


def cast_view(
    *,
    script_format: str | None,
    interviewer: str | None,
    presenter: str | None,
    participants: list[dict[str, str]] | None,
    speakers: list[str],
) -> dict[str, dict[str, Any]]:
    """speaker -> {"ruolo", "presentatore": bool, "nome_pronunciato", "descrizione"}."""
    by_speaker = {p.get("speaker"): p for p in (participants or [])}
    out: dict[str, dict[str, Any]] = {}
    for speaker in speakers:
        if speaker == presenter:
            role = "presentatore"
        elif script_format == "intervista":
            role = "intervistatore" if speaker == interviewer else "ospite"
        else:
            role = "voce"
        person = by_speaker.get(speaker) or {}
        out[speaker] = {
            "ruolo": role,
            "presentatore": speaker == presenter,
            "nome_pronunciato": person.get("nome"),
            "descrizione": person.get("ruolo"),
        }
    return out


def structured_turns(turni: list[dict[str, str]], cast: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for numero, turn in enumerate(turni, start=1):
        speaker = turn.get("speaker", "")
        testo = turn.get("testo", "")
        info = cast.get(speaker) or {}
        parts = split_parts(testo)
        out.append(
            {
                "n": numero,
                "speaker": speaker,
                "ruolo": info.get("ruolo"),
                "presentatore": bool(info.get("presentatore")),
                "nome_pronunciato": info.get("nome_pronunciato"),
                "testo": testo,
                "testo_parlato": spoken_text(testo),
                "tag": [p["testo"] for p in parts if p["tipo"] == "tag"],
                "parti": parts,
            }
        )
    return out


def session_cast(session: Any, speakers: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """cast_view() a partire da una ScriptSession."""
    return cast_view(
        script_format=getattr(session, "script_format", None),
        interviewer=getattr(session, "interviewer", None),
        presenter=getattr(session, "presenter", None),
        participants=getattr(session, "participants", None),
        speakers=list(speakers if speakers is not None else session.speakers),
    )


def _add_presenter_name(cast: dict[str, dict[str, Any]], session: Any, run_dir: Path) -> None:
    """Il nome pronunciato del presentatore sta in episodio.json, non nella
    sessione (che conosce solo i presentati): se la run ce l'ha, si aggiunge."""
    presenter = getattr(session, "presenter", None)
    if not presenter or presenter not in cast or cast[presenter].get("nome_pronunciato"):
        return
    try:
        from .episode_config import EpisodeConfig

        episode = EpisodeConfig.load(run_dir)
    except Exception:  # noqa: BLE001 - e' solo un'aggiunta alla vista
        return
    if episode and episode.presenter == presenter:
        cast[presenter]["nome_pronunciato"] = (episode.ruoli.get("presentatore") or {}).get("nome_pronunciato")


def script_payload(
    script: Any,
    session: Any,
    *,
    script_file: Any,
    warnings: list[str] | None = None,
    words_per_minute: int = 150,
) -> dict[str, Any]:
    """Il JSON comune ai tool che salvano o mostrano un copione.

    E' l'unione delle chiavi che script_save.py e script_write.py
    restituivano gia' (nessuna rinominata o tolta: il subagent le legge) con
    quelle nuove per l'interfaccia: `turni_strutturati`, `cast`, l'elenco
    breve delle versioni e lo stato dell'approvazione.
    """
    cast = session_cast(session, session.speakers or script.speakers)
    _add_presenter_name(cast, session, Path(str(script_file)).parent)
    approval = session.approval_view()
    spoken_words = script.spoken_words
    return {
        "titolo": script.titolo,
        "turni": script.turni,
        "speakers": script.speakers,
        "format": session.script_format,
        "interviewer": session.interviewer,
        "presenter": session.presenter,
        "participants": session.participants,
        "turn_count": len(script.turni),
        "chars": script.total_chars,
        "words": script.total_words,
        "spoken_words": spoken_words,
        # Stima grezza per l'intestazione "[n] battute · ~[mm] min": parole
        # pronunciate / parole al minuto. La stima vera (intervallo, costo)
        # e' quella di tools/estimate.py.
        "duration_hint_seconds": round(spoken_words / max(1, words_per_minute) * 60),
        "version": session.version_count,
        "revision_count": session.revision_count,
        "warnings": list(warnings or []),
        "errors": session.current_errors,
        **approval,
        "script_file": str(script_file),
        "session_file": str(session.path) if session.path else None,
        "rendered": script.render(),
        "cast": cast,
        "turni_strutturati": structured_turns(script.turni, cast),
    }


__all__ = ["cast_view", "script_payload", "session_cast", "split_parts", "structured_turns"]
