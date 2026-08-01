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
const toastContainer = document.getElementById('toastContainer');

let planState = null;
let activePlanTaskId = null;
let planPollTimer = null;
let planToast = null;
let planStartedAt = 0;

const PLAN_POLL_INTERVAL_MS = 5000;

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

function formatNumber(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '';
  return numeric.toLocaleString('en-GB');
}

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

function createPlanToast(title, message, tone = 'success') {
  if (!toastContainer) return null;
  const toast = document.createElement('div');
  toast.className = `toast ${tone}`;

  const content = document.createElement('div');
  const titleEl = document.createElement('div');
  titleEl.className = 'toast-title';
  titleEl.textContent = title;
  const bodyEl = document.createElement('div');
  bodyEl.className = 'toast-body';
  bodyEl.textContent = message;
  content.append(titleEl, bodyEl);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'toast-close';
  closeBtn.setAttribute('aria-label', 'Dismiss');
  closeBtn.textContent = '×';
  closeBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    toast.remove();
    if (planToast?.toast === toast) planToast = null;
  });

  toast.append(content, closeBtn);
  toastContainer.appendChild(toast);
  return { toast, titleEl, bodyEl };
}

function updatePlanToast(message, tone = 'success', title = 'Plan request') {
  if (!planToast || !planToast.toast.isConnected) {
    planToast = createPlanToast(title, message, tone);
    return;
  }
  planToast.titleEl.textContent = title;
  planToast.bodyEl.textContent = message;
  planToast.toast.className = `toast ${tone}`;
}

function dismissPlanToast(delayMs = 8000) {
  const toast = planToast?.toast;
  if (!toast) return;
  window.setTimeout(() => {
    if (toast.isConnected) toast.remove();
  }, delayMs);
  planToast = null;
}

function elapsedPlanSeconds() {
  if (!planStartedAt) return 0;
  return Math.max(0, Math.round((Date.now() - planStartedAt) / 1000));
}

function formatProcessingTimeEstimate(value) {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '';
  if (milliseconds < 1000) return `${Math.max(1, Math.round(milliseconds))} ms`;
  const seconds = milliseconds / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} seconds`;
  const minutes = seconds / 60;
  return `${minutes < 10 ? minutes.toFixed(1) : Math.round(minutes)} minutes`;
}

function planProgressMessage(status) {
  const elapsed = elapsedPlanSeconds();
  if (status === 'queued') {
    return `Request submitted. Gail is queued to prepare your plan (${elapsed}s elapsed).`;
  }
  if (status === 'running') {
    return `Gail is preparing your plan (${elapsed}s elapsed). You can keep using the site.`;
  }
  return `Waiting for Gail to prepare your plan (${elapsed}s elapsed).`;
}

async function readApiJson(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (_err) {
    return {
      error: response.ok ? 'invalid_response' : `request_failed_${response.status}`,
      details: response.ok
        ? 'The server returned an invalid response.'
        : `The server returned an error (${response.status}).`,
    };
  }
}

function waitForPlanPoll() {
  return new Promise((resolve) => {
    planPollTimer = window.setTimeout(() => {
      planPollTimer = null;
      resolve();
    }, PLAN_POLL_INTERVAL_MS);
  });
}

function stopPlanPolling() {
  if (planPollTimer !== null) {
    window.clearTimeout(planPollTimer);
    planPollTimer = null;
  }
}

function finishPlanRequest(taskId) {
  if (activePlanTaskId !== taskId) return;
  activePlanTaskId = null;
  planBtn.disabled = false;
  stopPlanPolling();
}

async function pollPlanTask(taskId) {
  while (activePlanTaskId === taskId) {
    try {
      const res = await apiFetch(`/api/subtasks/${encodeURIComponent(taskId)}?include_result=1`);
      if (res.status === 401 || res.redirected) {
        window.location.href = res.url || '/login';
        finishPlanRequest(taskId);
        return;
      }
      const data = await readApiJson(res);
      if (!res.ok) {
        if (res.status === 404) {
          finishPlanRequest(taskId);
          updatePlanToast(data.details || 'The plan request could not be found.', 'error', 'Plan failed');
          showStatus(data.details || 'Plan request could not be found.', true);
          dismissPlanToast();
          return;
        }
        updatePlanToast(`Still checking Gail (${elapsedPlanSeconds()}s elapsed).`, 'success');
        await waitForPlanPoll();
        continue;
      }

      const task = data.task || {};
      const status = `${task.status || 'queued'}`.trim().toLowerCase();
      if (status === 'queued' || status === 'running') {
        updatePlanToast(planProgressMessage(status));
        showStatus(status === 'queued' ? 'Plan request queued with Gail...' : 'Gail is preparing a plan...');
      } else if (status === 'completed') {
        const response = task.result?.response;
        if (!response || typeof response !== 'object') {
          finishPlanRequest(taskId);
          updatePlanToast('Gail completed the request without returning a plan.', 'error', 'Plan failed');
          showStatus('Gail completed the request without returning a plan.', true);
          return;
        }
        finishPlanRequest(taskId);
        planState = response;
        renderPlan(response);
        buildBtn.disabled = !response.job_payload;
        showStatus('Plan ready.');
        const estimate = formatProcessingTimeEstimate(response.processing_time_estimate_ms);
        updatePlanToast(
          estimate
            ? `Your plan is ready to review. Gail's estimated AI response time was about ${estimate}.`
            : 'Your plan is ready to review.',
          'success',
          'Plan ready',
        );
        dismissPlanToast();
        return;
      } else if (status === 'failed' || status === 'cancelled') {
        const result = task.result || {};
        const message = result.details || task.error || `Plan ${status}.`;
        finishPlanRequest(taskId);
        updatePlanToast(message, 'error', status === 'cancelled' ? 'Plan cancelled' : 'Plan failed');
        showStatus(message, true);
        dismissPlanToast();
        return;
      } else {
        updatePlanToast(planProgressMessage(status));
      }
    } catch (err) {
      console.error(err);
      updatePlanToast(`Connection check in progress (${elapsedPlanSeconds()}s elapsed).`, 'success');
    }
    await waitForPlanPoll();
  }
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
  const metaParts = [];
  if (data?.project_name) {
    metaParts.push(`Project: ${data.project_name}`);
  }
  if (Number.isFinite(Number(data?.token_estimate))) {
    metaParts.push(`Estimated reserve: ${formatNumber(data.token_estimate)} tokens`);
  }
  metaEl.textContent = metaParts.join(' | ') || 'Ready to build.';
}

