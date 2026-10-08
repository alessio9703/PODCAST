# Revisione di sicurezza · C7 (quinta tornata)

Intervallo esaminato: `9beb0d9..a2589e5`, cioè il commit `a2589e5`: correzione di MEDIA-1 di
`C7-sicurezza-4.md`. Nessuna modifica non committata.
File esaminati: 10. Il codice cambiato è in `src/registry.py` (`known_names`),
`src/voice_prep.py` (`revoke_by_name`) e `tools/voice_meta.py` (`_revoke`); il resto sono
test e documenti. Per il contesto ho letto anche `src/voice_meta.py` (`with_registry`,
`revocation_token`), `src/voice_library.py` (`_normalize`, `resolve`), `src/run_layout.py`
e `plan_voices`. Data: 2026-10-08.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 1 |
| Bassa | 4 |

MEDIA-1 della quarta revisione è chiusa. Ho riprodotto lo scenario del rapporto e le sue
varianti: revoca per nome del file, revoca per nome della variante cancellata, retake della
run vecchia dopo un nuovo consenso su una registrazione nuova. In tutti i casi il voice_id
della variante viene revocato e il retake dà exit 3. Nessun nome viene indovinato.

La correzione però è asimmetrica: la revoca vale per il nome normalizzato, mentre il
consenso toglie le revoche solo per il nome esatto. Se una variante e il file attuale hanno
gli **stessi byte** (un file rinominato o ricaricato con un altro nome), un nuovo consenso
della persona non sblocca più la voce, e nessun comando lo permette (MEDIA-1, nuova con
questo commit). Le garanzie di dominio non hanno regressioni.

## Problemi

### [MEDIA-1] Dopo una revoca per nome, un nuovo consenso non sblocca più una registrazione che ha gli stessi byte di una variante del registro
- **Dove**:
  - `src/voice_prep.py:439-447`: `revoke_by_name` scrive una revoca anche per le varianti
    del registro (`Mario_Rossi`), con lo sha256 delle loro registrazioni;
  - `src/registry.py:325-337`: `lift_revocations` toglie solo le revoche con `voce`
    **esattamente** uguale al nome del file che riceve il consenso;
  - `src/registry.py:213`: `revocation_for(audio_hash=...)` invece trova una revoca con
    quello sha256 **qualunque** sia la `voce`;
  - `src/voice_meta.py:201-204`: `with_registry` mette quindi lo stato `revocato`.
- **Voce della checklist**: 8. Garanzie di dominio (consenso e revoca). È un disservizio,
  non un indebolimento: si sbaglia verso la revoca.
