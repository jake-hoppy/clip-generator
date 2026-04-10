/**
 * clip-farm dashboard — vanilla JS, same origin as API
 */
const API = window.location.origin;

let currentPage = 'dashboard';
let logPollInterval = null;
let statusPollInterval = null;
let runPollInterval = null;
let _ingestPollInterval = null;
let selectedFile = null;
let _libraryData = [];
let _libFilter = 'all';
let _libTab = 'videos';
let _libraryClipsData = [];
let _outputsByClipId = {};

function outputFilename(clip) {
  if (clip._previewName) return clip._previewName;
  if (clip.filepath) {
    const p = String(clip.filepath).replace(/\\/g, '/');
    return p.split('/').pop();
  }
  return clip.filename || '';
}

function navigate(page) {
  // Stop ingest polling when leaving ingest page
  if (currentPage === 'ingest' && page !== 'ingest') {
    if (_ingestPollInterval) {
      clearInterval(_ingestPollInterval);
      _ingestPollInterval = null;
    }
  }

  document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach((n) => n.classList.remove('active'));
  const pg = document.getElementById('page-' + page);
  if (pg) pg.classList.add('active');
  const nav = document.querySelector(`[data-page="${page}"]`);
  if (nav) nav.classList.add('active');
  currentPage = page;

  if (logPollInterval) {
    clearInterval(logPollInterval);
    logPollInterval = null;
  }
  if (runPollInterval) {
    clearInterval(runPollInterval);
    runPollInterval = null;
  }

  if (page === 'dashboard') refreshDashboard();
  if (page === 'config') loadConfig();
  if (page === 'registry') loadRegistry();
  if (page === 'ingest') {
    loadIngested();
    loadOutputs();
  }
  if (page === 'library') loadLibrary();
  if (page === 'splice') {
    const st = document.getElementById('splice-status');
    if (st) st.textContent = '';
  }
  if (page === 'run') {
    refreshLog();
    logPollInterval = setInterval(refreshLog, 3000);
  }
}

document.querySelectorAll('.nav-item[data-page]').forEach((item) => {
  item.addEventListener('click', () => navigate(item.dataset.page));
});

function formatLogHtml(text) {
  if (!text) return '';
  const lines = text.split('\n');
  return lines
    .map((line) => {
      let cls = '';
      if (line.includes('ERROR') || line.includes('Traceback')) cls = 'log-error';
      else if (line.includes('WARNING')) cls = 'log-warn';
      else if (line.trim().startsWith('#')) cls = 'log-muted';
      const esc = line
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      return cls ? `<span class="${cls}">${esc}</span>` : esc;
    })
    .join('\n');
}

async function refreshLog() {
  try {
    const res = await fetch(`${API}/api/log`);
    const text = await res.text();
    const html = formatLogHtml(text);
    const logBox = document.getElementById('log-box');
    const runLog = document.getElementById('run-log-box');
    if (logBox) {
      logBox.innerHTML = html;
      logBox.scrollTop = logBox.scrollHeight;
    }
    if (runLog) {
      runLog.innerHTML = html;
      runLog.scrollTop = runLog.scrollHeight;
    }
  } catch (e) {
    console.error(e);
  }
}

function renderClipCard(clip, index) {
  const rank = index + 1;
  const score = clip.score != null ? Number(clip.score) : 0;
  const dur = clip.duration_seconds != null ? Number(clip.duration_seconds) : 0;
  const text = (clip.text || '').slice(0, 40) + ((clip.text || '').length > 40 ? '…' : '');
  const fname = outputFilename(clip);
  const videoSrc = fname ? `${API}/api/outputs/${encodeURIComponent(fname)}` : '';

  const div = document.createElement('div');
  div.className = 'clip-card';
  div.innerHTML = `
    <div class="clip-thumb">
      ${videoSrc ? `<video src="${videoSrc}" muted preload="metadata" playsinline></video>` : '<div style="color:var(--text-dim)">—</div>'}
      <div class="clip-rank">#${rank}</div>
      <div class="clip-score">${score.toFixed(1)}</div>
      <div class="clip-overlay"><div class="play-btn">▶</div></div>
    </div>
    <div class="clip-info">
      <div class="clip-title">${text || '—'}</div>
      <div class="clip-meta">${dur.toFixed(1)}s · ${(clip.video_id || '').slice(0, 12)}</div>
      <div class="score-bar"><div class="score-bar-fill" style="width:${Math.min(100, (score / 10) * 100)}%"></div></div>
    </div>
  `;
  div.addEventListener('click', () => openVideoModal(clip, fname));
  return div;
}

