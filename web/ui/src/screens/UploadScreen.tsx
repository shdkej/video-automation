// 업로드 화면 — 레거시 form-section 이식 + DESIGN.md 업로드 페이지 패턴.
// 마스트헤드 → 드롭존 → (썸네일 타이틀) → 세부 설정(접힘) → CTA → 최근 작업.
import { toast } from 'sonner';
import { requestNotifyPermission, notifyDone } from '@/lib/notify';
import { useJobPolling } from '@/hooks/useJobPolling';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fmtSize, captureFrameFromFile, isAudio, isVideo, isNoteImg, isNoteMode, MEDIA_RE, NOTE_IMG_RE } from '@/lib/media';
import { useRecentJobs } from '@/hooks/useRecentJobs';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ThumbTitleControls, DEFAULT_THUMB_STATE, thumbStateToForm, type ThumbState } from '@/components/ThumbTitleControls';

const MODE_NOTES: Record<string, string> = {
  auto: '영상 특성(발화·씬·오디오)을 실측해서 분석 방식을 자동으로 고릅니다. 뭘 고를지 모르겠으면 이걸로.',
  scene: 'ffmpeg 씬 감지로 컷 포인트를 찾습니다. API 키 불필요·무료. 자막은 speech 모드에서만 들어갑니다.',
  speech: 'Whisper가 음성을 받아 적고 LLM이 핵심 구간을 고른 뒤 한국어 자막을 입힙니다. .env에 OPENAI_API_KEY 또는 ANTHROPIC_API_KEY 필요.',
  vision: '정적·무음 영상용. 시점별 모자이크 한 장을 비전 LLM이 분석합니다. API 키 필요.',
};
const MODE_CARDS = [
  { mode: 'auto', badge: '추천', desc: '영상을 보고 알아서 판별 — 발화가 있으면 speech, 컷이 있으면 scene, 아니면 vision.' },
  { mode: 'scene', badge: '무료', desc: '컷이 또렷한 편집 영상. ffmpeg 씬 감지, API 키 불필요. AI 장면 자막은 키 있을 때만 생성.' },
  { mode: 'speech', badge: '자막', desc: '음성 위주. Whisper+LLM이 핵심 구간 선정·한국어 자막. API 키 필요.' },
  { mode: 'vision', badge: '비전', desc: '정적·무음 영상. 모자이크를 비전 LLM이 분석. API 키 필요.' },
];
const SUB_OPTIONS = [
  { value: 'fade', label: '애니메이션·페이드 (기본)' },
  { value: 'kinetic', label: '애니메이션·키네틱' },
  { value: 'impact', label: '임팩트 (원워드 슬램·스파크)' },
  { value: 'bounce', label: '바운스 (단어 팝)' },
  { value: 'typewriter', label: '타자기' },
  { value: 'wave', label: '웨이브 (글자 물결)' },
  { value: 'pil', label: '정적 (PIL)' },
  { value: 'off', label: '끄기' },
];
const REMOTION_STYLES = ['fade', 'kinetic', 'impact', 'bounce', 'typewriter', 'wave'];
const KO_COUNT = ['', '한', '두', '세', '네'];
const OUTPUT_KEYS = ['longform', 'shorts', 'thumbnail', 'intro'] as const;
type OutputKey = (typeof OUTPUT_KEYS)[number];

// fetch는 업로드 진행률을 못 주므로 XHR — 모바일 업링크에선 업로드가 수 분 걸린다 (레거시 이식)
function uploadWithProgress(
  url: string,
  fd: FormData,
  onProgress: (loaded: number, total: number) => void,
): Promise<{ job_id: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded, e.total); };
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

function fmtTs(ts: number) {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="font-mono text-[12px] tracking-wide text-muted-foreground">{children}</span>;
}

function StepNum({ n, children }: { n?: string; children: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-baseline gap-2">
      {n && <span className="font-mono text-[13px] text-primary">{n}</span>}
      <span className="text-[16px] font-medium">{children}</span>
    </div>
  );
}

