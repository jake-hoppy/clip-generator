/**
 * ClipGen — frontend logic
 */
const API = window.location.origin;

let currentPage = 'upload';
let selectedFile = null;
let _pipelinePoll = null;
let _logPoll = null;
let _libTab = 'clips';
let _libraryClips = [];
let _libraryVideos = [];
let _outputsByClipId = {};

// ============================================================
// Navigation
// ============================================================

function navigate(page) {
  stopAllPolling();
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const pg = document.getElementById('page-' + page);
  if (pg) pg.classList.add('active');
  const nav = document.querySelector(`[data-page="${page}"]`);
  if (nav) nav.classList.add('active');
  currentPage = page;

  if (page === 'library') loadLibrary();
  if (page === 'settings') loadSettings();
}

document.querySelectorAll('.nav-item[data-page]').forEach(item => {
  item.addEventListener('click', () => navigate(item.dataset.page));
});

// ============================================================
// Polling helpers
// ============================================================

function stopAllPolling() {
  if (_pipelinePoll) { clearInterval(_pipelinePoll); _pipelinePoll = null; }
  if (_logPoll) { clearInterval(_logPoll); _logPoll = null; }
}

// ============================================================
// Upload page — file selection
// ============================================================

const dropzone = document.getElementById('upload-dropzone');
const fileInput = document.getElementById('file-input');

if (dropzone) {
  dropzone.addEventListener('click', () => fileInput.click());
  ['dragenter', 'dragover'].forEach(ev =>
    dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add('drag-over'); })
  );
  ['dragleave', 'drop'].forEach(ev =>
    dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.remove('drag-over'); })
  );
  dropzone.addEventListener('drop', e => {
    const f = e.dataTransfer.files?.[0];
    if (f) selectFile(f);
  });
}

if (fileInput) {
  fileInput.addEventListener('change', () => {
    if (fileInput.files?.[0]) selectFile(fileInput.files[0]);
  });
}

function selectFile(f) {
  selectedFile = f;
  document.getElementById('file-name').textContent = f.name;
  document.getElementById('file-size').textContent =
    (f.size / 1024 / 1024).toFixed(1) + ' MB';
  document.getElementById('upload-card').style.display = 'none';
  document.getElementById('upload-config').style.display = 'block';
  document.getElementById('pipeline-progress').style.display = 'none';
  document.getElementById('results-section').style.display = 'none';
}

function clearFile() {
  selectedFile = null;
  if (fileInput) fileInput.value = '';
  document.getElementById('upload-card').style.display = 'block';
  document.getElementById('upload-config').style.display = 'none';
}

function adjustClipCount(delta) {
  const input = document.getElementById('clip-count');
  const val = Math.max(1, Math.min(50, (parseInt(input.value) || 5) + delta));
  input.value = val;
}

function resetUpload() {
  clearFile();
  document.getElementById('pipeline-progress').style.display = 'none';
  document.getElementById('results-section').style.display = 'none';
  document.getElementById('upload-card').style.display = 'block';
}

// ============================================================
// Upload page — start clipping
// ============================================================

