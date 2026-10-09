// Libreria Voci (Voci.dc.html). Ogni voce e' una persona reale: il consenso viene prima
// di tutto. Nuova voce (registrazione, nome pronunciato, consenso), ascolto del campione,
// consenso con lo sha256 e la revisione mostrati, revoca, nome pronunciato, sostituzione.
// La clonazione (a pagamento) avviene solo alla prima produzione, dopo la stima.

import { h, icona, sostituisci, giorno, dataOra, durata, nuovoId } from "../dom.js";
import { leggi, invia, modifica, togli, seg } from "../api.js";
import { boxErrore, sezione } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, domanda, confermaGettone, vuoto } from "../stati.js";
import { puo, sessione } from "../sessione.js";
import { caricaConSostituzione } from "../caricamento.js";

const FILTRI = [["", "Tutte"], ["clonate", "Già clonate"], ["da_clonare", "Da clonare"], ["consenso_da_confermare", "Consenso da confermare"]];
const CONSENSO = { registrato: "Registrato", mancante: "Mancante", superato: "Da confermare: registrazione cambiata", revocato: "Revocato" };
const NOTA_SOSTITUZIONE = "Il consenso va confermato di nuovo per la nuova registrazione, e alla prossima produzione la voce viene clonata di nuovo (a pagamento).";

export function testoRevoca(c) {
  return `Il consenso per questa registrazione era stato revocato${c?.revocato_da ? ` da ${c.revocato_da}` : ""}${c?.revocato_il ? ` il ${dataOra(c.revocato_il)}` : ""}.`;
}

// Consenso su una registrazione revocata (A5 MEDIA-1, C7-sicurezza-3 BASSA-1): mai
// automatico, sempre con questa domanda a parte che mostra la revoca e cosa succede.
export function confermaDopoRevoca(nome, consenso) {
  return domanda({ titolo: "Registrare di nuovo il consenso?",
    testo: `${testoRevoca(consenso)} Registrarlo di nuovo toglie la revoca: la voce di ${nome} torna utilizzabile in produzioni, retake e rimontaggi.`,
    nota: "Conferma solo se la persona ti ha dato di nuovo il consenso. Resta nel registro attività con il tuo nome.",
    opzioni: [{ testo: "Annulla", valore: false }, { testo: "Confermo: la persona ha dato di nuovo il consenso", valore: true, primaria: true }] });
}

