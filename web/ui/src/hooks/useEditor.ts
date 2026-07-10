// 편집실 상태 — 분석 데이터 로드, 구간 편집, dirty 추적, 적용(재생성).
// 알고리즘은 레거시 app.js(initEditor/doRebuild/apply)에서 그대로 이식.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getAnalysis, getMusic, getSfx, rebuild, saveAnalysis } from '@/lib/api';
import type { Analysis, EdSeg, MusicLib, SfxItem, SubStyle } from '@/lib/types';
import { DEFAULT_THUMB_STATE, type ThumbState, thumbStateToForm } from '@/components/ThumbTitleControls';

export type FxState = {
  jumpcut: boolean;
  punchin: boolean;
  blur: boolean;
  clean: boolean;
  shortsCount: number;
  thumbCount: number;
};

export type EditorState = {
  loading: boolean;
  loadError: string | null;
  analysis: Analysis | null;
  musicLib: MusicLib | null;
  sfxLib: SfxItem[];
  segs: EdSeg[];
  sel: number;
  style: SubStyle | 'pil' | 'off';
  subScale: number;
  bgmChoice: string;
  bgmVol: number;
  fx: FxState;
  thumb: ThumbState;
  transcript: { i: number; start: number; text: string }[];
};

const DEFAULT_FX: FxState = {
  jumpcut: true, punchin: true, blur: false, clean: true, shortsCount: 2, thumbCount: 3,
};

function segsFromAnalysis(a: Analysis): EdSeg[] {
  return (a.segments || []).map((s, i) => ({
    use: true,
    start: s.start,
    end: s.end,
    clipStart: s.clip_start,
    clipEnd: s.clip_end,
    caption: (a.captions || [])[i] || '',
    hook: s.hook || '',
    sfx: s.sfx || '',
    broll: s.broll || '',
    tpl: s,
  }));
}

export const isMontageSegs = (segs: { tpl?: { reason?: string } }[]) =>
  segs.length > 0 && String(segs[0].tpl?.reason || '').startsWith('montage');

