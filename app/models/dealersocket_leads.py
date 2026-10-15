from pydantic import BaseModel, ConfigDict, Field


class LeadReadOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maxRecords: int = Field(default=200, ge=1, le=1000)
    detailLimit: int = Field(default=50, ge=0, le=200)
