# Revisione di sicurezza · A5

Intervallo esaminato: `49d6b8c..d363381` (commit `ca4991c`, `68a3434`, `d363381`; nessuna
modifica non committata)
File esaminati: 32 del diff, più per contesto `app/servizi/azioni.py`, `app/servizi/esiti.py`,
`app/servizi/gettoni.py`, `app/agente/esecutore.py`, `app/file/download.py`,
`static/js/dom.js`, `static/js/schermate/stima.js` e, nell'agente, `src/voice_prep.py`,
`src/voice_library.py`, `src/lessons.py`, `src/retake.py`, `src/music_meta.py`,
`src/settings_view.py`, `tools/doctor.py`, `tools/retake.py`. Data: 2026-10-09.

Verifiche eseguite: `.venv/bin/python -m pytest` → 361 superati; sonde con `TestClient` e
l'agente finto dei test (in una cartella temporanea fuori dal progetto) per la doppia
proposta di lezioni, le righe del registro, una lezione con a capo, la diagnostica con un
percorso con spazi.

## Sintesi
| Gravità | Numero |
|---|---|
| Alta | 0 |
| Media | 1 |
| Bassa | 5 |

Le aggiunte di A5 sono solide nei punti che contano di più. Caricamenti e download usano lo
stesso codice di A2: nome, firma, limite contato mentre arriva, temporaneo nascosto, gettone
legato alla libreria, descrittori senza link. I retake sono legati alla stima e le
indicazioni passano da un elenco chiuso. Le lezioni chiedono il permesso, hanno `--by` e i
limiti dell'agente. L'unico problema medio sta nel flusso «Nuova voce». Concatena il
consenso al caricamento senza guardare lo stato. Così una registrazione revocata e poi
ricaricata torna utilizzabile senza che nessuno abbia visto la revoca: è il caso che
C7-sicurezza-3 BASSA-1 chiedeva all'adattatore di non fare. Le basse riguardano il
registro, la doppia spesa per le proposte di lezioni, i metadati di un brano sostituito,
il carico dei sottoprocessi brevi e il timeout di nginx.

## Problemi

### [MEDIA-1] «Nuova voce» registra il consenso anche su una registrazione revocata, senza mostrarlo
- **Dove**: `static/js/schermate/voci.js:224-243` (`salvaNuova`), casella a
  `static/js/schermate/voci.js:195` e `:214-215`. Nell'agente: `src/voice_prep.py:361-394`
  (`grant_consent` non guarda lo stato e chiama `lift_revocations`).
- **Voce della checklist**: 8. Garanzie di dominio (consenso e revoca).
- **Scenario**:
  - il consenso di «Mario Rossi» viene revocato, poi registrazione e `.json` vengono
    cancellati, come dice la procedura di `DEPLOY.md` §9 («Dopo A5»);
  - mesi dopo un gestore delle voci ricarica lo **stesso file** (stesso sha256, per esempio
    dall'archivio o da una mail) con «Aggiungi una voce». Scrive «Mario Rossi» (o
    «mario_rossi»: le varianti valgono uguale) e spunta la casella del consenso **prima** di
    caricare;
  - `salvaNuova` carica il file. Poi chiede `GET /api/voci/Mario Rossi/consenso`, che
    risponde `consenso.stato: "revocato"` e `revoca_nel_registro: true`. Il codice confronta
    solo lo sha256, che coincide, e chiama subito `POST …/consenso` con la `revisione` appena
    letta;
  - l'agente registra il consenso e toglie la revoca **di quella registrazione e dei
    `voice_id` clonati da lei**, anche per le varianti del nome. Retake e rimontaggi degli
    episodi vecchi con quei `voice_id` tornano possibili, e la voce si riusa senza nuova
    clonazione;
  - l'operatore non ha mai visto «Revocato da …, il …». La revisione, che dovrebbe legare
    il consenso allo stato mostrato, viene da una lettura che la pagina non mostra.
  - Lo stesso succede quando il nome coincide con una voce **revocata ancora presente**: la
    sostituzione chiede solo «Vuoi sostituirlo?», con la nota «il consenso va confermato di
    nuovo». Subito dopo il consenso si registra da solo per la registrazione nuova e prende
    il posto della revoca nel `.json`.

  La scheda della voce invece non offre «Registra il consenso» se lo stato è `revocato`
  (`voci.js:87`), e chiede un dialogo di conferma (`voci.js:106-112`). Il flusso nuovo
  aggira entrambe le cautele. C'è un'affermazione dell'operatore («ha dato il consenso»),
  per questo la gravità è media e non alta. Ma è data alla cieca e annulla una revoca, che
  è l'atto più forte del sistema.
