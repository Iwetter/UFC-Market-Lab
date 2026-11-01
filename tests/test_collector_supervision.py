"""Offline worker faults; no API credentials, orders or network calls."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
from threading import Event

from fastapi.testclient import TestClient
from backend.app import create_app

import pytest

from backend.book import Book
from backend.domain import Market,OrderIntent,Settings,now_ms
from backend.engine import Engine
from backend.operations import collector_health
from backend.fees import FeeSchedule


def workers(monkeypatch,mode):
    state={'rest_closed':False,'stream_canceled':False,'closed_event':Event()}
    class FakeRecorder:
        def __init__(self,engine):
            self.engine=engine
            async def close():
                state['rest_closed']=True
                state['closed_event'].set()
            self.rest=SimpleNamespace(close=close)
        async def polling(self):
            self.engine.health['last_rest_ms']=now_ms()
            await asyncio.sleep(0)
            if mode=='raises':raise OSError('private provider details must not be logged')
            if mode=='returns':return
            if mode=='cancels':raise asyncio.CancelledError()
            await asyncio.Future()
        async def streaming(self):
            if mode=='optional_disabled':return
            try:
                if mode=='ws_raises':raise ValueError('private provider details must not be logged')
                await asyncio.Future()
            finally:state['stream_canceled']=True
    async def disabled_clock(engine):return
    monkeypatch.setattr('backend.kalshi.KalshiRecorder',FakeRecorder)
    monkeypatch.setattr('backend.licensed_runtime.configured_recorders',lambda engine:[])
    monkeypatch.setattr('backend.clock_monitor.monitor_clock',disabled_clock)
    return state


@pytest.mark.parametrize('mode',['raises','returns','cancels','ws_raises'])
async def test_dead_required_collector_stops_engine_and_fails_closed(tmp_path,monkeypatch,mode):
    state=workers(monkeypatch,mode)
    engine=Engine(Settings(source='kalshi',database=str(tmp_path/'collector.db'),order_latency_ms=10000))
    book=Book();book.snapshot([['.4','10']],[['.5','10']],now_ms())
    engine.books.books['fixture']=book;engine.exchange_active=True
    ts=now_ms()
    market=Market(ticker='fixture',event_ticker='fixture',status='active',price_ranges=[{'start':'0','end':'1','step':'.01'}])
    intent=OrderIntent(id=uuid4(),ticker='fixture',outcome='yes',count='1',limit_price='.55')
    context={'book':book,'market':market,'mapping':{'confirmed':True,'bout_id':'fixture','card_id':'fixture'},
        'fight':SimpleNamespace(status='scheduled',stale=False,received_ms=ts),'model_ready':True,
        'clock_offset_ms':0,'fees':FeeSchedule(verified_ms=ts)}
    assert engine.broker.submit(intent,ts,context)['status']=='pending'
    try:
        with pytest.raises(RuntimeError,match='kalshi_ws' if mode=='ws_raises' else 'kalshi_rest') as caught:
            await asyncio.wait_for(engine.run(),timeout=1)
        assert 'private provider details' not in str(caught.value)
        assert engine.health['engine_fault']
        assert engine.broker.killed and book.stale and not engine.exchange_active
        assert not collector_health(engine)['ok']
        assert collector_health(engine)['collector_fault']['worker']==('kalshi_ws' if mode=='ws_raises' else 'kalshi_rest')
        assert state['rest_closed'] and state['stream_canceled']
        assert all(t.done() for t in engine.tasks)
        assert engine.store.get('killed') is True
        assert engine.broker.orders()[0]['status']=='canceled'
        assert not engine.store.bodies('fills') and engine.broker.cash==engine.cfg.initial_cash
    finally:engine.store.close()


async def test_disabled_optional_workers_and_parent_cancellation_are_not_failures(tmp_path,monkeypatch):
    state=workers(monkeypatch,'optional_disabled')
    engine=Engine(Settings(source='kalshi',database=str(tmp_path/'healthy.db')))
    task=asyncio.create_task(engine.run())
    try:
        await asyncio.sleep(.1)
        assert not task.done() and not engine.health.get('engine_fault')
        assert not engine.broker.killed and collector_health(engine)['ok']
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert state['rest_closed'] and all(t.done() for t in engine.tasks)
        assert not engine.health.get('engine_fault')
        engine.store.close()


async def test_fault_still_closes_workers_if_kill_persistence_fails(tmp_path,monkeypatch):
    state=workers(monkeypatch,'raises')
    engine=Engine(Settings(source='kalshi',database=str(tmp_path/'disk-fault.db')))
    def broken_kill(ts):raise OSError('private storage path')
    monkeypatch.setattr(engine.broker,'kill',broken_kill)
    try:
        with pytest.raises(RuntimeError,match='kalshi_rest'):
            await asyncio.wait_for(engine.run(),timeout=1)
        assert engine.broker.killed and not engine.exchange_active
        assert engine.health['kill_persistence_error']=='OSError'
        assert all(t.done() for t in engine.tasks) and state['rest_closed']
    finally:engine.store.close()


def test_api_reports_unhealthy_and_notifies_process_supervisor(tmp_path,monkeypatch):
    state=workers(monkeypatch,'raises')
    app=create_app(Settings(source='kalshi',database=str(tmp_path/'service.db')),background=True)
    supervisor=Event()
    app.state.on_runtime_failure=supervisor.set
    with TestClient(app) as client:
        assert state['closed_event'].wait(2)
        assert supervisor.wait(2)
        response=client.get('/api/health')
        assert response.status_code==503 and response.json()['ok'] is False
        assert app.state.engine.broker.killed
