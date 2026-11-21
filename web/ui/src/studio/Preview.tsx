import { useEffect, useRef } from 'react';
import type { Project, Scene } from './api';

export function Preview({project, scene, index}: {project: Project; scene?: Scene; index: number}) {
  const video = useRef<HTMLVideoElement>(null);
  const asset = project.assets.find((item) => item.id === scene?.asset_id);
  useEffect(() => {
    if (video.current) video.current.currentTime = scene?.trim_start || 0;
  }, [asset?.url, scene?.trim_start]);
  const seekToStart = () => { if (video.current) video.current.currentTime = scene?.trim_start || 0; };
  const design = project.design;
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
    {index === 0 && project.hook && <div style={design ? {top:design.title_y+"%",textAlign:design.align,color:design.ink} : undefined} className={`studio-frame-hook studio-frame-hook--${project.position}`}>{project.hook}</div>}
    {scene?.text && <div className="studio-frame-caption" style={design ? {top:design.body_y+"%",textAlign:design.align,color:design.ink,background:"transparent",transform:"translateY(-50%)"} : undefined}>{scene.text}</div>}
    {index === project.scenes.length - 1 && project.cta && <div className="studio-frame-cta">{project.cta}</div>}
  </div>;
}
