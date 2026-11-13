"""Provider contract tests: mocked network only; no paid OpenAI calls."""
import base64
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from PIL import Image


def ai_module():
    # Keep the first red result a contract assertion, rather than an import error.
    import importlib.util
    assert importlib.util.find_spec('web.studio_ai'), 'Studio provider module is missing'
    from web import studio_ai
    return studio_ai


@pytest.fixture
def ai(monkeypatch):
    module = ai_module()
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only-never-a-real-key')
    monkeypatch.delenv('YOUTUBE_API_KEY', raising=False)
    monkeypatch.setattr(module, 'OpenAI', Mock(side_effect=AssertionError('Unmocked paid API call')))
    return module


@pytest.fixture
def project():
    return {'script': '  다합에서 숙소를 옮겼어요.\n바다를 매일 걸었어요.  ', 'topic': '다합 여행',
            'audience': '여행 준비하는 사람', 'goal': '경험 공유', 'language': 'ko',
            'keywords': ['다합 여행'], 'hook': '사람이 고른 문장'}


def suggestions():
    angles = ['비용', '감정', '장소', '방법', '결과']
    words = ['다합 숙소 비용', '다합 교통비', '다합 여행 기분', '다합 일상', '다합 해변',
             '다합 숙소', '다합 걷기', '다합 숙소 이동', '다합 여행 변화', '다합 여행 경험']
    return {'keywords': [{'text': word, 'angle': angles[index // 2], 'reason': '대본의 여행 경험과 연결',
                          'evidence': '월간 검색량 5000'} for index, word in enumerate(words)],
            'hooks': [{'text': f'{words[index]}, 숙소를 옮긴 이유', 'type': kind,
                       'keyword': words[index], 'reason': '원문의 선택을 보여줘요'}
                      for index, kind in enumerate(['호기심갭형', '선택대비형', '변화형', '질문형', '정보형'])],
            'search_queries': ['Dahab coast', 'Dahab walking'],
            'image_prompts': ['A calm Dahab coast', 'Walking along the coast'],
            'caption': '다합에서 숙소를 옮기고 바다를 걸었어요.', 'source': 'invented search data', 'notices': []}


def fake_text_client(ai, payload):
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))])
    ai.OpenAI = Mock(return_value=client)
    return client


def test_capabilities_and_missing_key_are_truthful(ai, monkeypatch, project):
    monkeypatch.delenv('OPENAI_API_KEY')
    assert ai.capabilities() == {'ai_text': False, 'ai_images': False, 'ai_narration': False,
                                 'reference_search': True}
    with pytest.raises(ValueError, match='OPENAI_API_KEY'):
        ai.suggest(project)
    ai.OpenAI.assert_not_called()


def test_suggestions_preserve_script_validate_diversity_and_override_unmeasured_claims(ai, project):
    original = copy.deepcopy(project)
    client = fake_text_client(ai, suggestions())
    result = ai.suggest(project)
    assert project == original
    assert len(result['keywords']) >= 10
    assert len({x['angle'] for x in result['keywords']}) >= 5
    assert len({x['type'] for x in result['hooks']}) >= 5
    assert all(x['evidence'] == '검색량 미확인·대본 기반 제안' for x in result['keywords'])
    assert result['source'].startswith('OpenAI / ')
    assert '검색량 미확인' in ' '.join(result['notices'])
    sent = client.chat.completions.create.call_args.kwargs
    assert json.loads(sent['messages'][1]['content'])['script'] == project['script']
    assert '숫자' in sent['messages'][0]['content']
    assert '비용' in sent['messages'][0]['content']
    assert sent['response_format'] == {'type': 'json_object'}


@pytest.mark.parametrize('broken', ['missing', 'few_keywords', 'same_angles', 'same_types', 'duplicate_keywords', 'bad_keyword'])
def test_suggestions_reject_incomplete_or_undiversified_provider_output(ai, project, broken):
    payload = suggestions()
    if broken == 'missing': del payload['image_prompts']
    if broken == 'few_keywords': payload['keywords'] = payload['keywords'][:9]
    if broken == 'same_angles':
        for item in payload['keywords']: item['angle'] = '방법'
    if broken == 'same_types':
        for item in payload['hooks']: item['type'] = '정보형'
    if broken == 'duplicate_keywords': payload['keywords'][1]['text'] = payload['keywords'][0]['text']
    if broken == 'bad_keyword': payload['hooks'][0]['keyword'] = '없는 키워드'
    fake_text_client(ai, payload)
    with pytest.raises(ValueError, match='추천 응답'):
        ai.suggest(project)


