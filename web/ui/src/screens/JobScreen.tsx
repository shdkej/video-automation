// 잡 화면 — 폴링 상태에 따라 진행(같은 셸)·편집실·에러를 전환.
// 재생성은 편집실을 유지한 채 오버레이로 (P6: 컨텍스트를 뺏지 않는다).
import { useEffect, useRef, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Editor } from '@/components/editor/Editor';
import { useJobPolling } from '@/hooks/useJobPolling';
import { useRecentJobs } from '@/hooks/useRecentJobs';
import { notifyDone } from '@/lib/notify';
import type { Job } from '@/lib/types';

const STEPS = [
  { key: '분석', min: 0 }, { key: '롱폼', min: 30 }, { key: '숏츠', min: 55 },
  { key: '썸네일', min: 80 }, { key: '인트로', min: 92 },
];

function ProgressShell({ job, failStreak, onReset }: {
  job: Job | null; failStreak: number; onReset: () => void;
}) {
  const p = job?.progress || 0;
  const active = STEPS.reduce((acc, s, i) => (p >= s.min ? i : acc), 0);
  const showSteps = job?.kind !== 'note';
  return (
    <div className="mx-auto max-w-[640px] px-4 pt-10">
      <Card className="space-y-6 p-6">
        <div className="flex items-center gap-2.5">
          <span className="size-2 animate-pulse rounded-full bg-destructive" />
          <h2 className="text-xl font-semibold">
            {job?.status === 'queued' ? '대기 중' : '처리 중'}
          </h2>
        </div>
        {showSteps && (
          <ol className="flex justify-between">
            {STEPS.map((s, i) => (
              <li key={s.key} className="flex flex-1 flex-col items-center gap-2">
                <span className={`size-4 rounded-full border-2 transition-colors ${
                  i < active || p >= 100 ? 'border-accent bg-accent'
                  : i === active ? 'border-primary bg-primary shadow-[0_0_0_4px_rgba(255,122,24,0.18)]'
                  : 'border-border bg-background'
                }`} />
                <span className={`font-mono text-[11px] ${i === active ? 'text-primary' : 'text-muted-foreground'}`}>
                  {s.key}
                </span>
              </li>
            ))}
          </ol>
        )}
        <div className="h-2.5 overflow-hidden rounded-full border border-border bg-background">
          <div className="h-full bg-primary transition-[width] duration-500" style={{ width: `${p}%` }} />
        </div>
        <p className="text-center font-mono text-xs text-muted-foreground">
          {failStreak > 0
            ? `서버 연결 대기 중… (${failStreak}초) — 잡은 서버에서 계속 돕니다`
            : `${job?.stage || '연결 중'} · ${p}%`}
        </p>
        <Button variant="ghost" size="sm" onClick={onReset} className="mx-auto flex">
          <ArrowLeft className="size-4" /> 새 영상으로
        </Button>
      </Card>
    </div>
  );
}

export function JobScreen({ jobId, onReset, onOpenJob: _onOpenJob }: {
  jobId: string;
  onReset: () => void;
  onOpenJob: (id: string) => void;
}) {
  // pollKey — 적용(재생성) 시작 시 폴링을 재무장하는 스위치
  const [pollKey, setPollKey] = useState(0);
  const { job, failStreak, fatal } = useJobPolling(jobId, pollKey);
  const { update } = useRecentJobs();
  // 재생성 중에도 편집실 유지 — 마지막 done 잡을 보관
  const lastDone = useRef<Job | null>(null);
  const [, force] = useState(0);

  const notified = useRef(false);
  useEffect(() => {
    if (job?.status === 'done' && !notified.current) {
      notified.current = true;
      notifyDone(true, '산출물이 준비됐습니다');
    }
    if (job?.status === 'error' && !notified.current) {
      notified.current = true;
      notifyDone(false, job.error || '');
    }
    if (job?.status === 'queued' || job?.status === 'running') notified.current = false;
  }, [job]);

  useEffect(() => {
    if (job?.status === 'done') {
      lastDone.current = job;
      const o = job.outputs || {};
      const parts: string[] = [];
      if (o.longform) parts.push('롱폼');
      if (o.shorts?.length) parts.push(`숏츠${o.shorts.length}`);
      if (o.thumbnail?.length) parts.push(`썸네일${o.thumbnail.length}`);
      if (o.intro) parts.push('인트로');
      if (o.note) parts.push('노트');
      update(jobId, { out: parts.join(' · ') });
      force((n) => n + 1);
    }
  }, [job, jobId, update]);

  if (fatal) {
    return (
      <div className="mx-auto max-w-[560px] px-4 pt-14">
        <Card className="space-y-4 border-destructive/40 p-6">
          <h2 className="text-lg font-semibold text-destructive">멈췄습니다</h2>
          <p className="break-words font-mono text-xs text-muted-foreground">{fatal}</p>
          <Button variant="ghost" onClick={onReset}><ArrowLeft className="size-4" /> 새 영상으로</Button>
        </Card>
      </div>
    );
  }

  if (job?.status === 'error') {
    return (
      <div className="mx-auto max-w-[560px] px-4 pt-14">
        <Card className="space-y-4 border-destructive/40 p-6">
          <h2 className="text-lg font-semibold text-destructive">처리 중단</h2>
          <p className="break-words font-mono text-xs text-muted-foreground">{job.error || '알 수 없는 오류'}</p>
          <Button variant="ghost" onClick={onReset}><ArrowLeft className="size-4" /> 새 영상으로</Button>
        </Card>
      </div>
    );
  }

  const running = !job || job.status === 'queued' || job.status === 'running';

  // 재생성: 이전 done 잡이 있으면 편집실 유지 + 오버레이
  if (running && lastDone.current) {
    return (
      <Editor
        jobId={jobId}
        job={lastDone.current}
        rebuilding={{ stage: job?.stage || '재생성 준비', progress: job?.progress || 0 }}
        onReset={onReset}
        onApplied={() => setPollKey((k) => k + 1)}
      />
    );
  }

  if (running) return <ProgressShell job={job} failStreak={failStreak} onReset={onReset} />;

  return (
    <Editor
      key={pollKey} // 재생성 완료 후 분석 데이터 재로드
      jobId={jobId}
      job={job!}
      rebuilding={null}
      onReset={onReset}
      onApplied={() => setPollKey((k) => k + 1)}
    />
  );
}
