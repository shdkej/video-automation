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
  random,
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
  style?: 'fade' | 'kinetic' | 'impact'; // 이벤트별 스타일 override
  words?: { text: string; start: number; end: number }[]; // 카라오케 단어 타이밍(이벤트 상대 초)
};
export type SubtitleProps = {
  events: SubEvent[];
  fontSize: number;
  marginBottom: number;
  width: number;
  height: number;
  fps: number;
  style: 'fade' | 'kinetic' | 'impact'; // 전역 기본 스타일
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
// HookBanner — 미니멀 타이틀: Pretendard ExtraBold + 크림 + 은은한 확산 그림자.
// 박스·테두리·라벨·네온 없음(사용자 결정). 왼쪽 손낙서 강조선만 포인트로.
// frame 0부터 완전 노출(커버 프레임), 레이아웃은 전부 비례 단위.
// ---------------------------------------------------------------------------

const CREAM = '#FBF6EA';
const SOFT_SHADOW =
  '0 0.03em 0.06em rgba(40,30,20,0.45), 0 0.11em 0.4em rgba(40,30,20,0.45), 0 0.22em 0.8em rgba(40,30,20,0.3)';

const HookBanner: React.FC<{
  hook: string; fontSize: number; height: number;
}> = ({ hook, fontSize, height }) => {
  const top = Math.round(height * 0.1);
  const lineHeight = 1.35;
  return (
    <AbsoluteFill style={{ alignItems: 'center' }}>
      <div style={{ position: 'absolute', top, maxWidth: '90%' }}>
        <svg viewBox="0 0 40 40" style={{
          position: 'absolute', left: '-1.15em', top: '0.05em',
          width: '0.95em', height: '0.95em', fontSize,
        }}>
          <g stroke={CREAM} strokeWidth={3.4} strokeLinecap="round" opacity={0.95}>
            <line x1="6" y1="34" x2="16" y2="24" />
            <line x1="14" y1="38" x2="22" y2="30" />
            <line x1="3" y1="26" x2="12" y2="18" />
          </g>
        </svg>
        <div style={{
          fontFamily: FONT,
          fontWeight: 800,
          color: CREAM,
          fontSize,
          lineHeight,
          textAlign: 'center',
          textShadow: SOFT_SHADOW,
        }}>
          <span style={{
            display: '-webkit-box',
            WebkitLineClamp: 3,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
            maxHeight: `${lineHeight * 3}em`,
          }}>
            {hook}
          </span>
        </div>
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
        color: '#FDFBF6',
        fontFamily: FONT,
        fontSize,
        fontWeight: 800,
        lineHeight: 1.35,
        textAlign: 'center',
        textShadow: '0 0.03em 0.07em rgba(20,15,10,0.55), 0 0.12em 0.45em rgba(20,15,10,0.45)',
        whiteSpace: 'pre-wrap',
      }}
    >
      {children}
    </div>
  </AbsoluteFill>
);

// ---------------------------------------------------------------------------
// Impact — 숏츠 트렌드 원워드 슬램: 한 번에 한 단어를 크게 쾅 + 스파크 번쩍
// ---------------------------------------------------------------------------

const SPARK_COLORS = ['#FFE14D', '#FF9F1C', '#FFFFFF'];

const Sparks: React.FC<{ seed: string; fontSize: number; wf: number }> = ({ seed, fontSize, wf }) => {
  const n = 12;
  return (
    <>
      {Array.from({ length: n }, (_, i) => {
        const key = `${seed}:${i}`;
        const angle = random(key) * Math.PI * 2;
        const reach = fontSize * (0.8 + random(key + 'd') * 1.0);
        const progress = interpolate(wf, [0, 14], [0.2, 1], {
          extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
        });
        const op = interpolate(wf, [2, 16], [1, 0], {
          extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
        });
        const size = fontSize * (0.14 + random(key + 's') * 0.12);
        const square = random(key + 'k') > 0.5;
        return (
          <span key={i} style={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            width: size,
            height: size,
            background: SPARK_COLORS[i % SPARK_COLORS.length],
            borderRadius: square ? '15%' : '50%',
            opacity: op,
            transform: `translate(-50%, -50%) translate(${Math.cos(angle) * reach * progress}px, ${Math.sin(angle) * reach * progress}px) rotate(${square ? 45 + wf * 14 : 0}deg)`,
            boxShadow: `0 0 ${size * 1.4}px rgba(255,214,77,0.95), 0 0 ${size * 2.6}px rgba(255,159,28,0.5)`,
          }} />
        );
      })}
    </>
  );
};