def test_provider_failure_is_actionable_and_does_not_leak_response_or_secrets(ai, project):
    client = fake_text_client(ai, suggestions())
    client.chat.completions.create.side_effect = RuntimeError('token=test-only-secret input=private-script')
    with pytest.raises(ValueError) as exc:
        ai.suggest(project)
    assert 'OpenAI' in str(exc.value)
    assert 'secret' not in str(exc.value) and 'private-script' not in str(exc.value)


def png_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (32, 48), '#dddcc1').save(buffer, format='PNG')
    return buffer.getvalue()


def test_images_request_count_decode_real_bytes_and_report_partial_failure(ai, project, tmp_path):
    client = Mock()
    client.images.generate.return_value = SimpleNamespace(data=[
        SimpleNamespace(b64_json=base64.b64encode(png_bytes()).decode()),
        SimpleNamespace(b64_json='invalid-base64')])
    ai.OpenAI = Mock(return_value=client)
    result = ai.generate_images(project, '바닷가에서 걷는 장면', 3, tmp_path)
    assert len(result['images']) == 1 and len(result['errors']) == 2
    saved = Path(result['images'][0]['path'])
    with Image.open(saved) as picture:
        assert picture.format == 'PNG' and picture.size == (32, 48)
    assert result['images'][0]['prompt'] == '바닷가에서 걷는 장면'
    assert client.images.generate.call_args.kwargs['n'] == 3
    assert client.images.generate.call_args.kwargs['model'] == 'gpt-image-1.5'
    assert ai.OpenAI.call_args.kwargs['max_retries'] == 0
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize('count', [0, 1, 7, True, 2.5])
def test_images_invalid_count_never_calls_provider(ai, project, tmp_path, count):
    with pytest.raises(ValueError): ai.generate_images(project, 'coast', count, tmp_path)
    ai.OpenAI.assert_not_called()


def http_mock(ai, monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(ai.httpx, 'Client', lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))


def commons_page(kind='image', *, licensed=True, unsafe=False):
    extension = 'jpg' if kind == 'image' else 'webm'
    return {'pageid': 42 if kind == 'image' else 43, 'index': 1, 'title': f'File:Dahab.{extension}',
            'imageinfo': [{'url': f'https://upload.wikimedia.org/wikipedia/commons/a/ab/Dahab.{extension}'
                          if not unsafe else 'http://127.0.0.1/private',
                          'thumburl': 'https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Dahab.jpg/400px-Dahab.jpg',
                          'descriptionurl': f'https://commons.wikimedia.org/wiki/File:Dahab.{extension}',
                          'mime': 'image/jpeg' if kind == 'image' else 'video/webm', 'size': 1000,
                          'extmetadata': {'Artist': {'value': '<a href="url">Example photographer</a>'},
                                          'LicenseShortName': {'value': 'CC BY-SA 4.0' if licensed else ''}}}]}


def test_reference_search_returns_actual_image_and_video_results_with_rights(ai, monkeypatch, project):
    requests = []
    def handler(request):
        requests.append(request)
        kind = 'video' if 'filetype:video' in request.url.params['gsrsearch'] else 'image'
        return httpx.Response(200, json={'query': {'pages': [commons_page(kind)]}})
    http_mock(ai, monkeypatch, handler)
    result = ai.search_references(project, 'Dahab', 'all')
    assert {item['kind'] for item in result['items']} == {'image', 'video'}
    assert len(requests) == 2
    for item in result['items']:
        assert item['provider'] == 'Wikimedia Commons'
        assert item['license'] == 'CC BY-SA 4.0' and item['credit'] == 'Example photographer'
        assert item['importable'] is True and 'Dahab' in item['reason']
        assert item['source_url'].startswith('https://commons.wikimedia.org/')


def test_reference_search_drops_unsafe_urls_and_marks_unknown_rights_unimportable(ai, monkeypatch, project):
    http_mock(ai, monkeypatch, lambda request: httpx.Response(200, json={'query': {'pages': [
        commons_page(unsafe=True), commons_page(licensed=False)]}}))
    result = ai.search_references(project, 'Dahab', 'image')
    assert len(result['items']) == 1
    assert result['items'][0]['importable'] is False
    assert '미확인' in result['items'][0]['license']


def test_reference_failure_is_visible_without_fabricated_results(ai, monkeypatch, project):
    http_mock(ai, monkeypatch, lambda request: httpx.Response(429, text='private error detail'))
    result = ai.search_references(project, 'Dahab', 'all')
    assert result['items'] == []
    assert result['notices'] and '실패' in ' '.join(result['notices'])
    assert 'private error detail' not in ' '.join(result['notices'])


