import {
  AbsoluteFill,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
  staticFile,
  delayRender,
  continueRender,
} from 'remotion';

// 동봉 Pretendard ExtraBold를 헤드리스 Chromium에 등록 (PIL 엔진과 동일 폰트).
const pretendardHandle = delayRender('load-pretendard');
const pretendard = new FontFace(
  'Pretendard',
  `url(${staticFile('Pretendard-ExtraBold.otf')}) format('opentype')`,
  { weight: '800' },
);
pretendard
  .load()
  .then((f) => {
    document.fonts.add(f);
    continueRender(pretendardHandle);
  })
  .catch(() => continueRender(pretendardHandle));

// 컷 영상 타임라인(초) 기준 자막 이벤트.
export type SubEvent = {
  text: string;
  start: number;
  end: number;
  speaker?: string; // 화자 키(색 매핑). 없으면 기본색
  style?: 'fade' | 'kinetic'; // 이벤트별 스타일 override
  words?: { text: string; start: number; end: number }[]; // 카라오케 단어 타이밍(이벤트 상대 초)
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
  hook?: string; // 숏츠/인트로 상단 후킹 배너 문구
  mode?: 'shorts' | 'longform' | 'intro'; // shorts: 펀치 자막+배너, longform: fade+키워드강조, intro: 배너 온리
  durationSec?: number; // 이벤트 없이도 오버레이 길이를 보장 (인트로 훅 배너 전용)
};

const FONT = 'Pretendard, "Apple SD Gothic Neo", "AppleGothic", -apple-system, sans-serif';

// 강조 키컬러 — 숫자/따옴표 토큰을 노랗게 띄운다 (배너·자막 공통).
const ACCENT = '#FFE14D';

// 배경 박스 없이 가독성을 내는 외곽선(stroke). PIL 엔진(stroke_width≈4, 검정)과 룩을 맞춘다.
const STROKE = '#000';
const STROKE_SHADOW = [
  '-3px 0 0', '3px 0 0', '0 -3px 0', '0 3px 0',
  '-2px -2px 0', '2px -2px 0', '-2px 2px 0', '2px 2px 0',
].map((o) => `${o} ${STROKE}`).join(', ') + ', 0 2px 6px rgba(0,0,0,0.85)';

// 팔레트에 없는 화자도 안정적으로 구분되는 색을 받도록 폴백
const DEFAULT_COLORS = ['#ffd166', '#4cc9f0', '#f72585', '#80ed99', '#ff9f1c', '#c77dff'];
function colorFor(speaker: string | undefined, palette: Record<string, string>): string | null {
  if (!speaker) return null;
  if (palette[speaker]) return palette[speaker];
  let h = 0;
  for (let i = 0; i < speaker.length; i++) h = (h * 31 + speaker.charCodeAt(i)) >>> 0;
  return DEFAULT_COLORS[h % DEFAULT_COLORS.length];
}

// 숫자(단위 포함)와 따옴표 안 토큰을 강조 대상으로 식별. 화면당 최대 maxHits개만.
const NUMBER_RE = /\d/;
const QUOTE_RE = /[""'']/;
function isAccentToken(token: string): boolean {
  return NUMBER_RE.test(token) || QUOTE_RE.test(token);
}

// 텍스트를 공백 토큰으로 쪼개 강조 span 배열로. 강조는 앞에서부터 maxHits개까지만.
function highlightSpans(text: string, maxHits: number): { word: string; accent: boolean }[] {
  const words = text.split(/(\s+)/); // 공백 보존(롱폼은 단어 stagger 없음)
  let hits = 0;
  return words.map((word) => {
    if (word.trim() && isAccentToken(word) && hits < maxHits) {
      hits += 1;
      return { word, accent: true };
    }
    return { word, accent: false };
  });
}

// ---------------------------------------------------------------------------
// HookBanner — 숏츠 상단 후킹 배너 (전체 길이 상시 표시, 시작 시 1회 슬라이드 인)
// ---------------------------------------------------------------------------

// 노랑 띠 타이틀 위에서는 노랑 ACCENT가 죽으므로 강조를 레드로 (배너 전용).
const BANNER_HIGHLIGHT = '#E11D2A';