export function useEditor(jobId: string) {
  const [st, setSt] = useState<EditorState>({
    loading: true, loadError: null, analysis: null, musicLib: null, sfxLib: [],
    segs: [], sel: -1, style: 'fade', subScale: 1, bgmChoice: 'auto', bgmVol: 0.3,
    fx: DEFAULT_FX, thumb: DEFAULT_THUMB_STATE, transcript: [],
  });
  // dirty 판정 기준 — 마지막 렌더(로드/적용 시점)의 스냅샷
  const baseline = useRef<string>('');

  const snapshot = useCallback((s: EditorState) => JSON.stringify({
    segs: s.segs.map(({ tpl: _tpl, ...rest }) => rest),
    style: s.style, subScale: s.subScale, bgmChoice: s.bgmChoice, bgmVol: s.bgmVol,
    fx: s.fx, thumb: s.thumb, transcript: s.transcript.map((t) => t.text),
  }), []);

  useEffect(() => {
    let stopped = false;
    (async () => {
      try {
        const [analysis, musicLib, sfxLib] = await Promise.all([
          getAnalysis(jobId),
          getMusic().catch(() => ({ moods: {} }) as MusicLib),
          getSfx().catch(() => [] as SfxItem[]),
        ]);
        if (stopped) return;
        setSt((prev) => {
          const next: EditorState = {
            ...prev,
            loading: false,
            analysis,
            musicLib,
            sfxLib,
            segs: segsFromAnalysis(analysis),
            transcript: analysis.transcript || [],
          };
          baseline.current = snapshot(next);
          return next;
        });
      } catch (e) {
        if (!stopped) setSt((p) => ({ ...p, loading: false, loadError: (e as Error).message }));
      }
    })();
    return () => { stopped = true; };
  }, [jobId, snapshot]);

  const patch = useCallback((p: Partial<EditorState>) => setSt((s) => ({ ...s, ...p })), []);
  const patchSeg = useCallback((i: number, p: Partial<EdSeg>) =>
    setSt((s) => ({ ...s, segs: s.segs.map((sg, j) => (j === i ? { ...sg, ...p } : sg)) })), []);

  const addCut = useCallback((i: number) => {
    setSt((s) => {
      const src = s.segs[i];
      if (!src || src.clipStart == null || src.clipEnd == null) return s;
      const span = src.clipEnd - src.clipStart;
      const len = Math.min(Math.max(0.4, src.end - src.start), span);
      let ns = src.end, ne = src.end + len;
      if (ne > src.clipEnd) { ne = src.start; ns = src.start - len; }
      if (ns < src.clipStart) { ns = src.clipStart; ne = Math.min(src.clipEnd, ns + len); }
      const copy: EdSeg = {
        use: true, start: Math.round(ns * 10) / 10, end: Math.round(ne * 10) / 10,
        clipStart: src.clipStart, clipEnd: src.clipEnd,
        caption: '', hook: '', sfx: '', broll: '', tpl: src.tpl,
      };
      const segs = [...s.segs];
      segs.splice(i + 1, 0, copy);
      return { ...s, segs, sel: i + 1 };
    });
  }, []);

  const dirty = snapshot(st) !== baseline.current;
  const dirtyCount = useMemo(() => {
    if (!dirty) return 0;
    try {
      const a = JSON.parse(baseline.current || '{}');
      const b = JSON.parse(snapshot(st));
      let n = 0;
      for (const k of Object.keys(b)) {
        if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) n += 1;
      }
      return Math.max(1, n);
    } catch { return 1; }
  }, [dirty, snapshot, st]);

  /** 적용 — 교정 저장 후 재생성 시작. 성공 시 호출부가 폴링을 재개한다. */
  const apply = useCallback(async () => {
    const segments: object[] = [];
    const captions: string[] = [];
    for (let i = 0; i < st.segs.length; i++) {
      const s = st.segs[i];
      if (!s.use) continue;
      if (!(s.start < s.end)) throw new Error(`구간 ${i + 1}: 시작이 끝보다 앞서야 합니다`);
      const seg: Record<string, unknown> = { ...s.tpl, start: s.start, end: s.end };
      if (s.clipStart != null) { seg.clip_start = s.clipStart; seg.clip_end = s.clipEnd; }
      if (s.hook.trim()) seg.hook = s.hook.trim(); else delete seg.hook;
      if (s.sfx) seg.sfx = s.sfx; else delete seg.sfx;
      if (s.broll) seg.broll = s.broll; else delete seg.broll;
      segments.push(seg);
      captions.push(s.caption.trim());
    }
    if (!segments.length) throw new Error('구간을 최소 1개는 남겨야 합니다');

    await saveAnalysis(jobId, {
      segments, captions,
      transcript: st.transcript.map((t) => ({ i: t.i, text: t.text })),
    });

    const fd = new FormData();
    fd.append('shorts_count', String(st.fx.shortsCount));
    fd.append('thumbnail_count', String(st.fx.thumbCount));
    fd.append('shorts_blur', String(st.fx.blur));
    fd.append('shorts_jumpcut', String(st.fx.jumpcut));
    fd.append('shorts_punchin', String(st.fx.punchin));
    fd.append('shorts_clean', String(st.fx.clean));
    fd.append('bgm_volume', String(st.bgmVol));
    fd.append('bgm_choice', st.bgmChoice);
    fd.append('sub_scale', String(st.subScale));
    const animated = !['pil', 'off'].includes(st.style);
    fd.append('no_subtitle', String(st.style === 'off'));
    fd.append('sub_engine', animated ? 'remotion' : 'pil');
    fd.append('sub_style', animated ? st.style : 'fade');
    thumbStateToForm(fd, st.thumb);
    // 부분 재생성 — 바뀐 것만 다시 만든다. 클립 컷 수정이 썸네일까지 전부
    // 재생성하지 않도록: 영상 계열 변경 → 영상 산출물, 썸네일 변경 → 썸네일만.
    try {
      const a = JSON.parse(baseline.current || '{}');
      const b = JSON.parse(snapshot(st));
      const changed = new Set(Object.keys(b).filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k])));
      const fxA = (a.fx || {}) as Record<string, unknown>;
      const fxB = (b.fx || {}) as Record<string, unknown>;
      const fxVideo = ['jumpcut', 'punchin', 'blur', 'clean', 'shortsCount']
        .some((k) => fxA[k] !== fxB[k]);
      const videoChanged = fxVideo
        || ['segs', 'style', 'subScale', 'bgmChoice', 'bgmVol', 'transcript'].some((k) => changed.has(k));
      const outs: string[] = [];
      if (videoChanged) outs.push('longform', 'shorts', 'intro');
      if (changed.has('thumb') || fxA.thumbCount !== fxB.thumbCount) outs.push('thumbnail');
      (outs.length ? outs : ['longform', 'shorts', 'intro']).forEach((o) => fd.append('outputs', o));
    } catch {
      // 진단 실패 시 전체 재생성 (outputs 미지정 = 서버 기본 전체)
    }
    await rebuild(jobId, fd);
    baseline.current = snapshot(st);
  }, [jobId, snapshot, st]);

  return { st, patch, patchSeg, addCut, dirty, dirtyCount, apply, montage: isMontageSegs(st.segs) };
}
