"""Validate the model's draft before a visitor can review it. No pricing here."""
from datetime import date
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, ValidationInfo, model_validator

from erp_demo.app import StrictBody


FIELD_LABELS = {
    'production_date': 'تاريخ الإنتاج', 'duration_hours': 'ساعات الاستوديو',
    'camera_count': 'عدد الكاميرات', 'include_audio': 'تجهيز الصوت',
    'editing_hours': 'ساعات المونتاج', 'budget_sar': 'الميزانية',
}


class DraftValue(StrictBody):
    value: StrictInt | StrictBool | str | None
    source: Literal['explicit', 'suggested', 'missing']
    reason: str = Field(min_length=3, max_length=320)
    evidence: str | None = Field(default=None, max_length=220)


class DraftFields(StrictBody):
    production_date: DraftValue
    duration_hours: DraftValue
    camera_count: DraftValue
    include_audio: DraftValue
    editing_hours: DraftValue
    budget_sar: DraftValue


class Interpretation(StrictBody):
    summary: str = Field(min_length=5, max_length=600)
    fields: DraftFields

    @model_validator(mode='after')
    def validate_draft(self, info: ValidationInfo):
        context = info.context or {}
        brief = ' '.join(context.get('brief', '').split())
        today = date.fromisoformat(context.get('today', date.today().isoformat()))
        capacity = context.get('catalog', {}).get('capacity', {})
        bounds = {'duration_hours': (1, 8), 'camera_count': (1, 3),
                  'editing_hours': (0, 8), 'budget_sar': (1, 1_000_000)}
        for name in FIELD_LABELS:
            item = getattr(self.fields, name)
            if item.source == 'missing':
                if item.value is not None or item.evidence:
                    raise ValueError('A missing field cannot have a value or evidence')
                continue
            if item.value is None:
                raise ValueError('A known field needs a value')
            if item.source == 'explicit':
                evidence = ' '.join((item.evidence or '').split())
                if not evidence or evidence not in brief:
                    raise ValueError('Explicit values need an actual excerpt from the brief')
            elif item.evidence:
                raise ValueError('A suggestion cannot claim an explicit excerpt')
            if name in ('production_date', 'budget_sar') and item.source != 'explicit':
                raise ValueError('Never invent a production date or budget')
            if name == 'production_date':
                if type(item.value) is not str or len(item.value) != 10:
                    raise ValueError('Invalid date')
                day = date.fromisoformat(item.value)
                if not 0 <= (day - today).days <= 366:
                    raise ValueError('Date outside the demo window')
            elif name == 'include_audio':
                if type(item.value) is not bool:
                    raise ValueError('Audio must be a boolean')
            else:
                minimum, maximum = bounds[name]
                if type(item.value) is not int or not minimum <= item.value <= maximum:
                    raise ValueError('Invalid resource quantity or budget')
                if item.source == 'suggested':
                    limit = capacity.get({'duration_hours': 'studio_hours', 'camera_count': 'camera_packages'}.get(name, ''), maximum)
                    if item.value > limit:
                        raise ValueError('Suggested resources exceed the catalog capacity')
        return self
