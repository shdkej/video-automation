import {useRef, useState} from 'react';
import {ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Check, Download, Film, ImagePlus, LoaderCircle, Music2, Plus, Save, Sparkles, Trash2, Upload, X} from 'lucide-react';
import type {Asset, Project, Reference, Scene} from './api';
import {referenceAsset, studioApi} from './api';
import {Preview} from './Preview';
import {KeywordResearch} from './KeywordResearch';
import {AiKeySettings} from './AiKeySettings';
import {ReferenceSearch} from './ReferenceSearch';
import {ReferenceMaterials} from './ReferenceMaterials';
import {useStudio} from './useStudio';
import './studio.css';

const steps = ['대본', '키워드·후킹', '소재', '조합·출력'];
const seconds = (value: number) => `${Number(value.toFixed(1))}초`;
const createScene = (text = '', asset_id = ''): Scene => ({id: crypto.randomUUID(), text, asset_id, duration: 4, trim_start: 0});
const scriptParts = (script: string) => {
  const paragraphs = script.split(/\n+/).map((text) => text.trim()).filter(Boolean);
  const parts = paragraphs.length > 1 ? paragraphs : script.split(/(?<=[.!?。！？])\s+/).map((text) => text.trim()).filter(Boolean);
  return parts.flatMap((text) => text.match(/[\s\S]{1,500}/gu) || []);
};
function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('ko-KR', {month: 'short', day: 'numeric'});
}

function AssetMedia({asset}: {asset: Asset}) {
  if (asset.kind === 'video') return <video src={asset.url} muted playsInline preload="metadata" />;
  if (asset.kind === 'audio') return <Music2 aria-hidden="true" size={28} />;
  return <img src={asset.url} alt={asset.name} loading="lazy" />;
}

