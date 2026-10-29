import asyncio
import json
import sqlite3
import sys

import pytest

from backend.domain import Settings
from backend.engine import Engine
from backend.operations import backup_database,collector_health,monitor_operations,writer_lease


def test_backup_includes_wal_and_isolates_destination(tmp_path):
    engine=Engine(Settings(database=str(tmp_path/'source.db')))
    count=engine.store.db.execute('SELECT count(*) FROM events').fetchone()[0]
    dest=tmp_path/'backup.db';backup_database(engine.cfg.database,dest)
    db=sqlite3.connect(dest)
    assert db.execute('SELECT count(*) FROM events').fetchone()[0]==count
    db.close()
    with pytest.raises(ValueError):backup_database(engine.cfg.database,dest)
    with pytest.raises(ValueError):backup_database(engine.cfg.database,engine.cfg.database)
    engine.store.close()


def test_writer_lease_releases_on_exit(tmp_path):
    path=tmp_path/'db'
    with writer_lease(path):
        size=(tmp_path/'db.writer.lock').stat().st_size
        with pytest.raises(RuntimeError):
            with writer_lease(path):pass
        assert (tmp_path/'db.writer.lock').stat().st_size==size==1
    for _ in range(5):
        with writer_lease(path):pass
    assert (tmp_path/'db.writer.lock').stat().st_size==1


def test_backup_final_name_is_absent_until_verified(tmp_path,monkeypatch):
    engine=Engine(Settings(database=str(tmp_path/'source.db')))
    dest=tmp_path/'final.sqlite3'
    original=sqlite3.connect
    class InspectSource(sqlite3.Connection):
        def backup(self,target,**kwargs):
            assert not dest.exists()
            assert len(list(tmp_path.glob('.*.partial')))==1
            return super().backup(target,**kwargs)
    def connect(path,*args,**kwargs):
        if kwargs.get('uri'):kwargs['factory']=InspectSource
        return original(path,*args,**kwargs)
    monkeypatch.setattr('backend.operations.sqlite3.connect',connect)
    backup_database(engine.cfg.database,dest)
    assert dest.exists() and not list(tmp_path.glob('.*.partial'))
    engine.store.close()


def test_failed_backup_keeps_partial_without_publishing_final_name(tmp_path,monkeypatch):
    engine=Engine(Settings(database=str(tmp_path/'source.db')))
    dest=tmp_path/'unverified.sqlite3'
    original=sqlite3.connect
    class FailedVerification(sqlite3.Connection):
        def execute(self,statement,*args):
            if statement=='PRAGMA quick_check':
                return super().execute("SELECT 'forced integrity check failure'")
            return super().execute(statement,*args)
    def connect(path,*args,**kwargs):
        if str(path).endswith('.partial'):kwargs['factory']=FailedVerification
        return original(path,*args,**kwargs)
    monkeypatch.setattr('backend.operations.sqlite3.connect',connect)
    with pytest.raises(RuntimeError,match='integrity check failed'):
        backup_database(engine.cfg.database,dest)
    assert not dest.exists() and len(list(tmp_path.glob('.*.partial')))==1
    engine.store.close()


def test_local_launcher_rejects_existing_writer_before_engine_creation(tmp_path,monkeypatch):
    import start
    database=tmp_path/'shared.sqlite3'
    created=[]
    monkeypatch.setattr(sys,'argv',['start.py','--no-install','--no-browser','--database',str(database)])
    monkeypatch.setattr('backend.app.create_app',lambda *args,**kwargs:created.append(True))
    with writer_lease(database):
        with pytest.raises(RuntimeError,match='another recorder'):
            start.main()
    assert not created


def test_local_launcher_holds_writer_lease_through_uvicorn_lifetime(tmp_path,monkeypatch):
    import start
    database=tmp_path/'shared.sqlite3'
    ran=[]
    monkeypatch.setattr(sys,'argv',['start.py','--no-install','--no-browser','--database',str(database)])
    monkeypatch.setattr('backend.app.create_app',lambda *args,**kwargs:object())
    def run(*args,**kwargs):
        with pytest.raises(RuntimeError,match='another recorder'):
            with writer_lease(database):pass
        ran.append(True)
    monkeypatch.setattr('uvicorn.run',run)
    start.main()
    assert ran
    with writer_lease(database):pass


async def test_monitor_health_and_cancellation(tmp_path):
    engine=Engine(Settings(database=str(tmp_path/'s.db')))
    task=asyncio.create_task(monitor_operations(engine,backup_interval_s=0,min_free_mb=1))
    await asyncio.sleep(.01);task.cancel();await asyncio.gather(task,return_exceptions=True)
    report=json.loads((tmp_path/'collector-health.json').read_text())
    assert report['source']=='mock' and report['mode']=='PAPER' and not report['live_enabled']
    engine.cfg=engine.cfg.model_copy(update={'source':'kalshi'})
    assert not collector_health(engine,100000)['ok']
    engine.health['last_rest_ms']=100000
    assert collector_health(engine,100001)['ok']
    engine.store.close()
