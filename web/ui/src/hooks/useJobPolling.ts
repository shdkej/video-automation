// 잡 폴링 훅 — 레거시 startPolling의 내성 로직 이식:
// 5xx·비JSON·네트워크 단절은 일시 장애로 버티고(90초 상한), 백그라운드 탭은 5초 간격.
import { useEffect, useRef, useState } from 'react';
import { fetchJob } from '@/lib/api';
import type { Job } from '@/lib/types';

const FAIL_LIMIT = 90;

export type PollState = {
  job: Job | null;
  failStreak: number;
  fatal: string | null; // 폴링을 끝낸 사유 (404·연속 실패 한도)
};

export function useJobPolling(jobId: string | null, restartKey = 0): PollState {
  const [state, setState] = useState<PollState>({ job: null, failStreak: 0, fatal: null });
  const failRef = useRef(0);

  useEffect(() => {
    if (!jobId) return;
    void restartKey; // 재생성 시작 시 폴링 재무장 스위치
    failRef.current = 0;
    setState({ job: null, failStreak: 0, fatal: null });
    let stopped = false;
    let lastHiddenPoll = 0;

    const tick = async () => {
      if (stopped) return;
      if (document.hidden) {
        const now = Date.now();
        if (now - lastHiddenPoll < 5000) return;
        lastHiddenPoll = now;
      }
      const job = await fetchJob(jobId);
      if (stopped) return;
      if (job === 'gone') {
        setState((s) => ({ ...s, fatal: '작업을 찾을 수 없습니다 (서버 재시작으로 정리됐을 수 있음)' }));
        stopped = true;
        return;
      }
      if (job === null) {
        failRef.current += 1;
        if (failRef.current >= FAIL_LIMIT) {
          setState((s) => ({ ...s, failStreak: failRef.current,
            fatal: "서버가 응답하지 않습니다 — 복구되면 '최근 작업'에서 이어서 확인할 수 있습니다" }));
          stopped = true;
        } else {
          setState((s) => ({ ...s, failStreak: failRef.current }));
        }
        return;
      }
      failRef.current = 0;
      setState({ job, failStreak: 0, fatal: null });
      if (job.status === 'done' || job.status === 'error') stopped = true;
    };

    tick();
    const timer = setInterval(tick, 1000);
    return () => { stopped = true; clearInterval(timer); };
  }, [jobId, restartKey]);

  return state;
}
