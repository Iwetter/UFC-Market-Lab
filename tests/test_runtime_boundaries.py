import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.domain import Market,Settings,now_ms
from backend.engine import Engine
from backend.licensed_runtime import configured_recorders


def test_historical_inference_never_promotes_trading_model(tmp_path):
    app=create_app(Settings(database=str(tmp_path/'mock.db')),background=False)
    with TestClient(app) as c:
        token=c.get('/api/session').json()['token'];h={'X-Research-Token':token}
        spec=c.get('/api/research/data').json()['research_model_specs']['prefight']
        assert spec['research_only'] and not spec['approved_for_trading']
        body={'features':{key:0. for key in spec['features']}}
        response=c.post('/api/research/predict/prefight',json=body,headers=h)
        assert response.status_code==200
        assert response.json()['probability_a']==.5 and not response.json()['approved_for_trading']
        assert c.post('/api/models/approve',json={'version':spec['spec_sha256']},headers=h).status_code==409
        assert not c.get('/api/state').json()['model']['trained']


async def test_licensed_discrete_events_share_journal_without_mirror_duplicates(tmp_path,monkeypatch):
    engine=Engine(Settings(source='kalshi',database=str(tmp_path/'real.db')))
    for ticker in ('KXUFCFIGHT-UNIT-A','KXUFCFIGHT-UNIT-B'):
        engine.add_market(Market(ticker=ticker,event_ticker='E',status='active'))
        engine.confirm_mapping(ticker,'sportradar:123','CARD','A','B',True,3,now_ms())
    path=tmp_path/'event-map.json';path.write_text(json.dumps({'fixture_knockdown':'knockdown'}))
    monkeypatch.setenv('UFC_DDE_ENABLED','1');monkeypatch.setenv('UFC_DDE_FIGHT_IDS','123')
    monkeypatch.setenv('UFC_DDE_EVENT_MAP_FILE',str(path));monkeypatch.setenv('UFC_DATA_LICENSE_ID','unit-test-license')
    class Fake:
        def __init__(self,store,cfg,on_observation):self.observe=on_observation;self.health={'stale':True}
    monkeypatch.setattr('backend.licensed_runtime.SportradarDDERecorder',Fake)
    feeds=configured_recorders(engine);assert len(feeds)==2
    event=SimpleNamespace(event_element_type_raw='fixture_knockdown',stream='details',seq=1,
                          received_utc_ns=now_ms()*1_000_000,received_mono_ns=100)
    await feeds[0].observe(event);await feeds[0].observe(event)
    rows=engine.store.db.execute("SELECT payload FROM events WHERE channel='latency_annotation'").fetchall()
    assert len(rows)==1
    normalized=json.loads(rows[0][0])
    assert normalized['physical_start_ms'] is None and normalized['provider_generated_ms'] is None
    assert normalized['source']=='sportradar_dde_details'
    engine.store.close()
