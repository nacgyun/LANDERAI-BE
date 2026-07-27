from fastapi.testclient import TestClient

from app.main import app
from app.routers import auth


def test_auth_me_disabled_env_returns_404_before_auth(monkeypatch):
    monkeypatch.setattr(auth.settings, "APP_ENV", "production")

    def fail_if_auth_runs():
        raise AssertionError("get_current_user should not run when auth/me is disabled")

    app.dependency_overrides[auth.get_current_user] = fail_if_auth_runs
    try:
        response = TestClient(app).get("/api/v1/auth/me")
    finally:
        app.dependency_overrides.pop(auth.get_current_user, None)

    assert response.status_code == 404
