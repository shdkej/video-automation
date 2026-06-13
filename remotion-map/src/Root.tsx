import { Composition } from 'remotion';
import { MapRoute } from './MapRoute';
import { MapFlyStatic } from './MapFlyStatic';
import { SubtitleOverlay, SubtitleProps } from './SubtitleOverlay';
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
        id="SubtitleOverlay"
        component={SubtitleOverlay}
        fps={SUB_FPS}
        width={SUB_W}
        height={SUB_H}
        durationInFrames={1}
        defaultProps={{ events: SAMPLE_EVENTS, fontSize: 44, marginBottom: 72, width: SUB_W, height: SUB_H, fps: SUB_FPS, style: 'kinetic', palette: SAMPLE_PALETTE, hook: undefined, mode: 'longform' }}
        calculateMetadata={({ props }) => {
          const fps = props.fps || SUB_FPS;
          const lastEnd = props.events.length ? Math.max(...props.events.map((e) => e.end)) : 1;
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