async function refreshDashboard() {
  try {
    const [statsRes, clipsRes, cfgRes] = await Promise.all([
      fetch(`${API}/api/stats`),
      fetch(`${API}/api/clips`),
      fetch(`${API}/api/config`),
    ]);
    const stats = await statsRes.json().catch(() => ({}));
    const clipsData = await clipsRes.json().catch(() => ({ clips: [] }));
    const cfg = await cfgRes.json().catch(() => ({}));

    document.getElementById('stat-outputs').textContent = stats.outputs_count ?? '—';
    document.getElementById('stat-videos').textContent = stats.videos_count ?? '—';
    document.getElementById('stat-used').textContent = stats.used_clips ?? '—';
    document.getElementById('stat-score').textContent =
      stats.avg_score != null ? String(stats.avg_score) : '—';

    const lr = document.getElementById('last-run-time');
    const lrc = document.getElementById('last-run-clips');
    if (lr) {
      lr.textContent = stats.last_run
        ? new Date(stats.last_run).toLocaleString()
        : '—';
    }
    if (lrc) {
      lrc.textContent =
        stats.outputs_count != null ? `${stats.outputs_count} clips in outputs` : '';
    }

    const grid = document.getElementById('dashboard-clips');
    const clips = (clipsData.clips || []).slice(0, 8);
    grid.innerHTML = '';
    if (!clips.length) {
      grid.innerHTML = `
        <div class="empty-state" style="grid-column:1/-1">
          <div class="icon">🎬</div>
          <div class="title">No clips yet</div>
          <div class="desc">Run the pipeline to generate clips</div>
        </div>`;
    } else {
      clips.forEach((c, i) => grid.appendChild(renderClipCard(c, i)));
    }

    document.getElementById('status-whisper').textContent = cfg.whisper_model || '—';
    document.getElementById('status-scene').textContent = cfg.use_scene_detection ? 'enabled' : 'off';
    document.getElementById('status-vertical').textContent = cfg.export_vertical
      ? 'blur fill · subtitles on'
      : 'off';
    document.getElementById('status-visual').textContent = cfg.scoring_visual ? 'enabled' : 'disabled';
    document.getElementById('status-trending').textContent = cfg.use_trending ? 'on' : 'off';

    ['dot-scene', 'dot-vertical'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.className = 'dot ' + (id === 'dot-scene' ? (cfg.use_scene_detection ? 'dot-green' : 'dot-gray') : cfg.export_vertical ? 'dot-green' : 'dot-gray');
    });
    const dw = document.getElementById('dot-whisper');
    if (dw) dw.className = 'dot dot-green';
    const dv = document.getElementById('dot-visual');
    if (dv) dv.className = 'dot ' + (cfg.scoring_visual ? 'dot-green' : 'dot-amber');
    const dt = document.getElementById('dot-trending');
    if (dt) dt.className = 'dot ' + (cfg.use_trending ? 'dot-green' : 'dot-gray');
  } catch (e) {
    console.error(e);
  }
  await refreshLog();
}

