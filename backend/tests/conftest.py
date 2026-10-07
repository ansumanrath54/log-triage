import os

# Must be set before the app is imported so tests use a throwaway in-memory DB.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for name in (
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "LLM_PROVIDER",
):
    os.environ.pop(name, None)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    c = TestClient(app)
    c.delete("/api/reset")
    yield c
    c.delete("/api/reset")
