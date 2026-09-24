import os

import pytest
from fastapi.testclient import TestClient
from hypothesis import settings as hypothesis_settings

from sixhops.app.config import Settings
from sixhops.app.main import create_app

PASSWORD = "test-password"

# HYPOTHESIS_PROFILE=thorough for a long run before merging risky core changes.
hypothesis_settings.register_profile("default", max_examples=40)
hypothesis_settings.register_profile("thorough", max_examples=2000)
hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        app_password=PASSWORD,
        secret_key="test-secret",
        api_token="test-token",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        me_name="Abhiram",
        llm_provider="fake",
        _env_file=None,
    )


@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def authed(client) -> TestClient:
    client.post("/login", data={"password": PASSWORD})
    return client
