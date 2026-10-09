# A2 · Piano: API del flusso principale

> 2026-10-09. Rotte da A0 §8 (fase A2). Ogni punto qui sotto ha almeno un test; i punti
> **K1–K24** sono i controlli rimandati dalla revisione di A1 ("Da verificare nelle
> prossime fasi"), da spuntare nel resoconto `A2.md`.

## Moduli

- `app/api/episodi.py`: Archivio, crea episodio, stato, passi 1–5 e 7 (rotte → tabella).
- `app/api/documenti.py`, `app/api/voci.py`, `app/api/musiche.py`: librerie usate dal flusso.
- `app/api/lavori.py`: stesura, revisione, produzione, interruzione, coda, eventi (SSE e polling).
- `app/lavori/coda.py`: coda a un worker; `app/lavori/eventi.py`: lettura di `_eventi.jsonl`.
- `app/file/nomi.py` (nomi e normalizzazione), `app/file/caricamenti.py`, `app/file/download.py`.
- `app/servizi/azioni.py`: protezione dalla disconnessione.

## Caricamenti (documenti; voci e brani in A5 con lo stesso codice)

- **K1** Nome da `X-LIL-Nome-File`: percent-decoding UTF-8 **stretto**, normalizzazione NFC;
  rifiutati `/ \ ..`, caratteri di controllo, caratteri invisibili o bidirezionali
  (categoria Unicode `Cf`), `.` o `-` iniziali, spazi ai bordi, più di 150 caratteri.
- **K2** Estensione in elenco (documenti: `.pdf .docx .txt .md .markdown .rst .text`), mai
  `.json .lock .tmp`.
- **K3** Firma dei primi byte: `%PDF-` per `.pdf`, `PK\x03\x04` per `.docx`, UTF-8 valido e
  senza NUL per i testuali (controllato su tutto il file mentre arriva). Per l'audio (A5):
  `RIFF…WAVE`, `ID3`/frame MP3, `ftyp`, `fLaC`, `OggS`, EBML; un `ffconcat` o una playlist
  travestiti non passano.
- **K4** Limite in streaming **prima** di scrivere su disco: `Content-Length` oltre il limite
  → 413 subito; senza `Content-Length`, o con un valore falso, si contano i byte mentre
  arrivano e ci si ferma al primo byte oltre. Limite dei documenti = `documenti.max_bytes`
  dell'agente.
- **K5** Temporaneo `.<nome>.<casuale>.tmp` nella cartella della libreria, aperto con
  `O_CREAT|O_EXCL|O_NOFOLLOW`, 0600, tramite il descrittore della cartella (aperta con
  `O_DIRECTORY|O_NOFOLLOW`: cartella della libreria che è un link simbolico → errore);
  `fsync` e `rename` atomico; temporaneo cancellato su errore, rifiuto o disconnessione.
- **K6** Sostituzione: un nome che collide (confronto dei comandi: senza estensione,
  minuscole, senza accenti, `_`/`-` come spazio) → 409 `FileEsistente` con un gettone legato
  a utente, libreria e **nome normalizzato**; il file si rimanda con `X-LIL-Gettone`. Un
  gettone di un altro file, di un'altra libreria o di un altro utente non vale; i gettoni dei
  file non valgono su `/api/conferme`. Permesso di caricare in quella libreria; nel registro
  (`carica_documento` / `sostituisci_documento`, nome, byte, sha256); il vecchio file con
  un'altra estensione si toglie.
- **K7** Mai una sostituzione mentre un lavoro lungo è in corso (409 `LavoroGiaInCorso`).
- **K8** (A5, voci) Dopo la sostituzione di una registrazione il consenso risulta `superato`:
  lo dice l'agente; test in A5.

## Download

- **K9** Si serve solo un nome elencato dall'agente: `episode_info` (`file_finale.nome` e
  `file[]` con `interno: false`) per gli episodi, `documents_list` per i documenti. Mai i
  `.json` accanto a voci e brani.
- **K10** Apertura dentro `output/<run>` o `documenti/` tramite il descrittore della cartella
  (`O_DIRECTORY|O_NOFOLLOW`) e `O_NOFOLLOW` sul file; solo file regolari; nome senza
  separatori. Un link simbolico al posto del file → 404.
- **K11** `Range`: un solo intervallo (`bytes=a-b`, `bytes=a-`, `bytes=-n`); intervallo
  invertito o oltre la fine → 416 con `Content-Range: bytes */N`; sintassi non valida o più
  intervalli → file intero (200, come da RFC 9110); numeri limitati, niente overflow.
