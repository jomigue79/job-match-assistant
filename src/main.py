import streamlit as st
import os
import asyncio
import time
from pathlib import Path
from config import get_settings, ConfigurationError
from persistence import init_db, PersistenceService, JobWithMatch
from coordinator.run_coordinator import build_run_coordinator, RunCoordinator, RunAlreadyActiveError
from domain import RunStatus, JobStatus
from skills.writer import Writer
from knowledge import KnowledgeLoader

# Set premium Streamlit page configurations
st.set_page_config(
    page_title="Job Match Assistant - Operational Control Center",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Helper function to execute async coroutines synchronously in Streamlit
def run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)

# Load configuration safely
try:
    config = get_settings()
    config_error = None
except ConfigurationError as e:
    config = None
    config_error = str(e)
except Exception as e:
    config = None
    config_error = f"Unexpected error: {str(e)}"

# Singleton Service Provider for Streamlit session
@st.cache_resource
def get_services():
    if config_error:
        return None, None, None, None
    settings = get_settings()
    init_db(settings.db_path)
    persistence_service = PersistenceService(settings.db_path)
    coordinator = build_run_coordinator(persistence_service=persistence_service)
    writer = Writer()
    knowledge_loader = KnowledgeLoader(settings.knowledge_dir)
    return persistence_service, coordinator, writer, knowledge_loader

persistence_service, coordinator, writer, knowledge_loader = get_services()

# Inject modern Google Fonts (Outfit) and premium CSS for rich aesthetics and Glassmorphism
custom_css = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');

/* Apply font across all streamlit elements */
html, body, [class*="css"], .stMarkdown, p, h1, h2, h3, h4, h5, h6, span, label, div {
    font-family: 'Outfit', sans-serif !important;
}

/* Background gradient styling for main viewport */
.stApp {
    background: radial-gradient(circle at 10% 20%, rgba(15, 23, 42, 1) 0%, rgba(9, 11, 20, 1) 90%);
    color: #F1F5F9;
}

/* Glassmorphism main dashboard panel */
.dashboard-card {
    background: rgba(30, 41, 59, 0.45);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 20px;
    padding: 1.75rem;
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    margin-bottom: 1.5rem;
    transition: transform 0.3s ease, border-color 0.3s ease;
}

.dashboard-card:hover {
    border-color: rgba(99, 102, 241, 0.4);
    box-shadow: 0 12px 40px 0 rgba(99, 102, 241, 0.15);
}

/* Pulsing operational indicator animation */
.pulse-indicator {
    display: inline-block;
    width: 14px;
    height: 14px;
    background-color: #10B981;
    border-radius: 50%;
    margin-right: 12px;
    box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
    animation: pulse 2s infinite;
    vertical-align: middle;
}

.pulse-indicator-running {
    display: inline-block;
    width: 14px;
    height: 14px;
    background-color: #F59E0B;
    border-radius: 50%;
    margin-right: 12px;
    box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.7);
    animation: pulse-running 1.2s infinite;
    vertical-align: middle;
}

.pulse-indicator-error {
    display: inline-block;
    width: 14px;
    height: 14px;
    background-color: #EF4444;
    border-radius: 50%;
    margin-right: 12px;
    box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7);
    animation: pulse-error 2s infinite;
    vertical-align: middle;
}

@keyframes pulse {
    0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
    70% { transform: scale(1); box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); }
    100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
}

@keyframes pulse-running {
    0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.7); }
    70% { transform: scale(1.1); box-shadow: 0 0 0 12px rgba(245, 158, 11, 0); }
    100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }
}

@keyframes pulse-error {
    0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
    70% { transform: scale(1); box-shadow: 0 0 0 10px rgba(239, 68, 68, 0); }
    100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
}

/* Glowing system status banner */
.status-banner {
    display: flex;
    align-items: center;
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.1) 0%, rgba(5, 150, 105, 0.2) 100%);
    border: 1px solid rgba(16, 185, 129, 0.3);
    border-radius: 12px;
    padding: 0.85rem 1.25rem;
    margin-bottom: 1.5rem;
}

