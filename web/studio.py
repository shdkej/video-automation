"""Persistent single-user Reel Room studio. Mounted behind existing app authentication."""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from web import studio_render


class StudioRoute(APIRoute):
    """Return useful validation errors without echoing private or invalid input."""
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                messages = '; '.join(str(error['msg']) for error in exc.errors()[:3])
                # An unpaired UTF-16 surrogate in JSON must be a 422, not an
                # encoding failure while FastAPI echoes its invalid input.
                messages = messages.encode('utf-8', errors='replace').decode('utf-8')
                raise HTTPException(422, '입력값을 확인해 주세요: ' + messages) from exc
        return handler


def now():
    return datetime.now(timezone.utc).isoformat()


def ident():
    return uuid.uuid4().hex


class Scene(BaseModel):
    id: str = Field(min_length=1,max_length=80)
    text: str = Field(default='',max_length=500)
    asset_id: str = Field(default='',max_length=64)
    duration: float = Field(default=5,ge=1,le=30,allow_inf_nan=False)
    trim_start: float = Field(default=0,ge=0,le=7200,allow_inf_nan=False)


class ProjectPatch(BaseModel):
    title: str = Field(default='새 릴스',max_length=120)
    script: str = Field(default='',max_length=12000)
    topic: str = Field(default='',max_length=500)
    audience: str = Field(default='',max_length=500)
    goal: str = Field(default='',max_length=500)
    language: str = Field(default='한국어',max_length=40)
    keywords: list[str] = Field(default_factory=list,max_length=30)
    hook: str = Field(default='',max_length=180)
    caption: str = Field(default='',max_length=2200)
    cta: str = Field(default='',max_length=180)
    style: Literal['photo','shade','editorial'] = 'shade'
    font: Literal['pretendard','blackhansans','dohyeon'] = 'pretendard'
    position: Literal['top','center','bottom'] = 'top'
    scenes: list[Scene] = Field(default_factory=list,max_length=30)
    audio_asset_id: str | None = Field(default=None,max_length=64)
    references: list[dict] = Field(default_factory=list,max_length=50)


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1,max_length=3000)
    count: int = Field(default=4,ge=2,le=6)


class ReferenceSearch(BaseModel):
    query: str = Field(min_length=1,max_length=200)
    kind: Literal['all','image','video'] = 'all'


class NarrationRequest(BaseModel):
    voice: Literal['alloy','nova','onyx','shimmer','echo','fable'] = 'nova'


class StudioStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()

    def directory(self, project_id: str) -> Path:
        if not re.fullmatch('[a-f0-9]{32}',project_id):
            raise HTTPException(404,'프로젝트를 찾을 수 없습니다.')
        return self.root/project_id

    def read(self, project_id: str) -> dict:
        with self.lock:
            try:
                return json.loads((self.directory(project_id)/'project.json').read_text())
            except (FileNotFoundError,json.JSONDecodeError):
                raise HTTPException(404,'프로젝트를 찾을 수 없습니다.')

    @staticmethod
    def write_json(path: Path, data: dict):
        try:
            encoded = json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8')
        except UnicodeError as exc:
            raise HTTPException(422,'문자가 손상되었습니다. 대본이나 참고 자료의 해당 문장을 다시 입력해 주세요.') from exc
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary = path.with_name(path.name+'.'+ident()+'.tmp')
        temporary.write_bytes(encoded)
        temporary.replace(path)

    def save(self, project: dict):
        project['updated_at'] = now()
        self.write_json(self.directory(project['id'])/'project.json',project)

    def public(self, project: dict) -> dict:
        result = {k:v for k,v in project.items() if not k.startswith('_')}
        result['assets'] = [{k:v for k,v in a.items() if not k.startswith('_')} for a in project['assets']]
        result['last_render'] = None
        rid = project.get('_last_render_id')
        if rid:
            try:
                result['last_render'] = json.loads((self.directory(project['id'])/'renders'/rid/'status.json').read_text())
            except (FileNotFoundError,json.JSONDecodeError): pass
        return result

    def resolve(self, project: dict, asset_id: str):
        asset = next((a for a in project['assets'] if a['id']==asset_id),None)
        if not asset:
            raise HTTPException(422,'장면에 사용할 소재를 선택해 주세요.')
        path = self.directory(project['id'])/'assets'/asset['_file']
        if not path.is_file():
            raise HTTPException(422,'저장된 소재를 찾을 수 없습니다. 다시 첨부해 주세요.')
        return path, asset

    def add(self, project_id: str, path: Path, name: str, source: str, **meta) -> dict:
        try:
            try:
                with Image.open(path) as image:
                    if image.width*image.height > 32_000_000:
                        raise ValueError('이미지는 3,200만 화소 이하로 첨부해 주세요.')
                    image.verify()
                info = {'kind':'image'}
            except UnidentifiedImageError:
                info = studio_render.media_info(path)
            aid = ident()
            assets_dir = self.directory(project_id)/'assets'
            assets_dir.mkdir(exist_ok=True)
            if info['kind']=='image':
                target = assets_dir/(aid+'.png')
                with Image.open(path) as image:
                    image = ImageOps.exif_transpose(image).convert('RGB')
                    image.thumbnail((3840,3840))
                    image.save(target)
            else:
                suffix = path.suffix.lower()
                if suffix not in {'.mp4','.mov','.m4v','.webm','.mkv','.mp3','.m4a','.wav','.ogg','.ogv','.aac','.flac'}:
                    suffix = '.mp4' if info['kind']=='video' else '.mp3'
                target = assets_dir/(aid+suffix)
                shutil.copyfile(path,target)
            asset = {'id':aid,'name':Path(name).name[:200],'source':source,'url':f'/api/studio/projects/{project_id}/assets/{aid}',
                     '_file':target.name,**info,**{k:v for k,v in meta.items() if v is not None}}
            with self.lock:
                project = self.read(project_id)
                if len(project['assets']) >= 100:
                    target.unlink(missing_ok=True)
                    raise ValueError('한 프로젝트에는 소재를 100개까지 저장할 수 있습니다.')
                project['assets'].append(asset)
                self.save(project)
            return {k:v for k,v in asset.items() if not k.startswith('_')}
        except (OSError,ValueError,Image.DecompressionBombError) as exc:
            if isinstance(exc,ValueError): raise
            raise ValueError('파일을 읽을 수 없습니다. PNG/JPG, MP4 또는 음성 파일을 다시 첨부해 주세요.') from exc


