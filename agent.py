"""Entry point required by the assignment: `from agent import Agent`."""

from pathlib import Path

from dotenv import load_dotenv

# entry-point config loading; real environment variables win over .env values
load_dotenv(Path(__file__).parent / ".env")

from payment_agent import Agent  # noqa: E402

__all__ = ["Agent"]
