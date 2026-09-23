# podcast-agent

Trasforma un documento (PDF, DOCX, TXT, MD) in un episodio podcast completo:
Claude analizza il documento e scrive il copione, il provider vocale clona le
voci a partire dalle registrazioni che gli fornisci, e l'agente monta l'audio
in un unico file mp3.

---

## Nota etica — leggere prima di usare

**L'agente assume che ogni registrazione presente in `voci_disponibili/`
appartenga a una persona che ha dato il proprio consenso esplicito alla
clonazione della sua voce.** L'agente non ha modo di verificarlo e non lo
verifica: la responsabilità di raccogliere e conservare quel consenso è di chi
usa questo strumento.

Prima di aggiungere un file in quella cartella, assicurati che:

- la persona sappia che la sua voce verrà clonata e usata per generare parlato
  che non ha mai pronunciato;
- sappia in che contesto verrà pubblicato il risultato;
- possa revocare il consenso — in quel caso vanno cancellati sia il file audio,
  sia la voce clonata sul provider, sia la riga corrispondente in
  `voices_registry.json`.

Le voci clonate vengono create come **private** sul provider (`visibility:
private` in `config.yaml`). Verifica comunque i termini di servizio del
provider che usi: i file audio di riferimento vengono caricati sui suoi server.

**Le musiche in `musiche/` devono essere tue, royalty-free o con una licenza
che permetta la pubblicazione.** L'agente non lo verifica — lo rende visibile:
ogni volta che usa un brano senza `licenza` nei metadati te lo dice nel
riepilogo. Se un'intro o un'outro contiene voci di persone reali, vale lo
stesso principio del consenso scritto sopra per le voci clonate: quelle
persone devono sapere dove verrà pubblicata la registrazione.

---

## Installazione

Il progetto si installa allo stesso modo su **macOS, Windows e Linux** con il
solo `pip`: non servono pacchetti di sistema, nemmeno per la musica.
`imageio-ffmpeg` (fra le dipendenze obbligatorie) porta con sé un eseguibile
ffmpeg pronto all'uso per la piattaforma corrente.

**macOS / Linux:**

```bash
cd podcast-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env    # poi riempi le due API key
```

**Windows (PowerShell):**

```powershell
cd podcast-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env  # poi riempi le due API key
```

Poi, su qualunque piattaforma, verifica che sia tutto a posto:

```bash
.venv/bin/python tools/doctor.py     # Windows: .venv\Scripts\python tools\doctor.py
```

Controlla versione di Python, dipendenze installate, dove ha trovato ffmpeg
(o se manca), i filtri ffmpeg necessari al mix delle musiche, e che le
cartelle di `config.yaml` esistano. Stampa un JSON coi controlli ed esce con
`1` se manca qualcosa di obbligatorio — usalo come primo passo dopo
l'installazione, e ogni volta che qualcosa non torna.

**La `.venv` non va copiata da una macchina all'altra**: contiene percorsi e
binari specifici di quella piattaforma (compreso l'eseguibile ffmpeg di
`imageio-ffmpeg`). Su una macchina nuova, ricreala con `python -m venv .venv`
e reinstalla con `pip install -r requirements.txt`.

Se preferisci un ffmpeg di sistema che hai già (es. installato col tuo
package manager) invece di quello del pacchetto pip, va bene lo stesso:
`src/ffmpeg_tool.py` lo usa come ripiego se lo trova nel PATH.

### Chiavi API

| Variabile | Obbligatoria? | Dove si ottiene | A cosa serve |
|---|---|---|---|
| `FISH_AUDIO_API_KEY` | **sì** | <https://fish.audio/app/api-keys/> | clonazione voci + sintesi |
| `ANTHROPIC_API_KEY` | solo per `run.py` | <https://console.anthropic.com/settings/keys> | far scrivere il copione all'API |

**La chiave Anthropic serve solo alla CLI classica.** Con il subagent
`@podcast-agent` il copione lo scrive l'agente stesso in conversazione, quindi basta la
chiave di Fish Audio. Attenzione: `ANTHROPIC_API_KEY` è una chiave API a consumo,
**diversa dall'abbonamento Claude Pro/Max** — averlo non la include.

Vanno nel file `.env` (non committarlo). Il nome della variabile per il
provider vocale è configurabile: `providers.<nome>.options.api_key_env`.

---

## I documenti di partenza

Metti in `documenti/` i file da trasformare in episodio:

```
documenti/
├── report-q3.pdf
└── nota-interna.docx
```

- Formati letti: **PDF**, **DOCX**, **TXT**, **MD** (`.markdown`, `.rst`).
- Se il file è lì dentro, **basta il nome**: `python run.py report-q3.pdf`, o
  `@podcast-agent trasforma report-q3.pdf in un episodio...`. Vale sia per la
  CLI sia per il subagent.
- Un percorso completo continua a funzionare da qualunque cartella: `documenti/`
  è una comodità, non un vincolo.
