# Plan: the writer gets its own LLM profile

Status: proposed
Branch: feat/writer-llm-profile

## Why
The scorer runs on every scraped job and must stay on a cheap model. The writer runs once per letter, and four rounds of prompt rules failed on the current model. The writer needs its own provider, model, key and rates. Today one LLM_* triple serves both: build_llm_client() (src/llm/client.py:119) takes no arguments and both adapters read get_settings() in argument-less constructors (openai_adapter.py:12, google_adapter.py:13).

## Design
1. LLMProfile — a frozen value object: provider, model, api_key (SecretStr), base_url (optional), input_token_rate_usd, output_token_rate_usd.
2. Settings gains optional WRITER_LLM_PROVIDER, WRITER_LLM_MODEL, WRITER_LLM_API_KEY, WRITER_LLM_BASE_URL, WRITER_LLM_INPUT_TOKEN_RATE_USD, WRITER_LLM_OUTPUT_TOKEN_RATE_USD. Settings exposes main_llm_profile() and writer_llm_profile(). Every unset writer field falls back to its LLM_* counterpart.
3. Validation at startup: if the writer provider differs from the main provider and WRITER_LLM_API_KEY is unset, fail with a clear error — a key for one provider never works on another. A base_url given to the google provider is an error, not silently ignored.
4. Adapters take the profile in their constructor instead of reading settings. get_adapter(provider) is unchanged; the registry constructs adapters with the profile.
5. build_llm_client(profile=None): None means the main profile, so every existing caller and test keeps working unchanged.
6. src/ui/main.py:23 passes build_llm_client(settings.writer_llm_profile()) to Writer. The scorer path (run_coordinator.py:450) is untouched.
7. Cost estimates for writer calls use the writer profile's rates.
8. Each LLM call logs provider and model (never the key). The structlog redaction processor is unchanged.
9. .env.example documents the WRITER_LLM_* block, commented out, with an OpenAI example (provider "openai", model gpt-6.1-sol as listed on developers.openai.com/api/docs/models on 2026-10-01, base URL left empty for OpenAI itself) and a note that rates must match the model and must be re-checked before use.

## Revisions after implementation review

1. Item 7's mechanism. No cost accumulator reaches the writer today: src/ui/page.py:235 passes none, and the only two accumulators live inside scraping runs (run_coordinator.py:123, :323), so letter cost was never estimated or stored. The writer now builds its own accumulator from its client's profile rates when none is handed in, and logs the summary. Log only: no schema change, no per-letter cost row, and page.py is untouched.
2. get_adapter takes the profile. It instantiated adapters with no arguments (adapter.py:43), so the signature is now get_adapter(provider_name, profile). The registry itself is unchanged.
3. Provider logging is new, not existing. client.py logged model but never provider. provider and model are now on the success log and on all three failure logs (timeout, transient, permanent).
4. A third validator clause. LLM_BASE_URL with LLM_PROVIDER=google was silently ignored by google_adapter.py, the same defect the plan caught for the writer. It is an error now too.
5. An inherited-rate warning. A writer on another provider or model with both rate fields unset prices its letters at the scoring model's rate, and no validation can infer the right number. Settings logs a warning at load; it is not an error.
6. Temperature is a per-skill literal, not a default: writer.py:117-119 and scorer.py:127-129. Out of scope here, as the plan says, but hardcoded rather than configurable.
7. get_settings is lru_cached (settings.py:137-138), so any WRITER_LLM_* change needs an app restart. The UI also runs with reload=False (D10).

LLMProfile lives in src/llm/types.py, with deferred imports inside the two Settings methods: llm.client already imports config, so a module-level import of llm from config would close a cycle. observability/metrics.py:30-42 dodges the same cycle the same way.

## Out of scope
The two-step writer (next plan). Temperature (stays hardcoded per skill for now). Any scorer change. No new dependency.

## Tests
- Writer profile falls back field by field to the main profile when WRITER_LLM_* is unset.
- Different writer provider without WRITER_LLM_API_KEY raises at settings load.
- base_url with provider google raises.
- The writer receives a client built from the writer profile; the scorer from the main profile.
- Writer cost uses writer rates.
- A logged call contains provider and model and does not contain the key value.

## Acceptance
- py -m pytest -q passes.
- With no WRITER_LLM_* set, the app behaves exactly as today.
- With WRITER_LLM_* pointing at a second provider, a regenerated letter's log line shows that provider and model, and a scoring run's log lines still show the main one.