// Gli esiti mostrati sempre con lo stesso schema (wireframe Stati.dc.html):
//   0 · conferma breve (con "Annulla" dove c'e') · 1 · cosa correggere, accanto al campo
//   2 · blocco del servizio esterno, MAI "Riprova" · 3 · domanda con le opzioni
// piu' gli stati in corso, interrotto e vuoto. La UI decide da `error_type`, mai dal testo.

import { h, svuota, nuovoId } from "./dom.js";
import { invia } from "./api.js";

// Titoli per error_type (il testo dettagliato e' `messaggio`, che viene dall'agente).
const TITOLI = {
  DocumentNotExtractable: "Questo PDF non contiene testo",
  DocumentTooLarge: "Documento troppo grande",
  UnsupportedDocument: "Formato del documento non gestito",
  DocumentError: "Documento non leggibile",
  EpisodeConfigError: "Scelta non valida",
  VoiceLibraryError: "Voce non utilizzabile",
  MusicChoiceError: "Scelta delle musiche non valida",
  MusicLibraryError: "Brano non utilizzabile",
  ScriptInvalid: "La modifica renderebbe il copione non valido",
  ScriptHasErrors: "Il copione ha errori da correggere prima dell'approvazione",
  VersionConflict: "Il copione è cambiato nel frattempo",
  NothingToChange: "Nessuna modifica da salvare",
  ApprovalRequired: "Il copione non è approvato",
  ScriptMissing: "Manca il copione",
  ScriptWriterError: "Scrittura del copione non riuscita",
  RunBusy: "Sull'episodio è in corso un'altra operazione",
  LockTimeout: "Un file è occupato da un'altra operazione",
  ModelRateLimited: "Troppe richieste al modello",
  ModelOverloaded: "Il modello è sovraccarico",
  ModelUnreachable: "Il modello non è raggiungibile",
  ProductionInterrupted: "Generazione interrotta",
  Interrupted: "Operazione interrotta",
  AssemblyError: "Sintesi o montaggio non riusciti",
  EpisodeNotProduced: "L'episodio non è ancora stato prodotto",
  // esito 2
  ProviderQuotaError: "Credito del servizio vocale esaurito",
  ProviderAuthError: "Chiave del servizio vocale non valida",
  ProviderRateLimitError: "Troppe richieste al servizio vocale",
  ProviderTimeoutError: "Il servizio vocale non risponde",
  ProviderAudioQualityError: "Il servizio vocale rifiuta la registrazione",
  VoiceProviderError: "Errore del servizio vocale",
  ModelAuthError: "Chiave del modello non valida",
  ModelCreditExhausted: "Credito del modello esaurito",
  ExternalServiceError: "Errore di un servizio esterno",
  // esito 3
  AmbiguousName: "Quale intendi?",
  SpokenNameIncomplete: "Manca il cognome",
  MusicChanged: "Un brano è stato sostituito",
  ConsentRequired: "Serve il consenso",
  CloneOutcomeUnknown: "Clonazione dall'esito incerto",
  RecordingChanged: "La registrazione è cambiata",
  RevokedMeanwhile: "Il consenso è stato revocato nel frattempo",
  // controller
  NonPermesso: "Non hai il permesso per questa azione",
  GettoneNonValido: "La conferma non è più valida",
  FileEsistente: "Esiste già un file con questo nome",
  FileNonAmmesso: "File non ammesso",
  FileTroppoGrande: "File troppo grande",
  LavoroGiaInCorso: "C'è già un lavoro in corso su questo episodio",
  CodaPiena: "Troppi lavori in attesa",
  Timeout: "Operazione troppo lunga",
  NonTrovato: "Elemento non trovato",
  ErroreInterno: "Errore interno",
  InvalidArguments: "Errore interno",
  NotAllowedInWebMode: "Errore interno",
  ByRequired: "Errore interno",
  InternalError: "Errore interno",
  ErroreRete: "Il servizio non risponde",
  NonAutenticato: "Sessione scaduta",
};

export function titolo(corpo) {
  return TITOLI[corpo?.error_type] || "Operazione non riuscita";
}

// --- esito 0: conferma breve ----------------------------------------------

let regioneAvvisi;
export function preparaAvvisi(el) { regioneAvvisi = el; }

