// 자막·음악·효과 패널 — 단일 선택은 ToggleGroup, boolean은 Switch (DESIGN.md)
import { useRef, useState } from 'react';
import { Play } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { musicUrl } from '@/lib/api';
import type { MusicLib, SubStyle } from '@/lib/types';
import type { FxState } from '@/hooks/useEditor';
import { cn } from '@/lib/utils';

const STYLES: { v: SubStyle | 'pil' | 'off'; label: string; demo?: boolean }[] = [
  { v: 'fade', label: '페이드', demo: true },
  { v: 'kinetic', label: '키네틱', demo: true },
  { v: 'impact', label: '임팩트', demo: true },
  { v: 'bounce', label: '바운스' },
  { v: 'typewriter', label: '타자기' },
  { v: 'wave', label: '웨이브' },
  { v: 'pil', label: '정적', demo: true },
  { v: 'off', label: '끄기' },
];

export function SubtitlePanel({
  style, subScale, transcript, onStyle, onScale, onTranscript,
}: {
  style: SubStyle | 'pil' | 'off';
  subScale: number;
  transcript: { i: number; start: number; text: string }[];
  onStyle: (s: SubStyle | 'pil' | 'off') => void;
  onScale: (n: number) => void;
  onTranscript: (i: number, text: string) => void;
}) {
  const demo = useRef<HTMLVideoElement>(null);
  const hasDemo = STYLES.find((s) => s.v === style)?.demo;
  return (
    <div className="flex flex-wrap gap-4">
      <div className="min-w-[240px] flex-1 space-y-4">
        <div className="space-y-1.5">
          <span className="font-mono text-xs tracking-wide text-muted-foreground">스타일</span>
          <div className="flex flex-wrap gap-1 rounded-md bg-muted p-1">
            {STYLES.map((s) => (
              <button
                key={s.v}
                type="button"
                onClick={() => {
                  onStyle(s.v);
                  if (s.demo && demo.current) {
                    demo.current.src = `/demos/${s.v}.mp4`;
                    demo.current.play().catch(() => {});
                  }
                }}
                className={cn(
                  'rounded-sm px-3 py-1.5 text-[13px] transition-colors',
                  style === s.v ? 'bg-input text-foreground' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs tracking-wide text-muted-foreground">크기</span>
          <ToggleGroup
            type="single"
            value={String(subScale)}
            onValueChange={(v) => v && onScale(Number(v))}
            className="rounded-md bg-muted p-1"
          >
            {[[0.85, '작게'], [1, '보통'], [1.2, '크게']].map(([v, l]) => (
              <ToggleGroupItem key={String(v)} value={String(v)} className="rounded-sm px-3 text-[13px] data-[state=on]:bg-input">
                {l}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        {transcript.length > 0 && (
          <div className="space-y-1.5">
            <span className="font-mono text-xs tracking-wide text-muted-foreground">
              발화 자막 교정 <em className="not-italic text-muted-foreground/70">— 고친 문장은 통자막으로</em>
            </span>
            <div className="max-h-52 space-y-1 overflow-y-auto pr-1">
              {transcript.map((t, idx) => (
                <div key={t.i} className="flex items-center gap-2.5">
                  <span className="w-11 shrink-0 font-mono text-[11px] text-muted-foreground">
                    {Math.floor(t.start / 60)}:{String(Math.floor(t.start % 60)).padStart(2, '0')}
                  </span>
                  <Input
                    value={t.text}
                    onChange={(e) => onTranscript(idx, e.target.value)}
                    className="h-8 text-[13px]"
                  />
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      <video
        ref={demo}
        muted
        playsInline
        loop
        preload="none"
        className={cn('w-[150px] rounded-md border border-border bg-black aspect-[9/16]', !hasDemo && 'hidden')}
      />
    </div>
  );
}

export function MusicPanel({
  musicLib, currentTrack, choice, vol, onChoice, onVol,
}: {
  musicLib: MusicLib | null;
  currentTrack?: string;
  choice: string;
  vol: number;
  onChoice: (v: string) => void;
  onVol: (v: number) => void;
}) {
  const audio = useRef(new Audio());
  const [playingKey, setPlayingKey] = useState<string | null>(null);
  const preview = (url: string, key: string) => {
    if (playingKey === key) {
      audio.current.pause();
      setPlayingKey(null);
      return;
    }
    audio.current.src = url;
    audio.current.play().catch(() => {});
    audio.current.onended = () => setPlayingKey(null);
    setPlayingKey(key);
  };

  const rows: { val: string; main: string; sub?: string; url?: string }[] = [
    { val: 'auto', main: '자동 선곡', sub: `영상 무드 기반${currentTrack ? ` · 현재 ${currentTrack}` : ''}` },
    { val: 'off', main: '끄기', sub: 'BGM 없이' },
  ];
  const moods = musicLib?.moods || {};
  for (const mood of Object.keys(moods)) {
    for (const t of moods[mood]) {
      rows.push({
        val: `${mood}/${t.name}`,
        main: t.name.replace(/\.mp3$/i, '').replace(/_/g, ' '),
        sub: [mood, t.bpm ? `${t.bpm}bpm` : '', t.name === currentTrack ? '현재 적용' : ''].filter(Boolean).join(' · '),
        url: musicUrl(mood, t.name),
      });
    }
  }

  return (
    <div className="space-y-3">
      <div className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
        {rows.map((r) => (
          <button
            key={r.val}
            type="button"
            onClick={() => onChoice(r.val)}
            className={cn(
              'flex w-full items-center gap-2.5 rounded-md border px-3 py-2 text-left transition-colors',
              choice === r.val ? 'border-primary bg-primary/5' : 'border-border hover:border-input',
            )}
          >
            <span className="text-[13.5px] text-foreground">{r.main}</span>
            {r.sub && <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">{r.sub}</span>}
            {r.url && (
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => { e.stopPropagation(); preview(r.url!, r.val); }}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); preview(r.url!, r.val); } }}
                className={cn(
                  'grid size-8 shrink-0 place-items-center rounded-full border border-input',
                  playingKey === r.val ? 'text-primary' : 'text-muted-foreground',
                )}
              >
                <Play className="size-3.5" />
              </span>
            )}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-3 font-mono text-xs text-muted-foreground">
        볼륨
        <Input
          type="number" min={0.05} max={1} step={0.05} value={vol}
          onChange={(e) => onVol(parseFloat(e.target.value) || 0.3)}
          className="w-[84px] font-mono"
        />
      </label>
    </div>
  );
}

export function FxPanel({ fx, onFx }: { fx: FxState; onFx: (p: Partial<FxState>) => void }) {
  const rows: { key: keyof FxState; label: string }[] = [
    { key: 'jumpcut', label: '점프컷 (무음 제거)' },
    { key: 'punchin', label: '펀치인 (줌 강조)' },
    { key: 'blur', label: '흐린 배경' },
    { key: 'clean', label: '클린 버전 함께' },
  ];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-x-6 gap-y-3 max-sm:grid-cols-1">
        {rows.map((r) => (
          <label key={r.key} className="flex items-center justify-between gap-3 text-[13.5px] text-muted-foreground">
            {r.label}
            <Switch checked={fx[r.key] as boolean} onCheckedChange={(v) => onFx({ [r.key]: v })} />
          </label>
        ))}
      </div>
      <div className="flex flex-wrap gap-5 font-mono text-xs text-muted-foreground">
        <label className="flex items-center gap-2.5">
          숏츠 개수
          <Input type="number" min={0} max={10} value={fx.shortsCount}
            onChange={(e) => onFx({ shortsCount: parseInt(e.target.value) || 0 })} className="w-[70px] font-mono" />
        </label>
        <label className="flex items-center gap-2.5">
          썸네일 장수
          <Input type="number" min={1} max={10} value={fx.thumbCount}
            onChange={(e) => onFx({ thumbCount: parseInt(e.target.value) || 1 })} className="w-[70px] font-mono" />
        </label>
      </div>
    </div>
  );
}
