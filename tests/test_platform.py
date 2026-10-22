import json
from decimal import Decimal as D
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.domain import Settings, OrderIntent
from backend.engine import Engine
from backend.replay import run_replay


def test_api_security_and_paper_flow(tmp_path):
    app=create_app(Settings(database=str(tmp_path/"paper.db")),background=False)
    with TestClient(app) as client:
        state=client.get("/api/state").json()
        assert state["mode"] == "PAPER" and not state["live_enabled"]
        assert state["source"] == "mock" and not state["model"]["trained"]
        ticker=state["markets"][0]["ticker"]
        body={"id":str(uuid4()),"ticker":ticker,"outcome":"yes","count":"2","limit_price":".60"}
        assert client.post("/api/paper/orders",json=body).status_code == 403
        token=client.get("/api/session").json()["token"]
        with client.websocket_connect("/ws",headers={"Origin":"http://testserver"}) as socket:
            streamed=socket.receive_json()
            assert streamed["source"] == "mock" and streamed["mode"] == "PAPER"
        headers={"X-Research-Token":token}
        response=client.post("/api/paper/orders",json=body,headers=headers)
        assert response.status_code == 200 and response.json()["status"] == "pending"
        app.state.engine.tick(response.json()["arrival_ms"])
        state=client.get("/api/state").json()
        assert state["fills"] and D(state["portfolio"]["cash"]) < 1000
        assert client.get("/api/audit").json()["chain_valid"]
        assert client.post("/api/kill",headers=headers).json()["kill_switch"]
        assert client.post("/api/mock/pause",headers=headers|{"Origin":"https://hostile.example"}).status_code == 403
        assert client.get("/api/state",headers={"Host":"hostile.example"}).status_code == 400
        assert client.post("/api/live/orders",json=body,headers=headers).status_code == 404


def test_restart_does_not_claim_freshness_and_source_isolation(tmp_path):
    path=str(tmp_path/"db")
    engine=Engine(Settings(database=path))
    engine.persist_fights(); engine.store.close()
    other=Engine(Settings(database=path))
    assert all(f.stale for f in other.fights.values())
    other.store.close()
    with pytest.raises(ValueError): Engine(Settings(database=path,source="kalshi"))


def test_replay_identical_result_hash(tmp_path):
    cfg=Settings(database=str(tmp_path/"input.db"))
    engine=Engine(cfg)
    ts=engine.books.books["MOCK-UFC-001"].updated_ms
    i=OrderIntent(id=uuid4(),ticker="MOCK-UFC-001",outcome="yes",count="2",limit_price=".60")
    engine.submit(i,ts); engine.tick(ts+250)
    engine.store.close()
    a=run_replay(cfg.database,tmp_path/"a.db",cfg)
    b=run_replay(cfg.database,tmp_path/"b.db",cfg)
    assert a["output_sha256"] == b["output_sha256"]
    assert a["result"]["fills"]


def test_settlement_uses_finalized_exchange_value(tmp_path):
    engine=Engine(Settings(database=str(tmp_path/"s.db")))
    market=engine.markets["MOCK-UFC-001"].model_dump(mode="json")
    market.update(status="determined",settlement_value_dollars=".5")
    engine.apply_market(market,1000)
    assert "MOCK-UFC-001" not in engine.broker.settled
    market["status"]="finalized";engine.apply_market(market,1001)
    assert "MOCK-UFC-001" in engine.broker.settled
    assert not engine.snapshot(1001)['markets'][0]['edges']
