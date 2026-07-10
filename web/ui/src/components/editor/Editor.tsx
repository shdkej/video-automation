// 편집실 셸 — 앱바 · 플레이어 · 타임라인 · 도구 시트(모바일)/사이드 패널(데스크톱) · 도구바.
// CapCut의 편집 패러다임 × Reel Room 다크룸 스킨. 산출물은 내보내기 탭에.
import { useCallback, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft, Captions, Image as ImageIcon, Info, Music, Package, Scissors, Sparkles,
} from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger,
} from '@/components/ui/sheet';
import { ThumbTitleControls } from '@/components/ThumbTitleControls';
import { useEditor } from '@/hooks/useEditor';
import type { Job } from '@/lib/types';
import { cn } from '@/lib/utils';
import { ClipPanel } from './ClipPanel';
import { ExportPanel } from './ExportPanel';
import { PlayerBay, outVideosOf } from './PlayerBay';
import { Timeline, outTimelineMap } from './Timeline';
import { FxPanel, MusicPanel, SubtitlePanel } from './panels';

type Tool = 'clip' | 'subtitle' | 'music' | 'thumb' | 'fx' | 'export';

const TOOLS: { id: Tool; label: string; Icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'clip', label: '클립', Icon: Scissors },
  { id: 'subtitle', label: '자막', Icon: Captions },
  { id: 'music', label: '음악', Icon: Music },
  { id: 'thumb', label: '썸네일', Icon: ImageIcon },
  { id: 'fx', label: '효과', Icon: Sparkles },
  { id: 'export', label: '내보내기', Icon: Package },
];

