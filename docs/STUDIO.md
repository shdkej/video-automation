# Reel Room 통합 스튜디오 — 구현 계약 (2026-11-13)

사람이 쓴 대본을 보존하며 키워드·후킹·참고 자료·여러 AI 이미지 중 사용자가 선택해 세로 릴스를 만든다. Instagram Maker의 Photo Hook / Soft Shade / Editorial 타이틀 합성은 같은 프로젝트의 스타일이 된다. 기존 Reel Room 자동 컷 편집은 `?mode=cut`로 유지한다. 기본 진입은 새 스튜디오다.

## 구현 결정

기존 React/Vite + FastAPI + Pillow/ffmpeg를 재사용한다. 별도 Sites 앱이나 중복 편집 엔진을 만들지 않는다. 사용자 요청이 구현 승인 범위다. warm white/lemon, Pretendard, 단계형 텍스트 내비게이션으로 대본부터 시작한다. 첨부 사진의 UI는 사용하지 않는다. 최종 결과는 1080×1920 MP4, 커버 JPG, SRT, 프로젝트 JSON이다. 사용자 확정: 직접 녹음 업로드 + 대본 자막과 AI 내레이션을 모두 지원한다. 내레이션이 장면보다 길면 렌더를 막고 장면 길이를 조절한다. 자막 타이밍은 장면 길이로 편집하며 자동 음성 인식 정렬은 포함하지 않는다. 선택한 음성만 사용하고 원본 영상의 소리는 제외한다.

## API 계약

모든 API는 `/api/studio` 아래. 오류는 `{detail:string}`. JSON 저장은 원자적이며 로컬 데이터는 Git 제외. base 디렉터리는 `VIDAUTO_STUDIO_DIR`, 기본 `web/studio-data`다. 기존 영상 처리 세마포어를 공유한다.

- `GET /capabilities` → `{ai_text:boolean,ai_images:boolean,reference_search:boolean,ai_narration:boolean,keyword_search:boolean,youtube_search:boolean}`.
- `GET /projects` → `{projects:Project[]}` (최신순).
- `POST /projects` body `{title?:string}` → Project.
- `GET /projects/{id}` → Project.
- `PUT /projects/{id}` body Project editable fields → Project. 서버 소유 assets/id/timestamps는 덮어쓰지 않는다.
- `POST /projects/{id}/assets` multipart `files` (여러 image/video/audio) → `{assets:Asset[]}` 새 자산만. 업로드는 200MB/파일, 500MB/요청, 프로젝트 100개 제한. ffprobe/PIL 검증.
- `GET /projects/{id}/assets/{asset_id}` → 미디어 inline.
- `POST /projects/{id}/keywords/search` body `{query:string}` → `{provider,seeds,queried_at,total_found,items,notices}`. 쉼표/줄바꿈으로 1~5개, 중복 제거·공백 정규화, 입력어 우선 및 검색량순 최대 45개. 서버 소유 `keyword_research`에 저장하고 PUT으로 위조/삭제하지 않음. 항목 `{keyword,is_seed,pc,mobile,monthly_total:number|null,monthly_label,competition}`. 비공개/누락 수치는 범위/미확인으로 보존.
- `POST /projects/{id}/suggestions` body `{}` → Suggestions. 저장한 script/topic/audience/goal/language 사용. 원문 변형 금지.
- `POST /projects/{id}/images` body `{prompt:string,count:2|3|4|5|6}` → `{assets:Asset[],errors:string[]}`. 후보는 저장, 선택은 사용자. 서버 모델 설정, 비밀은 서버에만.
- `POST /projects/{id}/narration` body `{voice:"alloy"|"nova"|"onyx"|"shimmer"|"echo"|"fable"}` → `{assets:Asset[]}`. 원문 그대로 읽는 AI 음성, 4000자 제한.
- `POST /projects/{id}/references/search` body `{query:string,kind:'all'|'image'|'video',provider?:'all'|'commons'|'youtube'}` → `{items:Reference[],notices:string[]}`. 실제 제공자의 검색 결과만 출력, 생성된 링크 금지.
- `POST /projects/{id}/references/import` body Reference → `{assets:Asset[]}`. 허용 공급자 미디어만 제한 다운로드. YouTube는 참고 링크만 저장하고 직접 다운로드하지 않음.
- `POST /projects/{id}/render` body `{}` → RenderJob.
- `GET /projects/{id}/renders/{render_id}` → RenderJob.
- `GET /projects/{id}/renders/{render_id}/{filename}` → 파일. 출력 경로는 서버 결정.