async function startPipeline(command) {
  try {
    if (command === 'refresh') {
      if (
        !confirm(
          'Clear all ranked clips, candidate clips, and related manifests? Source videos under data/videos/ are kept. This cannot be undone.'
        )
      ) {
        return;
      }
    }

    const statusRes = await fetch(`${API}/api/run/status`);
    const st = await statusRes.json();
    if (st.running) {
      alert('Pipeline is already running.');
      return;
    }

    const limitEl = document.getElementById('opt-limit');
    const dryEl = document.getElementById('opt-dryrun');
    const body = {
      command,
      dry_run: dryEl && dryEl.checked,
      limit_videos: null,
    };
    if (limitEl && limitEl.value && command === 'run') {
      body.limit_videos = parseInt(limitEl.value, 10);
    }

    const res = await fetch(`${API}/api/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res.status === 409) {
      alert('Pipeline already running.');
      return;
    }
    if (!res.ok) {
      const err = await res.text();
      alert('Failed to start: ' + err);
      return;
    }

    document.getElementById('stop-btn').style.display = 'inline-flex';
    document.getElementById('run-spinner').style.display = 'block';
    document.getElementById('run-status-badge').innerHTML =
      '<span class="badge badge-amber">Running…</span>';

    if (runPollInterval) clearInterval(runPollInterval);
    runPollInterval = setInterval(async () => {
      const r = await fetch(`${API}/api/run/status`).catch(() => null);
      if (!r) return;
      const d = await r.json();
      if (!d.running && d.exit_code != null) {
        clearInterval(runPollInterval);
        runPollInterval = null;
        document.getElementById('stop-btn').style.display = 'none';
        document.getElementById('run-spinner').style.display = 'none';
        const ok = d.exit_code === 0;
        document.getElementById('run-status-badge').innerHTML = ok
          ? '<span class="badge badge-green">Complete</span>'
          : '<span class="badge" style="background:var(--red-bg);color:var(--red)">Failed</span>';
        refreshLog();
      }
    }, 2000);
  } catch (e) {
    console.error(e);
    alert('Error: ' + e.message);
  }
}

async function stopPipeline() {
  try {
    await fetch(`${API}/api/run/stop`, { method: 'POST' });
    document.getElementById('stop-btn').style.display = 'none';
    document.getElementById('run-spinner').style.display = 'none';
    if (runPollInterval) clearInterval(runPollInterval);
  } catch (e) {
    console.error(e);
  }
}

function renderOutputCard(clip) {
  const fname = clip.filename;
  const src = `${API}/api/outputs/${encodeURIComponent(fname)}`;
  const score = clip.score != null ? clip.score.toFixed(1) : '—';
  const dur = clip.duration != null ? clip.duration.toFixed(1) + 's' : '—';

  const wrap = document.createElement('div');
  wrap.className = 'clip-card';
  wrap.style.cursor = 'default';
  wrap.innerHTML = `
    <div class="clip-thumb">
      <video src="${src}" muted preload="metadata" playsinline></video>
      <div class="clip-score">${score}</div>
    </div>
    <div class="clip-info">
      <div class="clip-title" title="${(clip.text || '').replace(/"/g, '&quot;')}">${fname}</div>
      <div class="clip-meta">${dur}</div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn btn-sm preview-btn">Preview</button>
        <a class="btn btn-sm" href="${src}" download="${fname}">Download</a>
      </div>
    </div>
  `;
  wrap.querySelector('.preview-btn').addEventListener('click', (ev) => {
    ev.stopPropagation();
    openVideoModal(
      {
        text: clip.text,
        score: clip.score,
        duration_seconds: clip.duration,
        filepath: clip.filename,
      },
      fname
    );
  });
  return wrap;
}

async function loadOutputs() {
  const grid = document.getElementById('outputs-grid');
  if (!grid) return;
  const cnt = document.getElementById('output-count');
  try {
    const res = await fetch(`${API}/api/outputs`);
    const data = await res.json();
    const clips = data.clips || [];
    if (cnt) cnt.textContent = clips.length ? `(${clips.length} files)` : '';
    grid.innerHTML = '';
    if (!clips.length) {
      grid.innerHTML = `
        <div class="empty-state" style="grid-column:1/-1">
          <div class="icon">📁</div>
          <div class="title">No outputs yet</div>
          <div class="desc">Run the pipeline with export_vertical: true to generate clips</div>
        </div>`;
      return;
    }
    clips.forEach((c) => grid.appendChild(renderOutputCard(c)));
  } catch (e) {
    console.error(e);
    grid.innerHTML = '<div class="empty-state">Failed to load outputs</div>';
  }
}

let _configCache = {};

function escapeAttr(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;');
}

function setSelectValue(sel, value) {
  if (!sel || value == null || value === '') return;
  const v = String(value).trim();
  const found = Array.from(sel.options).some((o) => o.value === v);
  if (found) {
    sel.value = v;
    return;
  }
  const o = document.createElement('option');
  o.value = v;
  o.textContent = v + ' (from config)';
  sel.appendChild(o);
  sel.value = v;
}

function addQueryRow(text) {
  const t = text == null ? '' : String(text);
  const wrap = document.createElement('div');
  wrap.className = 'query-row';
  wrap.innerHTML =
    '<input type="text" class="cfg-query-input" value="' +
    escapeAttr(t) +
    '" placeholder="YouTube search query">' +
    '<button type="button" class="btn btn-sm query-remove" title="Remove">×</button>';
  const parent = document.getElementById('cfg-queries-rows');
  parent.appendChild(wrap);
  wrap.querySelector('.query-remove').addEventListener('click', () => {
    if (parent.querySelectorAll('.query-row').length <= 1) {
      wrap.querySelector('.cfg-query-input').value = '';
      return;
    }
    wrap.remove();
  });
}

function setQueriesFromList(lines) {
  const parent = document.getElementById('cfg-queries-rows');
  parent.innerHTML = '';
  const arr = lines && lines.length ? lines : [''];
  arr.forEach((line) => addQueryRow(line));
}

function getQueriesFromRows() {
  return Array.from(document.querySelectorAll('.cfg-query-input'))
    .map((i) => i.value.trim())
    .filter(Boolean);
}

async function loadConfig() {
  try {
    const res = await fetch(`${API}/api/config`);
    const cfg = await res.json();
    _configCache = cfg;
    setQueriesFromList(cfg.queries || []);
    document.getElementById('cfg-results-per-query').value = cfg.results_per_query ?? 10;
    document.getElementById('cfg-max-videos').value = cfg.max_videos_total ?? 25;
    document.getElementById('cfg-quality').value = cfg.download_quality || 'best';
    document.getElementById('cfg-min-dur').value = cfg.min_video_duration_seconds ?? 60;
    document.getElementById('cfg-max-dur').value = cfg.max_video_duration_seconds ?? 1800;
    document.getElementById('cfg-seg-min').value = cfg.segment_min_duration_seconds ?? 14;
    document.getElementById('cfg-seg-max').value = cfg.segment_max_duration_seconds ?? 28;
    document.getElementById('cfg-top-n').value = cfg.top_n_global ?? 10;
    setSelectValue(document.getElementById('cfg-whisper-model'), cfg.whisper_model || 'distil-large-v3');
    setSelectValue(document.getElementById('cfg-openai-model'), cfg.openai_model || 'gpt-4o-mini');
    document.getElementById('cfg-scoring-text').checked = !!cfg.scoring_text;
    document.getElementById('cfg-scoring-audio').checked = !!cfg.scoring_audio;
    document.getElementById('cfg-scoring-visual').checked = !!cfg.scoring_visual;
    document.getElementById('cfg-scene').checked = !!cfg.use_scene_detection;
    document.getElementById('cfg-vertical').checked = !!cfg.export_vertical;
    document.getElementById('cfg-trending').checked = !!cfg.use_trending;
    document.getElementById('cfg-prompt').value = cfg.openai_prompt || '';
  } catch (e) {
    console.error(e);
  }
}

async function saveConfig() {
  try {
    const cfg = { ..._configCache };
    cfg.queries = getQueriesFromRows();
    cfg.results_per_query = parseInt(document.getElementById('cfg-results-per-query').value, 10);
    cfg.max_videos_total = parseInt(document.getElementById('cfg-max-videos').value, 10);
    cfg.download_quality = document.getElementById('cfg-quality').value;
    cfg.min_video_duration_seconds = parseFloat(document.getElementById('cfg-min-dur').value);
    cfg.max_video_duration_seconds = parseFloat(document.getElementById('cfg-max-dur').value);
    cfg.segment_min_duration_seconds = parseFloat(document.getElementById('cfg-seg-min').value);
    cfg.segment_max_duration_seconds = parseFloat(document.getElementById('cfg-seg-max').value);
    cfg.top_n_global = parseInt(document.getElementById('cfg-top-n').value, 10);
    cfg.whisper_model = document.getElementById('cfg-whisper-model').value;
    cfg.openai_model = document.getElementById('cfg-openai-model').value;
    cfg.scoring_text = document.getElementById('cfg-scoring-text').checked;
    cfg.scoring_audio = document.getElementById('cfg-scoring-audio').checked;
    cfg.scoring_visual = document.getElementById('cfg-scoring-visual').checked;
    cfg.use_scene_detection = document.getElementById('cfg-scene').checked;
    cfg.export_vertical = document.getElementById('cfg-vertical').checked;
    cfg.use_trending = document.getElementById('cfg-trending').checked;
    cfg.openai_prompt = document.getElementById('cfg-prompt').value;

    const res = await fetch(`${API}/api/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    if (res.ok) {
      _configCache = cfg;
      const el = document.getElementById('config-saved');
      el.style.display = 'block';
      setTimeout(() => {
        el.style.display = 'none';
      }, 3000);
    }
  } catch (e) {
    console.error(e);
    alert('Save failed: ' + e.message);
  }
}

