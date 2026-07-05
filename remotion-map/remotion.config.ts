import os from 'os';
import { Config } from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
// 서버/컨테이너 환경에서는 Remotion의 자동 브라우저 대신 시스템 chromium을 쓴다.
// 로컬에서는 이 변수가 없으므로 기존 동작 그대로.
if (process.env.REMOTION_BROWSER_EXECUTABLE) {
  Config.setBrowserExecutable(process.env.REMOTION_BROWSER_EXECUTABLE);
}
// 가용 코어를 넘으면 remotion이 렌더를 거부한다. 상한 4를 유지하되 가용 병렬성에 맞춰 클램프.
// 컨테이너(cgroup CPU 제한) 환경에서는 os.cpus()가 호스트 코어를 그대로 보고해
// 실제 가용치보다 크게 잡히므로 availableParallelism을 우선 사용한다.
const cores = typeof os.availableParallelism === 'function'
  ? os.availableParallelism()
  : os.cpus().length;
Config.setConcurrency(Math.max(1, Math.min(4, cores)));