export function chiudiAvvisi() {
  if (regioneAvvisi) svuota(regioneAvvisi);
}

export function conferma(testo, { annulla } = {}) {
  if (!regioneAvvisi) return;
  const avviso = h("div", { class: "avviso avviso-ok", role: "status" },
    h("span", {}, testo),
    annulla ? h("button", { type: "button", class: "pulsante pulsante-link", onclick: async () => { avviso.remove(); await annulla(); } }, "Annulla") : null,
    h("button", { type: "button", class: "chiudi", "aria-label": "Chiudi l'avviso", onclick: () => avviso.remove() }, "×"),
  );
  regioneAvvisi.append(avviso);
  setTimeout(() => avviso.remove(), annulla ? 12000 : 5000);
}

// --- esito 1: cosa correggere --------------------------------------------

// Messaggio di un esito 1 (o di un errore del controller) in `contenitore`, che e'
// il box accanto al campo (o della sezione). `azione`: {testo, fai} facoltativa.
export function erroreCorreggibile(contenitore, corpo, { azione, riprova } = {}) {
  svuota(contenitore);
  contenitore.hidden = false;
  const blocco = h("div", { class: "messaggio messaggio-errore", role: "alert" },
    h("p", { class: "messaggio-titolo" }, titolo(corpo)),
    h("p", {}, testoMessaggio(corpo)),
    dettagliElenco(corpo),
    h("div", { class: "riga-azioni" },
      azione ? h("button", { type: "button", class: "pulsante", onclick: azione.fai }, azione.testo) : null,
      corpo.riprovabile && riprova ? h("button", { type: "button", class: "pulsante", onclick: riprova }, "Riprova") : null,
    ),
  );
  contenitore.append(blocco);
  return blocco;
}

function testoMessaggio(corpo) {
  if (["InvalidArguments", "NotAllowedInWebMode", "ByRequired", "InternalError", "ErroreInterno"].includes(corpo.error_type)) {
    return "Qualcosa non ha funzionato nel servizio. L'errore è stato registrato: se si ripete, avvisa l'amministratore.";
  }
  return corpo.messaggio || "";
}

function dettagliElenco(corpo) {
  const d = corpo.details || {};
  const errori = Array.isArray(d.errors) ? d.errors : null;
  if (errori && errori.length) return h("ul", { class: "elenco-errori" }, errori.map((e) => h("li", {}, e)));
  if (corpo.error_type === "ModelRateLimited" || corpo.error_type === "ModelOverloaded") {
    if (d.retry_after_seconds) return h("p", { class: "nota" }, `Riprova fra ${Math.ceil(d.retry_after_seconds)} secondi.`);
  }
  return null;
}

export function pulisciErrore(contenitore) {
  if (contenitore) { svuota(contenitore); contenitore.hidden = true; }
}

// --- esito 2: blocco, nessun "Riprova" ----------------------------------

export function bloccoServizioEsterno(corpo) {
  const id = nuovoId("blocco");
  const dettagli = JSON.stringify({ error_type: corpo.error_type, messaggio: corpo.messaggio, details: corpo.details || null }, null, 2);
  const stato = h("p", { class: "nota", "aria-live": "polite" });
  const dialogo = h("dialog", { class: "dialogo dialogo-blocco", "aria-labelledby": id },
    h("h2", { id }, titolo(corpo)),
    h("p", {}, corpo.messaggio || ""),
    h("p", { class: "nota" }, "Non riproviamo da soli: un nuovo tentativo potrebbe creare voci duplicate a pagamento. Serve un intervento sull'account o sulla configurazione. L'episodio resta nell'Archivio."),
    h("div", { class: "riga-azioni" },
      h("button", { type: "button", class: "pulsante", onclick: async () => {
        try { await navigator.clipboard.writeText(dettagli); stato.textContent = "Dettagli copiati."; }
        catch { stato.textContent = "Copia non riuscita: seleziona il testo qui sotto."; }
      } }, "Copia i dettagli"),
      h("button", { type: "button", class: "pulsante pulsante-primario", onclick: () => dialogo.close() }, "Ho capito"),
    ),
    stato,
    h("details", {}, h("summary", {}, "Dettagli per l'amministratore"), h("pre", { class: "dettagli" }, dettagli)),
  );
  mostraDialogo(dialogo);
}

