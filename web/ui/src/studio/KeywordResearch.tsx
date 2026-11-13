import {useState} from 'react';
import {Check, Search} from 'lucide-react';
import type {Project} from './api';

export function KeywordResearch({project, enabled, query, setQuery, onSearch, onPick}: {
  project: Project; enabled: boolean; query: string; setQuery: (query: string) => void; onSearch: (query: string) => void; onPick: (keyword: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const research = project.keyword_research;
  const items = research?.items || [];
  return <div className="studio-keyword-research">
    <div className="studio-subheading"><h2>실제 검색어에서 찾기</h2><span>AI 없이 조회</span></div>
    <label className="studio-field"><span>시작할 키워드</span><input value={query} maxLength={500} placeholder="예: 세계여행, 신혼여행, 여행경비" onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
      if (event.key === 'Enter' && !event.nativeEvent.isComposing && enabled && query.trim()) {event.preventDefault(); setExpanded(false); onSearch(query);}
    }} /></label>
    <div className="studio-keyword-search-actions"><p className="studio-metadata">쉼표로 나누어 최대 5개. 대본과 관련된 단어부터 시작해요.</p><button className="studio-button studio-button-primary" disabled={!enabled || !query.trim()} onClick={() => {setExpanded(false); onSearch(query);}}><Search size={16} />연관 키워드 조회</button></div>
    {!enabled && <p className="studio-inline-help">네이버 키워드 조회가 아직 연결되지 않았어요. 직접 입력하며 작업할 수 있어요.</p>}
    {research && <>
      <div className="studio-keyword-result-meta"><strong>{research.seeds.join(' · ')}</strong><span>연관어 {research.total_found.toLocaleString()}개 중 {items.length}개</span></div>
      <p className="studio-metadata">네이버 검색광고 · {new Date(research.queried_at).toLocaleString('ko-KR')} 조회</p>
      {research.notices.map((notice) => <p key={notice} className="studio-metadata">{notice}</p>)}
      {items.length ? <>
        <div className="studio-keyword-candidates studio-measured-keywords">{(expanded ? items : items.slice(0, 12)).map((item) => <button key={item.keyword} className={`studio-keyword-candidate ${project.keywords.includes(item.keyword) ? 'is-selected' : ''}`} aria-pressed={project.keywords.includes(item.keyword)} aria-label={`${item.keyword} 키워드 ${project.keywords.includes(item.keyword) ? '해제' : '선택'}`} onClick={() => onPick(item.keyword)}>
          <span className="studio-candidate-angle">{item.is_seed ? '입력한 키워드' : '연관 키워드'}</span>
          <strong>{item.keyword}{project.keywords.includes(item.keyword) && <Check size={16} />}</strong>
          <span className="studio-search-volume">월 {item.monthly_label}{item.monthly_label === '미확인' ? '' : '회'}</span>
          <span>PC {item.pc} · 모바일 {item.mobile}</span>
          <small>광고 경쟁도 {item.competition}</small>
        </button>)}</div>
        {items.length > 12 && <button className="studio-text-button studio-keyword-more" onClick={() => setExpanded(!expanded)}>{expanded ? '처음 12개만 보기' : `후보 ${items.length - 12}개 더 보기`}</button>}
        {research.total_found > items.length && <p className="studio-metadata">더 좁은 단어를 입력하면 다른 연관어를 살펴볼 수 있어요.</p>}
      </> : <div className="studio-empty-inline">검색 결과가 없어요. 더 짧거나 다른 단어로 조회해 보세요.</div>}
    </>}
  </div>;
}
