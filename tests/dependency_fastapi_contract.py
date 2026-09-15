from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, FastAPI, Header, HTTPException, WebSocket
from fastapi.testclient import TestClient
from pydantic import BaseModel

root = Path(__file__).resolve().parents[1]
hub_requirements = (root / "hub/requirements.txt").read_text().splitlines()
agent_requirements = (root / "agent/requirements.txt").read_text().splitlines()

assert "fastapi==0.141.1" in hub_requirements
assert "fastapi==0.115.6" not in hub_requirements
assert not any(line.startswith("fastapi") for line in agent_requirements)
assert version("fastapi") == "0.141.1"

class ContractBody(BaseModel):
    value: int

def operator_context(
    x_dark_noc: str = Header(...),
    dark_noc_session: str | None = Cookie(default=None),
):
    if x_dark_noc != "contract-token":
        raise HTTPException(401, "invalid contract token")
    return {"header": x_dark_noc, "session": dark_noc_session}

app = FastAPI()
router = APIRouter()

@router.post("/api/contract/{item_id}")
def contract_http(
    item_id: int,
    body: ContractBody,
    context: dict = Depends(operator_context),
):
    return {
        "item_id": item_id,
        "value": body.value,
        "session": context["session"],
    }

@router.websocket("/ws/contract")
async def contract_websocket(websocket: WebSocket):
    await websocket.accept()
    payload = await websocket.receive_json()
    await websocket.send_json({"echo": payload, "query": websocket.query_params.get("source")})
    await websocket.close(code=1000)

app.include_router(router)

with TestClient(app) as client:
    unauthorized = client.post("/api/contract/7", json={"value": 9})
    assert unauthorized.status_code == 422

    denied = client.post(
        "/api/contract/7",
        headers={"X-Dark-Noc": "wrong"},
        json={"value": 9},
    )
    assert denied.status_code == 401 and denied.json()["detail"] == "invalid contract token"

    client.cookies.set("dark_noc_session", "session-contract")
    response = client.post(
        "/api/contract/7",
        headers={"X-Dark-Noc": "contract-token"},
        json={"value": 9},
    )
    assert response.status_code == 200
    assert response.json() == {"item_id": 7, "value": 9, "session": "session-contract"}

    with client.websocket_connect("/ws/contract?source=dark-noc") as websocket:
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {
            "echo": {"type": "ping"},
            "query": "dark-noc",
        }

print("fastapi dependency contract passed")