- **Proposta di correzione** (in `salvaNuova`, dopo la lettura dello stato):
  - se `s.dati.consenso.stato` è `revocato`, oppure `revoca_nel_registro` è vero, **non**
    registrare il consenso. Ricaricare l'elenco e mostrare nella scheda «Consenso revocato
    da X il Y: questa registrazione era stata revocata». Il consenso eventuale va dato con
    un'azione separata e un dialogo che lo dica esplicitamente («Registrare di nuovo il
    consenso dopo la revoca?»), con la revisione di **quella** vista;
  - concatenare il consenso solo se lo stato è `mancante` o `superato`, e sempre passando dal
    dialogo `domanda()` di `registraConsenso` (testo con il nome pronunciato), non dalla sola
    casella spuntata prima del caricamento;
  - se il caricamento è stato una sostituzione (`corpo.dati.sostituiti` non vuoto), non
    concatenare mai il consenso: mostrare la scheda aggiornata;
  - nell'agente resta utile la seconda proposta di C7-sicurezza-3 BASSA-1: dal web, un
    `--consent` su uno stato `revocato` richiede un flag esplicito, altrimenti exit 3. Va
    chiesto all'utente, perché `podcast-web` non modifica l'agente.

### [BASSA-1] Le proposte di lezioni non hanno il controllo dei doppioni in coda: due conferme, due spese
- **Dove**: `app/lavori/coda.py:145` (il controllo vale solo se `run is not None`),
  `app/api/avanzate.py:66-71`, `app/agente/tabella.py:282`.
- **Voce della checklist**: 8 (spesa) / estesa 5.
- **Scenario**: verificato con l'agente finto. Due `POST /api/lezioni/proposta` con la stessa
  impronta, mentre la prima proposta è ancora in corso, rispondono entrambi `202`, e la coda
  lancia due volte `lessons.py --propose`, cioè due chiamate al modello. Succede con due
  schede aperte o con due amministratori. Ciascuno ha visto la stima, ma nessuno sa che ce
  n'è già una in coda. L'impronta resta valida finché l'evidenza non cambia. Spesa piccola,
  con permesso amministratore: bassa.
- **Proposta di correzione**: in `Coda.accoda`, per le azioni senza run, trattare l'azione
  come chiave di unicità (per esempio `chiave = run or f"azione:{azione}"` per
  `lezioni_proponi`), con la stessa prenotazione di A2 MEDIA-1. Seconda richiesta →
  `LavoroGiaInCorso`.

### [BASSA-2] Il registro attività non dice quale lezione è stata accettata, rifiutata o tolta, né quale take è stato scelto
- **Dove**: `app/servizi/azioni.py:175-190` (`_dettagli_registro` copia solo `versione`,
  `battuta`, `nome`, `documento`, `titolo`, `versione_attesa`).