function handleFileSelect(input) {
  const f = input.files && input.files[0];
  selectedFile = f || null;
  const info = document.getElementById('upload-info');
  const fn = document.getElementById('upload-filename');
  const fs = document.getElementById('upload-filesize');
  if (f) {
    info.style.display = 'block';
    document.getElementById('ingest-progress').style.display = 'none';
    document.getElementById('ingest-status').innerHTML = '';
    if (fn) fn.textContent = f.name;
    if (fs) fs.textContent = (f.size / 1024 / 1024).toFixed(1) + ' MB · ' + (f.type || 'video');
  } else {
    info.style.display = 'none';
  }
}

// Legacy helpers used by submitSplice
function setProgressUI(prefix, percent, label) {
  const wrap = document.getElementById(prefix + '-progress-wrap');
  const fill = document.getElementById(prefix + '-progress-fill');
  const lab = document.getElementById(prefix + '-progress-label');
  if (wrap) wrap.style.display = 'block';
  if (fill) fill.style.width = Math.min(100, Math.max(0, percent)) + '%';
  if (lab) lab.textContent = label || '';
}

function hideProgressUI(prefix) {
  const wrap = document.getElementById(prefix + '-progress-wrap');
  if (wrap) wrap.style.display = 'none';
}

function smoothWhisperProgress(serverPct, label, elapsedMs) {
  if (!label || label.indexOf('Transcribing') === -1) return serverPct;
  if (elapsedMs < 6000) return serverPct;
  const extra = Math.min(16, ((elapsedMs - 6000) / 14000) * 16);
  return Math.min(52, serverPct + extra);
}

function serverToBarPercent(serverPct, label, elapsedMs) {
  const s = smoothWhisperProgress(Number(serverPct) || 0, label, elapsedMs);
  return 15 + (s / 100) * 85;
}

function uploadBarPercent(ratio) {
  return ratio * 15;
}

function uploadFormDataWithProgress(url, formData, onUploadProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) onUploadProgress(e.loaded / e.total);
    });
    xhr.onload = () => {
      let body = {};
      try {
        body = JSON.parse(xhr.responseText || '{}');
      } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
      } else {
        const msg =
          typeof body.detail === 'string'
            ? body.detail
            : Array.isArray(body.detail)
              ? body.detail.map((x) => x.msg || JSON.stringify(x)).join(', ')
              : body.detail
                ? JSON.stringify(body.detail)
                : xhr.statusText || 'Request failed';
        reject(new Error(msg));
      }
    };
    xhr.onerror = () => reject(new Error('Network error'));
    xhr.send(formData);
  });
}

