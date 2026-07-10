// 클립 패널 — 트림 바(이동·앞뒤 핸들·고스트)·시간·자막·훅·효과음·B컷·사용 토글.
// 트림 연산은 레거시 initTrimBar 포인터 로직 이식.
import { useCallback, useRef, useState } from 'react';
import { ImagePlus, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { brollUrl, frameUrl, sfxUrl, uploadBroll } from '@/lib/api';
import { secToMmss } from '@/lib/media';
import type { EdSeg, SfxItem } from '@/lib/types';
import { cn } from '@/lib/utils';

const TRIM_MIN = 0.4;
const r1 = (n: number) => Math.round(n * 10) / 10;

function TrimBar({ seg, siblings, onChange }: {
  seg: EdSeg;
  siblings: EdSeg[]; // 같은 클립에서 나온 다른 사용 구간 (고스트)
  onChange: (p: { start: number; end: number }, final: boolean) => void;
}) {
  const bar = useRef<HTMLDivElement>(null);
  const drag = useRef<{ mode: 'l' | 'r' | 'move'; startX: number; s0: number; e0: number } | null>(null);
  const cs = seg.clipStart!;
  const ce = seg.clipEnd!;
  const span = ce - cs;
  const pct = (t: number) => `${Math.min(100, Math.max(0, ((t - cs) / span) * 100))}%`;

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    const el = e.target as HTMLElement;
    const handle = el.closest('[data-h]') as HTMLElement | null;
    const win = el.closest('[data-win]');
    let { start, end } = seg;
    if (!handle && !win && bar.current) {
      // 바 빈 곳 탭 — 창을 그 위치로 이동(중심 정렬)
      const rect = bar.current.getBoundingClientRect();
      const t = cs + ((e.clientX - rect.left) / rect.width) * span;
      const len = end - start;
      start = Math.min(Math.max(cs, t - len / 2), ce - len);
      end = start + len;
      onChange({ start, end }, false);
    }
    drag.current = { mode: handle ? (handle.dataset.h as 'l' | 'r') : 'move', startX: e.clientX, s0: start, e0: end };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    e.preventDefault();
  }, [seg, cs, ce, span, onChange]);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || !bar.current) return;
    const dt = ((e.clientX - d.startX) / bar.current.clientWidth) * span;
    let { s0: start, e0: end } = d;
    if (d.mode === 'l') start = Math.min(Math.max(cs, d.s0 + dt), end - TRIM_MIN);
    else if (d.mode === 'r') end = Math.max(Math.min(ce, d.e0 + dt), start + TRIM_MIN);
    else {
      const len = d.e0 - d.s0;
      start = Math.min(Math.max(cs, d.s0 + dt), ce - len);
      end = start + len;
    }
    onChange({ start, end }, false);
  }, [cs, ce, span, onChange]);

  const onPointerUp = useCallback(() => {
    if (!drag.current) return;
    drag.current = null;
    onChange({ start: r1(seg.start), end: r1(seg.end) }, true);
  }, [seg, onChange]);

  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2 font-mono text-xs text-muted-foreground">
        <span className="flex-1">
          클립 내 사용 구간 <em className="ml-1 not-italic text-accent">{(seg.end - seg.start).toFixed(1)}s / 클립 {span.toFixed(1)}s</em>
        </span>
      </div>
      <div
        ref={bar}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        className="relative h-9 cursor-pointer touch-none overflow-hidden rounded-md border border-border bg-background"
      >
        {siblings.map((o, i) => (
          <div
            key={i}
            className="pointer-events-none absolute bottom-1 top-1 rounded-sm bg-foreground/15"
            style={{ left: pct(o.start), width: `calc(${pct(o.end)} - ${pct(o.start)})` }}
          />
        ))}
        <div
          data-win
          className="absolute bottom-0 top-0 cursor-grab rounded-md border-[1.5px] border-primary bg-primary/20 active:cursor-grabbing"
          style={{ left: pct(seg.start), width: `calc(${pct(seg.end)} - ${pct(seg.start)})` }}
        >
          <span data-h="l" className="absolute inset-y-0 -left-1 w-3.5 cursor-ew-resize rounded-l-md border-l-4 border-primary" />
          <span data-h="r" className="absolute inset-y-0 -right-1 w-3.5 cursor-ew-resize rounded-r-md border-r-4 border-primary" />
        </div>
      </div>
    </div>
  );
}

