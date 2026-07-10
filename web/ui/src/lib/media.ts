// 미디어 파일 유틸 — 레거시 app.js에서 알고리즘 그대로 이식

export const AUDIO_RE = /\.(m4a|mp3|wav|aac|flac|ogg|opus|aiff?)$/i;
export const MEDIA_RE = /\.(mp4|mov|mkv|webm|avi|m4v|m4a|mp3|wav|aac|flac|ogg|opus|aiff?)$/i;
export const NOTE_IMG_RE = /\.(png|jpe?g|webp)$/i;

export const isAudio = (f: File) => AUDIO_RE.test(f.name) || f.type.startsWith('audio/');
export const isNoteImg = (f: File) => NOTE_IMG_RE.test(f.name) || f.type.startsWith('image/');
export const isVideo = (f: File) => !isAudio(f) && !isNoteImg(f);

/** 영상 + 이미지가 함께 있으면 노트 오버레이 모드 — 파일 조합이 곧 의도 */
export const isNoteMode = (files: File[]) =>
  files.some(isNoteImg) && files.some(isVideo);

export function fmtSize(bytes: number): string {
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes > 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${(bytes / 1e3).toFixed(0)} KB`;
}

/** 선택한 영상의 첫 프레임을 브라우저에서 캡처 — 썸네일 미리보기 바탕.
 * preload만으론 프레임이 준비되지 않아(iOS) 음소거 재생으로 킥, rVFC에서 캡처. */
export function captureFrameFromFile(file: File): Promise<Blob | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const v = document.createElement('video');
    v.muted = true;
    v.playsInline = true;
    v.autoplay = true;
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const finish = (blob: Blob | null) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      v.pause();
      v.removeAttribute('src');
      URL.revokeObjectURL(url);
      resolve(blob);
    };
    const draw = () => {
      if (settled || !v.videoWidth) return;
      try {
        const c = document.createElement('canvas');
        c.width = 540;
        c.height = Math.max(2, Math.round((v.videoHeight * 540) / v.videoWidth));
        c.getContext('2d')!.drawImage(v, 0, 0, c.width, c.height);
        c.toBlob((b) => finish(b), 'image/jpeg', 0.85);
      } catch { finish(null); }
    };
    const rvfc = (v as HTMLVideoElement & { requestVideoFrameCallback?: (cb: () => void) => void })
      .requestVideoFrameCallback?.bind(v);
    if (rvfc) {
      rvfc(() => draw());
    } else {
      v.addEventListener('playing', () => setTimeout(draw, 60), { once: true });
      v.addEventListener('canplay', () => setTimeout(draw, 60), { once: true });
    }
    v.onerror = () => finish(null);
    timer = setTimeout(() => finish(null), 8000);
    v.src = url;
    v.load();
    const p = v.play();
    if (p) p.catch(() => { /* 자동재생 거부 — 타임아웃이 처리 */ });
  });
}

export const secToMmss = (s: number) => {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
};

export const fmtTimecode = (s: number) => {
  if (!isFinite(s)) s = 0;
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}.${Math.floor((s % 1) * 10)}`;
};