.status-banner-running {
    display: flex;
    align-items: center;
    background: linear-gradient(135deg, rgba(245, 158, 11, 0.1) 0%, rgba(217, 119, 6, 0.2) 100%);
    border: 1px solid rgba(245, 158, 11, 0.4);
    border-radius: 12px;
    padding: 0.85rem 1.25rem;
    margin-bottom: 1.5rem;
}

.status-banner-error {
    display: flex;
    align-items: center;
    background: linear-gradient(135deg, rgba(239, 68, 68, 0.1) 0%, rgba(220, 38, 38, 0.2) 100%);
    border: 1px solid rgba(239, 68, 68, 0.3);
    border-radius: 12px;
    padding: 0.85rem 1.25rem;
    margin-bottom: 1.5rem;
}

.status-text {
    font-size: 1.05rem;
    font-weight: 600;
    color: #34D399;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

.status-text-running {
    font-size: 1.05rem;
    font-weight: 600;
    color: #FBBF24;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

.status-text-error {
    font-size: 1.05rem;
    font-weight: 600;
    color: #F87171;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

/* Header gradient text styling */
.gradient-header {
    background: linear-gradient(135deg, #A5B4FC 0%, #6366F1 50%, #4F46E5 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-weight: 700;
    font-size: 2.75rem;
    margin-bottom: 0.15rem;
}

.sub-header {
    color: #94A3B8;
    font-size: 1rem;
    margin-bottom: 1.5rem;
    font-weight: 300;
}

/* Custom table/key-value look */
.config-row {
    display: flex;
    justify-content: space-between;
    padding: 0.6rem 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.config-row:last-child {
    border-bottom: none;
}

.config-key {
    color: #94A3B8;
    font-weight: 500;
}

.config-val {
    color: #E2E8F0;
    font-family: monospace;
    background: rgba(15, 23, 42, 0.6);
    padding: 0.15rem 0.5rem;
    border-radius: 6px;
    border: 1px solid rgba(255, 255, 255, 0.04);
}

.score-badge-high {
    background: rgba(16, 185, 129, 0.15);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: #34D399;
    font-family: monospace;
    font-weight: 700;
    font-size: 1.25rem;
    padding: 0.25rem 0.75rem;
    border-radius: 12px;
}

.score-badge-mid {
    background: rgba(99, 102, 241, 0.15);
    border: 1px solid rgba(99, 102, 241, 0.3);
    color: #818CF8;
    font-family: monospace;
    font-weight: 700;
    font-size: 1.25rem;
    padding: 0.25rem 0.75rem;
    border-radius: 12px;
}

.pill {
    display: inline-block;
    padding: 0.2rem 0.6rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
}

.pill-success {
    background-color: rgba(16, 185, 129, 0.15);
    color: #34D399;
    border: 1px solid rgba(16, 185, 129, 0.2);
}

.pill-warn {
    background-color: rgba(245, 158, 11, 0.15);
    color: #FBBF24;
    border: 1px solid rgba(245, 158, 11, 0.2);
}
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# Top Bar Header & Action Buttons
head_col1, head_col2 = st.columns([2, 1])

with head_col1:
    st.markdown("<h1 class='gradient-header'>Job Match Assistant</h1>", unsafe_allow_html=True)
    st.markdown("<div class='sub-header'>Operational Control Center & Match Engine</div>", unsafe_allow_html=True)

with head_col2:
    st.write("")
    st.write("")
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    
    with btn_col1:
        if st.button("⚡ Run Pipeline", type="primary", use_container_width=True):
            if coordinator:
                try:
                    coordinator.start_run()
                    st.toast("⚡ Background pipeline run triggered!", icon="🚀")
                except (RunAlreadyActiveError, Exception) as e:
                    if isinstance(e, RunAlreadyActiveError) or "already active" in str(e).lower():
                        st.warning("Pipeline is currently active and running. Please wait for it to complete.")
                    else:
                        st.error(f"Failed to trigger pipeline: {e}")
            else:
                st.error("System configuration error.")

    with btn_col2:
        if st.button("🔄 Sync Ledger", use_container_width=True):
            if coordinator:
                try:
                    run_async(coordinator.sync_ledger())
                    st.toast("🔄 Ledger synchronized successfully!", icon="✅")
                except Exception as e:
                    st.error(f"Sync failed: {e}")
            else:
                st.error("System configuration error.")

    with btn_col3:
        if st.button("🔁 Refresh", use_container_width=True):
            if coordinator and coordinator._active_run and coordinator._active_run.status in (RunStatus.DONE, RunStatus.FAILED):
                coordinator._active_run = None
            st.rerun()

# Check Run Coordinator Status
active_run = coordinator.get_active_run() if coordinator else None
is_running = active_run is not None and active_run.status == RunStatus.RUNNING

if config_error:
    st.markdown("""
    <div class="status-banner-error">
        <span class="pulse-indicator-error"></span>
        <span class="status-text-error">System Configuration Error</span>
    </div>
    """, unsafe_allow_html=True)
elif is_running:
    st.markdown(f"""
    <div class="status-banner-running">
        <span class="pulse-indicator-running"></span>
        <span class="status-text-running">Pipeline Execution Active — Scraped: {active_run.n_scraped} | Genuinely New: {active_run.n_new} | Matched: {active_run.n_matched} | No Match: {active_run.n_no_match}</span>
    </div>
    """, unsafe_allow_html=True)
else:
    st.markdown("""
    <div class="status-banner">
        <span class="pulse-indicator"></span>
        <span class="status-text">System Operational & Ready</span>
    </div>
    """, unsafe_allow_html=True)

# Fetch Lifetime System Metrics & Listings from SQLite Persistence
if persistence_service:
    counters = run_async(persistence_service.counters())
    status_breakdown = run_async(persistence_service.status_breakdown())
    jobs_with_match = run_async(persistence_service.list_jobs_with_match())
else:
    counters = None
    status_breakdown = {}
    jobs_with_match = []

# Lifetime Metrics Row
if counters:
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    with m_col1:
        st.metric("Total Postings", counters.total)
    with m_col2:
        matched_count = status_breakdown.get(JobStatus.MATCHED, 0) + status_breakdown.get(JobStatus.WRITTEN, 0) + status_breakdown.get("matched", 0) + status_breakdown.get("written", 0)
        st.metric("Strong Matches", matched_count)
    with m_col3:
        st.metric("Applied Jobs", counters.applied)
    with m_col4:
        st.metric("Rejected Jobs", counters.rejected)

st.write("---")

# Partition Jobs into Groups
matches = []
applied = []
non_matches = []
pending = []
rejected = []

for jm in jobs_with_match:
    status = jm.job.status
    if status in (JobStatus.MATCHED, JobStatus.WRITTEN):
        matches.append(jm)
    elif status == JobStatus.APPLIED:
        applied.append(jm)
    elif status == JobStatus.NO_MATCH:
        non_matches.append(jm)
    elif status == JobStatus.SCRAPED:
        pending.append(jm)
    elif status == JobStatus.REJECTED:
        rejected.append(jm)

# Interactive Job Operational Tabs
tab_matches, tab_applied, tab_nonmatches, tab_rejected = st.tabs([
    f"🌟 Strong Matches ({len(matches)})",
    f"🎯 Applied ({len(applied)})",
    f"📊 Non-Matches & Unscored ({len(non_matches) + len(pending)})",
    f"🗑️ Rejected ({len(rejected)})"
])

# Async Action Helper Handlers
def handle_generate_cover_letter(identity_hash: str):
    async def _impl():
        knowledge = knowledge_loader.load()
        match_result = await persistence_service.get_match_result(identity_hash)
        job = await persistence_service.get_job(identity_hash)
        if not match_result or not job:
            raise ValueError("Match result or job posting missing from database.")
        letter_text = await writer.generate(job, knowledge, match_result)
        await persistence_service.save_cover_letter(identity_hash, letter_text)
        if job.status != JobStatus.WRITTEN:
            await persistence_service.set_status(identity_hash, JobStatus.WRITTEN)
    try:
        run_async(_impl())
        st.toast("✍️ Cover letter generated successfully!", icon="📝")
        st.rerun()
    except Exception as e:
        st.error(f"Failed to generate cover letter: {e}")

def handle_set_status(identity_hash: str, new_status: JobStatus):
    try:
        run_async(persistence_service.set_status(identity_hash, new_status))
        st.toast(f"Status updated to {new_status.value.upper()}", icon="✅")
        st.rerun()
    except Exception as e:
        st.error(f"Failed to update status: {e}")

# Tab 1: Strong Matches
with tab_matches:
    if not matches:
        st.info("No strong matched job postings found. Run the pipeline to scrape and score new jobs.")
    else:
        for jm in matches:
            job = jm.job
            match = jm.match
            score = match.score if match else 0
            badge_class = "score-badge-high" if score >= 80 else "score-badge-mid"
            
            with st.container():
                st.markdown(f"""
                <div class="dashboard-card">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <div>
                            <h3 style="margin: 0; color: #F1F5F9; font-weight: 700; font-size: 1.35rem;">
                                <a href="{job.url or '#'}" target="_blank" style="color: #A5B4FC; text-decoration: none;">{job.title or 'Unknown Title'}</a>
                            </h3>
                            <p style="margin: 0.25rem 0 0.75rem 0; color: #94A3B8; font-size: 0.95rem;">
                                {job.company or 'Unknown Company'} • {job.location or 'Remote'}
                            </p>
                        </div>
                        <div class="{badge_class}">
                            {score} / 100
                        </div>
                    </div>
                """, unsafe_allow_html=True)
                
                # ATS Breakdown & Evidence
                if match and match.dimension_breakdown:
                    c1, c2 = st.columns([1, 1])
                    with c1:
                        st.caption("**ATS Dimension Ratings**")
                        for dim, d_score in match.dimension_breakdown.items():
                            st.write(f"- `{dim}`: **{d_score}**")
                    with c2:
                        st.caption("**Key Match Evidence**")
                        for reason in (match.match_reasons or []):
                            st.write(f"• {reason}")
                            
                # Cover Letter Section
                st.write("")
                if jm.letter_text:
                    with st.expander(f"📄 View Generated Cover Letter (v{jm.letter_version or 1})", expanded=True):
                        st.text_area(
                            label="Cover Letter Text",
                            value=jm.letter_text,
                            height=200,
                            key=f"letter_{job.identity_hash}",
                            label_visibility="collapsed"
                        )
                
                # Action Buttons
                ac1, ac2, ac3, ac4 = st.columns([1.5, 1.2, 1.2, 2.1])
                with ac1:
                    if jm.letter_text:
                        if st.button("🔄 Regenerate", key=f"regen_{job.identity_hash}"):
                            handle_generate_cover_letter(job.identity_hash)
                    else:
                        if st.button("✍️ Write Letter", key=f"write_{job.identity_hash}"):
                            handle_generate_cover_letter(job.identity_hash)
                with ac2:
                    if st.button("✅ Mark Applied", key=f"apply_{job.identity_hash}"):
                        handle_set_status(job.identity_hash, JobStatus.APPLIED)
                with ac3:
                    if st.button("❌ Reject", key=f"reject_{job.identity_hash}"):
                        handle_set_status(job.identity_hash, JobStatus.REJECTED)
                with ac4:
                    if jm.letter_text:
                        st.code(jm.letter_text[:60] + "...", language=None)
                
                st.markdown("</div>", unsafe_allow_html=True)

# Tab 2: Applied Jobs
with tab_applied:
    if not applied:
        st.info("No jobs marked as applied yet.")
    else:
        for jm in applied:
            job = jm.job
            score = jm.match.score if jm.match else 0
            with st.container():
                st.markdown(f"""
                <div class="dashboard-card" style="border-color: rgba(16, 185, 129, 0.3);">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <div>
                            <h3 style="margin: 0; color: #F1F5F9; font-weight: 700; font-size: 1.25rem;">
                                <a href="{job.url or '#'}" target="_blank" style="color: #34D399; text-decoration: none;">{job.title}</a>
                            </h3>
                            <p style="margin: 0.25rem 0; color: #94A3B8; font-size: 0.9rem;">
                                {job.company} • {job.location}
                            </p>
                        </div>
                        <span class="pill pill-success">Applied ✓</span>
                    </div>
                """, unsafe_allow_html=True)
                
                if jm.letter_text:
                    with st.expander("📄 Submitted Cover Letter"):
                        st.markdown(f"```text\n{jm.letter_text}\n```")
                        
                if st.button("↩️ Withdraw / Reject", key=f"withd_{job.identity_hash}"):
                    handle_set_status(job.identity_hash, JobStatus.REJECTED)
                    
                st.markdown("</div>", unsafe_allow_html=True)

# Tab 3: Non-Matches & Unscored
with tab_nonmatches:
    col_nm, col_pend = st.columns(2)
    with col_nm:
        st.subheader("Below Threshold Postings")
        if not non_matches:
            st.caption("No non-matched postings.")
        else:
            for jm in non_matches:
                job = jm.job
                score = jm.match.score if jm.match else 0
                st.write(f"- **{job.company}** — {job.title} `(Score: {score})`")
                
    with col_pend:
        st.subheader("Pending Unscored Postings")
        if not pending:
            st.caption("No pending unscored postings.")
        else:
            for jm in pending:
                job = jm.job
                st.write(f"- **{job.company}** — {job.title} `[UNSCORED]`")

# Tab 4: Rejected Jobs
with tab_rejected:
    if not rejected:
        st.info("No rejected postings.")
    else:
        for jm in rejected:
            job = jm.job
            col_rj1, col_rj2 = st.columns([3, 1])
            with col_rj1:
                st.write(f"**{job.company}** — {job.title} ({job.location})")
            with col_rj2:
                if st.button("↩️ Undo Reject", key=f"undo_{job.identity_hash}"):
                    handle_set_status(job.identity_hash, JobStatus.MATCHED)

st.write("---")

# Preserved System Settings & Diagnostics Panel
with st.expander("🛠️ System Settings & Environment Diagnostics", expanded=False):
    if config:
        db_path = config.db_path
        db_exists = Path(db_path).exists()
        db_status_pill = "<span class='pill pill-success'>Active</span>" if db_exists else "<span class='pill pill-warn'>Auto-Init Pending</span>"
        
        ledger_path = config.ledger_xlsx_path
        ledger_exists = Path(ledger_path).exists()
        ledger_status_pill = "<span class='pill pill-success'>Exists</span>" if ledger_exists else "<span class='pill pill-warn'>Will Create</span>"

        def mask_key(k: str) -> str:
            if not k or k in ["your_llm_api_key_here", "mock_llm_api_key_for_testing"]:
                return "Placeholder (Not configured)"
            return f"{k[:8]}...{k[-8:]}" if len(k) > 16 else "Configured (Short Key)"

        llm_key_raw = config.llm_api_key.get_secret_value()
        llm_masked = mask_key(llm_key_raw)

        st.markdown(f"""
        <div class="dashboard-card">
            <div class="config-row">
                <span class="config-key">Database Path</span>
                <div>
                    <span class="config-val">{db_path}</span>
                    {db_status_pill}
                </div>
            </div>
            <div class="config-row">
                <span class="config-key">Ledger XLSX Path</span>
                <div>
                    <span class="config-val">{ledger_path}</span>
                    {ledger_status_pill}
                </div>
            </div>
            <div class="config-row">
                <span class="config-key">Raw Scrapes Dir</span>
                <span class="config-val">{config.raw_scrape_dir}</span>
            </div>
            <div class="config-row">
                <span class="config-key">Log File Path</span>
                <span class="config-val">{config.log_file_path}</span>
            </div>
            <div class="config-row">
                <span class="config-key">Scraper Source</span>
                <span class="config-val">{config.scraper_source}</span>
            </div>
            <div class="config-row">
                <span class="config-key">Scraper Query</span>
                <span class="config-val">{config.scraper_query} ({config.scraper_location}, max: {config.scraper_limit})</span>
            </div>
            <div class="config-row">
                <span class="config-key">LLM Provider / Model</span>
                <span class="config-val">{config.llm_provider} / {config.llm_model}</span>
            </div>
            <div class="config-row">
                <span class="config-key">LLM API Key</span>
                <span class="config-val">{llm_masked}</span>
            </div>
            <div class="config-row">
                <span class="config-key">Score Threshold</span>
                <span class="config-val">{config.score_threshold}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

# Auto-rerun loop while background pipeline is running to provide live progress updates
if is_running:
    time.sleep(1.5)
    st.rerun()

