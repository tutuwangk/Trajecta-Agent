from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Strict base class for all data crossing a planning boundary."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