def test_youtube_uses_only_returned_ids_and_is_not_importable(ai, monkeypatch, project):
    monkeypatch.setenv('YOUTUBE_API_KEY', 'test-youtube-key')
    def handler(request):
        if request.url.host == 'commons.wikimedia.org': return httpx.Response(200, json={'query': {'pages': []}})
        assert request.url.host == 'www.googleapis.com'
        return httpx.Response(200, json={'items': [
            {'id': {'videoId': 'abcdefghijk'}, 'snippet': {'title': 'Dahab walk', 'channelTitle': 'A creator',
                'thumbnails': {'medium': {'url': 'https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg'}}}},
            {'id': {'videoId': 'bad-id'}, 'snippet': {'title': 'bad'}}]})
    http_mock(ai, monkeypatch, handler)
    result = ai.search_references(project, 'Dahab', 'video')
    assert len(result['items']) == 1
    assert result['items'][0]['url'] == 'https://www.youtube.com/watch?v=abcdefghijk'
    assert result['items'][0]['importable'] is False


def reference(url='https://upload.wikimedia.org/wikipedia/commons/a/ab/Dahab.jpg'):
    return {'provider': 'Wikimedia Commons', 'url': url, 'kind': 'image', 'importable': True,
            'source_url': 'https://commons.wikimedia.org/wiki/File:Dahab.jpg', 'license': 'CC BY-SA 4.0'}


@pytest.mark.parametrize('url', ['http://upload.wikimedia.org/wikipedia/commons/a.jpg',
    'https://upload.wikimedia.org.evil.test/a.jpg', 'https://127.0.0.1/a.jpg',
    'https://upload.wikimedia.org@evil.test/a.jpg', 'https://user@upload.wikimedia.org/wikipedia/commons/a.jpg',
    'https://upload.wikimedia.org:444/wikipedia/commons/a.jpg',
    'https://upload.wikimedia.org/wikipedia/commons/../../private.jpg'])
def test_reference_import_rejects_unapproved_network_before_request(ai, monkeypatch, tmp_path, url):
    calls = []
    http_mock(ai, monkeypatch, lambda request: calls.append(request))
    with pytest.raises(ValueError): ai.import_reference(reference(url), tmp_path)
    assert not calls and not list(tmp_path.iterdir())