// --- esito 3: domanda -----------------------------------------------------

// `opzioni`: [{testo, valore, primaria, descrizione}] → la promessa si risolve con il
// valore scelto (o null se l'utente chiude).
export function domanda({ titolo: t, testo, opzioni, nota }) {
  return new Promise((risolvi) => {
    const id = nuovoId("domanda");
    let scelta = null;
    const dialogo = h("dialog", { class: "dialogo", "aria-labelledby": id },
      h("h2", { id }, t),
      testo ? h("p", {}, testo) : null,
      nota ? h("p", { class: "nota" }, nota) : null,
      h("div", { class: "opzioni" }, opzioni.map((o) => h("button", {
        type: "button", class: ["pulsante", o.primaria ? "pulsante-primario" : "", o.descrizione ? "opzione-estesa" : ""],
        onclick: () => { scelta = o.valore; dialogo.close(); },
      }, h("span", {}, o.testo), o.descrizione ? h("span", { class: "nota" }, o.descrizione) : null))),
    );
    dialogo.addEventListener("close", () => { dialogo.remove(); risolvi(scelta); });
    mostraDialogo(dialogo, false);
  });
}

function mostraDialogo(dialogo, rimuovi = true) {
  document.body.append(dialogo);
  if (rimuovi) dialogo.addEventListener("close", () => dialogo.remove());
  dialogo.showModal();
}

// Esito 3 con gettone: chiede conferma e, se l'utente accetta, manda il gettone.
// Restituisce la risposta della conferma o null.
export async function confermaGettone(corpo, { testoConferma = "Confermo", testo } = {}) {
  const scelta = await domanda({
    titolo: titolo(corpo),
    testo: testo || corpo.messaggio,
    nota: corpo.scade_il ? "La domanda resta valida per qualche minuto." : null,
    opzioni: [
      { testo: "Annulla", valore: false },
      { testo: testoConferma, valore: true, primaria: true },
    ],
  });
  if (!scelta) return null;
  return invia(`/api/conferme/${encodeURIComponent(corpo.gettone)}`, {});
}

// --- esito generico ---------------------------------------------------------

// Mostra un esito non riuscito nel posto giusto: esito 2 → blocco; esito 1 e
// controller → nel contenitore. Gli esiti 3 li gestisce chi chiama (servono le opzioni).
export function mostraErrore(contenitore, corpo, opzioni = {}) {
  if (corpo.esito === "servizio_esterno") { bloccoServizioEsterno(corpo); return; }
  if (contenitore) erroreCorreggibile(contenitore, corpo, opzioni);
}

// --- stati: in corso, interrotto, vuoto ----------------------------------

export function scheletro(testo) {
  return h("div", { class: "in-corso", role: "status", "aria-live": "polite" },
    h("p", { class: "in-corso-titolo" }, h("span", { class: "rotella", "aria-hidden": "true" }), testo),
    h("div", { class: "scheletro", "aria-hidden": "true" }, [1, 2, 3, 4].map(() => h("div", { class: "scheletro-riga" }))),
    h("p", { class: "nota" }, "Si può lasciare la pagina: il lavoro continua."),
  );
}

export function interrotto({ fatte, totale, mancanti, riprendi }) {
  return h("div", { class: "messaggio messaggio-attenzione", role: "status" },
    h("p", { class: "messaggio-titolo" }, `Generazione interrotta alla battuta ${fatte + 1 > totale ? totale : fatte + 1} di ${totale}`),
    h("p", {}, `Le battute già pronte sono salvate. Riprendendo si generano solo le ${mancanti} mancanti.`),
    riprendi ? h("div", { class: "riga-azioni" }, h("button", { type: "button", class: "pulsante pulsante-primario", onclick: riprendi }, "Riprendi")) : null,
  );
}

export function vuoto({ titolo: t, testo, azione }) {
  return h("div", { class: "vuoto" },
    h("p", { class: "vuoto-titolo" }, t),
    h("p", {}, testo),
    azione ? h("a", { class: "pulsante pulsante-primario", href: azione.href }, azione.testo) : null,
  );
}
