# Revisione di sicurezza · C7 (quarta tornata)

Intervallo esaminato: `55dd769..9beb0d9`, cioè il commit `9beb0d9`: correzioni di MEDIA-1 e
MEDIA-2 di `C7-sicurezza-3.md`. Nessuna modifica non committata.
File esaminati: 10. Codice: `src/registry.py`, `src/voice_prep.py`, `tools/voice_meta.py`.
Il resto sono test e documenti; per il contesto ho letto anche `src/voice_meta.py` e
`src/voice_library.py`. Data: 2026-10-08.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 1 |
| Bassa | 1 |

Le due medie del terzo rapporto sono chiuse per gli scenari descritti e per tutti gli ordini
che ho provato:
- **MEDIA-1**: i doppioni `.wav`/`.mp3`, le varianti `_`/`-`/maiuscole presenti in libreria
  e i nomi di sole cifre vengono revocati tutti. Un numero non viene mai preso come indice.
- **MEDIA-2**: le clonazioni si leggono dentro il lock, e `revocation_for` consulta lo
  storico.

Resta un buco, che c'era già prima di questo commit: le varianti dello stesso nome che si
trovano **solo nel registro**. La regola nuova ("stesso nome, si revoca tutto") vale per i
file della libreria, non per i nomi del registro. Nessuna regressione su gate sha256, retry,
nomi esatti, sha256 delle musiche, cache dei segmenti e modalità web.

## Problemi

### [MEDIA-1] La revoca per nome ignora le varianti dello stesso nome rimaste solo nel registro, e quelle varianti non si possono più revocare
- **Dove**:
  - `tools/voice_meta.py:92-98`: se `resolve_library_voice` trova **un** file, si revoca
    solo quello, per il suo nome esatto (`:123`);
  - `tools/voice_meta.py:157-175`: con i doppioni si revocano solo i file della libreria;
    si ripiega sul registro solo se non c'è nessun file con quel nome;
  - `src/voice_prep.py:411-416` e `src/registry.py:226-232`: `_record_revocation` e
    `clones_of` lavorano sul nome **esatto** (`h["voce"] == voice`).

  `_normalize` (`src/voice_library.py:36-40`) considera uguali `Mario_Rossi`,
  `Mario-Rossi`, `mario rossi` e `Mario Rossi`. Il registro invece li tiene come chiavi
  distinte.
- **Voce della checklist**: 8. Garanzie di dominio (revoca del consenso); 1. Nomi da input.
- **Scenario** (riprodotto, `scratchpad/sec4/s2.py`):
  1. `Mario_Rossi.wav` (registrazione A) viene clonata come `X_ID` e usata nella run R.
     Nel registro: riga e storico sotto `Mario_Rossi`.
  2. A viene cancellata. Si carica `Mario Rossi.mp3` (B) e si registra il consenso.
  3. La persona revoca:
     - `voice_meta.py --name "Mario Rossi" --revoke-consent --by Persona` risolve in B
       (ramo a voce singola) e revoca solo `Mario Rossi`;
     - anche `--name "Mario_Rossi"` risolve in B: `_normalize` è uguale e c'è un solo
       file. Il ripiego su `revoke_missing_voice("Mario_Rossi")` non si raggiunge mai
       finché esiste B;
     - risultato: `revocation_for(voice_id="X_ID")` è `None` e la UI mostra "revocato".
  4. Più tardi B viene tolta e si ricarica una registrazione C, chiamata di nuovo
     `Mario_Rossi.wav` (byte nuovi). Si registra il consenso su C.
  5. Retake della run R: `check_not_revoked(["Mario_Rossi"], voice_map={"Mario_Rossi":
     "X_ID"}, require_consent=True)` passa (`AMMESSO` nello script). Il retake sintetizza
     con `X_ID`, cioè con la voce clonata dalla registrazione A, che la persona aveva
     revocato.

  Questo contraddice la garanzia di ALTA-1 di C7: un consenso su una registrazione diversa
  con lo stesso nome non rende usabili i voice_id della vecchia. Nel contratto
  (`CONTRATTO_INTERFACCIA.md:846-851`) la revoca per nome risulta completa. La gravità è
  media e non alta perché servono una variante di nome che vive solo nel registro, un nuovo
  caricamento con **quella** variante esatta e un consenso nuovo. Però i nomi con `_`
  sono comuni nei file caricati, e l'operatore non ha nessun modo di revocare `X_ID`
  finché esiste B.
