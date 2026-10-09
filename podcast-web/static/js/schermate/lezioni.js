// Lezioni: cio' che l'agente impara dalle correzioni. Una lezione accettata entra nel
// prompt di TUTTI gli episodi successivi. Proporre lezioni costa (modello): la stima si
// vede prima, sul pulsante e nella conferma. Accetta (testo modificabile), rifiuta con
// un motivo; tetto raggiunto -> si sceglie quale lezione togliere.

import { h, sostituisci, dataOra, numero, nuovoId } from "../dom.js";
import { leggi, invia, aspettaLavoro } from "../api.js";
import { boxErrore, sezione } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, domanda, scheletro } from "../stati.js";
import { puo } from "../sessione.js";

const MAX_TESTO = 400;

function costoUsd(stima) {
  return typeof stima?.cost_usd === "number"
    ? new Intl.NumberFormat("it-IT", { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(stima.cost_usd)
    : null;
}

export async function mostraLezioni(pagina) {
  const errore = boxErrore();
  const corpo = h("div", {});
  const proposta = h("div", {});
  const extra = h("div", {});
  let chiudiAttesa = null;
  sostituisci(pagina,
    h("header", { class: "intestazione-pagina" },
      h("div", {}, h("h1", { tabindex: "-1" }, "Lezioni"),
        h("p", { class: "nota" }, "Indicazioni ricavate dalle correzioni degli episodi. Una lezione accettata entra nelle istruzioni di tutti gli episodi successivi, di tutti."))),
    errore);
  if (!puo("lezioni")) {
    pagina.append(h("p", { class: "nota" }, "Le lezioni le gestisce chi amministra il progetto."));
    return;
  }
  pagina.append(proposta, corpo, extra);

  async function carica() {
    const { corpo: r } = await leggi("/api/lezioni");
    if (!r.ok) { mostraErrore(errore, r, { riprova: carica }); return; }
    const l = r.dati.lezioni || {};
    sostituisci(corpo,
      sezione(`Proposte (${(l.proposte || []).length})`,
        (l.proposte || []).length ? h("ul", { class: "lezioni" }, l.proposte.map(rigaProposta)) : h("p", { class: "nota" }, "Nessuna proposta da valutare.")),
      sezione(`Attive (${(l.attive || []).length})`,
        (l.attive || []).length ? h("ul", { class: "lezioni" }, l.attive.map((x) => h("li", { class: "lezione" },
          h("p", {}, h("span", { class: "forte" }, `${x.id} · `), x.testo),
          h("p", { class: "nota" }, `Accettata da ${x.accettata_da || "—"} il ${dataOra(x.accettata_il)}`)))) : h("p", { class: "nota" }, "Nessuna lezione attiva.")),
      (l.rifiutate || []).length || (l.rimosse || []).length ? h("details", { class: "scheda" },
        h("summary", {}, `Rifiutate e rimosse (${(l.rifiutate || []).length + (l.rimosse || []).length})`),
        h("ul", { class: "lezioni" }, [...(l.rifiutate || []), ...(l.rimosse || [])].map((x) => h("li", { class: "lezione" },
          h("p", {}, h("span", { class: "forte" }, `${x.id} · `), x.testo),
          h("p", { class: "nota" }, x.stato === "rifiutata"
            ? `Rifiutata da ${x.rifiutata_da || "—"} il ${dataOra(x.rifiutata_il)}${x.motivo_rifiuto ? ` · «${x.motivo_rifiuto}»` : ""}`
            : `Rimossa${x.rimossa_il ? ` il ${dataOra(x.rimossa_il)}` : ""}`))))) : null);
  }

  function rigaProposta(p) {
    const box = boxErrore();
    const id = nuovoId("testo-lezione");
    const testo = h("textarea", { id, rows: "3", maxlength: String(MAX_TESTO) }, p.testo || "");
    return h("li", { class: "lezione" },
      h("label", { for: id, class: "etichetta" }, `${p.id} · testo della lezione (puoi modificarlo, al massimo ${MAX_TESTO} caratteri)`),
      testo,
      p.pattern ? h("p", { class: "nota" }, `Schema: ${p.pattern.categoria || ""}${p.pattern.dettaglio ? ` · ${p.pattern.dettaglio}` : ""} · ${(p.prove || []).length} esempi in ${(p.episodi || []).length} episodi`) : null,
      p.conflitto_copione ? h("p", { class: "nota nota-errore" }, `Possibile conflitto con le istruzioni del copione: ${p.conflitto_copione}`) : null,
      h("div", { class: "riga-azioni" },
        h("button", { type: "button", class: "pulsante pulsante-primario", onclick: () => accetta(p, testo.value.trim(), box) }, "Accetta"),
        h("button", { type: "button", class: "pulsante", onclick: () => rifiuta(p, box) }, "Rifiuta")),
      box);
  }

  async function accetta(p, testo, box) {
    pulisciErrore(box);
    const si = await domanda({ titolo: `Accettare la lezione ${p.id}?`, testo: `«${testo}»`,
      nota: "Entra nelle istruzioni di tutti gli episodi successivi, di tutti gli utenti.",
      opzioni: [{ testo: "Annulla", valore: false }, { testo: "Accetta", valore: true, primaria: true }] });
    if (!si) return;
    const { corpo: r } = await invia(`/api/lezioni/${encodeURIComponent(p.id)}/accettazione`, testo !== p.testo ? { testo } : {});
    if (r.ok) { conferma(`Lezione ${p.id} accettata`); carica(); return; }
    if (r.esito === "decisione" && r.error_type === "LessonRemovalRequired") {
      const scelta = await domanda({ titolo: "Quale lezione togliere?",
        testo: "Le lezioni attive hanno raggiunto il massimo: per accettare questa, scegli quale togliere.",
        opzioni: [...(r.details?.candidates || []).map((c) => ({ testo: c.id, valore: c.id, descrizione: [c.testo, c.motivo].filter(Boolean).join(" · ") })),
          { testo: "Annulla", valore: null }] });
      if (!scelta) return;
      const { corpo: c } = await invia(`/api/conferme/${encodeURIComponent(r.gettone)}`, { scelta });
      if (c.ok) { conferma(`Lezione ${p.id} accettata, ${scelta} tolta`); carica(); } else mostraErrore(box, c);
      return;
    }
    mostraErrore(box, r);
  }

  async function rifiuta(p, box) {
    pulisciErrore(box);
    const id = nuovoId("motivo");
    const campo = h("textarea", { id, rows: "2", maxlength: String(MAX_TESTO) });
    const motivo = await new Promise((risolvi) => {
      const dialogo = h("dialog", { class: "dialogo", "aria-labelledby": `${id}-t` },
        h("h2", { id: `${id}-t` }, `Rifiutare la lezione ${p.id}?`),
        h("label", { for: id, class: "etichetta" }, "Perché? (resta nel registro delle lezioni)"), campo,
        h("div", { class: "opzioni" },
          h("button", { type: "button", class: "pulsante", onclick: () => dialogo.close() }, "Annulla"),
          h("button", { type: "button", class: "pulsante pulsante-primario", onclick: () => {
            if (!campo.value.trim()) { campo.focus(); return; }
            risolvi(campo.value.trim()); dialogo.close();
          } }, "Rifiuta")));
      dialogo.addEventListener("close", () => { dialogo.remove(); risolvi(null); });
      document.body.append(dialogo);
      dialogo.showModal();
      campo.focus();
    });
    if (!motivo) return;
    const { corpo: r } = await invia(`/api/lezioni/${encodeURIComponent(p.id)}/rifiuto`, { motivo });
    if (r.ok) { conferma(`Lezione ${p.id} rifiutata`); carica(); } else mostraErrore(box, r);
  }

  // Proposte nuove: la stima (gratis) prima, poi la chiamata al modello (costa).
  async function caricaProposta() {
    const box = boxErrore();
    const { corpo: r } = await leggi("/api/lezioni/proposta/stima");
    if (!r.ok) { sostituisci(proposta, sezione("Proponi nuove lezioni", box)); mostraErrore(box, r); return; }
    const d = r.dati;
    const schemi = d.evidenza?.schemi || [];
    if (!schemi.length) {
      sostituisci(proposta, sezione("Proponi nuove lezioni",
        h("p", { class: "nota" }, `Nessuno schema ricorrente sopra soglia in ${numero(d.evidenza?.episodi_analizzati ?? 0)} episodi: niente da proporre.`)));
      return;
    }
    const costo = costoUsd(d.stima);
    const testoCosto = costo ? `Costo stimato ${costo}` : "Costo non noto: in config.yaml mancano i prezzi del modello per le lezioni.";
    const pulsante = h("button", { type: "button", class: "pulsante pulsante-primario", onclick: async () => {
      pulisciErrore(box);
      const si = await domanda({ titolo: "Chiedere al modello nuove lezioni?",
        testo: `${schemi.length} schemi ricorrenti in ${numero(d.evidenza?.episodi_analizzati)} episodi. Al massimo due proposte, da approvare una per una.`,
        nota: `${testoCosto}. È un servizio a pagamento.`,
        opzioni: [{ testo: "Annulla", valore: false }, { testo: costo ? `Proponi · ${costo}` : "Proponi", valore: true, primaria: true }] });
      if (!si) return;
      const { corpo: a } = await invia("/api/lezioni/proposta", { impronta: d.impronta });
      if (!a.ok) { if (a.error_type === "StimaCambiata") caricaProposta(); mostraErrore(box, a); return; }
      pulsante.disabled = true;
      chiudiAttesa = aspettaLavoro(a.dati.lavoro, (v) => {
        if (!v) { carica(); caricaProposta(); return; }
        if (v.stato === "in_coda") sostituisci(stato, h("p", { class: "nota", role: "status" }, `In coda: posizione ${v.posizione}.`));
        else if (v.stato === "in_corso") sostituisci(stato, scheletro("Sto cercando lezioni nelle correzioni…"));
        else {
          sostituisci(stato);
          const esito = v.esito || {};
          if (esito.ok) { conferma(`${(esito.dati?.proposte || []).length} proposte nuove`); carica(); caricaProposta(); }
          else { pulsante.disabled = false; if (esito.error_type) mostraErrore(box, esito); }
        }
      });
    } }, costo ? `Proponi lezioni · ${costo}` : "Proponi lezioni");
    const stato = h("div", { "aria-live": "polite" });
    sostituisci(proposta, sezione("Proponi nuove lezioni",
      h("ul", {}, schemi.map((s) => h("li", {}, `${s.categoria}${s.dettaglio ? ` · ${s.dettaglio}` : ""}: ${s.occorrenze} volte in ${(s.episodi || []).length} episodi`))),
      h("p", { class: "nota costo" }, `${testoCosto} (circa ${numero(d.stima?.input_tokens_stimati)} token in ingresso).`),
      h("div", { class: "riga-azioni" }, pulsante), stato, box));
  }

  function caricaExtra() {
    const box = boxErrore();
    const contenuto = h("div", { "aria-live": "polite" });
    sostituisci(extra, sezione("Controlli",
      h("div", { class: "riga-azioni" },
        h("button", { type: "button", class: "pulsante", onclick: async () => {
          const { corpo: r } = await leggi("/api/lezioni/revisione");
          if (!r.ok) { mostraErrore(box, r); return; }
          const nf = r.dati.lezioni_non_funzionanti || [];
          sostituisci(contenuto, nf.length ? h("ul", {}, nf.map((x) => h("li", {}, `${x.id}: lo schema si è ripresentato ${x.occorrenze_dopo} volte in ${x.episodi_controllati} episodi da quando è attiva.`)))
            : h("p", { class: "nota" }, "Nessuna lezione attiva segnalata come non funzionante."));
        } }, "Le lezioni attive funzionano?"),
        h("button", { type: "button", class: "pulsante", onclick: async () => {
          const { corpo: r } = await leggi("/api/lezioni/andamento");
          if (!r.ok) { mostraErrore(box, r); return; }
          const ep = r.dati.episodi || [];
          sostituisci(contenuto, h("p", { class: "nota" }, r.dati.avvertenza || ""),
            ep.length ? h("div", { class: "tabella-contenitore" }, h("table", { class: "tabella" },
              h("caption", { class: "solo-lettori" }, "Andamento per episodio"),
              h("thead", {}, h("tr", {}, ["Episodio", "Revisioni", "Battute modificate", "Retake", "Correzioni dopo l'ascolto"].map((t) => h("th", { scope: "col" }, t)))),
              h("tbody", {}, ep.map((x) => h("tr", {},
                h("td", {}, x.episodio), h("td", {}, numero(x.revisioni_totali)),
                h("td", {}, x.percentuale_turni_modificati === null || x.percentuale_turni_modificati === undefined ? "—" : `${numero(x.percentuale_turni_modificati)}%`),
                h("td", {}, numero(x.retake_totali)), h("td", {}, numero(x.correzioni_post_ascolto))))))) : h("p", { class: "nota" }, "Nessun episodio ancora."));
        } }, "Andamento per episodio")),
      contenuto, box));
  }

  await Promise.all([carica(), caricaProposta()]);
  caricaExtra();
  return () => { if (chiudiAttesa) chiudiAttesa(); };
}
