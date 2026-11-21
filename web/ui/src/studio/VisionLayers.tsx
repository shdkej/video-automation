import type {Design, TextRegion} from './api';

const roles = {title:'첫 장면 제목',body:'장면 본문',cta:'마지막 장면 CTA',label:'고정 문구'};
export function VisionLayers({design,onChange}:{design:Design;onChange:(d:Design)=>void}) {
  const layout=design.vision;
  if(!layout) return null;
  const change=(regions:TextRegion[])=>onChange({...design,vision:{...layout,regions}});
  const edit=(index:number,patch:Partial<TextRegion>)=>change(layout.regions.map((r,i)=>i===index?{...r,...patch}:r));
  return <div className="vision-layers"><p>{layout.composition}</p>
    <div className="vision-palette" aria-label="레퍼런스 팔레트">{layout.palette.map((color,i)=><button key={i} type="button" aria-label={color+' 배경 색조로 적용'} title={color} style={{background:color}} onClick={()=>onChange({...design,wash:color})}/>)}</div>
    {layout.regions.map((r,i)=><details key={i} className="studio-details"><summary>{i+1}. {roles[r.role]} · {r.text.slice(0,30)}</summary>
      <label>문구 연결 <select value={r.role} onChange={e=>edit(i,{role:e.target.value as TextRegion['role']})}>{Object.entries(roles).map(([k,label])=><option value={k} key={k}>{label}</option>)}</select></label>
      <label>기본 문구 <textarea value={r.text} maxLength={500} onChange={e=>edit(i,{text:e.target.value})}/></label>
      <div className="archive-controls">{([['x','가로 중심',0,100],['y','세로 중심',0,100],['width','영역 너비',5,100],['size','글자 크기',1,18],['rotation','회전',-180,180]] as const).map(([k,label,min,max])=><label key={k}>{label} {r[k]}{k==='rotation'?'°':'%'}<input type="range" value={r[k]} min={min} max={max} step={0.5} onChange={e=>edit(i,{[k]:Number(e.target.value)})}/></label>)}
      <label>글자색 <input type="color" value={r.color} onChange={e=>edit(i,{color:e.target.value})}/></label>
      <label>정렬 <select value={r.align} onChange={e=>edit(i,{align:e.target.value as TextRegion['align']})}><option value="left">왼쪽</option><option value="center">가운데</option><option value="right">오른쪽</option></select></label>
      <label><input type="checkbox" checked={r.bold} onChange={e=>edit(i,{bold:e.target.checked})}/> 굵게</label>
      <label><input type="checkbox" checked={!!r.outline} onChange={e=>edit(i,{outline:e.target.checked?'#242424':null})}/> 외곽선</label>
      {r.outline && <label>외곽선 색 <input type="color" value={r.outline} onChange={e=>edit(i,{outline:e.target.value})}/></label>}</div>
      <button type="button" onClick={()=>change(layout.regions.filter((_,n)=>n!==i))}>영역 삭제</button></details>)}
      <button type="button" disabled={layout.regions.length>=12} onClick={()=>change([...layout.regions,{role:'label',text:'새 문구',x:50,y:50,width:75,size:5,rotation:0,color:design.ink,outline:null,align:'center',bold:false}])}>글자 영역 추가</button>
    </div>;
}
