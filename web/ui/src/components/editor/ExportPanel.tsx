// 내보내기 패널 — 산출물 다운로드·공유·클린 비교·zip/srt. 레거시 결과 그리드의 이주지.
import { useMemo, useRef, useState } from 'react';
import { Download, Share2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { archiveUrl, fileUrl } from '@/lib/api';
import type { Job } from '@/lib/types';

const CAN_SHARE_FILES = (() => {
  try {
    return !!navigator.canShare
      && navigator.canShare({ files: [new File([''], 't.mp4', { type: 'video/mp4' })] });
  } catch { return false; }
})();

type Cut = { name: string; label: string; fmt: string; vertical?: boolean; image?: boolean };

export function ExportPanel({ jobId, job }: { jobId: string; job: Job }) {
  const [sharing, setSharing] = useState<string | null>(null);
  const cmpRefs = useRef<Record<number, HTMLVideoElement[]>>({});

  const cuts = useMemo<Cut[]>(() => {
    const o = job.outputs || {};
    const list: Cut[] = [];
    if (o.note) list.push({ name: o.note, label: '노트 오버레이', fmt: 'mp4' });
    if (o.subtitled) list.push({ name: o.subtitled, label: '자막본', fmt: '원본 그대로' });
    if (o.longform) list.push({ name: o.longform, label: '롱폼', fmt: '16:9' });
    (o.shorts || []).forEach((n, i) => list.push({ name: n, label: `숏츠 ${i + 1}`, fmt: '9:16', vertical: true }));
    (o.shorts_clean || []).forEach((n, i) => list.push({ name: n, label: `숏츠 ${i + 1} 클린`, fmt: '9:16', vertical: true }));
    if (o.intro) list.push({ name: o.intro, label: '인트로', fmt: 'hook' });
    (o.thumbnail || []).forEach((n, i) => list.push({ name: n, label: `썸네일 ${i + 1}`, fmt: 'JPG', image: true }));
    return list;
  }, [job]);

  const share = async (cut: Cut) => {
    setSharing(cut.name);
    try {
      const blob = await (await fetch(fileUrl(jobId, cut.name))).blob();
      const file = new File([blob], cut.name, { type: blob.type || 'video/mp4' });
      await navigator.share({ files: [file] });
    } catch (e) {
      if ((e as Error).name !== 'AbortError') toast.error('공유 불가 — 다운로드를 이용해주세요');
    } finally {
      setSharing(null);
    }
  };

  const compares = useMemo(() => {
    const full = job.outputs?.shorts || [];
    const clean = job.outputs?.shorts_clean || [];
    return Array.from({ length: Math.min(full.length, clean.length) }, (_, i) => [full[i], clean[i]] as const);
  }, [job]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2.5">
        <Button asChild variant="secondary" size="sm">
          <a href={archiveUrl(jobId)}><Download className="size-3.5" /> 전부 받기 (zip)</a>
        </Button>
        {job.outputs?.srt && (
          <Button asChild variant="ghost" size="sm">
            <a href={fileUrl(jobId, job.outputs.srt)} download>자막 .srt</a>
          </Button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {cuts.map((c) => (
          <div key={c.name} className="space-y-2 rounded-lg border border-border bg-card p-2.5">
            <div className="flex items-center justify-between font-mono text-[11px] uppercase tracking-wide">
              <span className="text-primary">{c.label}</span>
              <span className="text-muted-foreground">{c.fmt}</span>
            </div>
            {c.image ? (
              <img src={fileUrl(jobId, c.name)} alt={c.label} className="w-full rounded-sm bg-black object-contain" />
            ) : (
              <video src={fileUrl(jobId, c.name)} controls preload="metadata"
                className="max-h-64 w-full rounded-sm bg-black object-contain" />
            )}
            <div className="flex items-center justify-between gap-2">
              <a href={fileUrl(jobId, c.name)} download
                className="min-w-0 truncate font-mono text-[11px] text-muted-foreground hover:text-primary">
                ↓ {c.name}
              </a>
              {CAN_SHARE_FILES && (
                <Button variant="ghost" size="sm" disabled={sharing === c.name} onClick={() => share(c)}>
                  <Share2 className="size-3.5" />
                  {sharing === c.name ? '준비 중…' : '공유'}
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>

      {compares.length > 0 && (
        <div className="space-y-3">
          <span className="font-mono text-xs tracking-wide text-muted-foreground">
            효과 비교 — 왼쪽 풀 효과 · 오른쪽 클린
          </span>
          {compares.map(([full, clean], i) => (
            <div key={i} className="space-y-2">
              <Button
                variant="secondary" size="sm"
                onClick={() => cmpRefs.current[i]?.forEach((v) => { v.currentTime = 0; v.play(); })}
              >
                숏츠 {i + 1} 동시 재생 ▶
              </Button>
              <div className="flex gap-2.5">
                {[full, clean].map((name, j) => (
                  <video
                    key={name}
                    ref={(el) => {
                      if (!cmpRefs.current[i]) cmpRefs.current[i] = [];
                      if (el) cmpRefs.current[i][j] = el;
                    }}
                    src={fileUrl(jobId, name)}
                    preload="metadata"
                    muted
                    className="aspect-[9/16] w-[calc(50%-5px)] max-w-[180px] rounded-md border border-border bg-black"
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
