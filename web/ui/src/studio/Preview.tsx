import { useEffect, useRef } from 'react';
import type { Project, Scene, TextRegion } from './api';

export function Preview({project, scene, index}: {project: Project; scene?: Scene; index: number}) {
  const video = useRef<HTMLVideoElement>(null);
  const asset = project.assets.find((item) => item.id === scene?.asset_id);
  useEffect(() => {
    if (video.current) video.current.currentTime = scene?.trim_start || 0;
  }, [asset?.url, scene?.trim_start]);
  const seekToStart = () => { if (video.current) video.current.currentTime = scene?.trim_start || 0; };
  const design = project.design;
  const regions:TextRegion[] = [...(design?.vision?.regions || [])];
  if(design?.vision) {
    for(const [role,y,size] of [['title',design.title_y,8],['body',design.body_y,5],['cta',90,4]] as const) {
      if(!regions.some(r=>r.role===role)) regions.push({role,text:'',x:50,y,width:84,size,rotation:0,color:design.ink,outline:'#242424',align:design.align,bold:true});
    }
  }
  return <div className={`studio-frame studio-frame--${design ? 'photo' : project.style} studio-frame--${project.font}`}>
    {asset?.kind === 'image' && <img className="studio-frame-media" src={asset.url} alt={asset.name} />}
    {asset?.kind === 'video' && <video ref={video} className="studio-frame-media" src={asset.url} muted playsInline controls
      onLoadedMetadata={seekToStart} onTimeUpdate={() => {
        if (video.current && scene && video.current.currentTime >= scene.trim_start + scene.duration) {
          video.current.pause(); seekToStart();
        }
      }} />}
    {!asset && !design && <div className="studio-frame-empty"><span>9 : 16</span><p>장면에 쓸 소재를<br />선택해 주세요.</p></div>}
    <div className="studio-frame-shade" style={design ? {background:design.wash,opacity:design.strength/100} : undefined} />
    {regions.map((region,i)=>{
      if(region.role==='title' && index!==0 || region.role==='cta' && index!==project.scenes.length-1) return null;
      const bound={title:project.hook,body:scene?.text,cta:project.cta,label:''}[region.role];
      return <div key={i} style={{position:'absolute',left:region.x+'%',top:region.y+'%',width:region.width+'%',fontSize:region.size+'cqw',
        transform:'translate(-50%,-50%) rotate('+region.rotation+'deg)',textAlign:region.align,lineHeight:1.25,color:region.color,
        fontWeight:region.bold?700:400,fontFamily:region.bold?undefined:'Studio Pretendard',whiteSpace:'pre-wrap',overflowWrap:'anywhere',WebkitTextStroke:region.outline?'0.05em '+region.outline:undefined,
        paintOrder:'stroke fill',pointerEvents:'none'}}>{bound || region.text}</div>;
    })}
    {!design?.vision && index === 0 && project.hook && <div style={design ? {top:design.title_y+"%",textAlign:design.align,color:design.ink} : undefined} className={`studio-frame-hook studio-frame-hook--${project.position}`}>{project.hook}</div>}
    {!design?.vision && scene?.text && <div className="studio-frame-caption" style={design ? {top:design.body_y+"%",textAlign:design.align,color:design.ink,background:"transparent",transform:"translateY(-50%)"} : undefined}>{scene.text}</div>}
    {!design?.vision && index === project.scenes.length - 1 && project.cta && <div className="studio-frame-cta">{project.cta}</div>}
  </div>;
}
