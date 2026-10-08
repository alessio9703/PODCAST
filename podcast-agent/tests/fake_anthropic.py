"""Client Anthropic finto: risponde con copioni preparati dal test, mai con l'API.

E' l'equivalente di tests/fake_provider.py per la scrittura del copione. Si
attiva da config.yaml, con lo stesso meccanismo del provider vocale:

    script:
      client_factory: tests.fake_anthropic:build
      client_options:
        responses_file: /tmp/.../risposte.json   # lista di risposte, in coda
        log_file: /tmp/.../richieste.jsonl       # una riga per chiamata

Le risposte vivono in un FILE e non in memoria perche' ogni tool e' un
processo separato: la seconda invocazione (una revisione) deve trovare la
risposta successiva, non di nuovo la prima. Ogni chiamata consuma la prima
risposta della lista e riscrive il file con le rimanenti.

Forme ammesse per una risposta:

    {"json": {...}}             -> testo JSON, stop_reason "end_turn"
    {"text": "..."}             -> testo grezzo (per provare JSON non valido)
    {"stop_reason": "refusal"}  -> rifiuto del modello
    {"stop_reason": "max_tokens"}
    {"raise": "auth" | "permission" | "credit" | "rate_limit" | "overloaded"
              | "unavailable" | "connection", "retry_after": 7}
                                -> solleva la stessa eccezione della libreria
                                   anthropic che solleverebbe l'API vera

Il log registra modello, system prompt e messaggi di ogni chiamata: i test lo
leggono per verificare COSA e' stato chiesto al modello (formato, presentatore,
istruzioni di revisione), non solo cosa e' stato risposto.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class FakeAnthropicError(RuntimeError):
    """Il test non ha preparato abbastanza risposte."""


class _Messages:
    def __init__(self, owner: "FakeAnthropic") -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> SimpleNamespace:
        return self._owner._respond(kwargs)


class FakeAnthropic:
    def __init__(self, responses_file: str, log_file: str | None = None) -> None:
        self._responses_file = Path(responses_file)
        self._log_file = Path(log_file) if log_file else None
        self.messages = _Messages(self)

    def _respond(self, request: dict[str, Any]) -> SimpleNamespace:
        if self._log_file is not None:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "model": request.get("model"),
                "system": request.get("system"),
                "messages": request.get("messages"),
            }
            with self._log_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

        queue = (
            json.loads(self._responses_file.read_text(encoding="utf-8"))
            if self._responses_file.is_file()
            else []
        )
        if not queue:
            raise FakeAnthropicError(
                f"Nessuna risposta preparata in {self._responses_file}: il test "
                "ha fatto piu' chiamate al modello di quante ne aspettasse."
            )
        answer, rest = queue[0], queue[1:]
        self._responses_file.write_text(json.dumps(rest, ensure_ascii=False), encoding="utf-8")

        if "raise" in answer:
            raise _api_error(answer["raise"], answer.get("retry_after"))

        if "json" in answer:
            text = json.dumps(answer["json"], ensure_ascii=False)
        else:
            text = str(answer.get("text", ""))
        return SimpleNamespace(
            stop_reason=answer.get("stop_reason", "end_turn"),
            stop_details=None,
            content=[SimpleNamespace(type="text", text=text)],
        )


def _api_error(kind: str, retry_after: float | None) -> Exception:
    """Le eccezioni VERE della libreria anthropic, con risposta HTTP finta."""
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    headers = {"retry-after": str(retry_after)} if retry_after is not None else {}

    def response(status: int):  # noqa: ANN202
        return httpx2.Response(status, headers=headers, request=request)

    if kind == "connection":
        return anthropic.APIConnectionError(request=request)
    table = {
        "auth": (anthropic.AuthenticationError, 401, "invalid x-api-key"),
        "permission": (anthropic.PermissionDeniedError, 403, "permission denied"),
        "credit": (anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API."),
        "rate_limit": (anthropic.RateLimitError, 429, "rate limit exceeded"),
        "overloaded": (getattr(anthropic, "OverloadedError", anthropic.InternalServerError), 529, "Overloaded"),
        "unavailable": (anthropic.InternalServerError, 500, "internal error"),
    }
    cls, status, message = table[kind]
    return cls(message, response=response(status), body=None)


def queue_responses(responses_file: Path, *answers: dict[str, Any]) -> None:
    """Aggiunge risposte in coda (comodo nei test)."""
    queue = (
        json.loads(responses_file.read_text(encoding="utf-8"))
        if responses_file.is_file()
        else []
    )
    queue.extend(answers)
    responses_file.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")


def read_log(log_file: Path) -> list[dict[str, Any]]:
    if not log_file.is_file():
        return []
    return [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def build(options: dict[str, Any]) -> FakeAnthropic:
    return FakeAnthropic(**options)
