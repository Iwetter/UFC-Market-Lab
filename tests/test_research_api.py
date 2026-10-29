from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.domain import Settings


def test_research_findings_and_no_mock_latency_promotion(tmp_path):
    app=create_app(Settings(database=str(tmp_path/'paper.db')),background=False)
    with TestClient(app) as c:
        assert c.get('/api/research/data').json()['execution_approved'] is False
        assert c.get('/api/research/positions').json()['status']=='insufficient_data'
        study=c.get('/api/research/latency').json()
        assert study['hypothesis']['status']=='insufficient_physical_timing'
        state=c.get('/api/state').json();m=state['markets'][0]
        token=c.get('/api/session').json()['token'];h={'X-Research-Token':token}
        event={'id':str(uuid4()),'bout_id':m['mapping']['bout_id'],'card_id':m['mapping']['card_id'],'ticker':m['ticker'],
               'event_kind':'knockdown','source':'manual_broadcast','broadcast_observed_ms':state['server_ms'],'is_mock':False}
        assert c.post('/api/research/latency-annotations',json=event).status_code==403
        r=c.post('/api/research/latency-annotations',json=event,headers=h)
        assert r.status_code==200 and r.json()['is_mock'] is True
        assert c.post('/api/research/latency-annotations',json=event,headers=h).json()==r.json()
        event['event_kind']='finish'
        assert c.post('/api/research/latency-annotations',json=event,headers=h).status_code==409
        assert c.get('/api/research/latency').json()['summaries']['physical_delay_ms']['n']==0
