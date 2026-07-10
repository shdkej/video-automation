// 최근 작업 — localStorage 키·형태는 레거시와 동일(reelroom_recent_jobs) → 이관 시 이력 유지
import { useCallback, useSyncExternalStore } from 'react';
import type { RecentJob } from '@/lib/types';

const KEY = 'reelroom_recent_jobs';
const listeners = new Set<() => void>();
let cache: RecentJob[] | null = null;

function read(): RecentJob[] {
  if (cache) return cache;
  try { cache = JSON.parse(localStorage.getItem(KEY) || '[]') as RecentJob[]; }
  catch { cache = []; }
  return cache;
}

function write(list: RecentJob[]) {
  cache = list;
  localStorage.setItem(KEY, JSON.stringify(list));
  listeners.forEach((fn) => fn());
}

export function useRecentJobs() {
  const jobs = useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => listeners.delete(cb); },
    read,
  );
  const save = useCallback((id: string, meta: Partial<RecentJob> = {}) => {
    write([{ id, ts: Date.now(), ...meta }, ...read().filter((j) => j.id !== id)].slice(0, 15));
  }, []);
  const update = useCallback((id: string, patch: Partial<RecentJob>) => {
    write(read().map((j) => (j.id === id ? { ...j, ...patch } : j)));
  }, []);
  return { jobs, save, update };
}
