"""Local operator controls for an isolated historical PAPER practice account."""
import asyncio
from typing import Literal

from fastapi import APIRouter,Depends,Query
from pydantic import Field

from .domain import StrictModel


class StartPractice(StrictModel):
    fight_id: str = Field(min_length=1,max_length=200)
    speed: Literal[1,5,15,30,60] = 30


class PracticeControl(StrictModel):
    action: Literal['pause','resume','step','speed']
    speed: Literal[1,5,15,30,60] | None = None


def practice_router(controller,operator):
    router=APIRouter(prefix='/api/practice',tags=['historical-practice'])
    loading=asyncio.Lock()

    @router.get('/catalog')
    async def catalog(q:str=Query(default='',max_length=100)):
        # Parsing/hash verification is off the feed event loop. This creates no
        # SQLite connection: practice accounts are opened only by start below.
        async with loading:
            return await asyncio.to_thread(controller.catalog,q)

    @router.get('/state')
    async def state():
        result=controller.snapshot()
        error=getattr(controller,'runtime_error',None)
        return result|{'status':'error','error':f'Practice worker stopped ({error}); restart the server.'} if error else result

    @router.post('/start',dependencies=[Depends(operator)])
    async def start(request:StartPractice):
        async with loading:
            if getattr(controller,'runtime_error',None):
                raise ValueError('practice worker stopped; restart the server before starting another session')
            return controller.start(request.fight_id,speed=request.speed)

    @router.post('/control',dependencies=[Depends(operator)])
    async def control(request:PracticeControl):
        return controller.control(request.action,speed=request.speed)

    return router