function startClipping() {
  if (!selectedFile) return;

  const clipCount = document.getElementById('clip-count').value || '5';
  const title = (document.getElementById('upload-title').value || '').trim();
  const autoYT = document.getElementById('auto-youtube').checked;

  // Show pipeline progress
  document.getElementById('upload-config').style.display = 'none';
  document.getElementById('pipeline-progress').style.display = 'block';
  document.getElementById('results-section').style.display = 'none';
  document.getElementById('stop-btn').style.display = 'inline-flex';

  // Set file banner
  document.getElementById('pipeline-file-banner').textContent =
    selectedFile.name + ' — ' + (selectedFile.size / 1024 / 1024).toFixed(1) + ' MB — ' + clipCount + ' clips';

  // Reset all nodes
  resetNodes();
  setNodeState('upload', 'active');
  updatePipelineBar(0, 'Uploading...');

  // Build FormData
  const fd = new FormData();
  fd.append('file', selectedFile);
  fd.append('title', title);
  fd.append('top_n', clipCount);

  // Upload with progress
  const xhr = new XMLHttpRequest();

  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = (e.loaded / e.total) * 15;
    updatePipelineBar(pct, 'Uploading... ' + Math.round(e.loaded / e.total * 100) + '%');
  };

  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      setNodeState('upload', 'completed');
      setNodeState('ingest', 'active');
      updatePipelineBar(15, 'Upload complete. Processing...');
      startPipelinePolling(autoYT);
    } else {
      let msg = 'Upload failed';
      try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (_) {}
      setNodeState('upload', 'failed');
      updatePipelineBar(0, msg);
      document.getElementById('stop-btn').style.display = 'none';
    }
  };

  xhr.onerror = () => {
    setNodeState('upload', 'failed');
    updatePipelineBar(0, 'Network error during upload');
    document.getElementById('stop-btn').style.display = 'none';
  };

  xhr.open('POST', `${API}/api/ingest`);
  xhr.send(fd);
}

// ============================================================
// Pipeline progress — node management
// ============================================================

const STAGES = ['upload', 'ingest', 'transcribe', 'score', 'export', 'youtube'];

const STAGE_MAP = {
  'idle': { idx: -1, label: 'Idle' },
  'starting': { idx: 1, label: 'Starting...' },
  'ingesting': { idx: 1, label: 'Preparing video...' },
  'transcribing': { idx: 2, label: 'Transcribing with Whisper...' },
  'scoring': { idx: 3, label: 'Scoring segments...' },
  'exporting': { idx: 4, label: 'Exporting clips...' },
  'downloading': { idx: 1, label: 'Downloading...' },
  'done': { idx: 5, label: 'Complete!' },
  'failed': { idx: -1, label: 'Pipeline failed' },
};

function resetNodes() {
  STAGES.forEach(s => {
    const node = document.querySelector(`.pipeline-node[data-stage="${s}"]`);
    if (node) node.className = 'pipeline-node';
  });
}

function setNodeState(stage, state) {
  const node = document.querySelector(`.pipeline-node[data-stage="${stage}"]`);
  if (!node) return;
  node.classList.remove('completed', 'active', 'failed');
  if (state) node.classList.add(state);
}

function setNodesFromStage(stageKey) {
  const info = STAGE_MAP[stageKey];
  if (!info) return;

  STAGES.forEach((s, i) => {
    if (i < info.idx) setNodeState(s, 'completed');
    else if (i === info.idx) setNodeState(s, 'active');
    else setNodeState(s, null);
  });
}

function updatePipelineBar(pct, text) {
  const fill = document.getElementById('pipeline-bar-fill');
  const stageText = document.getElementById('pipeline-stage-text');
  const pctEl = document.getElementById('pipeline-pct');
  if (fill) fill.style.width = Math.min(100, pct).toFixed(1) + '%';
  if (stageText) stageText.textContent = text || '';
  if (pctEl) pctEl.textContent = Math.round(pct) + '%';
}

// ============================================================
// Pipeline polling
// ============================================================