export function Editor({
  jobId, job, rebuilding, onReset, onApplied,
}: {
  jobId: string;
  job: Job;
  /** 재생성 진행 정보 — 있으면 인라인 오버레이 표시 (컨텍스트 유지) */
  rebuilding: { stage: string; progress: number } | null;
  onReset: () => void;
  onApplied: () => void;
}) {
  const ed = useEditor(jobId);
  const [tool, setTool] = useState<Tool>('clip');
  const [sheetOpen, setSheetOpen] = useState(false);
  const [playTime, setPlayTime] = useState(0);
  const [mappable, setMappable] = useState(false);
  const [applying, setApplying] = useState(false);
  const seekRef = useRef<((t: number) => void) | null>(null);

  const outs = useMemo(() => outVideosOf(job, ed.montage), [job, ed.montage]);

  const openTool = useCallback((t: Tool) => {
    setTool((prev) => {
      if (prev === t && sheetOpen) { setSheetOpen(false); return prev; }
      setSheetOpen(true);
      return t;
    });
  }, [sheetOpen]);

  const selectSeg = useCallback((i: number) => {
    ed.patch({ sel: i });
    setTool('clip');
    setSheetOpen(true);
    const seg = ed.st.segs[i];
    if (mappable && seg?.use && seekRef.current) {
      seekRef.current(outTimelineMap(ed.st.segs)[i].t0 + 0.01);
    }
  }, [ed, mappable]);

  const apply = useCallback(async () => {
    setApplying(true);
    try {
      await ed.apply();
      toast('적용 시작 — 재생성이 끝나면 여기서 바로 이어집니다');
      onApplied();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setApplying(false);
    }
  }, [ed, onApplied]);

  const metaBits = useMemo(() => {
    const bits: string[] = [];
    if (job.source_count && job.source_count > 1) bits.push(`${job.source_count}개 소스 결합`);
    if (job.segment_count != null) bits.push(`선정 구간 ${job.segment_count}개`);
    if (job.mode_detected) bits.push(`자동 판별 → ${job.mode_detected}`);
    (job.notes || []).forEach((n) => bits.push(n));
    if (job.llm_usage?.calls) bits.push(`LLM ~$${job.llm_usage.usd} (${job.llm_usage.calls}콜, 추정)`);
    if (job.bgm_track) bits.push(`BGM ${job.bgm_track}${job.bgm_credit ? ` (${job.bgm_credit})` : ''}`);
    return bits;
  }, [job]);

  const applyBtn = (
    <Button
      size="sm"
      disabled={!ed.dirty || applying || !!rebuilding}
      onClick={apply}
      className="shrink-0"
    >
      {applying ? '저장 중…' : '적용'}
      {ed.dirty && !applying && (
        <Badge variant="secondary" className="ml-1 h-4 min-w-4 rounded-full px-1 font-mono text-[10px]">
          {ed.dirtyCount}
        </Badge>
      )}
    </Button>
  );

  const panel = ed.st.loading ? (
    <p className="py-6 text-center font-mono text-xs text-muted-foreground">편집 데이터 불러오는 중…</p>
  ) : ed.st.loadError ? (
    <p className="py-6 text-center font-mono text-xs text-destructive">{ed.st.loadError}</p>
  ) : (
    <>
      {tool === 'clip' && (
        <ClipPanel jobId={jobId} segs={ed.st.segs} sel={ed.st.sel} sfxLib={ed.st.sfxLib}
          onPatch={ed.patchSeg} onAddCut={ed.addCut} />
      )}
      {tool === 'subtitle' && (
        <SubtitlePanel
          style={ed.st.style} subScale={ed.st.subScale} transcript={ed.st.transcript}
          onStyle={(s) => ed.patch({ style: s })}
          onScale={(n) => ed.patch({ subScale: n })}
          onTranscript={(idx, text) => ed.patch({
            transcript: ed.st.transcript.map((t, j) => (j === idx ? { ...t, text } : t)),
          })}
        />
      )}
      {tool === 'music' && (
        <MusicPanel musicLib={ed.st.musicLib} currentTrack={job.bgm_track}
          choice={ed.st.bgmChoice} vol={ed.st.bgmVol}
          onChoice={(v) => ed.patch({ bgmChoice: v })} onVol={(v) => ed.patch({ bgmVol: v })} />
      )}
      {tool === 'thumb' && (
        <ThumbTitleControls
          state={ed.st.thumb}
          onChange={(t) => ed.patch({ thumb: t })}
          getBase={async () => {
            const seg = ed.st.segs[0];
            if (!seg) return null;
            return { jobId, t: (seg.start + seg.end) / 2 };
          }}
          autoText={() => {
            const segs = ed.st.analysis?.segments || [];
            const scored = segs.filter((s) => s.score != null && s.hook);
            if (scored.length) {
              return scored.reduce((a, b) => (Number(a.score) >= Number(b.score) ? a : b)).hook || '';
            }
            return segs.find((s) => s.hook)?.hook
              || (ed.st.analysis?.captions || []).find((c) => c && c.trim())?.trim()
              || '';
          }}
        />
      )}
      {tool === 'fx' && <FxPanel fx={ed.st.fx} onFx={(p) => ed.patch({ fx: { ...ed.st.fx, ...p } })} />}
      {tool === 'export' && <ExportPanel jobId={jobId} job={job} />}
    </>
  );

  return (
    <div className="mx-auto max-w-[980px] px-4 pb-6 pt-3 max-sm:px-0 max-sm:pt-0">
      {/* 앱바 */}
      <div className="sticky top-0 z-40 mb-2.5 flex items-center gap-2 border-b border-border bg-background/95 px-1 py-2 backdrop-blur max-sm:px-3">
        <Button variant="ghost" size="sm" onClick={onReset}>
          <ArrowLeft className="size-4" /> 새 영상
        </Button>
        <span className="min-w-0 flex-1 truncate text-center font-mono text-xs text-muted-foreground">
          {job.mode || ''} · {job.status === 'done' ? '완성' : job.stage}
        </span>
        <Sheet>
          <SheetTrigger asChild>
            <Button variant="ghost" size="sm" aria-label="작업 정보"><Info className="size-4" /></Button>
          </SheetTrigger>
          <SheetContent side="bottom" className="rounded-t-lg">
            <SheetHeader>
              <SheetTitle className="font-mono text-sm">작업 정보</SheetTitle>
              <SheetDescription className="sr-only">분석·산출 메타데이터</SheetDescription>
            </SheetHeader>
            <ul className="space-y-1.5 px-4 pb-6 font-mono text-xs text-muted-foreground">
              {metaBits.map((b, i) => <li key={i}>· {b}</li>)}
            </ul>
          </SheetContent>
        </Sheet>
        {applyBtn}
      </div>

      <div className="rounded-xl border border-border bg-card max-sm:rounded-none max-sm:border-x-0">
        {/* 플레이어 + 재생성 오버레이 */}
        <div className="relative">
          <PlayerBay
            jobId={jobId} outs={outs}
            onTime={setPlayTime} onMappableChange={setMappable}
            seekRef={seekRef}
          />
          {rebuilding && (
            <div className="absolute inset-0 z-20 grid place-items-center bg-black/72 backdrop-blur-sm">
              <div className="w-56 space-y-3 text-center">
                <p className="font-mono text-xs text-accent">{rebuilding.stage} · {rebuilding.progress}%</p>
                <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                  <div className="h-full bg-primary transition-[width] duration-500"
                    style={{ width: `${rebuilding.progress}%` }} />
                </div>
                <p className="font-mono text-[11px] text-muted-foreground">적용 중 — 이 화면에서 그대로 이어집니다</p>
              </div>
            </div>
          )}
        </div>

        {/* 타임라인 */}
        {!ed.st.loading && !ed.st.loadError && ed.st.segs.length > 0 && (
          <Timeline
            jobId={jobId} segs={ed.st.segs} sel={ed.st.sel} sfxLib={ed.st.sfxLib}
            playTime={playTime} showPlayhead={mappable} onSelect={selectSeg}
          />
        )}

        {/* 데스크톱: 인라인 패널 / 모바일: 시트 */}
        <div className={cn('px-4 py-4 max-sm:hidden', rebuilding && 'pointer-events-none opacity-50')}>
          {panel}
        </div>

        {/* 도구바 */}
        <nav className="sticky bottom-0 z-30 flex border-t border-border bg-background shadow-[0_-8px_24px_rgba(0,0,0,0.5)] sm:static sm:shadow-none">
          {TOOLS.map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => openTool(id)}
              className={cn(
                'flex min-h-[52px] flex-1 flex-col items-center justify-center gap-0.5 border-t-2 font-mono text-[11px] tracking-wide transition-colors',
                tool === id ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon className="size-[17px]" />
              {label}
            </button>
          ))}
        </nav>
      </div>

      {/* 모바일 도구 시트 — 도구바 위로, dim 없음 (플레이어를 보면서 조작) */}
      <div
        className={cn(
          'fixed inset-x-0 bottom-[52px] z-20 max-h-[44vh] overflow-y-auto rounded-t-lg border-t border-border bg-popover px-4 pb-5 pt-2 transition-transform duration-200 sm:hidden',
          sheetOpen && !rebuilding ? 'translate-y-0' : 'translate-y-full',
        )}
      >
        <button
          type="button"
          aria-label="패널 닫기"
          onClick={() => setSheetOpen(false)}
          className="mx-auto mb-2.5 block h-1 w-9 rounded-full bg-muted-foreground/40"
        />
        {panel}
      </div>
    </div>
  );
}

