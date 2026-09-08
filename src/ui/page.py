from dataclasses import dataclass
from typing import List, Optional, Any
from nicegui import ui

from domain import Run, RunStatus, JobPosting, JobStatus, display_location
from persistence import Counters, JobWithMatch
from coordinator import RunCoordinator, RunAlreadyActiveError

from datetime import datetime, timezone

# Sort floor for applied cards whose letter_created_at is None. Substituted only
# inside the sort key so datetime is never compared against None.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

@dataclass(frozen=True)
class MatchCard:
    identity_hash: str
    company: str
    title: str
    location: str
    url: str
    score: int
    dimension_breakdown: dict
    match_reasons: List[str]
    has_letter: bool
    letter_text: Optional[str]
    letter_version: Optional[int]
    letter_created_at: Optional[datetime]
    status: JobStatus

@dataclass(frozen=True)
class NonMatchCompact:
    identity_hash: str
    company: str
    title: str
    score: int

@dataclass(frozen=True)
class PendingCompact:
    identity_hash: str
    company: str
    title: str

@dataclass(frozen=True)
class ViewState:
    status_text: str  # "idle" | "running" | "done" | "failed"
    n_scraped: int
    n_new: int
    total_count: int
    rejected_count: int
    written_count: int
    applied_count: int
    run_button_disabled: bool
    
    # Extended groupings & breakdown
    matches: List[MatchCard]
    non_matches: List[NonMatchCompact]
    pending: List[PendingCompact]
    
    # Placeholders for future phases
    written: List[JobPosting]
    applied: List[MatchCard]
    rejected: List[MatchCard]
    
    # Status breakdown counts
    matched_count: int
    no_match_count: int
    scraped_count: int
    
    # Signature to detect when to rebuild UI cards
    cards_signature: str

def build_view_state(
    active_run: Optional[Run],
    jobs_with_match: List[JobWithMatch],
    counters: Counters,
    status_breakdown: dict
) -> ViewState:
    """
    Pure-ish view-state helper translating domain entity properties into UI metrics.
    Groups jobs by status and returns a cards signature to throttle UI re-renders.
    """
    if active_run is None:
        status_text = "idle"
        n_scraped = 0
        n_new = 0
        run_button_disabled = False
    else:
        status_text = active_run.status.value
        n_scraped = active_run.n_scraped
        n_new = active_run.n_new
        run_button_disabled = (active_run.status == RunStatus.RUNNING)
        
    matches = []
    non_matches = []
    pending = []
    written = []
    applied = []
    rejected = []
    
    for jm in jobs_with_match:
        job = jm.job
        match = jm.match
        
        if job.status in (JobStatus.MATCHED, JobStatus.WRITTEN, JobStatus.APPLIED, JobStatus.REJECTED):
            score_val = match.score if match is not None else 0
            dim_val = match.dimension_breakdown if match is not None else {}
            reasons_val = match.match_reasons if match is not None else []
            has_letter = jm.letter_text is not None
            card = MatchCard(
                identity_hash=job.identity_hash,
                company=job.company or "Unknown Company",
                title=job.title or "Unknown Title",
                location=display_location(job.location) or "Remote",
                url=job.url or "",
                score=score_val,
                dimension_breakdown=dim_val,
                match_reasons=reasons_val,
                has_letter=has_letter,
                letter_text=jm.letter_text,
                letter_version=jm.letter_version,
                letter_created_at=jm.letter_created_at,
                status=job.status
            )
            if job.status in (JobStatus.MATCHED, JobStatus.WRITTEN):
                matches.append(card)
            elif job.status == JobStatus.APPLIED:
                applied.append(card)
            elif job.status == JobStatus.REJECTED:
                rejected.append(card)
        elif job.status == JobStatus.NO_MATCH:
            score_val = match.score if match is not None else 0
            non_matches.append(
                NonMatchCompact(
                    identity_hash=job.identity_hash,
                    company=job.company or "Unknown Company",
                    title=job.title or "Unknown Title",
                    score=score_val
                )
            )
        elif job.status == JobStatus.SCRAPED:
            pending.append(
                PendingCompact(
                    identity_hash=job.identity_hash,
                    company=job.company or "Unknown Company",
                    title=job.title or "Unknown Title"
                )
            )
            
    # Resolve counts dynamically supporting both enum and string representation from SQLite GROUP BY
    def get_count(status_key):
        if status_key in status_breakdown:
            return status_breakdown[status_key]
        if status_key.value in status_breakdown:
            return status_breakdown[status_key.value]
        return 0
        
    matched_count = get_count(JobStatus.MATCHED)
    no_match_count = get_count(JobStatus.NO_MATCH)
    scraped_count = get_count(JobStatus.SCRAPED)
    written_count = get_count(JobStatus.WRITTEN)
    applied_count = get_count(JobStatus.APPLIED)
    rejected_count = get_count(JobStatus.REJECTED)
    
    # Applied cards, newest letter first. letter_created_at is a proxy for the
    # application date -- there is no applied_at column. None sorts last.
    # Two stable passes: date descending, then partition None to the end.
    applied.sort(key=lambda c: c.letter_created_at or _EPOCH, reverse=True)
    applied.sort(key=lambda c: c.letter_created_at is None)

    # Signature
    cards_signature = f"{scraped_count}-{matched_count}-{no_match_count}-{written_count}-{applied_count}-{rejected_count}-{counters.total}"

    return ViewState(
        status_text=status_text,
        n_scraped=n_scraped,
        n_new=n_new,
        total_count=counters.total,
        rejected_count=counters.rejected,
        written_count=counters.written,
        applied_count=counters.applied,
        run_button_disabled=run_button_disabled,
        matches=matches,
        non_matches=non_matches,
        pending=pending,
        written=written,
        applied=applied,
        rejected=rejected,
        matched_count=matched_count,
        no_match_count=no_match_count,
        scraped_count=scraped_count,
        cards_signature=cards_signature
    )

