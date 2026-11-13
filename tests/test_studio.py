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
