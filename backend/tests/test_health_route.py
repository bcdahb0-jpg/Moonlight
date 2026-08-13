"""进程探针回归测试。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.open_llm_vtuber.health_route import init_health_route
from src.open_llm_vtuber.service_context import ServiceContext


def _app(context: ServiceContext) -> FastAPI:
    app = FastAPI()
    app.include_router(init_health_route(context))
    return app


def test_healthz_does_not_depend_on_model_initialization():
    with TestClient(_app(ServiceContext())) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "moonlight-backend"}


def test_readyz_reports_starting_until_context_is_loaded():
    with TestClient(_app(ServiceContext())) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "starting"
    assert response.json()["initialized"] is False


def test_readyz_reports_ready_after_context_fields_are_present():
    context = ServiceContext()
    context.config = object()
    context.system_config = object()
    context.character_config = object()

    with TestClient(_app(context)) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["initialized"] is True
