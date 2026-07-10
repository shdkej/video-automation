// 필름스트립 타임라인 — 영상·자막·효과음 트랙 + 플레이헤드. 레거시 renderTimeline 이식.
import { useEffect, useMemo, useRef } from 'react';
import { frameUrl } from '@/lib/api';
import type { EdSeg, SfxItem } from '@/lib/types';
import { cn } from '@/lib/utils';

const PX_PER_SEC = 16;

/** 편집 상태의 출력 타임라인 — 사용 구간 누적 (몽타주=정확, 롱폼=xfade 근사) */
export function outTimelineMap(segs: EdSeg[]): { t0: number; d: number }[] {
  let t = 0;
  return segs.map((s) => {
    const d = s.use ? Math.max(0, s.end - s.start) : 0;
    const m = { t0: t, d };
    t += d;
    return m;
  });
}

export function Timeline({
  jobId, segs, sel, sfxLib, playTime, showPlayhead, onSelect,
}: {
  jobId: string;
  segs: EdSeg[];
  sel: number;
  sfxLib: SfxItem[];
  playTime: number;
  showPlayhead: boolean;
  onSelect: (i: number) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const cellRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const playheadRef = useRef<HTMLDivElement>(null);
  const map = useMemo(() => outTimelineMap(segs), [segs]);
  const showSfx = sfxLib.length > 0;

  const sfxLabel = (name: string) =>
    sfxLib.find((x) => x.name === name)?.label || name;

  // 플레이헤드 위치 — 셀 실측(offsetLeft/Width) 기반이라 CSS 변화에도 안전
  useEffect(() => {
    if (!showPlayhead || !playheadRef.current) return;
    let x: number | null = null;
    for (let i = 0; i < map.length; i++) {
      const cell = cellRefs.current[i];
      if (!map[i].d || !cell) continue;
      if (playTime < map[i].t0 + map[i].d) {
        const f = Math.min(1, Math.max(0, (playTime - map[i].t0) / map[i].d));
        x = cell.offsetLeft + f * cell.offsetWidth;
        break;
      }
      x = cell.offsetLeft + cell.offsetWidth;
    }
    if (x != null) playheadRef.current.style.left = `${Math.round(x)}px`;
  }, [playTime, map, showPlayhead, segs]);

  const width = (s: EdSeg) => Math.max(72, Math.round((s.end - s.start) * PX_PER_SEC));

  return (
    <div className="border-y border-border bg-black/40 px-3 py-2.5">
      <div ref={scroller} className="relative overflow-x-auto pb-1">
        <div className="flex w-max min-w-full flex-col gap-1">
          {/* 영상 트랙 */}
          <div className="flex gap-1">
            {segs.map((s, i) => (
              <button
                key={i}
                ref={(el) => { cellRefs.current[i] = el; }}
                type="button"
                onClick={() => onSelect(i)}
                style={{ width: width(s), backgroundImage: `url('${frameUrl(jobId, (s.start + s.end) / 2)}')` }}
                className={cn(
                  'relative h-16 shrink-0 overflow-hidden rounded-sm border bg-cover bg-center transition-colors',
                  i === sel ? 'border-primary ring-1 ring-inset ring-primary/50' : 'border-border',
                  !s.use && 'opacity-35',
                )}
              >
                {s.broll && (
                  <span className="absolute left-1 top-1 rounded-sm bg-accent px-1 font-mono text-[10px] font-semibold text-accent-foreground">B</span>
                )}
                <span className="absolute bottom-0.5 right-1 rounded-sm bg-black/60 px-1 font-mono text-[10px] text-foreground">
                  {(s.end - s.start).toFixed(1)}s
                </span>
              </button>
            ))}
          </div>
          {/* 자막 트랙 */}
          <div className="flex gap-1">
            {segs.map((s, i) => (
              <button
                key={i}
                type="button"
                onClick={() => onSelect(i)}
                style={{ width: width(s) }}
                className={cn(
                  'h-7 shrink-0 truncate rounded-sm border px-2 text-left text-xs text-muted-foreground',
                  i === sel ? 'border-primary/60' : 'border-border',
                  !s.use && 'opacity-35',
                )}
              >
                {(s.caption || '').split('\n')[0] || '–'}
              </button>
            ))}
          </div>
          {/* 효과음 트랙 */}
          {showSfx && (
            <div className="flex gap-1">
              {segs.map((s, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => onSelect(i)}
                  style={{ width: width(s) }}
                  className={cn(
                    'h-6 shrink-0 truncate rounded-sm border px-2 text-center font-mono text-[11px]',
                    i === sel ? 'border-primary/60' : 'border-border',
                    s.sfx ? 'text-accent' : 'text-muted-foreground/60',
                    !s.use && 'opacity-35',
                  )}
                >
                  {s.sfx ? sfxLabel(s.sfx) : '＋'}
                </button>
              ))}
            </div>
          )}
        </div>
        {showPlayhead && (
          <div
            ref={playheadRef}
            className="pointer-events-none absolute bottom-1 top-0 z-10 w-0.5 bg-primary shadow-[0_0_8px_var(--primary)]"
          />
        )}
      </div>
    </div>
  );
}
