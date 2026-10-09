// Passo 4 · Copione (04-Copione.dc.html): leggi, modifica, chiedi una modifica, approva.
// La stesura e la revisione COSTANO (modello che scrive il copione): partono solo da un
// pulsante, con l'avviso del costo; l'agente non ne da' una stima in euro.

import { h, sostituisci, dataOra, durata, nuovoId } from "../dom.js";
import { leggi, invia, modifica, togli, seg, aspettaLavoro } from "../api.js";
import { intestazionePasso, statoRun, boxErrore, piede, sezione, pulsanteIndietro, pulsanteAvanti } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, chiudiAvvisi, scheletro, domanda } from "../stati.js";
import { puo } from "../sessione.js";

const RUOLI = { presentatore: "Presentatore", intervistatore: "Intervistatore", ospite: "Ospite", voce: "Voce" };
const AVVISO_COSTO = "Usa il modello che scrive il copione: è un servizio a pagamento. L'agente non ne dà una stima prima della chiamata.";

export async function mostraCopione(pagina, { run }) {
  let chiudiAttesa = null;
  let copione = null;
  let stato = null;
  const errore = boxErrore();
  const corpo = h("div", {});

  async function ricarica() {
    const [c, s] = await Promise.all([leggi(`/api/episodi/${seg(run)}/copione`), statoRun(run)]);
    stato = s;
    if (!c.corpo.ok) { sostituisci(pagina, errore); mostraErrore(errore, c.corpo, { riprova: ricarica }); return; }
    copione = c.corpo.dati;
    sostituisci(pagina,
      intestazionePasso({ run, passo: 4, titolo: copione.titolo || s?.titolo, stato: s?.etichetta || "Bozza", raggiunto: s?.passo || 4 }),
      h("h2", { class: "domanda-passo" }, "Leggi il copione e approvalo"),
      h("p", { class: "nota" }, "Modificalo quanto vuoi. Senza approvazione non si genera nulla."),
      errore, corpo);
    disegna();
  }

  // Lavoro in coda (stesura o revisione): attesa, poi esito.
  function segui(idLavoro) {
    if (chiudiAttesa) chiudiAttesa();
    chiudiAttesa = aspettaLavoro(idLavoro, (v) => {
      if (!v) { ricarica(); return; }
      if (v.stato === "in_coda") {
        sostituisci(corpo, h("p", { class: "nota", role: "status" }, `In coda: posizione ${v.posizione}. Il lavoro parte appena il servizio è libero.`));
      } else if (v.stato === "in_corso") {
        sostituisci(corpo, scheletro(v.azione === "revisiona_copione" ? "Sto rivedendo il copione…" : "Sto scrivendo il copione…"));
      } else {
        const esito = v.esito || {};
        ricarica().then(() => {
          if (esito.ok) conferma(`Copione salvato · versione ${esito.dati?.version ?? ""}`);
          else if (esito.error_type) mostraErrore(errore, esito, { riprova: puo("copione_scrivi") ? () => scrivi() : null });
        });
      }
    });
  }

  async function scrivi(istruzioni) {
    pulisciErrore(errore);
    const url = istruzioni ? `/api/episodi/${seg(run)}/copione/revisione` : `/api/episodi/${seg(run)}/copione/stesura`;
    const { corpo: r } = await invia(url, istruzioni ? { istruzioni } : {});
    if (!r.ok) { mostraErrore(errore, r); return; }
    segui(r.dati.lavoro);
  }

  function disegna() {
    // In corso (anche lanciato da un altro utente o prima di un riavvio).
    if (copione.in_corso) {
      sostituisci(corpo, scheletro(copione.in_corso.operazione === "revisione_copione" ? "Sto rivedendo il copione…" : "Sto scrivendo il copione…"),
        copione.in_corso.istruzione ? h("p", { class: "nota" }, `Richiesta: «${copione.in_corso.istruzione}»`) : null);
      const timer = setTimeout(ricarica, 4000);
      chiudiAttesa = () => clearTimeout(timer);
      return;
    }
    if (!copione.has_script) {
      sostituisci(corpo, sezione("Il copione non è ancora stato scritto",
        h("p", {}, "Il copione viene scritto a partire dal documento, con il cast e la durata dei passi precedenti."),
        h("p", { class: "nota costo" }, AVVISO_COSTO),
        puo("copione_scrivi")
          ? h("button", { type: "button", class: "pulsante pulsante-primario", onclick: () => scrivi() }, "Scrivi il copione")
          : h("p", { class: "nota" }, "Non hai il permesso di far scrivere il copione."),
      ), piede(pulsanteIndietro(`#/episodi/${seg(run)}/3`)));
      return;
    }
    sostituisci(corpo,
      h("div", { class: "colonne" },
        h("div", { class: "colonna-principale" }, testoCopione()),
        h("aside", { class: "colonna-laterale" }, pannelloApprovazione(), pannelloRevisione(), pannelloVersioni())),
      piede(pulsanteIndietro(`#/episodi/${seg(run)}/3`),
        pulsanteAvanti("Continua: stima e consenso", `#/episodi/${seg(run)}/5`, !!copione.approved)));
  }

  function testoCopione() {
    const c = copione;
    return sezione(null,
      h("div", { class: "testa-copione" },
        h("h2", {}, `Versione ${c.version}`),
        h("p", { class: "nota" }, `${c.turn_count} battute · ~${durata(c.duration_hint_seconds)}`),
        h("p", { class: "legenda" }, h("span", { class: "badge badge-presentatore" }, "Presentatore"), " ", h("span", { class: "badge badge-neutro" }, "Partecipanti"),
          " ", h("span", { class: "tag" }, "[tag]"), h("span", { class: "nota" }, " Espressività"))),
      (c.errors || []).length ? h("div", { class: "messaggio messaggio-errore", role: "alert" },
        h("p", { class: "messaggio-titolo" }, "Errori da correggere prima dell'approvazione"),
        h("ul", {}, c.errors.map((e) => h("li", {}, e)))) : null,
      (c.warnings || []).length ? h("div", { class: "messaggio messaggio-attenzione" },
        h("p", { class: "messaggio-titolo" }, "Da controllare"), h("ul", {}, c.warnings.map((e) => h("li", {}, e)))) : null,
      h("ol", { class: "battute" }, (c.turni_strutturati || []).map(battuta)));
  }

  function battuta(t) {
    const box = boxErrore();
    const li = h("li", { class: "battuta" });
    const mostra = () => sostituisci(li,
      h("div", { class: "battuta-testa" },
        h("span", { class: "battuta-n" }, String(t.n).padStart(2, "0")),
        h("span", { class: "forte" }, t.speaker),
        h("span", { class: ["badge", t.presentatore ? "badge-presentatore" : "badge-neutro"] }, t.presentatore ? "Presentatore" : (RUOLI[t.ruolo] || "Partecipante"))),
      h("p", { class: "battuta-testo" }, (t.parti || [{ tipo: "testo", testo: t.testo }]).map((p) => p.tipo === "tag" ? h("span", { class: "tag" }, p.testo) : p.testo)),
      puo("copione_modifica") ? h("button", { type: "button", class: "pulsante pulsante-link", "aria-label": `Modifica la battuta ${t.n}`, onclick: modifica_ }, "Modifica la battuta") : null,
      box);
    const modifica_ = () => {
      const id = nuovoId("battuta");
      const area = h("textarea", { id, rows: "4", maxlength: "5000" }, t.testo);
      sostituisci(li,
        h("label", { for: id, class: "etichetta" }, `Battuta ${t.n} · ${t.speaker}`),
        area,
        h("p", { class: "nota" }, "Le indicazioni fra [quadre] cambiano la resa, ad esempio [pausa] o [ride]."),
        h("div", { class: "riga-azioni" },
          h("button", { type: "button", class: "pulsante pulsante-primario", onclick: async () => {
            pulisciErrore(box);
            const versione = copione.version;
            const { corpo: r } = await modifica(`/api/episodi/${seg(run)}/copione/battute/${t.n}`, { testo: area.value, versione_attesa: versione });
            if (r.ok) {
              await ricarica();
              conferma(`Copione salvato · versione ${r.dati.version}`, { annulla: () => annullaModifica(r.dati.version) });
            } else if (r.error_type === "VersionConflict") {
              await ricarica();
              mostraErrore(errore, r);
            } else { li.append(box); mostraErrore(box, r); }
          } }, "Salva battuta"),
          h("button", { type: "button", class: "pulsante", onclick: mostra }, "Annulla")),
        box);
      area.focus();
    };
    mostra();
    return li;
  }

  // "Annulla" dopo un salvataggio: nuova versione uguale alla precedente (C7, N22).
  async function annullaModifica(versione) {
    const { corpo: r } = await invia(`/api/episodi/${seg(run)}/copione/ripristino`, { versione: versione - 1, versione_attesa: versione });
    await ricarica();
    if (r.ok) conferma(`Modifica annullata · versione ${r.dati.version}`);
    else mostraErrore(errore, r);
  }

  function pannelloApprovazione() {
    const c = copione;
    const box = boxErrore();
    if (c.approved) {
      return sezione(`Approvato · versione ${c.approved_version}`,
        h("p", {}, `Approvato da ${c.approved_by || "—"} il ${dataOra(c.approved_at)}.`),
        h("p", { class: "nota" }, "Il prossimo passo mostra la stima del costo prima di generare."),
        h("a", { class: "pulsante pulsante-primario", href: `#/episodi/${seg(run)}/5` }, "Continua: stima e consenso"),
        puo("copione_approva") ? h("button", { type: "button", class: "pulsante pulsante-link", onclick: async () => {
          const { corpo: r } = await togli(`/api/episodi/${seg(run)}/copione/approvazione`);
          if (r.ok) { await ricarica(); conferma("Approvazione annullata"); } else mostraErrore(box, r);
        } }, "Annulla approvazione") : null, box);
    }
    return sezione("Decisione tua",
      h("p", { class: "forte" }, "Approva questa versione"),
      h("p", { class: "nota" }, "L'approvazione vale sul testo che hai letto. Se dopo cambi anche una sola battuta, dovrai approvare di nuovo."),
      c.approval_problem && c.approved_version ? h("p", { class: "nota" }, c.approval_problem) : null,
      puo("copione_approva")
        ? h("button", { type: "button", class: "pulsante pulsante-primario", disabled: !c.approvable, onclick: async () => {
          pulisciErrore(box);
          const { corpo: r } = await invia(`/api/episodi/${seg(run)}/copione/approvazione`, { versione: c.version });
          if (r.ok) { chiudiAvvisi(); await ricarica(); conferma(`Copione approvato · versione ${r.dati.version}`); }
          else if (r.error_type === "VersionConflict") { await ricarica(); mostraErrore(errore, r); }
          else mostraErrore(box, r);
        } }, "Approva il copione")
        : h("p", { class: "nota" }, "Serve il ruolo di approvatore per approvare."),
      !c.approvable ? h("p", { class: "nota" }, "Correggi prima gli errori segnalati.") : null,
      box);
  }

  function pannelloRevisione() {
    if (!puo("copione_scrivi")) return null;
    const id = nuovoId("istruzioni");
    const area = h("textarea", { id, rows: "3", maxlength: "4000", placeholder: "Ad esempio: intro più breve, più esempi concreti" });
    return sezione("Chiedi una modifica",
      h("label", { for: id, class: "etichetta" }, "Descrivi cosa cambiare, con parole tue"),
      area,
      h("p", { class: "nota costo" }, AVVISO_COSTO),
      h("button", { type: "button", class: "pulsante", onclick: async () => {
        if (!area.value.trim()) { area.focus(); return; }
        const ok = await domanda({ titolo: "Riscrivere il copione?", testo: "Viene creata una nuova versione; l'approvazione attuale decade.",
          nota: AVVISO_COSTO, opzioni: [{ testo: "Annulla", valore: false }, { testo: "Riscrivi il copione", valore: true, primaria: true }] });
        if (ok) scrivi(area.value.trim());
      } }, "Riscrivi il copione"),
      h("p", { class: "nota" }, "Crea una nuova versione."));
  }

  function pannelloVersioni() {
    const versioni = (copione.versions || []).slice().reverse();
    if (!versioni.length) return null;
    return sezione("Versioni", h("ol", { class: "versioni", reversed: true }, versioni.map((v) => h("li", {},
      h("span", { class: "forte" }, `v${v.version}`),
      v.current ? h("span", { class: "badge badge-neutro" }, "corrente") : null,
      v.approved ? h("span", { class: "badge badge-ok" }, "approvata") : null,
      v.approved_but_outdated ? h("span", { class: "badge badge-attenzione" }, "approvata in passato") : null,
      h("span", { class: "nota" }, ` ${v.instruction ? `«${v.instruction}»` : (v.kind || "").replace("_", " ")} · ${dataOra(v.at)}${v.by ? ` · ${v.by}` : ""}`)))));
  }

  await ricarica();
  return () => { if (chiudiAttesa) chiudiAttesa(); };
}