const HookBanner: React.FC<{ hook: string; fontSize: number; height: number }> = ({
  hook, fontSize, height,
}) => {
  // 첫 프레임이 곧 커버 — 슬라이드인 없이 frame 0부터 완전 노출 (트렌드 표준)
  const top = Math.round(height * 0.13); // 상단 10% 세이프존 비우고 13% 지점
  const lineHeight = 1.2;
  return (
    <AbsoluteFill style={{ justifyContent: 'flex-start', alignItems: 'center' }}>
      <div
        style={{
          position: 'absolute',
          top,
          maxWidth: 'calc(100% - 100px)',
          background: '#FFE14D',
          borderRadius: 16,
          padding: '18px 32px',
          color: '#111',
          fontFamily: FONT,
          fontSize,
          fontWeight: 800,
          lineHeight,
          textAlign: 'center',
          boxShadow: '0 6px 20px rgba(0,0,0,0.35)',
        }}
      >
        <span
          style={{
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
            maxHeight: `${Math.round(fontSize * lineHeight * 2)}px`,
          }}
        >
          {highlightSpans(hook, 99).map((s, i) => (
            <span key={i} style={s.accent ? { color: BANNER_HIGHLIGHT, whiteSpace: 'nowrap' } : undefined}>{s.word}</span>
          ))}
        </span>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// Caption container — 하단 자막 박스 없는 외곽선 룩
// ---------------------------------------------------------------------------

const Pill: React.FC<{
  children: React.ReactNode;
  fontSize: number;
  marginBottom: number;
  containerOpacity: number;
  containerY: number;
}> = ({ children, fontSize, marginBottom, containerOpacity, containerY }) => (
  <AbsoluteFill style={{ justifyContent: 'flex-end', alignItems: 'center', paddingBottom: marginBottom }}>
    <div
      style={{
        opacity: containerOpacity,
        transform: `translateY(${containerY}px)`,
        maxWidth: '88%',
        color: '#fff',
        fontFamily: FONT,
        fontSize,
        fontWeight: 800,
        lineHeight: 1.3,
        textAlign: 'center',
        WebkitTextStroke: `3px ${STROKE}`,
        paintOrder: 'stroke fill',
        textShadow: STROKE_SHADOW,
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
  mode: 'shorts' | 'longform';
  palette: Record<string, string>;
}> = ({ ev, durationInFrames, fontSize, marginBottom, defaultStyle, mode, palette }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const accent = colorFor(ev.speaker, palette);

  // 공통 퇴장 페이드(끝 6프레임)
  const exit = interpolate(frame, [durationInFrames - 6, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  // 숏츠: 카라오케 — 실제 발화 시점에 단어 등장(words). 없으면 균일 stagger 폴백.
  if (mode === 'shorts') {
    const boxIn = spring({ frame, fps, config: { damping: 20, mass: 0.5 }, durationInFrames: 8 });
    const stagger = 2;
    const words: { text: string; startFrame: number }[] = ev.words?.length
      ? ev.words.map((w) => ({ text: w.text, startFrame: Math.round(w.start * fps) }))
      : ev.text.split(/\s+/).filter(Boolean).map((t, i) => ({ text: t, startFrame: i * stagger }));
    return (
      <Pill fontSize={fontSize} marginBottom={marginBottom}
        containerOpacity={Math.min(boxIn, exit)} containerY={interpolate(boxIn, [0, 1], [20, 0])}>
        {words.map((w, i) => {
          const wf = frame - w.startFrame;
          // 스케일 펀치: 0.7 → 1.06 → 1.0 안착 (~8프레임), 1.1배 초과 금지
          const sc = interpolate(wf, [0, 5, 8], [0.7, 1.06, 1.0], {
            extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
          });
          const op = interpolate(wf, [0, 4], [0, 1], {
            extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
          });
          const hot = isAccentToken(w.text);
          return (
            <span key={i} style={{
              display: 'inline-block', marginRight: '0.28em',
              opacity: op, transform: `scale(${Math.min(sc, 1.1)})`,
              ...(hot ? { color: ACCENT } : accent ? { color: accent } : {}),
            }}>
              {w.text}
            </span>
          );
        })}
      </Pill>
    );
  }

  const style = ev.style ?? defaultStyle;

  if (style === 'kinetic') {
    const boxIn = spring({ frame, fps, config: { damping: 20, mass: 0.5 }, durationInFrames: 8 });
    const words = ev.text.split(/\s+/).filter(Boolean);
    const stagger = 2;
    return (
      <Pill fontSize={fontSize} marginBottom={marginBottom}
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

  // 롱폼 fade: 전체가 한 번에 페이드+슬라이드, 숫자·따옴표 토큰만 노란 강조(화면당 2개)
  const enter = spring({ frame, fps, config: { damping: 18, mass: 0.6 }, durationInFrames: 14 });
  return (
    <Pill fontSize={fontSize} marginBottom={marginBottom}
      containerOpacity={Math.min(enter, exit)} containerY={interpolate(enter, [0, 1], [26, 0])}>
      {highlightSpans(ev.text, 2).map((s, i) => (
        <span key={i} style={s.accent ? { color: ACCENT } : accent ? { color: accent } : undefined}>
          {s.word}
        </span>
      ))}
    </Pill>
  );
};

// 투명 배경 자막 오버레이. alpha 코덱(vp8)으로 렌더 → ffmpeg overlay로 실사 위에 합성.
export const SubtitleOverlay: React.FC<SubtitleProps> = ({
  events, fontSize, marginBottom, style, palette, hook, mode,
}) => {
  const { fps, height } = useVideoConfig();
  const resolvedMode = mode ?? 'longform';
  const captionMode = resolvedMode === 'shorts' ? 'shorts' : 'longform';
  // 배너는 타이틀 위계로 말 자막의 1.43배(숏츠 기준 ≈80px)
  const bannerFontSize = Math.round(fontSize * 1.43);
  return (
    <AbsoluteFill>
      {events.map((e, i) => {
        const from = Math.round(e.start * fps);
        const dur = Math.max(1, Math.round((e.end - e.start) * fps));
        return (
          <Sequence key={i} from={from} durationInFrames={dur}>
            <Caption ev={e} durationInFrames={dur} fontSize={fontSize} marginBottom={marginBottom}
              defaultStyle={style} mode={captionMode} palette={palette} />
          </Sequence>
        );
      })}
      {(resolvedMode === 'shorts' || resolvedMode === 'intro') && hook ? (
        <HookBanner hook={hook} fontSize={bannerFontSize} height={height} />
      ) : null}
    </AbsoluteFill>
  );
};