- **Proposta di correzione**:
  - portare tutta la revoca per nome in una funzione di `src/voice_prep.py`, per esempio
    `revoke_by_name(config, name, by)`, usata dal tool per **ogni** `--revoke-consent`
    (sia il ramo a voce singola sia quello senza voce singola). La funzione:
    1. revoca ogni file di `same_name_voices(config, name)` con `revoke_voice_consent`;
    2. raccoglie dal registro i nomi esatti `n` (righe, `__storico__` e, per prudenza,
       anche le `voce` di `__revoche__`) con `_normalize(n) == _normalize(name)` e
       `n` diverso da tutti i `v.name` del punto 1;
    3. chiama `revoke_missing_voice(config, n, by=by)` su ciascuno;
    4. restituisce file revocati e nomi del registro revocati.

    Così la regola "stesso nome normalizzato, si revoca tutto" vale sia per la libreria sia
    per il registro. Non si indovina niente: è la stessa equivalenza che `resolve` usa per
    il match esatto.
  - Aggiungere a `VoiceRegistry` un `names_like(normalized)` che lavora in sola lettura.
  - Risposta del tool: aggiungere `nomi_registro: [...]` accanto a `files`.
  - Test: lo scenario sopra. Dopo `--revoke-consent --name "Mario Rossi"`,
    `revocation_for(voice_id="X_ID")` non deve essere `None`, e il retake della run con
    `Mario_Rossi`/`X_ID` deve dare `ConsentRequired` anche dopo un nuovo consenso su C.

### [BASSA-1] Revoca dei doppioni: un `.json` rotto di un doppione ferma la revoca degli altri, e l'errore non dice che cosa è già stato revocato
- **Dove**: `tools/voice_meta.py:161-163`. Il ciclo si interrompe alla prima eccezione di
  `revoke_voice_consent`, per esempio `VoiceMetaError` da `load_meta` dentro
  `revoke_consent`.
- **Voce della checklist**: 8. Garanzie di dominio (revoca); robustezza.
- **Scenario** (riprodotto, `scratchpad/sec4/s3.py`):
  - in libreria ci sono `Anna Neri.wav`, con consenso, e `Anna-Neri.mp3`, con
    `Anna-Neri.json` illeggibile;
  - `--revoke-consent --name "Anna Neri"` termina con exit 1 `VoiceMetaError`.
    `Anna Neri.wav` qui è stata revocata perché viene prima nell'ordine della libreria;
  - con un ordine diverso (il file rotto per primo) **nessun** file viene revocato e la
    persona risulta ancora con il consenso valido sul file sano;
  - la voce non è comunque utilizzabile finché ci sono i doppioni: `plan_voices` li
    rifiuta, e un `.json` illeggibile conta come revocato. Però, se l'operatore "risolve"
    cancellando il doppione rotto, il file sano resta con il consenso valido;
  - l'errore non dice quali file sono stati revocati e quali no.

  L'errore è visibile e basta rilanciare il comando dopo aver corretto il file: per questo
  la gravità è bassa.
- **Proposta di correzione**:
  - nel ciclo, revocare tutti i file possibili e raccogliere gli errori file per file;
  - alla fine:
    - se almeno uno è fallito, lanciare un errore di dominio con
      `details={"revocati": [...], "non_revocati": [{"file": ..., "errore": ...}]}`;
    - in ogni caso scrivere comunque la revoca **nel registro** per il nome
      (`revoke_missing_voice` o `_record_revocation`), che non dipende dal `.json`.
  - Test: un doppione con `.json` rotto, ordinato prima del file sano: il file sano deve
    risultare revocato.

## Verificato e a posto
- **MEDIA-1 del terzo rapporto (doppioni)**: provato con il ramo vero del tool
  (`scratchpad/sec4/s1.py`). Casi controllati:
  - con `Mario Rossi.wav` clonata, `Mario Rossi.mp3` con consenso e `mario_rossi.wav` con
    consenso, si revocano tutti e tre: metadati e registro, con `X_ID` nella revoca della
    registrazione A;
  - tolti i doppioni, il `.mp3` resta `revocato` e bloccato;
  - `2024.wav` viene revocato per nome, non come indice;
  - `--name 1` dà `VoiceLibraryError` e non revoca la prima voce della libreria (nessun
    nome indovinato);
  - un nome parziale (`Mario`) che il registro non conosce esattamente resta un errore;
  - la revoca di una persona diversa con un nome che si normalizza uguale è la direzione
    sicura, e una revoca non sblocca niente;
  - un file caricato dopo la scansione della libreria non è un problema: con il `.json`
    in comune è revocato dai metadati; con un nome variante ha il suo `.json` e un
    consenso dato dopo, che vale come qualunque consenso successivo alla revoca.