export async function mostraVoci(pagina) {
  let filtro = "";
  let cerca = "";
  let attesa = null;
  const estensioni = (sessione.formati.audio || []).join(",");
  const errore = boxErrore();
  const elenco = h("div", { class: "griglia-voci", "aria-live": "polite" });
  const nuova = h("div", {});
  const filtri = h("div", { class: "filtri", role: "group", "aria-label": "Filtra le voci" });
  const ricerca = h("input", { type: "search", id: "cerca-voci", placeholder: "Cerca voci", autocomplete: "off",
    oninput: (e) => { cerca = e.target.value; clearTimeout(attesa); attesa = setTimeout(carica, 300); } });

  sostituisci(pagina,
    h("header", { class: "intestazione-pagina" },
      h("div", {}, h("h1", { tabindex: "-1" }, "Voci"),
        h("p", { class: "nota" }, "Le persone che possono parlare negli episodi. Ogni voce è una persona reale: il suo consenso viene prima di tutto.")),
      puo("voce_carica") ? h("button", { type: "button", class: "pulsante pulsante-primario", onclick: formNuova }, icona("piu"), h("span", {}, "Aggiungi una voce")) : null),
    nuova,
    h("div", { class: "barra-filtri" },
      h("label", { class: "campo-ricerca", for: "cerca-voci" }, icona("cerca"), h("span", { class: "solo-lettori" }, "Cerca voci"), ricerca),
      filtri),
    errore, elenco);

  function disegnaFiltri() {
    sostituisci(filtri, FILTRI.map(([v, t]) => h("button", { type: "button", class: "filtro", "aria-pressed": String(v === filtro),
      onclick: () => { filtro = v; disegnaFiltri(); carica(); } }, t)));
  }

  async function carica() {
    const q = new URLSearchParams();
    if (filtro) q.set("filtro", filtro);
    if (cerca.trim()) q.set("cerca", cerca.trim());
    const { corpo } = await leggi(`/api/voci?${q}`);
    if (!corpo.ok) { mostraErrore(errore, corpo, { riprova: carica }); return; }
    const voci = corpo.dati.voices || [];
    if (!voci.length) {
      sostituisci(elenco, !filtro && !cerca.trim()
        ? vuoto({ titolo: "Nessuna voce", testo: "Aggiungi la registrazione di una persona che ha dato il consenso a clonare la propria voce." })
        : h("p", { class: "nota" }, "Nessuna voce corrisponde alla ricerca."));
    } else {
      sostituisci(elenco, voci.map(scheda));
    }
    const scartati = corpo.dati.rejected || [];
    if (scartati.length) elenco.append(h("p", { class: "nota" }, `File non usati come voci: ${scartati.map((s) => s.file || s.name || s).join(", ")}.`));
  }

  function scheda(v) {
    const box = boxErrore();
    const c = v.consenso || {};
    const statoConsenso = c.da_confermare ? "Da confermare: voce già clonata" : CONSENSO[c.stato] || c.stato || "—";
    const nome = v.nome_pronunciato || v.spoken_name || v.name;
    const fuoriDurata = typeof v.durata_campione_seconds === "number" && (v.durata_campione_seconds < 10 || v.durata_campione_seconds > 30);
    const serveConsenso = c.stato !== "registrato" || c.da_confermare;
    const revocata = c.stato === "revocato" || c.revoca_nel_registro === true;
    const audioId = nuovoId("campione");
    return h("article", { class: "scheda scheda-voce" },
      h("div", { class: "scheda-voce-testa" },
        h("h2", {}, v.name),
        h("span", { class: "nota" }, `Campione ${durata(v.durata_campione_seconds)}`),
        menuVoce(v, box)),
      v.errore ? h("p", { class: "nota nota-errore" }, v.errore === "Doppione" ? "Due file con lo stesso nome: la voce non si usa finché non ne togli uno." : String(v.errore)) : null,
      fuoriDurata ? h("p", { class: "nota" }, "Durata consigliata del campione: da 10 a 30 secondi.") : null,
      h("dl", { class: "riepilogo" },
        h("dt", {}, "Nome pronunciato"), h("dd", {}, v.nome_confermato ? v.nome_pronunciato : h("span", {}, `${v.spoken_name || "—"} `, h("span", { class: "nota" }, "(proposto, non confermato)"))),
        h("dt", {}, "Stato"), h("dd", {}, v.stato === "Già clonata" && v.clonata_il ? `Già clonata il ${giorno(v.clonata_il)}` : v.stato || "—"),
        h("dt", {}, "Consenso"), h("dd", { class: c.stato === "revocato" ? "nota-errore" : null }, statoConsenso,
          c.da && c.stato === "registrato" ? h("span", { class: "nota" }, ` · ${c.da}, ${dataOra(c.il)}`) : null,
          c.stato === "revocato" && c.revocato_da ? h("span", { class: "nota" }, ` · ${c.revocato_da}, ${dataOra(c.revocato_il)}`) : null),
        h("dt", {}, "Usata in"), h("dd", {}, v.usata_in ? `${v.usata_in} ${v.usata_in === 1 ? "episodio" : "episodi"}` : "Mai usata")),
      v.file ? h("div", {}, h("label", { for: audioId, class: "etichetta" }, "Ascolta il campione"),
        h("audio", { id: audioId, controls: true, preload: "none", src: `/api/voci/${seg(v.name)}/campione`, class: "lettore" })) : null,
      serveConsenso && !revocata && puo("voce_consenso") && c.audio_sha256 ? h("button", { type: "button", class: "pulsante pulsante-primario",
        onclick: () => registraConsenso(v, nome, box) }, "Registra il consenso") : null,
      revocata && puo("voce_consenso") && c.audio_sha256 ? h("button", { type: "button", class: "pulsante",
        onclick: () => registraConsenso(v, nome, box, { dopoRevoca: true }) }, "Registra di nuovo il consenso") : null,
      serveConsenso && !puo("voce_consenso") ? h("p", { class: "nota" }, "Il consenso lo registra chi gestisce le voci.") : null,
      box);
  }

  // Menu "⋯": <details> nativo, usabile da tastiera.
  function menuVoce(v, box) {
    const voci = [
      puo("voce_nome") ? h("button", { type: "button", class: "pulsante pulsante-link", onclick: () => rinomina(v, box) }, "Cambia il nome pronunciato") : null,
      puo("voce_carica") ? h("button", { type: "button", class: "pulsante pulsante-link", onclick: () => sostituisciRegistrazione(v, box) }, "Sostituisci la registrazione") : null,
      puo("voce_consenso") && v.consenso?.stato !== "revocato" ? h("button", { type: "button", class: "pulsante pulsante-link nota-errore", onclick: () => revoca(v, box) }, "Revoca il consenso") : null,
    ].filter(Boolean);
    if (!voci.length) return null;
    return h("details", { class: "menu-azioni" },
      h("summary", { "aria-label": `Altre azioni per ${v.name}` }, "⋯"),
      h("div", { class: "menu-azioni-voci" }, voci));
  }

  async function registraConsenso(v, nome, box, { dopoRevoca = false } = {}) {
    pulisciErrore(box);
    const si = dopoRevoca ? await confermaDopoRevoca(nome, v.consenso) : await domanda({ titolo: "Registrare il consenso?",
      testo: `Confermo che ${nome} ha dato il consenso a clonare la propria voce per gli episodi.`,
      nota: "Il consenso vale per questa registrazione e viene scritto nel registro attività con il tuo nome.",
      opzioni: [{ testo: "Annulla", valore: false }, { testo: "Confermo il consenso", valore: true, primaria: true }] });
    if (!si) return;
    const { corpo: r } = await invia(`/api/voci/${seg(v.name)}/consenso`,
      { audio_sha256: v.consenso.audio_sha256, revisione: v.consenso.revisione, dopo_revoca: dopoRevoca });
    if (r.ok) { conferma(`Consenso registrato per ${v.name}`); carica(); return; }
    if (r.error_type === "ConsensoDopoRevoca") {
      await carica();
      mostraErrore(errore, { ...r, messaggio: "Nel frattempo la registrazione risulta revocata: niente registrato. Controlla la scheda e, se serve, conferma di nuovo." });
      return;
    }
    if (r.error_type === "RecordingChanged" || r.error_type === "RevokedMeanwhile") {
      await carica();
      mostraErrore(errore, { ...r, messaggio: r.error_type === "RevokedMeanwhile" ? "Nel frattempo il consenso è stato revocato: niente registrato." : "La registrazione è cambiata: ascoltala e conferma di nuovo." });
      return;
    }
    mostraErrore(box, r);
  }

  async function revoca(v, box) {
    pulisciErrore(box);
    const si = await domanda({ titolo: `Revocare il consenso di ${v.name}?`,
      testo: "La voce non si potrà più usare: nessuna produzione, nessun nuovo take e nessun rimontaggio degli episodi che la usano.",
      nota: "La revoca resta nel registro anche se poi la registrazione viene cancellata. Si può dare di nuovo il consenso solo per la stessa registrazione.",
      opzioni: [{ testo: "Annulla", valore: false }, { testo: "Revoca il consenso", valore: true, primaria: true }] });
    if (!si) return;
    const { corpo: r } = await togli(`/api/voci/${seg(v.name)}/consenso`);
    if (r.ok) { conferma(`Consenso revocato per ${v.name}`); carica(); return; }
    mostraErrore(box, r);
  }

  async function rinomina(v, box) {
    pulisciErrore(box);
    const id = nuovoId("nome-voce");
    const campo = h("input", { type: "text", id, maxlength: "100", value: v.nome_pronunciato || v.spoken_name || "", autocomplete: "off" });
    const valore = await new Promise((risolvi) => {
      const dialogo = h("dialog", { class: "dialogo", "aria-labelledby": `${id}-t` },
        h("h2", { id: `${id}-t` }, `Nome pronunciato di ${v.name}`),
        h("label", { for: id, class: "etichetta" }, "Nome e cognome, come vanno pronunciati"), campo,
        h("p", { class: "nota" }, "Lo pronuncia il presentatore negli episodi di tutti."),
        h("div", { class: "opzioni" },
          h("button", { type: "button", class: "pulsante", onclick: () => { dialogo.close(); } }, "Annulla"),
          h("button", { type: "button", class: "pulsante pulsante-primario", onclick: () => { risolvi(campo.value.trim()); dialogo.close(); } }, "Salva")));
      dialogo.addEventListener("close", () => { dialogo.remove(); risolvi(null); });
      document.body.append(dialogo);
      dialogo.showModal();
      campo.focus();
    });
    if (!valore) return;
    await salvaNome(v.name, valore, box);
    carica();
  }

  async function salvaNome(voce, valore, box) {
    const { corpo: r } = await modifica(`/api/voci/${seg(voce)}`, { nome_pronunciato: valore });
    if (r.ok) { conferma(`Nome pronunciato salvato: ${valore}`); return true; }
    if (r.esito === "decisione" && r.error_type === "SpokenNameIncomplete") {
      const c = await confermaGettone(r, { testoConferma: "Usa questo nome", testo: `«${valore}» non ha il cognome. Il presentatore lo pronuncerà così: confermi?` });
      if (c?.corpo.ok) { conferma(`Nome pronunciato salvato: ${valore}`); return true; }
      if (c) mostraErrore(box, c.corpo);
      return false;
    }
    mostraErrore(box, r);
    return false;
  }

  async function sostituisciRegistrazione(v, box) {
    pulisciErrore(box);
    const input = h("input", { type: "file", accept: estensioni, class: "solo-lettori" });
    input.addEventListener("change", async () => {
      const f = input.files[0];
      input.remove();
      if (!f) return;
      const est = f.name.includes(".") ? f.name.slice(f.name.lastIndexOf(".")) : "";
      const file = new File([f], `${v.name}${est}`, { type: f.type });
      const corpo = await caricaConSostituzione("/api/voci", file, { nota: NOTA_SOSTITUZIONE });
      if (!corpo) return;
      if (!corpo.ok) { mostraErrore(box, corpo); return; }
      conferma(`Registrazione sostituita: ${v.name}. Il consenso va confermato di nuovo.`);
      carica();
    });
    document.body.append(input);
    input.click();
  }

  // "Nuova voce": nome e cognome (diventa il nome del file), registrazione, consenso.
  function formNuova() {
    const box = boxErrore();
    const idNome = nuovoId("nuova-nome"), idFile = nuovoId("nuova-file"), idConsenso = nuovoId("nuova-consenso");
    const campoNome = h("input", { type: "text", id: idNome, maxlength: "100", autocomplete: "off" });
    const campoFile = h("input", { type: "file", id: idFile, accept: estensioni });
    const consenso = h("input", { type: "checkbox", id: idConsenso, disabled: !puo("voce_consenso") });
    const salva = h("button", { type: "button", class: "pulsante pulsante-primario", onclick: async () => {
      pulisciErrore(box);
      const nome = campoNome.value.trim();
      const f = campoFile.files[0];
      if (!nome) { mostraErrore(box, { error_type: "RichiestaNonValida", messaggio: "Scrivi nome e cognome della persona." }); campoNome.focus(); return; }
      if (!f) { mostraErrore(box, { error_type: "RichiestaNonValida", messaggio: "Scegli il file della registrazione." }); campoFile.focus(); return; }
      salva.disabled = true;
      try {
        await salvaNuova(nome, f, consenso.checked, box);
      } finally { salva.disabled = false; }
    } }, "Salva la voce");
    sostituisci(nuova, sezione("Nuova voce",
      h("label", { for: idNome, class: "etichetta" }, "Nome e cognome, come vanno pronunciati"), campoNome,
      h("label", { for: idFile, class: "etichetta" }, "Carica la registrazione"), campoFile,
      h("p", { class: "nota" }, `${estensioni.replaceAll(".", "").replaceAll(",", ", ")} · da 10 a 30 secondi`),
      h("div", { class: "messaggio" },
        h("p", { class: "messaggio-titolo" }, "Come registrare un buon campione"),
        h("p", {}, "Da 10 a 30 secondi, in un ambiente silenzioso. Intonazione viva, non letta: includi una domanda e qualche frase con enfasi.")),
      h("div", { class: "consenso" }, consenso,
        h("label", { for: idConsenso }, "Questa persona ha dato il consenso a clonare la propria voce per gli episodi.")),
      !puo("voce_consenso") ? h("p", { class: "nota" }, "Il consenso lo registra chi gestisce le voci: la voce resta in attesa del consenso.") : null,
      h("div", { class: "riga-azioni" }, salva,
        h("button", { type: "button", class: "pulsante", onclick: () => sostituisci(nuova) }, "Annulla")),
      h("p", { class: "nota" }, "La voce viene clonata solo la prima volta che la usi in un episodio, dopo la stima."),
      box));
    campoNome.focus();
  }

  async function salvaNuova(nome, f, conConsenso, box) {
    const est = f.name.includes(".") ? f.name.slice(f.name.lastIndexOf(".")).toLowerCase() : "";
    const file = new File([f], `${nome}${est}`, { type: f.type });
    const corpo = await caricaConSostituzione("/api/voci", file, { nota: NOTA_SOSTITUZIONE });
    if (!corpo) return;
    if (!corpo.ok) { mostraErrore(box, corpo); return; }
    const voce = corpo.dati.nome.replace(/\.[^.]+$/, "");
    if (puo("voce_nome") && !(await salvaNome(voce, nome, box))) { carica(); return; }
    if (conConsenso) {
      // sha256 e revisione come li mostra l'agente: il consenso vale per QUESTA registrazione.
      const { corpo: s } = await leggi(`/api/voci/${seg(voce)}/consenso`);
      if (!s.ok) { mostraErrore(box, s); carica(); return; }
      const c = s.dati.consenso || {};
      if (c.audio_sha256 !== corpo.dati.sha256) {
        mostraErrore(box, { error_type: "RecordingChanged", messaggio: "La registrazione è cambiata subito dopo il caricamento: controlla e registra il consenso dalla scheda." });
        carica();
        return;
      }
      // Il consenso si concatena SOLO per una registrazione nuova senza storia: mai se era
      // stata revocata (stesso sha256 nel registro delle revoche) e mai su una sostituzione.
      // In quei casi la scheda mostra la situazione e serve una conferma a parte.
      if (c.stato === "revocato" || c.revoca_nel_registro === true) {
        sostituisci(nuova);
        await carica();
        mostraErrore(errore, { error_type: "ConsensoDopoRevoca",
          messaggio: `Voce salvata: ${voce}. ${testoRevoca(c)} Il consenso NON è stato registrato: se la persona lo ha dato di nuovo, usa «Registra di nuovo il consenso» nella sua scheda.` });
        return;
      }
      if ((corpo.dati.sostituiti || []).length || !["mancante", "superato"].includes(c.stato)) {
        sostituisci(nuova);
        await carica();
        conferma(`Voce salvata: ${voce}. La registrazione ne ha sostituita una esistente: registra il consenso dalla scheda.`);
        return;
      }
      const { corpo: r } = await invia(`/api/voci/${seg(voce)}/consenso`, { audio_sha256: c.audio_sha256, revisione: c.revisione });
      if (!r.ok) { mostraErrore(box, r); carica(); return; }
    }
    sostituisci(nuova);
    conferma(conConsenso ? `Voce salvata con il consenso: ${voce}` : `Voce salvata: ${voce}. Manca il consenso.`);
    carica();
  }

  disegnaFiltri();
  await carica();
}
