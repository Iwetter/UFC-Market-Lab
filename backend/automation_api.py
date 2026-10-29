"""Same-origin operator configuration; every execution route remains PAPER."""
import json
from pathlib import Path

from fastapi import APIRouter,Depends

from .autopaper import AutoPaperSettings,FixtureReview,normalize_name,rules_hash
from .domain import now_ms


def automation_router(engine,root:Path,operator):
    router=APIRouter(prefix='/api/automation',tags=['experimental-paper'])

    @router.get('/models')
    async def models():
        report=root/'docs/model-comparison.json'
        return {'status':engine.autopaper.snapshot()['model'],
                'comparison':json.loads(report.read_text(encoding='utf-8')) if report.exists() else None}

    @router.get('/fixtures')
    async def fixtures():
        service=engine.autopaper.service
        fighters=sorted(service.fighters(),key=lambda f:f['name']) if service else []
        candidates=[]
        for m in engine.markets.values():
            title=' '+normalize_name(m.event_title or m.title)+' '
            matches=[f for f in fighters if any(' '+normalize_name(n)+' ' in title for n in f.get('aliases',[f['name']]))]
            candidates.append({'ticker':m.ticker,'fighters':matches,'rules_sha256':rules_hash(m),
                'rules_primary':m.rules_primary,'rules_secondary':m.rules_secondary,
                'scheduled_ms':None,'schedule_status':'human_verified_earliest_start_required',
                'execution_eligible':False})
        return {'fighters':fighters,'candidates':candidates,'reviewed':engine.autopaper.fixtures}

    @router.post('/fixtures',dependencies=[Depends(operator)])
    async def fixture(review:FixtureReview):
        return engine.autopaper.bind(review,now_ms())

    @router.post('/settings',dependencies=[Depends(operator)])
    async def settings(request:AutoPaperSettings):
        return engine.autopaper.configure(request,now_ms())

    return router