async function pollPipelineUntilDone(pipelineStartMs, onUpdate) {
  const maxMs = 120 * 60 * 1000;
  const absStart = Date.now();
  while (Date.now() - absStart < maxMs) {
    const res = await fetch(`${API}/api/run/progress`);
    const data = await res.json();
    const elapsed = Date.now() - pipelineStartMs;
    onUpdate(data, elapsed);
    if (!data.running) {
      return data;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error('Timed out waiting for pipeline');
}

// Pipeline stages for the ingest hero progress visualization
const PIPELINE_STAGES = [
  { key: 'uploading',    label: 'Upload',     pct: 0  },
  { key: 'ingesting',    label: 'Ingest',     pct: 18 },
  { key: 'transcribing', label: 'Transcribe', pct: 40 },
  { key: 'scoring',      label: 'Score',      pct: 65 },
  { key: 'exporting',    label: 'Export',     pct: 85 },
  { key: 'done',         label: 'Done',       pct: 100 },
];

function buildProgressStages() {
  const dotsEl = document.getElementById('progress-stage-dots');
  const labelsEl = document.getElementById('progress-stage-labels');
  if (!dotsEl || !labelsEl) return;
  dotsEl.innerHTML = PIPELINE_STAGES.map((s, i) =>
    `<div class="stage-dot" id="sdot-${i}"></div>`
  ).join('');
  labelsEl.innerHTML = PIPELINE_STAGES.map((s, i) =>
    `<div class="stage-label-item" id="slabel-${i}">${s.label}</div>`
  ).join('');
}

function updateProgressUI(stageName, pct) {
  const fill = document.getElementById('progress-fill');
  const stageNameEl = document.getElementById('progress-stage-name');
  const pctEl = document.getElementById('progress-pct');
  if (fill) fill.style.width = Math.min(100, pct) + '%';
  if (pctEl) pctEl.textContent = Math.round(pct) + '%';

  const stageIdx = PIPELINE_STAGES.findIndex((s) => s.key === stageName);
  PIPELINE_STAGES.forEach((s, i) => {
    const dot = document.getElementById('sdot-' + i);
    const lbl = document.getElementById('slabel-' + i);
    if (!dot || !lbl) return;
    if (i < stageIdx) {
      dot.className = 'stage-dot done';
      lbl.className = 'stage-label-item done';
    } else if (i === stageIdx) {
      dot.className = 'stage-dot active';
      lbl.className = 'stage-label-item active';
    } else {
      dot.className = 'stage-dot';
      lbl.className = 'stage-label-item';
    }
  });

  const names = {
    uploading:    'Uploading file...',
    ingesting:    'Registering video...',
    transcribing: 'Transcribing with Whisper...',
    scoring:      'Scoring segments...',
    exporting:    'Exporting vertical clips...',
    done:         'Complete!',
    starting:     'Starting pipeline...',
    failed:       'Failed',
  };
  if (stageNameEl) stageNameEl.textContent = names[stageName] || stageName;
}

function startIngestPolling() {
  if (_ingestPollInterval) clearInterval(_ingestPollInterval);
  _ingestPollInterval = setInterval(async () => {
    try {
      const res = await fetch(`${API}/api/run/stages`);
      if (!res.ok) return;
      const data = await res.json();

      updateProgressUI(data.stage, data.progress);

      if (!data.running && data.stage === 'done') {
        clearInterval(_ingestPollInterval);
        _ingestPollInterval = null;
        document.getElementById('ingest-status').innerHTML =
          '<span style="color:var(--green)">✓ Done! Your clips are below.</span>';
        setTimeout(() => {
          loadIngested();
          loadOutputs();
          document.querySelector('.ingest-clips-section')?.scrollIntoView({
            behavior: 'smooth',
            block: 'start',
          });
          resetIngestUI();
        }, 2500);
      } else if (!data.running && data.stage !== 'idle' && data.stage !== 'starting') {
        const statusRes = await fetch(`${API}/api/run/status`);
        const st = await statusRes.json();
        if (st.exit_code !== null && st.exit_code !== 0) {
          clearInterval(_ingestPollInterval);
          _ingestPollInterval = null;
          document.getElementById('ingest-status').innerHTML =
            '<span style="color:var(--red)">Pipeline failed. Check Run pipeline for log.</span>';
          setTimeout(resetIngestUI, 3000);
        }
      }
    } catch (e) { /* ignore */ }
  }, 1500);
}

function resetIngestUI() {
  const btn = document.getElementById('ingest-btn');
  if (btn) btn.disabled = false;
  clearFileSelection();
}

function clearFileSelection() {
  selectedFile = null;
  document.getElementById('upload-info').style.display = 'none';
  document.getElementById('ingest-progress').style.display = 'none';
  document.getElementById('ingest-status').innerHTML = '';
  const fi = document.getElementById('file-input');
  if (fi) fi.value = '';
  const btn = document.getElementById('ingest-btn');
  if (btn) btn.disabled = false;
}

function submitIngest() {
  if (!selectedFile) {
    document.getElementById('ingest-status').innerHTML =
      '<span style="color:var(--amber)">Select a file first.</span>';
    return;
  }

  const title = (document.getElementById('upload-title').value || '').trim();
  const fd = new FormData();
  fd.append('file', selectedFile);
  fd.append('title', title);

  document.getElementById('upload-info').style.display = 'none';
  document.getElementById('ingest-progress').style.display = 'block';
  buildProgressStages();
  updateProgressUI('uploading', 0);

  const btn = document.getElementById('ingest-btn');
  if (btn) btn.disabled = true;

  const xhr = new XMLHttpRequest();

  xhr.upload.onprogress = function (e) {
    if (!e.lengthComputable) return;
    const pct = (e.loaded / e.total) * 18;
    updateProgressUI('uploading', pct);
    document.getElementById('ingest-status').textContent =
      `Uploading: ${(e.loaded / 1024 / 1024).toFixed(1)} / ${(e.total / 1024 / 1024).toFixed(1)} MB`;
  };

  xhr.onload = function () {
    if (xhr.status >= 200 && xhr.status < 300) {
      updateProgressUI('ingesting', 18);
      document.getElementById('ingest-status').textContent = 'Upload complete. Pipeline running...';
      startIngestPolling();
    } else {
      let msg = xhr.statusText;
      try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (e) {}
      document.getElementById('ingest-status').innerHTML =
        '<span style="color:var(--red)">Upload failed: ' + escapeHtml(msg) + '</span>';
      resetIngestUI();
    }
  };

  xhr.onerror = function () {
    document.getElementById('ingest-status').innerHTML =
      '<span style="color:var(--red)">Network error during upload.</span>';
    resetIngestUI();
  };

  xhr.open('POST', `${API}/api/ingest`);
  xhr.send(fd);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function clipForLibraryCard(clip) {
  const out = clip.clip_id && _outputsByClipId[clip.clip_id];
  let fromPath = '';
  if (clip.filepath) {
    const p = String(clip.filepath).replace(/\\/g, '/');
    fromPath = p.split('/').pop() || '';
  }
  const _previewName = out || fromPath || clip.filename || '';
  return Object.assign({}, clip, { _previewName });
}

function syncLibTabPanels() {
  document.querySelectorAll('.lib-subtab[data-lib-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.libTab === _libTab);
  });
  const vidPanel = document.getElementById('library-videos-panel');
  const clipPanel = document.getElementById('library-clips-panel');
  if (vidPanel) vidPanel.style.display = _libTab === 'videos' ? '' : 'none';
  if (clipPanel) clipPanel.style.display = _libTab === 'clips' ? '' : 'none';
}

function setLibTab(tab) {
  _libTab = tab;
  syncLibTabPanels();
  if (tab === 'videos') renderLibrary();
  else renderLibraryClips();
}

async function loadLibrary() {
  const grid = document.getElementById('library-grid');
  const clipGrid = document.getElementById('library-clips-grid');
  if (grid) {
    grid.innerHTML = '<div class="lib-empty"><div class="icon">⏳</div><div class="title">Loading...</div></div>';
  }
  if (clipGrid) {
    clipGrid.innerHTML =
      '<div class="empty-state" style="grid-column:1/-1"><div class="icon">⏳</div><div class="title">Loading...</div></div>';
  }
  try {
    const [libRes, clipsRes, outRes] = await Promise.all([
      fetch(`${API}/api/library`),
      fetch(`${API}/api/clips`),
      fetch(`${API}/api/outputs`),
    ]);
    const libData = await libRes.json().catch(() => ({}));
    const clipsData = await clipsRes.json().catch(() => ({ clips: [] }));
    const outData = await outRes.json().catch(() => ({ clips: [] }));

    _libraryData = libData.videos || [];
    _outputsByClipId = {};
    for (const o of outData.clips || []) {
      if (o.clip_id && o.filename) _outputsByClipId[o.clip_id] = o.filename;
    }
    _libraryClipsData = clipsData.clips || [];

    syncLibTabPanels();
    if (_libTab === 'videos') renderLibrary();
    else renderLibraryClips();
  } catch (e) {
    console.error(e);
    if (grid) {
      grid.innerHTML = '<div class="lib-empty"><div class="title">Failed to load library</div></div>';
    }
    if (clipGrid) {
      clipGrid.innerHTML =
        '<div class="empty-state" style="grid-column:1/-1"><div class="title">Failed to load</div></div>';
    }
  }
}

function setLibFilter(filter) {
  _libFilter = filter;
  document.querySelectorAll('.filter-btn[data-filter]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.filter === filter);
  });
  if (_libTab === 'videos') renderLibrary();
}

function renderLibraryClips() {
  const grid = document.getElementById('library-clips-grid');
  const countEl = document.getElementById('lib-count');
  const search = (document.getElementById('lib-clip-search')?.value || '').toLowerCase().trim();
  if (!grid) return;

  let filtered = _libraryClipsData.slice();
  if (search) {
    filtered = filtered.filter((c) => {
      const t = (c.text || '').toLowerCase();
      const vid = (c.video_id || '').toLowerCase();
      const cid = (c.clip_id || '').toLowerCase();
      return t.includes(search) || vid.includes(search) || cid.includes(search);
    });
  }

  if (countEl) {
    if (!_libraryClipsData.length) {
      countEl.textContent = '';
    } else if (filtered.length) {
      countEl.textContent = `(${filtered.length} of ${_libraryClipsData.length} clips)`;
    } else {
      countEl.textContent = `(0 of ${_libraryClipsData.length} clips)`;
    }
  }

  grid.innerHTML = '';
  if (!filtered.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1">
        <div class="icon">🎞️</div>
        <div class="title">${search ? 'No matching clips' : 'No ranked clips yet'}</div>
        <div class="desc">${
          search ? 'Try another search' : 'Run ingest + rank or the pipeline to populate the top ranked manifest'
        }</div>
      </div>`;
    return;
  }

  filtered.forEach((c, i) => grid.appendChild(renderClipCard(clipForLibraryCard(c), i)));
}

function renderLibrary() {
  const grid = document.getElementById('library-grid');
  const countEl = document.getElementById('lib-count');
  const search = (document.getElementById('lib-search')?.value || '').toLowerCase().trim();
  if (!grid) return;

  let filtered = _libraryData;

  if (_libFilter !== 'all') {
    filtered = filtered.filter((v) => v.status === _libFilter);
  }

  if (search) {
    filtered = filtered.filter(
      (v) =>
        (v.title || '').toLowerCase().includes(search) ||
        (v.video_id || '').toLowerCase().includes(search)
    );
  }

  if (countEl) {
    if (!_libraryData.length) {
      countEl.textContent = '';
    } else if (filtered.length) {
      countEl.textContent = `(${filtered.length} of ${_libraryData.length} videos)`;
    } else {
      countEl.textContent = `(0 of ${_libraryData.length} videos)`;
    }
  }

  if (!filtered.length) {
    grid.innerHTML = `
      <div class="lib-empty">
        <div class="icon">📭</div>
        <div class="title">${search || _libFilter !== 'all' ? 'No matches' : 'No videos yet'}</div>
        <div class="desc">${
          search
            ? 'Try a different search term'
            : _libFilter !== 'all'
            ? 'No videos with this status'
            : 'Ingest a video to start your library'
        }</div>
      </div>`;
    return;
  }

  grid.innerHTML = '';
  filtered.forEach((v) => grid.appendChild(renderLibCard(v)));
}

function renderLibCard(v) {
  const dur = v.duration_seconds ? (v.duration_seconds / 60).toFixed(1) + ' min' : '—';

  const dateStr = v.download_time
    ? new Date(v.download_time).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
    : '—';

  const statusBadge =
    {
      ingested: '<span class="badge badge-accent">Ingested</span>',
      ranked: '<span class="badge badge-green">Ranked</span>',
      exhausted: '<span class="badge badge-exhausted">Exhausted</span>',
    }[v.status] || '';

  const clipLine =
    v.clip_count > 0
      ? `<strong>${v.clip_count}</strong> candidate clips extracted`
      : v.status === 'ingested'
      ? '<span style="color:var(--amber)">Not yet ranked</span>'
      : 'No clips';

  const rankBtn =
    v.status === 'ingested'
      ? `<button class="btn btn-sm btn-primary" onclick="rankVideo('${escapeHtml(v.video_id)}')">▶ Rank now</button>`
      : '';

  const card = document.createElement('div');
  card.className = 'lib-card';
  card.innerHTML = `
    <div class="lib-card-header">
      <div class="lib-card-icon">🎬</div>
      <div style="flex:1;min-width:0">
        <div class="lib-card-title">${escapeHtml(v.title || v.video_id)}</div>
      </div>
      ${statusBadge}
    </div>
    <div class="lib-card-meta">
      <span>🕐 ${dur}</span>
      <span>📅 ${dateStr}</span>
      <span style="font-family:var(--mono);font-size:10px;color:var(--text-dim)">${escapeHtml(v.video_id)}</span>
    </div>
    <div class="lib-card-footer">
      <div class="lib-clip-count">${clipLine}</div>
      ${rankBtn}
    </div>
  `;
  return card;
}

async function rankVideo(videoId) {
  try {
    const stRes = await fetch(`${API}/api/run/status`);
    const st = await stRes.json();
    if (st.running) {
      alert('Pipeline is already running. Wait for it to finish first.');
      return;
    }
  } catch (e) { /* continue */ }

  const res = await fetch(`${API}/api/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command: 'rank', dry_run: false }),
  }).catch(() => null);

  if (res && res.ok) {
    navigate('run');
  } else {
    alert('Failed to start ranking. Check the Run pipeline page.');
  }
}

