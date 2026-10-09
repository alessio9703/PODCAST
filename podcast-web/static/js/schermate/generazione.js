// Passo 6 · Generazione (06-Generazione.dc.html). Avanzamento dagli eventi dell'agente
// (SSE, polling come ripiego). La pagina si puo' chiudere: lo stato vero lo dice l'agente.

import { h, sostituisci, durata } from "../dom.js";
import { invia, seg, segui } from "../api.js";
import { intestazionePasso, statoRun, boxErrore, piede, sezione } from "../componenti.js";
import { mostraErrore, conferma, confermaGettone, domanda, interrotto, bloccoServizioEsterno } from "../stati.js";
import { puo } from "../sessione.js";

const NOMI_FASI = {
  preparazione_voci: "Preparazione voci", sintesi: "Sintesi delle battute", montaggio: "Montaggio",
  mix: "Mix con le musiche", chiusura: "Chiusura e archiviazione",
};

export async function mostraGenerazione(pagina, { run }) {
  const s = await statoRun(run);
  const errore = boxErrore();
  const stato = { fasi: [], fatte: new Set(), corrente: null, battuta: 0, totale: 0, eta: null, voci: null, esito: null, lavoro: null, avvisi: [] };
  const progresso = h("progress", { max: "1", value: "0", "aria-label": "Battute generate" });
  const testoProgresso = h("p", { class: "forte", "aria-live": "polite" }, "In attesa di cominciare…");
  const elencoFasi = h("ol", { class: "fasi" });
  const coda = h("p", { class: "nota", "aria-live": "polite" });
  const esito = h("div", {});
  const azioni = h("div", { class: "riga-azioni" });

  sostituisci(pagina,
    intestazionePasso({ run, passo: 6, titolo: s?.titolo, stato: s?.etichetta || "In generazione", raggiunto: s?.passo || 6,
      sottotitolo: "Puoi chiudere questa pagina: il lavoro continua" }),
    h("h2", { class: "domanda-passo" }, "Stiamo generando l'episodio"),
    h("p", { class: "nota" }, "Ogni battuta viene salvata appena pronta. Se qualcosa si interrompe, riprendi da dove si è fermata."),
    errore,
    sezione("Sintesi delle battute", testoProgresso, progresso, coda),
    sezione("Fasi", elencoFasi),
    esito,
    piede(azioni, h("a", { class: "pulsante", href: "#/archivio" }, "Torna all'archivio")));

  function disegnaFasi() {
    const fasi = stato.fasi.length ? stato.fasi : Object.keys(NOMI_FASI);
    sostituisci(elencoFasi, fasi.map((f) => {
      const etichetta = stato.fatte.has(f) ? "Pronta" : stato.corrente === f ? "In corso" : "In attesa";
      const dettaglio = f === "preparazione_voci" && stato.voci ? ` · ${stato.voci.riusate} riusate · ${stato.voci.clonate} clonate`
        : f === "sintesi" && stato.corrente === "sintesi" && stato.battuta ? ` · battuta ${Math.min(stato.battuta + 1, stato.totale)}` : "";
      return h("li", { class: `fase fase-${etichetta === "Pronta" ? "pronta" : etichetta === "In corso" ? "incorso" : "attesa"}` },
        h("span", { class: "forte" }, NOMI_FASI[f] || f), h("span", { class: "nota" }, ` ${etichetta}${dettaglio}`));
    }));
  }

  function disegnaProgresso() {
    if (!stato.totale) return;
    progresso.max = stato.totale;
    progresso.value = stato.battuta;
    let eta = "";
    if (stato.battuta < stato.totale && typeof stato.eta === "number") {
      eta = stato.eta < 60 ? " · meno di un minuto" : ` · ~${Math.round(stato.eta / 60)} min rimanenti`;
    }
    testoProgresso.textContent = `${stato.battuta} di ${stato.totale}${eta}`;
  }

  function disegnaAzioni(attivo) {
    sostituisci(azioni);
    if (attivo && puo("produrre")) {
      azioni.append(h("button", { type: "button", class: "pulsante", disabled: !!stato.lavoro?.interruzione_richiesta, onclick: interrompi },
        stato.lavoro?.interruzione_richiesta ? "Interruzione richiesta…" : "Interrompi la generazione"));
    }
  }

  async function interrompi() {
    const ok = await domanda({ titolo: "Vuoi fermarti?", testo: "Le battute già pronte restano salvate. Potrai riprendere dall'Archivio.",
      opzioni: [{ testo: "Continua a generare", valore: false }, { testo: "Interrompi la generazione", valore: true, primaria: true }] });
    if (!ok) return;
    const { corpo } = await invia(`/api/episodi/${seg(run)}/interruzione`, {});
    if (!corpo.ok) { mostraErrore(errore, corpo); return; }
    conferma(corpo.dati.messaggio || "Interruzione richiesta");
    if (stato.lavoro) stato.lavoro.interruzione_richiesta = true;
    disegnaAzioni(true);
  }

  function evento(e) {
    switch (e.evento) {
      case "iniziata": stato.fasi = e.fasi || []; stato.totale = e.totale || 0; stato.fatte.clear(); stato.battuta = 0; break;
      case "fase_iniziata": stato.corrente = e.fase; break;
      case "fase_finita":
        stato.fatte.add(e.fase);
        if (e.fase === "preparazione_voci") stato.voci = { riusate: e.riusate ?? 0, clonate: e.clonate ?? 0 };
        break;
      case "battuta": stato.battuta = e.n; stato.totale = e.totale || stato.totale; stato.eta = e.eta_seconds; break;
      case "avviso": if (e.testo) stato.avvisi.push(e.testo); break;
      case "finita": stato.esito = { finita: e }; break;
      case "interrotta": stato.esito = { interrotta: e }; break;
      case "errore": stato.esito = { errore: e }; break;
      default: break;
    }
    disegnaFasi();
    disegnaProgresso();
  }

  function aggiornaLavoro(v) {
    stato.lavoro = v;
    const attivo = v && (v.stato === "in_coda" || v.stato === "in_corso");
    coda.textContent = v?.stato === "in_coda" ? `In coda: posizione ${v.posizione}. La generazione parte appena il servizio è libero.` : "";
    disegnaAzioni(attivo);
  }

  // Esito finale: dal lavoro (se l'abbiamo lanciato noi ne vediamo i dettagli) o dagli eventi.
  async function fine(dati) {
    if (dati?.lavoro) stato.lavoro = dati.lavoro;
    disegnaAzioni(false);
    const corpoEsito = stato.lavoro?.esito;
    if (corpoEsito && !corpoEsito.ok) return mostraEsitoLavoro(corpoEsito);
    if (stato.esito?.finita || corpoEsito?.ok) {
      sostituisci(esito, h("div", { class: "messaggio messaggio-ok", role: "status" },
        h("p", { class: "messaggio-titolo" }, "L'episodio è pronto"),
        stato.esito?.finita ? h("p", {}, `Durata ${durata(stato.esito.finita.duration_seconds)}.`) : null,
        stato.avvisi.length ? h("ul", {}, stato.avvisi.map((a) => h("li", {}, a))) : null,
        h("a", { class: "pulsante pulsante-primario", href: `#/episodi/${seg(run)}/7` }, "Ascolta l'episodio")));
      return;
    }
    // Nessun dettaglio del lavoro (lanciato da altri o prima di un riavvio): lo stato dell'agente.
    const ora = await statoRun(run);
    if (ora?.stato === "interrotta") return mostraInterrotta(stato.battuta, stato.totale || stato.battuta, ora.battute_mancanti);
    if (ora?.stato === "pronto") { location.hash = `#/episodi/${seg(run)}/7`; return; }
    if (stato.esito?.errore) {
      sostituisci(esito, h("div", { class: "messaggio messaggio-errore", role: "alert" },
        h("p", { class: "messaggio-titolo" }, "La generazione si è fermata"), h("p", {}, stato.esito.errore.error || "")));
    } else if (ora && ora.passo && ora.passo !== 6) {
      sostituisci(esito, h("p", { class: "nota" }, `Nessuna generazione in corso. Stato: ${ora.etichetta}.`),
        h("a", { class: "pulsante", href: `#/episodi/${seg(run)}/${ora.passo}` }, ora.azione || "Vai"));
    }
  }

  function mostraInterrotta(fatte, totale, mancanti) {
    sostituisci(esito, interrotto({ fatte, totale, mancanti: mancanti ?? Math.max(0, totale - fatte),
      riprendi: puo("produrre") ? () => { location.hash = `#/episodi/${seg(run)}/5`; } : null }),
      h("p", { class: "nota" }, "«Riprendi» mostra prima la stima delle battute mancanti."));
  }

  async function mostraEsitoLavoro(c) {
    if (c.error_type === "ProductionInterrupted") {
      return mostraInterrotta(c.details?.done ?? 0, c.details?.total ?? 0, (c.details?.missing || []).length);
    }
    if (c.esito === "servizio_esterno") { bloccoServizioEsterno(c); }
    if (c.error_type === "MusicChanged" && c.gettone) {
      const r = await confermaGettone(c, { testoConferma: "Usa il brano sostituito",
        testo: `${c.messaggio} Confermi di usare il brano nella versione attuale?` });
      if (r?.corpo?.ok) { conferma("Generazione rimessa in coda"); avvia(); return; }
      if (r) mostraErrore(errore, r.corpo);
      return;
    }
    if (c.error_type === "ConsentRequired") {
      sostituisci(esito, h("div", { class: "messaggio messaggio-attenzione" },
        h("p", { class: "messaggio-titolo" }, "Serve il consenso per alcune voci"), h("p", {}, c.messaggio),
        h("a", { class: "pulsante pulsante-primario", href: `#/episodi/${seg(run)}/5` }, "Vai alla stima e consenso")));
      return;
    }
    if (c.error_type === "CloneOutcomeUnknown") {
      sostituisci(esito, h("div", { class: "messaggio messaggio-attenzione" },
        h("p", { class: "messaggio-titolo" }, "Clonazione dall'esito incerto"), h("p", {}, c.messaggio),
        h("p", {}, puo("voce_clonazione") ? "Controlla le voci sul servizio vocale, poi conferma dalla stima." : "Chiedi a chi gestisce le voci di controllare sul servizio vocale e confermare."),
        h("a", { class: "pulsante", href: `#/episodi/${seg(run)}/5` }, "Vai alla stima e consenso")));
      return;
    }
    if (c.esito !== "servizio_esterno") mostraErrore(errore, c);
  }

  let chiudi = null;
  function avvia() {
    if (chiudi) chiudi();
    sostituisci(esito);
    stato.esito = null;
    chiudi = segui(run, { agente: evento, lavoro: aggiornaLavoro, azzerato: () => { stato.fatte.clear(); stato.battuta = 0; stato.esito = null; }, fine });
  }
  disegnaFasi();
  avvia();
  return () => { if (chiudi) chiudi(); };
}
