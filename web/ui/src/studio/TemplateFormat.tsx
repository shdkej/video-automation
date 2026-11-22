import type {Design,Mood} from './api';

import {moodLabels} from './api';
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

export function BackgroundControls({design,change}:{design:Design;change:(value:NonNullable<Design['background']>)=>void}) {
  const bg=design.background || {kind:'photo',color:design.wash,description:''};
  return <fieldset className="studio-details"><legend>배경 디자인</legend>
    <label>유형 <select value={bg.kind} onChange={e=>change({...bg,kind:e.target.value as typeof bg.kind})}><option value="photo">사진</option><option value="illustration">일러스트</option><option value="solid">단색</option></select></label>
    <label>바탕색 <input type="color" value={bg.color} onChange={e=>change({...bg,color:e.target.value})}/></label>
    {bg.kind==='solid'?<p>모든 장면에 이 색을 사용합니다.</p>:<><label>배경 설명 <textarea maxLength={900} value={bg.description} onChange={e=>change({...bg,description:e.target.value})}/></label>
      <p>소재 단계에서 배경을 업로드하거나, 이 설명으로 이미지 생성을 준비한 뒤 장면에 넣을 수 있어요.</p></>}
  </fieldset>;
}