function startPipelinePolling(autoYT) {
  if (_pipelinePoll) clearInterval(_pipelinePoll);
  _pipelinePoll = setInterval(async () => {
    try {
      const [stagesRes, logRes] = await Promise.all([
        fetch(`${API}/api/run/stages`),
        fetch(`${API}/api/log`),
      ]);
      const stages = await stagesRes.json();
      const logText = await logRes.text();

      // Update nodes
      setNodesFromStage(stages.stage);

      // Update bar
      const pct = stages.progress || 0;
      const info = STAGE_MAP[stages.stage] || { label: stages.stage };
      updatePipelineBar(pct, info.label);

      // Update log
      const logBox = document.getElementById('pipeline-log-box');
      if (logBox) {
        logBox.innerHTML = formatLog(logText);
        logBox.scrollTop = logBox.scrollHeight;
      }

      // Check if done
      if (!stages.running && (stages.stage === 'done' || stages.stage === 'failed')) {
        clearInterval(_pipelinePoll);
        _pipelinePoll = null;
        document.getElementById('stop-btn').style.display = 'none';

        if (stages.stage === 'done') {
          setNodesFromStage('done');
          updatePipelineBar(95, 'Clips ready!');

          if (autoYT) {
            // Attempt YouTube upload
            setNodeState('youtube', 'active');
            updatePipelineBar(96, 'Uploading top 2 to YouTube...');
            try {
              const ytRes = await fetch(`${API}/api/youtube/upload-top`, { method: 'POST' });
              const ytData = await ytRes.json();
              if (ytRes.ok && ytData.uploaded > 0) {
                setNodeState('youtube', 'completed');
                updatePipelineBar(100, 'Done! ' + ytData.uploaded + ' clips uploaded to YouTube.');
                document.getElementById('youtube-upload-status').innerHTML =
                  '<span style="color:var(--green)">' + ytData.uploaded + ' clip(s) uploaded to YouTube.</span>' +
                  (ytData.urls ? '<br>' + ytData.urls.map(u => '<a href="' + esc(u) + '" target="_blank" style="color:var(--accent)">' + esc(u) + '</a>').join('<br>') : '');
              } else {
                setNodeState('youtube', 'failed');
                updatePipelineBar(98, 'Clips ready. YouTube upload skipped.');
                const msg = ytData.detail || ytData.error || 'Not configured';
                document.getElementById('youtube-upload-status').innerHTML =
                  '<span style="color:var(--amber)">YouTube: ' + esc(msg) + '</span>';
              }
            } catch (e) {
              setNodeState('youtube', 'failed');
              updatePipelineBar(98, 'Clips ready. YouTube upload failed.');
              document.getElementById('youtube-upload-status').innerHTML =
                '<span style="color:var(--amber)">YouTube upload failed: ' + esc(e.message) + '</span>';
            }
          } else {
            // Skip YouTube node
            updatePipelineBar(100, 'Done!');
          }

          // Load results
          await loadResults();
        } else {
          updatePipelineBar(pct, 'Pipeline failed. Check log for details.');
        }
      }
    } catch (e) { /* ignore */ }
  }, 1500);
}

async function stopPipeline() {
  try {
    await fetch(`${API}/api/run/stop`, { method: 'POST' });
    if (_pipelinePoll) { clearInterval(_pipelinePoll); _pipelinePoll = null; }
    document.getElementById('stop-btn').style.display = 'none';
    updatePipelineBar(0, 'Stopped.');
  } catch (e) { console.error(e); }
}

// ============================================================
// Results
// ============================================================

async function loadResults() {
  const grid = document.getElementById('results-grid');
  const section = document.getElementById('results-section');
  if (!grid || !section) return;

  try {
    const res = await fetch(`${API}/api/outputs`);
    const data = await res.json();
    const clips = data.clips || [];

    if (!clips.length) {
      // Try ranked clips instead
      const clipsRes = await fetch(`${API}/api/clips`);
      const clipsData = await clipsRes.json();
      const ranked = clipsData.clips || [];
      if (!ranked.length) {
        section.style.display = 'none';
        return;
      }
    }

    grid.innerHTML = '';
    const displayClips = clips.slice(0, 12);
    displayClips.forEach((c, i) => {
      grid.appendChild(renderClipCard({
        ...c,
        duration_seconds: c.duration_seconds ?? c.duration,
      }, i));
    });
    section.style.display = 'block';
  } catch (e) {
    console.error(e);
  }
}

// ============================================================
// Clip card rendering (shared)
// ============================================================

