// Passo 7 · "Come è detta" (retake): stesso testo approvato, nuova resa. Le indicazioni
// sono un elenco chiuso (niente testo libero verso la sintesi, decisione D1). Il costo
// si vede PRIMA di generare, sul pulsante e nella conferma; la generazione e' legata a
// quella stima (impronta). Poi: ascolto in contesto, scelta del take, rimontaggio.

import { h, sostituisci, costo, dataOra } from "../dom.js";
import { leggi, invia, seg, aspettaLavoro } from "../api.js";
import { boxErrore } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, domanda, confermaGettone, scheletro } from "../stati.js";
import { puo } from "../sessione.js";

export const INDICAZIONI = ["Più lento", "Più veloce", "Più energico", "Più calmo", "Più serio", "Più sorridente", "Più pause"];

// `vaiACosaDice()`: per "Pronuncia diversa", che si corregge nel testo.
export function comeEDetta(run, t, corpo, { vaiACosaDice, suRimontato, registraChiusura }) {
  const scelte = new Set();
  let take = 1;
  let stima = null;
  let attesa = null;
  const box = boxErrore();
  const costoEl = h("p", { class: "costo", "aria-live": "polite" });
  const genera = h("button", { type: "button", class: "pulsante pulsante-primario", disabled: true, onclick: generaTake }, "Genera");
  const indicazioni = h("div", { class: "scelte", role: "group", "aria-label": "Come deve cambiare?" });
  const numeroTake = h("div", { class: "scelte", role: "group", "aria-label": "Nuovi take" });
  const elencoTake = h("div", { "aria-live": "polite" });

  function disegnaIndicazioni() {
    sostituisci(indicazioni,
      INDICAZIONI.map((d) => h("button", { type: "button", class: "scelta", "aria-pressed": String(scelte.has(d)),
        onclick: () => { if (scelte.has(d)) scelte.delete(d); else scelte.add(d); disegnaIndicazioni(); aggiornaStima(); } }, d)),
      h("button", { type: "button", class: "scelta", onclick: vaiACosaDice }, "Pronuncia diversa"));
    sostituisci(numeroTake, [1, 2, 3].map((k) => h("button", { type: "button", class: "scelta", "aria-pressed": String(k === take),
      onclick: () => { take = k; disegnaIndicazioni(); aggiornaStima(); } }, String(k))));
  }

  function parametri() {
    const q = new URLSearchParams({ battuta: String(t.n), take: String(take) });
    for (const d of scelte) q.append("indicazioni", d);
    return q;
  }

  function aggiornaStima() {
    clearTimeout(attesa);
    stima = null;
    genera.disabled = true;
    sostituisci(costoEl, "Calcolo il costo…");
    attesa = setTimeout(async () => {
      pulisciErrore(box);
      const q = parametri();
      const { corpo: r } = await leggi(`/api/episodi/${seg(run)}/retake/stima?${q}`);
      if (q.toString() !== parametri().toString()) return; // nel frattempo e' cambiata la scelta
      if (!r.ok) { sostituisci(costoEl); mostraErrore(box, r); return; }
      stima = r.dati;
      sostituisci(costoEl, `Costo stimato ${costo(stima)} · ${stima.chars} caratteri`);
      sostituisci(genera, `Genera ${take} ${take === 1 ? "take" : "take"} · ${costo(stima)}`);
      genera.disabled = !puo("produrre");
    }, 250);
  }

  async function generaTake() {
    if (!stima) return;
    pulisciErrore(box);
    const si = await domanda({ titolo: `Generare ${take} ${take === 1 ? "nuovo take" : "nuovi take"}?`,
      testo: `Battuta ${t.n} · ${t.speaker}${scelte.size ? ` · ${[...scelte].join(", ")}` : ""}.`,
      nota: `Costo stimato: ${costo(stima)}. È un servizio a pagamento. Il copione e l'approvazione non cambiano.`,
      opzioni: [{ testo: "Annulla", valore: false }, { testo: `Genera · ${costo(stima)}`, valore: true, primaria: true }] });
    if (!si) return;
    const { corpo: r } = await invia(`/api/episodi/${seg(run)}/retake`,
      { battuta: t.n, take, indicazioni: [...scelte], impronta: stima.impronta });
    if (!r.ok) {
      if (r.error_type === "StimaCambiata") aggiornaStima();
      mostraErrore(box, r);
      return;
    }
    genera.disabled = true;
    const chiudi = aspettaLavoro(r.dati.lavoro, (v) => {
      if (!v) { caricaTake(); return; }
      if (v.stato === "in_coda") sostituisci(elencoTake, h("p", { class: "nota", role: "status" }, `In coda: posizione ${v.posizione}.`));
      else if (v.stato === "in_corso") sostituisci(elencoTake, scheletro("Sto generando i nuovi take…"));
      else {
        genera.disabled = !stima;
        const esito = v.esito || {};
        if (esito.ok) { conferma("Nuovi take pronti: ascoltali in contesto"); caricaTake(); }
        else { caricaTake(); if (esito.error_type) mostraErrore(box, esito); }
      }
    });
    registraChiusura(chiudi);
  }

  async function caricaTake() {
    const { corpo: r } = await leggi(`/api/episodi/${seg(run)}/retake/${t.n}?musica=1`);
    if (!r.ok) { sostituisci(elencoTake); mostraErrore(box, r); return; }
    const lista = r.dati.take_list || [];
    let scelto = lista.find((x) => x.scelto)?.take ?? null;
    const nome = `take-${t.n}`;
    const usa = h("button", { type: "button", class: "pulsante pulsante-primario" }, "Usa il take e rimonta");
    const aggiornaUsa = () => {
      const x = lista.find((k) => k.take === scelto);
      sostituisci(usa, `Usa ${x ? x.label : "il take"} e rimonta`);
      usa.disabled = !puo("episodio_modifica") || !x || x.scelto;
    };
    usa.addEventListener("click", () => usaTake(scelto));
    sostituisci(elencoTake,
      h("h3", {}, "Ascolta in contesto"),
      h("p", { class: "nota" }, "Battuta prima, take, battuta dopo, con il sottofondo se c'è."),
      h("ul", { class: "take" }, lista.map((x) => {
        const id = `${nome}-${x.take}`;
        return h("li", { class: "take-riga" },
          h("input", { type: "radio", name: nome, id, checked: x.take === scelto, onchange: () => { scelto = x.take; aggiornaUsa(); } }),
          h("label", { for: id }, h("span", { class: "forte" }, x.label), x.scelto ? " · nell'episodio" : "",
            (x.indicazioni || []).length ? h("span", { class: "nota" }, ` · ${x.indicazioni.join(", ")}`) : null,
            x.generato_il ? h("span", { class: "nota" }, ` · ${dataOra(x.generato_il)}`) : null),
          x.anteprima_musica || x.anteprima
            ? h("audio", { controls: true, preload: "none", class: "lettore-piccolo", src: x.anteprima_musica || x.anteprima, "aria-label": `Ascolta ${x.label} in contesto` })
            : h("span", { class: "nota" }, "Anteprima non disponibile"));
      })),
      h("div", { class: "riga-azioni" }, usa),
      h("p", { class: "nota" }, "I take scartati restano disponibili."));
    aggiornaUsa();
  }

  async function usaTake(k) {
    pulisciErrore(box);
    const { corpo: r } = await invia(`/api/episodi/${seg(run)}/retake/scelta`, { battuta: t.n, take: k });
    if (!r.ok) { mostraErrore(box, r); return; }
    await rimonta(box, run, suRimontato);
  }

  sostituisci(corpo,
    h("h3", {}, "Come deve cambiare?"), indicazioni,
    h("p", { class: "nota" }, "Altre indicazioni con parole tue non sono disponibili: alla sintesi arriva solo il testo del copione approvato, con queste indicazioni."),
    h("h3", {}, "Nuovi take"), numeroTake,
    costoEl,
    puo("produrre") ? h("div", { class: "riga-azioni" }, genera) : h("p", { class: "nota" }, "Generare nuovi take costa: serve il permesso di produrre."),
    box, elencoTake);
  disegnaIndicazioni();
  aggiornaStima();
  caricaTake();
}

// Rimontaggio dalla cache (take scelti, musiche cambiate): nessuna sintesi, non costa.
export async function rimonta(box, run, suRimontato) {
  const { corpo: r } = await invia(`/api/episodi/${seg(run)}/rimontaggio`, {});
  if (r.ok) { conferma("Episodio rimontato"); suRimontato(); return; }
  if (r.esito === "decisione" && r.error_type === "MusicChanged") {
    const c = await confermaGettone(r, { testoConferma: "Usa il brano nuovo e rimonta",
      testo: "Un brano dell'episodio è stato sostituito dopo il montaggio. Rimontare con il brano nuovo?" });
    if (c?.corpo.ok) { conferma("Episodio rimontato"); suRimontato(); }
    else if (c) mostraErrore(box, c.corpo);
    return;
  }
  mostraErrore(box, r);
}