```ts
type Asset = {id:string;name:string;kind:'image'|'video'|'audio';url:string;source:'upload'|'ai'|'reference';credit?:string;source_url?:string;license?:string;prompt?:string};
type Reference = {id:string;title:string;kind:'image'|'video';url:string;thumbnail:string;source_url:string;provider:string;credit:string;license:string;reason:string;importable:boolean};
type Suggestions = {keywords:{text:string;angle:string;reason:string;evidence:string}[];hooks:{text:string;type:string;keyword:string;reason:string}[];search_queries:string[];image_prompts:string[];caption:string;source:string;notices:string[]};
type Scene = {id:string;text:string;asset_id:string;duration:number;trim_start:number};
type Project = {id:string;title:string;script:string;topic:string;audience:string;goal:string;language:string;keywords:string[];hook:string;caption:string;cta:string;style:'photo'|'shade'|'editorial';font:'pretendard'|'blackhansans'|'dohyeon';position:'top'|'center'|'bottom';scenes:Scene[];audio_asset_id:string|null;assets:Asset[];references:Reference[];created_at:string;updated_at:string;suggestions?:Suggestions;last_render?:RenderJob|null};
type RenderJob = {id:string;status:'queued'|'running'|'done'|'error';progress:number;stage:string;error?:string;outputs?:{video:string;cover:string;subtitles:string;project:string}};
```

## Provider 모듈 계약 (`web/studio_ai.py`)

- `capabilities() -> dict`
- `suggest(project:dict) -> dict` Suggestions. OpenAI 기존 SDK 사용. API 미설정 시 명확한 ValueError, 가짜 AI 결과 금지. 최소 10 키워드/5각도, 5개 서로 다른 후킹 유형. keyword-title/inflow·tone 준수, 검색량 미확인, 숫자/경험 날조 금지.
- `generate_images(project:dict,prompt:str,count:int,output_dir:Path) -> dict` `{images:[{path:str,name:str,prompt:str}],errors:[str]}`. 기존 SDK의 b64 이미지로 각각 별도 PNG 저장, 실제 API 호출만. 참조 업로드 이미지는 향후 선택적 편집 입력; 기본 생성은 대본/선택 키워드/프롬프트 기준.
- `search_references(project:dict,query:str,kind:str) -> dict` `{items:Reference[],notices:[]}`. Commons 무료 검색 + 가능한 기존 provider. AI 검색어는 suggest의 search_queries 제공. 유효 출처·권리정보·이미지/영상 추천.
- `generate_narration(project:dict,voice:str,output_dir:Path) -> dict` `{path:str,name:str,kind:"audio"}`. 사용자 대본 원문을 읽고 저장한다.
- `import_reference(reference:dict,output_dir:Path) -> dict` `{path:str,name:str,kind:str}`. allowlist, redirect 검증, 사이즈·타임아웃, 미디어 검증은 호출자가 추가 실행.

## 작업 계획과 검증

1. **프로젝트/합성 (root)**: 저장·업로드·유효성·원문 보존 테스트를 먼저 실패 확인. JSON 저장과 라우터, Pillow 타이틀/자막과 ffmpeg 합성 구현. 실제 이미지+클립으로 MP4/SRT/JPG 검증. 사용자 변경이 있는 remotion-map 파일은 만지지 않음.
2. **UI (Developer)**: src/studio/ 전용 파일과 App.tsx만 소유. 저장/재열기, 대본·추천·소재·조합·출력, 후보 선택/재생성/변경, 반응형. 서버 호출은 studio/api.ts로 모음. 타입 체크·Vite build.
3. **AI/추천 (Operator)**: studio_ai.py와 해당 테스트만 소유. provider JSON 검증·누락키·실패·부분생성·검색 실패·불허 URL 검증. 외부 API 계약은 공식 자료 확인. 유료 API 실제 실행은 하지 않음.
4. **Planner/Marketer 검토**: 대본 보존, 후보 각도·후킹 기준, 선택·조합 결과 연결을 독립 확인. 리뷰는 구현 범위를 늘리지 않음.
5. **통합**: pytest, build, 실제 브라우저 375/768/1440px, 업로드→선택→MP4→재열기. README·운영 중앙 문서 갱신. 의도 파일만 기본 브랜치 commit/push, SHA 확인. 운영 배포는 별도 경계로 남김.

## 검토 초점

동시 저장과 업로드의 자산 손실, 긴 한글/줄바꿈 자막, 손상 파일·경로 탈출·외부 URL, AI 부분실패/중복 클릭, 재시작과 기존 프로젝트 복구. 원본 대본·사용자가 선택한 후킹은 추천 호출이 덮어쓰지 않는다. 실제 검색 데이터를 받지 않았다면 검색량/인기도를 만들지 않는다.

## 진행 증거