async function clearIngested() {
  if (!confirm('Remove all ingested video records? This only clears the manifest list — source files and outputs are untouched.')) return;
  try {
    await fetch(`${API}/api/ingested`, { method: 'DELETE' });
    loadIngested();
  } catch (e) {
    console.error(e);
  }
}

async function loadIngested() {
  const el = document.getElementById('ingested-list');
  if (!el) return;
  try {
    const res = await fetch(`${API}/api/videos`);
    const data = await res.json();
    const locals = (data.videos || []).filter((v) => v.local);
    if (!locals.length) {
      el.innerHTML = '<div style="color:var(--text-dim);font-size:13px;text-align:center;padding:1.5rem 0">No videos ingested yet</div>';
      return;
    }
    el.innerHTML = locals.map((v) => `
      <div class="recent-ingest-item">
        <div class="recent-ingest-thumb">🎬</div>
        <div style="flex:1;min-width:0">
          <div class="recent-ingest-name">${escapeHtml(v.title || v.video_id)}</div>
          <div class="recent-ingest-meta">${Math.round(v.duration_seconds || 0)}s · ${escapeHtml(v.video_id)}</div>
        </div>
        <span class="badge badge-green" style="flex-shrink:0">ingested</span>
      </div>
    `).join('');
  } catch (e) {
    console.error(e);
    el.textContent = 'Failed to load';
  }
}

