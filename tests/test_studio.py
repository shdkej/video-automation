"""Studio public API: human text and assets survive edits, safe render/export."""
import io
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


def image_bytes():
    out = io.BytesIO()
    Image.new('RGB', (320, 480), '#b8c7a4').save(out, format='PNG')
    return out.getvalue()


@pytest.fixture
def studio(tmp_path):
    from web.studio import create_router
    app = FastAPI()
    app.include_router(create_router(tmp_path, threading.Semaphore(1)))
    return TestClient(app), tmp_path


def test_script_survives_save_upload_and_reopen(studio):
    client, root = studio
    p = client.post('/api/studio/projects', json={'title': '내 이야기'}).json()
    base = '/api/studio/projects/' + p['id']
    script = '  내가 쓴 문장입니다.\n\n둘째 줄은 그대로. '
    r = client.put(base, json={'script': script, 'hook': '내가 남긴 것'} )
    assert r.status_code == 200
    r = client.post(base + '/assets', files=[('files', ('사진.png', image_bytes(), 'image/png'))])
    assert r.status_code == 200
    asset = r.json()['assets'][0]
    assert client.get(asset['url']).status_code == 200
    # An old client snapshot must never erase newly uploaded assets.
    client.put(base, json={'script': script, 'assets': [], 'id': 'forged'})
    loaded = client.get(base).json()
    assert loaded['script'] == script and loaded['id'] == p['id']
    assert len(loaded['assets']) == 1
    from web.studio import create_router
    app2 = FastAPI()
    app2.include_router(create_router(root, threading.Semaphore(1)))
    assert TestClient(app2).get(base).json()['script'] == script


def test_rejects_invalid_media_and_render_inputs(studio):
    client, _ = studio
    p = client.post('/api/studio/projects', json={}).json()
    base = '/api/studio/projects/' + p['id']
    assert client.post(base + '/assets', files={'files': ('bad.png', b'not an image', 'image/png')}).status_code == 422
    assert client.post(base + '/render', json={}).status_code == 422
    assert client.put(base, json={'scenes': [{'id':'s','text':'hello','asset_id':'unknown','duration':-1,'trim_start':0}]}).status_code == 422
    client.put(base, json={'script':'hello', 'hook':'hello', 'scenes':[{'id':'s','text':'hello','asset_id':'unknown','duration':1,'trim_start':0}]})
    assert client.post(base + '/render', json={}).status_code == 422
    assert client.get(base + '/assets/../../project.json').status_code in (404, 422)


def test_real_render_has_video_cover_srt_and_snapshot(studio):
    client, _ = studio
    p = client.post('/api/studio/projects', json={}).json()
    base = '/api/studio/projects/' + p['id']
    a = client.post(base+'/assets', files={'files':('photo.png',image_bytes(),'image/png')}).json()['assets'][0]
    script = '직접 쓴 대본입니다.\n단어를 바꾸지 않습니다.'
    client.put(base, json={'script':script,'hook':'나의 첫 릴스','scenes':[{'id':'s','text':script,'asset_id':a['id'],'duration':1,'trim_start':0}]})
    r = client.post(base+'/render',json={})
    assert r.status_code == 200, r.text
    job = r.json()
    for _ in range(160):
        job = client.get(base+'/renders/'+job['id']).json()
        if job['status'] in ('done','error'): break
        time.sleep(.1)
    assert job['status'] == 'done', job
    assert client.get(job['outputs']['video']).content[4:8] == b'ftyp'
    assert client.get(job['outputs']['cover']).headers['content-type'].startswith('image/jpeg')
    assert script in client.get(job['outputs']['subtitles']).text
    assert client.get(job['outputs']['project']).json()['script'] == script


def test_narration_must_not_be_cut_and_audio_is_muxed(studio, tmp_path):
    import json
    import subprocess
    client, root = studio
    audio = tmp_path/'voice.wav'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2','-c:a','pcm_s16le',str(audio)],check=True)
    clip = tmp_path/'clip.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=blue:s=320x480:d=2','-c:v','libx264','-threads','1',str(clip)],check=True)
    p=client.post('/api/studio/projects',json={}).json()
    base='/api/studio/projects/'+p['id']
    a=client.post(base+'/assets',files={'files':('voice.wav',audio.read_bytes(),'audio/wav')}).json()['assets'][0]
    v=client.post(base+'/assets',files={'files':('clip.mp4',clip.read_bytes(),'video/mp4')}).json()['assets'][0]
    assert a['duration'] == 2 and v['kind']=='video'
    scene={'id':'one','text':'첫 문장\n둘째 문장','asset_id':v['id'],'duration':1,'trim_start':0}
    client.put(base,json={'script':scene['text'],'hook':'직접 녹음한 이야기','audio_asset_id':a['id'],'scenes':[scene]})
    assert client.post(base+'/render',json={}).status_code==422
    scene['duration']=2
    client.put(base,json={'scenes':[scene]})
    job=client.post(base+'/render',json={}).json()
    for _ in range(160):
        job=client.get(base+'/renders/'+job['id']).json()
        if job['status'] in ('done','error'): break
        time.sleep(.1)
    assert job['status']=='done',job
    loaded=client.get(base).json()
    assert loaded['last_render']['id']==job['id']
    output=root/p['id']/'renders'/job['id']/'reel.mp4'
    result=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(output)],capture_output=True,check=True)
    probe=json.loads(result.stdout)
    assert any(s['codec_type']=='audio' for s in probe['streams'])
    video=next(s for s in probe['streams'] if s['codec_type']=='video')
    assert (video['width'],video['height'])==(1080,1920)
    assert abs(float(probe['format']['duration'])-2)<.1
    assert '00:00:02,000' in client.get(job['outputs']['subtitles']).text


