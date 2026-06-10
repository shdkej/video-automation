import {
  AbsoluteFill,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from 'remotion';

// 컷 영상 타임라인(초) 기준 자막 이벤트.
export type SubEvent = {
  text: string;
  start: number;
  end: number;
  speaker?: string; // 화자 키(색 매핑). 없으면 기본색
  style?: 'fade' | 'kinetic'; // 이벤트별 스타일 override
};
export type SubtitleProps = {
  events: SubEvent[];
  fontSize: number;
  marginBottom: number;
  width: number;
  height: number;
  fps: number;
  style: 'fade' | 'kinetic'; // 전역 기본 스타일
  palette: Record<string, string>; // 화자 → hex
};

const FONT = '"Apple SD Gothic Neo", "AppleGothic", -apple-system, sans-serif';

// 팔레트에 없는 화자도 안정적으로 구분되는 색을 받도록 폴백
const DEFAULT_COLORS = ['#ffd166', '#4cc9f0', '#f72585', '#80ed99', '#ff9f1c', '#c77dff'];
function colorFor(speaker: string | undefined, palette: Record<string, string>): string | null {
  if (!speaker) return null;
  if (palette[speaker]) return palette[speaker];
  let h = 0;
  for (let i = 0; i < speaker.length; i++) h = (h * 31 + speaker.charCodeAt(i)) >>> 0;
  return DEFAULT_COLORS[h % DEFAULT_COLORS.length];
}

const Pill: React.FC<{
  children: React.ReactNode;
  fontSize: number;
  marginBottom: number;
  accent: string | null;
  containerOpacity: number;
  containerY: number;
}> = ({ children, fontSize, marginBottom, accent, containerOpacity, containerY }) => (
  <AbsoluteFill style={{ justifyContent: 'flex-end', alignItems: 'center', paddingBottom: marginBottom }}>
    <div
      style={{
        opacity: containerOpacity,
        transform: `translateY(${containerY}px)`,
        maxWidth: '82%',
        padding: '14px 30px',
        borderRadius: 16,
        ...(accent ? { borderLeft: `6px solid ${accent}` } : {}),
        background: 'rgba(0,0,0,0.62)',
        boxShadow: accent ? `0 6px 24px rgba(0,0,0,0.45), 0 0 0 1px ${accent}55` : '0 6px 24px rgba(0,0,0,0.45)',
        color: '#fff',
        fontFamily: FONT,
        fontSize,
        fontWeight: 700,
        lineHeight: 1.3,
        textAlign: 'center',
        textShadow: '0 2px 8px rgba(0,0,0,0.8)',
        whiteSpace: 'pre-wrap',
      }}
    >
      {children}
    </div>
  </AbsoluteFill>
);

const Caption: React.FC<{
  ev: SubEvent;
  durationInFrames: number;
  fontSize: number;
  marginBottom: number;
  defaultStyle: 'fade' | 'kinetic';
  palette: Record<string, string>;
}> = ({ ev, durationInFrames, fontSize, marginBottom, defaultStyle, palette }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const style = ev.style ?? defaultStyle;
  const accent = colorFor(ev.speaker, palette);

  // 공통 퇴장 페이드(끝 6프레임)
  const exit = interpolate(frame, [durationInFrames - 6, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  if (style === 'kinetic') {
    // 컨테이너는 빠르게 등장, 단어는 순차 등장
    const boxIn = spring({ frame, fps, config: { damping: 20, mass: 0.5 }, durationInFrames: 8 });
    const words = ev.text.split(/\s+/).filter(Boolean);
    const stagger = 2; // 단어 간 프레임 간격
    return (
      <Pill fontSize={fontSize} marginBottom={marginBottom} accent={accent}
        containerOpacity={Math.min(boxIn, exit)} containerY={interpolate(boxIn, [0, 1], [20, 0])}>
        {words.map((w, i) => {
          const wf = frame - i * stagger;
          const wp = spring({ frame: wf, fps, config: { damping: 16, mass: 0.6 }, durationInFrames: 10 });
          return (
            <span key={i} style={{
              display: 'inline-block', marginRight: '0.28em',
              opacity: wp, transform: `translateY(${interpolate(wp, [0, 1], [14, 0])}px)`,
              ...(accent ? { color: accent } : {}),
            }}>
              {w}
            </span>
          );
        })}
      </Pill>
    );
  }

  // fade: 전체가 한 번에 페이드+슬라이드
  const enter = spring({ frame, fps, config: { damping: 18, mass: 0.6 }, durationInFrames: 14 });
  return (
    <Pill fontSize={fontSize} marginBottom={marginBottom} accent={accent}
      containerOpacity={Math.min(enter, exit)} containerY={interpolate(enter, [0, 1], [26, 0])}>
      {ev.text}
    </Pill>
  );
};

// 투명 배경 자막 오버레이. alpha 코덱(vp8)으로 렌더 → ffmpeg overlay로 실사 위에 합성.
export const SubtitleOverlay: React.FC<SubtitleProps> = ({ events, fontSize, marginBottom, style, palette }) => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill>
      {events.map((e, i) => {
        const from = Math.round(e.start * fps);
        const dur = Math.max(1, Math.round((e.end - e.start) * fps));
        return (
          <Sequence key={i} from={from} durationInFrames={dur}>
            <Caption ev={e} durationInFrames={dur} fontSize={fontSize} marginBottom={marginBottom}
              defaultStyle={style} palette={palette} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
