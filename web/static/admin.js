const API_BASE = (() => {
  if (typeof window !== 'undefined' && typeof window.__RAG_API_BASE === 'string' && window.__RAG_API_BASE.trim()) {
    return window.__RAG_API_BASE.trim().replace(/\/+$/, '');
  }
  const meta = document.querySelector('meta[name="rag-api-base"]');
  if (meta && meta.content) {
    const value = meta.content.trim();
    if (value) return value.replace(/\/+$/, '');
  }
  return '';
})();

const activeUsersCountEl = document.getElementById('activeUsersCount');
const totalUsersCountEl = document.getElementById('totalUsersCount');
const workersCountEl = document.getElementById('workersCount');
const jobsRunningCountEl = document.getElementById('jobsRunningCount');
const jobsQueuedCountEl = document.getElementById('jobsQueuedCount');
const jobsFailedCountEl = document.getElementById('jobsFailedCount');
const jobsCompletedCountEl = document.getElementById('jobsCompletedCount');
const jobsPausedCountEl = document.getElementById('jobsPausedCount');
const jobsTotalCountEl = document.getElementById('jobsTotalCount');
const uptimeValueEl = document.getElementById('uptimeValue');
const activeUsersListEl = document.getElementById('activeUsersList');
const jobsStatusGridEl = document.getElementById('jobsStatusGrid');
const refundListEl = document.getElementById('refundList');

const LONDON_TIMEZONE = 'Europe/London';
const LONDON_TIME_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  timeZone: LONDON_TIMEZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  return fetch(url, { credentials: 'include', ...options });
}

function formatDuration(totalSeconds) {
  if (!Number.isFinite(totalSeconds)) return '--';
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const parts = [];
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (minutes) parts.push(`${minutes}m`);
  parts.push(`${secs}s`);
  return parts.join(' ');
}

function formatAbsoluteTimeFromMs(ms) {
  const parts = LONDON_TIME_FORMATTER.formatToParts(new Date(ms));
  const data = {};
  parts.forEach((part) => {
    if (part.type !== 'literal') data[part.type] = part.value;
  });
  if (!data.year) return new Date(ms).toISOString();
  return `${data.day}/${data.month}/${data.year} ${data.hour}:${data.minute}:${data.second}`;
}

function getLondonOffsetMs(ms) {
  const parts = LONDON_TIME_FORMATTER.formatToParts(new Date(ms));
  const data = {};
  parts.forEach((part) => {
    if (part.type !== 'literal') data[part.type] = part.value;
  });
  if (!data.year) return 0;
  const asUTC = Date.UTC(
    Number(data.year),
    Number(data.month) - 1,
    Number(data.day),
    Number(data.hour),
    Number(data.minute),
    Number(data.second),
  );
  return asUTC - ms;
}

function parseTimestamp(value) {
  if (!value) return null;
  const cleaned = String(value).trim();
  const ukMatch = cleaned.match(/^(\d{2})\/(\d{2})\/(\d{4})(?:[\s,]+(\d{2}):(\d{2})(?::(\d{2}))?)?$/);
  if (ukMatch) {
    const day = Number(ukMatch[1]);
    const month = Number(ukMatch[2]);
    const year = Number(ukMatch[3]);
    const hour = Number(ukMatch[4] || '0');
    const minute = Number(ukMatch[5] || '0');
    const second = Number(ukMatch[6] || '0');
    const utcGuess = Date.UTC(year, month - 1, day, hour, minute, second);
    const offsetMs = getLondonOffsetMs(utcGuess);
    return utcGuess - offsetMs;
  }
  const direct = Date.parse(cleaned);
  if (!Number.isNaN(direct)) return direct;
  const normalized = cleaned.replace(' ', 'T');
  const attempt = Date.parse(normalized);
  if (!Number.isNaN(attempt)) return attempt;
  return null;
}