- **Voce della checklist**: estesa 5 (registro attività); regola 6 di `CLAUDE.md`.
- **Scenario**: verificato con l'agente finto. Le righe di `lezioni_accetta` e
  `lezioni_rifiuta` hanno `dettagli: null`. Mancano l'id della lezione, il testo
  (eventualmente riscritto dall'utente), il motivo del rifiuto e il `remove_id` scelto con
  `LessonRemovalRequired`. Il registro dice solo «X ha accettato una lezione». Eppure quel
  testo entra nel prompt di tutti gli episodi. Lo stesso vale per `scegli_take` (la
  `scelta` `N=K` non c'è) e per `metadati_brano` (mancano la categoria e la licenza
  dichiarata). Il database dell'agente conserva `accettata_da`, ma il registro del
  controller è l'unica traccia append-only.
- **Proposta di correzione**: aggiungere a `_dettagli_registro` le chiavi `id`, `motivo`,
  `scelta`, `categoria`, `licenza`, e per `testo` lo sha256 (più i primi 400 caratteri per le
  lezioni). Passare a `chiudi_esito` anche `parametri_nascosti`, per scrivere `remove_id`
  nella riga della conferma.

### [BASSA-3] Sostituire un brano lascia licenza, parlato e trascrizione del brano vecchio
- **Dove**: `app/file/caricamenti.py:90-103` (giustamente non tocca il `.json`),
  `static/js/schermate/brani.js:14` e `:37-40`; nell'agente `src/music_meta.py:95-105` (la
  licenza non è legata allo sha256 del file).
- **Voce della checklist**: estesa 1 (sostituzione che non tocca `.json`).
- **Scenario**: l'amministratore sostituisce `intro/Sigla.mp3` con un brano diverso.
  `Sigla.json` resta, quindi il brano nuovo risulta «Licenza dichiarata» da chi ha
  dichiarato quella del vecchio, e con il parlato e la trascrizione del vecchio. La
  trascrizione entra nel prompt dei copioni. La pagina invece dice «Dichiara la licenza
  prima di usarlo» (`brani.js:40`) e la nota della sostituzione parla solo degli episodi
  già montati. Il registro ha `sostituisci_brano`, ma il brano nuovo passa per «con
  licenza» senza che nessuno l'abbia dichiarata. Non porta conseguenze di sicurezza in
  senso stretto, ma è un'attestazione che non corrisponde al vero.
- **Proposta di correzione**: nella nota della sostituzione dei brani, dire che licenza,
  parlato e trascrizione restano quelli del file vecchio. Dopo una sostituzione, aprire i
  dettagli del brano e chiedere di riconfermare la licenza. Proporre all'agente (con
  l'utente) di salvare lo sha256 del file accanto alla licenza e mostrare «licenza da
  confermare» quando cambia, come per il consenso delle voci.

### [BASSA-4] Anteprime dei take e azioni brevi lente: un processo dell'agente per richiesta, senza limite
- **Dove**: `app/api/correzioni.py:110-125` (ogni richiesta di anteprima, compresa ogni
  richiesta `Range` del lettore, rilancia `retake.py --list N [--with-music]`),
  `app/api/correzioni.py:92-107`, `app/agente/tabella.py:235` (`forma_onda`, 900 s) e
  `:251` (`ascolta_take`, 900 s), entrambe con permesso `leggere`; `rimonta` (`:259`, 900 s)
  gira nella richiesta e non nella coda.
- **Voce della checklist**: 3 (tempo e memoria) / estesa 3.
- **Scenario**: chi ha solo `leggere` apre il passo 7 e ascolta i take. Ogni `<audio>` con
  `?musica=1` e ogni salto nella barra (una richiesta `Range`) lanciano un processo Python
  dell'agente. La prima volta il processo monta anche l'anteprima con il sottofondo
  (ffmpeg), con 900 s di timeout. Uno script con lo stesso utente può aprire decine di
  richieste in parallelo: non c'è un limite ai processi brevi contemporanei. Con uvicorn
  reale le letture non si fermano quando il client chiude (A2 NOTA-1), quindi i processi
  restano vivi e tolgono CPU a una produzione in corso. Le anteprime sono in cache su disco
  (`src/retake.py:369-372`), per questo il danno è solo un disservizio.