export function Studio() {
  const state = useStudio();
  const {project, capabilities, busy, update, run, saveCurrent, appendAssets} = state;
  const [step, setStep] = useState(0);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [keywordQuery, setKeywordQuery] = useState('');
  const [materialTab, setMaterialTab] = useState<'library' | 'search' | 'generate'>('library');
  const [references, setReferences] = useState<Reference[]>([]);
  const [searched, setSearched] = useState(false);
  const [referenceSummary, setReferenceSummary] = useState('');
  const [referenceNotices, setReferenceNotices] = useState<string[]>([]);
  const [referenceUrl, setReferenceUrl] = useState('');
  const [referenceTitle, setReferenceTitle] = useState('');
  const [referenceNote, setReferenceNote] = useState('');
  const [prompt, setPrompt] = useState('');
  const [imageCount, setImageCount] = useState(3);
  const [activeSceneId, setActiveSceneId] = useState('');
  const [voice, setVoice] = useState('alloy');
  const [audioTab, setAudioTab] = useState<'upload' | 'ai'>('upload');
  const uploadInput = useRef<HTMLInputElement>(null);
  const audioInput = useRef<HTMLInputElement>(null);
  const referenceFileInput = useRef<HTMLInputElement>(null);
  const pendingReference = useRef<{reference: Reference; sceneId?: string} | null>(null);
  const activeIndex = Math.max(0, project.scenes.findIndex((scene) => scene.id === activeSceneId));
  const activeScene = project.scenes[activeIndex];
  const visualAssets = project.assets.filter((asset) => asset.kind !== 'audio');
  const audioAssets = project.assets.filter((asset) => asset.kind === 'audio');
  const selectedAudio = audioAssets.find((asset) => asset.id === project.audio_asset_id);
  const totalDuration = project.scenes.reduce((sum, scene) => sum + scene.duration, 0);
  const incompleteScenes = project.scenes.filter((scene) => !visualAssets.some((asset) => asset.id === scene.asset_id)).length;
  const invalidTrim = project.scenes.some((scene) => {const asset = visualAssets.find((item) => item.id === scene.asset_id); return asset?.kind === 'video' && !!asset.duration && scene.trim_start + scene.duration > asset.duration + 0.05;});
  const outputRunning = state.render?.status === 'queued' || state.render?.status === 'running';
  const audioTooLong = (selectedAudio?.duration || 0) > totalDuration + 0.05;
  const renderIssue = !project.scenes.length ? '장면을 추가해 주세요.' : incompleteScenes ? `소재가 없는 장면 ${incompleteScenes}개를 채워 주세요.` : invalidTrim ? '영상의 시작과 길이를 원본 길이 안으로 조정해 주세요.' : totalDuration > 180.001 ? '전체 길이를 180초 이내로 줄여 주세요.' : audioTooLong ? '음성이 모두 들어가도록 장면 길이를 늘려 주세요.' : '';
  const nextSceneNumber = project.scenes.findIndex((scene) => !scene.asset_id) >= 0 ? project.scenes.findIndex((scene) => !scene.asset_id) + 1 : project.scenes.length + 1;
  const clearWorkspaceTools = () => {
    setKeywordQuery(state.current.current.keyword_research?.seeds.join(', ') || state.current.current.topic || '');
    setStep(0); setActiveSceneId(''); setReferences([]); setSearched(false); setReferenceSummary(''); setReferenceNotices([]);
    setPrompt(''); setKeyword(''); setLibraryOpen(false); setMaterialTab('library');
    setReferenceUrl(''); setReferenceTitle(''); setReferenceNote('');
  };
  const chooseStep = (next: number) => { setStep(next); window.scrollTo({top: 0, behavior: 'smooth'}); };
  const upload = (files: File[], audioOnly = false) => {
    if (!files.length) return;
    void run('소재 업로드 중', async () => {
      if (files.some((file) => file.size > 200 * 1024 * 1024)) throw new Error('파일 한 개는 200MB까지 올릴 수 있어요.');
      if (files.reduce((sum, file) => sum + file.size, 0) > 500 * 1024 * 1024) throw new Error('한 번에 500MB까지 올릴 수 있어요. 나누어 올려 주세요.');
      const saved = await saveCurrent();
      const result = await studioApi.upload(saved.id, files);
      appendAssets(result.assets);
      if (audioOnly && result.assets.some((asset) => asset.kind === 'audio')) {
        state.replace({...state.current.current, audio_asset_id: result.assets.find((asset) => asset.kind === 'audio')!.id}, true);
        await saveCurrent();
      }
      state.setNotice(`${result.assets.length}개 파일을 올렸어요.`);
    });
  };
  const attachReference = (reference: Reference, sceneId?: string) => {
    pendingReference.current = {reference, sceneId};
    if (referenceFileInput.current) {
      referenceFileInput.current.accept = reference.kind === 'video' ? 'video/*,.mp4,.mov,.webm,.mkv' : 'image/*';
      referenceFileInput.current.click();
    }
  };
  const uploadReferenceFile = (file?: File) => {
    const pending = pendingReference.current;
    pendingReference.current = null;
    if (!file || !pending) return;
    void run('참고 자료에 파일 연결 중', async () => {
      if (file.size > 200 * 1024 * 1024) throw new Error('파일 한 개는 200MB까지 올릴 수 있어요.');
      const saved = await saveCurrent();
      const result = await studioApi.upload(saved.id, [file], pending.reference.id);
      appendAssets(result.assets);
      const asset = result.assets[0];
      if (pending.sceneId && asset) {
        const scenes = state.current.current.scenes.map((scene) => scene.id === pending.sceneId ? {...scene, asset_id: asset.id, trim_start: 0, duration: Math.min(scene.duration, asset.duration || scene.duration)} : scene);
        state.replace({...state.current.current, scenes}, true);
      }
      await saveCurrent();
      state.setNotice(pending.sceneId ? '영상 파일을 연결해 선택한 장면에 넣었어요.' : '파일을 연결했어요. 4번 조합·출력에서 장면 소재로 선택할 수 있어요.');
    });
  };
  const addKeyword = (value: string) => {
    const text = value.trim();
    if (!text) return;
    if (project.keywords.length >= 30 && !project.keywords.includes(text)) {state.setError('키워드는 30개까지 고를 수 있어요.'); return;}
    if (!project.keywords.includes(text)) update({keywords: [...project.keywords, text]});
    setKeyword('');
  };
  const chooseAsset = (asset: Asset) => {
    let scenes = [...project.scenes];
    if (!scenes.length) {
      const parts = scriptParts(project.script);
      if (parts.length > 30) {state.setError('대본을 30개 이하의 문단으로 정리하거나 조합 화면에서 장면을 직접 추가해 주세요.'); return;}
      scenes = parts.map((text) => createScene(text));
    }
    const free = scenes.findIndex((scene) => !scene.asset_id);
    if (free >= 0) {
      scenes[free] = {...scenes[free], asset_id: asset.id}; setActiveSceneId(scenes[free].id);
    } else if (scenes.length < 30) {
      const scene = createScene('', asset.id); scenes.push(scene); setActiveSceneId(scene.id);
    } else { state.setError('장면은 30개까지 만들 수 있어요. 조합 화면에서 기존 장면의 소재를 바꿔 주세요.'); return; }
    update({scenes});
    state.setNotice(`“${asset.name}”을 장면에 선택했어요.`);
  };
  const editScene = (id: string, values: Partial<Scene>) => update({scenes: project.scenes.map((scene) => scene.id === id ? {...scene, ...values} : scene)});
  const moveScene = (index: number, direction: number) => {
    const scenes = [...project.scenes];
    [scenes[index], scenes[index + direction]] = [scenes[index + direction], scenes[index]];
    update({scenes});
  };
  const fitAudio = () => {
    if (!selectedAudio?.duration || !project.scenes.length) return;
    const target = Math.ceil(selectedAudio.duration * 10) / 10;
    if (target > 180 || target > project.scenes.length * 30) { state.setError('음성 길이에 맞추려면 장면을 더 추가해 주세요. 전체 길이는 최대 180초예요.'); return; }
    let remaining = Math.max(target, project.scenes.length);
    let weight = totalDuration;
    const durations = project.scenes.map(() => 0);
    const pending = new Set(project.scenes.map((_, index) => index));
    for (let pass = 0; pass < project.scenes.length; pass++) {
      let changed = false;
      for (const index of [...pending]) {
        const scaled = project.scenes[index].duration / weight * remaining;
        if (scaled < 1 || scaled > 30) {
          durations[index] = Math.max(1, Math.min(30, scaled)); remaining -= durations[index];
          weight -= project.scenes[index].duration; pending.delete(index); changed = true;
        }
      }
      if (!changed) break;
    }
    for (const index of pending) durations[index] = project.scenes[index].duration / weight * remaining;
    const ticks = durations.map((value) => Math.max(10, Math.min(300, Math.floor(value * 10))));
    let leftover = Math.round(Math.max(target, project.scenes.length) * 10) - ticks.reduce((sum, value) => sum + value, 0);
    const order = durations.map((value, index) => ({index, fraction: value * 10 - Math.floor(value * 10)})).sort((a, b) => b.fraction - a.fraction);
    for (const {index} of order) {if (leftover > 0 && ticks[index] < 300) {ticks[index]++; leftover--;}}
    update({scenes: project.scenes.map((scene, index) => ({...scene, duration: ticks[index] / 10}))});
  };
  const saveReferenceLink = () => void run('참고 링크 저장 중', async () => {
    let url: URL;
    try {url = new URL(referenceUrl.trim());} catch {throw new Error('https://로 시작하는 참고 링크를 입력해 주세요.');}
    if (!['https:', 'http:'].includes(url.protocol)) throw new Error('http 또는 https 링크를 입력해 주세요.');
    if (project.references.length >= 50) throw new Error('참고 링크는 프로젝트마다 50개까지 보관할 수 있어요.');
    const saved = await saveCurrent();
    if (saved.references.some((item) => item.source_url === url.href)) throw new Error('이미 보관한 참고 링크예요.');
    const reference: Reference = {id: `manual-${crypto.randomUUID()}`, title: referenceTitle.trim() || url.hostname,
      kind: 'video', url: url.href, thumbnail: '', source_url: url.href, provider: url.hostname,
      credit: '', license: '링크 참고용', reason: referenceNote.trim(), importable: false};
    state.replace({...state.current.current, references: [...state.current.current.references, reference]}, true);
    await saveCurrent(); setReferenceUrl(''); setReferenceTitle(''); setReferenceNote('');
    state.setNotice('참고 링크를 보관했어요. 영상에 쓸 파일은 따로 올려 주세요.');
  });
  const importReference = (reference: Reference) => void run(reference.importable ? '참고 소재 가져오는 중' : '참고 링크 저장 중', async () => {
    const currentReferences = state.current.current.references;
    if (currentReferences.length >= 50 && !currentReferences.some((item) => item.id === reference.id)) {
      throw new Error('참고 자료는 프로젝트마다 50개까지 보관할 수 있어요. 내 소재에서 보관한 참고 링크를 제거한 뒤 다시 시도해 주세요.');
    }
    const saved = await saveCurrent();
    if (reference.importable) appendAssets((await studioApi.importReference(saved.id, reference)).assets);
    if (!state.current.current.references.some((item) => item.id === reference.id)) {
      state.replace({...state.current.current, references: [...state.current.current.references, reference]}, true);
      await saveCurrent();
    }
    state.setNotice(reference.importable ? '내 소재에 추가했어요. 사용할 장면을 선택해 주세요.' : '참고 링크를 프로젝트에 저장했어요.');
  });

  return <div className="studio-app">
    <input ref={referenceFileInput} className="studio-hidden" type="file" disabled={!!busy} aria-label="참고 자료에 연결할 파일" onChange={(event) => {uploadReferenceFile(event.target.files?.[0]); event.target.value = '';}} />
    <a href="#studio-main" className="studio-skip">작업 영역으로 이동</a>
    <header className="studio-header">
      <a className="studio-brand" href="/" aria-label="Reel Room 스튜디오"><span className="studio-brand-mark"><Film size={20} aria-hidden="true" /></span>REEL ROOM<span className="studio-brand-note">STUDIO</span></a>
      <div className="studio-header-actions">
        <AiKeySettings active={state.hasBrowserKey} disabled={!!busy} onChange={state.setApiKey} />
        <a href="?mode=cut" className="studio-text-button studio-cut-link">자동 컷 편집 <ArrowRight size={14} /></a>
        <button className="studio-text-button" onClick={() => setLibraryOpen(!libraryOpen)} aria-expanded={libraryOpen} aria-controls="studio-projects">내 프로젝트<span className="studio-counter">{state.projects.length}</span></button>
        <button className="studio-button studio-button-small" disabled={!!busy || state.loading} onClick={() => void state.save()}><Save size={15} /><span>저장</span></button>
      </div>
    </header>

    {libraryOpen && <section id="studio-projects" className="studio-projects" aria-label="저장한 프로젝트">
      <div className="studio-row"><h2>다시 이어서 만들기</h2><button className="studio-button" disabled={!!busy} onClick={async () => {await state.create(); if (!state.current.current.id) clearWorkspaceTools();}}><Plus size={16} />새 프로젝트</button></div>
      {state.loading ? <p className="studio-muted">프로젝트를 불러오고 있어요.</p> : state.projects.length ? <div className="studio-project-list">{state.projects.map((item) => <button key={item.id} disabled={!!busy} className={`studio-project-item ${project.id === item.id ? 'is-selected' : ''}`} onClick={async () => {await state.open(item.id); if (state.current.current.id === item.id) clearWorkspaceTools();}}><span>{item.title || '제목 없는 릴스'}</span><small>{dateLabel(item.updated_at)}</small>{project.id === item.id && <Check size={16} />}</button>)}</div> : <p className="studio-muted">첫 대본을 쓰고 저장하면 여기서 이어갈 수 있어요.</p>}
    </section>}

    <main id="studio-main" className="studio-main">
      <div className="studio-context"><span>{project.title || '새로운 릴스'}</span><span>{busy ? <><LoaderCircle className="studio-spin" size={13} />{busy}</> : state.dirty ? '저장 전' : project.id ? '저장됨' : '대본에서 시작하기'}</span></div>
      <nav className="studio-steps" aria-label="릴스 제작 단계">{steps.map((label, index) => <button key={label} onClick={() => chooseStep(index)} aria-current={step === index ? 'step' : undefined}><span className="studio-step-number">0{index + 1}</span>{label}</button>)}</nav>

      {state.error && <div className="studio-alert" role="alert"><span>{state.error}</span><button className="studio-icon-button" aria-label="오류 메시지 닫기" onClick={() => state.setError('')}><X size={17} /></button></div>}
      {state.notice && <div className="studio-notice" role="status"><Check size={16} /><span>{state.notice}</span><button className="studio-icon-button" aria-label="안내 닫기" onClick={() => state.setNotice('')}><X size={15} /></button></div>}
      {!capabilities && !state.loading && <div className="studio-connection"><span>AI 연결 상태를 확인하지 못했어요. 직접 입력하며 작업할 수 있어요.</span><button className="studio-text-button" disabled={!!busy} onClick={() => void state.reload()}>연결 다시 확인</button></div>}

      <fieldset className="studio-workspace" disabled={!!busy} aria-busy={!!busy}>
        {step === 0 && <section className="studio-script-layout">
          <div className="studio-script-main">
            <p className="studio-eyebrow">나의 문장으로 시작하기</p>
            <h1><span>어떤 이야기를</span><span>들려줄까요?</span></h1>
            <p className="studio-lead">직접 쓴 대본을 넣어 주세요.<br />키워드와 후킹을 고르고, 어울리는 장면을 붙여요.</p>
            <label className="studio-field studio-title-field"><span>프로젝트 이름</span><input value={project.title} maxLength={120} placeholder="예: 퇴근 후 처음 만든 나의 작업실" onChange={(event) => update({title: event.target.value})} /></label>
            <label className="studio-field studio-script-field"><span>내 대본 <small>추천을 받아도 원문은 그대로 남아요.</small></span><textarea rows={10} value={project.script} maxLength={12000} placeholder={'영상에서 하고 싶은 말을 써 주세요.\n\n이미 작성한 대본을 그대로 붙여 넣어도 좋아요.\n줄을 나누면 장면을 구성하기 편해요.'} onChange={(event) => update({script: event.target.value})} /></label>
            <div className="studio-field-footer"><span>문단마다 하나의 장면으로 나눌 수 있어요.</span><span>{project.script.length.toLocaleString()} / 12,000</span></div>
            <details className="studio-details"><summary>이야기의 방향 더하기 <span>선택</span></summary><div className="studio-planning-grid">
              <label className="studio-field"><span>주제</span><input value={project.topic} maxLength={500} placeholder="이번 영상에서 다룰 것" onChange={(event) => update({topic: event.target.value})} /></label>
              <label className="studio-field"><span>누구에게</span><input value={project.audience} maxLength={500} placeholder="이 이야기가 필요한 사람" onChange={(event) => update({audience: event.target.value})} /></label>
              <label className="studio-field"><span>보고 난 뒤의 행동</span><input value={project.goal} maxLength={500} placeholder="예: 첫 작업 기록을 남기기" onChange={(event) => update({goal: event.target.value})} /></label>
              <label className="studio-field"><span>언어</span><select value={project.language} onChange={(event) => update({language: event.target.value})}><option value="ko">한국어</option><option value="en">English</option>{!['ko', 'en'].includes(project.language) && <option value={project.language}>{project.language}</option>}</select></label>
            </div></details>
            <div className="studio-step-footer"><span>{project.script.trim() ? '대본이 준비됐으면 다음으로 넘어가세요.' : '아직 정리되지 않은 문장이어도 괜찮아요.'}</span><button className="studio-button studio-button-primary" disabled={!project.script.trim()} onClick={() => chooseStep(1)}>키워드·후킹 고르기 <ArrowRight size={17} /></button></div>
          </div>
          <aside className="studio-start-note"><div className="studio-note-line" /><p>내 이야기,<br />하나의 릴스로.</p><ol><li><span>01</span>대본은 내가 쓰고</li><li><span>02</span>다른 각도의 후킹을 찾고</li><li><span>03</span>사진과 영상으로 장면을 채워요</li></ol><span className="studio-note-meta">1080 × 1920 · 세로 영상</span></aside>
        </section>}

        {step === 1 && <section>
          <div className="studio-section-heading"><div><p className="studio-eyebrow">02 / 키워드·후킹</p><h1>첫 문장의 각도를 골라요.</h1><p className="studio-lead">실제 검색어를 고르고 첫 문장을 다듬어요. AI 표현 추천도 받을 수 있어요.</p></div><button className="studio-button studio-button-primary" disabled={!project.script.trim() || !capabilities?.ai_text} onClick={() => void run('키워드·후킹 추천 중', async () => {const saved = await saveCurrent(); state.setSuggestions(await studioApi.suggest(saved.id));})}><Sparkles size={17} />{state.suggestions ? 'AI 후보 다시 받기' : 'AI 표현 추천 받기'}</button></div>
          {!project.script.trim() ? <p className="studio-inline-help">대본 단계에서 원문을 먼저 넣으면 추천을 받을 수 있어요.</p> : !capabilities?.ai_text && <p className="studio-inline-help">AI 추천이 연결되지 않았어요. 아래에서 키워드와 후킹을 직접 입력할 수 있어요.</p>}
          <div className="studio-choice-layout"><div className="studio-choice-main">
            <KeywordResearch project={project} query={keywordQuery} setQuery={setKeywordQuery} enabled={!!capabilities?.keyword_search} onSearch={(query) => void run('네이버 키워드 조회 중', async () => {
              const saved = await saveCurrent();
              const research = await studioApi.keywords(saved.id, query);
              state.replace({...state.current.current, keyword_research: research});
              state.setNotice(`네이버에서 키워드 ${research.items.length}개를 가져왔어요.`);
            })} onPick={(text) => project.keywords.includes(text) ? update({keywords: project.keywords.filter((item) => item !== text)}) : addKeyword(text)} />
            <div className="studio-subheading"><h2>대본에서 확장한 AI 키워드</h2>{state.suggestions && <span>{state.suggestions.keywords.length}개 후보</span>}</div>
            {state.suggestions ? <div className="studio-keyword-candidates">{state.suggestions.keywords.map((item, index) => <button className={`studio-keyword-candidate ${project.keywords.includes(item.text) ? 'is-selected' : ''}`} key={`${item.text}-${index}`} aria-pressed={project.keywords.includes(item.text)} onClick={() => project.keywords.includes(item.text) ? update({keywords: project.keywords.filter((value) => value !== item.text)}) : addKeyword(item.text)}><span className="studio-candidate-angle">{item.angle}</span><strong>{item.text}{project.keywords.includes(item.text) && <Check size={16} />}</strong><span>{item.reason}</span>{item.evidence && <small>{item.evidence}</small>}</button>)}</div> : <div className="studio-empty-inline">대본을 바탕으로 서로 다른 각도의 후보를 받아보세요.<br />직접 떠오른 단어부터 골라도 좋아요.</div>}
            <div className="studio-subheading studio-spaced"><h2>처음 시선을 잡는 문장</h2></div>
            {state.suggestions ? <div className="studio-hook-candidates">{state.suggestions.hooks.map((item, index) => <button key={`${item.text}-${index}`} className={`studio-hook-candidate ${project.hook === item.text ? 'is-selected' : ''}`} aria-pressed={project.hook === item.text} onClick={() => update({hook: item.text.slice(0, 180)})}><span className="studio-candidate-angle">{item.type}{item.keyword ? ` · ${item.keyword}` : ''}</span><strong>{item.text}</strong><span>{item.reason}</span>{project.hook === item.text && <Check size={18} />}</button>)}</div> : <p className="studio-muted">AI 추천을 받으면 표현 방식이 다른 후킹 문장이 여기에 나와요.</p>}
            {state.suggestions?.notices?.map((item, index) => <p className="studio-inline-help" key={index}>{item}</p>)}
            {state.suggestions?.source && <p className="studio-metadata">추천 출처 · {state.suggestions.source}</p>}
          </div><aside className="studio-selection-panel"><p className="studio-eyebrow">내 선택</p><h2>이 방향으로 만들게요.</h2><label className="studio-field"><span>선택한 키워드</span><div className="studio-keyword-entry"><input value={keyword} placeholder="키워드 직접 추가" maxLength={100} onChange={(event) => setKeyword(event.target.value)} onKeyDown={(event) => {if (event.key === 'Enter' && !event.nativeEvent.isComposing) {event.preventDefault(); addKeyword(keyword);}}} /><button className="studio-icon-button" aria-label="키워드 추가" disabled={!keyword.trim()} onClick={() => addKeyword(keyword)}><Plus size={18} /></button></div></label><div className="studio-chips">{project.keywords.map((item) => <button key={item} className="studio-chip" onClick={() => update({keywords: project.keywords.filter((value) => value !== item)})} aria-label={`${item} 키워드 제거`}>{item}<X size={13} /></button>)}</div><label className="studio-field"><span>첫 장면 후킹 문장</span><textarea rows={4} value={project.hook} maxLength={180} placeholder="첫 장면에 보여줄 문장" onChange={(event) => update({hook: event.target.value})} /></label><p className="studio-metadata">원문과 별도로 저장해요. 첫 장면에 표시돼요.</p></aside></div>
          <div className="studio-step-footer"><button className="studio-text-button" onClick={() => chooseStep(0)}><ArrowLeft size={16} />대본</button><button className="studio-button studio-button-primary" onClick={() => chooseStep(2)}>소재 고르기 <ArrowRight size={17} /></button></div>
        </section>}

        {step === 2 && <section>
          <div className="studio-section-heading"><div><p className="studio-eyebrow">03 / 소재</p><h1>이야기에 어울리는 장면.</h1><p className="studio-lead">내 사진과 영상, 참고 자료, 생성한 이미지 중에서 골라요.</p></div><button className="studio-button studio-button-primary" onClick={() => uploadInput.current?.click()}><Upload size={17} />파일 올리기</button></div>
          <input ref={uploadInput} className="studio-hidden" type="file" accept="image/*,video/*,audio/*,.mkv,.mov,.m4a" multiple onChange={(event) => {upload(Array.from(event.target.files || [])); event.target.value = '';}} />
          <nav className="studio-material-tabs" aria-label="소재 찾는 방법">{([['library', '내 소재'], ['search', '참고 자료 찾기'], ['generate', 'AI 이미지 만들기']] as const).map(([value, label]) => <button key={value} aria-pressed={materialTab === value} onClick={() => setMaterialTab(value)}>{label}{value === 'library' && <span>{visualAssets.length}</span>}</button>)}</nav>
          {materialTab === 'library' && <>
            {visualAssets.length ? <div className="studio-asset-grid">{visualAssets.map((asset) => <article className="studio-asset" key={asset.id}><div className="studio-asset-image"><AssetMedia asset={asset} /><span className="studio-asset-type">{asset.source === 'ai' ? 'AI 이미지' : asset.kind === 'video' ? '영상' : asset.source === 'reference' ? '참고 소재' : '사진'}</span>{project.scenes.some((scene) => scene.asset_id === asset.id) && <span className="studio-asset-selected"><Check size={13} />사용 중</span>}</div><div className="studio-asset-body"><h3 title={asset.name}>{asset.name}</h3>{asset.credit && <p className="studio-metadata">{asset.credit}</p>}{asset.duration && <p className="studio-metadata">{seconds(asset.duration)}</p>}<button className="studio-button studio-button-full" onClick={() => chooseAsset(asset)}><Plus size={15} />장면 {nextSceneNumber}에 사용</button><details className="studio-asset-details"><summary>소재 정보</summary>{asset.prompt && <p>{asset.prompt}</p>}{asset.license && <p>{asset.license}</p>}<a href={asset.source_url || asset.url} target="_blank" rel="noreferrer">{asset.source_url ? '출처 보기' : '원본 보기'} <ArrowRight size={12} /></a></details></div></article>)}</div> : <button className="studio-upload-empty" onClick={() => uploadInput.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => {event.preventDefault(); upload(Array.from(event.dataTransfer.files));}}><ImagePlus size={34} strokeWidth={1.3} /><strong>첫 번째 소재를 올려 주세요.</strong><span>사진이나 영상을 끌어놓거나 눌러서 선택해요.</span><small>파일당 200MB · 한 번에 500MB까지</small></button>}
            <ReferenceMaterials references={project.references} assets={visualAssets} onAttach={(reference) => attachReference(reference)} onSelect={chooseAsset} />
            <details className="studio-details"><summary>YouTube·Instagram 참고 링크 보관</summary><div className="studio-reference-form"><label className="studio-field"><span>참고 URL</span><input type="url" value={referenceUrl} maxLength={2000} placeholder="https://www.youtube.com/…" onChange={(event) => setReferenceUrl(event.target.value)} /></label><label className="studio-field"><span>이름 <small>선택</small></span><input value={referenceTitle} maxLength={200} placeholder="다시 찾기 쉬운 이름" onChange={(event) => setReferenceTitle(event.target.value)} /></label><label className="studio-field"><span>참고할 점 <small>선택</small></span><textarea rows={2} value={referenceNote} maxLength={500} placeholder="예: 첫 장면의 구도와 자막 리듬" onChange={(event) => setReferenceNote(event.target.value)} /></label><div className="studio-row"><p className="studio-metadata">링크는 참고용으로 보관해요. 조합할 사진·영상 파일은 따로 올려 주세요.</p><button className="studio-button" disabled={!referenceUrl.trim()} onClick={saveReferenceLink}><Plus size={15} />링크 보관</button></div></div></details>
            {project.references.length > 0 && <details className="studio-details"><summary>보관한 참고 링크 <span>{project.references.length}</span></summary><div className="studio-reference-links">{project.references.map((item) => <div className="studio-reference-item" key={item.id}><a href={item.source_url} target="_blank" rel="noreferrer">{item.title}<span>{item.provider} ↗</span>{item.reason && <small>{item.reason}</small>}</a><button className="studio-icon-button" aria-label={`${item.title} 참고 링크 제거`} onClick={() => update({references: project.references.filter((reference) => reference.id !== item.id)})}><X size={15} /></button></div>)}</div></details>}
          </>}
          {materialTab === 'search' && <ReferenceSearch project={project} suggestions={state.suggestions} enabled={!!capabilities?.reference_search} youtube={!!capabilities?.youtube_search} busy={!!busy} items={references} resultSummary={referenceSummary} notices={referenceNotices} searched={searched} onImport={importReference} onSearch={(query, kind, provider) => void run('참고 자료 찾는 중', async () => {
            const saved = await saveCurrent();
            const result = await studioApi.references(saved.id, query, kind, provider);
            setReferences(result.items); setReferenceNotices(result.notices); setSearched(true); setReferenceSummary(`${query.trim()} · ${provider === 'youtube' ? 'YouTube' : provider === 'commons' ? 'Commons' : '모든 출처'}`);
          })} />}
          {materialTab === 'generate' && <div className="studio-generation"><div><label className="studio-field"><span>어떤 이미지가 필요하세요?</span><textarea rows={5} value={prompt} maxLength={3000} placeholder="장소, 피사체, 빛, 분위기를 적어 주세요. 예: 오후 햇빛이 드는 작은 작업실, 나무 책상과 펼친 노트. 글자 없이 세로 사진." onChange={(event) => setPrompt(event.target.value)} /></label>{!!state.suggestions?.image_prompts?.length && <details className="studio-details"><summary>대본을 바탕으로 추천한 이미지 방향</summary><div className="studio-prompt-options">{state.suggestions.image_prompts.map((item, index) => <button key={index} onClick={() => setPrompt(item)}>{item}<Plus size={16} /></button>)}</div></details>}
            <div className="studio-generation-controls"><label className="studio-field"><span>비교할 후보 수</span><select value={imageCount} onChange={(event) => setImageCount(Number(event.target.value))}>{[2, 3, 4, 5, 6].map((count) => <option key={count} value={count}>{count}장</option>)}</select></label><button className="studio-button studio-button-primary" disabled={!prompt.trim() || !capabilities?.ai_images} onClick={() => void run(`AI 이미지 ${imageCount}장 만드는 중`, async () => {const saved = await saveCurrent(); const result = await studioApi.images(saved.id, prompt.trim(), imageCount); appendAssets(result.assets); if (result.errors.length) state.setError(result.errors.join(' · ')); state.setNotice(`${result.assets.length}장의 이미지를 만들었어요. 장면에 쓸 후보를 골라 주세요.`); if (result.assets.length) setMaterialTab('library');})}><Sparkles size={16} />이미지 {imageCount}장 생성</button></div>
            <p className="studio-metadata">연결한 AI API의 사용량에 따라 비용이 발생해요. 만든 이미지는 내 소재에 남아요.</p>{!capabilities?.ai_images && <p className="studio-inline-help">AI 이미지 생성이 연결되지 않았어요. 내 소재에서 사진을 올려 사용할 수 있어요.</p>}
          </div><aside className="studio-generation-note"><span>생성 → 비교 → 선택</span><p>같은 방향의 후보를<br />나란히 보고 골라요.</p><small>선택한 이미지가 장면에 들어가요.<br />다시 생성해도 이전 후보는 남아요.</small></aside></div>}
          <div className="studio-step-footer"><button className="studio-text-button" onClick={() => chooseStep(1)}><ArrowLeft size={16} />키워드·후킹</button><button className="studio-button studio-button-primary" onClick={() => chooseStep(3)}>장면 조합하기 <ArrowRight size={17} /></button></div>
        </section>}

        {step === 3 && <section>
          <div className="studio-section-heading"><div><p className="studio-eyebrow">04 / 조합·출력</p><h1>장면을 이어, 나의 릴스로.</h1><p className="studio-lead">순서와 자막, 목소리를 다듬고 영상으로 꺼내세요.</p></div><span className="studio-total">{project.scenes.length}개 장면 <span>·</span> {seconds(totalDuration)}</span></div>
          <div className="studio-compose-layout"><div className="studio-compose-main">
            <ReferenceMaterials references={project.references} assets={visualAssets} onAttach={(reference) => attachReference(reference, activeScene?.id)} onSelect={(asset) => {
              if (activeScene) editScene(activeScene.id, {asset_id: asset.id, trim_start: 0, duration: Math.min(activeScene.duration, asset.duration || activeScene.duration)});
              else chooseAsset(asset);
            }} />
            <div className="studio-subheading"><h2>장면 순서</h2><button className="studio-text-button" disabled={project.scenes.length >= 30} onClick={() => {const scene = createScene(); update({scenes: [...project.scenes, scene]}); setActiveSceneId(scene.id);}}><Plus size={16} />장면 추가</button></div>
            {!project.scenes.length ? <div className="studio-scene-empty"><p>대본의 문단을 장면으로 나눠 볼까요?</p><span>원본 대본을 보존하면서 장면별 자막을 만들어요.</span><button className="studio-button" disabled={!project.script.trim()} onClick={() => {const parts = scriptParts(project.script); if (parts.length > 30) {state.setError('대본을 30개 이하의 문단으로 정리해 주세요. 한 장면의 자막은 500자까지예요.'); return;} const scenes = parts.map((text) => createScene(text)); update({scenes}); setActiveSceneId(scenes[0]?.id || '');}}>대본으로 장면 나누기 <ArrowRight size={16} /></button></div> : <div className="studio-scene-list">{project.scenes.map((scene, index) => {const asset = visualAssets.find((item) => item.id === scene.asset_id); return <article className={`studio-scene ${scene.id === activeScene?.id ? 'is-active' : ''}`} key={scene.id}><button className="studio-scene-pick" aria-label={`${index + 1}번 장면 미리보기`} aria-pressed={scene.id === activeScene?.id} onClick={() => setActiveSceneId(scene.id)}><span>{String(index + 1).padStart(2, '0')}</span><div>{asset ? <AssetMedia asset={asset} /> : <ImagePlus size={20} />}</div></button><div className="studio-scene-fields"><label className="studio-field"><span className="studio-sr-only">{index + 1}번 장면 자막</span><textarea rows={2} maxLength={500} value={scene.text} placeholder="이 장면에 들어갈 자막" onFocus={() => setActiveSceneId(scene.id)} onChange={(event) => editScene(scene.id, {text: event.target.value})} /></label><div className="studio-scene-settings"><label className="studio-scene-asset-select"><span className="studio-sr-only">{index + 1}번 장면 소재</span><select value={scene.asset_id} onChange={(event) => {const reference = project.references.find((item) => `reference:${item.id}` === event.target.value); if (reference) attachReference(reference, scene.id); else {const chosen = visualAssets.find((item) => item.id === event.target.value); editScene(scene.id, {asset_id: event.target.value, trim_start: 0, duration: Math.min(scene.duration, chosen?.duration || scene.duration)});} setActiveSceneId(scene.id);}}><option value="">소재 선택</option>{visualAssets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}{project.references.some((item) => !referenceAsset(visualAssets, item)) && <optgroup label="파일 연결이 필요한 참고 자료">{project.references.filter((item) => !referenceAsset(visualAssets, item)).map((item) => <option key={item.id} value={`reference:${item.id}`}>{item.title} · 파일 연결</option>)}</optgroup>}</select></label><label><span>길이</span><input type="number" min={1} max={30} step={0.1} value={scene.duration} onChange={(event) => editScene(scene.id, {duration: Math.max(1, Math.min(30, Number(event.target.value) || 1))})} /><span>초</span></label>{asset?.kind === 'video' && <label><span>시작</span><input type="number" min={0} max={asset.duration || undefined} step={0.1} value={scene.trim_start} onChange={(event) => editScene(scene.id, {trim_start: Math.max(0, Number(event.target.value) || 0)})} /><span>초</span></label>}</div>{asset?.kind === 'video' && asset.duration && scene.trim_start + scene.duration > asset.duration + 0.05 && <p className="studio-inline-help">원본 {seconds(asset.duration)} 안에서 시작과 길이를 조정해 주세요.</p>}</div><div className="studio-scene-actions"><button className="studio-icon-button" disabled={index === 0} aria-label={`${index + 1}번 장면 위로`} onClick={() => moveScene(index, -1)}><ArrowUp size={15} /></button><button className="studio-icon-button" disabled={index === project.scenes.length - 1} aria-label={`${index + 1}번 장면 아래로`} onClick={() => moveScene(index, 1)}><ArrowDown size={15} /></button><button className="studio-icon-button" aria-label={`${index + 1}번 장면 삭제`} onClick={() => update({scenes: project.scenes.filter((item) => item.id !== scene.id)})}><Trash2 size={15} /></button></div></article>;})}</div>}
            <section className="studio-control-section"><div className="studio-subheading"><h2>글자와 화면</h2></div><div className="studio-style-options">{([['photo', 'Photo Hook', '사진 위에 또렷하게'], ['shade', 'Soft Shade', '부드러운 음영 위에'], ['editorial', 'Editorial', '여백과 문장 중심']] as const).map(([value, title, description]) => <button key={value} className={project.style === value ? 'is-selected' : ''} aria-pressed={project.style === value} onClick={() => update({style: value})}><strong>{title}</strong><span>{description}</span></button>)}</div><div className="studio-planning-grid"><label className="studio-field"><span>글꼴</span><select value={project.font} onChange={(event) => update({font: event.target.value as Project['font']})}><option value="pretendard">Pretendard</option><option value="blackhansans">검은고딕</option><option value="dohyeon">도현</option></select></label><label className="studio-field"><span>후킹 위치</span><select value={project.position} onChange={(event) => update({position: event.target.value as Project['position']})}><option value="top">위</option><option value="center">가운데</option><option value="bottom">아래</option></select></label></div><label className="studio-field"><span>첫 장면 후킹</span><textarea rows={2} maxLength={180} value={project.hook} onChange={(event) => update({hook: event.target.value})} /></label><label className="studio-field"><span>마지막 장면 한마디 <small>선택</small></span><input value={project.cta} maxLength={180} placeholder="예: 내일의 기록도 함께해 주세요." onChange={(event) => update({cta: event.target.value})} /></label></section>
            <section className="studio-control-section"><div className="studio-subheading"><h2>목소리</h2><span>선택</span></div><p className="studio-inline-help">원본 영상 소리는 제외돼요. 선택한 음성만 영상에 들어가요.</p><label className="studio-field"><span>영상에 넣을 음성</span><select value={project.audio_asset_id || ''} onChange={(event) => update({audio_asset_id: event.target.value || null})}><option value="">추가 음성 없음</option>{audioAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.source === 'ai' ? 'AI 음성 · ' : ''}{asset.name}{asset.duration ? ` · ${seconds(asset.duration)}` : ''}</option>)}</select></label>{selectedAudio && <div className="studio-audio-preview"><audio src={selectedAudio.url} controls preload="metadata" /><div><span>{selectedAudio.source === 'ai' ? 'AI 음성' : '업로드 음성'}{selectedAudio.duration ? ` · ${seconds(selectedAudio.duration)}` : ''}</span>{selectedAudio.duration && <button className="studio-text-button" disabled={!project.scenes.length} onClick={fitAudio}>음성 길이에 맞추기</button>}</div>{audioTooLong && <p className="studio-inline-help">음성이 장면보다 {seconds((selectedAudio.duration || 0) - totalDuration)} 길어요.</p>}</div>}<div className="studio-audio-tabs"><button aria-pressed={audioTab === 'upload'} onClick={() => setAudioTab('upload')}>직접 녹음한 음성</button><button aria-pressed={audioTab === 'ai'} onClick={() => setAudioTab('ai')}>AI 음성</button></div>
            <input ref={audioInput} className="studio-hidden" type="file" accept="audio/*,.m4a,.mp3,.wav,.ogg" onChange={(event) => {upload(Array.from(event.target.files || []), true); event.target.value = '';}} />
            {audioTab === 'upload' ? <div className="studio-audio-action"><span>녹음한 대본을 영상에 함께 넣어요.</span><button className="studio-button" onClick={() => audioInput.current?.click()}><Upload size={15} />음성 올리기</button></div> : <><div className="studio-audio-action"><label className="studio-field"><span>목소리</span><select value={voice} onChange={(event) => setVoice(event.target.value)}><option value="alloy">Alloy</option><option value="nova">Nova</option><option value="onyx">Onyx</option></select></label><button className="studio-button" disabled={!project.script.trim() || !capabilities?.ai_narration} onClick={() => void run('AI 음성 만드는 중', async () => {const saved = await saveCurrent(); const result = await studioApi.narration(saved.id, voice); appendAssets(result.assets); const audio = result.assets.find((asset) => asset.kind === 'audio'); if (audio) {state.replace({...state.current.current, audio_asset_id: audio.id}, true); await saveCurrent();} state.setNotice('AI 음성을 만들었어요. 재생해서 확인하고 장면 길이를 맞춰 주세요.');})}><Sparkles size={15} />대본으로 AI 음성 생성</button></div><p className="studio-metadata">직접 쓴 대본을 그대로 읽어요. AI API 사용 비용이 발생해요.</p>{!capabilities?.ai_narration && <p className="studio-inline-help">AI 음성이 연결되지 않았어요. 직접 녹음한 파일을 올릴 수 있어요.</p>}</>}
            </section>
            <details className="studio-details studio-caption-details"><summary>게시글 캡션 <span>선택</span></summary><label className="studio-field"><span className="studio-sr-only">게시글 캡션</span><textarea rows={5} value={project.caption} maxLength={2200} placeholder="릴스와 함께 게시할 글" onChange={(event) => update({caption: event.target.value})} /></label>{state.suggestions?.caption && <button className="studio-text-button" onClick={() => update({caption: state.suggestions!.caption})}>추천 캡션 가져오기</button>}<p className="studio-metadata">영상에 들어가는 자막과 별도로 프로젝트에 저장해요.</p></details>
          </div><aside className="studio-preview-panel"><div className="studio-preview-sticky"><div className="studio-subheading"><h2>{state.render?.status === 'done' && !state.staleOutput ? '출력한 영상' : '장면 미리보기'}</h2><span>{state.render?.status === 'done' && !state.staleOutput ? `전체 ${seconds(totalDuration)}` : activeScene ? `${activeIndex + 1} / ${project.scenes.length}` : '9 : 16'}</span></div>{state.render?.status === 'done' && state.render.outputs && !state.staleOutput ? <video className="studio-output-video" src={state.render.outputs.video} poster={state.render.outputs.cover} controls playsInline /> : <Preview project={project} scene={activeScene} index={activeIndex} />}
            {!(state.render?.status === 'done' && !state.staleOutput) && <p className="studio-preview-note">배치 확인용 미리보기예요. 최종 줄바꿈과 재생은 출력한 영상에서 확인해 주세요.</p>}
            {project.scenes.length > 1 && !(state.render?.status === 'done' && !state.staleOutput) && <div className="studio-preview-nav"><button className="studio-icon-button" disabled={activeIndex === 0} aria-label="이전 장면" onClick={() => setActiveSceneId(project.scenes[activeIndex - 1].id)}><ArrowLeft size={17} /></button><span>장면 {activeIndex + 1}{activeScene ? ` · ${seconds(activeScene.duration)}` : ''}</span><button className="studio-icon-button" disabled={activeIndex === project.scenes.length - 1} aria-label="다음 장면" onClick={() => setActiveSceneId(project.scenes[activeIndex + 1].id)}><ArrowRight size={17} /></button></div>}
            <div className="studio-output-actions">{renderIssue && <p className="studio-inline-help">{renderIssue}</p>}<button className="studio-button studio-button-primary studio-button-full" disabled={!!renderIssue || outputRunning} onClick={() => void state.renderVideo()}>{outputRunning ? <><LoaderCircle className="studio-spin" size={17} />영상 만드는 중</> : <><Film size={17} />{state.render?.status === 'done' ? '수정한 영상 출력' : '영상 출력하기'}</>}</button><p className="studio-metadata">MP4 영상 · JPG 커버 · SRT 자막</p></div>
            {outputRunning && <div className="studio-render-progress" role="status"><div><span>{state.render?.stage || '출력 준비 중'}</span><span>{Math.round(state.render?.progress || 0)}%</span></div><progress max={100} value={state.render?.progress || 0} /></div>}
            {state.pollError && <div className="studio-inline-error" role="alert"><p>{state.pollError}</p><button className="studio-text-button" onClick={state.retryPoll}>출력 상태 다시 확인</button></div>}
            {state.render?.status === 'error' && <p className="studio-inline-error" role="alert">{state.render.error || '영상을 만들지 못했어요. 장면 설정을 확인한 뒤 다시 출력해 주세요.'}</p>}
            {state.render?.status === 'done' && state.render.outputs && <div className="studio-downloads"><p>{state.staleOutput ? '이후 수정한 내용은 다시 출력해야 반영돼요.' : '파일을 내려받아 게시할 수 있어요.'}</p>{([['video', 'MP4 영상'], ['cover', 'JPG 커버'], ['subtitles', 'SRT 자막'], ['project', '프로젝트 JSON']] as const).map(([key, label]) => <a key={key} className="studio-download" href={state.render!.outputs![key]} download><span>{label}</span><Download size={15} /></a>)}</div>}
          </div></aside></div>
          <div className="studio-step-footer"><button className="studio-text-button" onClick={() => chooseStep(2)}><ArrowLeft size={16} />소재</button><span>이 프로젝트는 저장하고 다시 열 수 있어요.</span></div>
        </section>}
      </fieldset>
    </main>
    <footer className="studio-footer"><span>REEL ROOM</span><span>나의 문장에서, 나의 영상으로.</span><a href="?mode=cut">자동 컷 편집 ↗</a></footer>
    {!!busy && <div className="studio-busy" role="status"><LoaderCircle className="studio-spin" size={16} />{busy}<span>잠시만 기다려 주세요.</span></div>}
  </div>;
}
