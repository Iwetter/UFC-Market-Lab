"""Browser-facing practice workflow with an explicitly synthetic model/source."""
from datetime import datetime,timezone
from uuid import UUID

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.domain import Settings


class OfflineFixtureService:
    def __init__(self,root):
        self.rows=[{'fight_id':f'offline-{i}','event_date':day,'event_name':'Offline test card',
            'f1_id':'a','f2_id':'b','f1_name':'Test Alpha','f2_name':'Test Beta',
            'result_f1':'W','result_f2':'L','fight_duration_sec':'900','scheduled_rounds':'3',
            'method':'Decision','end_round':'3','end_time':'5:00'}
            for i,day in enumerate(('2020-01-01','2020-02-01','2024-02-01'))]
    def status(self):return {'available':True,'trained_through_ms':1704067200000}
    def predict(self,a,b,scheduled_ms,as_of_ms):
        return {'probability_a':.6,'model_version':'offline-fixture-only','features_asof_ms':as_of_ms,
            'trained_through_ms':1704067200000,'eligible_for_live':False}


def test_historical_practice_http_flow_is_automatic_isolated_and_authenticated(tmp_path,monkeypatch):
    monkeypatch.setattr('backend.practice.PredictionService',OfflineFixtureService)
    app=create_app(Settings(source='kalshi',database=str(tmp_path/'main.sqlite3')),background=False)
    with TestClient(app) as c:
        catalog=c.get('/api/practice/catalog').json()
        assert catalog['available'] and catalog['default_fight_id']=='offline-2'
        assert c.get('/api/practice/catalog?q=Alpha').json()['matched_count']==1
        assert c.get('/api/practice/catalog?q=2023').json()['fights']==[]
        body={'fight_id':'offline-2','speed':5}
        assert c.post('/api/practice/start',json=body).status_code==403
        token=c.get('/api/session').json()['token'];headers={'X-Research-Token':token}
        assert c.post('/api/practice/start',json=body,headers=headers|{'Origin':'https://attacker.example'}).status_code==403
        result=c.post('/api/practice/start',json=body,headers=headers)
        assert result.status_code==200
        state=result.json();UUID(state['run_id'])
        assert state['status']=='running' and state['simulated_account']
        assert 'result' not in state['fight']
        assert 'SYNTHETIC' in state['sources']['market']
        state=c.post('/api/practice/control',json={'action':'pause'},headers=headers).json()
        before=state['elapsed_s']
        assert state['status']=='paused'
        state=c.post('/api/practice/control',json={'action':'step'},headers=headers).json()
        assert state['elapsed_s']>before
        for _ in range(150):
            state=c.post('/api/practice/control',json={'action':'step'},headers=headers).json()
            if state['status']=='finished':break
        assert state['status']=='finished'
        assert state['fight']['result']['winner_name']=='Test Alpha'
        assert state['orders'] and state['fills']
        assert any(o['intent']['action']=='sell' for o in state['orders'])
        real_account=c.get('/api/state').json()
        assert real_account['orders']==[] and real_account['portfolio']['positions']==[]
        assert real_account['live_enabled'] is False


def test_practice_unknown_fight_and_speed_are_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr('backend.practice.PredictionService',OfflineFixtureService)
    app=create_app(Settings(database=str(tmp_path/'main.sqlite3')),background=False)
    with TestClient(app) as c:
        token=c.get('/api/session').json()['token'];headers={'X-Research-Token':token}
        assert c.post('/api/practice/start',json={'fight_id':'../../secrets'},headers=headers).status_code==409
        assert c.post('/api/practice/start',json={'fight_id':'offline-2','speed':1000},headers=headers).status_code==422
        assert c.get('/api/practice/state').json()['status']=='idle'


def test_practice_worker_failure_is_visible_without_killing_main_account(tmp_path,monkeypatch):
    import asyncio
    async def broken(self):
        await asyncio.sleep(0)
        raise RuntimeError('offline test fault')
    monkeypatch.setattr('backend.practice.PracticeController.run',broken)
    app=create_app(Settings(database=str(tmp_path/'main.sqlite3')),background=True)
    with TestClient(app) as c:
        state=c.get('/api/practice/state').json()
        assert state['status']=='error' and 'restart' in state['error']
        assert c.get('/api/health').status_code==200
        assert not c.get('/api/state').json()['portfolio']['kill_switch']
        token=c.get('/api/session').json()['token']
        assert c.post('/api/practice/start',json={'fight_id':'any'},headers={'X-Research-Token':token}).status_code==409
