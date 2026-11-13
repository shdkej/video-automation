import {useEffect, useRef, useState} from 'react';
import {Check, Film, Plus, Search, X} from 'lucide-react';
import type {Project, Reference, Suggestions} from './api';

type Kind = 'all' | 'image' | 'video';
type Provider = 'all' | 'commons' | 'youtube';

export function ReferenceSearch({project, suggestions, enabled, youtube, busy, items, resultSummary, notices, searched, onSearch, onImport}: {
  project: Project; suggestions: Suggestions | null; enabled: boolean; youtube: boolean; busy: boolean;
  items: Reference[]; resultSummary: string; notices: string[]; searched: boolean;
  onSearch: (query: string, kind: Kind, provider: Provider) => void; onImport: (item: Reference) => void;
}) {
  const [query, setQuery] = useState(project.keywords[0] || project.topic || '');
  const [kind, setKind] = useState<Kind>('image');
  const [provider, setProvider] = useState<Provider>('commons');
  const [preview, setPreview] = useState<Reference | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const chips = [...new Set([...project.keywords, project.topic, ...(suggestions?.search_queries || [])].filter(Boolean))].slice(0, 12);
  useEffect(() => {if (preview) dialog.current?.showModal(); else dialog.current?.close();}, [preview]);
  const search = (value = query, nextKind = kind, nextProvider = provider) => {if (value.trim()) {setQuery(value.slice(0, 200)); onSearch(value.trim().slice(0, 200), nextKind, nextProvider);}};
  const selectKind = (next: Kind) => {setKind(next); setProvider(next === 'video' && youtube ? 'youtube' : 'commons');};
  const encoded = encodeURIComponent(query.trim());
  const youtubeId = preview?.provider === 'YouTube' ? preview.id.match(/^youtube-([\w-]{11})$/)?.[1] : null;
  return <div>
    <div className="studio-reference-kind" aria-label="찾을 자료"><button aria-pressed={kind === 'image'} onClick={() => selectKind('image')}>이미지 찾기</button><button aria-pressed={kind === 'video'} onClick={() => selectKind('video')}>참고영상 찾기</button><button aria-pressed={kind === 'all'} onClick={() => selectKind('all')}>사진과 영상</button></div>
    <form className="studio-search-form" onSubmit={(event) => {event.preventDefault(); search();}}>
      <label className="studio-field"><span>찾고 싶은 장면</span><input value={query} maxLength={200} placeholder={kind === 'video' ? '예: 다합 여행 브이로그' : '예: Dahab beach, 바다, 작업실'} onChange={(event) => setQuery(event.target.value)} /></label>
      <label className="studio-field studio-kind-select"><span>검색 출처</span><select value={provider} onChange={(event) => setProvider(event.target.value as Provider)}><option value="commons">Commons · 가져올 소재</option>{kind !== 'image' && <><option value="youtube" disabled={!youtube}>YouTube · 참고영상{!youtube ? ' (미연결)' : ''}</option><option value="all">모든 출처</option></>}</select></label>
      <button className="studio-button studio-button-primary" disabled={!query.trim() || !enabled} type="submit"><Search size={16} />찾기</button>
    </form>
    <p className="studio-metadata">YouTube는 구성·연출을 참고하고, Commons 소재는 이용 조건을 확인해 가져올 수 있어요. Commons는 짧은 영어 검색어도 써 보세요.</p>
    {chips.length > 0 && <div className="studio-suggestions-inline"><span>내 키워드로 바로 찾기</span>{chips.map((chip) => <button key={chip} disabled={!enabled} onClick={() => search(chip)}>{chip}</button>)}</div>}
    {query.trim() && <div className="studio-external-search"><span>더 찾아보기</span><a href={`https://www.youtube.com/results?search_query=${encoded}`} target="_blank" rel="noreferrer">YouTube ↗</a><a href={`https://www.google.com/search?tbm=isch&q=${encoded}`} target="_blank" rel="noreferrer">이미지 검색 ↗</a><a href={`https://www.pexels.com/search/${encoded}/`} target="_blank" rel="noreferrer">Pexels ↗</a></div>}
    {!enabled && <p className="studio-inline-help">참고 검색 연결을 확인해 주세요. 외부 검색에서 찾은 자료의 링크도 내 소재에 보관할 수 있어요.</p>}
    {notices.map((notice) => <p className="studio-inline-help" key={notice}>{notice}</p>)}
    {items.length ? <><p className="studio-reference-count">{resultSummary} · {items.length}개 · 눌러서 미리 보고 골라요.</p><div className="studio-asset-grid">{items.map((item) => <article className="studio-asset" key={item.id}>
      <button className="studio-reference-image studio-preview-trigger" aria-label={`${item.title} 미리보기`} onClick={() => setPreview(item)}>{item.thumbnail ? <img src={item.thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <Film size={28} />}<span>{item.kind === 'video' ? '▶ 영상' : '이미지'} · {item.provider}</span></button>
      <div className="studio-asset-body"><h3>{item.title}</h3><p className="studio-metadata">{item.credit} · {item.license}</p><a className="studio-text-button" href={item.source_url} target="_blank" rel="noreferrer">원본 보기 ↗</a><button className="studio-button studio-button-full" disabled={project.references.some((reference) => reference.id === item.id)} onClick={() => onImport(item)}>{project.references.some((reference) => reference.id === item.id) ? <><Check size={15} />보관됨</> : item.importable ? <><Plus size={15} />내 소재로 가져오기</> : '참고 링크 보관'}</button></div>
    </article>)}</div></> : <div className="studio-empty-inline">{searched ? '결과가 없어요. 짧은 장소명·사물명이나 다른 출처로 찾아보세요.' : '키워드를 누르면 바로 검색해요. 미리 보고 필요한 자료만 보관하세요.'}</div>}
    <dialog className="studio-reference-dialog" ref={dialog} onClose={() => setPreview(null)} aria-label="참고 자료 미리보기">
      {preview && <><div className="studio-row"><h2>{preview.title}</h2><button className="studio-icon-button" aria-label="미리보기 닫기" onClick={() => setPreview(null)}><X size={22} /></button></div><div className="studio-reference-player">{youtubeId ? <iframe src={`https://www.youtube-nocookie.com/embed/${youtubeId}`} title={preview.title} allow="encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" /> : preview.kind === 'video' ? <video src={preview.url} controls playsInline preload="metadata" poster={preview.thumbnail || undefined} /> : <img src={preview.url} alt={preview.title} />}</div><p className="studio-metadata">{preview.credit} · {preview.license}</p><div className="studio-row"><a href={preview.source_url} target="_blank" rel="noreferrer" className="studio-text-button">원본에서 보기 ↗</a><button className="studio-button studio-button-primary" disabled={busy || project.references.some((item) => item.id === preview.id)} onClick={() => {onImport(preview); setPreview(null);}}>{project.references.some((item) => item.id === preview.id) ? '보관됨' : preview.importable ? '내 소재로 가져오기' : '참고 링크 보관'}</button></div></>}
    </dialog>
  </div>;
}
