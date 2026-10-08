"""L'elenco unico degli `error_type` che un comando puo' restituire (fase C8, N28).

L'adattatore web decide cosa mostrare da `error_type`: questo elenco e' il
contratto. `tools/_common.run_tool` garantisce che nel JSON finisca SOLO un nome
di questo elenco: un'eccezione imprevista non arriva con il nome di una classe
Python (`ValueError`, `KeyError`, ...), ma come `InvalidInput`, `NotFound` o
`InternalError`. `docs/CONTRATTO_INTERFACCIA.md` §3 riporta la stessa tabella, e
`tests/test_web_integration.py` (`test_c8_error_catalog`) controlla che codice,
elenco e contratto coincidano, exit code compresi.

Convenzione dei nomi:
  - CamelCase, sempre;
  - senza suffisso: una situazione precisa (`ConsentRequired`, `AmbiguousName`,
    `VersionConflict`);
  - con il suffisso `Error`: l'errore generico di un componente o di un servizio
    (`DocumentError`, `MusicMetaError`, `ProviderAuthError`), con il testo che dice
    cosa fare e, dove serve, il `campo` in `details`.

Exit code: 1 errore correggibile, 2 errore di un servizio esterno a pagamento
(provider vocale o modello: niente "Riprova" automatico), 3 serve una scelta
dell'utente. 0 non ha `error_type`.
"""

from __future__ import annotations