def create_router(root: Path, work_limiter: threading.Semaphore) -> APIRouter:
    store = StudioStore(root)
    router = APIRouter(prefix='/api/studio', route_class=StudioRoute)
    ai_limiter = threading.BoundedSemaphore(1)
    render_lock = threading.Lock()
    active: set[str] = set()

    def provider_call(fn,*args):
        if not ai_limiter.acquire(blocking=False):
            raise HTTPException(429,'앞선 AI 요청이 끝난 뒤 다시 시도해 주세요.')
        try:
            return fn(*args)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502,'AI 서비스에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.') from exc
        finally:
            ai_limiter.release()

    @router.get('/capabilities')
    def capabilities():
        from web import studio_ai
        return studio_ai.capabilities()

    @router.get('/projects')
    def projects():
        items = []
        for file in store.root.glob('*/project.json'):
            try: items.append(store.public(store.read(file.parent.name)))
            except HTTPException: pass
        return {'projects': sorted(items,key=lambda p:p['updated_at'],reverse=True)}

    @router.post('/projects')
    def create(payload: ProjectPatch = Body(default=ProjectPatch())):
        with store.lock:
            p = {'id':ident(),**payload.model_dump(),'assets':[],'created_at':now()}
            store.save(p)
            return store.public(p)

    @router.get('/projects/{pid}')
    def get_project(pid: str):
        project = store.public(store.read(pid))
        if project.get('last_render'):
            project['last_render'] = render_status(pid, project['last_render']['id'])
        return project

    @router.put('/projects/{pid}')
    def update(pid: str,payload: ProjectPatch):
        with store.lock:
            project = store.read(pid)
            changes = payload.model_dump(exclude_unset=True)
            if any(len(k)>100 for k in changes.get('keywords',[])):
                raise HTTPException(422,'키워드는 각각 100자 이내로 입력해 주세요.')
            if 'references' in changes and len(json.dumps(changes['references']))>50000:
                raise HTTPException(422,'참고 자료 메모가 너무 깁니다.')
            project.update(changes)
            store.save(project)
            return store.public(project)

    @router.post('/projects/{pid}/assets')
    def upload(pid: str,files: list[UploadFile] = File(...)):
        store.read(pid)
        if len(files)>20:
            raise HTTPException(422,'한 번에 20개까지 첨부할 수 있습니다.')
        uploaded=[]
        total=0
        temp = store.directory(pid)/('upload-'+ident())
        temp.mkdir()
        try:
            for file in files:
                name = file.filename or 'media'
                path=temp/(ident()+Path(name).suffix.lower())
                count=0
                with path.open('wb') as handle:
                    while chunk:=file.file.read(1024*1024):
                        total += len(chunk)
                        count += len(chunk)
                        if count>200*1024**2 or total>500*1024**2:
                            raise HTTPException(413,'파일은 200MB, 한 번의 첨부는 500MB까지 가능합니다.')
                        handle.write(chunk)
                uploaded.append(store.add(pid,path,name,'upload'))
            return {'assets':uploaded}
        except Exception as exc:
            # A failed batch must not leave hidden partial uploads behind.
            ids = {asset['id'] for asset in uploaded}
            if ids:
                with store.lock:
                    project = store.read(pid)
                    paths = [store.directory(pid)/'assets'/a['_file'] for a in project['assets'] if a['id'] in ids]
                    project['assets'] = [a for a in project['assets'] if a['id'] not in ids]
                    store.save(project)
                    for path in paths: path.unlink(missing_ok=True)
            if isinstance(exc, ValueError):
                raise HTTPException(422,str(exc)) from exc
            raise
        finally:
            shutil.rmtree(temp,ignore_errors=True)
            for file in files: file.file.close()

    @router.get('/projects/{pid}/assets/{aid}')
    def asset(pid: str,aid: str):
        path, _ = store.resolve(store.read(pid),aid)
        return FileResponse(path)

    @router.post('/projects/{pid}/suggestions')
    def suggestions(pid: str):
        from web import studio_ai
        result = provider_call(studio_ai.suggest,store.public(store.read(pid)))
        with store.lock:
            project = store.read(pid)
            project['suggestions'] = result
            store.save(project)
        return result

    @router.post('/projects/{pid}/images')
    def images(pid: str,payload: ImageRequest):
        from web import studio_ai
        project = store.read(pid)
        if len(project['assets'])+payload.count>100:
            raise HTTPException(422,'소재 수가 한도에 도달했습니다. 새 프로젝트를 만들어 주세요.')
        temp=store.directory(pid)/('generation-'+ident())
        temp.mkdir()
        try:
            result=provider_call(studio_ai.generate_images,store.public(project),payload.prompt,payload.count,temp)
            assets=[]
            errors=list(result.get('errors',[]))
            for item in result.get('images',[]):
                try: assets.append(store.add(pid,Path(item['path']),item['name'],'ai',prompt=item.get('prompt')))
                except ValueError as exc: errors.append(str(exc))
            return {'assets':assets,'errors':errors}
        finally: shutil.rmtree(temp,ignore_errors=True)

    @router.post('/projects/{pid}/narration')
    def narration(pid: str,payload: NarrationRequest):
        from web import studio_ai
        project=store.read(pid)
        temp=store.directory(pid)/('narration-'+ident())
        temp.mkdir()
        try:
            item=provider_call(studio_ai.generate_narration,store.public(project),payload.voice,temp)
            try: asset=store.add(pid,Path(item['path']),item['name'],'ai')
            except ValueError as exc: raise HTTPException(422,str(exc)) from exc
            return {'assets':[asset]}
        finally: shutil.rmtree(temp,ignore_errors=True)

    @router.post('/projects/{pid}/references/search')
    def references(pid: str,payload: ReferenceSearch):
        from web import studio_ai
        return provider_call(studio_ai.search_references,store.public(store.read(pid)),payload.query,payload.kind)

    @router.post('/projects/{pid}/references/import')
    def import_reference(pid: str,payload: dict = Body(...)):
        from web import studio_ai
        store.read(pid)
        temp=store.directory(pid)/('reference-'+ident())
        temp.mkdir()
        try:
            item=provider_call(studio_ai.import_reference,payload,temp)
            try:
                asset=store.add(pid,Path(item['path']),item['name'],'reference',credit=str(payload.get('credit',''))[:500],
                                source_url=str(payload.get('source_url',''))[:2000],license=str(payload.get('license',''))[:500])
            except ValueError as exc: raise HTTPException(422,str(exc)) from exc
            return {'assets':[asset]}
        finally: shutil.rmtree(temp,ignore_errors=True)

    def validate_render(project):
        if not project['script'].strip(): raise HTTPException(422,'직접 쓴 대본을 먼저 입력해 주세요.')
        if not project['hook'].strip(): raise HTTPException(422,'첫 장면의 후킹 문구를 선택하거나 입력해 주세요.')
        if not project['scenes']: raise HTTPException(422,'대본을 장면으로 나누고 소재를 선택해 주세요.')
        total=sum(s['duration'] for s in project['scenes'])
        if total>180: raise HTTPException(422,'릴스 전체 길이는 180초 이내로 맞춰 주세요.')
        for scene in project['scenes']:
            _, asset=store.resolve(project,scene['asset_id'])
            if asset['kind'] not in ('image','video'): raise HTTPException(422,'장면에는 이미지 또는 영상을 선택해 주세요.')
            if asset['kind']=='video' and scene['trim_start']+scene['duration']>asset['duration']+.1:
                raise HTTPException(422,f'{asset["name"]}: 영상 길이 안에서 시작점과 장면 길이를 조정해 주세요.')
        if project['audio_asset_id']:
            _, asset=store.resolve(project,project['audio_asset_id'])
            if asset['kind']!='audio': raise HTTPException(422,'내레이션에는 음성 파일을 선택해 주세요.')
            if asset['duration']>total+.1: raise HTTPException(422,'음성이 장면보다 깁니다. 음성 길이에 맞춰 장면 길이를 늘려 주세요.')

    @router.post('/projects/{pid}/render')
    def start_render(pid: str):
        project=store.read(pid)
        validate_render(project)
        with render_lock:
            if pid in active: raise HTTPException(409,'이 프로젝트의 영상을 만들고 있습니다.')
            if len(active)>=3: raise HTTPException(429,'영상 제작 대기열이 가득 찼습니다. 잠시 후 다시 시도해 주세요.')
            active.add(pid)
        rid=ident()
        output=store.directory(pid)/'renders'/rid
        job={'id':rid,'status':'queued','progress':0,'stage':'영상 제작 대기 중'}
        def persist(): store.write_json(output/'status.json',job)
        persist()
        with store.lock:
            current = store.read(pid)
            current['_last_render_id'] = rid
            store.save(current)
        def worker():
            try:
                with work_limiter:
                    def progress(stage,pct):
                        job.update(status='running',stage=stage,progress=pct)
                        persist()
                    studio_render.render(project,lambda aid:store.resolve(project,aid),output,progress)
                    base=f'/api/studio/projects/{pid}/renders/{rid}'
                    job.update(status='done',progress=100,stage='릴스가 준비됐습니다.',outputs={
                        'video':base+'/reel.mp4','cover':base+'/cover.jpg','subtitles':base+'/subtitles.srt','project':base+'/project.json'})
            except Exception as exc:
                message=str(exc) if isinstance(exc,ValueError) else '영상을 만들지 못했습니다. 소재와 장면 길이를 확인한 뒤 다시 시도해 주세요.'
                job.update(status='error',stage='영상 제작 실패',error=message)
            finally:
                persist()
                with render_lock: active.discard(pid)
        threading.Thread(target=worker,daemon=True).start()
        return dict(job)

    def output_dir(pid,rid):
        store.read(pid)
        if not re.fullmatch('[a-f0-9]{32}',rid): raise HTTPException(404,'출력을 찾을 수 없습니다.')
        return store.directory(pid)/'renders'/rid

    @router.get('/projects/{pid}/renders/{rid}')
    def render_status(pid: str,rid: str):
        try: job=json.loads((output_dir(pid,rid)/'status.json').read_text())
        except (FileNotFoundError,json.JSONDecodeError): raise HTTPException(404,'출력을 찾을 수 없습니다.')
        if job['status'] in ('queued','running') and pid not in active:
            job.update(status='error',stage='서버가 재시작됐습니다.',error='다시 영상 만들기를 눌러 주세요. 대본과 소재는 보존됐습니다.')
        return job

    @router.get('/projects/{pid}/renders/{rid}/{filename}')
    def render_file(pid: str,rid: str,filename: str):
        if filename not in {'reel.mp4','cover.jpg','subtitles.srt','project.json'}:
            raise HTTPException(404,'파일을 찾을 수 없습니다.')
        path=output_dir(pid,rid)/filename
        if not path.is_file(): raise HTTPException(404,'아직 출력 파일이 없습니다.')
        return FileResponse(path,filename=filename,media_type='application/json' if filename=='project.json' else None)

    return router