async function loadRegistry() {
  try {
    const res = await fetch(`${API}/api/registry`);
    const data = await res.json();
    document.getElementById('reg-used').textContent = data.used_clips ?? '—';
    document.getElementById('reg-processed').textContent = data.processed_videos_count ?? '—';
    document.getElementById('reg-exhausted').textContent = data.exhausted_videos ?? '—';

    const table = document.getElementById('reg-table');
    const rows = data.processed || [];
    if (!rows.length) {
      table.innerHTML = '<div style="color:var(--text-dim)">No processed videos recorded.</div>';
      return;
    }
    table.innerHTML =
      '<table class="status-table" style="width:100%"><tbody>' +
      rows
        .map(
          (r) =>
            `<tr><td>${escapeHtml(r.title)}</td><td style="font-family:var(--mono);font-size:11px">${escapeHtml(
              r.video_id
            )}</td><td>${r.times_processed}</td><td>${
              r.exhausted ? '<span class="badge badge-amber">exhausted</span>' : '<span class="badge badge-green">ok</span>'
            }</td></tr>`
        )
        .join('') +
      '</tbody></table>';
  } catch (e) {
    console.error(e);
  }
}

async function clearRegistry() {
  if (!confirm('Clear the used-clips registry?')) return;
  try {
    await fetch(`${API}/api/registry/used-clips`, { method: 'DELETE' });
    loadRegistry();
  } catch (e) {
    console.error(e);
  }
}

