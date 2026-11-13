import {Check, Film, Upload} from 'lucide-react';
import {referenceAsset} from './api';
import type {Asset, Reference} from './api';

export function ReferenceMaterials({references, assets, onAttach, onSelect}: {
  references: Reference[]; assets: Asset[]; onAttach: (reference: Reference) => void; onSelect: (asset: Asset) => void;
}) {
  if (!references.length) return null;
  return <section className="studio-reference-materials" aria-label="보관한 참고 자료와 연결 파일">
    <div className="studio-subheading"><h2>보관한 참고 자료</h2><span>{references.length}개</span></div>
    <p className="studio-metadata">YouTube 링크만 보관했다면 영상 파일을 연결해 주세요. 연결한 파일을 장면 소재로 선택할 수 있어요.</p>
    <div className="studio-reference-material-list">{references.map((reference) => {
      const asset = referenceAsset(assets, reference);
      return <article key={reference.id} className="studio-reference-material">
        <a href={reference.source_url} target="_blank" rel="noreferrer" className="studio-reference-material-thumb" aria-label={`${reference.title} 원본 보기`}>{reference.thumbnail ? <img src={reference.thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <Film size={24} />}</a>
        <div className="studio-reference-material-info"><h3>{reference.title}</h3><p className="studio-metadata">{asset ? `연결됨 · ${asset.name}` : `${reference.provider} · 파일 연결 필요`}</p><a className="studio-text-button" href={reference.source_url} target="_blank" rel="noreferrer">원본 보기 ↗</a></div>
        {asset ? <button className="studio-button" onClick={() => onSelect(asset)}><Check size={16} />장면에 사용</button> : <button className="studio-button" aria-label={`${reference.title} ${reference.kind === 'video' ? '영상' : '이미지'} 파일 연결`} onClick={() => onAttach(reference)}><Upload size={16} />{reference.kind === 'video' ? '영상' : '이미지'} 파일 연결</button>}
      </article>;
    })}</div>
  </section>;
}
