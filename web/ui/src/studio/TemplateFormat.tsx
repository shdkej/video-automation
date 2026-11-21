import type {Design,Mood} from './api';

const moodLabels = {clean:'담백한 · 프리텐다드',impact:'강렬한 · 검은고딕',friendly:'친근한 · 주아',handwritten:'손글씨 · 나눔손글씨펜'};
export function MoodSelect({design,change}:{design:Design;change:(mood:Mood)=>void}) {
  return <label className="studio-field">글꼴 분위기<select value={design.mood||''} onChange={e=>change(e.target.value as Mood)}>
    {!design.mood && <option value="" disabled>기존 프로젝트 글꼴</option>}
    {(Object.keys(moodLabels) as Mood[]).map(mood=><option key={mood} value={mood}>{moodLabels[mood]}</option>)}
  </select></label>;
}
export function FormatSize({design}:{design:Design}) {
  if(!design.source_size) return <p role="status">원본 크기를 확인할 수 없어요. 출력 전에 레퍼런스를 다시 선택하거나 템플릿을 해제해 주세요.</p>;
  const [w,h]=design.source_size;
  const scale=Math.min(1,4096/Math.max(w,h),Math.sqrt(8294400/(w*h)));
  const size=(unit:number)=>[w,h].map(v=>Math.max(unit,Math.round(v*scale/unit)*unit)).join(' × ');
  return <p className="studio-metadata">원본 {w} × {h} · JPG {size(1)} · MP4 {size(2)}</p>;
}
