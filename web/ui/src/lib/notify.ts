// 완료 알림 — 레거시 notifyDone 이식: 탭이 백그라운드일 때만 브라우저 알림 + 타이틀 배지.
const BASE_TITLE = document.title;

export function requestNotifyPermission() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {});
  }
}

export function notifyDone(ok: boolean, msg: string) {
  if (!document.hidden) return;
  document.title = `${ok ? '✅ 완성' : '⚠️ 오류'} — Reel Room`;
  if ('Notification' in window && Notification.permission === 'granted') {
    try {
      new Notification(ok ? 'Reel Room — 완성' : 'Reel Room — 처리 중단', { body: msg });
    } catch { /* 미지원 환경 무시 */ }
  }
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) document.title = BASE_TITLE;
});
