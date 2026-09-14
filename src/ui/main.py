import asyncio

from config import get_settings
from persistence import init_db, PersistenceService
from coordinator import build_run_coordinator
from ui.page import build_ui
from nicegui import app, ui, Client
from observability.log_config import setup_logging

def main():
    setup_logging()
    settings = get_settings()
    
    # Auto-initialize database & migrations
    init_db(settings.db_path)
    
    persistence_service = PersistenceService(settings.db_path)
    coordinator = build_run_coordinator(persistence_service=persistence_service)
    
    from skills.writer import Writer
    from knowledge import KnowledgeLoader
    
    writer = Writer(candidate_name=settings.candidate_name)
    knowledge_loader = KnowledgeLoader(settings.knowledge_dir)
    
    build_ui(coordinator, persistence_service, writer, knowledge_loader)

    # --- Lifecycle: closing the last browser tab shuts the app down ---
    # The tab is the app window. A refresh disconnects and reconnects, so the
    # shutdown is deferred by a grace period that a reconnect cancels.
    exit_task = {"handle": None}

    def _live_client_count() -> int:
        # handle_disconnect clears tab_id before invoking handlers, so a
        # disconnecting client already counts as zero here.
        return sum(1 for c in Client.instances.values() if c.has_socket_connection)

    def _cancel_pending_exit() -> None:
        handle = exit_task["handle"]
        if handle is not None and not handle.done():
            handle.cancel()
        exit_task["handle"] = None

    async def _exit_after_grace() -> None:
        try:
            await asyncio.sleep(settings.ui_exit_grace_seconds)
        except asyncio.CancelledError:
            return
        if _live_client_count() > 0:
            return
        app.shutdown()

    def _on_connect() -> None:
        _cancel_pending_exit()

    def _on_disconnect() -> None:
        _cancel_pending_exit()
        if _live_client_count() > 0:
            return
        exit_task["handle"] = asyncio.create_task(_exit_after_grace())

    async def _on_shutdown() -> None:
        await coordinator.shutdown()

    app.on_connect(_on_connect)
    app.on_disconnect(_on_disconnect)
    app.on_shutdown(_on_shutdown)

    # Bind to settings configuration host & port
    ui.run(
        host=settings.ui_host,
        port=settings.ui_port,
        show=True,
        reload=False,
        title="Job Match Assistant - Control Room"
    )

if __name__ in {"__main__", "__mp_main__"}:
    main()