- Context Pack: product route `/tmp/reel-studio-context.json` (2026-11-13). content route는 삭제된 `insights/title-selection-pressure.mdx`를 가리켜 실패했으며, product 필수 문서와 실제 keyword-title 정본을 사용했다.
- Planner/Developer/Marketer/Operator 검토를 수행했다. Planner 지적에 따라 음성 길이 검증, 원문/선택값 보존, 추천과 마지막 출력의 서버 복구를 추가했다.
- 운영 반영은 아직 수행하지 않았다. 코드 push는 이미지 빌드까지만 실행되며, Space 배포 변경과 운영 재시작은 별도 승인 범위다.
- 최종 Python 테스트: 186 passed, 49 warnings. UI TypeScript/Vite 빌드와 대상 oxlint, diff 검증 통과. 업로드 일괄 실패 복구, 영상 스트림 길이, 추천의 미확인 수치, 이모지 경계, 참고 자료 50개 한도에서 저장 유지도 검증했다.
- 실제 브라우저: 직접 작성 대본 → 이미지 2개 → 녹음 6초 → 장면 2개 → 실제 1080×1920 H.264/AAC MP4 → 저장/재열기. 375/768/1440px에서 가로 넘침·페이지 오류 없음. 테스트 소재·음성은 합성 fixture이며 실제 사용자 또는 AI 생성물로 표시하지 않았다.
- 실제 무료 Commons 검색으로 이미지·영상 결과 10개를 확인했다. 유료 이미지·추천·AI 내레이션은 공급자 모의 응답으로 검증했으며 실제 호출·품질 검증은 아직 하지 않았다. 실행 증거 정본은 Infinity `reel-room-studio-20261113`이다.

## 운영 검증에서 확인한 수정 (2026-11-13)

운영 계정의 모델 목록과 [OpenAI Image API](https://developers.openai.com/api/reference/resources/images/methods/generate)를 대조해 기존 기본값 gpt-image-1이 2026-10-23 종료된 것을 확인했다. 기본값을 지원 중인 gpt-image-1.5로 수정했다. 1024×1536·low·복수 후보 계약은 동일하다. 기본 모델 회귀 검증 RED→GREEN을 확인한다. 운영 manifest에도 VIDAUTO_STUDIO_IMAGE_MODEL을 명시해 배포 이미지의 기본값에 의존하지 않는다.

운영 Secret의 인증과 모델 목록 조회는 통과했지만 텍스트·이미지·음성 생성은 상류 429로 실패했다. 이 계정 한도/빈도 문제는 모델 기본값 수정과 구분하며 성공으로 기록하지 않는다.

첫 운영 이미지의 무료 참고 검색 검증에서 `ModuleNotFoundError: httpx`를 확인했다. 로컬에는 httpx 0.28.1/OpenAI 2.44.0이 있었지만 최신 OpenAI 3.50.0은 httpx를 자동 설치하지 않았다. 직접 의존성을 requirements-web.txt에 명시하고 OpenAI SDK를 검증한 2.44.0으로 고정했다. Docker 빌드에서 Studio 세 모듈을 실제 import해 의존성 누락이 운영까지 넘어가지 않도록 했다. 이후 유료 검증은 사용자 요청으로 보류하며 무료 검색·수동 제작·영구 저장을 계속 검증한다.

## 2026-11-13 후속: 검색 API와 브라우저 입력 키

사용자는 AI 없이 keyword-title 스킬 API 직접 조회, 쉽게 찾는 참고영상/이미지, 화면의 AI 키 입력을 요청했다. Naver SearchAd는 `web/studio_keywords.py`의 독립 모듈·세마포어·capability로 실행하고 LLM 대체값을 만들지 않는다. YouTube는 기존 GOG_YOUTUBE_API_KEY를 별칭으로 지원하며 출처 선택 시 해당 API만 호출한다. 검색 chips·이미지/영상 분리·출처 필터·모달 미리보기·원본/외부 검색·보관/가져오기를 제공한다. YouTube 임베드는 업로더나 브라우저 정책에 따라 재생이 제한될 수 있어 원본 링크를 함께 제공한다.

AI POST 3종(suggestions/images/narration)은 선택적 `X-OpenAI-API-Key` 헤더를 받는다. 현재 탭 메모리에서만 유지하고 요청마다 SDK에 직접 전달한다. 서버 환경·프로젝트 파일을 수정하지 않으며 다른 사용자/요청으로 전달하지 않는다. 브라우저 키가 없으면 서버 키를 사용한다. 서버와 브라우저 모두 키 형식을 검증하되 실제 인증/한도 확인은 생성 요청에서 수행한다. 키가 있는 capability는 연결 가능 여부이며 생성 성공 보장이 아니다. 생성 API 실호출 검증은 사용자 지시로 계속 보류한다.

검증: 전체 Python 212개 통과(기존 경고 49개), 실제 Naver SearchAd(세계여행/신혼여행 후보 872개 중 45개), YouTube 한국어 검색 6개, Commons 이미지 8개 반환. 375/768/1440px 브라우저에서 첫 검색어·선택 저장/복원, 브라우저 키의 AI 요청 헤더 한정 전달·저장소 미보관·새로고침 삭제를 확인했다. AI 키 전달은 모의 SDK/브라우저 가로채기로 검증했고 유료 호출은 0회다. 배포 결과는 Space와 KL 운영 문서를 따른다.
