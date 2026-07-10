// API 클라이언트 — web/app.py 계약 동결. 여기 외의 곳에서 fetch 금지.
import type { Analysis, Job, MusicLib, SfxItem, ThumbTemplate } from './types';

export const fileUrl = (jobId: string, name: string) =>
  `/api/jobs/${jobId}/file/${encodeURIComponent(name)}`;
export const frameUrl = (jobId: string, t: number) =>
  `/api/jobs/${jobId}/frame?t=${t.toFixed(1)}`;
export const brollUrl = (jobId: string, name: string) =>
  `/api/jobs/${jobId}/broll/${encodeURIComponent(name)}`;
export const archiveUrl = (jobId: string) => `/api/jobs/${jobId}/archive`;
export const musicUrl = (mood: string, name: string) =>
  `/api/music/${encodeURIComponent(mood)}/${encodeURIComponent(name)}`;
export const sfxUrl = (name: string) => `/api/sfx/${encodeURIComponent(name)}`;

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({} as { detail?: string }));
    throw new Error((body as { detail?: string }).detail || res.statusText || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

/** 잡 조회 — 5xx·비JSON(프록시 HTML)은 null 반환(일시 장애), 404는 'gone'. */
export async function fetchJob(jobId: string): Promise<Job | 'gone' | null> {
  let res: Response;
  try {
    res = await fetch(`/api/jobs/${jobId}`);
  } catch {
    return null; // 네트워크 단절 — 일시 장애로 취급
  }
  if (res.status === 404) return 'gone';
  if (!res.ok) return null;
  try {
    return (await res.json()) as Job;
  } catch {
    return null; // HTML 에러 페이지 — Safari generic SyntaxError 방지의 이식
  }
}

export const getAnalysis = (jobId: string) =>
  fetch(`/api/jobs/${jobId}/analysis`).then((r) => jsonOrThrow<Analysis>(r));

export const saveAnalysis = (jobId: string, body: {
  segments: object[]; captions: string[]; transcript: { i: number; text: string }[];
}) =>
  fetch(`/api/jobs/${jobId}/analysis`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((r) => jsonOrThrow<object>(r));

export const getMusic = () => fetch('/api/music').then((r) => jsonOrThrow<MusicLib>(r));
export const getSfx = () =>
  fetch('/api/sfx').then((r) => jsonOrThrow<{ sfx: SfxItem[] }>(r)).then((d) => d.sfx || []);
export const getThumbTemplates = () =>
  fetch('/api/thumb-templates').then((r) => jsonOrThrow<ThumbTemplate[]>(r));

export const rebuild = (jobId: string, fd: FormData) =>
  fetch(`/api/jobs/${jobId}/rebuild`, { method: 'POST', body: fd }).then((r) => jsonOrThrow<object>(r));

export const uploadBroll = (jobId: string, file: File) => {
  const fd = new FormData();
  fd.append('file', file);
  return fetch(`/api/jobs/${jobId}/broll`, { method: 'POST', body: fd })
    .then((r) => jsonOrThrow<{ name: string }>(r));
};

export const thumbPreview = (fd: FormData) =>
  fetch('/api/thumb-preview', { method: 'POST', body: fd }).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.blob();
  });

/** 업로드 진행률이 필요해 fetch 대신 XHR — 모바일 업링크에선 수 분 걸린다. */
export function createJobWithProgress(
  fd: FormData,
  onProgress: (loaded: number, total: number) => void,
): Promise<{ job_id: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/jobs');
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      let body: { detail?: string; job_id?: string } = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* 비JSON */ }
      if (xhr.status >= 200 && xhr.status < 300 && body.job_id) resolve({ job_id: body.job_id });
      else reject(new Error(body.detail || xhr.statusText || `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error('네트워크 오류 — 업로드에 실패했습니다'));
    xhr.send(fd);
  });
}