function openVideoModal(clip, filenameOverride) {
  const fname =
    filenameOverride ||
    outputFilename(clip) ||
    (clip.filepath ? clip.filepath.split('/').pop() : '');
  if (!fname) {
    alert('No video file available for this clip.');
    return;
  }
  const src = `${API}/api/outputs/${encodeURIComponent(fname)}`;
  const vid = document.getElementById('modal-video');
  vid.pause();
  vid.src = src;
  vid.load();

  document.getElementById('modal-title').textContent = fname;
  const sc = document.getElementById('modal-score');
  sc.textContent = clip.score != null ? 'Score ' + Number(clip.score).toFixed(1) : '';
  document.getElementById('modal-duration').textContent =
    clip.duration_seconds != null ? Number(clip.duration_seconds).toFixed(1) + 's' : '';
  document.getElementById('modal-text').textContent = clip.text || '';
  const dl = document.getElementById('modal-download');
  dl.href = src;
  dl.download = fname;

  document.getElementById('video-modal').classList.add('open');
}

function closeVideoModal() {
  const vid = document.getElementById('modal-video');
  vid.pause();
  vid.src = '';
  document.getElementById('video-modal').classList.remove('open');
}

function closeModal(ev) {
  if (ev.target.id === 'video-modal') closeVideoModal();
}

// Upload zone drag & drop
const uz = document.getElementById('upload-zone');
if (uz) {
  ['dragenter', 'dragover'].forEach((ev) =>
    uz.addEventListener(ev, (e) => {
      e.preventDefault();
      uz.classList.add('drag-over');
    })
  );
  ['dragleave', 'drop'].forEach((ev) =>
    uz.addEventListener(ev, (e) => {
      e.preventDefault();
      uz.classList.remove('drag-over');
    })
  );
  uz.addEventListener('drop', (e) => {
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) {
      selectedFile = f;
      const info = document.getElementById('upload-info');
      const fn = document.getElementById('upload-filename');
      const fs = document.getElementById('upload-filesize');
      if (info) info.style.display = 'block';
      if (fn) fn.textContent = f.name;
      if (fs) fs.textContent = (f.size / 1024 / 1024).toFixed(1) + ' MB · ' + (f.type || 'video');
      document.getElementById('ingest-progress').style.display = 'none';
      document.getElementById('ingest-status').innerHTML = '';
    }
  });
}

function startStatusPolling() {
  if (statusPollInterval) clearInterval(statusPollInterval);
  statusPollInterval = setInterval(async () => {
    try {
      const res = await fetch(`${API}/api/run/status`);
      if (!res.ok) return;
      const data = await res.json();
      if (currentPage === 'run' && data.running) {
        document.getElementById('stop-btn').style.display = 'inline-flex';
        document.getElementById('run-spinner').style.display = 'block';
      }
    } catch (e) {
      /* ignore */
    }
  }, 3000);
}

async function submitSplice() {
  const input = document.getElementById('splice-files');
  const statusEl = document.getElementById('splice-status');
  const btn = document.getElementById('splice-submit');
  if (!input.files || !input.files.length) {
    statusEl.innerHTML = '<span style="color:var(--amber)">Select at least one video.</span>';
    return;
  }
  try {
    const stRes = await fetch(`${API}/api/run/status`);
    const st = await stRes.json();
    if (st.running) {
      statusEl.innerHTML = '<span style="color:var(--amber)">Pipeline is already running.</span>';
      return;
    }
  } catch (e) {
    /* continue */
  }

  const fd = new FormData();
  for (let i = 0; i < input.files.length; i++) {
    fd.append('files', input.files[i]);
  }
  if (btn) btn.disabled = true;
  statusEl.innerHTML = '';
  setProgressUI('splice', 0, 'Uploading…');
  try {
    await uploadFormDataWithProgress(`${API}/api/splice`, fd, (ratio) => {
      setProgressUI('splice', uploadBarPercent(ratio), 'Uploading…');
    });
    const pipelineStartMs = Date.now();
    setProgressUI('splice', 15, 'Starting pipeline…');
    const fin = await pollPipelineUntilDone(pipelineStartMs, (data, elapsed) => {
      const pct = serverToBarPercent(data.percent, data.label, elapsed);
      setProgressUI('splice', pct, data.label || 'Working…');
    });
    if (fin.exit_code === 0) {
      setProgressUI('splice', 100, 'Complete');
      loadOutputs();
      statusEl.innerHTML =
        '<span style="color:var(--green)">Splice finished — open Ingest video to see clips below the upload.</span>';
    } else {
      setProgressUI('splice', Math.min(fin.percent || 99, 99), 'Failed');
      statusEl.innerHTML =
        '<span style="color:var(--red)">Pipeline failed. Check Run pipeline for the log.</span>';
    }
  } catch (e) {
    console.error(e);
    hideProgressUI('splice');
    statusEl.innerHTML = '<span style="color:var(--red)">' + escapeHtml(e.message) + '</span>';
  } finally {
    if (btn) btn.disabled = false;
  }
}

const spliceFiles = document.getElementById('splice-files');
if (spliceFiles) {
  spliceFiles.addEventListener('change', function () {
    const list = document.getElementById('splice-file-list');
    const files = this.files;
    if (!files || !files.length) {
      list.innerHTML = '';
      return;
    }
    list.innerHTML = Array.from(files)
      .map(
        (f) =>
          `<div class="splice-file-line">${escapeHtml(f.name)} (${(f.size / 1024 / 1024).toFixed(1)} MB)</div>`
      )
      .join('');
  });
}

refreshDashboard();
startStatusPolling();