def test_reference_import_blocks_redirect_to_private_network(ai, monkeypatch, tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location': 'http://169.254.169.254/latest/meta-data/'})
    http_mock(ai, monkeypatch, handler)
    with pytest.raises(ValueError): ai.import_reference(reference(), tmp_path)
    assert len(calls) == 1 and not list(tmp_path.iterdir())


def test_reference_import_writes_bounded_binary_to_server_chosen_name(ai, monkeypatch, tmp_path):
    payload = png_bytes()
    http_mock(ai, monkeypatch, lambda request: httpx.Response(200, content=payload, headers={'Content-Type': 'image/png'}))
    result = ai.import_reference(reference(), tmp_path)
    assert result['kind'] == 'image'
    assert Path(result['path']).parent == tmp_path
    assert Path(result['path']).read_bytes() == payload
    assert result['name'].endswith('.png')


def test_reference_import_removes_partial_file_when_body_exceeds_limit(ai, monkeypatch, tmp_path):
    monkeypatch.setattr(ai, 'MAX_REFERENCE_BYTES', 10)
    http_mock(ai, monkeypatch, lambda request: httpx.Response(200, content=b'x' * 11, headers={'Content-Type': 'image/png'}))
    with pytest.raises(ValueError, match='크기'): ai.import_reference(reference(), tmp_path)
    assert not list(tmp_path.iterdir())


def test_narration_sends_verbatim_script_and_saves_audio(ai, project, tmp_path):
    client = Mock()
    response = Mock()
    response.iter_bytes.return_value = iter([b'ID3', b'fake-audio-fixture'])
    client.audio.speech.with_streaming_response.create.return_value.__enter__ = Mock(return_value=response)
    client.audio.speech.with_streaming_response.create.return_value.__exit__ = Mock(return_value=False)
    ai.OpenAI = Mock(return_value=client)
    original = copy.deepcopy(project)
    result = ai.generate_narration(project, 'alloy', tmp_path)
    assert client.audio.speech.with_streaming_response.create.call_args.kwargs['input'] == project['script']
    assert project == original and result['kind'] == 'audio'
    assert Path(result['path']).read_bytes() == b'ID3fake-audio-fixture'


@pytest.mark.parametrize('script,voice', [('x' * 4001, 'alloy'), ('   ', 'alloy'), ('대본', 'cloned-person')])
def test_narration_invalid_input_never_calls_provider(ai, project, tmp_path, script, voice):
    project['script'] = script
    with pytest.raises(ValueError): ai.generate_narration(project, voice, tmp_path)
    ai.OpenAI.assert_not_called()


def test_provider_contract_exists():
    module = ai_module()
    for name in ('capabilities', 'suggest', 'generate_images', 'generate_narration', 'search_references', 'import_reference'):
        assert callable(getattr(module, name, None)), f'Missing provider function: {name}'


@pytest.mark.parametrize('location', ['hooks', 'caption'])
def test_suggestions_reject_numbers_absent_from_human_script(ai, project, location):
    payload = suggestions()
    if location == 'hooks': payload['hooks'][0]['text'] += ' 99만원'
    else: payload['caption'] += ' 99만원'
    fake_text_client(ai, payload)
    with pytest.raises(ValueError, match='숫자'):
        ai.suggest(project)


def test_suggestions_allow_numbers_that_are_actually_in_script(ai, project):
    project['script'] += '\n숙소에는 99만원을 썼어요.'
    payload = suggestions()
    payload['hooks'][0]['text'] += ' 99만원'
    fake_text_client(ai, payload)
    assert '99만원' in ai.suggest(project)['hooks'][0]['text']


@pytest.mark.parametrize('content', ['not json', '[]', '{}', '{"keywords":null}'])
def test_suggestions_reject_invalid_json_shapes_without_fallback(ai, project, content):
    client = fake_text_client(ai, suggestions())
    client.chat.completions.create.return_value.choices[0].message.content = content
    with pytest.raises(ValueError, match='추천 응답'): ai.suggest(project)


def test_image_provider_failure_does_not_leave_files(ai, project, tmp_path):
    client = Mock()
    client.images.generate.side_effect = RuntimeError('private request body')
    ai.OpenAI = Mock(return_value=client)
    with pytest.raises(ValueError, match='이미지 생성 실패'): ai.generate_images(project, 'coast', 2, tmp_path)
    assert not list(tmp_path.iterdir())


def test_generated_non_image_base64_is_rejected(ai, project, tmp_path):
    client = Mock()
    client.images.generate.return_value = SimpleNamespace(data=[
        SimpleNamespace(b64_json=base64.b64encode(b'<html>not an image</html>').decode())] * 2)
    ai.OpenAI = Mock(return_value=client)
    result = ai.generate_images(project, 'coast', 2, tmp_path)
    assert not result['images'] and len(result['errors']) == 2
    assert not list(tmp_path.iterdir())


def test_import_allows_safe_commons_redirect_and_blocks_loop(ai, monkeypatch, tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location': '/wikipedia/commons/a/ab/Other.jpg'})
    http_mock(ai, monkeypatch, handler)
    with pytest.raises(ValueError, match='리디렉션'): ai.import_reference(reference(), tmp_path)
    assert len(calls) == 4 and not list(tmp_path.iterdir())


def test_reference_import_rejects_html_response(ai, monkeypatch, tmp_path):
    http_mock(ai, monkeypatch, lambda request: httpx.Response(200, content=b'<html>blocked</html>',
                                                           headers={'Content-Type': 'text/html'}))
    with pytest.raises(ValueError, match='이미지'): ai.import_reference(reference(), tmp_path)
    assert not list(tmp_path.iterdir())


def test_narration_failure_removes_partial_file_and_preserves_script(ai, project, tmp_path):
    client = Mock()
    def broken_stream():
        yield b'ID3'
        raise httpx.ReadTimeout('secret request')
    response = Mock()
    response.iter_bytes.return_value = broken_stream()
    client.audio.speech.with_streaming_response.create.return_value.__enter__ = Mock(return_value=response)
    client.audio.speech.with_streaming_response.create.return_value.__exit__ = Mock(return_value=False)
    ai.OpenAI = Mock(return_value=client)
    with pytest.raises(ValueError, match='내레이션 생성 실패'): ai.generate_narration(project, 'alloy', tmp_path)
    assert not list(tmp_path.iterdir())


def test_commons_current_api_tracking_urls_and_thumbnail_host_are_accepted(ai, monkeypatch, project):
    page = commons_page()
    info = page['imageinfo'][0]
    info['url'] += '?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original'
    info['thumburl'] = info['thumburl'].replace('upload.wikimedia.org', 'thumb.wikimedia.org')
    info['thumburl'] += '?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail'
    http_mock(ai, monkeypatch, lambda request: httpx.Response(200, json={'query': {'pages': [page]}}))
    result = ai.search_references(project, 'Dahab', 'image')
    assert len(result['items']) == 1
    assert '?' not in result['items'][0]['url']
    assert result['items'][0]['thumbnail'].startswith('https://thumb.wikimedia.org/')
    assert '?' not in result['items'][0]['thumbnail']


@pytest.mark.parametrize('field,claim', [('keywords','월간 검색량 5000회'),('hooks','유튜브 조회수 10만으로 검증'),('keywords','검색량이 높아 인기가 검증되었습니다')])
def test_unmeasured_metrics_cannot_leak_through_reasons(ai, project, field, claim):
    payload=suggestions()
    payload[field][0]['reason']=claim
    fake_text_client(ai,payload)
    with pytest.raises(ValueError):
        ai.suggest(project)
