# Revisione di sicurezza · C7 (terza tornata)

Intervallo esaminato: `269b7a6..55dd769`, con attenzione al commit `55dd769` (correzioni di
MEDIA-1..4 di `C7-sicurezza-2.md`). `06acc06` e `dff2236` erano già stati esaminati nella
seconda tornata: qui li ho ricontrollati solo per le regressioni. Nessuna modifica non
committata.
File esaminati: 17 (codice: `run.py`, `src/registry.py`, `src/voice_meta.py`,
`src/voice_prep.py`, `src/voice_catalog.py`, `src/voice_library.py`, `src/music_library.py`,
`tools/voice_meta.py`; più test e documenti). Data: 2026-10-08.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 2 |
| Bassa | 1 |

MEDIA-1 e MEDIA-2 del secondo rapporto sono chiuse davvero:
- **MEDIA-1**: la revisione cambia a ogni revoca e nessun token vecchio fa passare un
  consenso dopo una revoca;
- **MEDIA-2**: le chiavi riservate si validano in `_load`, prima di ogni spesa.

MEDIA-3 e MEDIA-4 sono chiuse solo per lo scenario esatto del rapporto, e le correzioni
lasciano due buchi riprodotti nello scratchpad (`sec3/s1.py`, `sec3/s2.py`):
- il ripiego su `revoke_missing_voice` scatta anche quando la voce **c'è** in libreria
  (omonimi `.wav`/`.mp3`). La revoca risulta riuscita, ma la registrazione attuale resta
  con il consenso valido;
- la revoca fotografa le clonazioni **prima** di prendere il lock del registro. Un
  `record()` che cade in quella finestra resta fuori, perché la correzione in `record()`
  copre solo l'ordine inverso.

Nessuna regressione su gate sha256, retry, nomi esatti, sha256 delle musiche, cache e
modalità web.

## Problemi

### [MEDIA-1] Revoca per nome esatto: il ripiego scatta anche con la voce presente in libreria (omonimi) e la revoca resta a metà
- **Dove**:
  - `tools/voice_meta.py:91-116`: si ripiega su `revoke_missing_voice` per qualunque
    `AmbiguousName`/`VoiceLibraryError`, se il registro conosce il nome;
  - `src/voice_library.py:193-197`: due file con lo stesso nome normalizzato danno
    `(None, exact)`, quindi `AmbiguousName`;
  - `src/voice_prep.py:441-457`: `revoke_missing_voice` scrive solo le revoche delle
    clonazioni del registro. Non tocca i metadati e non conosce l'hash della
    registrazione attuale.
- **Voce della checklist**: 8. Garanzie di dominio (consenso/revoca); 1. Nomi da input.
- **Scenario** (riprodotto, `sec3/s2.py`):
  1. `Mario Rossi.wav` (A) clonata come `X_ID`.
  2. L'utente carica una nuova registrazione `Mario Rossi.mp3` (B) e ne registra il
     consenso. `Mario Rossi.json` è condiviso dai due file, ma il consenso porta lo sha256
     di B.
  3. La persona revoca: `voice_meta.py --name "Mario Rossi" --revoke-consent --by Persona`.
     `resolve_library_voice` → `AmbiguousName` (candidati `['Mario Rossi', 'Mario Rossi']`),
     il registro conosce il nome, quindi si passa a `revoke_missing_voice`. Esito:
     - exit 0, con `"file": null`, `consenso.stato: "revocato"` e la nota "la
       registrazione non c'è più";
     - nel registro una sola revoca, per A e `X_ID`;
     - `Mario Rossi.json` intatto: consenso di B valido, nessun `consenso_revocato`.
  4. L'utente toglie il doppione `.wav`, come chiede `plan_voices`: per `Mario Rossi`
     risulta `action=clone`, `stato=registrato`, `blocked_by_consent=False`. La prossima
     produzione **clona B a pagamento con il consenso revocato**.

  Lo stesso ripiego scatta per una voce dal nome di sole cifre (`2024.wav`): il nome viene
  letto come indice, quindi `VoiceLibraryError`. Il diff introduce il problema: prima di
  `55dd769` l'`AmbiguousName` usciva con exit 3 e la UI mostrava gli omonimi. La
  conseguenza (clonazione senza consenso) sarebbe da gravità alta, ma servono gli omonimi
  nella libreria al momento della revoca: per questo è media. La proposta del secondo
  rapporto chiedeva proprio la condizione "nessun file della libreria ha
  `voice.name == args.name`", che non è stata implementata.
