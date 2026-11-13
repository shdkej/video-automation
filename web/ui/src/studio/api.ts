export type Asset = {
  id: string; name: string; kind: 'image' | 'video' | 'audio'; url: string;
  source: 'upload' | 'ai' | 'reference'; duration?: number;
  reference_id?: string; credit?: string; source_url?: string; license?: string; prompt?: string;
};
export type Reference = {
  id: string; title: string; kind: 'image' | 'video'; url: string; thumbnail: string;
  source_url: string; provider: string; credit: string; license: string; reason: string; importable: boolean;
};
export type Suggestions = {
  keywords: {text: string; angle: string; reason: string; evidence: string}[];
  hooks: {text: string; type: string; keyword: string; reason: string}[];
  search_queries: string[]; image_prompts: string[]; caption: string; source: string; notices: string[];
};
export type KeywordResearch = {
  provider: string; seeds: string[]; queried_at: string; total_found: number; notices: string[];
  items: {keyword: string; is_seed: boolean; pc: string; mobile: string; monthly_total: number | null; monthly_label: string; competition: string}[];
};
export type Scene = {id: string; text: string; asset_id: string; duration: number; trim_start: number};
export type Project = {
  id: string; title: string; script: string; topic: string; audience: string; goal: string; language: string;
  keywords: string[]; hook: string; caption: string; cta: string;
  style: 'photo' | 'shade' | 'editorial'; font: 'pretendard' | 'blackhansans' | 'dohyeon';
  position: 'top' | 'center' | 'bottom'; scenes: Scene[]; audio_asset_id: string | null;
  assets: Asset[]; references: Reference[]; created_at: string; updated_at: string;
  keyword_research?: KeywordResearch; suggestions?: Suggestions; last_render?: RenderJob | null;
};
export type Capabilities = {ai_text: boolean; ai_images: boolean; ai_narration: boolean; reference_search: boolean; keyword_search: boolean; youtube_search: boolean};
export type RenderJob = {
  id: string; status: 'queued' | 'running' | 'done' | 'error'; progress: number; stage: string; error?: string;
  outputs?: {video: string; cover: string; subtitles: string; project: string};
};

export function emptyProject(): Project {
  return {id: '', title: '', script: '', topic: '', audience: '', goal: '', language: 'ko', keywords: [], hook: '',
    caption: '', cta: '', style: 'photo', font: 'pretendard', position: 'top', scenes: [], audio_asset_id: null,
    assets: [], references: [], created_at: '', updated_at: ''};
}

export function editableProject(project: Project) {
  // Assets and timestamps belong to the server. A draft can never replace an upload.
  const {title, script, topic, audience, goal, language, keywords, hook, caption, cta, style, font, position, scenes, audio_asset_id, references} = project;
  return {title, script, topic, audience, goal, language, keywords, hook, caption, cta, style, font, position, scenes, audio_asset_id, references};
}

let browserApiKey = '';
export function setBrowserApiKey(value: string) { browserApiKey = value; }
function aiJson(body: unknown): RequestInit {
  const options = json('POST', body);
  if (browserApiKey) options.headers = {...options.headers, 'X-OpenAI-API-Key': browserApiKey};
  return options;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/studio${path}`, options);
  if (!response.ok) {
    let detail = `요청을 처리하지 못했어요 (${response.status}). 다시 시도해 주세요.`;
    try {
      const data = await response.json();
      if (typeof data.detail === 'string') detail = data.detail;
      else if (Array.isArray(data.detail)) detail = data.detail.map((item: {msg?: string}) => item.msg).filter(Boolean).join(' · ') || detail;
    } catch { /* Keep the useful HTTP status for non-JSON server errors. */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function json(method: string, body: unknown): RequestInit {
  return {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
}
const projectPath = (id: string) => `/projects/${encodeURIComponent(id)}`;

export const studioApi = {
  capabilities: () => request<Capabilities>('/capabilities'),
  list: () => request<{projects: Project[]}>('/projects'),
  create: (title: string) => request<Project>('/projects', json('POST', {title})),
  get: (id: string) => request<Project>(projectPath(id)),
  save: (project: Project) => request<Project>(projectPath(project.id), json('PUT', editableProject(project))),
  upload: (id: string, files: File[], referenceId?: string) => {
    const body = new FormData();
    files.forEach((file) => body.append('files', file));
    return request<{assets: Asset[]}>(`${projectPath(id)}/assets${referenceId ? `?reference_id=${encodeURIComponent(referenceId)}` : ''}`, {method: 'POST', body});
  },
  keywords: (id: string, query: string) => request<KeywordResearch>(`${projectPath(id)}/keywords/search`, json('POST', {query})),
  suggest: (id: string) => request<Suggestions>(`${projectPath(id)}/suggestions`, aiJson({})),
  images: (id: string, prompt: string, count: number) => request<{assets: Asset[]; errors: string[]}>(`${projectPath(id)}/images`, aiJson({prompt, count})),
  narration: (id: string, voice: string) => request<{assets: Asset[]}>(`${projectPath(id)}/narration`, aiJson({voice})),
  references: (id: string, query: string, kind: 'all' | 'image' | 'video', provider: 'all' | 'commons' | 'youtube' = 'all') => request<{items: Reference[]; notices: string[]}>(`${projectPath(id)}/references/search`, json('POST', {query, kind, provider})),
  importReference: (id: string, reference: Reference) => request<{assets: Asset[]}>(`${projectPath(id)}/references/import`, json('POST', reference)),
  render: (id: string) => request<RenderJob>(`${projectPath(id)}/render`, json('POST', {})),
  renderStatus: (id: string, renderId: string) => request<RenderJob>(`${projectPath(id)}/renders/${encodeURIComponent(renderId)}`),
  renderProject: (id: string, renderId: string) => request<Project>(`${projectPath(id)}/renders/${encodeURIComponent(renderId)}/project.json`),
};

export function referenceAsset(assets: Asset[], reference: Reference) {
  return assets.find((asset) => asset.kind === reference.kind && (asset.reference_id === reference.id || (!!reference.source_url && asset.source_url === reference.source_url)));
}
