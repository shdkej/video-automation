"""Per-request browser credentials must never persist or replace the server key."""
import json
import os
import threading
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from web import studio_ai
    from web.studio import create_router
    monkeypatch.setenv('OPENAI_API_KEY', 'server-key-placeholder')
    sdk = Mock()
    sdk.chat.completions.create.side_effect = RuntimeError('private upstream key')
    sdk.images.generate.side_effect = RuntimeError('private upstream key')
    sdk.audio.speech.with_streaming_response.create.side_effect = RuntimeError('private upstream key')
    factory = Mock(return_value=sdk)
    monkeypatch.setattr(studio_ai, 'OpenAI', factory)
    app = FastAPI(); app.include_router(create_router(tmp_path, threading.Semaphore(1)))
    client = TestClient(app)
    p = client.post('/api/studio/projects', json={'script': '내 원문'}).json()
    return client, factory, tmp_path, '/api/studio/projects/' + p['id']


@pytest.mark.parametrize('route,body', [('suggestions', {}), ('images', {'prompt': '바다', 'count': 2}), ('narration', {'voice': 'nova'})])
def test_browser_key_is_per_request_and_does_not_leak(setup, route, body):
    client, factory, root, base = setup
    response = client.post(base + '/' + route, json=body, headers={'X-OpenAI-API-Key': 'browser-key-placeholder'})
    assert factory.call_args.kwargs['api_key'] == 'browser-key-placeholder'
    assert response.status_code == 422
    assert 'browser-key-placeholder' not in response.text and 'private upstream key' not in response.text
    assert os.environ['OPENAI_API_KEY'] == 'server-key-placeholder'
    assert 'browser-key-placeholder' not in client.get(base).text
    assert all('browser-key-placeholder' not in f.read_text() for f in root.glob('**/*.json'))
    client.post(base + '/' + route, json=body)
    assert factory.call_args.kwargs['api_key'] == 'server-key-placeholder'


@pytest.mark.parametrize('key', ['', 'two words', 'a' * 513])
def test_invalid_browser_key_cannot_fall_back_to_server(setup, key):
    client, factory, _, base = setup
    response = client.post(base + '/suggestions', json={}, headers={'X-OpenAI-API-Key': key})
    assert response.status_code == 422
    factory.assert_not_called()
