from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    service: str
    status: Literal["ok"]
    version: str

