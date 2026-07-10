// 플레이어 + 트랜스포트 — 결과물 재생, 타임코드, 플레이헤드 시간 공급.
// 매핑 가능(몽타주 숏폼·롱폼) 여부는 출력물마다 다르다 — 레거시 로직 이식.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { fileUrl } from '@/lib/api';
import { fmtTimecode } from '@/lib/media';
import type { Job } from '@/lib/types';
import { cn } from '@/lib/utils';

export type OutVideo = { name: string; label: string; mappable: boolean };

export function outVideosOf(job: Job, montage: boolean): OutVideo[] {
  const o = job.outputs || {};
  const list: OutVideo[] = [];
  if (o.note) list.push({ name: o.note, label: '노트', mappable: false });
  if (o.subtitled) list.push({ name: o.subtitled, label: '자막본', mappable: false });
  if (o.longform) list.push({ name: o.longform, label: '롱폼', mappable: true });
  (o.shorts || []).forEach((n, i) =>
    list.push({ name: n, label: `숏츠 ${i + 1}`, mappable: montage && i === 0 }));
  if (o.intro) list.push({ name: o.intro, label: '인트로', mappable: false });
  return list;
}

export function PlayerBay({
  jobId, outs, onTime, onMappableChange, seekRef, applySlot,
}: {
  jobId: string;
  outs: OutVideo[];
  onTime: (t: number) => void;
  onMappableChange: (m: boolean) => void;
  /** 타임라인 → 플레이어 시크 함수를 밖에서 쓸 수 있게 ref로 노출 */
  seekRef: React.MutableRefObject<((t: number) => void) | null>;
  /** 트랜스포트 우측 슬롯 — 적용 버튼 */
  applySlot?: React.ReactNode;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [sel, setSel] = useState(() => Math.max(0, outs.findIndex((v) => v.mappable)));
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [dur, setDur] = useState(0);
  const cur = outs[sel];

  useEffect(() => {
    onMappableChange(!!cur?.mappable);
  }, [cur, onMappableChange]);

  useEffect(() => {
    seekRef.current = (t: number) => {
      const v = video.current;
      if (v && cur?.mappable) v.currentTime = t;
    };
  }, [cur, seekRef]);

  // rAF 루프 — 재생 중 플레이헤드를 부드럽게
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      const v = video.current;
      if (v) {
        setTime(v.currentTime);
        onTime(v.currentTime);
        if (!v.paused && !v.ended) raf = requestAnimationFrame(loop);
      }
    };
    if (playing) raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [playing, onTime]);

  const toggle = useCallback(() => {
    const v = video.current;
    if (!v || !v.src) return;
    if (v.paused) v.play(); else v.pause();
  }, []);

  const src = useMemo(() => (cur ? fileUrl(jobId, cur.name) : undefined), [jobId, cur]);

  return (
    <div className="relative overflow-hidden rounded-t-xl bg-black">
      <video
        ref={video}
        src={src}
        playsInline
        preload="metadata"
        onClick={toggle}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
        onTimeUpdate={(e) => { setTime(e.currentTarget.currentTime); onTime(e.currentTarget.currentTime); }}
        className="mx-auto block max-h-[42vh] w-full cursor-pointer object-contain max-sm:max-h-[36vh]"
      />
      {!playing && (
        <button
          type="button"
          onClick={toggle}
          aria-label="재생"
          className="absolute inset-0 grid place-items-center text-foreground/90"
        >
          <span className="grid size-16 place-items-center rounded-full bg-black/45 backdrop-blur-sm">
            <Play className="size-7 translate-x-0.5" fill="currentColor" />
          </span>
        </button>
      )}
      {outs.length > 1 && (
        <div className="absolute left-2.5 top-2.5 flex flex-wrap gap-1.5">
          {outs.map((v, i) => (
            <button
              key={v.name}
              type="button"
              onClick={() => setSel(i)}
              className={cn(
                'rounded-full border px-2.5 py-1 font-mono text-[11px] backdrop-blur-sm transition-colors',
                i === sel
                  ? 'border-primary bg-black/70 text-primary'
                  : 'border-border bg-black/55 text-muted-foreground hover:text-foreground',
              )}
            >
              {v.label}
            </button>
          ))}
        </div>
      )}
      {/* 트랜스포트 */}
      <div className="flex items-center gap-3 border-t border-border bg-background px-3.5 py-2.5">
        <Button
          variant="secondary"
          size="icon"
          onClick={toggle}
          aria-label="재생/일시정지"
          className="size-11 rounded-full border border-input"
        >
          {playing ? <Pause className="size-4" /> : <Play className="size-4 translate-x-px" />}
        </Button>
        <span className="font-mono text-[12.5px] tracking-wide text-accent">
          {fmtTimecode(time)} / {fmtTimecode(dur)}
        </span>
        {cur && !cur.mappable && (
          <Badge variant="secondary" className="font-mono text-[10px] text-muted-foreground">
            타임라인 비동기 출력
          </Badge>
        )}
        <div className="flex-1" />
        {applySlot}
      </div>
    </div>
  );
}