- **K12** `Content-Disposition` con nome sicuro (ASCII di ripiego senza `"`, `\`, CR/LF, più
  `filename*=UTF-8''…`), `inline` per l'ascolto, `attachment` con `?scarica=1`;
  `Content-Type` dall'estensione; `nosniff`; permesso `leggere`.

## Eventi (SSE e polling)

- **K13** `_eventi.jsonl` aperto con `O_NOFOLLOW` dentro la run validata (cartella reale di
  `output/`), solo righe complete (terminate da `\n`), righe oltre 64 KB scartate, al più
  20.000 righe; ogni evento ripulito con `Pulitore`.
- **K14** `Last-Event-ID` e `?dopo=N` interi ≥ 0 con un tetto (fullmatch ASCII); un file
  svuotato da una nuova produzione si riconosce (dimensione minore) e si riparte da zero.
- **K15** Al più 4 connessioni SSE aperte per utente (429 → la UI usa il polling);
  heartbeat ogni 15 s; ogni ciclo controlla la disconnessione; lo stream si chiude da solo
  quando sulla run non ci sono lavori e l'ultimo evento è finale, o dopo 2 ore; nessun
  task orfano (test: dopo la chiusura il contatore torna a zero).

## Coda dei lavori

- **K16** Un solo worker, un lavoro per run (409 `LavoroGiaInCorso`), coda limitata (503
  `CodaPiena`).
- **K17** Accodare rispetta: permesso dell'azione, `solo_da_gettone`, validazione degli
  argomenti **prima** di accodare (400/404 subito), `--by` dall'ingresso.
- **K18** Registro con l'id del `lavoro`: una riga all'accodamento (chi ha lanciato una spesa)
  e una all'esito.
- **K19** Esiti 3 dei lavori lunghi con gettone dell'utente che ha lanciato il lavoro:
  `MusicChanged` (→ rilancio con `--accept-changed-music` dalla conferma, di nuovo in coda),
  `CloneOutcomeUnknown` (→ `ritenta_clonazione`), `ConsentRequired` (la UI registra i
  consensi e rilancia).
- **K20** Produzione: `estimate.py` subito prima di accodare, `cost_usd`/`cost_eur` nel
  registro; il costo non arriva mai dal browser.
- **K21** Interruzione: chi interrompe deve avere il permesso dell'azione del lavoro; un
  lavoro in attesa si toglie dalla coda; uno in corso riceve **un solo** SIGTERM (al
  processo, come dice il contratto); una seconda richiesta risponde "interruzione già
  richiesta"; il controller non manda mai SIGKILL a una produzione (né durante
  `preparazione_voci` né dopo). Allo spegnimento del servizio: un SIGTERM al lavoro in corso
  e attesa.
- **K22** `in_corso.istruzione` di una revisione: **si mostra** a tutti gli utenti della run
  (diventa comunque l'`instruction` della versione, visibile a tutti nell'elenco versioni).
  Decisione registrata; da rivedere se serve riservatezza fra autori.

## Azioni brevi e disconnessione

- **K23** Un'azione breve che registra (approvazione, consenso, revoca, modifica…) è
  protetta con `asyncio.shield`: se il browser si chiude e la richiesta viene cancellata,
  il comando dell'agente **finisce** (niente SIGTERM) e la riga del registro si scrive.
  Test: richiesta cancellata a comando partito → processo non fermato, riga presente.
  Le azioni di sola lettura invece si fermano (il processo riceve SIGTERM).
- **K24** Le letture restano annullabili; le scritture no.

## Rotte (da A0 §8)

Archivio, stato, crea episodio; documenti (elenco, uno, anteprima, file, caricamento);
configurazione (lettura, modifica, documento); voci (elenco, consenso: stato, registra,
revoca); musiche (elenco, scelta); copione (vista, stesura, revisione, battuta, approva,
ritira, versioni, ripristino); stima; produzione, interruzione, eventi (SSE e polling),
lavori; episodio e file.

## End-to-end

Copia temporanea dell'agente (`src/`, `tools/`, `prompts/`, `VERSION`, i due finti di
`tests/`; la `.venv` è un link a quella dell'agente), `config.yaml` con provider e client
finti come in `test_full_flow`, controller in demo. Via HTTP: caricamento del documento →
episodio → cast → intro → stesura in coda → modifica di una battuta → approvazione → stima →
consensi → produzione interrotta durante la battuta 3 (SSE) → ripresa → episodio → download
con Range → Archivio "pronti" → registro con approvazione, consensi, produzioni e costo.
