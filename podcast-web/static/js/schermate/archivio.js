// Archivio (Main.dc.html): ricerca, filtri per gruppo, tabella con la prossima azione.

import { h, icona, sostituisci, data } from "../dom.js";
import { leggi } from "../api.js";
import { badgeStato, boxErrore } from "../componenti.js";
import { mostraErrore, vuoto } from "../stati.js";
import { puo } from "../sessione.js";

const GRUPPI = [["", "Tutti"], ["bozze", "Bozze"], ["da_approvare", "Da approvare"], ["in_produzione", "In produzione"], ["pronti", "Pronti"]];
const FORMATI = { conversazione: "Conversazione", intervista: "Intervista" };

export async function mostraArchivio(pagina) {
  let gruppo = "";
  let cerca = "";
  let attesa = null;
  const errore = boxErrore();
  const corpoTabella = h("div", { class: "tabella-contenitore", "aria-live": "polite" });
  const filtri = h("div", { class: "filtri", role: "group", "aria-label": "Filtra per stato" });
  const ricerca = h("input", { type: "search", id: "cerca-episodi", placeholder: "Cerca per titolo o documento", autocomplete: "off",
    oninput: (e) => { cerca = e.target.value; clearTimeout(attesa); attesa = setTimeout(carica, 300); } });

  sostituisci(pagina,
    h("header", { class: "intestazione-pagina" },
      h("div", {},
        h("h1", { tabindex: "-1" }, "Archivio"),
        h("p", { class: "nota" }, "Tutti gli episodi, dalle bozze a quelli pronti. Ognuno riparte dal punto in cui l'hai lasciato.")),
      puo("episodio_modifica") ? h("a", { class: "pulsante pulsante-primario", href: "#/nuovo" }, icona("piu"), h("span", {}, "Nuovo episodio")) : null),
    h("div", { class: "barra-filtri" },
      h("label", { class: "campo-ricerca", for: "cerca-episodi" }, icona("cerca"), h("span", { class: "solo-lettori" }, "Cerca episodi"), ricerca),
      filtri),
    errore,
    corpoTabella);

  function disegnaFiltri(conteggi = {}) {
    sostituisci(filtri, GRUPPI.map(([valore, etichetta]) => h("button", {
      type: "button", class: "filtro", "aria-pressed": String(valore === gruppo),
      onclick: () => { gruppo = valore; carica(); },
    }, etichetta, valore && typeof conteggi[valore] === "number" ? h("span", { class: "conteggio" }, ` ${conteggi[valore]}`) : null)));
  }

  async function carica() {
    const parametri = new URLSearchParams();
    if (gruppo) parametri.set("gruppo", gruppo);
    if (cerca.trim()) parametri.set("cerca", cerca.trim());
    const { corpo } = await leggi(`/api/episodi?${parametri}`);
    if (!corpo.ok) { mostraErrore(errore, corpo, { riprova: carica }); return; }
    errore.hidden = true;
    disegnaFiltri(corpo.dati.conteggi);
    const episodi = corpo.dati.episodi || [];
    if (!episodi.length) {
      const tutto = !gruppo && !cerca.trim();
      sostituisci(corpoTabella, tutto
        ? vuoto({ titolo: "Non hai ancora nessun episodio", testo: "Parti da un documento: in pochi passi diventa un podcast con le voci del tuo team.",
          azione: puo("episodio_modifica") ? { href: "#/nuovo", testo: "Crea il primo episodio" } : null })
        : h("p", { class: "nota" }, "Nessun episodio corrisponde alla ricerca."));
      return;
    }
    sostituisci(corpoTabella, h("table", { class: "tabella" },
      h("caption", { class: "solo-lettori" }, "Episodi"),
      h("thead", {}, h("tr", {},
        h("th", { scope: "col" }, "Episodio"), h("th", { scope: "col" }, "Formato"), h("th", { scope: "col" }, "Voci"),
        h("th", { scope: "col" }, "Stato"), h("th", { scope: "col" }, "Aggiornato"), h("th", { scope: "col", class: "a-destra" }, "Prossima azione"))),
      h("tbody", {}, episodi.map(riga))));
  }

  function riga(e) {
    const primaria = e.stato === "copione_da_approvare";
    const azione = e.passo
      ? h("a", { class: ["pulsante", "pulsante-piccolo", primaria ? "pulsante-primario" : ""], href: `#/episodi/${encodeURIComponent(e.run)}/${e.passo}`,
        "aria-label": `${e.azione}: ${e.titolo}` }, e.azione)
      : h("span", { class: "nota" }, e.errore ? "File della run illeggibili" : "—");
    return h("tr", {},
      h("td", {}, h("div", { class: "forte" }, e.titolo || e.run), h("div", { class: "nota" }, e.documento || "—")),
      h("td", {}, FORMATI[e.formato] || "—"),
      h("td", {}, e.voci ?? "—"),
      h("td", {}, badgeStato(e)),
      h("td", { class: "nota" }, data(e.aggiornato_il)),
      h("td", { class: "a-destra" }, azione));
  }

  disegnaFiltri();
  await carica();
  // L'Archivio si aggiorna da solo mentre qualcosa e' in produzione.
  const timer = setInterval(() => { if (!document.hidden) carica(); }, 10000);
  return () => { clearInterval(timer); clearTimeout(attesa); };
}
