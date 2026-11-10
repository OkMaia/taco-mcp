"""Tests for the remote Streamable HTTP entrypoint."""

import asyncio
import os

import pytest
from starlette.testclient import TestClient

os.environ["MCP_AUTH_TOKEN"] = "test-token"
os.environ["MCP_ALLOWED_HOSTS"] = "testserver"
os.environ["TACO_MCP_READ_ONLY"] = "1"

from taco_mcp.http_server import app
from taco_mcp.server import list_tools


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def mcp_headers(token: str = "test-token") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Host": "testserver",
    }


def test_health_is_public(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "taco-mcp"}


def test_mcp_requires_bearer_token(client: TestClient):
    response = client.post(
        "/mcp",
        headers=mcp_headers(token="wrong-token"),
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
    )

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized"}


def test_mcp_accepts_bearer_token(client: TestClient):
    response = client.post(
        "/mcp",
        headers=mcp_headers(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "taco-mcp"


def test_http_mode_only_lists_read_only_tools():
    tools = asyncio.run(list_tools())

    assert {tool.name for tool in tools} == {
        "search_food",
        "get_food",
        "calculate_macros",
        "calculate_meal_macros",
        "fit_meals",
    }


def test_mcp_call_fit_meals_via_http(client: TestClient):
    # Call tools/call for fit_meals
    response = client.post(
        "/mcp",
        headers=mcp_headers(),
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "fit_meals",
                "arguments": {
                    "meals": [
                        {
                            "id": "teste_http",
                            "target": {
                                "kcal": 234.5,
                                "protein_g": 33.5,
                                "carb_g": 14.7,
                                "fat_g": 4.6,
                            },
                            "items": [
                                {
                                    "food": 486,
                                    "role": "protein",
                                    "min_g": 30,
                                    "max_g": 300,
                                    "step_g": 10,
                                },
                                {
                                    "food": 239,
                                    "role": "fruit",
                                    "min_g": 30,
                                    "max_g": 200,
                                    "step_g": 10,
                                },
                                {
                                    "food": 78,
                                    "role": "free",
                                },
                            ],
                        }
                    ]
                },
            },
        },
    )

    assert response.status_code == 200
    res_data = response.json()
    assert "result" in res_data
    assert "content" in res_data["result"]
    import json
    content_text = json.loads(res_data["result"]["content"][0]["text"])
    assert "results" in content_text
    assert len(content_text["results"]) == 1
    assert content_text["results"][0]["id"] == "teste_http"
    assert content_text["results"][0]["feasible"] is True

