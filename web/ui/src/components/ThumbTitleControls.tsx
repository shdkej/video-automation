// 썸네일 타이틀 컨트롤 — 업로드 폼·편집실 공용.
// 미리보기는 CSS 근사가 아니라 서버 렌더(overlay_hook_text) — 산출물과 동일 픽셀.
// 레거시 createThumbControls / TC_TEMPLATE 이식.
import { useEffect, useMemo, useRef, useState } from 'react';
import { getThumbTemplates, thumbPreview } from '@/lib/api';
import type { ThumbTemplate } from '@/lib/types';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Slider } from '@/components/ui/slider';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

export type ThumbState = {
  text: string; font: string; scale: number; weight: string; effect: string; pos: string; template: string;
};

export const DEFAULT_THUMB_STATE: ThumbState = {
  text: '오늘의 하이라이트\n지금 공개합니다',
  font: 'pretendard', scale: 1.5, weight: 'bold', effect: 'none', pos: 'top-center', template: 'custom',
};

export function thumbStateToForm(fd: FormData, s: ThumbState): void {
  fd.append('thumb_text', s.text.trim());
  fd.append('thumb_pos', s.pos);
  fd.append('thumb_font', s.font);
  fd.append('thumb_scale', String(s.scale));
  fd.append('thumb_weight', s.weight);
  fd.append('thumb_effect', s.effect);
  fd.append('thumb_template', s.template);
}

// 폰트 키 → 프리뷰 칩용 CSS 패밀리 (백엔드 동봉 폰트와 동일)
const FONT_CSS: Record<string, string> = {
  pretendard: "'Pretendard'", blackhan: "'BlackHanSansW'", dohyeon: "'DoHyeonW'",
  jua: "'JuaW'", nanumpen: "'NanumPenW'",
};
const FONTS = [
  { key: 'pretendard', label: '프리텐다드' }, { key: 'blackhan', label: '블랙한산스' },
  { key: 'dohyeon', label: '도현' }, { key: 'jua', label: '주아' }, { key: 'nanumpen', label: '나눔손글씨' },
];
const WEIGHTS = [{ key: 'normal', label: '보통' }, { key: 'bold', label: '굵게' }, { key: 'heavy', label: '아주 굵게' }];
const EFFECTS = [
  { key: 'none', label: '없음' }, { key: 'fireworks', label: '폭죽' },
  { key: 'fire', label: '불꽃' }, { key: 'sparkle', label: '반짝이' },
];
const POSITIONS = [
  'top-left', 'top-center', 'top-right',
  'middle-left', 'middle-center', 'middle-right',
  'bottom-left', 'bottom-center', 'bottom-right',
];

// 동봉 @font-face를 1회 주입 — 칩이 실제 폰트로 렌더되도록
const FONT_FACES = `
@font-face{font-family:'BlackHanSansW';src:url('/fonts/BlackHanSans-Regular.ttf');font-display:swap}
@font-face{font-family:'DoHyeonW';src:url('/fonts/DoHyeon-Regular.ttf');font-display:swap}
@font-face{font-family:'JuaW';src:url('/fonts/Jua-Regular.ttf');font-display:swap}
@font-face{font-family:'NanumPenW';src:url('/fonts/NanumPenScript-Regular.ttf');font-display:swap}`;
let facesInjected = false;
function injectFontFaces() {
  if (facesInjected || typeof document === 'undefined') return;
  facesInjected = true;
  const style = document.createElement('style');
  style.textContent = FONT_FACES;
  document.head.appendChild(style);
}

// 템플릿 목록 — 모듈 레벨 1회 캐시(폼·편집실 공용)
let tplPromise: Promise<ThumbTemplate[]> | null = null;
function loadTemplates() {
  if (!tplPromise) tplPromise = getThumbTemplates().catch(() => [] as ThumbTemplate[]);
  return tplPromise;
}

