"""Impostazioni: cosa c'e' in config.yaml, in sola lettura, per lo schermo web.

Solo una WHITELIST di chiavi, elencate qui una per una: una chiave nuova in
config.yaml non compare finche' qualcuno non la aggiunge. Mai le chiavi API,
mai i nomi delle variabili d'ambiente che le contengono (`api_key_env`), mai
valori presi dall'ambiente, mai percorsi assoluti (le cartelle si mostrano per
nome). La modifica delle impostazioni non passa da qui.
"""

from __future__ import annotations

from typing import Any

from .retake import directions_config, max_takes

_PROVIDER_OPTIONS = (
    "tts_model", "audio_format", "mp3_bitrate", "latency", "visibility",
    "cost_per_million_chars_usd", "chars_per_second",
)
_TTS_PARAMS = ("normalize", "speed", "volume", "temperature", "top_p", "repetition_penalty")
_SCRIPT = ("model", "effort", "language", "default_format", "target_minutes", "words_per_minute", "max_document_chars")
_AUDIO = (
    "trim_silence", "pause_between_turns_ms", "lead_in_ms", "lead_out_ms", "output_mp3_bitrate",
    "cap_internal_silence", "cap_internal_silence_ms", "dropout_detection", "dropout_max_retries",
    "accepted_extensions",
)
_MUSIC = (
    "intro_gap_ms", "outro_gap_ms", "intro_fade_out_ms", "outro_fade_in_ms", "bed_fade_in_ms",
    "bed_fade_out_ms", "bed_loop_crossfade_ms", "bed_level_db", "ducking", "target_lufs", "true_peak_db",
)
_LESSONS = (
    "recurrence_min_occurrences", "recurrence_min_episodes", "evidence_lookback_episodes", "max_lines",
    "review_after_episodes", "model", "input_cost_per_million_tokens_usd", "output_cost_per_million_tokens_usd",
)


def _pick(section: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: section.get(key) for key in keys if key in section}


def settings_view(config: Any) -> dict[str, Any]:
    options = dict(config.provider_config.get("options") or {})
    limits = config.document_limits
    rate = config.eur_rate
    return {
        "provider": {
            "attivo": config.active_provider,
            **_pick(options, _PROVIDER_OPTIONS),
            "parametri_sintesi": _pick(dict(options.get("tts") or {}), _TTS_PARAMS),
            "costo_clonazione_usd": config.clone_cost_usd,
        },
        "copione": _pick(config.script, _SCRIPT),
        "audio": _pick(config.audio, _AUDIO),
        "musica": _pick(config.music, _MUSIC),
        "documenti": {
            "max_bytes": limits.max_bytes,
            "max_pages": limits.max_pages,
            "max_chars": limits.max_chars,
            "max_unpacked_bytes": limits.max_unpacked_bytes,
            "max_xml_bytes": limits.max_xml_bytes,
            "max_zip_entries": limits.max_zip_entries,
            "max_content_bytes": limits.max_content_bytes,
            "max_seconds": limits.max_seconds,
        },
        "retake": {
            "max_takes": max_takes(config),
            "indicazioni": {label: spec["tag"] for label, spec in directions_config(config).items()},
        },
        "valuta": {"eur_per_usd": rate[0] if rate else None, "data_cambio": rate[1] if rate else None},
        "lezioni": _pick(config.lessons, _LESSONS),
        "cartelle": {
            "documenti": config.documents_dir.name,
            "voci": config.voices_dir.name,
            "musiche": config.music_dir.name,
            "archivio": config.output_dir.name,
        },
    }


__all__ = ["settings_view"]