- **Proposta di correzione** (in `tools/voice_meta.py`, oppure meglio in una funzione di
  `src/voice_prep.py` chiamata dal tool):
  - prima di ripiegare, scandire la libreria (`_library(config).voices`) e raccogliere
    `same = [v for v in voices if _normalize(v.name) == _normalize(args.name)]`;
  - se `same` non è vuota, **non** usare `revoke_missing_voice`. Revocare ogni file di
    `same` con `revoke_voice_consent`: la revoca è la direzione sicura e non richiede di
    scegliere un file. In alternativa rilanciare l'`AmbiguousName`, ma solo per
    `--consent`, mai per la revoca;
  - ripiegare su `revoke_missing_voice` solo se `same` è vuota e
    `registry.knows(args.name)` (il caso vero di MEDIA-3: nome simile, non uguale);
  - per i nomi di sole cifre, lo stesso controllo sul nome esatto, senza passare
    dall'indice;
  - test: `.wav` clonato più `.mp3` con consenso, poi `--revoke-consent` → stato
    `revocato` sul `.json` e revoca nel registro anche per lo sha256 del `.mp3`; tolto il
    `.wav`, `estimate` dà `ConsentRequired`.

### [MEDIA-2] MEDIA-4 chiusa a metà: un `record()` che cade fra la fotografia delle clonazioni e la scrittura della revoca resta fuori
- **Dove**:
  - `src/voice_prep.py:411-417` e `:434-437`: `_record_revocation` riceve un
    `_registry(config)` caricato **prima**, calcola `clones_of` su quella copia, poi
    `file_hash` e `_clones_in_runs` (scansione di tutte le run). Solo dopo
    `record_revocation` prende il lock del registro e aggiunge quelle voci senza
    ricalcolarle;
  - `src/registry.py:269-273`: la correzione in `record()` aggiunge il voice_id solo se
    la revoca è **già** sul disco;
  - `src/registry.py:207-214`: `revocation_for(voice_id=…)` non consulta `__storico__`.