// 단일 선택 세그먼티드 — muted 트랙, 활성 = bg-input (DESIGN Segmented)
function Segmented({ value, onChange, options }: {
  value: string;
  onChange: (v: string) => void;
  options: { key: string; label: string; style?: React.CSSProperties }[];
}) {
  return (
    <ToggleGroup
      type="single"
      value={value}
      onValueChange={(v) => { if (v) onChange(v); }}
      className="w-max bg-muted p-0.5"
    >
      {options.map((o) => (
        <ToggleGroupItem
          key={o.key}
          value={o.key}
          style={o.style}
          className="h-8 rounded-sm px-3 text-[13px] data-[state=on]:bg-input data-[state=on]:text-foreground"
        >
          {o.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

function ScrollRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-12 shrink-0 font-mono text-[12px] tracking-wide text-muted-foreground">{label}</span>
      <div className="min-w-0 flex-1 overflow-x-auto [mask-image:linear-gradient(to_right,black_92%,transparent)] [-webkit-mask-image:linear-gradient(to_right,black_92%,transparent)]">
        {children}
      </div>
    </div>
  );
}

type Base = { blob?: Blob; jobId?: string; t?: number; note?: string } | null;

export function ThumbTitleControls({ state, onChange, getBase, autoText }: {
  state: ThumbState;
  onChange: (next: ThumbState) => void;
  getBase: () => Promise<Base>;
  autoText?: () => string;
}) {
  const [templates, setTemplates] = useState<ThumbTemplate[]>([]);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [hasBase, setHasBase] = useState(false);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState('');
  const [aspect, setAspect] = useState('16 / 9');
  const urlRef = useRef<string | null>(null);

  useEffect(() => { injectFontFaces(); }, []);
  useEffect(() => { loadTemplates().then(setTemplates); }, []);
  useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current); }, []);

  const tplMap = useMemo(() => Object.fromEntries(templates.map((t) => [t.key, t])), [templates]);
  const manualDim = state.template !== 'custom';

  // 폰트/굵기/효과는 템플릿 번들에 덮이므로 직접 만지면 "직접 조합"으로 복귀
  const setCustom = (patch: Partial<ThumbState>) => onChange({ ...state, template: 'custom', ...patch });

  const selectTemplate = (key: string) => {
    if (key === 'custom') { onChange({ ...state, template: 'custom' }); return; }
    const meta = tplMap[key];
    onChange(meta
      ? { ...state, template: key, font: meta.font, weight: meta.weight, effect: meta.effect }
      : { ...state, template: key });
  };
  const toggleOff = () => onChange({ ...state, pos: state.pos === 'off' ? 'bottom-center' : 'off' });

  // 서버 미리보기 — 상태·바탕이 바뀔 때마다 400ms 디바운스로 재렌더
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      const base = await getBase();
      if (cancelled) return;
      if (!base) { setHasBase(false); return; }
      setHasBase(true);
      setLoading(true);
      const fd = new FormData();
      fd.append('text', state.pos === 'off' ? '' : (state.text.trim() || (autoText ? autoText() : '')));
      fd.append('pos', state.pos === 'off' ? 'bottom-center' : state.pos);
      fd.append('font', state.font);
      fd.append('scale', String(state.scale));
      fd.append('weight', state.weight);
      fd.append('effect', state.effect);
      fd.append('template', state.template);
      if (base.blob) fd.append('frame', base.blob, 'frame.jpg');
      else if (base.jobId) { fd.append('job_id', base.jobId); fd.append('t', String(base.t)); }
      try {
        const blob = await thumbPreview(fd);
        if (cancelled) { return; }
        const url = URL.createObjectURL(blob);
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = url;
        setPreviewUrl(url);
        setNote(base.note || '');
      } catch (err) {
        if (!cancelled) setNote(`미리보기 실패 (${(err as Error).message}) — 산출엔 영향 없음`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 400);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [state.text, state.font, state.scale, state.weight, state.effect, state.pos, state.template, getBase, autoText]);

  return (
    <div className="flex flex-col gap-4 md:flex-row-reverse md:items-start">
      {hasBase && (
        <div className="md:sticky md:top-4 md:w-60 md:shrink-0">
          <div
            className="relative overflow-hidden rounded-lg border bg-background"
            style={{ aspectRatio: aspect }}
          >
            {previewUrl && (
              <img
                src={previewUrl}
                alt="썸네일 미리보기"
                onLoad={(e) => {
                  const img = e.currentTarget;
                  if (img.naturalWidth) setAspect(`${img.naturalWidth} / ${img.naturalHeight}`);
                }}
                className={cn('h-full w-full object-contain transition-opacity', loading && 'opacity-60')}
              />
            )}
            {loading && (
              <span className="absolute top-2 right-2 rounded-full border bg-popover/90 px-2 py-0.5 font-mono text-[11px]">
                준비 중…
              </span>
            )}
          </div>
          {note && <p className="mt-2 text-[12px] text-muted-foreground">{note}</p>}
        </div>
      )}

      <div className="min-w-0 flex-1 space-y-3">
        <Textarea
          rows={2}
          value={state.text}
          onChange={(e) => onChange({ ...state, text: e.target.value })}
          placeholder="비우면 자동 (훅 문구) — 엔터로 줄바꿈"
          className="resize-none bg-background"
        />

        <ScrollRow label="템플릿">
          <Segmented
            value={state.template}
            onChange={selectTemplate}
            options={[
              { key: 'custom', label: '직접 조합' },
              ...templates.map((t) => ({
                key: t.key,
                label: t.label,
                style: {
                  fontFamily: FONT_CSS[t.font] || FONT_CSS.pretendard,
                  color: t.color,
                  ...(t.bg ? { background: t.bg } : {}),
                } as React.CSSProperties,
              })),
            ]}
          />
        </ScrollRow>

        <div style={{ opacity: manualDim ? 0.55 : 1 }} className="space-y-3">
          <ScrollRow label="폰트">
            <Segmented
              value={state.font}
              onChange={(v) => setCustom({ font: v })}
              options={FONTS.map((f) => ({ key: f.key, label: f.label, style: { fontFamily: FONT_CSS[f.key] } }))}
            />
          </ScrollRow>
          <ScrollRow label="굵기">
            <Segmented value={state.weight} onChange={(v) => setCustom({ weight: v })} options={WEIGHTS} />
          </ScrollRow>
          <ScrollRow label="효과">
            <Segmented value={state.effect} onChange={(v) => setCustom({ effect: v })} options={EFFECTS} />
          </ScrollRow>
        </div>

        <div className="flex items-center gap-3">
          <span className="w-12 shrink-0 font-mono text-[12px] tracking-wide text-muted-foreground">크기</span>
          <Slider
            value={[Math.round(state.scale * 100)]}
            min={50}
            max={200}
            step={5}
            onValueChange={([v]) => onChange({ ...state, scale: v / 100 })}
            className="max-w-64 flex-1"
          />
          <span className="w-12 shrink-0 text-right font-mono text-[12px] text-muted-foreground">
            {Math.round(state.scale * 100)}%
          </span>
        </div>

        <div className="flex items-start gap-3">
          <span className="w-12 shrink-0 pt-1 font-mono text-[12px] tracking-wide text-muted-foreground">위치</span>
          <div className="grid w-fit grid-cols-3 gap-1">
            {POSITIONS.map((p) => (
              <button
                key={p}
                type="button"
                aria-label={p}
                onClick={() => onChange({ ...state, pos: p })}
                className={cn(
                  'h-5 w-7 rounded-sm border transition-colors',
                  state.pos === p ? 'border-primary bg-primary' : 'border-input bg-background hover:border-ring',
                )}
              />
            ))}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={toggleOff}
            className={cn(state.pos === 'off' && 'bg-secondary text-foreground')}
          >
            글자 없음
          </Button>
        </div>
      </div>
    </div>
  );
}
