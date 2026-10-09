// Caricamento in una libreria con la sostituzione confermata: un nome che c'e' gia'
// risponde 409 FileEsistente con un gettone; se l'utente conferma si rimanda il file.

import { caricaFile } from "./api.js";
import { domanda, titolo } from "./stati.js";

// Restituisce il corpo della risposta, oppure null se l'utente non ha confermato.
export async function caricaConSostituzione(url, file, { nota } = {}) {
  const { stato, corpo } = await caricaFile(url, file);
  if (stato !== 409 || corpo.error_type !== "FileEsistente" || !corpo.gettone) return corpo;
  const esistenti = (corpo.details?.esistenti || []).join(", ");
  const si = await domanda({
    titolo: titolo(corpo),
    testo: `Nella libreria c'è già «${esistenti}». Vuoi sostituirlo con «${file.name}»?`,
    nota,
    opzioni: [{ testo: "Annulla", valore: false }, { testo: "Sostituisci", valore: true, primaria: true }],
  });
  if (!si) return null;
  return (await caricaFile(url, file, corpo.gettone)).corpo;
}

// Un <input type=file> nascosto con la sua etichetta-pulsante (tastiera compresa).
// Con `multiplo` `suFile` riceve un elenco di file, altrimenti un file.
export function sceltaFile(h, { id, accept, testo, multiplo = false, suFile }) {
  const input = h("input", { type: "file", id, accept, multiple: multiplo, class: "solo-lettori",
    onchange: (e) => {
      const files = [...e.target.files];
      e.target.value = "";
      if (files.length) suFile(multiplo ? files : files[0]);
    } });
  return { input, etichetta: h("label", { class: "pulsante", for: id }, testo) };
}