function renderActiveUsers(items) {
  if (!activeUsersListEl) return;
  if (!items.length) {
    activeUsersListEl.innerHTML = '<p class="subtitle">No active users in the current window.</p>';
    return;
  }
  activeUsersListEl.innerHTML = '';
  items.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'admin-list-item';
    const name = document.createElement('div');
    name.textContent = item.user || 'unknown';
    const meta = document.createElement('small');
    meta.textContent = `Seen ${formatDuration(item.last_seen_sec)} ago`;
    row.appendChild(name);
    row.appendChild(meta);
    activeUsersListEl.appendChild(row);
  });
}

function renderJobStatusGrid(statuses) {
  if (!jobsStatusGridEl) return;
  jobsStatusGridEl.innerHTML = '';
  const entries = Object.entries(statuses || {});
  if (!entries.length) {
    jobsStatusGridEl.innerHTML = '<p class="subtitle">No job data available.</p>';
    return;
  }
  entries.forEach(([status, count]) => {
    const card = document.createElement('div');
    card.className = 'admin-status-card';
    const label = document.createElement('span');
    label.textContent = status.replace(/_/g, ' ');
    const value = document.createElement('strong');
    value.textContent = String(count ?? 0);
    card.appendChild(label);
    card.appendChild(value);
    jobsStatusGridEl.appendChild(card);
  });
}

function formatDate(value) {
  if (!value) return '--';
  const ts = parseTimestamp(value);
  if (!ts) return value;
  return formatAbsoluteTimeFromMs(ts);
}

function renderRefunds(requests) {
  if (!refundListEl) return;
  if (!requests.length) {
    refundListEl.innerHTML = '<p class="subtitle">No refund requests.</p>';
    return;
  }
  refundListEl.innerHTML = '';
  requests.forEach((entry) => {
    const refund = entry.refund || {};
    const screening = refund.llm_screening || null;
    const files = refund.screenshots || [];
    const fileBase = API_BASE || '';
    const card = document.createElement('div');
    card.className = 'refund-card';
    card.innerHTML = `
      <div class="refund-card-header">
        <div>
          <strong>${entry.project_name || 'Untitled'}</strong>
          <div class="subtitle">${entry.job_id} · ${entry.owner} · ${entry.workflow}</div>
        </div>
        <div><span class="badge">${refund.status || 'requested'}</span></div>
      </div>
      <div class="refund-meta-grid">
        <div><span>Requested</span><strong>${refund.requested_amount ?? '--'}</strong></div>
        <div><span>Approved</span><strong>${refund.approved_amount ?? refund.admin_decision?.amount ?? '--'}</strong></div>
        <div><span>Job Status</span><strong>${entry.job_status}</strong></div>
        <div><span>Token Actual</span><strong>${entry.tokens?.actual ?? '--'}</strong></div>
        <div><span>Requested At</span><strong>${formatDate(refund.requested_at)}</strong></div>
        <div><span>Reason</span><strong>${refund.reason || '--'}</strong></div>
      </div>
      <div class="refund-files-list">
        ${files.map((file) => `<a href="${fileBase}/api/refunds/${entry.job_id}/${refund.id}/file/${file.filename}" target="_blank" rel="noopener">${file.filename}</a>`).join('')}
      </div>
      <div class="status-banner" ${screening ? '' : 'hidden'}>${screening ? `LLM: ${screening.decision || 'n/a'} · ${screening.suggested_amount ?? '--'} · ${screening.rationale || ''}` : ''}</div>
      <div class="refund-actions">
        <div class="inline-input">
          <select class="refund-status">
            <option value="requested">Requested</option>
            <option value="awaiting approval">Awaiting approval</option>
            <option value="approved">Approved</option>
            <option value="partial-refund">Partial refund</option>
            <option value="settled">Settled</option>
            <option value="rejected">Rejected</option>
          </select>
          <input class="refund-amount" type="number" min="0" step="1" placeholder="Amount">
        </div>
        <textarea class="refund-note" rows="2" placeholder="Admin note (optional)"></textarea>
        <div class="assistant-actions">
          <button type="button" class="ghost refund-screen">Screen with LLM</button>
          <button type="button" class="primary refund-apply">Apply Decision</button>
        </div>
        <div class="status-banner refund-status-msg" hidden></div>
      </div>
    `;
    const statusSelect = card.querySelector('.refund-status');
    if (statusSelect && refund.status) statusSelect.value = refund.status;
    const amountInput = card.querySelector('.refund-amount');
    if (amountInput && refund.requested_amount) amountInput.value = refund.requested_amount;
    const statusMsg = card.querySelector('.refund-status-msg');
    const showStatus = (message, isError = false) => {
      if (!statusMsg) return;
      statusMsg.textContent = message;
      statusMsg.hidden = false;
      statusMsg.classList.toggle('error', Boolean(isError));
    };
    const screenBtn = card.querySelector('.refund-screen');
    if (screenBtn) {
      screenBtn.addEventListener('click', async () => {
        showStatus('Running LLM screening...');
        try {
          const res = await apiFetch(`/api/refunds/${entry.job_id}/${refund.id}/screen`, { method: 'POST' });
          const data = await res.json();
          if (!res.ok) {
            showStatus(data.details || data.error || 'Screening failed.', true);
            return;
          }
          showStatus('Screening complete.');
          loadRefunds();
        } catch (err) {
          console.error(err);
          showStatus('Screening failed.', true);
        }
      });
    }
    const applyBtn = card.querySelector('.refund-apply');
    if (applyBtn) {
      applyBtn.addEventListener('click', async () => {
        const status = statusSelect ? statusSelect.value : 'awaiting approval';
        const amount = amountInput ? amountInput.value : '';
        const note = card.querySelector('.refund-note')?.value || '';
        showStatus('Submitting decision...');
        try {
          const res = await apiFetch(`/api/refunds/${entry.job_id}/${refund.id}/decision`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status, amount: amount ? parseInt(amount, 10) : null, note }),
          });
          const data = await res.json();
          if (!res.ok) {
            showStatus(data.details || data.error || 'Decision failed.', true);
            return;
          }
          showStatus('Decision saved.');
          loadRefunds();
        } catch (err) {
          console.error(err);
          showStatus('Decision failed.', true);
        }
      });
    }
    refundListEl.appendChild(card);
  });
}