- **Proposta di correzione**: un semaforo per i comandi brevi in `esecutore.esegui`
  (globale, per esempio 4, e per utente, per esempio 2) con risposta `CodaPiena` o `503`
  quando è pieno. In `anteprima_take`, una cache di pochi secondi della `take_list` per
  `(run, n, musica)`, così le richieste `Range` dello stesso ascolto non rilanciano
  l'agente. In alternativa, risolvere il percorso alla prima richiesta e servire le
  successive senza richiamare `--list`.

### [BASSA-5] nginx d'esempio a 300 s, mentre rimontaggio, forma d'onda e anteprime con musica arrivano a 900 s
- **Dove**: `deploy/nginx-vps.conf:29` (`location /`, `proxy_read_timeout 300s`), contro
  `DEPLOY.md:129` (aggiornato in A5: «il rimontaggio e la forma d'onda fino a 900 s») e
  `app/agente/tabella.py:235,251,259`.
- **Voce della checklist**: estesa (deploy); era fra le cose «Da verificare» di A4 (NOTA-3).
- **Scenario**: un rimontaggio lungo supera i 300 s. nginx risponde 504 e chiude. Il
  controller finisce comunque il `mix.py` (azione protetta) e scrive il registro, ma
  l'interfaccia mostra un errore. Un secondo clic su «Rimonta» trova `RunBusy`, oppure
  rimonta di nuovo appena finisce. È solo un disservizio.
- **Proposta di correzione**: in `nginx-vps.conf` aggiungere una `location` per
  `^/api/episodi/[^/]+/(rimontaggio|forma-onda|retake/)` con `proxy_read_timeout 960s`, o
  portare a 960 s quella generale. Dire lo stesso in DEPLOY §3 per il proxy del CMS.

## Note

- **NOTA-1** (estesa 4): `impronta_retake` (`app/servizi/stima.py:31-37`) non contiene
  `run` né la versione approvata. Un'impronta vista per la battuta 5 di un episodio vale
  per la battuta 5 di un altro, se coincidono voce, caratteri e costo. In pratica la spesa
  è identica a quella vista, quindi non è un problema. Per coerenza con la produzione si
  può aggiungere `run` (dai parametri) e `approved_version` (se `retake.py --dry-run` lo
  dà).
- **NOTA-2** (estesa 5): `lezioni_proponi` parte anche **senza costo** (`costo_obbligatorio=False`),
  e la riga del registro resta senza costo. È una decisione esplicita (A5.md), mostrata nel
  dialogo («Costo non noto»), ed è un'eccezione alla regola di A2 BASSA-7. Va scritta in
  DEPLOY §9: impostare i prezzi del modello delle lezioni in `config.yaml` prima della
  produzione.
- **NOTA-3** (estesa 1): `esistenti_con_chiave` cerca i doppioni solo fra le estensioni per
  cui il controller sa controllare la firma (`app/file/caricamenti.py:99`). Se un giorno
  `audio.accepted_extensions` comprende un formato in più (per esempio `.aac`), un
  `Mario Rossi.aac` già in libreria non fa scattare `FileEsistente` quando si carica
  `Mario Rossi.wav`. Restano due file con lo stesso nome e la voce diventa «Doppione»
  (inutilizzabile). Basta confrontare con tutte le estensioni di `accepted_extensions`
  (sempre escluse `.json/.lock/.tmp`).
- **NOTA-4** (estesa 1): un file voce con un nome di sole cifre (`123.wav`) si carica, ma
  l'agente non lo risolve mai (un numero non vale come nome, `src/voice_prep.py:164`). Si
  può rifiutare al caricamento.
- **NOTA-5** (estesa 3): il campione di una voce si ascolta e si salva con il solo
  `leggere`. Il contratto lo ammette («solo agli utenti autenticati»), ma una registrazione
  di 10-30 s basta a clonare la voce altrove. Valutare un permesso dedicato (o
  `voce_consenso`) per il campione, e una riga di registro per gli scaricamenti.
- **NOTA-6** (estesa 6): `_senza_percorsi` (`app/api/avanzate.py:103-114`) maschera solo
  `detail`, e un percorso con spazi solo fino al primo spazio (verificato:
  `/srv/Mario Rossi/agente/config.yaml` → `… Rossi/agente/config.yaml`). L'agente
  sostituisce già la cartella del progetto con `.` e la home con `~`, e la pagina è solo
  per l'amministratore. Si può mascherare fino alla fine del token che contiene `/`.
- **NOTA-7** (estesa 7): il confronto mostra a tutti gli utenti `to.instruction`, la
  richiesta scritta da chi ha chiesto la revisione (`confronto.js:98`). Era già così
  nell'elenco delle versioni del passo 4. Il contratto lascia la decisione all'adattatore:
  va scritta.

## Verificato e a posto

- **1. Path traversal**:
  - nome di voci e brani: `nome_caricato` rifiuta `/`, `\`, `..`, controllo, `Cf`, punto o
    trattino iniziale, più di 200 byte, `.json/.lock/.tmp`;
  - estensioni solo dall'intersezione fra `accepted_extensions` e le firme note;
  - categoria da un elenco chiuso; cartella della libreria da `settings_show` con
    `cartella_libreria` (niente `/`, `..`, punto iniziale) e aperta con `O_NOFOLLOW`;
  - download di campioni e brani solo per il `file` / `name+extension` elencato
    dall'agente, ricontrollato con `controlla_nome`, mai `.json/.lock/.tmp`;
  - anteprime dei take: `apri_percorso_relativo` esige il prefisso `output/<run>/` (run già
    validata), al massimo 6 componenti, niente `.`/`..`/nascosti/`\`/controllo, ogni
    cartella con `O_NOFOLLOW` a partire dal descrittore della run, file finale con
    `O_NOFOLLOW` e solo file regolari (test con link e `..` in `tests/test_a5.py`).
- **2. Sottoprocessi**: nessun nuovo punto di lancio. Le nuove azioni sono in tabella, con
  valori `--opz=valore`. Le indicazioni dei retake usano `SCELTA_RIPETUTA` sulla whitelist,
  `--diff` interi, `--points` da un elenco chiuso, `--at` da regex, `--choose` costruito dal
  controller con interi. `--remove-id` solo da gettone e in `FLAG_CONFERMA`.
  `verifica_tabella` invariata e superata.
- **3. File non fidati**: limiti in streaming (voci 20 MB, brani 50 MB) prima di scrivere
  oltre il limite, `Content-Length` controllato prima di leggere. Firma dei primi 16 byte
  per formato. I file audio si servono con il tipo da elenco chiuso e `nosniff`: un
  poliglotta `RIFF…WAVE`/HTML non diventa una pagina. `CARICAMENTI` aggiornato con le sole
  cinque rotte. Fuori da queste il corpo resta JSON e al massimo 256 KB, con il percorso
  già decodificato da Starlette (`%69ntro` non aggira niente).
- **4. Testo nei prompt**:
  - lezioni: permesso `lezioni` (solo amministratore), `--by` dall'ingresso, `--text` al
    massimo 400 caratteri. L'agente rifiuta righe che iniziano con `#` o `---`, caratteri
    di controllo e temi vietati (costi, approvazione, consenso), con un tetto di 4000
    caratteri in tutto. Una lezione con a capo arriva intatta all'agente, che la valida;
  - il motivo del rifiuto non entra nei prompt;
  - nome pronunciato: spazi compattati e controllo dei caratteri nell'agente;
  - trascrizione dei brani: solo amministratore, 500 caratteri.
- **5. Tag dei retake**: nessun testo libero. La tabella usa `INDICAZIONI_RETAKE` (uguale
  al contratto), l'interfaccia non ha il campo «con parole tue», «Pronuncia diversa» porta
  a «Cosa dice» (nuova versione, quindi nuova approvazione). Permesso `produrre`, `--by`.
  Stima con impronta rifatta prima di accodare, `StimaCambiata` se diversa, costo
  dell'agente nel registro; un retake per run alla volta. «Usa il take» richiede
  `episodio_modifica`. `MusicChanged` del rimontaggio passa dal gettone di A2.
- **6. Segreti**: `settings_show` elenca solo campi scelti (mai chiavi né nomi delle
  variabili). Diagnostica solo con `diagnostica`, `detail` mascherato, chiavi vietate tolte
  (`ffmpeg_path`). `preview` resta fra le chiavi tolte. L'anteprima del documento torna
  come `anteprima` solo se non contiene la cartella dell'agente o la home e non è un
  percorso. `/api/io` aggiunge solo le estensioni.
- **7. Lock e temporanei**: temporaneo `.<nome>.<hex>.tmp` con `O_EXCL|O_NOFOLLOW`, 0600,
  tolto in ogni errore. Gettone `FileEsistente` controllato senza consumarlo, consumato
  subito prima del rename, legato a utente, libreria (`voci`, `musiche/<categoria>`) e
  chiave: un gettone dei documenti non sostituisce una voce. Sostituzione rifiutata con un
  lavoro in corso, controllata anche al momento del rename. Non si tocca mai
  `.json/.lock/.tmp`.
- **8. Garanzie di dominio** (salvo MEDIA-1):
  - consenso dalla scheda solo con dialogo, sha256 e revisione mostrati, non offerto se
    revocato;
  - `stima.js` invariato;
  - nessuna clonazione nel caricamento: la voce si clona alla prima produzione, dopo la
    stima;
  - nomi di voce mai indovinati, perché il campione si cerca per nome esatto nell'elenco;
  - nessun retry di clonazione nuovo.
- **Estesa 7 · Interfaccia**:
  - tutte le pagine nuove usano `h()`/`textContent`; nessun `innerHTML`, `eval`, `style`
    o gestore inline (anche da `tests/test_interfaccia.py`);
  - `src` degli `<audio>` costruiti con `seg()` o dal controller, CSP `media-src 'self'`;
  - «Apri il file» con `rel=noopener`;
  - costi visibili prima di spendere: retake sul pulsante e nel dialogo, proposte di
    lezioni nella pagina e nel dialogo (anche «costo non noto»), rimontaggio dichiarato
    gratuito;
  - pulsante «Genera» disattivato finché la stima non corrisponde alla scelta corrente.
- **Estesa 8 · `/api/io`**: espone solo le estensioni ammesse (documenti e audio), a utenti
  autenticati; nessun percorso né impostazione.

## Da verificare nelle prossime fasi

- Nel browser, a mano: confronto, Voci (in particolare «Nuova voce» dopo la correzione di
  MEDIA-1), Musiche, Documenti, passo 7 (forma d'onda, «Come è detta», «Cosa dice»),
  Lezioni, Impostazioni, Diagnostica (A5.md, «Non provato nel browser»).
- Con l'agente vero: `LessonRemovalRequired` con id che sono stringhe (A1 NOTA-8),
  `--with-music` su una run con sottofondo, e i tempi reali di `retake.py --list` per
  dimensionare il semaforo di BASSA-4.
- Nell'agente (da chiedere all'utente): flag esplicito per il consenso dopo una revoca
  (C7-sicurezza-3 BASSA-1) e licenza dei brani legata allo sha256 (BASSA-3).
- Sulla VPS: timeout del proxy (BASSA-5) e `client_max_body_size` per rotta (A4 BASSA-4),
  ora che le rotte binarie sono cinque.
