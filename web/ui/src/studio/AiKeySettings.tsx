import {useRef, useState} from 'react';
import {KeyRound} from 'lucide-react';

export function AiKeySettings({active, disabled, onChange}: {active: boolean; disabled: boolean; onChange: (key: string) => void}) {
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const details = useRef<HTMLDetailsElement>(null);
  return <details className="studio-key-settings" ref={details} onToggle={() => {if (!details.current?.open) {setDraft(''); setError('');}}}>
    <summary className="studio-text-button"><KeyRound size={16} />{active ? '내 AI 키 사용 중' : 'AI 키 입력'}</summary>
    <form className="studio-key-panel" onSubmit={(event) => {
      event.preventDefault();
      const key = draft.trim();
      if (!/^[!-~]{1,512}$/.test(key)) {setError('공백 없이 API 키를 입력해 주세요.'); return;}
      onChange(key); setDraft(''); setError(''); if (details.current) details.current.open = false;
    }}>
      <h2>내 OpenAI API 키</h2>
      <p className="studio-metadata">입력한 키를 이 탭의 AI 생성에 우선 사용해요. 새로고침하거나 탭을 닫으면 사라져요.</p>
      <label className="studio-field"><span>OpenAI API 키</span><input type="password" value={draft} maxLength={512} autoComplete="off" spellCheck={false} placeholder="sk-…" onChange={(event) => setDraft(event.target.value)} disabled={disabled} /></label>
      <p className="studio-metadata">AI 요청 때만 이 서비스 서버를 거쳐 OpenAI에 전달돼요. 브라우저 저장소와 프로젝트에는 저장하지 않아요.</p>
      <p className="studio-metadata">키 입력만으로는 API를 호출하지 않아요. 생성 버튼을 누르면 해당 계정에 사용료가 발생해요.</p>
      {error && <p className="studio-inline-help" role="alert">{error}</p>}
      <div className="studio-row"><button className="studio-button studio-button-primary" disabled={disabled || !draft.trim()}>이 탭에서 사용</button>{active && <button type="button" className="studio-text-button" disabled={disabled} onClick={() => {onChange(''); setDraft('');}}>내 키 지우기</button>}</div>
      {active && <p className="studio-metadata">내 키를 지우면 설정된 서버 키를 사용해요. 키 유효성은 실제 생성 요청에서 확인돼요.</p>}
    </form>
  </details>;
}
