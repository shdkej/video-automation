import { useCallback, useEffect, useState } from 'react';
import { Toaster } from '@/components/ui/sonner';
import { UploadScreen } from '@/screens/UploadScreen';
import { JobScreen } from '@/screens/JobScreen';

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
  return (
    <>
      {jobId
        ? <JobScreen jobId={jobId} onReset={() => setJobId(null)} onOpenJob={setJobId} />
        : <UploadScreen onSubmitted={setJobId} onOpenJob={setJobId} />}
      <Toaster position="top-center" />
    </>
  );
}
