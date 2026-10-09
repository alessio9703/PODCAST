// Passo 3 · Musiche (03-Musiche.dc.html). Facoltativo: intro, sottofondo, outro.
// "Nessuna musica" in una sezione = null (music_set --none). "Salta" = tutte null.

import { h, sostituisci, durata } from "../dom.js";
import { leggi, sostituisci as put, seg } from "../api.js";
import { intestazionePasso, statoRun, boxErrore, piede, sezione, pulsanteIndietro } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, domanda, titolo as titoloEsito } from "../stati.js";
import { puo } from "../sessione.js";

const SEZIONI = [
  ["intro", "Intro", "All'inizio, prima delle voci."],
  ["sottofondo", "Sottofondo", "A volume basso, sotto le voci, in loop."],
  ["outro", "Outro", "Alla fine, dopo le voci."],
];

export async function mostraMusiche(pagina, { run }) {
  const [c, m, s] = await Promise.all([leggi(`/api/episodi/${seg(run)}/configurazione`), leggi("/api/musiche"), statoRun(run)]);
  if (!c.corpo.ok) { const box = boxErrore(); sostituisci(pagina, box); mostraErrore(box, c.corpo); return; }
  let conf = c.corpo.dati;
  const libreria = m.corpo.ok ? m.corpo.dati : {};
  const modificabile = puo("episodio_modifica");
  const errore = boxErrore();
  const corpo = h("div", {});

  sostituisci(pagina,
    intestazionePasso({ run, passo: 3, titolo: conf.titolo, stato: s?.etichetta || "Bozza", raggiunto: s?.passo || 3 }),
    h("h2", { class: "domanda-passo" }, "Vuoi aggiungere delle musiche?"),
    h("p", { class: "nota" }, "Facoltativo. Puoi cambiarle anche a episodio montato."),
    errore, corpo);

  async function scegli(modifiche, box) {
    pulisciErrore(errore);
    if (box) pulisciErrore(box);
    const { corpo: r } = await put(`/api/episodi/${seg(run)}/musiche`, modifiche);
    if (!r.ok && r.esito === "decisione" && r.error_type === "AmbiguousName") {
      const scelta = await domanda({ titolo: titoloEsito(r), testo: r.messaggio,
        opzioni: [...(r.details?.candidates || []).map((n) => ({ testo: n, valore: n })), { testo: "Annulla", valore: null }] });
      if (scelta && r.details?.category) return scegli({ [r.details.category]: scelta }, box);
      return false;
    }
    if (!r.ok) { mostraErrore(box || errore, r); return false; }
    const nuova = await leggi(`/api/episodi/${seg(run)}/configurazione`);
    if (nuova.corpo.ok) conf = nuova.corpo.dati;
    conferma("Musiche salvate");
    disegna();
    return true;
  }

  function pezzo(cat, nome) {
    const v = (conf.musiche || {})[cat];
    const scelto = v && typeof v === "object";
    return h("li", { class: ["schema-pezzo", scelto ? "schema-musica" : "schema-vuota"] }, scelto ? `${nome} · ${v.nome}` : `${nome} · nessuna`);
  }

  function brani(categoria) {
    return (libreria[categoria] || []).filter((b) => b.usable);
  }

  function disegna() {
    const conFocus = document.activeElement?.id;
    disegnaSezioni();
    if (conFocus) document.getElementById(conFocus)?.focus();
  }

  function disegnaSezioni() {
    const scelte = conf.musiche || {};
    const di = conf.durata_indicativa || {};
    sostituisci(corpo,
      sezione("Struttura dell'episodio",
        h("ol", { class: "schema-episodio" },
          pezzo("intro", "Intro"),
          h("li", { class: "schema-pezzo schema-voci" }, `Parlato · ~${durata(di.parlato_seconds)}`,
            scelte.sottofondo && typeof scelte.sottofondo === "object" ? ` · sottofondo ${scelte.sottofondo.nome}` : " · nessun sottofondo"),
          pezzo("outro", "Outro")),
        h("p", {}, h("span", { class: "nota" }, "Durata totale indicativa "), h("span", { class: "forte" }, durata(di.totale_seconds)))),
      SEZIONI.map(([cat, nome, aiuto]) => sezioneBrano(cat, nome, aiuto, scelte[cat])),
      piede(
        pulsanteIndietro(`#/episodi/${seg(run)}/2`),
        modificabile ? h("button", { type: "button", class: "pulsante", onclick: async () => {
          if (await scegli({ intro: null, outro: null, sottofondo: null })) location.hash = `#/episodi/${seg(run)}/4`;
        } }, "Salta, nessuna musica") : null,
        h("button", { type: "button", class: "pulsante pulsante-primario", disabled: !modificabile, onclick: async () => {
          // "Avanti" conta come scelta fatta: le sezioni non scelte restano senza musica.
          const da = Object.fromEntries(SEZIONI.filter(([cat]) => scelte[cat] === "non_scelta").map(([cat]) => [cat, null]));
          if (Object.keys(da).length === 0 || await scegli(da)) location.hash = `#/episodi/${seg(run)}/4`;
        } }, "Avanti: scrivi il copione")),
    );
  }

  function sezioneBrano(cat, nome, aiuto, scelta) {
    const box = boxErrore();
    const id = `brano-${cat}`;
    const attuale = scelta && typeof scelta === "object" ? scelta.nome : "";
    const elenco = brani(cat);
    const brano = elenco.find((b) => b.name === attuale);
    return sezione(nome,
      h("p", { class: "nota" }, aiuto),
      h("label", { for: id, class: "etichetta" }, "Brano"),
      h("select", { id, disabled: !modificabile, onchange: (e) => scegli({ [cat]: e.target.value || null }, box) },
        h("option", { value: "", selected: !attuale }, "Nessuna musica"),
        elenco.map((b) => h("option", { value: b.name, selected: b.name === attuale }, `${b.name} · ${durata(b.duration_seconds)}`))),
      scelta && typeof scelta === "object" && scelta.disponibile === false
        ? h("p", { class: "nota nota-errore" }, "Il brano scelto non è più disponibile: scegline un altro.") : null,
      brano ? h("p", { class: "etichette" },
        h("span", { class: ["badge", brano.has_licenza ? "badge-ok" : "badge-attenzione"] }, brano.has_licenza ? "Licenza ok" : "Licenza mancante"),
        h("span", { class: "badge badge-neutro" }, brano.parlato ? "Contiene parlato" : "Solo strumentale"),
        cat === "sottofondo" ? h("span", { class: "badge badge-neutro" }, "in loop") : null) : null,
      brano && !brano.has_licenza ? h("p", { class: "nota" }, "Puoi usarlo, ma verifica i diritti prima di pubblicare l'episodio.") : null,
      !elenco.length ? h("p", { class: "nota" }, "Nessun brano in libreria per questa sezione.") : null,
      box);
  }

  disegna();
}
