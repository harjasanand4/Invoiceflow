from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from app import create_app
from app.config import Settings
from app.extraction.rules import RulesExtractor
from app.storage import LocalStorage
from data.generate_invoices import generate


@pytest.fixture(scope="session")
def dataset(tmp_path_factory) -> Path:
    """A small generated dataset shared by all tests (fixed seed, so it's deterministic)."""
    out = tmp_path_factory.mktemp("dataset")
    generate(count=24, seed=7, out=out)
    return out


@pytest.fixture
def labels(dataset) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((dataset / "labels").glob("*.json"))]


def pdf_for(dataset: Path, label: dict) -> bytes:
    return (dataset / "pdfs" / label["file"]).read_bytes()


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Set TEST_DATABASE_URL to run the suite against Postgres (CI does this).
    return replace(
        Settings(),
        database_url=os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/test.db",
        storage_backend="local",
        storage_dir=str(tmp_path / "files"),
        extractor="rules",
        process_on_upload=True,
    )


@pytest.fixture(autouse=True)
def clean_database():
    """With a shared database (Postgres), drop all tables after each test."""
    yield
    if os.getenv("TEST_DATABASE_URL"):
        from app.db import Base, Session, get_engine

        Session.remove()
        engine = get_engine()
        if engine.dialect.name != "sqlite":  # eval tests use their own throwaway SQLite file
            Base.metadata.drop_all(engine)


@pytest.fixture
def app(settings):
    app = create_app(settings, extractor=RulesExtractor(), storage=LocalStorage(settings.storage_dir))
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


class FakeLLMClient:
    """Stands in for Claude/Gemini in tests. Returns a canned response and counts calls."""

    def __init__(self, response: dict | Exception):
        self.response = response
        self.calls = 0

    def extract_json(self, text, pdf_bytes):
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def label_as_llm_output(label: dict) -> dict:
    exp = label["expected"]
    return {**exp, "line_items": [dict(li) for li in exp["line_items"]]}
