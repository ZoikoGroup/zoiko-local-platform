from datetime import date as date_type

from pydantic import BaseModel


class AnalyticsDailyPoint(BaseModel):
    date: date_type
    calls: int
    call_minutes: float
    video_minutes: float
    messages: int


class AnalyticsOverviewResponse(BaseModel):
    range_days: int
    total_calls: int
    total_call_minutes: float
    total_video_minutes: float
    total_messages: int
    active_numbers: int
    ai_summaries: int
    daily: list[AnalyticsDailyPoint]