export function ClipPanel({
  jobId, segs, sel, sfxLib, onPatch, onAddCut,
}: {
  jobId: string;
  segs: EdSeg[];
  sel: number;
  sfxLib: SfxItem[];
  onPatch: (i: number, p: Partial<EdSeg>) => void;
  onAddCut: (i: number) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const seg = segs[sel];

  if (!seg) {
    return (
      <p className="py-6 text-center font-mono text-xs leading-relaxed text-muted-foreground">
        타임라인에서 클립을 탭하세요 — 시간 · 자막 · 효과음 · B컷 · 사용 여부를 편집합니다
      </p>
    );
  }

  const hasClip = seg.clipStart != null && seg.clipEnd != null && seg.clipEnd - seg.clipStart > 0.2;
  const mid = (seg.start + seg.end) / 2;

  const pickBroll = async (f: File) => {
    setUploading(true);
    try {
      const { name } = await uploadBroll(jobId, f);
      onPatch(sel, { broll: name });
      toast.success('B컷 업로드 완료 — 적용하면 이 구간을 덮습니다');
    } catch (e) {
      toast.error(`B컷 업로드 실패: ${(e as Error).message}`);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[12.5px] text-muted-foreground">
          구간 {sel + 1} · {secToMmss(seg.start)}~{secToMmss(seg.end)}
        </span>
        <label className="flex items-center gap-2 text-[13px] text-muted-foreground">
          사용
          <Switch checked={seg.use} onCheckedChange={(v) => onPatch(sel, { use: v })} />
        </label>
      </div>

      <div className="grid grid-cols-[168px_1fr] gap-4 max-sm:grid-cols-1">
        <img
          src={frameUrl(jobId, mid)}
          alt={`구간 ${sel + 1}`}
          className="w-full rounded-md border border-border max-sm:mx-auto max-sm:max-w-[220px]"
        />
        <div className="min-w-0 space-y-3.5">
          {hasClip && (
            <>
              <TrimBar
                seg={seg}
                siblings={segs.filter((o, j) => j !== sel && o.use && o.clipStart === seg.clipStart)}
                onChange={(p, final) => onPatch(sel, final ? { start: r1(p.start), end: r1(p.end) } : p)}
              />
              <div className="flex gap-2">
                <Button variant="ghost" size="sm" onClick={() => onPatch(sel, { start: seg.clipStart!, end: seg.clipEnd! })}>
                  클립 전체
                </Button>
                <Button variant="ghost" size="sm" onClick={() => onAddCut(sel)}>
                  ＋ 구간 추가
                </Button>
              </div>
            </>
          )}
          <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
            <Input
              type="number" step={0.1} min={0} value={seg.start}
              onChange={(e) => onPatch(sel, { start: parseFloat(e.target.value) || 0 })}
              className="w-[88px] font-mono"
            /> ~
            <Input
              type="number" step={0.1} min={0} value={seg.end}
              onChange={(e) => onPatch(sel, { end: parseFloat(e.target.value) || 0 })}
              className="w-[88px] font-mono"
            /> 초
          </div>
          <Textarea
            rows={2}
            value={seg.caption}
            placeholder="자막 — 엔터로 줄을 나누면 그대로 반영"
            onChange={(e) => onPatch(sel, { caption: e.target.value })}
          />
          <Input
            value={seg.hook}
            placeholder="훅 배너 문구"
            onChange={(e) => onPatch(sel, { hook: e.target.value })}
          />
          {sfxLib.length > 0 && (
            <label className="flex items-center gap-2.5 font-mono text-xs text-muted-foreground">
              효과음
              <select
                value={seg.sfx}
                onChange={(e) => onPatch(sel, { sfx: e.target.value })}
                className="h-9 flex-1 rounded-md border border-input bg-background px-2.5 text-[13px] text-foreground"
              >
                <option value="">없음</option>
                {sfxLib.map((x) => (
                  <option key={x.name} value={x.name}>{x.label || x.name}</option>
                ))}
              </select>
              {seg.sfx && (
                <Button variant="ghost" size="sm" onClick={() => new Audio(sfxUrl(seg.sfx)).play()}>▶</Button>
              )}
            </label>
          )}
          {/* B컷 — 이 구간 화면을 덮는 컷어웨이 (오디오 유지 · 켄번즈) */}
          <div className="flex flex-wrap items-center gap-2.5 font-mono text-xs text-muted-foreground">
            B컷
            <input
              ref={fileInput}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) pickBroll(f); e.target.value = ''; }}
            />
            {seg.broll ? (
              <>
                <img src={brollUrl(jobId, seg.broll)} alt="B컷" className="h-9 w-14 rounded-sm border border-input object-cover" />
                <Button variant="ghost" size="sm" disabled={uploading} onClick={() => fileInput.current?.click()}>교체</Button>
                <Button variant="ghost" size="sm" onClick={() => onPatch(sel, { broll: '' })}>
                  <Trash2 className="size-3.5" /> 제거
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" size="sm" disabled={uploading} onClick={() => fileInput.current?.click()}>
                  <ImagePlus className={cn('size-3.5', uploading && 'animate-pulse')} />
                  {uploading ? '업로드 중…' : '이미지 추가'}
                </Button>
                <span className="text-muted-foreground/70">구간 화면을 덮는 컷어웨이</span>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
