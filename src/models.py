from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Article(BaseModel):
    title: str
    url: str
    source: str
    published_at: Optional[datetime] = None
    summary_raw: str = ""
    language: str = "en"
    region: str = "global"
    topic: str = "general"


class CuratedItem(BaseModel):
    title_ko: str
    url: str
    source: str
    summary_ko: str
    insight: str = ""
    score: int = 0
    product_tag: Optional[str] = None
    why_hot: Optional[str] = None
    relevance: Optional[str] = None


class Briefing(BaseModel):
    headline: str
    date_label: str
    editor_note: str = ""
    kr_security: list[CuratedItem] = Field(default_factory=list)
    global_security: list[CuratedItem] = Field(default_factory=list)
    kr_market: list[CuratedItem] = Field(default_factory=list)
    global_market: list[CuratedItem] = Field(default_factory=list)
    focus_product: list[CuratedItem] = Field(default_factory=list)
