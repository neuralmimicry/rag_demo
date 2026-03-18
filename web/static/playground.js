const promptEl = document.getElementById('playgroundPrompt');
const planBtn = document.getElementById('playgroundPlan');
const buildBtn = document.getElementById('playgroundBuild');
const statusEl = document.getElementById('playgroundStatus');
const summaryEl = document.getElementById('planSummary');
const stepsEl = document.getElementById('planSteps');
const metaEl = document.getElementById('planMeta');
const controlLinkEl = document.getElementById('controlLink');
const controlLinkAnchor = controlLinkEl ? controlLinkEl.querySelector('a') : null;
const quickTaskButtons = document.querySelectorAll('.chip[data-task]');

let planState = null;

const API_BASE = (() => {
  if (typeof window !== 'undefined' && typeof window.__RAG_API_BASE === 'string' && window.__RAG_API_BASE.trim()) {
    return window.__RAG_API_BASE.trim().replace(/\/+$/, '');
  }
  const meta = document.querySelector('meta[name="rag-api-base"]');
  if (meta && meta.content) {
    const value = meta.content.trim();
    if (value && !value.includes('{{')) {
      return value.replace(/\/+$/, '');
    }
  }
  return '';
})();

const apiUrl = (path) => {
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE}${suffix}`;
};

const apiFetch = (path, options = {}) => {
  return fetch(apiUrl(path), { ...options, credentials: 'include' });
};

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value ?? '';
  return div.innerHTML;
}

function showStatus(message, isError = false) {
  if (!statusEl) return;
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.toggle('error', Boolean(isError));
}

function clearStatus() {
  if (!statusEl) return;
  statusEl.textContent = '';
  statusEl.hidden = true;
  statusEl.classList.remove('error');
}

function renderPlan(data) {
  const summary = (data?.summary || '').trim();
  const steps = Array.isArray(data?.steps) ? data.steps : [];
  summaryEl.textContent = summary || 'Plan ready. Review the steps below.';
  if (!steps.length) {
    stepsEl.innerHTML = '<p class="subtitle">No steps yet. Try a different prompt.</p>';
    return;
  }
  stepsEl.innerHTML = steps
    .map((step, idx) => `
      <div class="plan-step">
        <span class="plan-step-index">${idx + 1}</span>
        <div class="plan-step-text">${escapeHtml(step)}</div>
      </div>
    `)
    .join('');
  const name = data?.project_name ? `Project: ${data.project_name}` : '';
  metaEl.textContent = name || 'Ready to build.';
}

async function requestPlan() {
  const prompt = promptEl?.value.trim();
  if (!prompt) {
    showStatus('Please describe what you want to build.', true);
    return;
  }
  clearStatus();
  showStatus('Creating a plan...');
  planBtn.disabled = true;
  buildBtn.disabled = true;
  try {
    const res = await apiFetch('/api/playground/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    const data = await res.json();
    if (!res.ok) {
      showStatus(data.details || data.error || 'Plan failed.', true);
      return;
    }
    planState = data;
    renderPlan(data);
    buildBtn.disabled = !data.job_payload;
    showStatus('Plan ready.');
  } catch (err) {
    console.error(err);
    showStatus('Plan failed. Check console.', true);
  } finally {
    planBtn.disabled = false;
  }
}

async function startBuild() {
  if (!planState?.job_payload) {
    showStatus('Create a plan first.', true);
    return;
  }
  clearStatus();
  showStatus('Queuing your build...');
  buildBtn.disabled = true;
  try {
    const res = await apiFetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(planState.job_payload),
    });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    const data = await res.json();
    if (!res.ok) {
      showStatus(data.details || data.error || 'Unable to queue job.', true);
      buildBtn.disabled = false;
      return;
    }
    showStatus('Job queued. You can watch it in Control Room.');
    if (controlLinkEl) {
      if (controlLinkAnchor && data.id) {
        const params = new URLSearchParams({ job_id: data.id, scope: 'personal' });
        controlLinkAnchor.setAttribute('href', `/?${params.toString()}`);
      }
      controlLinkEl.hidden = false;
    }
  } catch (err) {
    console.error(err);
    showStatus('Unable to queue job. Check console.', true);
    buildBtn.disabled = false;
  }
}

if (planBtn) planBtn.addEventListener('click', requestPlan);
if (buildBtn) buildBtn.addEventListener('click', startBuild);
if (promptEl) promptEl.addEventListener('input', clearStatus);

if (quickTaskButtons.length && promptEl) {
  quickTaskButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      promptEl.value = btn.dataset.task || '';
      promptEl.focus();
      clearStatus();
    });
  });
}
