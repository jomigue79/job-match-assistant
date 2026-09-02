---
trigger: always_on
---

# Architectural Guardrails & Seams

- **Core Tech Stack**: Python 3.11+, Streamlit (for both User Interface and Async Pipeline Monitoring), SQLite (System of Record).
- **Architecture Philosophy**: Clean Architecture / Modular Monolith. Highly decoupled.
- **Persistence Boundary**: Database must use SQLite with Write-Ahead Logging (`PRAGMA journal_mode=WAL;`). All schema changes must eventually be trackable via code.
- **Downstream Synchronization**: The spreadsheet is treated strictly as an output target. The system must not read from an external spreadsheet to resolve pipeline state.
- **Staging / Cost Isolation**: Raw scraped data must be dumped in JSON format directly to `data/raw_scrapes/` prior to normalization, allowing offline pipeline testing without running up Apify compute charges.
- **Agent Rule**: Follow Spec-Driven Development (SDD). Generate an implementation plan and wait for human review before changing files.