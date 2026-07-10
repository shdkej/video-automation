import { Composition } from 'remotion';
import { MapRoute } from './MapRoute';
import { MapFlyStatic } from './MapFlyStatic';
import { SubtitleOverlay, SubtitleProps } from './SubtitleOverlay';
import { NoteOverlay, NoteOverlayProps } from './NoteOverlay';
import { BRollOverlay, BRollOverlayProps } from './BRollOverlay';
import data from './data.json';
import flyMeta from './data-fly.json';

const SUB_FPS = 30;
const SUB_W = 1280; // 기본값(맵 플라이 1280x720). 실제 footage 해상도는 props로 덮어씀
const SUB_H = 720;
const SAMPLE_EVENTS: SubtitleProps['events'] = [
  { text: '베를린에서 출발합니다', start: 0.3, end: 2.2, speaker: '진행자' },
  { text: '688km를 달려', start: 2.4, end: 4.2, speaker: '게스트' },
  { text: '헝가리 부다페스트에 도착!', start: 4.4, end: 6.5, speaker: '진행자' },
];
const SAMPLE_PALETTE: Record<string, string> = { 진행자: '#ffd166', 게스트: '#4cc9f0' };

// 데모 소재는 scripts/make_note_demo.py로 생성 (레포에 미포함)
const NOTE_DEMO: NoteOverlayProps = {
  videoSrc: 'note-demo/bg.mp4',
  durationSec: 12,
  width: 1080,
  height: 1920,
  fps: 30,
  pages: [
    { src: 'note-demo/note_tower.png', start: 0.8, end: 3.9 },
    { src: 'note-demo/note_bridge.png', start: 4.8, end: 7.9 },
  ],
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="MapRoute"
        component={MapRoute}
        durationInFrames={270}
        fps={30}
        width={data.width}
        height={data.height}
      />
      <Composition
        id="MapFly"
        component={MapFlyStatic}
        durationInFrames={flyMeta.frames}
        fps={flyMeta.fps}
        width={flyMeta.width}
        height={flyMeta.height}
      />
      <Composition
        id="NoteOverlay"
        component={NoteOverlay}
        fps={NOTE_DEMO.fps!}
        width={NOTE_DEMO.width!}
        height={NOTE_DEMO.height!}
        durationInFrames={1}
        defaultProps={NOTE_DEMO}
        calculateMetadata={({ props }) => {
          const fps = props.fps || 30;
          const pagesEnd = props.pages.length ? Math.max(...props.pages.map((p) => p.end)) : 0;
          const durationSec = Math.max(props.durationSec ?? 0, pagesEnd + 0.5);
          return {
            durationInFrames: Math.max(1, Math.ceil(durationSec * fps)),
            fps,
            width: props.width || 1080,
            height: props.height || 1920,
          };
        }}
      />
      <Composition
        id="BRollOverlay"
        component={BRollOverlay}
        fps={30}
        width={1080}
        height={1920}
        durationInFrames={1}
        defaultProps={{ videoSrc: 'note-demo/bg.mp4', pages: [] } as BRollOverlayProps}
        calculateMetadata={({ props }) => {
          const fps = props.fps || 30;
          const pagesEnd = props.pages.length ? Math.max(...props.pages.map((p) => p.end)) : 0;
          const durationSec = Math.max(props.durationSec ?? 0, pagesEnd + 0.2);
          return {
            durationInFrames: Math.max(1, Math.ceil(durationSec * fps)),
            fps,
            width: props.width || 1080,
            height: props.height || 1920,
          };
        }}
      />
      <Composition
        id="SubtitleOverlay"
        component={SubtitleOverlay}
        fps={SUB_FPS}
        width={SUB_W}
        height={SUB_H}
        durationInFrames={1}
        defaultProps={{ events: SAMPLE_EVENTS, fontSize: 44, marginBottom: 72, width: SUB_W, height: SUB_H, fps: SUB_FPS, style: 'kinetic', palette: SAMPLE_PALETTE, hook: undefined, mode: 'longform' }}
        calculateMetadata={({ props }) => {
          const fps = props.fps || SUB_FPS;
          const eventsEnd = props.events.length ? Math.max(...props.events.map((e) => e.end)) : 0;
          // durationSec: 이벤트 없이 훅 배너만 얹는 인트로가 footage 전체를 덮도록
          const lastEnd = Math.max(eventsEnd, props.durationSec ?? 0) || 1;
          return {
            durationInFrames: Math.max(1, Math.ceil((lastEnd + 0.3) * fps)),
            fps,
            width: props.width || SUB_W,
            height: props.height || SUB_H,
          };
        }}
      />
    </>
  );
};