function renderClipCard(clip, index) {
  const rank = index + 1;
  const score = clip.score != null ? Number(clip.score) : 0;
  const dur = clip.duration_seconds != null ? Number(clip.duration_seconds) : 0;
  const text = (clip.text || '').slice(0, 50) + ((clip.text || '').length > 50 ? '...' : '');
  const fname = clip.filename || clip._previewName || '';
  const videoSrc = fname ? `${API}/api/outputs/${encodeURIComponent(fname)}` : '';

  const div = document.createElement('div');
  div.className = 'clip-card';
  div.innerHTML = `
    <div class="clip-thumb">
      ${videoSrc ? `<video src="${videoSrc}" muted preload="metadata" playsinline></video>` : '<div style="color:var(--text-dim);display:flex;align-items:center;justify-content:center;height:100%">No preview</div>'}
      <div class="clip-rank">#${rank}</div>
      <div class="clip-score-badge">${score.toFixed(1)}</div>
      <div class="clip-overlay">
        <div class="clip-play-btn">
          <svg viewBox="0 0 24 24" fill="white"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        </div>
      </div>
    </div>
    <div class="clip-info">
      <div class="clip-title">${esc(text) || 'Clip ' + rank}</div>
      <div class="clip-meta">${dur.toFixed(1)}s</div>
      <div class="clip-score-bar"><div class="clip-score-bar-fill" style="width:${Math.min(100, score / 10 * 100)}%"></div></div>
    </div>
  `;
  div.addEventListener('click', () => openModal(clip, fname));
  return div;
}

// ============================================================
// Library
// ============================================================

function setLibTab(tab) {
  _libTab = tab;
  document.querySelectorAll('.lib-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === tab)
  );
  document.getElementById('lib-clips-panel').style.display = tab === 'clips' ? '' : 'none';
  document.getElementById('lib-videos-panel').style.display = tab === 'videos' ? '' : 'none';
  renderLibrary();
}

async function loadLibrary() {
  try {
    const [clipsRes, outRes, libRes] = await Promise.all([
      fetch(`${API}/api/clips`),
      fetch(`${API}/api/outputs`),
      fetch(`${API}/api/library`),
    ]);
    const clipsData = await clipsRes.json().catch(() => ({ clips: [] }));
    const outData = await outRes.json().catch(() => ({ clips: [] }));
    const libData = await libRes.json().catch(() => ({ videos: [] }));

    _libraryClips = clipsData.clips || [];
    _libraryVideos = libData.videos || [];

    _outputsByClipId = {};
    for (const o of (outData.clips || [])) {
      if (o.clip_id && o.filename) _outputsByClipId[o.clip_id] = o.filename;
    }

    renderLibrary();
    updateSidebarStats();
  } catch (e) {
    console.error(e);
  }
}

function renderLibrary() {
  const search = (document.getElementById('library-search')?.value || '').toLowerCase().trim();

  if (_libTab === 'clips') {
    renderLibraryClips(search);
  } else {
    renderLibraryVideos(search);
  }
}

function renderLibraryClips(search) {
  const grid = document.getElementById('library-clips-grid');
  if (!grid) return;

  let filtered = _libraryClips;
  if (search) {
    filtered = filtered.filter(c => {
      const t = (c.text || '').toLowerCase();
      const vid = (c.video_id || '').toLowerCase();
      return t.includes(search) || vid.includes(search);
    });
  }

  grid.innerHTML = '';
  if (!filtered.length) {
    grid.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/><line x1="10" y1="2" x2="10" y2="22"/></svg>
        </div>
        <div class="empty-title">${search ? 'No matching clips' : 'No clips yet'}</div>
        <div class="empty-desc">${search ? 'Try another search' : 'Upload a video to generate clips'}</div>
      </div>`;
    return;
  }

  filtered.forEach((c, i) => {
    const previewName = _outputsByClipId[c.clip_id] || '';
    grid.appendChild(renderClipCard({ ...c, _previewName: previewName }, i));
  });
}

function renderLibraryVideos(search) {
  const grid = document.getElementById('library-videos-grid');
  if (!grid) return;

  let filtered = _libraryVideos;
  if (search) {
    filtered = filtered.filter(v =>
      (v.title || '').toLowerCase().includes(search) ||
      (v.video_id || '').toLowerCase().includes(search)
    );
  }

  grid.innerHTML = '';
  if (!filtered.length) {
    grid.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
        </div>
        <div class="empty-title">${search ? 'No matching videos' : 'No videos yet'}</div>
        <div class="empty-desc">${search ? 'Try another search' : 'Upload a video to get started'}</div>
      </div>`;
    return;
  }

  filtered.forEach(v => grid.appendChild(renderVideoCard(v)));
}

