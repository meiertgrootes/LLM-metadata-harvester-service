from fastapi.testclient import TestClient

from llm_metadata_harvester_service import main

client = TestClient(main.app)


def test_liveness():
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_success(monkeypatch):
    monkeypatch.setattr(main, "_check_postgres", lambda: None)
    monkeypatch.setattr(main, "_check_redis", lambda: None)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_checks_both_dependencies(monkeypatch):
    called = []

    def fail_postgres():
        called.append("postgres")
        raise RuntimeError("down")

    def check_redis():
        called.append("redis")

    monkeypatch.setattr(main, "_check_postgres", fail_postgres)
    monkeypatch.setattr(main, "_check_redis", check_redis)
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert called == ["postgres", "redis"]
    assert response.json()["checks"] == {"postgres": "error", "redis": "ok"}