- **Voce della checklist**: 7. Lock (race); 8. Garanzie di dominio (revoche).
- **Scenario** (riprodotto, `sec3/s1.py`, con il `record()` fatto cadere dentro
  `_clones_in_runs`):
  1. Una produzione sta clonando `Mario Rossi` (registrazione B, consenso valido).
  2. Parte la revoca: il registro viene caricato senza `Y_ID`.
  3. La clonazione finisce: `record()` scrive `Y_ID` e lo storico. Non c'è ancora una
     revoca per B, quindi la correzione di MEDIA-4 non scatta.
  4. `record_revocation` scrive la revoca di B con `voice_id: []` (e
     `provider: None`, perché anche `entry` viene dalla copia vecchia).
     `revocation_for(voice_id="Y_ID")` → `None`, anche se `Y_ID` è nello storico con
     lo sha256 di B.
  5. Si cancellano audio e `.json`, si carica una registrazione C con consenso:
     `check_not_revoked(voice_map={"Mario Rossi": "Y_ID"}, require_consent=True)` passa.
     Il retake dal web sintetizza con la voce clonata dalla registrazione revocata.

  La finestra è più stretta che nel secondo rapporto: va dal caricamento del registro
  alla fine della scansione di `output/`. Con molte run sono decine o centinaia di
  millisecondi. Resta però una revoca concorrente a una clonazione, seguita da un
  ricaricamento: gravità media, come allora. Inoltre `C7.md` §8 e il contratto ("Una
  clonazione che si chiude dopo la revoca della sua registrazione entra nella revoca")
  promettono più di quello che succede.
- **Proposta di correzione** (meglio entrambe le misure):
  - in `VoiceRegistry.record_revocation`, **dentro** il lock e dopo `self._load()`,
    ricalcolare le clonazioni dal contenuto riletto (`self.clones_of(voice)`) e unirle a
    quelle passate (da `_clones_in_runs` e dagli hash richiesti). In pratica:
    `clones` diventa "extra" e `record_revocation` aggiunge `clones_of` sotto lock.
    Anche `provider` va letto lì;
  - in `revocation_for(voice_id=V)`: se V compare in `__storico__` con sha256 H e c'è una
    revoca con `audio_sha256 == H` (stesso nome), restituire quella revoca. È la seconda
    opzione proposta nel rapporto precedente e chiude ogni ordine, perché lo storico si
    scrive sotto lo stesso lock della riga;
  - test: il `record()` iniettato fra caricamento e `record_revocation` (come
    `sec3/s1.py`) → `revocation_for(voice_id="Y_ID")` non è `None`, e il retake della run
    con `Y_ID` dà `ConsentRequired`.

### [BASSA-1] "Nuova voce": la revisione non viene da uno stato mostrato, e un consenso su una registrazione revocata ricaricata toglie la revoca senza che la UI l'abbia mostrata
- **Dove**: `docs/CONTRATTO_INTERFACCIA.md:1202`. Il flusso indica `--expect-revision R`
  subito dopo il salvataggio del file, ma la schermata non ha mostrato nessuno stato da
  cui prendere R. Coinvolti anche `src/voice_prep.py:376-386` e
  `src/registry.py:304-323`.
- **Voce della checklist**: 8. Garanzie di dominio (consenso/revoca).
- **Scenario**:
  - l'adattatore, per "Nuova voce", deve chiamare `voice_meta.py --name VOCE` dopo il
    caricamento per avere H e R, e passarli subito a `--consent`;
  - se il file caricato ha gli stessi byte di una registrazione revocata e lo stesso nome
    (per esempio la registrazione cancellata e poi ripristinata da un backup), lo stato
    è `revocato` con `revoca_nel_registro: true`. Il consenso passa comunque e
    `lift_revocations` toglie la revoca di quella registrazione e dei suoi voice_id;
  - l'operatore ha spuntato "Confermo che … ha dato il consenso" senza aver mai visto che
    proprio quella registrazione era stata revocata.

  C'è un'affermazione esplicita dell'operatore, quindi la gravità è bassa. Ma la
  revisione, in questo flusso, non protegge niente.
- **Proposta di correzione**:
  - nel contratto: per "Nuova voce", se dopo il caricamento lo stato è `revocato`,
    l'adattatore **non** concatena il consenso. Mostra lo stato (chi e quando ha
    revocato) e chiede una conferma separata, con la R di quella vista;
  - oppure, in `grant_consent`: se lo stato attuale è `revocato` per una revoca nel
    registro, il consenso dal web richiede un flag esplicito (per esempio
    `--confirm-after-revocation`), altrimenti exit 3.

## Verificato e a posto
- **MEDIA-1 del secondo rapporto (consenso legato a `consenso.revisione`)**, provato in
  `sec3/s3.py`:
  - la revisione cambia a ogni revoca:
    - revoca senza `.json` (lo crea `revoke_consent`);
    - stessa voce revocata due volte nello stesso secondo dalla stessa persona:
      `record_revocation` aggiunge sempre almeno la voce con lo sha256 attuale, quindi la
      lista cresce;
    - `revoke_missing_voice` seguita dal ricaricamento della registrazione;
  - dopo una revoca nessuna delle revisioni precedenti (`t0`…`t3`) fa passare un
    consenso;
  - l'unico ABA possibile è revoca → consenso (con la revisione nuova) → revisione uguale
    a quella di partenza. È innocuo: a quel punto la voce ha di nuovo un consenso dato su
    uno stato che mostrava la revoca;
  - la revisione non è un segreto e non va falsificata: chi la conosce può comunque
    ricaricare lo stato. Serve solo a scartare le viste vecchie, e lo fa in tutti gli
    ordini:
    - revoca prima della vista: il consenso vale;
    - revoca fra vista e clic: `RevokedMeanwhile`;
    - revoca durante l'attesa sul lock: `RevokedMeanwhile`;
    - consenso che tiene il lock: la revoca arriva dopo e vince;
  - una revisione mista (registro e metadati letti in momenti diversi, o vista calcolata
    a metà di una revoca) dà solo falsi `RevokedMeanwhile`;
  - le revoche sotto un altro nome con lo stesso sha256 non entrano nella revisione, ma
    non si tolgono (`lift` filtra per nome) e `revocation_for(audio_hash)` continua a
    bloccare: la voce resta revocata;
  - web: `--consent` senza `--expect-revision` (anche vuoto) → `InvalidArguments`;
  - `run.py` prende la revisione dal piano calcolato prima di `input()` (test
    `test_c7_security_fixes_2`).