import observability
logger = observability.get_logger("page")

async def generate_cover_letter_handler(
    identity_hash: str,
    persistence,
    writer,
    knowledge_loader,
    generating_hashes: set,
    on_success_callback=None,
    on_error_notify_callback=None
) -> None:
    if identity_hash in generating_hashes:
        return
    
    generating_hashes.add(identity_hash)
    if on_success_callback:
        await on_success_callback()
        
    try:
        # a. Load knowledge
        knowledge = knowledge_loader.load()
        
        # b. Fetch MatchResult
        match_result = await persistence.get_match_result(identity_hash)
        if not match_result:
            raise ValueError("No match result found in database for this job.")
            
        # Retrieve the full JobPosting from database
        job = await persistence.get_job(identity_hash)
        if not job:
            raise ValueError("No job posting found in database.")
            
        # c. Call writer.generate
        letter_text = await writer.generate(job, knowledge, match_result)
        
        # d. Save cover letter
        await persistence.save_cover_letter(identity_hash, letter_text)
        
        # e. Set status to written
        if job.status != JobStatus.WRITTEN:
            await persistence.set_status(job.identity_hash, JobStatus.WRITTEN)
            
    except Exception as e:
        logger.exception("Failed to generate cover letter", identity_hash=identity_hash)
        if on_error_notify_callback:
            try:
                on_error_notify_callback(str(e))
            except RuntimeError as re:
                logger.warning("Error callback notification raised RuntimeError (e.g. parent element deleted)", error=str(re))
        else:
            try:
                ui.notify(f"Error generating cover letter: {e}", type="negative", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Default notification raised RuntimeError (e.g. parent element deleted)", error=str(re))
            
    finally:
        generating_hashes.discard(identity_hash)
        if on_success_callback:
            try:
                await on_success_callback()
            except RuntimeError as re:
                logger.warning("on_success_callback raised RuntimeError (e.g. parent element deleted)", error=str(re))

def build_ui(coordinator: RunCoordinator, persistence, writer, knowledge_loader) -> None:
    """
    Constructs the operational control center NiceGUI UI shell using DI.
    Updates all elements dynamically using an async refresh timer loop.
    """
    ui.add_head_html('''
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');
    body {
        font-family: 'Outfit', sans-serif !important;
        background: radial-gradient(circle at 10% 20%, #0F172A 0%, #090B14 90%) !important;
        color: #F1F5F9 !important;
    }
    .glass-card {
        background: rgba(30, 41, 59, 0.45) !important;
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 16px !important;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37) !important;
        transition: transform 0.3s ease, border-color 0.3s ease;
    }
    .glass-card:hover {
        transform: translateY(-2px);
        border-color: rgba(99, 102, 241, 0.3) !important;
    }
    .q-dialog .glass-card {
        background: rgba(15, 23, 42, 0.98) !important;
        backdrop-filter: none;
        -webkit-backdrop-filter: none;
    }
    .q-dialog .glass-card:hover {
        transform: none;
    }
    </style>
    ''')

    ui.dark_mode().enable()

    generating_hashes = set()

    def copy_letter_to_clipboard(text: str):
        try:
            ui.clipboard.write(text)
            ui.notify("Cover letter copied to clipboard!", type="positive", position="bottom-right")
        except RuntimeError as re:
            logger.warning("Could not copy to clipboard or show notification", error=str(re))

    def show_letter_dialog(m: MatchCard) -> None:
        """
        Open the full letter in a dialog.

        Created per click and cleared on close, per NiceGUI's guidance that a
        Dialog is an element which is hidden rather than removed when closed.
        Creating it here rather than in render_match_card also keeps it out of
        the containers rebuild_cards clears, so an open dialog survives a rebuild.
        """
        with ui.dialog() as dialog, ui.card().classes("glass-card w-full max-w-3xl p-6 gap-4"):
            with ui.row().classes("w-full justify-between items-center"):
                ui.label(f"{m.company} — {m.title}").classes("text-base font-bold text-slate-100")
                ui.label(f"v{m.letter_version}").classes("text-xs font-mono text-slate-400")
            ui.separator().classes("bg-white/10")
            ui.label(m.letter_text or "").style(
                "white-space: pre-wrap; word-break: break-word; max-height: 65vh; "
                "overflow-y: auto; width: 100%; line-height: 1.6;"
            ).classes("text-sm text-slate-200")
            with ui.row().classes("w-full justify-end gap-2 border-t border-white/10 pt-3"):
                copy_btn = ui.button("Copy", on_click=lambda: copy_letter_to_clipboard(m.letter_text))
                copy_btn.classes("bg-slate-700 hover:bg-slate-600 text-white text-xs font-semibold px-3 py-1.5 rounded-lg")
                close_btn = ui.button("Close", on_click=dialog.close)
                close_btn.classes("bg-indigo-650 hover:bg-indigo-500 text-white text-xs font-semibold px-3 py-1.5 rounded-lg")
        dialog.on_value_change(lambda e: dialog.clear() if not e.value else None)
        dialog.open()

    def handle_error_notify(msg: str):
        try:
            ui.notify(f"Error generating cover letter: {msg}", type="negative", position="bottom-right")
        except RuntimeError as re:
            logger.warning("Could not display UI notification because parent element was deleted", error=str(re))

    async def generate_letter(m: MatchCard):
        await generate_cover_letter_handler(
            identity_hash=m.identity_hash,
            persistence=persistence,
            writer=writer,
            knowledge_loader=knowledge_loader,
            generating_hashes=generating_hashes,
            on_success_callback=refresh,
            on_error_notify_callback=handle_error_notify
        )

    async def transition_to_applied(m: MatchCard):
        try:
            await persistence.set_status(m.identity_hash, JobStatus.APPLIED)
            try:
                ui.notify(f"Job at {m.company} marked as Applied!", type="positive", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not show applied notification because parent element was deleted", error=str(re))
        except Exception as e:
            logger.exception("Failed to mark job as applied", identity_hash=m.identity_hash)
            try:
                ui.notify(f"Error marking applied: {e}", type="negative", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not show error notification", error=str(re))
        try:
            await refresh()
        except RuntimeError as re:
            logger.warning("Could not refresh after marking applied", error=str(re))

    async def transition_to_rejected(m: MatchCard):
        try:
            await persistence.set_status(m.identity_hash, JobStatus.REJECTED)
            try:
                ui.notify(f"Job at {m.company} marked as Rejected.", type="info", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not show rejected notification because parent element was deleted", error=str(re))
        except Exception as e:
            logger.exception("Failed to mark job as rejected", identity_hash=m.identity_hash)
            try:
                ui.notify(f"Error marking rejected: {e}", type="negative", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not show error notification", error=str(re))
        try:
            await refresh()
        except RuntimeError as re:
            logger.warning("Could not refresh after marking rejected", error=str(re))

    async def transition_to_matched(m: MatchCard):
        try:
            await persistence.set_status(m.identity_hash, JobStatus.MATCHED)
            try:
                ui.notify(f"Job at {m.company} reset to Matched.", type="info", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not show matched notification because parent element was deleted", error=str(re))
        except Exception as e:
            logger.exception("Failed to reset job to matched", identity_hash=m.identity_hash)
            try:
                ui.notify(f"Error resetting to matched: {e}", type="negative", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not show error notification", error=str(re))
        try:
            await refresh()
        except RuntimeError as re:
            logger.warning("Could not refresh after resetting to matched", error=str(re))

    with ui.column().classes("w-full max-w-4xl mx-auto p-8 gap-6"):
        # Header Row
        with ui.row().classes("w-full justify-between items-center border-b border-white/5 pb-4"):
            with ui.column().classes("gap-1"):
                ui.label("Job Match Assistant").classes("text-3xl font-extrabold tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-indigo-300 via-indigo-400 to-indigo-600")
                ui.label("Operational Control Room").classes("text-xs text-slate-400 font-medium tracking-wide uppercase")
            
            with ui.row().classes("items-center gap-3"):
                # Run Button
                run_button = ui.button("Run Pipeline", on_click=lambda: trigger_run())
                run_button.classes("bg-gradient-to-r from-indigo-600 to-indigo-700 hover:from-indigo-500 hover:to-indigo-600 text-white font-semibold px-6 py-2.5 rounded-xl transition-all shadow-lg shadow-indigo-950/50 disabled:opacity-50")
                
                # Sync Ledger Button
                sync_button = ui.button("Sync Ledger", on_click=lambda: trigger_sync())
                sync_button.classes("bg-gradient-to-r from-slate-700 to-slate-800 hover:from-slate-650 hover:to-slate-750 text-white font-semibold px-6 py-2.5 rounded-xl transition-all shadow-lg shadow-slate-950/50")
            
        # Grid of cards: Status Panel + Lifetime Metrics
        with ui.row().classes("w-full gap-6"):
            # 1. Pipeline Status Panel
            with ui.card().classes("glass-card flex-1 p-6 gap-3"):
                ui.label("Pipeline Status").classes("text-sm font-semibold tracking-wider text-slate-400 uppercase")
                with ui.row().classes("items-center gap-3"):
                    status_dot = ui.html('<span class="inline-block w-3.5 h-3.5 rounded-full bg-slate-500"></span>')
                    status_indicator = ui.label("idle").classes("text-xl font-bold uppercase tracking-wider text-slate-350")
                status_counts = ui.label("").classes("text-sm text-slate-450 font-mono")
                
            # 2. Lifetime Metrics Panel
            with ui.card().classes("glass-card flex-1 p-6 gap-3"):
                ui.label("Lifetime Metrics").classes("text-sm font-semibold tracking-wider text-slate-400 uppercase")
                with ui.row().classes("w-full justify-between items-center py-1.5"):
                    with ui.column().classes("items-center gap-0.5"):
                        ui.label("Total").classes("text-xs text-slate-450")
                        total_counter = ui.label("0").classes("text-xl font-bold text-slate-200")
                    with ui.column().classes("items-center gap-0.5"):
                        ui.label("Rejected").classes("text-xs text-slate-450")
                        rejected_counter = ui.label("0").classes("text-xl font-bold text-slate-200")
                    with ui.column().classes("items-center gap-0.5"):
                        ui.label("Written").classes("text-xs text-slate-450")
                        written_counter = ui.label("0").classes("text-xl font-bold text-slate-200")
                    with ui.column().classes("items-center gap-0.5"):
                        ui.label("Applied").classes("text-xs text-slate-450")
                        applied_counter = ui.label("0").classes("text-xl font-bold text-slate-200")

        # Tabbed job views. Header, status and metrics panels stay above, always visible.
        with ui.card().classes("glass-card w-full p-6 gap-4"):
            with ui.tabs().classes("w-full") as job_tabs:
                tab_pipeline = ui.tab("Pipeline")
                tab_applied = ui.tab("Applied")
                tab_non_matches = ui.tab("Non-Matches")
                tab_rejected = ui.tab("Rejected")
            with ui.tab_panels(job_tabs, value=tab_pipeline).classes("w-full bg-transparent"):
                with ui.tab_panel(tab_pipeline).classes("p-0"):
                    pipeline_container = ui.column().classes("w-full gap-2")
                with ui.tab_panel(tab_applied).classes("p-0"):
                    applied_container = ui.column().classes("w-full gap-2")
                with ui.tab_panel(tab_non_matches).classes("p-0"):
                    non_matches_container = ui.column().classes("w-full gap-2")
                with ui.tab_panel(tab_rejected).classes("p-0"):
                    rejected_container = ui.column().classes("w-full gap-2")

    last_cards_signature = ""

    async def trigger_run():
        try:
            coordinator.start_run()
            try:
                ui.notify("Background pipeline run triggered successfully.", type="info", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not display success notification", error=str(re))
        except RunAlreadyActiveError:
            try:
                ui.notify("Cannot trigger: A background pipeline is already active.", type="warning", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not display warning notification", error=str(re))
        try:
            await refresh()
        except RuntimeError as re:
            logger.warning("Could not refresh UI after triggering run", error=str(re))

    async def trigger_sync():
        try:
            try:
                ui.notify("Starting manual ledger sync...", type="info", position="bottom-right")
            except RuntimeError as re:
                logger.warning("Could not show sync start notification", error=str(re))
            
            await coordinator.sync_ledger()
        except Exception as e:
            logger.warning("Manual ledger sync call completed with error", error=str(e))

    def render_match_card(m: MatchCard, mode: str):
        card_classes = "glass-card p-6 flex flex-col gap-4 relative overflow-hidden"
        if mode == "applied":
            card_classes += " border-emerald-500/20 opacity-90"
        elif mode == "rejected":
            card_classes += " border-rose-500/20 opacity-70"
            
        with ui.card().classes(card_classes):
            score_class = "text-emerald-350 bg-emerald-500/10 border-emerald-500/20" if m.score >= 80 else "text-indigo-350 bg-indigo-500/10 border-indigo-500/20"
            
            with ui.row().classes("w-full justify-between items-start"):
                with ui.column().classes("gap-1 flex-1"):
                    ui.link(m.title, m.url, new_tab=True).classes("text-lg font-bold text-slate-100 hover:text-indigo-400 transition-colors truncate w-full")
                    ui.label(f"{m.company} • {m.location}").classes("text-xs text-slate-400 font-medium")
                with ui.row().classes("items-center gap-2"):
                    if mode == "applied":
                        ui.label("Applied ✓").classes("text-xs font-bold px-2.5 py-0.5 bg-emerald-500/20 border border-emerald-500/40 text-emerald-350 rounded-lg uppercase tracking-wider")
                    elif mode == "rejected":
                        ui.label("Rejected").classes("text-xs font-bold px-2.5 py-0.5 bg-rose-500/20 border border-rose-500/40 text-rose-350 rounded-lg uppercase tracking-wider")
                    ui.label(str(m.score)).classes(f"text-xl font-mono font-bold px-3 py-1 rounded-xl border text-center {score_class}")
                
            # Dimensions breakdown
            with ui.column().classes("w-full gap-1.5 mt-2"):
                ui.label("ATS Dimension Ratings").classes("text-xs font-semibold text-slate-400 uppercase tracking-wider")
                for dim_name, dim_score in m.dimension_breakdown.items():
                    with ui.row().classes("w-full items-center justify-between text-xs"):
                        ui.label(dim_name).classes("text-slate-350 truncate w-1/2")
                        with ui.row().classes("items-center gap-2 flex-1 justify-end"):
                            ui.label(str(dim_score)).classes("font-mono font-semibold text-slate-200")
                            
            # Match reasons
            with ui.column().classes("w-full gap-1 mt-2"):
                ui.label("Key Evidence Reasons").classes("text-xs font-semibold text-slate-400 uppercase tracking-wider")
                for reason in m.match_reasons:
                    with ui.row().classes("items-start gap-2 text-xs text-slate-300"):
                        ui.label("•").classes("text-indigo-400 font-bold")
                        ui.label(reason).classes("leading-relaxed")

            # Cover Letter Section
            is_generating = m.identity_hash in generating_hashes
            
            if mode == "active":
                if not m.has_letter:
                    btn_label = "Generating..." if is_generating else "Write Letter"
                    with ui.row().classes("w-full justify-between items-center mt-2 border-t border-white/5 pt-2"):
                        reject_btn = ui.button("Reject", on_click=lambda m=m: transition_to_rejected(m))
                        reject_btn.classes("bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 text-xs font-semibold px-3 py-1.5 rounded-lg border border-rose-500/20")
                        
                        with ui.row().classes("gap-2"):
                            apply_btn = ui.button("Mark Applied", on_click=lambda m=m: transition_to_applied(m))
                            apply_btn.classes("bg-emerald-700 hover:bg-emerald-600 text-white text-xs font-semibold px-3 py-1.5 rounded-lg shadow-md")
                            
                            btn = ui.button(btn_label, on_click=lambda m=m: generate_letter(m))
                            btn.classes("bg-indigo-650 hover:bg-indigo-500 text-white text-xs font-semibold px-4 py-2 rounded-lg shadow-md disabled:opacity-50")
                            if is_generating:
                                btn.disable()
                else:
                    ui.separator().classes("bg-white/5 my-2")
                    with ui.row().classes("w-full justify-between items-center text-xs text-slate-400 uppercase tracking-wider"):
                        ui.label(f"Cover Letter (v{m.letter_version})").classes("font-semibold text-indigo-300")
                        ui.label(f"Created: {m.letter_created_at.strftime('%Y-%m-%d %H:%M') if m.letter_created_at else ''}").classes("font-mono")

                    with ui.row().classes("w-full justify-between items-center mt-2 border-t border-white/5 pt-2"):
                        reject_btn = ui.button("Reject", on_click=lambda m=m: transition_to_rejected(m))
                        reject_btn.classes("bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 text-xs font-semibold px-3 py-1.5 rounded-lg border border-rose-500/20")
                        
                        with ui.row().classes("gap-2"):
                            apply_btn = ui.button("Mark Applied", on_click=lambda m=m: transition_to_applied(m))
                            apply_btn.classes("bg-emerald-700 hover:bg-emerald-600 text-white text-xs font-semibold px-3 py-1.5 rounded-lg shadow-md")
                            
                            view_btn = ui.button("View", on_click=lambda m=m: show_letter_dialog(m))
                            view_btn.classes("bg-slate-700 hover:bg-slate-600 text-white text-xs font-semibold px-3 py-1.5 rounded-lg")

                            reg_btn_label = "Generating..." if is_generating else "Regenerate"
                            reg_btn = ui.button(reg_btn_label, on_click=lambda m=m: generate_letter(m))
                            reg_btn.classes("bg-indigo-650 hover:bg-indigo-500 text-white text-xs font-semibold px-3 py-1.5 rounded-lg disabled:opacity-50")
                            if is_generating:
                                reg_btn.disable()
            
            elif mode == "applied":
                if m.has_letter:
                    ui.separator().classes("bg-white/5 my-2")
                    with ui.row().classes("w-full justify-between items-center text-xs text-slate-400 uppercase tracking-wider"):
                        ui.label(f"Cover Letter (v{m.letter_version})").classes("font-semibold text-indigo-300")
                        # Proxy for the application date; there is no applied_at column.
                        # Labelled honestly -- it is the letter's date, not the application's.
                        ui.label(f"Letter written: {m.letter_created_at.strftime('%Y-%m-%d')}" if m.letter_created_at else "").classes("font-mono")

                    with ui.row().classes("w-full justify-between items-center mt-2 border-t border-white/5 pt-2"):
                        reject_btn = ui.button("Withdraw / Reject", on_click=lambda m=m: transition_to_rejected(m))
                        reject_btn.classes("bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 text-xs font-semibold px-3 py-1.5 rounded-lg border border-rose-500/20")

                        view_btn = ui.button("View", on_click=lambda m=m: show_letter_dialog(m))
                        view_btn.classes("bg-slate-700 hover:bg-slate-600 text-white text-xs font-semibold px-3 py-1.5 rounded-lg")
                else:
                    with ui.row().classes("w-full justify-between items-center mt-2 border-t border-white/5 pt-2"):
                        reject_btn = ui.button("Withdraw / Reject", on_click=lambda m=m: transition_to_rejected(m))
                        reject_btn.classes("bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 text-xs font-semibold px-3 py-1.5 rounded-lg border border-rose-500/20")
            
            elif mode == "rejected":
                with ui.row().classes("w-full justify-end mt-2 border-t border-white/5 pt-2"):
                    undo_btn = ui.button("Undo Reject", on_click=lambda m=m: transition_to_matched(m))
                    undo_btn.classes("bg-slate-700 hover:bg-slate-600 text-white text-xs font-semibold px-3 py-1.5 rounded-lg")

    def rebuild_cards(vs: ViewState):
        """Clears and rebuilds card elements in all four tab panels upon state signature updates."""
        pipeline_container.clear()
        applied_container.clear()
        non_matches_container.clear()
        rejected_container.clear()

        # --- Pipeline tab: Strong Matches, then Pending Unscored ---
        with pipeline_container:
            ui.label("Strong Matches").classes("text-lg font-bold text-transparent bg-clip-text bg-gradient-to-r from-emerald-300 to-indigo-300 border-b border-white/5 pb-2 w-full mt-2")
            if not vs.matches:
                ui.label("No matched postings found.").classes("text-slate-400 italic text-sm py-4")
            else:
                with ui.grid().classes("grid grid-cols-1 md:grid-cols-2 gap-6 w-full py-4"):
                    for m in vs.matches:
                        render_match_card(m, "active")

            ui.label("Pending Unscored Postings").classes("text-lg font-bold text-slate-400 border-b border-white/5 pb-2 w-full mt-4")
            if not vs.pending:
                ui.label("No unscored postings pending.").classes("text-slate-455 italic text-sm py-4")
            else:
                with ui.column().classes("w-full gap-3 py-4"):
                    for p in vs.pending:
                        with ui.row().classes("w-full justify-between items-center bg-white/2 hover:bg-white/4 p-3 rounded-xl border border-white/5 transition-all text-sm opacity-85"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label(p.company).classes("font-semibold text-slate-200")
                                ui.label("•").classes("text-slate-500")
                                ui.label(p.title).classes("text-slate-350")
                            ui.label("UNSCORED").classes("text-[10px] font-bold tracking-wider text-amber-300 bg-amber-500/10 border border-amber-500/20 px-2.5 py-0.5 rounded-full")

        # --- Applied tab: newest letter first ---
        with applied_container:
            if not vs.applied:
                ui.label("No applied postings found.").classes("text-slate-400 italic text-sm py-4")
            else:
                with ui.grid().classes("grid grid-cols-1 md:grid-cols-2 gap-6 w-full py-4"):
                    for m in vs.applied:
                        render_match_card(m, "applied")

        # --- Non-Matches tab ---
        with non_matches_container:
            if not vs.non_matches:
                ui.label("No non-matched postings found.").classes("text-slate-450 italic text-sm py-4")
            else:
                with ui.column().classes("w-full gap-3 py-4"):
                    for nm in vs.non_matches:
                        with ui.row().classes("w-full justify-between items-center bg-white/2 hover:bg-white/4 p-3 rounded-xl border border-white/5 transition-all text-sm opacity-60"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label(nm.company).classes("font-semibold text-slate-300")
                                ui.label("•").classes("text-slate-500")
                                ui.label(nm.title).classes("text-slate-400")
                            ui.label(f"Score: {nm.score}").classes("text-xs font-mono font-semibold text-slate-455 bg-white/5 px-2.5 py-0.5 rounded-md")

        # --- Rejected tab: plain list, the tab already does the hiding ---
        with rejected_container:
            if not vs.rejected:
                ui.label("No rejected postings.").classes("text-slate-500 italic text-sm py-4")
            else:
                with ui.grid().classes("grid grid-cols-1 md:grid-cols-2 gap-6 w-full py-4"):
                    for m in vs.rejected:
                        render_match_card(m, "rejected")

    async def refresh():
        active_run = coordinator.get_active_run()
        jobs_with_match = await persistence.list_jobs_with_match()
        counters = await persistence.counters()
        status_breakdown = await persistence.status_breakdown()
        
        vs = build_view_state(active_run, jobs_with_match, counters, status_breakdown)
        
        # Update Status Dot and text style
        status_indicator.set_text(vs.status_text.upper())
        if vs.status_text == "idle":
            status_dot.set_content('<span class="inline-block w-3.5 h-3.5 rounded-full bg-slate-500"></span>')
            status_indicator.classes(replace="text-xl font-bold uppercase tracking-wider text-slate-400")
        elif vs.status_text == "running":
            status_dot.set_content('<span class="inline-block w-3.5 h-3.5 rounded-full bg-yellow-400 animate-pulse shadow-lg shadow-yellow-500/50"></span>')
            status_indicator.classes(replace="text-xl font-bold uppercase tracking-wider text-yellow-400")
        elif vs.status_text == "done":
            status_dot.set_content('<span class="inline-block w-3.5 h-3.5 rounded-full bg-emerald-500 shadow-lg shadow-emerald-500/50"></span>')
            status_indicator.classes(replace="text-xl font-bold uppercase tracking-wider text-emerald-400")
        elif vs.status_text == "failed":
            status_dot.set_content('<span class="inline-block w-3.5 h-3.5 rounded-full bg-rose-500 shadow-lg shadow-rose-500/50"></span>')
            status_indicator.classes(replace="text-xl font-bold uppercase tracking-wider text-rose-400")
            
        if vs.n_scraped > 0 or vs.n_new > 0 or vs.status_text == "running":
            status_counts.set_text(
                f"Scraped: {vs.n_scraped} | Genuinely New: {vs.n_new} | "
                f"Matched: {vs.matched_count} | No Match: {vs.no_match_count} | Scraped/Pending: {vs.scraped_count}"
            )
        else:
            status_counts.set_text(
                f"No active run. Matched: {vs.matched_count} | No Match: {vs.no_match_count} | Scraped/Pending: {vs.scraped_count}"
            )
            
        # Update Run Button
        if vs.run_button_disabled:
            run_button.disable()
        else:
            run_button.enable()
            
        # Update Counters
        total_counter.set_text(str(vs.total_count))
        rejected_counter.set_text(str(vs.rejected_count))
        written_counter.set_text(str(vs.written_count))
        applied_counter.set_text(str(vs.applied_count))
        
        # Only rebuild card elements if status signature changed (prevents lag/flicker)
        nonlocal last_cards_signature
        current_sig = f"{vs.cards_signature}-{sorted(list(generating_hashes))}"
        if current_sig != last_cards_signature:
            last_cards_signature = current_sig
            rebuild_cards(vs)
            
    # Register periodic refresh callback
    ui.timer(1.0, refresh)