async function requestPlan() {
  if (activePlanTaskId) return;
  const prompt = promptEl?.value.trim();
  if (!prompt) {
    showStatus('Please describe what you want to build.', true);
    return;
  }
  clearStatus();
  showStatus('Submitting your plan request...');
  planBtn.disabled = true;
  buildBtn.disabled = true;
  planState = null;
  planStartedAt = Date.now();
  try {
    const res = await apiFetch('/api/subtasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'playground_plan',
        payload: { prompt },
        scope_type: 'playground',
        timeout_sec: 600,
      }),
    });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    const data = await readApiJson(res);
    if (!res.ok) {
      showStatus(data.details || data.error || 'Plan failed.', true);
      updatePlanToast(data.details || data.error || 'Unable to submit the plan request.', 'error', 'Plan failed');
      dismissPlanToast();
      return;
    }
    const taskId = data.task?.task_id;
    if (!taskId) {
      showStatus('The plan request was not queued.', true);
      updatePlanToast('The plan request was not queued.', 'error', 'Plan failed');
      dismissPlanToast();
      return;
    }
    activePlanTaskId = taskId;
    updatePlanToast(planProgressMessage('queued'));
    void pollPlanTask(taskId);
  } catch (err) {
    console.error(err);
    showStatus('Plan failed. Check console.', true);
    updatePlanToast('Unable to submit the plan request. Check your connection.', 'error', 'Plan failed');
    dismissPlanToast();
  } finally {
    if (!activePlanTaskId) planBtn.disabled = false;
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
    const data = await readApiJson(res);
    if (!res.ok) {
      if (res.status === 402 && data?.error === 'insufficient_tokens') {
        const estimate = formatNumber(data?.estimate);
        const available = formatNumber(data?.available);
        if (estimate && available) {
          showStatus(`Insufficient tokens to submit this job. Need ${estimate}, available ${available}.`, true);
          buildBtn.disabled = false;
          return;
        }
      }
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
if (promptEl) {
  promptEl.addEventListener('input', () => {
    clearStatus();
    if (!activePlanTaskId) {
      planState = null;
      buildBtn.disabled = true;
    }
  });
}

window.addEventListener('beforeunload', stopPlanPolling);

if (promptEl) {
  const params = new URLSearchParams(window.location.search);
  const stagedPrompt = params.get('prompt')?.trim();
  if (stagedPrompt) {
    promptEl.value = stagedPrompt;
  }
}

if (quickTaskButtons.length && promptEl) {
  quickTaskButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      promptEl.value = btn.dataset.task || '';
      promptEl.focus();
      clearStatus();
    });
  });
}
