from config import get_settings
from persistence import init_db, PersistenceService
from coordinator import build_run_coordinator
from ui.page import build_ui
from nicegui import ui
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
    
    writer = Writer()
    knowledge_loader = KnowledgeLoader(settings.knowledge_dir)
    
    build_ui(coordinator, persistence_service, writer, knowledge_loader)
    
    # Bind to settings configuration host & port
    ui.run(
        host=settings.ui_host,
        port=settings.ui_port,
        show=False,
        title="Job Match Assistant - Control Room"
    )

if __name__ in {"__main__", "__mp_main__"}:
    main()