- **MEDIA-2 del terzo rapporto (race con `record`)**:
  - lo script `sec3/s1.py` del terzo rapporto, rieseguito sul codice attuale: `Y_ID`
    entra nella revoca e il retake dà `ConsentRequired`;
  - `clones_of` si calcola dentro il lock del registro, dopo la rilettura;
  - un `record()` prima della revoca viene letto, uno dopo si aggiunge alla revoca
    (correzione di MEDIA-4);
  - in più, `revocation_for(voice_id)` usa lo storico, che si scrive sotto lo stesso lock
    della riga. Questo chiude ogni ordine anche per le revoche scritte da codice vecchio;
  - `_clones_in_runs` resta fuori dal lock del registro: è giusto, perché la scansione di
    `output/` non tiene il lock.
- **Lift e storico**: verificato in `sec4/s3.py`.
  - Con `V_A` clonato da A, `V_B` clonato da B ed entrambi revocati, un consenso su B
    riattiva solo `V_B`; `V_A` resta revocato;
  - `lift_revocations` toglie solo le voci `(nome, sha256)` della registrazione
    confermata;
  - il ramo con lo storico richiede `voce` uguale **e** `audio_sha256` non vuoto e
    uguale, quindi non può legare un voice_id a una revoca di un'altra registrazione;
  - le revoche con chiave `None` (registrazione ignota) non si tolgono mai e il primo
    ciclo le trova lo stesso;
  - una revoca tolta da un consenso sulla stessa registrazione rende usabili i suoi
    voice_id, ed è il comportamento voluto.
- **`provider` nella revoca**: si legge ancora da `registry.get()` fuori dal lock
  (`src/voice_prep.py:413`), quindi in una race può valere `None`. Il campo è solo
  informativo: nessun punto del codice lo usa per decidere.
- **Deadlock**: nessun ordine nuovo.
  - Il ramo dei doppioni prende i lock di consenso uno alla volta, senza annidarli;
  - `record_revocation` prende solo il lock del registro, dentro quello del consenso,
    come prima.
- **Consenso e revisione**: `revocation_token` non cambia. Per ogni file la revoca passa
  dal suo lock di consenso, quindi:
  - un `--consent` con una revisione vista prima della revoca di quel file dà
    `RevokedMeanwhile`;
  - un consenso che tiene il lock per primo viene poi revocato.
- **Garanzie invariate**:
  - gate sha256 e `stop_or_unapproved`: non toccati;
  - nessun retry sulla clonazione: invariato;
  - nomi mai indovinati: `same_name_voices` usa solo l'uguaglianza normalizzata, la stessa
    del match esatto di `resolve`, senza indice, sottostringhe o difflib;
  - sha256 delle musiche e cache dei segmenti: non toccati;
  - modalità web: `--by` resta obbligatorio (`_require_by`) e `--consent` non passa dal
    nuovo ramo.
- **Segreti, sottoprocessi, prompt, path traversal**:
  - niente di nuovo;
  - `files` nella risposta contiene solo nomi di file della libreria, già esposti da
    `voices_list.py`.
- **Test**: eseguite le sezioni `test_c7_security_fixes_3` e `test_c7_security_fixes_2`
  di `tests/test_web_integration.py`, tutte `ok`. La suite intera non l'ho rilanciata
  (circa 14 minuti).

## Da verificare nelle prossime fasi
- Restano valide, con la stessa gravità:
  - le basse di `C7.md` §8 e §9: BASSA-1 della terza tornata ("Nuova voce" e revisione) e
    BASSA-3 di §8 (`_clones_in_runs` senza `voci.json`);
  - con lo storico consultato da `revocation_for`, BASSA-3 si riduce alle clonazioni
    precedenti all'introduzione dello storico.
- `--revoke-consent` insieme a `--spoken-name` sul ramo dei doppioni: il nome pronunciato
  viene ignorato senza avviso (exit 0). Non tocca la sicurezza, ma conviene rifiutare la
  combinazione, o almeno segnalarla.
- Su un filesystem che non distingue le maiuscole (macOS), `Anna Neri.wav` e
  `ANNA NERI.mp3` condividono lo stesso `.json` ma hanno lock di consenso diversi
  (`_consent_lock_target` usa il nome esatto). Era così già prima di questo commit: da
  tenere presente se si sposta la revoca in `revoke_by_name` (MEDIA-1). In quel caso
  conviene derivare il lock dal nome normalizzato.
