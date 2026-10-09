"""Dagli esiti dell'agente all'unico formato JSON verso il browser.

    {"ok": true,  "dati": {...}}
    {"ok": false, "esito": "correggibile|servizio_esterno|decisione|timeout|controller",
     "error_type": "...", "messaggio": "...", "details": {...}, "riprovabile": bool,
     "gettone": "...", "scade_il": "..."}          # gettone solo per esito 3

exit 0 -> 200 · 1 -> 422 (409 per i conflitti) · errori dell'adattatore -> 500 ·
2 -> 502 · 3 -> 409 con gettone · timeout -> 504. Mai percorsi del server, chiavi o
voice_id verso il browser (`pulisci`).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.agente.esecutore import Risultato

log = logging.getLogger("podcast_web.esiti")

# §3 del contratto: elenco completo con l'exit code (fonte: src/error_catalog.py dell'agente).
ERROR_TYPES: dict[str, int] = {
    "InvalidInput": 1, "NotFound": 1, "InternalError": 1, "ExternalServiceError": 2,
    "UserDecisionRequired": 3, "Interrupted": 1, "InvalidArguments": 1, "NotAllowedInWebMode": 1,
    "ByRequired": 1, "PathNotAllowed": 1, "LockTimeout": 1, "RunBusy": 1, "ConfigError": 1,
    "DocumentError": 1, "DocumentNotExtractable": 1, "UnsupportedDocument": 1, "DocumentTooLarge": 1,
    "EpisodeConfigError": 1, "SpokenNameIncomplete": 3, "AmbiguousName": 3, "ScriptInvalid": 1,
    "ScriptHasErrors": 1, "VersionConflict": 1, "VersionRequired": 1, "NothingToChange": 1,
    "TurnNotFound": 1, "ScriptMissing": 1, "ScriptSessionError": 1, "ScriptWriterError": 1,
    "ModelAuthError": 2, "ModelCreditExhausted": 2, "ModelRateLimited": 1, "ModelOverloaded": 1,
    "ModelUnreachable": 1, "VoiceLibraryError": 1, "VoiceMetaError": 1, "ConsentRequired": 3,
    "CloneOutcomeUnknown": 3, "RecordingChanged": 3, "RevokedMeanwhile": 3, "RevocationIncomplete": 1,
    "RegistryCorrupt": 1, "ApprovalRequired": 1, "ProductionInterrupted": 1, "AssemblyError": 1,
    "MissingSpeakerVoice": 1, "SegmentCacheError": 1, "VoiceProviderError": 2, "ProviderAuthError": 2,
    "ProviderQuotaError": 2, "ProviderRateLimitError": 2, "ProviderTimeoutError": 2,
    "ProviderAudioQualityError": 2, "RetakeError": 1, "SegmentsMissing": 1, "UnknownDirection": 1,
    "DirectionNeedsTextEdit": 1, "ConflictingDirections": 1, "TooManyTakes": 1, "WaveformError": 1,
    "EpisodeNotProduced": 1, "TimelineError": 1, "SignalsError": 1, "MusicLibraryError": 1,
    "MusicMixerError": 1, "MusicChanged": 3, "MusicMetaError": 1, "MusicChoiceError": 1,
    "FfmpegNotFoundError": 1, "LessonsError": 1, "LessonRemovalRequired": 3,
}
# Ripiego per un nome fuori elenco: l'errore generico del suo exit code (§3).
GENERICO_PER_EXIT = {1: "InternalError", 2: "ExternalServiceError", 3: "UserDecisionRequired"}

ERRORI_ADATTATORE = frozenset({"InvalidArguments", "NotAllowedInWebMode", "ByRequired", "InternalError"})
CONFLITTI = frozenset({"RunBusy", "VersionConflict", "LockTimeout", "ApprovalRequired"})
RIPROVABILI = frozenset({"RunBusy", "LockTimeout", "ModelRateLimited", "ModelOverloaded", "ModelUnreachable"})

ESITO_PER_EXIT = {1: "correggibile", 2: "servizio_esterno", 3: "decisione"}
HTTP_PER_EXIT = {0: 200, 1: 422, 2: 502, 3: 409}

MESSAGGIO_ADATTATORE = "Errore interno del controller. L'errore e' stato registrato."
MESSAGGIO_ESTERNO = "Errore di un servizio esterno. Non riproviamo da soli: serve un intervento sull'account o sulla configurazione."

# Chiavi con percorsi del server o dati interni (§1 "Campi da mostrare e campi da non mostrare").
CHIAVI_VIETATE = frozenset({
    "run_dir", "session_file", "script_file", "episodio_file", "musiche_file", "musiche_usate_file",
    "output", "path", "percorso", "voices_dir", "music_dir", "timings_file", "segnali_file",
    "voci_usate", "text_file", "audio_file", "events_file", "file_ui", "preview", "previews",
    "preview_ui", "preview_with_music", "previews_with_music", "voice_id", "voice_ids", "cache_reason",
    "pid", "ffmpeg_path", "lezioni_md",
})
_PERCORSO_ASSOLUTO = re.compile(r"^(?:/|~/)(?:[^/\s]+/)*[^/\s]*$")


class Pulitore:
    """Toglie le chiavi vietate e, come rete di sicurezza, ogni stringa che
    contiene un percorso del server (cartella dell'agente, home, percorso assoluto
    o relativo all'archivio `output/...`)."""

    def __init__(self, agent_dir: Path, home: str | None, archivio: str = "output") -> None:
        self._sporchi = [s for s in {str(agent_dir), str(agent_dir.resolve()), home} if s and len(s) > 1]
        self._relativo = re.compile(rf"^{re.escape(archivio)}/[^\s]+$")

    def stringa_sospetta(self, testo: str) -> bool:
        if any(s in testo for s in self._sporchi):
            return True
        return bool(_PERCORSO_ASSOLUTO.match(testo) or self._relativo.match(testo))

    def pulisci(self, valore: Any, _chiave: str = "") -> Any:
        if isinstance(valore, dict):
            pulito = {}
            for k, v in valore.items():
                if k in CHIAVI_VIETATE:
                    continue
                v2 = self.pulisci(v, k)
                if v2 is _TOLTO:
                    continue
                pulito[k] = v2
            return pulito
        if isinstance(valore, list):
            return [v for v in (self.pulisci(x, _chiave) for x in valore) if v is not _TOLTO]
        if isinstance(valore, str) and self.stringa_sospetta(valore):
            log.warning("tolto un valore con un percorso del server (chiave %r)", _chiave)
            return _TOLTO
        return valore


_TOLTO = object()


def nome_errore(risultato: Risultato) -> str:
    dati = risultato.json or {}
    exit_code = risultato.exit_code if risultato.exit_code in (1, 2, 3) else 1
    nome = dati.get("error_type")
    if isinstance(nome, str) and ERROR_TYPES.get(nome) == exit_code:
        return nome
    if isinstance(nome, str) and nome in ERROR_TYPES:
        log.error("%s: %s con exit %s diverso dal contratto", risultato.azione, nome, exit_code)
    elif nome is not None:
        log.error("%s: error_type fuori elenco %r", risultato.azione, nome)
    return GENERICO_PER_EXIT[exit_code]


def valuta(risultato: Risultato, pulitore: Pulitore) -> tuple[int, dict[str, Any]]:
    """(stato HTTP, corpo) per un risultato dell'agente. Il gettone degli esiti 3
    lo aggiunge chi chiama (`servizi/azioni.py`), che conosce utente e richiesta."""
    if risultato.timeout:
        return 504, {"ok": False, "esito": "timeout", "error_type": "Timeout",
                     "messaggio": "L'operazione ha impiegato troppo tempo ed e' stata fermata.", "riprovabile": True}
    dati = risultato.json
    if dati is None or risultato.exit_code not in (0, 1, 2, 3) or (risultato.exit_code == 0) != (dati.get("ok") is True):
        log.error("%s: esito incoerente (exit %s, ok=%s)", risultato.azione, risultato.exit_code,
                  None if dati is None else dati.get("ok"))
        return 500, {"ok": False, "esito": "controller", "error_type": "ErroreInterno",
                     "messaggio": MESSAGGIO_ADATTATORE, "riprovabile": False}
    if risultato.exit_code == 0:
        pulito = pulitore.pulisci(dati)
        pulito.pop("ok", None)
        return 200, {"ok": True, "dati": pulito}

    nome = nome_errore(risultato)
    exit_code = risultato.exit_code
    if nome in ERRORI_ADATTATORE:
        log.error("%s: errore dell'adattatore %s: %s · details=%s · stderr=%s", risultato.azione, nome,
                  dati.get("error"), dati.get("details"), risultato.stderr[-2000:])
        return 500, {"ok": False, "esito": "controller", "error_type": nome,
                     "messaggio": MESSAGGIO_ADATTATORE, "riprovabile": False}
    messaggio = dati.get("error") if isinstance(dati.get("error"), str) else None
    corpo: dict[str, Any] = {
        "ok": False,
        "esito": ESITO_PER_EXIT[exit_code],
        "error_type": nome,
        "messaggio": pulitore.pulisci(messaggio) if messaggio else (MESSAGGIO_ESTERNO if exit_code == 2 else nome),
        "riprovabile": exit_code == 1 and nome in RIPROVABILI,
    }
    if corpo["messaggio"] is _TOLTO:
        corpo["messaggio"] = nome
    details = dati.get("details")
    if isinstance(details, dict) and details:
        corpo["details"] = pulitore.pulisci(details)
    stato = HTTP_PER_EXIT[exit_code]
    if exit_code == 1 and nome in CONFLITTI:
        stato = 409
    return stato, corpo
