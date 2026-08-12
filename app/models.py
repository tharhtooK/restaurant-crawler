from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from .normalize import DAY_KEYS

WireModel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CrawlRequest(BaseModel):
    model_config = WireModel

    neighborhood: str = Field(min_length=1)
    city: str = "New York"
    limit: int = Field(default=10, ge=1, le=20)
    max_reviews_per_restaurant: int = Field(default=10, ge=1, le=50)
    sources: list[str] | None = None


class HoursWindow(BaseModel):
    model_config = WireModel

    open: str = Field(pattern=r"^\d{2}:\d{2}$")
    close: str = Field(pattern=r"^\d{2}:\d{2}$")


class Review(BaseModel):
    model_config = WireModel

    content: str = Field(min_length=1)
    source: str
    source_url: str | None = None
    published_at: str | None = None


class Restaurant(BaseModel):
    model_config = WireModel

    slug: str
    name: str
    neighborhood: str
    cuisine: str
    price_tier: int = Field(ge=1, le=4)
    address: str
    dietary: list[str]
    hours: dict[str, HoursWindow | None]
    reviews: list[Review]
    raw: dict

    @field_validator("hours")
    @classmethod
    def exactly_seven_days(cls, value: dict) -> dict:
        if set(value) != set(DAY_KEYS):
            raise ValueError(f"hours needs exactly the seven keys {DAY_KEYS}, got {sorted(value)}")
        return value


class JobStatus(BaseModel):
    model_config = WireModel

    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]