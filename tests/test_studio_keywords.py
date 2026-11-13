"""Direct measured keyword lookup must remain usable without an AI account."""
import json
import threading

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from web.studio import create_router
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('NAVER_SEARCH_ACCESS_LICENSE', 'test-license')
    monkeypatch.setenv('NAVER_SEARCH_SECRET_KEY', 'test-secret')
    monkeypatch.setenv('NAVER_SEARCH_CUSTOMER_ID', '12345')
    app = FastAPI()
    app.include_router(create_router(tmp_path, threading.Semaphore(1)))
    return TestClient(app)


def test_measured_keywords_work_without_openai_and_survive_edits(client, monkeypatch):
    def get(url, **kwargs):
        assert url == 'https://api.searchad.naver.com/keywordstool'
        assert kwargs['params'] == {'hintKeywords': '세계여행,신혼여행', 'showDetail': '1'}
        assert kwargs['headers']['X-Customer'] == '12345'
        assert kwargs['headers']['X-Signature']
        return httpx.Response(200, json={'keywordList': [
            {'relKeyword': '세계여행', 'monthlyPcQcCnt': 330, 'monthlyMobileQcCnt': 1040, 'compIdx': '높음'},
            {'relKeyword': '여행준비', 'monthlyPcQcCnt': '< 10', 'monthlyMobileQcCnt': 200, 'compIdx': '중간'},
            {'relKeyword': '희귀여행', 'monthlyPcQcCnt': '< 10', 'monthlyMobileQcCnt': '<10', 'compIdx': '낮음'},
        ]})
    monkeypatch.setattr(httpx, 'get', get)
    p = client.post('/api/studio/projects', json={'script': '내가 쓴 원문', 'hook': '나의 첫 문장'}).json()
    base = '/api/studio/projects/' + p['id']
    response = client.post(base + '/keywords/search', json={'query': '세계 여행, 신혼여행,세계여행'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['provider'] == 'Naver SearchAd' and result['queried_at']
    assert result['seeds'] == ['세계여행', '신혼여행']
    first, second, third = result['items']
    assert first['keyword'] == '세계여행' and first['monthly_total'] == 1370 and first['is_seed']
    assert second['monthly_total'] is None and second['monthly_label'] == '200~209'
    assert third['monthly_total'] is None and third['monthly_label'] == '20 미만'
    assert client.get('/api/studio/capabilities').json()['keyword_search'] is True
    assert client.get('/api/studio/capabilities').json()['ai_text'] is False
    client.put(base, json={'keywords': ['세계여행'], 'keyword_research': {'forged': True}, 'assets': []})
    saved = client.get(base).json()
    assert saved['keyword_research'] == result
    assert (saved['script'], saved['hook'], saved['keywords']) == ('내가 쓴 원문', '나의 첫 문장', ['세계여행'])


@pytest.mark.parametrize('query', ['', '  ', ', ,', 'a,b,c,d,e,f', 'x' * 101, '!!!', '\ud83d'])
def test_bad_seeds_never_call_provider(client, monkeypatch, query):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid input reached a provider')
    monkeypatch.setattr(httpx, 'get', forbidden)
    p = client.post('/api/studio/projects', json={}).json()
    assert client.post(f"/api/studio/projects/{p['id']}/keywords/search", content=json.dumps({'query': query}), headers={'Content-Type': 'application/json'}).status_code == 422


def test_missing_credentials_disables_only_measurements(client, monkeypatch):
    monkeypatch.delenv('NAVER_SEARCH_CUSTOMER_ID')
    assert client.get('/api/studio/capabilities').json().get('keyword_search') is False
    p = client.post('/api/studio/projects', json={}).json()
    response = client.post(f"/api/studio/projects/{p['id']}/keywords/search", json={'query': '여행'})
    assert response.status_code == 503


@pytest.mark.parametrize('provider_status,expected', [(401, 502), (403, 502), (429, 429), (500, 502)])
def test_provider_failures_do_not_leak_secrets_or_overwrite_research(client, monkeypatch, provider_status, expected):
    p = client.post('/api/studio/projects', json={}).json()
    base = f"/api/studio/projects/{p['id']}"
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: httpx.Response(200, json={'keywordList': []}))
    prior = client.post(base + '/keywords/search', json={'query': '여행'}).json()
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: httpx.Response(provider_status, text='test-secret private-provider-body'))
    response = client.post(base + '/keywords/search', json={'query': '여행'})
    assert response.status_code == expected
    assert 'test-secret' not in response.text and 'private-provider-body' not in response.text
    assert client.get(base).json()['keyword_research'] == prior


@pytest.mark.parametrize('payload', [None, {}, {'keywordList': None}, {'keywordList': [None]}])
def test_malformed_data_is_not_claimed_as_an_empty_success(client, monkeypatch, payload):
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: httpx.Response(200, json=payload))
    p = client.post('/api/studio/projects', json={}).json()
    assert client.post(f"/api/studio/projects/{p['id']}/keywords/search", json={'query': '여행'}).status_code == 502


def test_timeout_is_actionable_and_unknown_counts_are_not_zero(client, monkeypatch):
    p = client.post('/api/studio/projects', json={}).json()
    base = f"/api/studio/projects/{p['id']}"
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout('private address')
    monkeypatch.setattr(httpx, 'get', timeout)
    r = client.post(base + '/keywords/search', json={'query': '여행'})
    assert r.status_code == 504 and 'private address' not in r.text
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: httpx.Response(200, json={'keywordList': [
        {'relKeyword': '여행', 'monthlyPcQcCnt': None, 'monthlyMobileQcCnt': 10},
        {'relKeyword': '여행', 'monthlyPcQcCnt': 20, 'monthlyMobileQcCnt': 30},
        {'relKeyword': '여행준비', 'monthlyPcQcCnt': '1,000', 'monthlyMobileQcCnt': '2,000'},
    ]}))
    result = client.post(base + '/keywords/search', json={'query': '여행'}).json()
    assert len(result['items']) == 2
    assert result['items'][0]['monthly_label'] == '미확인'
    assert result['items'][0]['monthly_total'] is None
    assert result['items'][1]['monthly_total'] == 3000