function renderVideoCard(v) {
  const dur = v.duration_seconds ? (v.duration_seconds / 60).toFixed(1) + ' min' : '--';
  const dateStr = v.download_time
    ? new Date(v.download_time).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    : '--';
  const statusBadge = {
    ingested: '<span class="badge badge-accent">Ingested</span>',
    ranked: '<span class="badge badge-green">Ranked</span>',
    exhausted: '<span class="badge badge-dim">Exhausted</span>',
  }[v.status] || '';

  const card = document.createElement('div');
  card.className = 'video-card';
  card.innerHTML = `
    <div class="video-card-header">
      <div class="video-card-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
      </div>
      <div class="video-card-title">${esc(v.title || v.video_id)}</div>
      ${statusBadge}
    </div>
    <div class="video-card-meta">
      <span>${dur}</span>
      <span>${dateStr}</span>
    </div>
    <div class="video-card-footer">
      <span>${v.clip_count > 0 ? '<strong>' + v.clip_count + '</strong> clips' : 'No clips yet'}</span>
    </div>
  `;
  return card;
}

// ============================================================
// Settings
// ============================================================

let _settingsCache = {};

async function loadSettings() {
  try {
    const res = await fetch(`${API}/api/config`);
    const cfg = await res.json();
    _settingsCache = cfg;

    setValue('cfg-seg-min', cfg.segment_min_duration_seconds ?? 14);
    setValue('cfg-seg-max', cfg.segment_max_duration_seconds ?? 28);
    setValue('cfg-top-n', cfg.top_n_global ?? 10);
    setSelect('cfg-whisper-model', cfg.whisper_model || 'distil-large-v3');
    setSelect('cfg-openai-model', cfg.openai_model || 'gpt-4o-mini');
    setSelect('cfg-quality', cfg.download_quality || 'best');
    setChecked('cfg-scoring-text', cfg.scoring_text);
    setChecked('cfg-scoring-audio', cfg.scoring_audio);
    setChecked('cfg-scoring-visual', cfg.scoring_visual);
    setChecked('cfg-scene', cfg.use_scene_detection);
    setChecked('cfg-vertical', cfg.export_vertical);
    setChecked('cfg-auto-youtube', cfg.auto_youtube_upload);
    setValue('cfg-prompt', cfg.openai_prompt || '');

    checkYouTubeAuth();
  } catch (e) { console.error(e); }
}

