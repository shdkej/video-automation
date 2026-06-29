import os from 'os';
import { Config } from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
// 가용 코어를 넘으면 remotion이 렌더를 거부한다. 상한 4를 유지하되 머신 코어 수에 맞춰 클램프.
Config.setConcurrency(Math.max(1, Math.min(4, os.cpus().length)));