const ImpactCaption: React.FC<{
  ev: SubEvent;
  durationInFrames: number;
  fontSize: number;
  exit: number;
  words: { text: string; startFrame: number; endFrame: number }[];
}> = ({ durationInFrames, fontSize, exit, words }) => {
  const frame = useCurrentFrame();
  // 한 번에 한 단어 — 현재 단어를 찾고 마지막 단어는 이벤트 끝까지 유지
  let cur = -1;
  for (let i = 0; i < words.length; i++) {
    if (frame >= words[i].startFrame) cur = i;
  }
  if (cur < 0 || frame >= durationInFrames) return null;
  const w = words[cur];
  const wf = frame - w.startFrame;
  const seed = `impact:${cur}:${w.text}`;

  // 슬램: 2.4배에서 쾅 내려앉고 살짝 오버슈트 후 안착. 흔들림은 안착과 함께 소멸.
  const slam = interpolate(wf, [0, 4, 7], [2.4, 0.93, 1.0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const rot = (random(seed) - 0.5) * 14 * interpolate(wf, [0, 8], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const op = interpolate(wf, [0, 2], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const big = fontSize * 2.6;
  const hot = isAccentToken(w.text);
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
      <div style={{
        position: 'relative',
        transform: `translateY(${big * 0.8}px) scale(${slam}) rotate(${rot}deg)`,
        opacity: Math.min(op, exit),
      }}>
        <Sparks seed={seed} fontSize={big} wf={wf} />
        <span style={{
          position: 'relative',
          color: hot ? ACCENT : '#fff',
          fontFamily: FONT,
          fontSize: big,
          fontWeight: 800,
          whiteSpace: 'nowrap',
          WebkitTextStroke: `4px ${STROKE}`,
          paintOrder: 'stroke fill',
          textShadow: `${STROKE_SHADOW}, 0 0 0.3em rgba(255,225,77,0.95), 0 0 0.7em rgba(255,180,40,0.8), 0 0 1.4em rgba(255,140,20,0.5)`,
        }}>
          {w.text}
        </span>
      </div>
    </AbsoluteFill>
  );
};

const Caption: React.FC<{
  ev: SubEvent;
  durationInFrames: number;
  fontSize: number;
  marginBottom: number;
  defaultStyle: 'fade' | 'kinetic' | 'impact';
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

  // 숏츠: 미니멀 — 문장 통째 fade + 강조 토큰 색만 (카라오케 제거, 사용자 결정).
  // 'impact' 스타일만 원워드 슬램 유지. words 타이밍은 impact 전용으로 계산.
  if (mode === 'shorts' && (ev.style ?? defaultStyle) === 'impact') {
    let words: { text: string; startFrame: number; endFrame: number }[];
    if (ev.words?.length) {
      words = ev.words.map((w, i, arr) => ({
        text: w.text,
        startFrame: Math.round(w.start * fps),
        endFrame: Math.round((arr[i + 1]?.start ?? w.end) * fps),
      }));
    } else {
      const tokens = ev.text.split(/\s+/).filter(Boolean);
      const span = Math.max(tokens.length * 2, Math.round(durationInFrames * 0.85));
      words = tokens.map((t, i) => ({
        text: t,
        startFrame: Math.round((i * span) / tokens.length),
        endFrame: Math.round(((i + 1) * span) / tokens.length),
      }));
    }
    return (
      <ImpactCaption ev={ev} durationInFrames={durationInFrames}
        fontSize={fontSize} exit={exit} words={words} />
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
  // 타이틀 위계 — 박스 없는 키치 룩이라 말 자막의 1.6배로 존재감을 준다
  const bannerFontSize = Math.round(fontSize * 1.6);
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