- La cartella è in `.gitignore` (resta solo il `.gitkeep`): i documenti sono tuoi
  e non finiscono nel repo.
- Si sposta da `config.yaml` → `paths.documents_dir`.

I PDF **scansionati** (solo immagini) non funzionano: serve testo selezionabile
o un passaggio OCR. L'errore te lo dice esplicitamente, non degrada in silenzio.

---

## Le registrazioni delle voci

Metti in `voci_disponibili/` **un file per persona**, chiamato con il nome che
userai nello script:

```
voci_disponibili/
├── Mario Rossi.wav
├── Giulia Bianchi.wav
└── Luca Verdi.mp3
```

- Il nome del file, senza estensione, **è** il nome dello speaker.
- Formati accettati: `.wav`, `.mp3`, `.m4a`, `.flac`, `.ogg`, `.opus`, `.webm`
  (modificabile in `config.yaml` → `audio.accepted_extensions`).
- I file in formati diversi vengono ignorati e segnalati, non fatti passare
  per errore.
- Per il cloning conviene una registrazione pulita di **10–30 secondi**: voce
  sola, senza musica, senza rumore di fondo, senza riverbero.

L'agente **non inventa nomi**: se scrivi un nome che non corrisponde a nessun
file ti mostra le corrispondenze possibili e ti chiede quale intendevi, invece
di indovinare.

### Cosa registrare: la gamma di pitch, non solo la voce

Il cloning istantaneo non impara "come suona una voce" in astratto: riproduce
la **gamma prosodica presente nel campione**. Se il campione è una lettura
piana, il modello ha sentito solo quella e in sintesi non andrà oltre — per
quanto lungo sia il campione. Una lettura monocorde di due minuti insegna meno
di trenta secondi con intonazione viva.

È la leva più forte sulla naturalezza della dizione, molto più dei parametri di
sintesi in `config.yaml`, e costa solo una ri-clonazione.

Quando registri:

- **Frasi con escursione tonale vera**, non una lettura uniforme. Il tono deve
  salire e scendere davvero fra una frase e l'altra.
- **Due o tre domande con parola interrogativa** (*chi, come, quando, dove,
  perché, quanto*), dette come le diresti parlando. In italiano queste domande
  **scendono** alla fine: quello che le marca è il **picco sulla parola
  interrogativa**. È quel picco che il modello deve aver sentito.
- **Una o due domande sì/no** a ordine dichiarativo (*"E il prezzo sale?"*).
  Queste invece hanno il **rialzo finale**: è un contorno diverso dal
  precedente, e se non c'è nel campione il modello non lo produce.
- **Qualche frase con enfasi o sorpresa**, per dare al modello degli estremi:
  se nel campione non c'è mai un picco, in sintesi non ci sarà mai.
- Il resto vale come prima: voce sola, niente musica, niente rumore di fondo,
  niente riverbero.

Se le domande di un episodio escono piatte, prima di toccare i parametri di
sintesi guarda il campione: quasi sempre è lì che quel contorno non esiste.

### Se la dizione non convince

Tre leve, in ordine di forza: **il campione**, poi **il modello TTS**, poi **le
tag** nel testo. Quasi sempre è la prima.

Il modello predefinito per l'italiano è `drama-3-preview` e non `s2.1-pro`, per
una ragione precisa: `s2.1-pro` chiude **in salita** tutto ciò che finisce con
un punto interrogativo: è il contorno interrogativo inglese, e su una domanda
italiana con parola interrogativa (*chi, come, dove, perché*) è sbagliato —
quelle in italiano fanno il picco sulla parola interrogativa e poi **scendono**.
`drama-3-preview` distingue i due casi. In cambio sulle domande sì/no tende a
non fare il rialzo finale, che lì invece serve: è il caso in cui conviene
marcare la battuta con una tag (`[incalzante]`, `[enfasi sulla domanda]`).

`drama-3-preview` è un modello **preview** e può cambiare resa o sparire: il
fallback è rimettere `s2.1-pro` in `config.yaml`, senza nessun'altra modifica.

Il ragionamento completo, i numeri che lo sostengono e il metodo per rimisurare
stanno in **`LEGENDA.md`**, sezione *"La dizione italiana: cosa abbiamo
imparato"*.

---

## Le musiche (intro, outro, sottofondo) — opzionali

Tre elementi indipendenti, tutti facoltativi. Un episodio senza musiche esce
esattamente come prima di questa funzione.

```
musiche/
├── intro/          # apre l'episodio: può avere voci e musica insieme
├── outro/          # chiude l'episodio: idem
└── sottofondo/     # SOLO musica, sotto la parte parlata
```

- Il percorso si configura in `config.yaml` → `paths.music_dir` (default
  `./musiche`). Se la cartella o una sottocartella non esiste, quella
  funzione è semplicemente non disponibile — non è un errore.