export function UploadScreen({ onSubmitted, onOpenJob }: {
  onSubmitted: (jobId: string) => void;
  onOpenJob: (jobId: string) => void;
}) {
  const { jobs, save } = useRecentJobs();
  const [files, setFiles] = useState<File[]>([]);
  // 기존 잡의 원본 재사용 — 재업로드 생략 (서버가 hardlink)
  const [sourceJob, setSourceJob] = useState<{ id: string; name: string } | null>(null);
  const [thumb, setThumb] = useState<ThumbState>(DEFAULT_THUMB_STATE);
  const [mode, setMode] = useState('auto');
  const [targetMinutes, setTargetMinutes] = useState('3');
  const [shortsCount, setShortsCount] = useState('2');
  const [thumbnailCount, setThumbnailCount] = useState('3');
  const [subMode, setSubMode] = useState('fade');
  const [outputs, setOutputs] = useState<Record<OutputKey, boolean>>({
    longform: true, shorts: true, thumbnail: true, intro: true,
  });
  const [subtitleOnly, setSubtitleOnly] = useState(false);
  const [fx, setFx] = useState({
    shorts_blur: false, shorts_jumpcut: true, shorts_punchin: true, shorts_clean: true,
    scene_captions: true, beat_sync: true, bgm_auto: true,
  });
  const [adv, setAdv] = useState({
    shorts_focus: 'center', shorts_max: '45', shorts_ideal: '25',
    scene_th: '0.3', clip_sec: '6', montage_sec: '2', bgm_vol: '0.3',
  });
  const [bgmFile, setBgmFile] = useState<File | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState({ loaded: 0, total: 0 });
  const [error, setError] = useState('');

  const dzRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const frameCache = useRef<{ file: File | null; blob: Blob | null }>({ file: null, blob: null });

  const noteMode = isNoteMode(files);
  const hasVideo = files.some(isVideo);
  const firstVideo = useMemo(() => files.find(isVideo) ?? null, [files]);

  // 선택한 첫 영상의 프레임을 브라우저에서 뽑아 썸네일 미리보기 바탕으로 — 파일별 캐시
  const getBase = useCallback(async () => {
    if (!firstVideo && sourceJob) return { jobId: sourceJob.id, t: 1.0 };
    if (!firstVideo) return null;
    if (frameCache.current.file !== firstVideo) {
      frameCache.current = { file: firstVideo, blob: await captureFrameFromFile(firstVideo) };
    }
    const blob = frameCache.current.blob;
    return blob
      ? { blob }
      : { note: '영상 프레임을 읽지 못해 임시 배경입니다 — 산출물엔 실제 장면이 들어갑니다' };
  }, [firstVideo, sourceJob]);

  const pickedOutputs = () => OUTPUT_KEYS.filter((k) => outputs[k]);
  const ctaLabel = noteMode
    ? '노트 오버레이 만들기'
    : subtitleOnly
      ? '자막만 입히기'
      : pickedOutputs().length === 0
        ? '산출물을 선택하세요'
        : `${KO_COUNT[pickedOutputs().length]} 가지 만들기`;

  // ---------- 파일 ----------
  const addFiles = (list: FileList | File[]) => {
    const snapshot = Array.from(list); // FileList는 live — input.value 초기화 전에 스냅샷
    if (snapshot.length === 0) {
      toast.error('파일을 읽지 못했습니다 — 다시 선택해주세요');
      return;
    }
    setFiles((prev) => [...prev, ...snapshot]);
    toast(`${snapshot.length}개 파일 추가됨`);
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    dzRef.current?.classList.remove('ring-2');
    const ok = Array.from(e.dataTransfer.files).filter(
      (f) => f.type.startsWith('video/') || f.type.startsWith('audio/') || MEDIA_RE.test(f.name) || NOTE_IMG_RE.test(f.name),
    );
    addFiles(ok);
  };
  const move = (i: number, dir: -1 | 1) => setFiles((prev) => {
    const next = [...prev];
    [next[i], next[i + dir]] = [next[i + dir], next[i]];
    return next;
  });
  const removeAt = (i: number) => setFiles((prev) => prev.filter((_, j) => j !== i));

  // ---------- 제출 ----------
  const buildForm = () => {
    const fd = new FormData();
    files.forEach((f) => fd.append('files', f)); // 순서 보존 = 타임라인 순서
    fd.append('mode', mode);
    fd.append('target_minutes', targetMinutes);
    fd.append('shorts_count', shortsCount);
    fd.append('thumbnail_count', thumbnailCount);
    fd.append('shorts_blur', String(fx.shorts_blur));
    fd.append('shorts_jumpcut', String(fx.shorts_jumpcut));
    fd.append('shorts_punchin', String(fx.shorts_punchin));
    fd.append('shorts_clean', String(fx.shorts_clean));
    fd.append('scene_captions', String(fx.scene_captions));
    fd.append('shorts_focus', adv.shorts_focus);
    fd.append('shorts_max_seconds', adv.shorts_max);
    fd.append('shorts_ideal_seconds', adv.shorts_ideal);
    fd.append('scene_threshold', adv.scene_th);
    fd.append('clip_seconds', adv.clip_sec);
    fd.append('montage_seconds', adv.montage_sec);
    fd.append('bgm_volume', adv.bgm_vol);
    if (bgmFile) fd.append('bgm', bgmFile);
    fd.append('subtitle_only', String(subtitleOnly));
    fd.append('beat_sync', String(fx.beat_sync));
    fd.append('bgm_auto', String(fx.bgm_auto));
    thumbStateToForm(fd, thumb);
    const animated = REMOTION_STYLES.includes(subMode);
    fd.append('no_subtitle', String(subMode === 'off'));
    fd.append('sub_engine', animated ? 'remotion' : 'pil');
    fd.append('sub_style', animated ? subMode : 'fade');
    if (!subtitleOnly) pickedOutputs().forEach((o) => fd.append('outputs', o));
    return fd;
  };

  const runUpload = async (url: string, fd: FormData, jobMode: string, name: string) => {
    requestNotifyPermission(); // 완료 알림 권한 — 제출 제스처 안에서만 요청 가능
    setError('');
    setUploading(true);
    setProgress({ loaded: 0, total: 0 });
    try {
      const { job_id } = await uploadWithProgress(url, fd, (loaded, total) => setProgress({ loaded, total }));
      save(job_id, { mode: jobMode, name });
      onSubmitted(job_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const submit = () => {
    if (files.length === 0 && !sourceJob) {
      toast.error('먼저 영상을 추가해주세요');
      dzRef.current?.classList.add('ring-2');
      setTimeout(() => dzRef.current?.classList.remove('ring-2'), 600);
      return;
    }
    if (files.length === 0 && sourceJob) {
      if (!subtitleOnly && pickedOutputs().length === 0) {
        setError('산출물을 하나 이상 선택해주세요.');
        return;
      }
      const fd = buildForm();
      fd.append('source_job', sourceJob.id);
      void runUpload('/api/jobs', fd, mode, `${sourceJob.name} (소스 재사용)`);
      return;
    }
    if (noteMode) {
      const fd = new FormData();
      files.forEach((f) => fd.append('files', f)); // 순서 = 페이지 순서
      const vid = files.find(isVideo);
      void runUpload('/api/note-jobs', fd, 'note', vid ? vid.name : files[0].name);
      return;
    }
    if (!subtitleOnly && pickedOutputs().length === 0) {
      setError('산출물을 하나 이상 선택해주세요.');
      return;
    }
    const name = files.length > 1 ? `${files[0].name} 외 ${files.length - 1}개` : files[0].name;
    void runUpload('/api/jobs', buildForm(), mode, name);
  };

  const pct = progress.total ? Math.round((progress.loaded / progress.total) * 100) : 0;
  const progressText = pct >= 100
    ? '업로드 완료 — 처리 대기 중…'
    : `업로드 중 · ${pct}% (${fmtSize(progress.loaded)} / ${fmtSize(progress.total)})`;

  return (
    <div className="mx-auto max-w-[920px] px-4 py-10">
      {jobs[0] && <JobBanner key={jobs[0].id} jobId={jobs[0].id} onOpen={() => onOpenJob(jobs[0].id)} />}
      {/* 마스트헤드 — Fraunces 허용 유일 구간 */}
      <header className="mb-8 space-y-3">
        <span className="inline-flex items-center gap-2 rounded-full border bg-card px-3 py-1 font-mono text-[11px] text-muted-foreground">
          <span className="size-1.5 animate-pulse rounded-full bg-destructive" />
          LOCAL · 영상은 이 컴퓨터를 떠나지 않습니다
        </span>
        <h1 className="font-display text-[28px] leading-tight">
          Reel<span className="text-primary italic"> Room</span>
        </h1>
        <p className="text-[14px] text-muted-foreground">
          영상 하나로 롱폼 · 숏츠 · 썸네일 · 인트로 네 가지를 한 번에. 잘라내는 건 우리가 합니다.
        </p>
      </header>

      <div className="space-y-6">
        {/* 드롭존 */}
        <div>
          <StepNum n="01">소스 영상</StepNum>
          <Card
            ref={dzRef}
            role="button"
            tabIndex={0}
            aria-label="영상 파일 추가"
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInputRef.current?.click(); } }}
            onDragOver={(e) => { e.preventDefault(); dzRef.current?.classList.add('ring-2'); }}
            onDragLeave={(e) => { e.preventDefault(); dzRef.current?.classList.remove('ring-2'); }}
            onDrop={onDrop}
            className="cursor-pointer gap-0 py-8 text-center ring-primary/60 transition-shadow"
          >
            <label htmlFor="rr-file-input" className="block cursor-pointer">
            <input
              id="rr-file-input"
              ref={fileInputRef}
              type="file"
              accept="video/*,audio/*,image/*"
              multiple
              className="sr-only"
              onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ''; }}
            />
            <div className="text-[28px] text-primary">⌖</div>
            <p className="mt-2 text-[16px]">
              영상을 끌어다 놓거나 <span className="text-primary">클릭해 선택</span>
            </p>
            <p className="mx-auto mt-2 max-w-[520px] px-4 text-[13px] text-muted-foreground">
              여러 영상은 <strong className="text-foreground">순서대로 이어붙이고</strong>, 음성 파일(mp3·m4a·wav)을 같이 올리면{' '}
              <strong className="text-foreground">영상에 입힙니다</strong>. 이미지(png·jpg)를 함께 올리면{' '}
              <strong className="text-foreground">영상 위에 노트 페이지처럼 띄웁니다</strong>
            </p>
            </label>
          </Card>

          {sourceJob && files.length === 0 && (
            <div className="mt-3 flex items-center gap-3 rounded-md border border-primary/50 bg-card px-3 py-2">
              <span className="font-mono text-[12px] text-primary">↺ 소스 재사용</span>
              <span className="min-w-0 flex-1 truncate text-[13px]">{sourceJob.name}</span>
              <Button type="button" variant="ghost" size="icon-sm" aria-label="재사용 해제"
                onClick={() => setSourceJob(null)} className="text-destructive hover:text-destructive">✕</Button>
            </div>
          )}

          {/* 파일 목록 */}
          {files.length > 0 && (
            <ul className="mt-3 space-y-2">
              {files.map((f, i) => {
                const audio = isAudio(f);
                const img = isNoteImg(f);
                const vn = files.slice(0, i + 1).filter(isVideo).length;
                return (
                  <li key={`${f.name}-${i}`} className="flex items-center gap-3 rounded-md border bg-card px-3 py-2">
                    <span className={cn('w-6 shrink-0 text-center font-mono text-[13px]', audio ? 'text-muted-foreground' : 'text-primary')}>
                      {audio ? '♪' : img ? '🗒' : vn}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[13px]">{f.name}</span>
                    <span className="shrink-0 font-mono text-[12px] text-muted-foreground">{fmtSize(f.size)}</span>
                    <span className="flex shrink-0 items-center gap-1">
                      <Button type="button" variant="ghost" size="icon-sm" disabled={i === 0} onClick={() => move(i, -1)} aria-label="위로">↑</Button>
                      <Button type="button" variant="ghost" size="icon-sm" disabled={i === files.length - 1} onClick={() => move(i, 1)} aria-label="아래로">↓</Button>
                      <Button type="button" variant="ghost" size="icon-sm" onClick={() => removeAt(i)} aria-label="제거" className="text-destructive hover:text-destructive">✕</Button>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}

          {noteMode && (
            <p className="mt-3 rounded-md border bg-card px-3 py-2 text-[13px] text-muted-foreground">
              🗒 노트 오버레이 모드 — 이미지가 올린 순서대로 영상 위에 페이지처럼 떠오릅니다. 다른 산출물 없이 이 영상 하나만 만듭니다.
            </p>
          )}
        </div>

        {/* 썸네일 타이틀 — 영상이 있고 노트 모드가 아닐 때 */}
        {(hasVideo || !!sourceJob) && !noteMode && (
          <div>
            <StepNum n="02">썸네일 미리보기</StepNum>
            <Card className="py-4">
              <div className="px-4">
                <ThumbTitleControls state={thumb} onChange={setThumb} getBase={getBase} />
              </div>
            </Card>
          </div>
        )}

        {/* 세부 설정 — 기본 접힘 */}
        <div>
          <Button
            type="button"
            variant="ghost"
            onClick={() => setSettingsOpen((v) => !v)}
            className="w-full justify-between px-3"
          >
            <span>세부 설정 <span className="text-muted-foreground">— 평소엔 기본값으로 충분</span></span>
            <span className="font-mono text-muted-foreground">{settingsOpen ? '−' : '+'}</span>
          </Button>

          {settingsOpen && (
            <Card className="mt-3 gap-5 py-5">
              <div className="space-y-5 px-4">
                {/* 분석 방식 */}
                <div>
                  <StepNum>분석 방식</StepNum>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {MODE_CARDS.map((c) => (
                      <button
                        key={c.mode}
                        type="button"
                        onClick={() => setMode(c.mode)}
                        className={cn(
                          'rounded-lg border bg-background p-3 text-left transition-colors',
                          mode === c.mode ? 'border-primary' : 'hover:border-ring',
                        )}
                      >
                        <div className="mb-1 flex items-center gap-2">
                          <span className="font-mono text-[14px]">{c.mode}</span>
                          <span className="rounded-full bg-secondary px-2 py-0.5 text-[11px] text-secondary-foreground">{c.badge}</span>
                        </div>
                        <p className="text-[12px] text-muted-foreground">{c.desc}</p>
                      </button>
                    ))}
                  </div>
                  <p className="mt-2 text-[12px] text-muted-foreground">{MODE_NOTES[mode]}</p>
                </div>

                <Separator />

                {/* 산출물 옵션 */}
                <div>
                  <StepNum>산출물</StepNum>
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                    <label className="space-y-2">
                      <Label>롱폼 목표 (분)</Label>
                      <Input type="number" min="0.1" step="0.1" value={targetMinutes} onChange={(e) => setTargetMinutes(e.target.value)} className="bg-background" />
                    </label>
                    <label className="space-y-2">
                      <Label>숏츠 (개)</Label>
                      <Input type="number" min="0" max="10" value={shortsCount} onChange={(e) => setShortsCount(e.target.value)} className="bg-background" />
                    </label>
                    <label className="space-y-2">
                      <Label>썸네일 (장)</Label>
                      <Input type="number" min="1" max="10" value={thumbnailCount} onChange={(e) => setThumbnailCount(e.target.value)} className="bg-background" />
                    </label>
                    <label className="space-y-2">
                      <Label>자막 스타일</Label>
                      <Select value={subMode} onValueChange={setSubMode}>
                        <SelectTrigger className="w-full bg-background"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {SUB_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </label>
                  </div>

                  <div className={cn('mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4', subtitleOnly && 'opacity-35')}>
                    {([['longform', '롱폼'], ['shorts', '숏츠'], ['thumbnail', '썸네일'], ['intro', '인트로']] as [OutputKey, string][]).map(([k, l]) => (
                      <label key={k} className="flex items-center gap-2 text-[13px]">
                        <Switch checked={outputs[k]} disabled={subtitleOnly} onCheckedChange={(v) => setOutputs((p) => ({ ...p, [k]: v }))} />
                        {l}
                      </label>
                    ))}
                  </div>
                  <label className="mt-3 flex items-center gap-2 text-[13px]">
                    <Switch checked={subtitleOnly} onCheckedChange={setSubtitleOnly} />
                    자막만 (컷 편집 없이 원본 그대로 — 완성된 숏츠용)
                  </label>
                </div>

                <Separator />

                {/* 효과 */}
                <div>
                  <StepNum>효과</StepNum>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    {([
                      ['shorts_blur', '숏츠 흐린 배경(좌우 안 잘림)'],
                      ['shorts_jumpcut', '점프컷(무음 제거)'],
                      ['shorts_punchin', '펀치인(줌 강조)'],
                      ['shorts_clean', '클린 버전 함께(자막 없는 동일 컷)'],
                      ['scene_captions', 'AI 장면 자막(무발화 영상, scene·vision)'],
                      ['beat_sync', '비트 싱크(음악 리듬에 컷·줌 전환)'],
                      ['bgm_auto', '자동 BGM(라이브러리에서 무드 선곡)'],
                    ] as [keyof typeof fx, string][]).map(([k, l]) => (
                      <label key={k} className="flex items-center gap-2 text-[13px]">
                        <Switch checked={fx[k]} onCheckedChange={(v) => setFx((p) => ({ ...p, [k]: v }))} />
                        {l}
                      </label>
                    ))}
                  </div>
                </div>

                <Separator />

                {/* 고급 */}
                <div>
                  <StepNum>고급</StepNum>
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                    <label className="space-y-2">
                      <Label>세로 크롭 초점</Label>
                      <Select value={adv.shorts_focus} onValueChange={(v) => setAdv((p) => ({ ...p, shorts_focus: v }))}>
                        <SelectTrigger className="w-full bg-background"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="center">중앙</SelectItem>
                          <SelectItem value="left">왼쪽</SelectItem>
                          <SelectItem value="right">오른쪽</SelectItem>
                        </SelectContent>
                      </Select>
                    </label>
                    <label className="space-y-2">
                      <Label>숏츠 최대 (초)</Label>
                      <Input type="number" min="10" max="90" step="5" value={adv.shorts_max} onChange={(e) => setAdv((p) => ({ ...p, shorts_max: e.target.value }))} className="bg-background" />
                    </label>
                    <label className="space-y-2">
                      <Label>숏츠 적정 (초)</Label>
                      <Input type="number" min="5" max="60" step="5" value={adv.shorts_ideal} onChange={(e) => setAdv((p) => ({ ...p, shorts_ideal: e.target.value }))} className="bg-background" />
                    </label>
                    <label className="space-y-2">
                      <Label>씬 감도 (낮을수록 민감)</Label>
                      <Input type="number" min="0.05" max="0.9" step="0.05" value={adv.scene_th} onChange={(e) => setAdv((p) => ({ ...p, scene_th: e.target.value }))} className="bg-background" />
                    </label>
                    <label className="space-y-2">
                      <Label>씬 클립 길이 (초)</Label>
                      <Input type="number" min="2" max="20" value={adv.clip_sec} onChange={(e) => setAdv((p) => ({ ...p, clip_sec: e.target.value }))} className="bg-background" />
                    </label>
                    <label className="space-y-2">
                      <Label>몽타주 클립 (초·0=전체)</Label>
                      <Input type="number" min="0" max="10" step="0.5" value={adv.montage_sec} onChange={(e) => setAdv((p) => ({ ...p, montage_sec: e.target.value }))} className="bg-background" />
                    </label>
                    <label className="space-y-2">
                      <Label>BGM 파일</Label>
                      <Input type="file" accept="audio/*" onChange={(e) => setBgmFile(e.target.files?.[0] ?? null)} className="bg-background" />
                    </label>
                    <label className="space-y-2">
                      <Label>BGM 볼륨</Label>
                      <Input type="number" min="0.05" max="1" step="0.05" value={adv.bgm_vol} onChange={(e) => setAdv((p) => ({ ...p, bgm_vol: e.target.value }))} className="bg-background" />
                    </label>
                  </div>
                </div>
              </div>
            </Card>
          )}
        </div>

        {/* CTA */}
        <div className="space-y-2">
          <Button type="button" onClick={submit} disabled={uploading} className="h-11 w-full text-[14px]">
            {uploading ? progressText : ctaLabel} {!uploading && '→'}
          </Button>
          {uploading && (
            <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-primary transition-[width]" style={{ width: `${pct}%` }} />
            </div>
          )}
          {error && (
            <p className="rounded-md border border-destructive px-3 py-2 text-[13px] text-destructive">{error}</p>
          )}
        </div>

        {/* 최근 작업 */}
        {jobs.length > 0 && (
          <div>
            <StepNum>최근 작업</StepNum>
            <ul className="space-y-2">
              {jobs.map((j) => (
                <li key={j.id}>
                  <button
                    type="button"
                    onClick={() => onOpenJob(j.id)}
                    className="flex w-full items-center gap-3 rounded-md border bg-card px-3 py-2 text-left transition-colors hover:border-ring"
                  >
                    <span className="min-w-0 flex-1 truncate text-[13px]">{j.name || j.mode || '?'}</span>
                    <span className="shrink-0 font-mono text-[12px] text-muted-foreground">{fmtTs(j.ts)}</span>
                    {j.mode && <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 text-[11px] text-secondary-foreground">{j.mode}</span>}
                  </button>
                  {j.mode !== 'note' && (
                    <div className="mt-1 flex justify-end">
                      <Button type="button" variant="ghost" size="sm"
                        onClick={() => {
                          setSourceJob({ id: j.id, name: j.name || j.id });
                          window.scrollTo({ top: 0, behavior: 'smooth' });
                          toast(`"${j.name || j.id}" 원본을 재사용합니다 — 옵션 조정 후 만들기`);
                        }}>
                        ↺ 소스 재사용
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
          <p className="mt-8 text-center font-mono text-[10px] text-muted-foreground/60">build {__BUILD__}</p>
    </div>
  );
}


// 업로드 화면에 떠 있는 동안 최근 잡을 지켜본다 — 처리 중이면 상단 배너로 복귀 도선
function JobBanner({ jobId, onOpen }: { jobId: string; onOpen: () => void }) {
  const { job } = useJobPolling(jobId);
  const doneNotified = useRef(false);
  useEffect(() => {
    if (job?.status === 'done' && !doneNotified.current) {
      doneNotified.current = true;
      notifyDone(true, '백그라운드 작업이 끝났습니다');
    }
  }, [job]);
  if (!job) return null;
  const running = job.status === 'running' || job.status === 'queued';
  if (!running && !doneNotified.current) return null;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="fixed inset-x-0 top-0 z-50 border-b border-border bg-popover/95 px-4 py-2.5 text-center font-mono text-xs text-accent backdrop-blur"
    >
      {running
        ? `⏳ 작업 처리 중 · ${job.progress || 0}% — 보러 가기`
        : '✓ 작업 완성 — 보러 가기'}
    </button>
  );
}