async function loadRefunds() {
  if (!refundListEl) return;
  try {
    const res = await apiFetch('/api/refunds');
    if (!res.ok) return;
    const data = await res.json();
    renderRefunds(data.requests || []);
  } catch (err) {
    console.error(err);
  }
}

async function loadAdminStats() {
  try {
    const res = await apiFetch('/api/admin/stats');
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    if (res.status === 403) {
      if (activeUsersListEl) {
        activeUsersListEl.innerHTML = '<p class="subtitle">Admin access required.</p>';
      }
      return;
    }
    const data = await res.json();
    if (activeUsersCountEl) activeUsersCountEl.textContent = String(data.active_users_count ?? '--');
    if (totalUsersCountEl) totalUsersCountEl.textContent = String(data.total_users ?? '--');
    if (workersCountEl) workersCountEl.textContent = String(data.workers ?? '--');
    if (jobsRunningCountEl) jobsRunningCountEl.textContent = String(data.jobs_running ?? '--');
    if (jobsQueuedCountEl) jobsQueuedCountEl.textContent = String(data.jobs_queued ?? '--');
    if (jobsFailedCountEl) jobsFailedCountEl.textContent = String(data.jobs_failed ?? '--');
    if (jobsCompletedCountEl) jobsCompletedCountEl.textContent = String(data.jobs_completed ?? '--');
    if (jobsPausedCountEl) jobsPausedCountEl.textContent = String(data.jobs_paused ?? '--');
    if (jobsTotalCountEl) jobsTotalCountEl.textContent = String(data.jobs_total ?? '--');
    if (uptimeValueEl) uptimeValueEl.textContent = formatDuration(data.uptime_sec);
    renderActiveUsers(data.active_users || []);
    renderJobStatusGrid(data.jobs_by_status || {});
  } catch (err) {
    console.error(err);
  }
}

loadAdminStats();
setInterval(loadAdminStats, 15000);
loadRefunds();
setInterval(loadRefunds, 20000);