- Ogni sottocartella può contenere più brani: il nome del file, senza
  estensione, è il nome del brano. Formati accettati: gli stessi di
  `audio.accepted_extensions`.
- La cartella è in `.gitignore`, come `documenti/` e `voci_disponibili/`:
  restano solo i `.gitkeep`.

### Metadati (facoltativi, accanto a ogni brano)

Accanto a `musiche/intro/Sigla LIL.mp3` può esserci `musiche/intro/Sigla
LIL.json`:

```json
{
  "licenza": "Composta da X per LIL, uso libero",
  "parlato": true,
  "trascrizione": "Benvenuti a LIL Podcast, il podcast che..."
}
```

- `licenza`: se manca non è un blocco, ma un avviso — vedi la nota etica in
  cima a questo file.
- `parlato` e `trascrizione`: hanno senso solo per intro e outro. Se l'intro
  ha `parlato: true`, il copione non ripete quello che l'intro dice già (vedi
  `prompts/copione.md`). Per il **sottofondo**, `parlato: true` è un errore
  di dominio: il sottofondo deve essere solo musica.

### Come si sceglie

Le scelte di un episodio stanno in `output/<run>/musiche.json`, sulla
falsariga di `voci.json`:

```json
{ "intro": "Sigla LIL", "outro": null, "sottofondo": "Piano calmo" }
```

- `python run.py` chiede, per ciascuna categoria non vuota, quale brano usare
  (o nessuno) — **prima** di scrivere il copione, perché se l'intro ha
  parlato il copione deve saperlo.
- Con il subagent, `tools/music_set.py --run-dir D --intro NOME ...` fa lo
  stesso, senza spendere e senza richiedere approvazione: cambiare musica è
  un'operazione libera, che non tocca il copione.
- `tools/music_list.py` elenca i brani disponibili (con durata, licenza sì/no,
  parlato sì/no) e risolve i nomi con la stessa regola delle voci: nessuna
  corrispondenza parziale indovinata.

### Il mix è un passo separato, dopo il montaggio della voce

```
segmenti (cache) → montaggio voce (wav) → mix (intro + voce con sottofondo + outro) → mp3 finale
```

