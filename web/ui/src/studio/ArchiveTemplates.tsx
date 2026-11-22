import {useEffect,useRef,useState} from 'react';
import {studioApi, type ArchivePage, type Design, type Project, type TextRegion} from './api';
import {FormatSize,MoodSelect,BackgroundControls} from './TemplateFormat';
import {VisionLayers} from './VisionLayers';
import {Preview} from './Preview';

let tabGeminiKey = '';

export function ArchiveTemplates({project,update}:{project:Project;update:(values:Partial<Project>)=>void}) {
  const [open,setOpen] = useState(false);
  const [page,setPage] = useState<ArchivePage>({images:[],cursor:null});
  const [saved,setSaved] = useState<Design[]>([]);
  const [busy,setBusy] = useState(false);
  const [geminiKey,setGeminiKey] = useState(tabGeminiKey);
  const [message,setMessage] = useState('');
  const mounted = useRef(true);
  useEffect(()=>{mounted.current=true;return ()=>{mounted.current=false;};},[]);
  const design = project.design;
  const edit = (changes:Partial<Design>) => {if(design) update({design:{...design,...changes}});};
  async function act(action:()=>Promise<void>) {
    setBusy(true); setMessage('');
    try {await action();} catch(e) {setMessage(e instanceof Error ? e.message : '다시 시도해 주세요.');}
    finally {setBusy(false);}
  }
  function list(cursor='') {
    void act(async()=>{
      const data = await studioApi.archive(cursor);
      setPage(prev=>({images:cursor?[...prev.images,...data.images]:data.images,cursor:data.cursor}));
    });
  }
  const sample = project.scenes[0] || {id:'preview',asset_id:'',text:design?.vision?'':'본문 미리보기',duration:4,trim_start:0};
  return <section className="studio-details archive-picker">
    <button type="button" aria-expanded={open} className="studio-button" disabled={busy} onClick={()=>{
      setOpen(!open);
      if(!open) void act(async()=>{
        setSaved(await studioApi.templates());
        setPage(await studioApi.archive(''));
      });
    }}>{open?'템플릿 닫기':'아카이브 디자인 템플릿'}</button>
    {open && <fieldset disabled={busy}>
      {saved.length>0 && <label>저장한 템플릿 <select value="" onChange={e=>{const chosen=saved[Number(e.target.value)]; if(e.target.value && chosen) void act(async()=>{
          const resolved = chosen.source_size ? chosen : await studioApi.resolveTemplate(chosen);
          if(mounted.current) update({design:resolved});
        });}}><option value="">선택</option>{saved.map((s,i)=><option value={i} key={s.key+s.name}>{s.name}</option>)}</select></label>}
      <p>레퍼런스를 골라 색감과 글자 위치를 가져오세요.</p>
      <div className="archive-grid">{page.images.map(item=><button type="button" key={item.key} aria-label={item.key+' 디자인 적용'} aria-pressed={design?.key===item.key} onClick={()=>void act(async()=>{const derived=await studioApi.analyze(item.key); if(mounted.current) update({design:derived});})}>
        <img src={item.url} loading="lazy" alt={item.key.split('/').pop()} /></button>)}</div>
      {page.cursor && <button type="button" onClick={()=>list(page.cursor!)}>더 보기</button>}
      {!page.images.length && !busy && <button type="button" onClick={()=>list()}>사진 목록 다시 불러오기</button>}
      {design && <><div className="archive-comparison">
        <figure><img src={'https://img.shdkej.com/'+design.key} alt="선택한 레퍼런스" /><figcaption>레퍼런스</figcaption></figure>
        <figure><Preview project={{...project,hook:design.vision?project.hook:project.hook||'제목 미리보기'}} scene={sample} index={0}/><figcaption>현재 디자인</figcaption></figure>
      </div><FormatSize design={design}/><BackgroundControls design={design} change={background=>edit({background})}/><MoodSelect design={design} change={mood=>edit({mood})}/><label>템플릿 이름 <input value={design.name} maxLength={100} onChange={e=>edit({name:e.target.value})}/></label>
      <label className="studio-field">Gemini API 키 · 선택<input type="password" autoComplete="off" spellCheck={false} maxLength={512} value={geminiKey} onChange={e=>{tabGeminiKey=e.target.value.trim();setGeminiKey(tabGeminiKey);}} placeholder="서버 키 사용"/></label>
      <div className="studio-row">{(['fast','precision'] as const).map(mode=><button key={mode} type="button" onClick={()=>void act(async()=>{
        const result=await studioApi.vision(design.key,mode,geminiKey);
        if(mounted.current){update({design:result.design});setMessage(result.cached?'저장된 분석을 적용했어요.':result.model+' 분석을 적용했어요.');}
      })}>{mode==='fast'?'Gemini 기본 분석':'Gemini 정밀 분석'}</button>)}</div>
      <p className="studio-metadata">{design.vision?(design.vision.source==='manual'?'직접 설정한 글자 영역입니다.':'AI 분석 결과 · 글자 영역을 편집할 수 있어요.'):'현재는 사진의 명암으로 계산한 기본 배치입니다.'} 같은 사진과 모델의 분석 결과는 재사용합니다. 새 분석은 사진을 Gemini에 보내며 계정 요금이 적용됩니다.</p>
      {!design.vision && <button type="button" onClick={()=>{
        const regions:TextRegion[]=([['title',design.title_y,8],['body',design.body_y,5],['cta',90,4]] as const).map(([role,y,size])=>({
          role,text:'',mood:null,x:50,y,size,width:84,rotation:0,color:design.ink,outline:'#242424',align:design.align,bold:true
        }));
        edit({vision:{source:'manual',name:design.name,composition:'제목·본문·CTA를 직접 편집합니다.',palette:[design.wash,design.ink],wash:design.wash,strength:design.strength,regions}});
      }}>글자 영역 직접 편집</button>}
      {design.vision && <VisionLayers design={design} onChange={next=>update({design:next})}/>}
      {!design.vision && <label>글자 정렬 <select value={design.align} onChange={e=>edit({align:e.target.value as Design['align']})}><option value="left">왼쪽</option><option value="center">가운데</option><option value="right">오른쪽</option></select></label>}
      <div className="archive-controls">{([['ink','글자색'],['wash','배경 색조']] as const).filter(([key])=>(!design.vision||key==='wash')&&(key!=='wash'||design.background?.kind!=='solid')).map(([key,label])=><label key={key}>{label}<input type="color" value={design[key]} onChange={e=>edit({[key]:e.target.value})}/></label>)}
      {([['strength','색조 강도',0,90],['title_y','제목 위치',12,85],['body_y','본문 위치',12,85]] as const).filter(([key])=>(!design.vision||key==='strength')&&(key!=='strength'||design.background?.kind!=='solid')).map(([key,label,min,max])=><label key={key}>{label} {design[key]}%<input type="range" min={min} max={max} value={design[key]} onChange={e=>edit({[key]:Number(e.target.value)})}/></label>)}</div>
      <button type="button" disabled={!design.name.trim()} onClick={()=>void act(async()=>{
        await studioApi.saveTemplate(design); setSaved(await studioApi.templates()); setMessage('다른 프로젝트에서도 사용할 수 있게 저장했어요.');
      })}>템플릿 저장</button> <button type="button" onClick={()=>update({design:null})}>적용 해제</button></>}
    </fieldset>}
    {(busy||message) && <p role="status">{busy?'불러오는 중…':message}</p>}
  </section>;
}
