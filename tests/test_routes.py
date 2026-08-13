import pytest
from fastapi.testclient import TestClient

from app import jobs

AUTH = {"Authorization": "Bearer secret"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CRAWLER_API_KEY", "secret")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "google-key")
    import app.config
    monkeypatch.setattr(app.config, "_settings", None)
    jobs.reset_registry()

    from app.main import app

    async def never_runs(job, request):
        """The pipeline is exercised in test_jobs.py; these tests are about HTTP."""
        job.status = "running"

    monkeypatch.setattr(jobs, "run_crawl", never_runs)
    with TestClient(app) as test_client:
        yield test_client


def test_health_needs_no_token(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_crawl_rejects_a_missing_token(client):
    assert client.post("/crawl", json={"neighborhood": "Bushwick"}).status_code == 401


def test_crawl_rejects_a_wrong_token(client):
    response = client.post("/crawl", json={"neighborhood": "Bushwick"},
                           headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_polling_rejects_a_missing_token(client):
    assert client.get("/crawl/anything").status_code == 401


def test_crawl_returns_202_with_a_job_id(client):
    response = client.post("/crawl", json={"neighborhood": "Bushwick", "limit": 3},
                           headers=AUTH)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["jobId"]


def test_a_second_request_for_the_same_neighborhood_returns_the_same_job(client):
    """Clicks double-fire, so a duplicate must not start a second crawl."""
    first = client.post("/crawl", json={"neighborhood": "Bushwick"}, headers=AUTH).json()
    second = client.post("/crawl", json={"neighborhood": "bushwick"}, headers=AUTH).json()
    assert first["jobId"] == second["jobId"]


def test_a_different_neighborhood_gets_its_own_job(client):
    first = client.post("/crawl", json={"neighborhood": "Bushwick"}, headers=AUTH).json()
    second = client.post("/crawl", json={"neighborhood": "Red Hook"}, headers=AUTH).json()
    assert first["jobId"] != second["jobId"]


def test_an_empty_neighborhood_is_rejected(client):
    assert client.post("/crawl", json={"neighborhood": ""}, headers=AUTH).status_code == 422


def test_polling_an_unknown_job_is_404(client):
    assert client.get("/crawl/does-not-exist", headers=AUTH).status_code == 404


def test_polling_a_known_job_returns_its_payload(client):
    job_id = client.post("/crawl", json={"neighborhood": "Bushwick"}, headers=AUTH).json()["jobId"]
    response = client.get(f"/crawl/{job_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["jobId"] == job_id