Il montaggio della voce (`_tempi_turni.json`, la cache dei segmenti, i
retake) **non cambia**: resta riferito al master di sola voce. Il mix è un
passo in più, fatto da `src/music_mixer.py`, e produce anche
`output/<run>/musiche_usate.json`: nome, percorso, sha256, durata e licenza
di ogni brano usato, i parametri di mix effettivamente applicati, e
`voice_offset_seconds` — dove inizia la sezione parlata nel file finale
(durata dell'intro più la pausa, o 0 senza intro).

Se rimonti un episodio e lo sha256 di un brano già registrato in
`musiche_usate.json` è cambiato (il file in `musiche/` è stato sostituito),
`tools/mix.py` si ferma ed elenca cosa è cambiato: serve il flag esplicito
`--accept-changed-music` per procedere. Un episodio vecchio non deve cambiare
sigla in silenzio.

**Cambiare o togliere una musica di un episodio già montato** non richiede
risintesi:

```bash
.venv/bin/python tools/music_set.py --run-dir output/<run> --sottofondo "Piano calmo"
.venv/bin/python tools/mix.py --run-dir output/<run>     # zero chiamate TTS
```

`tools/mix.py` rimonta la voce dai segmenti già in cache e rifà il mix. Se i
segmenti non ci sono più (episodio vecchio, o `--purge-segments`), si ferma
con un errore che spiega che serve una nuova sintesi.

### I minutaggi si riferiscono al file che ascolti

`tools/turn_at.py` accetta il minutaggio **del file finale** (quello con le
musiche, se ce ne sono), legge l'offset da `musiche_usate.json` e lo sottrae
prima di cercare il turno. Se l'istante cade nell'intro o nell'outro te lo
dice esplicitamente (`"in": "intro"` / `"in": "outro"`) invece di restituire
un turno a caso.

`retake.py` monta le anteprime di sola voce, come sempre — ma con
`--with-music`, se l'episodio ha un sottofondo, le monta anche con il
sottofondo sotto, mixato con gli stessi parametri: è lì che si giudica se un
take regge nel contesto sonoro vero.

### Requisiti

Il mix delle musiche richiede **ffmpeg** (decodifica, ricampionamento, loop,
dissolvenze, ducking, normalizzazione). `imageio-ffmpeg`, fra le dipendenze
obbligatorie del progetto, lo porta già pronto all'uso: non serve installare
niente di aggiuntivo. Se **nessuna** musica è scelta per un episodio,
l'ffmpeg del mix non entra mai in gioco: il montaggio resta quello di sempre.
Se **almeno una** musica è scelta e per qualche motivo ffmpeg non si trova
(vedi `tools/doctor.py`), il comando si ferma con un errore di dominio
**prima** di qualunque chiamata al provider vocale.

I parametri del mix (pause, dissolvenze, livello del sottofondo, ducking,
loudness target) stanno in `config.yaml` → sezione `music:`, ciascuno con un
commento su cosa fa e cosa succede alzandolo o abbassandolo.

---

## Uso

```bash
python run.py documento.pdf          # cercato in documenti/
python run.py ~/Scrivania/report.pdf # oppure un percorso qualsiasi
# oppure, e te lo chiede lui mostrandoti cosa c'è in documenti/:
python run.py
```

Il flusso è:

1. **Documento** — estrazione del testo (PDF/DOCX/TXT/MD). Un nome senza
   percorso viene cercato prima in `documenti/`.
2. **Numero di voci** — monologo, dialogo, tavola rotonda.
3. **Scelta delle voci** — fra quelle trovate nella cartella.
3-bis. **Musiche (se `musiche/` non è vuota)** — intro, outro e sottofondo,
   uno alla volta, o nessuno. Prima del copione: se l'intro ha parlato, chi
   scrive il copione deve saperlo.
4. **Scrittura del copione** — Claude analizza il documento e produce
   `conversazione.json`.
5. **Checkpoint di revisione (obbligatorio)** — lo script ti viene mostrato in
   forma leggibile. **Nulla viene sintetizzato senza un tuo OK esplicito.** Se
   chiedi modifiche, l'agente riscrive e ti richiede una nuova conferma.
6. **Preparazione voci** — clonazione, o riuso dal registro locale.
7. **Stima** — caratteri, durata (~150 parole/minuto, più intro/outro/pause se
   ci sono musiche), costo indicativo.
8. **Sintesi** — un segmento audio per turno di parola.
9. **Montaggio** — concatenazione con pause, export mp3; se sono state scelte
   musiche, il mix (vedi sopra) è l'ultimo passo prima dell'export.

---

## Uso come subagent Claude Code

Oltre alla CLI, il progetto è un **subagent di Claude Code**: invece di rispondere a una
sequenza di prompt, descrivi cosa vuoi e l'agente conduce la conversazione.

Apri Claude Code nella cartella `podcast-agent/` e scrivi:

```
@podcast-agent trasforma documento.pdf in un episodio con Mario Rossi e Giulia Bianchi
```

L'agente legge il documento, ti propone un taglio, conferma i nomi delle voci contro la
cartella, scrive il copione, **te lo mostra e aspetta il tuo OK**, ti dà la stima, clona
quello che serve, sintetizza e ti riporta cosa ha fatto. Puoi interromperlo, chiedere
modifiche al copione in linguaggio naturale, cambiare idea sugli speaker.

Il subagent **non riscrive la logica in Markdown**: richiama gli stessi moduli di `src/`
attraverso piccoli comandi in `tools/`. L'unica cosa che scrive lui è il copione — seguendo
`prompts/copione.md`, la stessa fonte che usa `run.py`, così il podcast suona uguale da
qualunque parte lo generi. Python lo **valida e lo persiste**, non lo genera.

| Comando | Cosa fa |
|---|---|
| `tools/doc_extract.py` | estrae il testo dal documento |
| `tools/voices_list.py` | elenca le voci, risolve i nomi (senza mai indovinare) |
| `tools/run_init.py` | crea `output/<data>_<titolo>/` |
| `tools/script_save.py` | valida e salva il copione scritto dall'agente |
| `tools/script_approve.py` | registra il sì dell'utente sul copione |
| `tools/music_list.py` | elenca le musiche disponibili, risolve i nomi |
| `tools/music_set.py` | sceglie intro/outro/sottofondo per la run (gratis) |
| `tools/voices_prepare.py` | riusa dalla cache o clona |
| `tools/estimate.py` | caratteri, durata (incluse le musiche), costo |
| `tools/synthesize.py` | sintesi + montaggio (+ mix, se ci sono musiche) |
| `tools/mix.py` | rifà solo il mix dalla cache, zero chiamate TTS |
| `tools/turn_at.py` | dal minutaggio del file finale alla battuta |
| `tools/retake.py` | nuovi tentativi su una battuta (`--with-music` per l'anteprima col sottofondo) |
| `tools/run_finalize.py` | `voci_usate.json` + copia del documento |
| `tools/doctor.py` | diagnosi dell'installazione (Python, dipendenze, ffmpeg) |

Ogni comando stampa un oggetto JSON su stdout e i messaggi umani su stderr, ed è
utilizzabile anche a mano:

```bash
.venv/bin/python tools/voices_list.py --resolve "mario rosi"
```

Gli exit code distinguono i casi che l'agente deve trattare diversamente:
`0` ok · `1` errore di dominio (correggi e richiama) · `2` errore del provider
(**fermati, non ritentare**) · `3` serve una risposta dall'utente.

I vincoli del progetto sono scritti come regole non negoziabili nel prompt dell'agente
(`.claude/agents/podcast-agent.md`): niente nomi di voce indovinati su corrispondenza
parziale, nessun retry sulla clonazione, e il promemoria sul consenso ogni volta che una
voce viene clonata per la prima volta.

### Il checkpoint di approvazione non è una regola d'onore

Nella CLI è il ciclo `while` di `run.py` a non lasciarti proseguire. Con il subagent quel
ciclo non c'è, quindi l'approvazione è uno **stato sul disco legato ai byte del copione**:

1. `script_save.py` salva ogni versione con `approved: null`;
2. dopo il tuo sì, `script_approve.py` registra l'approvazione **insieme allo sha256 del
   copione approvato**;
3. `voices_prepare.py` e `synthesize.py` — i due passi che spendono — confrontano
   quell'hash con il copione che stanno per usare, e si rifiutano di partire se non
   corrisponde.

Quindi un sì dato su una bozza non vale per la versione successiva, e riscrivere
`conversazione.json` a mano dopo l'approvazione fa scattare il blocco invece di passare
inosservato. C'è un `--no-approval-gate` per l'uso manuale e la CI: è esplicito nel
comando, quindi lo vedi se viene usato.

**`run.py` resta la CLI classica** per test, CI e uso non interattivo: il subagent è un
modo in più di lavorare, non una sostituzione.

### Lo storico delle revisioni

`conversazione.json` è sempre la versione corrente. Lo storico sta in
`_script_session.json`, nella stessa cartella: un array `versions[]` con **ogni bozza per
intero**, l'istruzione che l'ha generata e il suo sha256, più lo stato dell'approvazione.
Puoi rileggere e confrontare qualunque versione.

Per la CLI classica, che fa revisionare il copione all'API, la sessione ha anche un
secondo ruolo: ricostruire la conversazione fra un processo e l'altro. In quel caso il
contesto inviato contiene **solo l'ultima bozza**, non tutto `versions[]` — le bozze
scartate stanno su disco, dove non costano nulla, e non tornano a farsi pagare a ogni
revisione.

---

## Lezioni dagli episodi precedenti

Il progetto può imparare dal modo in cui correggi i copioni — ma **solo attraverso
lezioni che approvi tu esplicitamente**. Niente si modifica da solo.

Il ciclo, in tre passi:

1. **Raccolta (automatica, gratis).** A ogni `synthesize.py` riuscito viene scritto
   `output/<run>/_segnali.json`: quante revisioni sono servite, quali turni sono
   cambiati fra bozza e testo approvato, quali tag hai tolto/aggiunto/sostituito e
   dove, quali battute hai fatto rifare, e quali correzioni sono arrivate **dopo**
   aver sentito l'episodio (il segnale più forte, perché nasce dall'ascolto). Non
   c'è nessuna chiamata a modelli in questo passo, e un suo eventuale fallimento
   non blocca mai la sintesi.
2. **Aggregazione e proposta (`tools/lessons.py`).** `--evidence` mostra gli schemi
   che si ripetono su più episodi (deterministico, nessun costo). `--propose`
   manda **solo le prove aggregate** — mai i copioni interi — a Claude, che
   dichiara il costo prima di essere chiamato e propone al massimo due regole di
   stile, ciascuna con le prove che la giustificano. Le proposte finiscono in
   `prompts/_lezioni.json`, **non** in `lezioni.md`.
3. **Approvazione (sempre e solo tua).** `tools/lessons.py --accept ID` è l'**unico**
   punto del codice che scrive in `prompts/lezioni.md` — il file che `ScriptWriter`
   e il subagent leggono, dopo `copione.md`, quando scrivono un nuovo copione. Puoi
   riscrivere il testo della proposta prima di accettarla, o scartarla con
   `--reject ID --reason "..."` (il motivo resta nello storico, così la stessa
   proposta non ti viene riproposta identica). `tools/lessons.py --review` segnala
   le lezioni attive che non stanno funzionando (lo schema che dovevano correggere
   si ripresenta ancora); `--trend` mostra l'andamento degli indicatori per
   episodio, con l'avvertenza che non sono una misura di qualità — dipendono anche
   da quanto hai ascoltato con attenzione e da quanto era difficile il documento.

Senza `prompts/lezioni.md` (o con il file assente, come in un progetto appena
clonato) il comportamento è **identico a oggi**: nessuna sezione in più nel prompt.
In caso di conflitto fra una lezione e `copione.md`, vince sempre `copione.md`.

L'agente subagent può proporti di guardare `--evidence` dopo un episodio, ma non
propone mai lezioni di sua iniziativa: solo se glielo chiedi tu esplicitamente.

---

## Output: una cartella per episodio

```
output/2026-09-13_10-30_titolo-episodio/
├── titolo-episodio.mp3            # l'episodio finale (con le musiche, se scelte)
├── conversazione.json             # lo script effettivamente usato
├── voci_usate.json                # speaker -> voice_id, hash e data di clonazione
├── musiche.json                   # intro/outro/sottofondo scelti (se ci sono)
├── musiche_usate.json             # brani usati: hash, durata, licenza, voice_offset_seconds
├── documento_originale.pdf        # copia del documento di partenza
├── _tempi_turni.json              # durata e posizione di ogni turno NEL MASTER DI SOLA VOCE
├── _sintesi_log.json              # QUANDO l'episodio e' stato sintetizzato, con quale copione
├── _segnali.json                  # segnali per le lezioni (vedi "Lezioni dagli episodi precedenti")
└── segmenti/                      # un file audio per battuta: la cache
    ├── 3f9a1c0e5b7d2481_t1.wav
    ├── _voce_master.wav           # montaggio voce-sola, riletto dal mix (non e' l'output finale)
    ├── scelte.json                # quale take usare, per chi ne ha più d'uno
    ├── indice.json                # turno -> speaker, chiave, take, file
    └── anteprime/                 # take nuovi montati in contesto, da riascoltare
```

Ogni run è quindi riascoltabile e rigenerabile senza ambiguità su quali voci e
quale script siano stati usati. (Se la copia del documento non riesce — file
troppo grande, permessi — viene salvato `documento_originale.percorso.txt` con
il percorso di origine.)

### `segmenti/` resta, ed è la cosa che rende correggibile un episodio

I segmenti **non vengono più cancellati** dopo il montaggio. Il nome del file non
è la posizione del turno ma una chiave di contenuto: testo della battuta, voce,
formato e impronta di sintesi del provider (modello TTS e parametri). Da qui
discende tutto il resto:

- correggere una battuta costa **solo quella battuta**: le altre chiavi non
  cambiano, e i loro file vengono riusati così come sono;
- le battute non toccate restano **identiche**, il che conta più del costo: il
  TTS non è deterministico, e risintetizzare tutto rimetterebbe in gioco anche
  quelle venute bene;
- cambiare `tts_model` o un parametro in `config.yaml` invalida la cache da solo,
  senza che nessuno debba ricordarsi di svuotare una cartella.

Il montaggio invece è sempre integrale: rilegge tutti i wav grezzi e ri-codifica
l'mp3 da zero. Non si taglia e non si ricuce un mp3 esistente.

Due battute identiche dello stesso speaker restano due segmenti distinti: lo
stesso identico audio ripetuto si sente come un nastro, e un nuovo tentativo
sull'una non deve cambiare di nascosto anche l'altra.

Per liberare spazio serve chiederlo: `synthesize.py --purge-segments` (oppure
`run.py --purge-segments`). `--keep-segments` esiste ancora come alias accettato
ma non fa più niente: i segmenti si tengono sempre.

### Correggere una singola battuta

```bash
# quale battuta si sente al tre e quaranta NEL FILE CHE ASCOLTI?
.venv/bin/python tools/turn_at.py --run-dir output/<run> --at 3:42
```

Il minutaggio che passi è sempre quello del **file finale**: se l'episodio ha
un'intro, `turn_at.py` legge `voice_offset_seconds` da `musiche_usate.json` e
lo sottrae prima di cercare il turno. Se l'istante cade nell'intro o
nell'outro te lo dice esplicitamente, invece di restituire un turno a caso.
Senza musiche l'offset è 0 e il comportamento è quello di sempre.

Se il **testo** è sbagliato, si passa dal copione: si corregge la bozza, si
risalva con `script_save.py`, si rifà approvare (l'approvazione è legata allo
sha256, quindi decade da sola) e si rilancia `synthesize.py` (che rifà anche
il mix, se ci sono musiche). La stima dice in anticipo quanto resta davvero da
sintetizzare:

```bash
.venv/bin/python tools/estimate.py --script output/<run>/conversazione.json \
    --run-dir output/<run>        # -> turns_to_generate: 1
```

Se è la **resa** a non convincere — intonazione, pronuncia, un artefatto — il
testo non si tocca e si chiedono altri tentativi:

```bash
.venv/bin/python tools/retake.py --run-dir output/<run> --turn 17 --takes 3 --dry-run
.venv/bin/python tools/retake.py --run-dir output/<run> --turn 17 --takes 3 --with-music
.venv/bin/python tools/retake.py --run-dir output/<run> --list 17
.venv/bin/python tools/retake.py --run-dir output/<run> --choose 17=3
.venv/bin/python tools/synthesize.py ...     # rimonta, zero chiamate al TTS
```

`--with-music`, se l'episodio ha un sottofondo, monta l'anteprima (turno
precedente + nuovo take + turno successivo) anche CON il sottofondo sotto,
mixato con gli stessi parametri: è lì che si giudica se un take regge nel
contesto sonoro vero, non solo a voce nuda. Le anteprime restano comunque
senza intro/outro.

Dopo aver scelto il take, per rimontare basta il mix — niente sintesi:

```bash
.venv/bin/python tools/mix.py --run-dir output/<run>     # zero chiamate al TTS
```

I take vecchi non vengono mai sovrascritti, così si può tornare indietro. Ogni
take nuovo viene anche montato in `segmenti/anteprime/` insieme al turno
precedente e a quello successivo, con le pause vere dell'episodio: un take
ascoltato da solo inganna. La scelta finale resta di chi ascolta.

Il `voice_id` usato per un nuovo take è quello salvato in `voci.json` **di quella
run**, mai quello del registro: se la voce fosse stata riclonata nel frattempo,
il timbro cambierebbe a metà episodio. Se quel `voice_id` non esiste più sul
provider il comando si ferma e **non riclona**.

---

## Il registro delle voci clonate (cache)

`voices_registry.json` evita di riclonare la stessa voce a ogni episodio:

```json
{
  "Mario Rossi": {
    "audio_file": "voci_disponibili/Mario Rossi.wav",
    "audio_hash": "sha256:9f2c...",
    "provider": "fish_audio",
    "voice_id": "abc123",
    "cloned_at": "2026-09-13T10:00:00"
  }
}
```

Prima di clonare, l'agente calcola lo `sha256` del file audio e confronta:

| Situazione | Cosa fa |
|---|---|
| nome presente, hash identico, stesso provider | **riusa** il `voice_id` — nessuna chiamata API |
| nome assente | clona e registra |
| hash diverso (hai sostituito la registrazione) | **riclona** e aggiorna il registro |
| stesso nome ma clonato su un altro provider | riclona sul provider attivo |

---

## Architettura: sostituire il provider vocale

Tutto l'agente parla **solo** con l'interfaccia astratta `VoiceProvider`
(`src/voice_provider.py`), mai direttamente con Fish Audio:

```python
class VoiceProvider(ABC):
    def list_cloned_voices(self) -> list[VoiceInfo]: ...
    def clone_voice(self, name: str, audio_path: str) -> str: ...
    def text_to_speech(self, text: str, voice_id: str) -> bytes: ...
```

Per passare a un altro servizio:

1. Scrivi `src/providers/cartesia_provider.py` con una classe
   `CartesiaProvider(VoiceProvider)` e una funzione `build(options) -> provider`
   che legge la API key dall'ambiente.
2. Aggiungila a `config.yaml`:

   ```yaml
   active_provider: cartesia      # <- unica riga da cambiare per sostituirlo

   providers:
     cartesia:
       class: src.providers.cartesia_provider:CartesiaProvider
       options:
         api_key_env: CARTESIA_API_KEY
   ```

Nessun altro file va toccato: il caricamento della classe è dinamico
(`src/config.py`), non ci sono `if provider == "fish_audio"` sparsi nel codice.

---

## Struttura del progetto

> Per la funzione di **ogni singolo file**, cosa toccare per ottenere un dato
> risultato e cosa invece è meglio non toccare, vedi **[LEGENDA.md](LEGENDA.md)**.


```
podcast-agent/
├── .claude/agents/podcast-agent.md # subagent Claude Code
├── config.yaml                   # provider attivo, percorsi, parametri audio e script
├── .env                          # API key (non committato)
├── voices_registry.json          # cache voci clonate (creato a runtime)
├── documenti/                    # i tuoi documenti di partenza (non committati)
├── voci_disponibili/             # le tue registrazioni
├── musiche/                      # intro/, outro/, sottofondo/ (opzionali, non committate)
├── output/                       # una sottocartella per episodio
├── run.py                        # agente interattivo (entry point)
├── prompts/copione.md            # regole di scrittura del copione (fonte unica)
├── src/
│   ├── config.py                 # lettura config + istanziazione dinamica del provider
│   ├── voice_provider.py         # interfaccia astratta VoiceProvider + errori tipizzati
│   ├── providers/
│   │   └── fish_audio_provider.py# implementazione concreta per Fish Audio
│   ├── pdf_reader.py             # estrazione testo dal documento
│   ├── voice_library.py          # scansione cartella voci + risoluzione dei nomi
│   ├── music_library.py          # scansione cartella musiche + risoluzione dei nomi
│   ├── ffmpeg_tool.py            # unico punto che cerca l'eseguibile ffmpeg
│   ├── script_writer.py          # generazione/revisione di conversazione.json
│   ├── registry.py               # voices_registry.json (hash, lookup, record)
│   ├── script_session.py         # stato delle revisioni su disco (per il subagent)
│   ├── run_layout.py             # struttura della cartella di output
│   ├── cost_estimate.py          # stima caratteri / durata / costo
│   ├── audio_assembler.py        # cache dei segmenti + concatenazione + export mp3
│   ├── music_mixer.py            # mix di intro/outro/sottofondo (unico modulo che parla ffmpeg per il mix)
│   ├── segment_cache.py          # rilettura della cache dei segmenti di una run (retake, mix)
│   ├── episode_timeline.py       # dal minutaggio dell'episodio alla battuta
│   └── retake.py                 # nuovi take di una battuta, a parità di testo
├── tools/                        # entry-point CLI richiamati dal subagent
│   ├── _common.py                # JSON su stdout, exit code tipizzati
│   ├── _approval.py              # gate: niente clonazione/sintesi senza il sì
│   ├── doctor.py                 # diagnosi dell'installazione
│   └── doc_extract.py  voices_list.py  music_list.py  music_set.py  run_init.py
│       script_save.py  script_approve.py  script_write.py  script_revise.py
│       voices_prepare.py  estimate.py  synthesize.py  mix.py  turn_at.py
│       retake.py  run_finalize.py
├── tests/
│   ├── fake_provider.py          # provider finto (toni sinusoidali)
│   ├── test_smoke.py             # 51 controlli end-to-end
│   ├── test_tools_cli.py         # 171 controlli sui CLI del subagent
│   └── test_music.py             # controlli su intro/outro/sottofondo (skip se manca ffmpeg)
└── README.md
```

---

## Configurazione utile

| Chiave | Effetto |
|---|---|
| `active_provider` | quale provider vocale usare |
| `paths.documents_dir` | dove vengono cercati i documenti indicati per solo nome |
| `paths.music_dir` | dove sta `musiche/` (default `./musiche`) |
| `providers.fish_audio.options.tts_model` | `s1`, `s2-pro`, `s2.1-pro`, `s2.1-pro-free`, `drama-3-preview` |
| `providers.fish_audio.options.audio_format` | `wav` (pause precise, consigliato) o `mp3` (nessuna ri-codifica, **niente pause**) |
| `providers.fish_audio.options.cost_per_million_chars_usd` | tariffa usata per la stima; `null` = stima senza costo |
| `audio.pause_between_turns_ms` | pausa fra un turno e l'altro (cambiarla sposta la timeline dei turni, non la cache) |
| `script.model` | modello Claude usato per il copione |
| `script.target_minutes` | durata indicativa richiesta all'autore |
| `music.bed_level_db` | quanto il sottofondo sta sotto la voce (dB, negativo) |
| `music.ducking` | se il sottofondo si abbassa ulteriormente mentre si parla (default `false`) |
| `music.target_lufs` / `music.true_peak_db` | loudness e picco target del file finale |

---

## Gestione degli errori

L'agente si ferma con un messaggio esplicito — mai a metà montaggio e in
silenzio — nei casi seguenti:

- cartella voci inesistente, vuota, o con meno registrazioni degli speaker richiesti;
- file audio in un formato non atteso (elencati, non ignorati di nascosto);
- nome speaker (o nome di un brano) che non corrisponde a nessun file (chiede
  conferma sui simili, non indovina);
- API key mancante o non valida (Anthropic o provider vocale);
- errori del provider: rate limit (429), credito esaurito (402), audio di
  riferimento rifiutato (413/415/422), timeout, errori 5xx — con retry ed
  exponential backoff dove ha senso;
- uno speaker dello script senza voce assegnata → **blocco prima di iniziare la
  sintesi**, non a metà;
- sintesi interrotta a metà → dice a quale turno si è fermata; i segmenti già
  scaricati restano in cache e rilanciando si pagano solo i turni mancanti;
- `voice_id` di una run che non esiste più sul provider → si ferma **senza
  riclonare**, perché una voce nuova avrebbe un timbro diverso dal resto
  dell'episodio;
- PDF scansionato senza testo estraibile, o protetto da password;
- nessun encoder mp3 disponibile → salva in WAV e avvisa (con `imageio-ffmpeg`
  fra le dipendenze, di norma non succede);
- una musica è scelta ma ffmpeg non si trova → si ferma **prima di qualunque
  sintesi** (`tools/doctor.py` dice cosa manca);
- un sottofondo ha `parlato: true` nei metadati → errore di dominio: il
  sottofondo deve essere solo musica;
- un brano già registrato in `musiche_usate.json` è stato sostituito → si
  ferma e chiede `--accept-changed-music` per procedere.

---

## Costi

Due chiamate a pagamento per episodio:

- **Claude** — solo con `run.py`: una chiamata per la prima stesura, una per ogni
  revisione. Con il subagent il copione lo scrive l'agente, quindi questa voce sparisce.
- **Provider vocale** — clonazione (una tantum per voce, poi cache) + sintesi,
  che di norma si paga a caratteri.

Il checkpoint di revisione esiste apposta per non pagare la sintesi due volte:
lo script si itera quanto serve, l'audio si genera una volta sola.

E se dopo l'ascolto una battuta non va, si paga quella: la cache dei segmenti
tiene tutto il resto. `estimate.py --run-dir` lo dice prima di spendere, e
`retake.py --dry-run` fa lo stesso per un nuovo tentativo sulla stessa battuta.
