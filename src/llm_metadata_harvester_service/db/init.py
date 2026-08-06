from llm_metadata_harvester_service.db import models  # noqa: F401  (register tables)
from llm_metadata_harvester_service.db.session import Base, engine


def init_db() -> None:
    """Create tables if they do not yet exist. Idempotent."""
    Base.metadata.create_all(bind=engine)
