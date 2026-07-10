// 백엔드 계약 — web/app.py 응답 형태 그대로 (API는 이 마이그레이션에서 동결)

export type JobStatus = 'queued' | 'running' | 'done' | 'error';

export type JobOutputs = {
  subtitled?: string;
  longform?: string;
  shorts?: string[];
  shorts_clean?: string[];
  thumbnail?: string[];
  intro?: string;
  srt?: string;
  note?: string;
};

export type Job = {
  status: JobStatus;
  stage?: string;
  progress?: number;
  outputs?: JobOutputs | null;
  error?: string | null;
  mode?: string | null;
  mode_detected?: string;
  kind?: string;
  source_count?: number | null;
  segment_count?: number;
  notes?: string[];
  llm_usage?: { calls: number; usd: number };
  bgm_track?: string;
  bgm_credit?: string;
};

export type Segment = {
  start: number;
  end: number;
  reason?: string;
  score?: number;
  hook?: string;
  sfx?: string;
  broll?: string;
  clip_start?: number;
  clip_end?: number;
};

export type Analysis = {
  segments: Segment[];
  captions: string[];
  transcript?: { i: number; start: number; text: string }[] | null;
};

export type MusicLib = { moods: Record<string, { name: string; bpm?: number }[]> };
export type SfxItem = { name: string; label?: string };

export type ThumbTemplate = {
  key: string;
  label: string;
  font: string;
  weight: string;
  effect: string;
  color: string;
  bg: string | null;
};

// 편집 상태의 한 구간 — 레거시 edSegs와 동일 필드 (알고리즘 이식 호환)
export type EdSeg = {
  use: boolean;
  start: number;
  end: number;
  clipStart?: number;
  clipEnd?: number;
  caption: string;
  hook: string;
  sfx: string;
  broll: string;
  tpl: Segment; // 재생성 시 원본 필드 계승 — 인덱스 비의존
};

export const SUB_STYLES = ['fade', 'kinetic', 'impact', 'bounce', 'typewriter', 'wave'] as const;
export type SubStyle = (typeof SUB_STYLES)[number];

export type RecentJob = { id: string; ts: number; mode?: string; name?: string; out?: string };