def test_suggestions_do_not_replace_human_choices(studio, monkeypatch):
    from web import studio_ai
    result={'keywords':[], 'hooks':[], 'search_queries':[], 'image_prompts':[], 'caption':'제안', 'source':'test', 'notices':[]}
    monkeypatch.setattr(studio_ai,'suggest',lambda project: result)
    client,_=studio
    p=client.post('/api/studio/projects',json={'script':'내 대본','hook':'내 후킹','caption':'내 캡션'}).json()
    base='/api/studio/projects/'+p['id']
    assert client.post(base+'/suggestions',json={}).json()==result
    saved=client.get(base).json()
    assert saved['suggestions']==result
    assert (saved['script'],saved['hook'],saved['caption'])==('내 대본','내 후킹','내 캡션')


def test_batch_upload_failure_rolls_back_new_assets(studio):
    client,_=studio
    p=client.post('/api/studio/projects',json={}).json()
    base='/api/studio/projects/'+p['id']
    response=client.post(base+'/assets',files=[('files',('good.png',image_bytes(),'image/png')),('files',('bad.png',b'bad','image/png'))])
    assert response.status_code==422
    assert client.get(base).json()['assets']==[]


def test_clip_duration_uses_video_stream_not_longer_audio(tmp_path):
    import subprocess
    from web.studio_render import media_info
    path=tmp_path/'short-video.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=red:s=160x240:d=1','-f','lavfi','-i','sine=duration=3','-c:v','libx264','-threads','1','-c:a','aac',str(path)],check=True)
    assert media_info(path)['duration']<=1.1


def test_rejects_invalid_unicode_without_breaking_saved_project(studio):
    client,_=studio
    p=client.post('/api/studio/projects',json={'script':'내 원문 😀'}).json()
    base='/api/studio/projects/'+p['id']
    response=client.put(base,content=b'{"script":"\\ud83d"}',headers={'Content-Type':'application/json'})
    assert response.status_code==422
    assert client.get(base).json()['script']=='내 원문 😀'


def test_reference_file_upload_creates_selectable_asset_and_survives_reopen(studio):
    client, _ = studio
    ref = {'id': 'saved-reference', 'title': '참고 이미지', 'kind': 'image', 'source_url': 'https://example.com/photo'}
    p = client.post('/api/studio/projects', json={'script': '내 대본', 'references': [ref]}).json()
    base = '/api/studio/projects/' + p['id']
    response = client.post(base + '/assets?reference_id=saved-reference', files={'files': ('photo.png', image_bytes(), 'image/png')})
    assert response.status_code == 200, response.text
    asset = response.json()['assets'][0]
    assert asset['reference_id'] == ref['id']
    assert asset['source_url'] == ref['source_url']
    client.put(base, json={'references': [ref], 'assets': [], 'scenes': [{'id': 's', 'text': '내 대본', 'asset_id': asset['id'], 'duration': 1}]})
    saved = client.get(base).json()
    assert saved['scenes'][0]['asset_id'] == saved['assets'][0]['id']
    assert saved['assets'][0]['reference_id'] == ref['id']
    assert saved['script'] == '내 대본'


def test_reference_upload_rejects_wrong_kind_and_rolls_back(studio):
    client, root = studio
    p = client.post('/api/studio/projects', json={'references': [{'id': 'youtube-one', 'kind': 'video'}]}).json()
    base = '/api/studio/projects/' + p['id']
    response = client.post(base + '/assets?reference_id=youtube-one', files={'files': ('wrong.png', image_bytes(), 'image/png')})
    assert response.status_code == 422
    assert client.get(base).json()['assets'] == []
    assert not list((root / p['id'] / 'assets').glob('*'))
    assert client.post(base + '/assets?reference_id=missing', files={'files': ('photo.png', image_bytes(), 'image/png')}).status_code == 404
    assert client.get(base).json()['assets'] == []


def test_reference_upload_is_one_file_at_a_time(studio):
    client, _ = studio
    p = client.post('/api/studio/projects', json={'references': [{'id': 'ref', 'kind': 'image'}]}).json()
    base = '/api/studio/projects/' + p['id']
    response = client.post(base + '/assets?reference_id=ref', files=[('files', ('one.png', image_bytes(), 'image/png')), ('files', ('two.png', image_bytes(), 'image/png'))])
    assert response.status_code == 422
    assert client.get(base).json()['assets'] == []


