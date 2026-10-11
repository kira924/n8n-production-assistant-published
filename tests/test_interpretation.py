import copy
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.app import Settings, create_app
from api.interpretation import Interpretation
from tests.test_gateway import quote_and_report, session


DAY = (date.today() + timedelta(days=3)).isoformat()
BRIEF = f'حوار مع ضيف يوم {DAY} بميزانية 3000 ريال سعودي، مع صوت واضح ومونتاج للنشر.'


def field(value, source, reason, evidence=None):
    return dict(value=value, source=source, reason=reason, evidence=evidence)


def draft():
    return {'summary': 'حوار مع ضيف وصوت واضح ومونتاج للنشر.', 'fields': {
        'production_date': field(DAY, 'explicit', 'الموعد المذكور في الطلب.', DAY),
        'budget_sar': field(3000, 'explicit', 'ميزانيتك بالريال السعودي.', '3000 ريال سعودي'),
        'duration_hours': field(2, 'suggested', 'تقدير مبدئي لإعداد وتصوير حوار قصير.'),
        'camera_count': field(2, 'suggested', 'لقطة للضيف وأخرى للمحاور، ضمن المتاح.'),
        'include_audio': field(True, 'explicit', 'طلبت صوتًا واضحًا.', 'صوت واضح'),
        'editing_hours': field(2, 'suggested', 'تقدير للمونتاج والمراجعة، قابل للتعديل.'),
    }}


def context():
    return {'brief': BRIEF, 'today': date.today().isoformat(), 'catalog': {'capacity': {'studio_hours': 8, 'camera_packages': 2}}}


@pytest.mark.parametrize('kind', ['budget', 'date', 'evidence', 'capacity', 'extra', 'coercion'])
def test_untrustworthy_model_drafts_cannot_reach_review(kind):
    value = draft()
    if kind == 'budget':
        value['fields']['budget_sar'] = field(5000, 'suggested', 'ميزانية افتراضية.')
    elif kind == 'date':
        value['fields']['production_date'] = field(DAY, 'suggested', 'موعد افتراضي.')
    elif kind == 'evidence':
        value['fields']['budget_sar']['evidence'] = '5000 ريال'
    elif kind == 'capacity':
        value['fields']['camera_count']['value'] = 3
    elif kind == 'extra':
        value['total_sar'] = 1
    else:
        value['fields']['camera_count']['value'] = '2'
    with pytest.raises(ValidationError):
        Interpretation.model_validate(value, context=context())


def test_missing_date_and_budget_remain_questions():
    value = draft()
    value['fields']['production_date'] = field(None, 'missing', 'ما تاريخ الإنتاج المطلوب؟')
    value['fields']['budget_sar'] = field(None, 'missing', 'ما ميزانيتك بالريال السعودي؟')
    result = Interpretation.model_validate(value, context=context())
    assert result.fields.production_date.value is None
    assert result.fields.budget_sar.value is None


def test_interpretation_review_quote_ownership_and_single_use(tmp_path):
    captured, contexts = [], []

    async def interpret(payload):
        contexts.append(payload)
        return draft()

    async def dispatch(payload):
        captured.append(payload)

    config = Settings(tmp_path, 'http://testserver', 'w' * 40, 'r' * 40, 's' * 40, session_limit=1)
    app = create_app(config, dispatcher=dispatch, interpreter=interpret)
    client = TestClient(app)
    headers = session(client)
    response = client.post('/api/interpret', json={'brief': BRIEF}, headers=headers)
    assert response.status_code == 200
    result = response.json()
    assert contexts[0]['catalog']['capacity']['camera_packages'] == 2
    assert client.get('/api/catalog').json()['services'][0]['unit_price_sar'] == 350
    assert not captured  # Understanding alone cannot make an estimate or order.
    fields = {name: item['value'] for name, item in result['fields'].items()}
    submission = dict(brief=BRIEF, interpretation_id=result['id'], **fields)
    stranger = TestClient(app)
    assert stranger.post('/api/jobs', json=submission, headers=session(stranger)).status_code == 404
    # The final allowed brief can still be confirmed; it does not consume a second quota.
    response = client.post('/api/jobs', json=submission, headers=headers)
    assert response.status_code == 202
    job = response.json()['id']
    quote_and_report(client, config, captured[0])
    assert client.get('/api/jobs/' + job).json()['status'] in ('awaiting_review', 'rejected')
    assert client.post('/api/jobs', json=submission, headers=headers).status_code == 409
    assert client.post('/api/interpret', json={'brief': BRIEF}, headers=headers).status_code == 429


def test_invalid_model_output_fails_without_private_error_details(tmp_path):
    async def broken(payload):
        return {'error': 'provider-key-and-internal-url'}

    config = Settings(tmp_path, 'http://testserver', 'w' * 40, 'r' * 40, 's' * 40)
    client = TestClient(create_app(config, interpreter=broken))
    response = client.post('/api/interpret', json={'brief': BRIEF}, headers=session(client))
    assert response.status_code == 503
    assert 'provider-key' not in response.text