async function saveSettings() {
  try {
    const cfg = { ..._settingsCache };
    cfg.segment_min_duration_seconds = parseFloat(el('cfg-seg-min').value);
    cfg.segment_max_duration_seconds = parseFloat(el('cfg-seg-max').value);
    cfg.top_n_global = parseInt(el('cfg-top-n').value, 10);
    cfg.whisper_model = el('cfg-whisper-model').value;
    cfg.openai_model = el('cfg-openai-model').value;
    cfg.download_quality = el('cfg-quality').value;
    cfg.scoring_text = el('cfg-scoring-text').checked;
    cfg.scoring_audio = el('cfg-scoring-audio').checked;
    cfg.scoring_visual = el('cfg-scoring-visual').checked;
    cfg.use_scene_detection = el('cfg-scene').checked;
    cfg.export_vertical = el('cfg-vertical').checked;
    cfg.auto_youtube_upload = el('cfg-auto-youtube').checked;
    cfg.openai_prompt = el('cfg-prompt').value;

    const res = await fetch(`${API}/api/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });

    if (res.ok) {
      _settingsCache = cfg;
      const status = document.getElementById('save-status');
      status.textContent = 'Settings saved';
      status.classList.add('visible');
      setTimeout(() => status.classList.remove('visible'), 3000);
    }
  } catch (e) {
    console.error(e);
    alert('Save failed: ' + e.message);
  }
}

let _ytConnected = false;

async function checkYouTubeAuth() {
  const statusEl = document.getElementById('yt-auth-status');
  if (!statusEl) return;
  try {
    const res = await fetch(`${API}/api/youtube/status`);
    const data = await res.json();
    _ytConnected = data.connected;
    if (data.connected) {
      statusEl.innerHTML =
        '<span class="yt-status-dot yt-status-connected"></span>' +
        '<span>Connected as ' + esc(data.channel_name || 'YouTube') + '</span>';
      document.getElementById('yt-auth-btn').textContent = 'Reconnect';
    } else {
      statusEl.innerHTML =
        '<span class="yt-status-dot yt-status-disconnected"></span>' +
        '<span>Not connected</span>';
    }
  } catch (e) {
    statusEl.innerHTML =
      '<span class="yt-status-dot yt-status-disconnected"></span>' +
      '<span>Not connected</span>';
  }
}

// Poll YouTube status every 10 seconds
setInterval(checkYouTubeAuth, 10000);

async function authorizeYouTube() {
  try {
    const res = await fetch(`${API}/api/youtube/auth-url`);
    const data = await res.json();
    if (data.url) {
      window.open(data.url, '_blank', 'width=600,height=700');
    } else {
      alert('YouTube OAuth not configured. Add client_secrets.json to config/ directory.');
    }
  } catch (e) {
    alert('Failed to start YouTube auth: ' + e.message);
  }
}

// ============================================================
// Video modal
// ============================================================

function openModal(clip, filenameOverride) {
  const fname = filenameOverride || clip.filename || clip._previewName || '';
  if (!fname) { alert('No video file available.'); return; }
  const src = `${API}/api/outputs/${encodeURIComponent(fname)}`;
  const vid = document.getElementById('modal-video');
  vid.pause();
  vid.src = src;
  vid.load();

  document.getElementById('modal-title').textContent = fname;
  document.getElementById('modal-score').textContent =
    clip.score != null ? 'Score ' + Number(clip.score).toFixed(1) : '';
  document.getElementById('modal-duration').textContent =
    clip.duration_seconds != null ? Number(clip.duration_seconds).toFixed(1) + 's' : '';
  document.getElementById('modal-text').textContent = clip.text || '';
  const dl = document.getElementById('modal-download');
  dl.href = src;
  dl.download = fname;

  document.getElementById('video-modal').classList.add('open');
}

function closeModal() {
  const vid = document.getElementById('modal-video');
  vid.pause();
  vid.src = '';
  document.getElementById('video-modal').classList.remove('open');
}

document.getElementById('video-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'video-modal') closeModal();
});

// ============================================================
// Sidebar stats
// ============================================================

async function updateSidebarStats() {
  try {
    const res = await fetch(`${API}/api/stats`);
    const stats = await res.json();
    document.getElementById('sidebar-clips').textContent = stats.outputs_count ?? '--';
    document.getElementById('sidebar-videos').textContent = stats.videos_count ?? '--';
  } catch (e) { /* ignore */ }
}

// ============================================================
// Utilities
// ============================================================

function el(id) { return document.getElementById(id); }
function setValue(id, val) { const e = el(id); if (e) e.value = val; }
function setChecked(id, val) { const e = el(id); if (e) e.checked = !!val; }
function setSelect(id, val) {
  const s = el(id);
  if (!s) return;
  const found = Array.from(s.options).some(o => o.value === val);
  if (found) { s.value = val; return; }
  const o = document.createElement('option');
  o.value = val;
  o.textContent = val;
  s.appendChild(o);
  s.value = val;
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatLog(text) {
  if (!text) return '';
  return text.split('\n').map(line => {
    const e = esc(line);
    if (line.includes('ERROR') || line.includes('Traceback')) return `<span style="color:#f87171">${e}</span>`;
    if (line.includes('WARNING')) return `<span style="color:#fbbf24">${e}</span>`;
    return e;
  }).join('\n');
}

// ============================================================
// Init
// ============================================================

updateSidebarStats();
checkYouTubeAuth();