def test_archive_template_preserves_style_independently_of_media(studio):
    from zipfile import ZipFile
    from web.studio_templates import derive
    client, _ = studio
    design = derive('original/2026/sample.webp', Image.new('RGB', (96,120), '#215b79')).model_dump()
    assert design['wash'] == '#215b79'
    assert client.post('/api/studio/archive/design', json={'key':'https://other.example/a.jpg'}).status_code == 422
    design.update(name='바다', title_y=28, body_y=67, ink='#fffefe', wash='#001f45', strength=60, align='right')
    assert client.post('/api/studio/templates', json=design).status_code == 200
    assert client.get('/api/studio/templates').json() == [design]
    project = client.post('/api/studio/projects', json={}).json()
    base = '/api/studio/projects/'+project['id']
    asset = client.post(base+'/assets', files={'files':('replacement.png',image_bytes(),'image/png')}).json()['assets'][0]
    scenes = [dict(id=str(i),asset_id=asset['id'],text='본문 '+str(i)) for i in range(2)]
    assert client.put(base,json={'hook':'제목','design':design,'scenes':scenes}).status_code == 200
    assert client.get(base).json()['design'] == design
    response = client.post(base+'/cards')
    assert response.status_code == 200, response.text[:100]
    with ZipFile(io.BytesIO(response.content)) as cards:
        assert cards.namelist() == ['card-01.jpg','card-02.jpg']
        with Image.open(io.BytesIO(cards.read('card-01.jpg'))) as preview:
            assert preview.size == (1080,1920)
            assert preview.getpixel((5,5))[0] < 100


def test_vision_template_provider_layers_and_saved_outputs(studio, monkeypatch):
    import json
    import httpx
    import base64
    from zipfile import ZipFile
    from concurrent.futures import ThreadPoolExecutor
    from web import studio_templates, studio_render
    client, root = studio
    sample = dict(role='label', text='IN PHONE', x=30,y=32,width=50,size=6,rotation=18,
                  color='#ffee22', outline='#202020', align='left', bold=False)
    layout = dict(name='주방',composition='대각선 휴대폰과 하단 자막',palette=['#693824'],
                  wash='#513726', strength=20, regions=[sample,dict(sample,role='body',text='본문',y=78)])
    calls = []
    endpoint = '/api/studio/archive/design/vision'
    data = {'key':'original/2026/demo.webp'}
    def generate(url, **kwargs):
        calls.append((url,kwargs))
        return httpx.Response(200,request=httpx.Request('POST',url),json={
            'candidates':[{'content':{'parts':[{'text':json.dumps(layout)}]}}]})
    monkeypatch.setattr(studio_templates,'reference_bytes',lambda key:image_bytes())
    monkeypatch.setattr(httpx,'post', generate)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    assert client.post(endpoint,json=data,headers={'X-OpenAI-API-Key':'openai-only'}).status_code==422
    headers = {'X-Gemini-API-Key':'test-gemini-only'}
    with ThreadPoolExecutor(2) as pool:
        responses=list(pool.map(lambda _:client.post(endpoint,json=data,headers=headers),range(2)))
    assert sorted(r.json()['cached'] for r in responses)==[False,True]
    design = responses[0].json()['design']
    assert design['vision']['regions'][0]['rotation'] == 18
    assert len(calls)==1 and calls[0][0].endswith('/gemini-3.1-flash-lite:generateContent')
    assert calls[0][1]['headers']=={'x-goog-api-key':'test-gemini-only'}
    inline=calls[0][1]['json']['contents'][0]['parts'][0]['inlineData']
    assert Image.open(io.BytesIO(base64.b64decode(inline['data']))).size==(320,480)
    assert client.post(endpoint,json=data).json()['cached'] is True
    pid=client.post('/api/studio/projects',json={}).json()['id']
    base='/api/studio/projects/'+pid
    aid=client.post(base+'/assets',files={'files':('p.png',image_bytes(),'image/png')}).json()['assets'][0]['id']
    client.put(base,json={'design':design,'hook':'새 제목','script':'대본',
                          'scenes':[dict(id='1',text='교체 문구',asset_id=aid,duration=1)]})
    project=client.get(base).json()
    assert project['design']==design
    assert client.post('/api/studio/templates',json=design).status_code==200
    card=client.post(base+'/cards')
    assert card.status_code==200
    with ZipFile(io.BytesIO(card.content)) as archive:
        assert Image.open(io.BytesIO(archive.read('card-01.jpg'))).size == (1080,1920)
    # The shared final composition keeps user title even if vision only detects label/body.
    surface=root/'layer.png'
    studio_render.overlay(project,project['scenes'][0],0,surface)
    with Image.open(surface) as image:
        assert image.crop((50,330,1030,530)).getchannel('A').getextrema()[1] == 255
    layout['regions'][0]['x']=200
    assert client.post(endpoint,json={**data,'mode':'precision'},headers=headers).status_code==502
    assert calls[-1][0].endswith('/gemini-3.8-flash:generateContent')
    assert not any(b'test-gemini-only' in p.read_bytes() for p in (root/'vision-cache').iterdir())
