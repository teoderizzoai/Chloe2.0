"""Root conftest: load .env so live tests pick up GEMINI_API_KEY etc."""
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env", override=False)
