import { useCallback, useEffect, useState } from 'react';
import { Toaster } from '@/components/ui/sonner';
import { UploadScreen } from '@/screens/UploadScreen';
import { JobScreen } from '@/screens/JobScreen';
import { fetchJob } from '@/lib/api';
import { useRecentJobs } from '@/hooks/useRecentJobs';

// ?job=<id> 딥링크 — 레거시와 동일 파라미터 유지
function useJobParam(): [string | null, (id: string | null) => void] {
  const [jobId, setJobIdState] = useState<string | null>(
    () => new URLSearchParams(location.search).get('job'),
  );
  const setJobId = useCallback((id: string | null) => {
    const url = new URL(location.href);
    if (id) url.searchParams.set('job', id);
    else url.searchParams.delete('job');
    history.pushState({}, '', url);
    setJobIdState(id);
  }, []);
  useEffect(() => {
    const onPop = () => setJobIdState(new URLSearchParams(location.search).get('job'));
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  return [jobId, setJobId];
}

export default function App() {
  const [jobId, setJobId] = useJobParam();
  const { jobs } = useRecentJobs();
  // 첫 로드에 진행 중 잡이 있으면 자동 복귀 — 새로고침해도 진행 화면 유지 (autoResume)
  useEffect(() => {
    if (jobId || !jobs[0]) return;
    let stopped = false;
    fetchJob(jobs[0].id).then((j) => {
      if (!stopped && j && j !== 'gone' && (j.status === 'running' || j.status === 'queued')) {
        setJobId(jobs[0].id);
      }
    });
    return () => { stopped = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <>
      {jobId
        ? <JobScreen jobId={jobId} onReset={() => setJobId(null)} onOpenJob={setJobId} />
        : <UploadScreen onSubmitted={setJobId} onOpenJob={setJobId} />}
      <Toaster position="top-center" />
    </>
  );
}
