// Passo 5 · Stima e consenso (05-Conferma.dc.html). Il costo e' sempre in vista prima
// di generare, e si ripete nella conferma. Il consenso si registra con lo sha256 e la
// revisione MOSTRATI (se nel frattempo cambiano, l'agente risponde con un esito 3).

import { h, sostituisci, durata, numero, costo, giorno } from "../dom.js";
import { leggi, invia, seg, aspettaLavoro } from "../api.js";
import { intestazionePasso, statoRun, boxErrore, piede, sezione, pulsanteIndietro } from "../componenti.js";
import { mostraErrore, pulisciErrore, conferma, domanda, titolo as titoloEsito } from "../stati.js";
import { puo } from "../sessione.js";

const FORMATI = { conversazione: "Conversazione", intervista: "Intervista" };
const RUOLI = { presentatore: "Presentatore", intervistatore: "Intervistatore", ospite: "Ospite", voce_a: "Voce A", voce_b: "Voce B", voce: "Voce" };

export async function mostraStima(pagina, { run }) {
  const errore = boxErrore();
  const corpo = h("div", {});
  let chiudiAttesa = null;

  async function ricarica() {
    const [st, conf, s] = await Promise.all([leggi(`/api/episodi/${seg(run)}/stima`), leggi(`/api/episodi/${seg(run)}/configurazione`), statoRun(run)]);
    const titolo = conf.corpo.ok ? conf.corpo.dati.titolo : s?.titolo;
    sostituisci(pagina,
      intestazionePasso({ run, passo: 5, titolo, stato: s?.etichetta || "", raggiunto: s?.passo || 5 }),
      h("h2", { class: "domanda-passo" }, "Controlla la stima prima di generare"),
      h("p", { class: "nota" }, "Verifica i numeri e conferma il consenso delle persone la cui voce viene clonata."),
      errore, corpo);
    if (!st.corpo.ok) { mostraErrore(errore, st.corpo, { riprova: ricarica }); return; }
    disegna(st.corpo.dati, conf.corpo.ok ? conf.corpo.dati : {});
  }

  function disegna(stima, conf) {
    const mancaConsenso = new Set(stima.consent_missing || []);
    const pronto = stima.approved && mancaConsenso.size === 0;
    const incerte = (stima.voices || []).filter((v) => v.clonazione_incerta);
    const musiche = conf.musiche || {};
    const nomiMusiche = ["intro", "sottofondo", "outro"].filter((c) => musiche[c] && typeof musiche[c] === "object");
    sostituisci(corpo,
      h("div", { class: "cifre" },
        cifra("Costo stimato", costo(stima), stima.cost_complete === false ? "manca il costo delle clonazioni: non è noto" : "clonazione e sintesi"),
        cifra("Durata stimata", `~${durata(stima.duration_with_music_seconds ?? stima.duration_seconds)}`,
          `musiche incluse${stima.duration_label ? ` · fra ${stima.duration_label.replace(" - ", " e ")}` : ""}`),
        cifra("Battute da generare", `${numero(stima.turns_to_generate)} di ${numero(stima.turns)}`, "le altre sono già pronte"),
        cifra("Caratteri", numero(stima.chars_to_generate ?? stima.chars), `dal copione v${stima.approved_version ?? "—"}`)),
      !stima.approved ? h("div", { class: "messaggio messaggio-attenzione" },
        h("p", { class: "messaggio-titolo" }, "Il copione non è approvato"),
        h("p", {}, stima.approval_problem || "Approva il copione prima di generare."),
        h("a", { class: "pulsante", href: `#/episodi/${seg(run)}/4` }, "Torna al copione")) : null,
      sezione("Voci dell'episodio", h("ul", { class: "voci-episodio" }, (stima.voices || []).map((v) => voce(v, mancaConsenso.has(v.speaker))))),
      incerte.length ? sezione("Clonazioni da verificare", incerte.map(clonazioneIncerta)) : null,
      sezione("Riepilogo",
        h("dl", { class: "riepilogo" },
          h("dt", {}, "Copione"), h("dd", {}, stima.approved ? `v${stima.approved_version} · approvato` : "non approvato"),
          h("dt", {}, "Formato"), h("dd", {}, FORMATI[conf.formato] || "—"),
          h("dt", {}, "Musiche"), h("dd", {}, nomiMusiche.length ? nomiMusiche.join(", ") : "nessuna")),
        h("a", { class: "pulsante pulsante-link", href: `#/episodi/${seg(run)}/4` }, "Rileggi il copione")),
      sezione("Pronto a generare",
        h("p", {}, "Clonazione delle voci nuove, sintesi delle battute e montaggio. Se si interrompe, riprendi da dove si è fermata."),
        puo("produrre")
          ? h("button", { type: "button", class: "pulsante pulsante-primario pulsante-grande", disabled: !pronto, onclick: () => genera(stima) },
            `Genera l'episodio · ${costo(stima)}`)
          : h("p", { class: "nota" }, "Serve il ruolo di approvatore per generare l'episodio."),
        !pronto && stima.approved ? h("p", { class: "nota" }, `Manca il consenso per: ${[...mancaConsenso].join(", ")}.`) : null),
      piede(pulsanteIndietro(`#/episodi/${seg(run)}/4`), h("p", { class: "nota" }, "Torna più tardi: la bozza resta in Archivio.")));
  }

  function cifra(etichetta, valore, nota) {
    return h("div", { class: "cifra" }, h("p", { class: "nota" }, etichetta), h("p", { class: "cifra-valore" }, valore), h("p", { class: "nota" }, nota));
  }

  function voce(v, serveConsenso) {
    const box = boxErrore();
    const nome = v.consenso?.nome_pronunciato || v.speaker;
    const azione = v.azione === "reuse" ? `Già clonata il ${giorno(v.clonata_il)}` : "Da clonare per la prima volta";
    let casella = null;
    if (serveConsenso) {
      const id = `consenso-${v.speaker.replace(/[^A-Za-z0-9]/g, "_")}`;
      const input = h("input", { type: "checkbox", id, disabled: !puo("voce_consenso"), onchange: async (e) => {
        if (!e.target.checked) return;
        pulisciErrore(box);
        e.target.disabled = true;
        const { corpo: r } = await invia(`/api/voci/${seg(v.speaker)}/consenso`, { audio_sha256: v.consenso?.audio_sha256, revisione: v.consenso?.revisione });
        if (r.ok) { conferma(`Consenso registrato per ${v.speaker}`); ricarica(); return; }
        e.target.checked = false;
        e.target.disabled = false;
        if (["RecordingChanged", "RevokedMeanwhile"].includes(r.error_type)) {
          await domanda({ titolo: titoloEsito(r), testo: `${r.messaggio} Ricarico lo stato aggiornato: se confermi di nuovo, vale la situazione nuova.`,
            opzioni: [{ testo: "Ricarica", valore: true, primaria: true }] });
          ricarica();
          return;
        }
        mostraErrore(box, r);
      } });
      casella = h("div", { class: "consenso" }, input,
        h("label", { for: id }, `Confermo che ${nome} ha dato il consenso a clonare la propria voce e a fargli dire questo copione.`),
        v.consenso?.da_confermare ? h("p", { class: "nota" }, "Voce già clonata, ma il consenso non era mai stato registrato.") : null,
        v.consenso?.stato === "superato" ? h("p", { class: "nota" }, "La registrazione è cambiata dopo il consenso: va confermato di nuovo.") : null,
        v.consenso?.stato === "revocato" ? h("p", { class: "nota nota-errore" }, "Il consenso è stato revocato.") : null,
        !puo("voce_consenso") ? h("p", { class: "nota" }, "Il consenso lo registra chi gestisce le voci.") : null);
    }
    return h("li", { class: "voce-episodio" },
      h("p", {}, h("span", { class: "forte" }, v.speaker), v.ruolo ? h("span", { class: "badge badge-neutro" }, RUOLI[v.ruolo] || v.ruolo) : null),
      h("p", { class: "nota" }, azione), casella, box);
  }

  function clonazioneIncerta(v) {
    const box = boxErrore();
    return h("div", { class: "messaggio messaggio-attenzione" },
      h("p", { class: "messaggio-titolo" }, `Clonazione di ${v.speaker} dall'esito incerto`),
      h("p", {}, "Una clonazione precedente non si sa come sia finita. Prima di ritentare, controlla le voci sul servizio vocale: una voce duplicata va cancellata lì."),
      puo("voce_clonazione")
        ? h("button", { type: "button", class: "pulsante", onclick: async () => {
          const ok = await domanda({ titolo: "Hai controllato sul servizio vocale?",
            testo: `Confermando, la prossima produzione clona di nuovo la voce di ${v.speaker}.`,
            nota: "Una nuova clonazione è a pagamento e, se la precedente era riuscita, crea una voce duplicata.",
            opzioni: [{ testo: "Annulla", valore: false }, { testo: "Ho controllato: ritenta", valore: true, primaria: true }] });
          if (!ok) return;
          const { corpo: r } = await invia(`/api/voci/${seg(v.speaker)}/clonazione-verificata`, {});
          if (!r.ok) { mostraErrore(box, r); return; }
          if (chiudiAttesa) chiudiAttesa();
          chiudiAttesa = aspettaLavoro(r.dati.lavoro, (l) => {
            if (l && ["finito", "annullato"].includes(l.stato)) {
              if (l.esito?.ok) conferma(`Clonazione di ${v.speaker} sbloccata`);
              else if (l.esito) mostraErrore(box, l.esito);
              ricarica();
            }
          });
        } }, "Ho controllato: ritenta la clonazione")
        : h("p", { class: "nota" }, "Chiedi a chi gestisce le voci di controllare e confermare."),
      box);
  }

  async function genera(stima) {
    pulisciErrore(errore);
    const ok = await domanda({
      titolo: "Generare l'episodio?",
      testo: `Costo stimato: ${costo(stima)}. Battute da generare: ${stima.turns_to_generate} di ${stima.turns}.`,
      nota: stima.cost_complete === false ? "Il costo delle clonazioni non è noto e non è compreso." : "Le voci nuove vengono clonate una sola volta, senza tentativi automatici.",
      opzioni: [{ testo: "Annulla", valore: false }, { testo: `Genera · ${costo(stima)}`, valore: true, primaria: true }],
    });
    if (!ok) return;
    const { corpo: r } = await invia(`/api/episodi/${seg(run)}/produzione`, {});
    if (!r.ok) { mostraErrore(errore, r); return; }
    location.hash = `#/episodi/${seg(run)}/6`;
  }

  await ricarica();
  return () => { if (chiudiAttesa) chiudiAttesa(); };
}