#: nome -> (exit code, quando, campi di `details`)
CATALOG: dict[str, tuple[int, str, str]] = {
    # --- generali -------------------------------------------------------
    "InvalidInput": (1, "Valore non valido passato al comando (il testo dice quale e perché)", "—"),
    "NotFound": (1, "Un file o una cartella richiesti non esistono (run, copione, documento…)", "—"),
    "InternalError": (1, "Errore imprevisto: da registrare nei log e segnalare", "—"),
    "Interrupted": (1, "Comando fermato da SIGTERM o Ctrl-C; nessun file lasciato a metà", "—"),
    "InvalidArguments": (1, "Argomenti non validi per il comando (errore dell'adattatore)", "`messaggio`"),
    "NotAllowedInWebMode": (1, "Argomento o comando riservato all'uso locale, in modalità web (errore dell'adattatore)", "`argomenti`, `comando`"),
    "ByRequired": (1, "Manca `--by` (chi fa l'azione) dove serve una firma (errore dell'adattatore)", "`campo: \"by\"`"),
    "PathNotAllowed": (1, "Un percorso esce dalla cartella prevista", "`campo`"),
    "LockTimeout": (1, "Un file condiviso è rimasto occupato troppo a lungo: si può riprovare", "`file`, `timeout_seconds`"),
    "RunBusy": (1, "Sulla run è già in corso un'operazione lunga", "`in_corso`"),
    "ConfigError": (1, "`config.yaml` non valido o incompleto (errore dell'amministratore)", "—"),
    # --- documenti (passo 1) ------------------------------------------------
    "DocumentError": (1, "Documento non leggibile o fuori dalla libreria", "—"),
    "DocumentNotExtractable": (1, "Documento senza testo ricavabile", "`motivo`, `file`"),
    "UnsupportedDocument": (1, "Estensione del documento non gestita", "`estensione`, `ammesse`"),
    "DocumentTooLarge": (1, "Il documento supera un limite di `documents:`", "`limite`, `massimo`, `valore`, `file`"),
    # --- episodio e copione (passi 2-4) -------------------------------------
    "EpisodeConfigError": (1, "Scelta non valida nei passi 1–2", "`campo` (+ `ruolo`, `ammessi`…)"),
    "SpokenNameIncomplete": (3, "Nome da pronunciare senza cognome: si conferma esplicitamente", "`campo`, `ruolo` o `voce`, `nome`"),
    "AmbiguousName": (3, "Un nome corrisponde a più elementi: non si indovina", "`query`, `candidates` (+ `campo`, `category`, `ruolo`)"),
    "ScriptInvalid": (1, "Il copione (modificato o salvato) non sarebbe valido: niente salvato", "`errors`"),
    "ScriptHasErrors": (1, "Approvare una versione con errori bloccanti", "`version`, `errors`"),
    "VersionConflict": (1, "Il copione è cambiato rispetto alla versione che l'utente guardava", "`expected_version`, `current_version`"),
    "VersionRequired": (1, "`script_approve.py` senza `--version`", "`current_version`"),
    "NothingToChange": (1, "La modifica lascerebbe tutto com'è", "`turn` o `version`"),
    "TurnNotFound": (1, "Numero di battuta inesistente", "`turn`, `turn_count`"),
    "ScriptMissing": (1, "Stima o produzione di una run senza copione", "—"),
    "ScriptSessionError": (1, "Sessione del copione mancante o illeggibile", "—"),
    "ScriptWriterError": (1, "Scrittura del copione non riuscita (rifiuto del modello, risposta non valida…)", "—"),
    "ModelAuthError": (2, "Chiave del modello non valida o senza permessi", "`status`"),
    "ModelCreditExhausted": (2, "Credito del modello esaurito", "`status`"),
    "ModelRateLimited": (1, "Troppe richieste al modello: si riprova dopo", "`status`, `retry_after_seconds`"),
    "ModelOverloaded": (1, "Modello sovraccarico o in errore (5xx)", "`status`, `retry_after_seconds`"),
    "ModelUnreachable": (1, "Modello non raggiungibile (rete, timeout)", "`status`, `retry_after_seconds`"),
    # --- voci e consenso (passo 5) ------------------------------------------
    "VoiceLibraryError": (1, "Voce inesistente, doppioni, campione non utilizzabile", "—"),
    "VoiceMetaError": (1, "Metadati di una voce illeggibili", "`file` (+ `campo`)"),
    "ConsentRequired": (3, "Una voce da usare non ha il consenso valido: niente clonato", "`voci` (+ `done`, `total`, `missing`)"),
    "CloneOutcomeUnknown": (3, "Una clonazione precedente non si sa come sia finita: non si ritenta da soli", "`speaker`, `dal`, `provider`"),
    "RecordingChanged": (3, "La registrazione non è quella che l'utente ha visto: consenso non registrato", "`voce`, `atteso`, `attuale`"),
    "RevokedMeanwhile": (3, "La voce è stata revocata dopo lo stato mostrato: vince la revoca", "`voce`"),
    "RevocationIncomplete": (1, "Una revoca non è riuscita per tutto: si è revocato il possibile", "`revocati`, `non_revocati`, `revoche_registro`"),
    "RegistryCorrupt": (1, "Revoche o storico del registro delle voci non ben formati: ci si ferma", "`file`, `campo`"),
    # --- produzione (passo 6) -----------------------------------------------
    "ApprovalRequired": (1, "Copione non approvato o diverso da quello approvato (anche a produzione in corso)", "`approval_problem` (+ `done`, `total`, `missing`)"),
    "ProductionInterrupted": (1, "Produzione fermata fra una battuta e l'altra: si riprende rilanciando", "`done`, `total`, `missing`"),
    "AssemblyError": (1, "Sintesi o montaggio non riusciti; le battute pronte restano", "—"),
    "MissingSpeakerVoice": (1, "Una voce del copione non ha un voice_id", "—"),
    "SegmentCacheError": (1, "Segmenti o loro indice illeggibili", "—"),
    "VoiceProviderError": (2, "Errore del provider vocale", "—"),
    "ProviderAuthError": (2, "Chiave del provider vocale non valida", "—"),
    "ProviderQuotaError": (2, "Credito del provider vocale esaurito", "—"),
    "ProviderRateLimitError": (2, "Troppe richieste al provider vocale", "—"),
    "ProviderTimeoutError": (2, "Il provider vocale non risponde in tempo", "—"),
    "ProviderAudioQualityError": (2, "Il provider rifiuta la registrazione di riferimento", "—"),
    # --- episodio e correzioni (passo 7) -------------------------------------
    "RetakeError": (1, "Retake non possibile (turno, take, file)", "—"),
    "SegmentsMissing": (1, "Rimontaggio senza tutti i segmenti: serve `produce.py`", "`missing`"),
    "UnknownDirection": (1, "Indicazione di resa non prevista in `config.yaml`", "`indicazione`, `ammesse`"),
    "DirectionNeedsTextEdit": (1, "\"Pronuncia diversa\": va corretto il testo", "`indicazione`"),
    "ConflictingDirections": (1, "Due indicazioni opposte insieme", "`indicazioni`"),
    "TooManyTakes": (1, "Più take di `retake.max_takes`", "`massimo`, `richiesti`"),
    "WaveformError": (1, "Numero di punti non ammesso, file non decodificabile", "`campo`, `ammessi`"),
    "EpisodeNotProduced": (1, "Operazione su un episodio senza file finale", "—"),
    "TimelineError": (1, "Tempi delle battute mancanti o di un copione diverso", "`code`"),
    "SignalsError": (1, "Segnali dell'episodio non calcolabili", "—"),
    # --- musiche -------------------------------------------------------------
    "MusicLibraryError": (1, "Brano inesistente o non utilizzabile, `musiche.json` illeggibile", "—"),
    "MusicMixerError": (1, "Mix con le musiche non riuscito (ffmpeg assente, brano sparito…)", "—"),
    "MusicChanged": (3, "Un brano già usato è stato sostituito: si conferma con `--accept-changed-music`", "—"),
    "MusicMetaError": (1, "Metadati di un brano non validi", "`campo`"),
    "MusicChoiceError": (1, "Scelta delle musiche non valida", "`campo` o `category`, `query`"),
    "FfmpegNotFoundError": (1, "ffmpeg non disponibile", "—"),
    # --- lezioni -------------------------------------------------------------
    "LessonsError": (1, "Operazione sulle lezioni non valida", "—"),
    "LessonRemovalRequired": (3, "Accettare una lezione supererebbe il tetto: va indicata quale togliere", "`campo`, `lezione`, `candidates`"),
}


def exit_code(name: str) -> int:
    return CATALOG[name][0]


__all__ = ["CATALOG", "exit_code"]