- **Scenario** (riprodotto, `scratchpad/sec5/s1.py`; sul codice di `9beb0d9`,
  `s1_old.py`, lo stesso scenario finisce con il consenso valido e la produzione a exit 0):
  1. Si carica `Mario_Rossi.wav`, si registra il consenso, la voce viene clonata e usata
     (riga e storico sotto `Mario_Rossi`, sha256 `S`).
  2. L'operatore corregge il nome: cancella e ricarica lo stesso file come
     `Mario Rossi.wav` (byte identici, sha256 `S`) e registra il consenso.
  3. La persona revoca: `voice_meta.py --name "Mario Rossi" --revoke-consent`. Si
     scrivono `(Mario Rossi, S)` e, per la correzione nuova, `(Mario_Rossi, S, [fake-0001])`.
  4. La persona ci ripensa. Dal web: `voice_meta.py --name "Mario Rossi" --consent
     --expect-sha256 S --expect-revision R --by Persona` dà **exit 0** ("Consenso
     registrato"). Però `consenso.stato` resta `revocato`, con `revoca_nel_registro: true`,
     perché `lift_revocations("Mario Rossi", S)` non tocca `(Mario_Rossi, S)`.
  5. Una run nuova con `Mario Rossi` dà exit 3 `ConsentRequired` ("consenso revocato").
     Ripetere il consenso non serve. Nessun comando documentato sblocca la voce: bisogna
     rinominare il file come `Mario_Rossi.wav` e dare il consenso lì, oppure correggere il
     registro a mano.

  Caricare di nuovo lo stesso file con il nome corretto è un'operazione normale dalla
  libreria Voci. La risposta a exit 0 con stato `revocato` porta la UI a un ciclo senza
  uscita.
- **Proposta di correzione**: rendere simmetrico il consenso.
  - `src/registry.py:325` `lift_revocations(voice, audio_hash)`: togliere le revoche con
    `_normalize(item["voce"]) == _normalize(voice)` **e** `audio_sha256 == audio_hash`. I
    voice_id di quelle revoche sono stati clonati da quegli stessi byte, e la persona ha
    confermato proprio quei byte. Le revoche di registrazioni diverse (sha256 diverso)
    restano, come chiede ALTA-1 di C7. Per evitare un import circolare, `_normalize` si può
    spostare in un modulo neutro, o passare la funzione come parametro.
  - `src/voice_meta.py:178` `revocation_token`: comprendere le revoche di tutte le `voce`
    con lo stesso nome normalizzato, così una revoca scritta su una variante cambia la
    `revisione` mostrata.
  - `src/voice_prep.py:340` `_consent_lock_target`: derivare il lock dal nome normalizzato.
    Così consenso e revoca di `Mario Rossi` e `Mario_Rossi` si serializzano, e si chiude
    anche la nota sui filesystem che non distinguono le maiuscole (quarta revisione, "Da
    verificare").
  - Test: lo scenario sopra. Dopo il nuovo consenso su `Mario Rossi.wav` (stessi byte),
    lo stato deve essere `registrato` e la produzione deve andare. Con byte diversi, invece,
    `fake-0001` deve restare revocato (non va indebolito il test di
    `test_c7_security_fixes_4`).

### [BASSA-1] Doppioni con registro illeggibile: la revoca si ferma al primo file, contro quanto dicono il commento e `C7.md` §10
- **Dove**: `src/voice_prep.py:434-438`. Il commento dice "la revoca nei metadati si scrive
  anche se poi il registro risulta illeggibile". Lo dice anche `C7.md` §10.
- **Voce della checklist**: 8. Garanzie di dominio (revoca); 7. robustezza.
- **Scenario** (riprodotto, `scratchpad/sec5/s2.py`):
  - in libreria ci sono `Anna Neri.wav` e `Anna-Neri.mp3`, e `voices_registry.json` non è
    un JSON valido;
  - `--revoke-consent --name "Anna Neri"` termina con exit 1 `RuntimeError`;
  - `Anna Neri.json` risulta revocato, `Anna-Neri.json` no. `revoke_voice_consent` apre il
    registro **dentro** il ciclo, file per file (`_record_revocation` → `_registry`), quindi
    la prima eccezione ferma i file successivi;
  - se poi l'operatore toglie il doppione revocato, l'altro resta con il consenso valido.

  Il trigger è diverso dalla BASSA-1 della quarta revisione (che parte da un `.json`
  rotto), ma l'esito e la correzione sono gli stessi. La gravità resta bassa: l'errore è
  visibile, e finché ci sono i doppioni la voce non si usa (`plan_voices`).
- **Proposta di correzione**: nello stesso intervento della BASSA-1 della quarta revisione.
  1. Prima scrivere la revoca nei metadati di **tutti** i file (`revoke_consent`, sotto il
     loro lock).
  2. Poi scrivere le revoche nel registro.
  3. Raccogliere gli errori e riportarli in `details` (`revocati`, `non_revocati`).
  4. Correggere il commento e `C7.md` §10.

### [BASSA-2] Con un registro illeggibile, il messaggio consiglia di cancellarlo: si perderebbero tutte le revoche
- **Dove**: `src/registry.py:127-130` ("Correggilo o cancellalo: verra' ricreato"). Il testo
  c'era già prima di questo commit. Ora compare però anche nella revoca per nome: è proprio
  il caso che il commento di `revoke_by_name` considera (vedi `s2.py`).
- **Voce della checklist**: 8. Garanzie di dominio (revoche nel registro).
- **Scenario**:
  1. Il registro si corrompe.
  2. L'operatore segue il messaggio e cancella il file: spariscono `__revoche__` e
     `__storico__`.
  3. Si ricarica la registrazione di una persona che aveva revocato, con lo stesso nome e
     un consenso nuovo.
  4. Un retake di una run vecchia con il suo voice_id (in `voci.json`) passa
     `check_not_revoked`, perché la revoca non c'è più.

  Servono un registro corrotto e un'azione manuale suggerita dal tool: per questo la
  gravità è bassa.
- **Proposta di correzione**: cambiare il messaggio. Dire che il file contiene le revoche
  del consenso e lo storico delle clonazioni, che va **corretto** (o ripristinato da una
  copia) e non cancellato. In `details` mettere `{"file": ..., "contiene_revoche": true}`.

### [BASSA-3] `revoche_registro` cambia significato: con un file conta solo le revoche delle varianti
- **Dove**: `tools/voice_meta.py:142`; `docs/CONTRATTO_INTERFACCIA.md:846-854`.
- **Voce della checklist**: contratto / output del tool.
- **Scenario**:
  - `--revoke-consent --name "Mario Rossi"` con un solo file e nessuna variante risponde
    `revoche_registro: 0`, anche se nel registro sono state scritte le revoche del file
    (una o più). Lo stesso succede nel riepilogo dei doppioni;
  - il contratto, poco sopra, definisce `revoche_registro` come "il numero di revoche
    scritte";
  - una UI che mostrasse "0 revoche nel registro", o che decidesse da quel numero se si
    possono cancellare i file, darebbe un'indicazione sbagliata.

  Non ha conseguenze sulla sicurezza: le revoche ci sono.
- **Proposta di correzione**: scegliere una delle due strade.
  - Contare tutte le revoche scritte: `revoke_voice_consent` può restituire gli item di
    `_record_revocation`.
  - Oppure rinominare il campo (per esempio `revoche_registro_varianti`) e aggiornare il
    contratto, dicendo che cosa conta in ciascuna forma della risposta.

### [BASSA-4] `--revoke-consent --spoken-name` ora ignora il nome pronunciato anche con un solo file
- **Dove**: `tools/voice_meta.py:88-90`. `_revoke` esce prima del blocco `spoken_name`
  (`:95`).
- **Voce della checklist**: robustezza / contratto.
- **Scenario**:
  - fino a `9beb0d9`, con un solo file, `--revoke-consent --spoken-name "Mario Rossi"`
    registrava il nome pronunciato e poi revocava;
  - ora il nome pronunciato viene ignorato senza avviso, con exit 0.

  La quarta revisione lo segnalava solo per il ramo dei doppioni; ora vale per ogni revoca.
  Non tocca la sicurezza.
- **Proposta di correzione**: rifiutare la combinazione con `InvalidArguments` (in
  `tools/voice_meta.py`, prima di `bootstrap`), oppure applicare `record_spoken_name` al
  singolo file prima di `_revoke`.

## Verificato e a posto
- **MEDIA-1 della quarta revisione**:
  - `test_c7_security_fixes_4` e `test_c7_security_fixes_3` eseguiti su una copia, tutti
    `ok`;
  - `--name "Mario Rossi"` (file in libreria) e `--name "Mario_Rossi"` (variante
    cancellata) revocano entrambi il file **e** la variante del registro;
  - il voice_id della variante finisce in `__revoche__` e il retake della run vecchia dà
    exit 3, anche dopo un consenso su una registrazione nuova chiamata `Mario_Rossi`.
- **Variante solo nel registro** (riga o storico): coperta da `known_names`. `knows()`,
  dentro `revoke_missing_voice`, accetta gli stessi nomi.
- **Variante solo in `__revoche__`**: `known_names` non la considera. Non serve: chi ha una
  revoca senza riga né storico non è mai stato clonato con quel nome (`record` scrive
  sempre riga e storico), e la sua revoca è già lì. Né il codice né i tool tolgono righe o
  storico dal registro (verificato con grep).
- **Variante solo in `voci_usate.json`**: le chiavi di `voci_usate.json` sono i nomi esatti
  della libreria (`plan_voices` vuole nomi esatti), che sono anche i nomi del registro. Una
  variante che esiste solo lì vuol dire un registro modificato a mano o ricreato. In quel
  caso sono sparite anche le revoche (vedi BASSA-2), e il problema non nasce da questo
  commit.
- **Nomi simili ma diversi** (`s2.py`): con il registro che conosce `Mario Rossini`,
  `Mário  Rossi` e `Rossi Mario`, la revoca di `Mario Rossi` comprende solo `Mário  Rossi`
  (stesso nome a meno di accenti e spazi).
  - `Mario Ros` → exit 3 `AmbiguousName`; `1`, uno spazio solo, `Rossini Mario` → exit 1
    `VoiceLibraryError`: niente revocato.
  - L'unico criterio è `_normalize(n) == _normalize(name)`, lo stesso del match esatto di
    `resolve`: niente indice, niente sottostringhe, niente `difflib`.
  - Quando non c'è niente da revocare, `resolve_library_voice` serve solo a dare l'errore:
    anche se trovasse una voce, segue sempre un `raise`.
- **Senza `--by`**: con sole varianti del registro si ottiene `ConsentByRequired` prima di
  scrivere. Con i file, `revoke_consent` rifiuta prima di scrivere.
- **Race**:
  - `known_names` si legge fuori dal lock, ma `revoke_missing_voice` →
    `record_revocation` rilegge le clonazioni dentro il lock del registro;
  - un `record()` concorrente di una variante richiede un file con quel nome, che è in
    `files`, oppure è caricato dopo la scansione con un consenso successivo, che vale come
    ogni consenso dato dopo una revoca;
  - `lift_revocations` non può togliere le revoche delle varianti, per nome diverso
    (anzi: troppo poco, vedi MEDIA-1);
  - `grant_consent` con una `revisione` vecchia dà `RevokedMeanwhile`, perché
    `revoke_by_name` revoca i file per primi e la revisione del file cambia;
  - nessun lock annidato nuovo (consenso → registro, come prima), quindi nessun deadlock.
- **Output e contratto**:
  - le forme della risposta (vista della voce con un solo file, riepilogo con
    `file: null`) corrispondono a "Revoche nel registro";
  - `consenso.stato: "revocato"` nel riepilogo è corretto: `record_revocation` scrive
    almeno una voce per ogni nome noto, perché le clonazioni hanno sempre un voice_id;
  - `files` e `nomi_registro` contengono solo nomi di file o di voci della libreria, già
    visibili in `voices_list.py`.
- **Garanzie invariate**:
  - il commit non tocca il gate sha256, la clonazione (nessun retry), le musiche, la cache
    dei segmenti, segreti, sottoprocessi, prompt e percorsi;
  - la modalità web resta quella di prima: `--consent` vuole ancora `--expect-sha256` e
    `--expect-revision` e non passa da `_revoke`;
  - `run.py` e il subagent non chiamano le funzioni cambiate.

## Da verificare nelle prossime fasi
- Le basse note di `C7.md` §8-§10 (BASSA-1 della terza e della quarta tornata, BASSA-3 di
  §8) restano valide con la stessa gravità.
- Se si corregge MEDIA-1 rendendo `lift_revocations` normalizzata, controllare che resti
  vero quanto segue:
  - un consenso su byte **diversi** non toglie mai revoche;
  - le revoche con chiave `None` non si tolgono mai.
- Un file cancellato fra la scansione della libreria e `revoke_voice_consent` fa
  fallire `file_hash` dopo la scrittura dei metadati. L'errore è visibile, e ripetendo il
  comando si passa dal ramo del registro. Da coprire con la raccolta degli errori di
  BASSA-1.
