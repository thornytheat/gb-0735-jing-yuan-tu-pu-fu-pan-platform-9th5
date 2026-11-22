from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.dependencies import SplitSvc

router = APIRouter(prefix="/api/runs/{run_id}/splits")


class PublishRequest(BaseModel):
    strategy: Literal["A_FIRST", "B_FIRST"]
    simulate_failure: bool = False


@router.get("/comparison")
def comparison(run_id: str, service: SplitSvc):
    return service.comparison(run_id)


@router.post("/publish", status_code=201)
def publish(run_id: str, request: PublishRequest, service: SplitSvc):
    # simulate_failure 由服务层转成 PUBLISH_INTERRUPTED，事务回滚后旧版仍是 current
    return service.publish(run_id, request.strategy, request.simulate_failure)


@router.get("/current")
def current(run_id: str, service: SplitSvc):
    return service.current(run_id)


@router.get("/history")
def history(run_id: str, service: SplitSvc):
    return service.history(run_id)


@router.get("/versions/{version}")
def version(run_id: str, version: int, service: SplitSvc):
    return service.version(run_id, version)
