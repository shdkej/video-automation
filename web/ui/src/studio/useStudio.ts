import {useCallback, useEffect, useRef, useState} from 'react';
import {editableProject, emptyProject, studioApi} from './api';
import type {Asset, Capabilities, Project, RenderJob, Suggestions} from './api';

const message = (error: unknown) => error instanceof Error ? error.message : '연결을 확인한 뒤 다시 시도해 주세요.';
const outputKey = (id: string) => `reelroom:studio:output:${id}`;
type SavedOutput = {id: string; signature: string};

export function useStudio() {
  const [project, setProject] = useState<Project>(emptyProject);
  const current = useRef(project);
  const [projects, setProjects] = useState<Project[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestions | null>(null);
  const [busy, setBusy] = useState('');
  const locked = useRef(false);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [render, setRender] = useState<RenderJob | null>(null);
  const [renderSignature, setRenderSignature] = useState('');
  const [pollError, setPollError] = useState('');
  const [pollRetry, setPollRetry] = useState(0);
  const renderId = render?.id;
  const renderStatus = render?.status;

  const replace = useCallback((next: Project, changed = false) => {
    current.current = next; setProject(next); dirtyRef.current = changed; setDirty(changed);
  }, []);
  const update = (change: Partial<Project>) => {
    if (locked.current) return;
    replace({...current.current, ...change}, true);
  };
  const remember = (saved: Project) => setProjects((items) => [saved, ...items.filter((item) => item.id !== saved.id)]);
  const saveCurrent = async () => {
    let next = current.current;
    if (!next.id) {
      const created = await studioApi.create(next.title || '제목 없는 릴스');
      next = {...next, id: created.id, assets: created.assets, created_at: created.created_at, updated_at: created.updated_at};
      // Retain the ID even if the following PUT fails, so retry cannot create duplicates.
      replace(next, true);
    }
    const saved = await studioApi.save(next);
    replace(saved); remember(saved);
    return saved;
  };
  const run = async (label: string, task: () => Promise<void>) => {
    if (locked.current) return;
    locked.current = true; setBusy(label); setError(''); setNotice('');
    try { await task(); } catch (cause) { setError(message(cause)); }
    finally { locked.current = false; setBusy(''); }
  };
  const appendAssets = (assets: Asset[]) => {
    const merged = new Map(current.current.assets.map((asset) => [asset.id, asset]));
    assets.forEach((asset) => merged.set(asset.id, asset));
    const next = {...current.current, assets: [...merged.values()]};
    replace(next, dirtyRef.current); remember(next);
  };
  const clearOutput = () => { setRender(null); setRenderSignature(''); setPollError(''); };
  const restoreOutput = async (next: Project) => {
    if (next.last_render) {
      setRender(next.last_render);
      if (next.last_render.status === 'done') {
        try { setRenderSignature(JSON.stringify(editableProject(await studioApi.renderProject(next.id, next.last_render.id)))); }
        catch { setRenderSignature(''); }
      }
      return;
    }
    let saved: SavedOutput | null = null;
    try { saved = JSON.parse(localStorage.getItem(outputKey(next.id)) || 'null'); } catch { /* Storage may be unavailable. */ }
    if (!saved?.id) return;
    try { setRender(await studioApi.renderStatus(next.id, saved.id)); setRenderSignature(saved.signature); }
    catch { setNotice('저장한 프로젝트를 열었어요. 이전 출력 파일은 다시 만들어 주세요.'); }
  };

  const reload = useCallback(async () => {
    setLoading(true); setError('');
    const results = await Promise.allSettled([studioApi.list(), studioApi.capabilities()]);
    if (results[0].status === 'fulfilled') setProjects(results[0].value.projects);
    else setError(message(results[0].reason));
    if (results[1].status === 'fulfilled') setCapabilities(results[1].value);
    else { setCapabilities(null); setError(message(results[1].reason)); }
    setLoading(false);
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current || locked.current) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, []);
  useEffect(() => {
    if (!renderId || !project.id || (renderStatus !== 'running' && renderStatus !== 'queued')) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await studioApi.renderStatus(project.id, renderId);
        if (stopped) return;
        if (next.status === 'done') {
          try {
            const snapshot = await studioApi.renderProject(project.id, renderId);
            if (!stopped) setRenderSignature(JSON.stringify(editableProject(snapshot)));
          } catch { /* Keep the render-start signature when the snapshot is unavailable. */ }
        }
        if (stopped) return;
        setPollError(''); setRender(next);
        if (next.status === 'running' || next.status === 'queued') timer = setTimeout(poll, 1500);
      } catch (cause) { if (!stopped) setPollError(message(cause)); }
    };
    timer = setTimeout(poll, 1000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [project.id, renderId, renderStatus, pollRetry]);

  return {
    project, current, projects, capabilities, suggestions, setSuggestions, busy, dirty, error, setError,
    notice, setNotice, loading, render, pollError, reload, update, replace, run, saveCurrent, appendAssets,
    staleOutput: !!render && renderSignature !== JSON.stringify(editableProject(project)),
    retryPoll: () => { setPollError(''); setPollRetry((value) => value + 1); },
    save: () => run('저장 중', async () => { await saveCurrent(); setNotice('프로젝트를 저장했어요.'); }),
    open: (id: string) => run('프로젝트 여는 중', async () => {
      if (dirtyRef.current) await saveCurrent();
      const next = await studioApi.get(id);
      replace(next); setSuggestions(next.suggestions || null); clearOutput(); await restoreOutput(next);
    }),
    create: () => run('새 프로젝트 준비 중', async () => {
      if (dirtyRef.current) await saveCurrent();
      replace(emptyProject()); setSuggestions(null); clearOutput();
    }),
    renderVideo: () => run('출력 준비 중', async () => {
      const saved = await saveCurrent();
      const job = await studioApi.render(saved.id);
      const signature = JSON.stringify(editableProject(saved));
      setRender(job); setRenderSignature(signature); setPollError('');
      try { localStorage.setItem(outputKey(saved.id), JSON.stringify({id: job.id, signature})); } catch { /* Rendering remains usable without browser storage. */ }
    }),
  };
}
