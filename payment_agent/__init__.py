from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from .logging_config import configure_logging  # noqa: E402

configure_logging()

from .agent import Agent  # noqa: E402

__all__ = ["Agent"]
