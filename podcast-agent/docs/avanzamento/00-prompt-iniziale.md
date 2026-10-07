# Prompt iniziale dell'integrazione web

## Obiettivo

Rendere `podcast-agent` integrabile con un'interfaccia web, in modo che OGNI funzione
mostrata nel wireframe in `docs/wireframe/` sia eseguibile tramite i comandi in `tools/`,
senza interazione da terminale e senza Claude Code nel mezzo.

Il server web, l'autenticazione, gli upload HTTP, la coda dei lavori e il frontend NON
fanno parte di questo lavoro: li costruirà un "adattatore" separato che chiamerà
`tools/*.py` come sottoprocessi e leggerà il loro JSON. Il compito è far sì che
l'agente esponga tutto ciò che serve a quell'adattatore, e documentarlo.

## Regole non negoziabili

- Il contratto dei tool resta quello di `tools/_common.py`: stdout = UN solo oggetto
  JSON, stderr = messaggi umani, exit code 0/1/2/3 con il significato attuale. Ogni tool
  nuovo usa `run_tool`, `emit`, `note`. Nessun `input()` nei tool.
- La logica sta in `src/`; i file in `tools/` restano sottili.
- Non si indeboliscono MAI le garanzie esistenti: gate di approvazione legato allo
  sha256, nessun retry sulla clonazione, nomi di voce mai indovinati, controllo sha256
  delle musiche, cache dei segmenti, promemoria sul consenso.
- `run.py` e il subagent `@podcast-agent` devono continuare a funzionare come oggi.
- I test esistenti restano verdi. Ogni funzione nuova ha test con il provider finto e un
  client Anthropic finto: nessuna chiamata reale nei test.
- Scritture su file condivisi atomiche e protette da lock su file.
- Lo stato di un episodio va DERIVATO dai file della run, non duplicato.

## Metodo di lavoro

1. Plan mode: per ogni schermata del wireframe, azioni, dati, comando esistente, cosa
   manca. Piano e domande prima di modificare codice.
2. Dopo l'OK, per aree con test: copione → produzione → episodio → archivio e librerie
   → contratto.
3. A fine lavoro: test verdi, README/LEGENDA aggiornati, contratto scritto.

## Lacune individuate all'inizio

- Passo 1: pagine/parole, errori strutturati per documenti non estraibili, elenco della
  libreria Documenti.
- Passo 2: formati conversazione/intervista con presentatore anche nel percorso API
  (regole condivise con script_save.py), cast salvato nella run prima del copione,
  libreria Voci con stato, consenso, uso; nome da pronunciare con esito strutturato.
- Consenso registrato per voce; voices_prepare rifiuta di clonare senza consenso.
- Passo 3: metadati dei brani, "usato in N episodi", durata totale indicativa.
- Passo 4: modifica di una battuta, elenco versioni, confronto, ripristino come nuova
  versione, annullamento approvazione, copione strutturato per la UI.
- Passo 5: valuta e cambio configurabili, costo con clonazioni, voci nuove vs riusate.
- Passo 6: comando unico "produci", avanzamento JSONL senza rompere stdout,
  interruzione pulita e ripresa, nessun retry sulla clonazione.
- Passo 7: info episodio con trascrizione e sezioni nel file finale, forma d'onda,
  retake e scelta, cambio musiche a episodio montato.
- Archivio: elenco run con stato derivato e prossima azione.
- Avanzate: lezioni e diagnostica con output per UI; impostazioni in sola lettura senza
  chiavi.

## Punti aperti decisi con l'utente

Retake con indicazioni (whitelist di tag), dove salvare consenso e metadati delle voci,
valuta, formati supportati.

## Consegna finale

Codice e test; docs/CONTRATTO_INTERFACCIA.md con, per ogni azione del wireframe,
comando, argomenti, esempio reale di JSON, exit code, se è lunga e se costa; le azioni a
carico dell'adattatore e quelle non supportate con il motivo; un riepilogo finale.