- **MEDIA-2 del secondo rapporto**: `_check_reserved` è in `_load`, quindi vale per
  costruttore, `reload`, `record`, `record_revocation` e `lift_revocations`. Tutti i
  percorsi di spesa passano da un `VoiceRegistry` prima di spendere:
  - `estimate`, `produce`/`prepare_voices`, `voices_prepare` e `run.py` passo 6
    (`plan_voices`);
  - `retake.py` e `mix.py` (`check_not_revoked` → `plan_voices`, righe 120/202 e 65,
    prima della sintesi);
  - `stop_or_revoked` fra una battuta e l'altra (un registro rotto a metà produzione la
    ferma).

  Resta il caso di un registro corrotto a mano **durante** la clonazione (`record()`
  rilegge e fallisce dopo la spesa): è lo stesso caso del JSON non valido, precedente a
  C7 e non realistico.
- **Correzione di MEDIA-4 in `record()`**: sotto il lock del registro, con rilettura. La
  modifica delle voci restituite da `revocations()` (copia superficiale) arriva a
  `self._data` e viene scritta. Gli ordini coperti:
  - con `lift_revocations` (stesso lock): un consenso dato dopo la revoca la toglie, e il
    voice_id nuovo diventa usabile. È corretto, perché la persona ha confermato;
  - con `record_revocation` scritta prima: la revoca contiene il voice_id.

  Il caso scoperto è in MEDIA-2.
- **MEDIA-3 del secondo rapporto, scenario originale**: con `Maria Rossi.wav` o
  `Mario Rossini.wav` in libreria, il nome esatto noto al registro si revoca. Un nome non
  esatto e sconosciuto resta `AmbiguousName`; un nome con maiuscole diverse non è
  "conosciuto" (`knows` è esatto). La revoca non può colpire un'altra persona né
  indovinare un nome: scrive solo revoche con `voce == args.name` esatto. Il buco è in
  MEDIA-1.
- **Deadlock**: nessun ordine nuovo.
  - `grant_consent`: consenso → metadati → registro;
  - `revoke_*`: consenso → metadati → registro, oppure consenso → registro;
  - `record`: clonazione → registro;
  - `_current_token` legge senza lock.
- **Garanzie invariate**:
  - gate sha256 e `stop_or_unapproved`: non toccati;
  - nessun retry: marcatore e `CloneOutcomeUnknown` invariati, e il ricontrollo del
    consenso sui byte letti resta;
  - nomi esatti: nessun nuovo punto che risolve per somiglianza;
  - sha256 delle musiche e cache dei segmenti: `music_library.py` cambia solo per
    l'indice dal web (già esaminato);
  - modalità web: i due nuovi obblighi (`--expect-sha256` e `--expect-revision`) sono
    solo restrizioni.
- **Segreti, sottoprocessi, prompt, path traversal**: niente di nuovo. La revisione è un
  hash troncato dei dati di revoca (nomi e date già mostrati) e non rivela altro.
- **Test**: `tests/test_web_integration.py` eseguita per intero in questa revisione: 839
  controlli superati, exit 0 (846 s).

## Da verificare nelle prossime fasi
- Le basse di `C7.md` §8 (BASSA-1..5 del secondo rapporto) restano valide, con la stessa
  gravità. BASSA-3 (`_clones_in_runs` senza `voci.json`) va decisa insieme a MEDIA-2 di
  questo rapporto: se `revocation_for` consulta lo storico, il buco si riduce alle
  clonazioni precedenti allo storico.
- Un voice_id clonato **da una registrazione revocata sotto un altro nome** con gli
  stessi byte (copia `Mario.wav` di `Mario Rossi.wav`) non entra nella revoca: le
  revoche sono per nome e sha256, `record()` confronta anche il nome. Oggi si blocca
  alla pianificazione grazie a `revocation_for(audio_hash)`; da tenere presente se si
  rilassa quel controllo.
- `revoke_missing_voice` su un nome che il registro conosce solo con righe senza
  `voice_id` non scrive niente, ma risponde `stato: "revocato"` e `revoche_registro: 0`.
  Non ci sono voice_id da bloccare, ma conviene un messaggio che lo dica.
- I dettagli di `ConsentRequired` (produzione, retake) non contengono `revisione`: la UI
  deve richiamare `voice_meta.py --name` prima di offrire la casella del consenso. Va
  scritto nel contratto.
