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
const teamFormEl = document.getElementById('teamForm');
const teamNameEl = document.getElementById('teamName');
const teamParentEl = document.getElementById('teamParent');
const teamLeadersEl = document.getElementById('teamLeaders');
const teamMembersEl = document.getElementById('teamMembers');
const teamCreateBtn = document.getElementById('teamCreate');
const teamStatusEl = document.getElementById('teamStatus');
const teamListEl = document.getElementById('teamList');
const projectFormEl = document.getElementById('projectForm');
const projectNameEl = document.getElementById('projectName');
const projectTeamEl = document.getElementById('projectTeam');
const projectLeadersEl = document.getElementById('projectLeaders');
const projectContributorsEl = document.getElementById('projectContributors');
const projectViewersEl = document.getElementById('projectViewers');
const projectPermReadEl = document.getElementById('projectPermRead');
const projectPermWriteEl = document.getElementById('projectPermWrite');
const projectPermGrantEl = document.getElementById('projectPermGrant');
const projectCreateBtn = document.getElementById('projectCreate');
const projectStatusEl = document.getElementById('projectStatus');
const projectListEl = document.getElementById('projectList');
const roomHistoryListEl = document.getElementById('roomHistoryList');
const auditTransferListEl = document.getElementById('auditTransferList');

let teams = [];
let projects = [];

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

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value ?? '';
  return div.innerHTML;
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

function parseList(value) {
  if (!value) return [];
  return String(value)
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function setStatus(el, message, isError = false) {
  if (!el) return;
  el.textContent = message;
  el.hidden = !message;
  el.classList.toggle('error', Boolean(isError));
}

function populateTeamSelect(selectEl, items, includeNone = true) {
  if (!selectEl) return;
  selectEl.innerHTML = '';
  if (includeNone) {
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'None';
    selectEl.appendChild(none);
  }
  items.forEach((team) => {
    const option = document.createElement('option');
    option.value = team.id;
    option.textContent = team.name;
    selectEl.appendChild(option);
  });
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

function formatAuditTime(value) {
  if (value === null || value === undefined) return '--';
  if (typeof value === 'number') {
    return formatAbsoluteTimeFromMs(value * 1000);
  }
  const numeric = Number(value);
  if (!Number.isNaN(numeric) && numeric > 0) {
    return formatAbsoluteTimeFromMs(numeric * 1000);
  }
  return formatDate(value);
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

function renderAuditTransfers(entries) {
  if (!auditTransferListEl) return;
  if (!entries.length) {
    auditTransferListEl.innerHTML = '<p class="subtitle">No transfer activity yet.</p>';
    return;
  }
  auditTransferListEl.innerHTML = '';
  entries.forEach((entry) => {
    const details = entry.details || {};
    const card = document.createElement('div');
    card.className = 'admin-item';
    const metaBits = [];
    if (details.job_id) metaBits.push(`Job ${String(details.job_id).slice(0, 8)}`);
    if (details.team_id) metaBits.push(`Team ${details.team_id}`);
    if (details.project_id) metaBits.push(`Project ${details.project_id}`);
    if (details.target) metaBits.push(`Target ${details.target}`);
    card.innerHTML = `
      <div class="admin-item-head">
        <strong>${entry.action || 'audit'}</strong>
        <span class="subtitle">${entry.status || '--'} · ${formatAuditTime(entry.ts)}</span>
      </div>
      <div class="admin-form-grid">
        <div class="field grid-2">
          <div>
            <label>Actor</label>
            <div class="input-static">${entry.actor || 'system'}</div>
          </div>
          <div>
            <label>Meta</label>
            <div class="input-static">${metaBits.join(' · ') || '--'}</div>
          </div>
        </div>
        <div class="field">
          <label>Details</label>
          <div class="input-static">${escapeHtml(JSON.stringify(details))}</div>
        </div>
      </div>
    `;
    auditTransferListEl.appendChild(card);
  });
}

function renderTeams() {
  if (!teamListEl) return;
  teamListEl.innerHTML = '';
  if (!teams.length) {
    teamListEl.innerHTML = '<p class="subtitle">No teams created yet.</p>';
    return;
  }
  teams.forEach((team) => {
    const card = document.createElement('div');
    card.className = 'admin-item';
    card.innerHTML = `
      <div class="admin-item-head">
        <strong>${team.name}</strong>
        <span class="subtitle">${team.id}</span>
      </div>
      <div class="admin-form-grid">
        <div class="field grid-2">
          <div>
            <label>Name</label>
            <input class="team-name" type="text" value="${team.name || ''}">
          </div>
          <div>
            <label>Parent</label>
            <select class="team-parent"></select>
          </div>
        </div>
        <div class="field grid-2">
          <div>
            <label>Leaders</label>
            <input class="team-leaders" type="text" value="${(team.leaders || []).join(', ')}">
          </div>
          <div>
            <label>Members</label>
            <input class="team-members" type="text" value="${(team.members || []).join(', ')}">
          </div>
        </div>
        <div class="field grid-2">
          <div>
            <label>Team Tokens</label>
            <div class="input-static team-token-balance">--</div>
          </div>
          <div>
            <label>Grant Tokens</label>
            <input class="team-token-amount" type="text" placeholder="500">
          </div>
        </div>
      </div>
      <div class="assistant-actions">
        <button type="button" class="ghost team-save">Save</button>
        <button type="button" class="ghost team-token-grant">Grant Tokens</button>
        <button type="button" class="ghost danger team-delete">Delete</button>
        <span class="status-banner team-msg" hidden></span>
      </div>
    `;
    const parentSelect = card.querySelector('.team-parent');
    populateTeamSelect(parentSelect, teams.filter((t) => t.id !== team.id), true);
    if (team.parent_id) {
      parentSelect.value = team.parent_id;
    }
    const messageEl = card.querySelector('.team-msg');
    const tokenBalanceEl = card.querySelector('.team-token-balance');
    const tokenAmountEl = card.querySelector('.team-token-amount');
    const tokenGrantBtn = card.querySelector('.team-token-grant');

    async function refreshTeamTokens() {
      if (!tokenBalanceEl) return;
      try {
        const res = await apiFetch(`/api/teams/${team.id}/tokens`);
        if (!res.ok) {
          tokenBalanceEl.textContent = '--';
          return;
        }
        const data = await res.json();
        const balance = data.balance ?? data.tokens ?? 0;
        const available = data.available ?? balance;
        tokenBalanceEl.textContent = `${balance} (avail ${available})`;
      } catch (err) {
        tokenBalanceEl.textContent = '--';
      }
    }

    refreshTeamTokens();
    card.querySelector('.team-save').addEventListener('click', async () => {
      const payload = {
        name: card.querySelector('.team-name').value.trim(),
        parent_id: parentSelect.value || null,
        leaders: parseList(card.querySelector('.team-leaders').value),
        members: parseList(card.querySelector('.team-members').value),
      };
      const res = await apiFetch(`/api/teams/${team.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        setStatus(messageEl, 'Update failed.', true);
        return;
      }
      setStatus(messageEl, 'Updated.');
      await fetchTeams();
      await fetchProjects();
    });
    if (tokenGrantBtn) {
      tokenGrantBtn.addEventListener('click', async () => {
        const amount = parseInt(tokenAmountEl?.value || '', 10);
        if (!amount || amount <= 0) {
          setStatus(messageEl, 'Enter a positive token amount.', true);
          return;
        }
        setStatus(messageEl, 'Granting tokens...');
        const res = await apiFetch(`/api/teams/${team.id}/tokens`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'grant', token_amount: amount }),
        });
        if (!res.ok) {
          setStatus(messageEl, 'Token grant failed.', true);
          return;
        }
        setStatus(messageEl, 'Tokens granted.');
        if (tokenAmountEl) tokenAmountEl.value = '';
        refreshTeamTokens();
      });
    }
    card.querySelector('.team-delete').addEventListener('click', async () => {
      if (!confirm('Delete this team?')) return;
      const res = await apiFetch(`/api/teams/${team.id}`, { method: 'DELETE' });
      if (!res.ok) {
        setStatus(messageEl, 'Delete failed.', true);
        return;
      }
      await fetchTeams();
      await fetchProjects();
    });
    teamListEl.appendChild(card);
  });
}

function renderProjects() {
  if (!projectListEl) return;
  projectListEl.innerHTML = '';
  if (!projects.length) {
    projectListEl.innerHTML = '<p class="subtitle">No projects created yet.</p>';
    return;
  }
  projects.forEach((project) => {
    const permissions = project.permissions || {};
    const card = document.createElement('div');
    card.className = 'admin-item';
    card.innerHTML = `
      <div class="admin-item-head">
        <strong>${project.name}</strong>
        <span class="subtitle">${project.id}</span>
      </div>
      <div class="admin-form-grid">
        <div class="field grid-2">
          <div>
            <label>Name</label>
            <input class="project-name" type="text" value="${project.name || ''}">
          </div>
          <div>
            <label>Team</label>
            <select class="project-team"></select>
          </div>
        </div>
        <div class="field grid-3">
          <div>
            <label>Leaders</label>
            <input class="project-leaders" type="text" value="${(project.leaders || []).join(', ')}">
          </div>
          <div>
            <label>Contributors</label>
            <input class="project-contributors" type="text" value="${(project.contributors || []).join(', ')}">
          </div>
          <div>
            <label>Viewers</label>
            <input class="project-viewers" type="text" value="${(project.viewers || []).join(', ')}">
          </div>
        </div>
        <div class="field grid-3">
          <div>
            <label>Explicit Read</label>
            <input class="project-perm-read" type="text" value="${(permissions.read || []).join(', ')}">
          </div>
          <div>
            <label>Explicit Write</label>
            <input class="project-perm-write" type="text" value="${(permissions.write || []).join(', ')}">
          </div>
          <div>
            <label>Explicit Grant</label>
            <input class="project-perm-grant" type="text" value="${(permissions.grant || []).join(', ')}">
          </div>
        </div>
      </div>
      <div class="assistant-actions">
        <button type="button" class="ghost project-save">Save</button>
        <button type="button" class="ghost danger project-delete">Delete</button>
        <span class="status-banner project-msg" hidden></span>
      </div>
    `;
    const teamSelect = card.querySelector('.project-team');
    populateTeamSelect(teamSelect, teams, true);
    if (project.team_id) {
      teamSelect.value = project.team_id;
    }
    const messageEl = card.querySelector('.project-msg');
    card.querySelector('.project-save').addEventListener('click', async () => {
      const payload = {
        name: card.querySelector('.project-name').value.trim(),
        team_id: teamSelect.value || null,
        leaders: parseList(card.querySelector('.project-leaders').value),
        contributors: parseList(card.querySelector('.project-contributors').value),
        viewers: parseList(card.querySelector('.project-viewers').value),
        permissions: {
          read: parseList(card.querySelector('.project-perm-read').value),
          write: parseList(card.querySelector('.project-perm-write').value),
          grant: parseList(card.querySelector('.project-perm-grant').value),
        },
      };
      const res = await apiFetch(`/api/projects/${project.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        setStatus(messageEl, 'Update failed.', true);
        return;
      }
      setStatus(messageEl, 'Updated.');
      await fetchProjects();
    });
    card.querySelector('.project-delete').addEventListener('click', async () => {
      if (!confirm('Delete this project?')) return;
      const res = await apiFetch(`/api/projects/${project.id}`, { method: 'DELETE' });
      if (!res.ok) {
        setStatus(messageEl, 'Delete failed.', true);
        return;
      }
      await fetchProjects();
    });
    projectListEl.appendChild(card);
  });
}

async function fetchTeams() {
  if (!teamListEl && !teamParentEl && !projectTeamEl) return;
  const res = await apiFetch('/api/teams');
  if (!res.ok) return;
  const data = await res.json();
  teams = data.teams || [];
  populateTeamSelect(teamParentEl, teams, true);
  populateTeamSelect(projectTeamEl, teams, true);
  renderTeams();
}

async function fetchProjects() {
  if (!projectListEl && !projectTeamEl) return;
  const res = await apiFetch('/api/projects');
  if (!res.ok) return;
  const data = await res.json();
  projects = data.projects || [];
  renderProjects();
}

function renderRoomHistory(rooms) {
  if (!roomHistoryListEl) return;
  roomHistoryListEl.innerHTML = '';
  if (!rooms.length) {
    roomHistoryListEl.innerHTML = '<p class="subtitle">No room history yet.</p>';
    return;
  }
  rooms.forEach((room) => {
    const card = document.createElement('div');
    card.className = 'admin-item';
    const lastEvent = room.last_event || {};
    const tail = room.events_tail || [];
    card.innerHTML = `
      <div class="admin-item-head">
        <strong>Room ${room.room_id || '--'}</strong>
        <span class="subtitle">${room.job_id || 'no job'} · ${room.project_id || 'no project'}</span>
      </div>
      <div class="admin-form-grid">
        <div class="field grid-2">
          <div>
            <label>Created By</label>
            <div class="input-static">${room.created_by || '--'}</div>
          </div>
          <div>
            <label>Updated</label>
            <div class="input-static">${formatDate(room.updated_at) || '--'}</div>
          </div>
        </div>
        <div class="field grid-2">
          <div>
            <label>Events</label>
            <div class="input-static">${room.events_count ?? 0}</div>
          </div>
          <div>
            <label>Last Event</label>
            <div class="input-static">${lastEvent.type || '--'} · ${lastEvent.user || 'system'}</div>
          </div>
        </div>
      </div>
      <div class="session-history">
        ${tail.map((event) => `<div class="session-history-item"><strong>${event.type}</strong> · ${event.user || 'system'} · ${event.ts || ''}</div>`).join('')}
      </div>
    `;
    roomHistoryListEl.appendChild(card);
  });
}

async function fetchRoomHistory() {
  if (!roomHistoryListEl) return;
  const res = await apiFetch('/api/sessions/history');
  if (!res.ok) return;
  const data = await res.json();
  renderRoomHistory(data.rooms || []);
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

async function loadAuditTransfers() {
  if (!auditTransferListEl) return;
  try {
    const actions = [
      'job_team_invite',
      'job_team_invite_cancelled',
      'job_team_invite_declined',
      'job_team_invite_accepted',
      'job_assign_user',
    ].join(',');
    const res = await apiFetch(`/api/audit?limit=60&actions=${encodeURIComponent(actions)}`);
    if (!res.ok) return;
    const data = await res.json();
    renderAuditTransfers(data.entries || []);
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
loadAuditTransfers();
setInterval(loadAuditTransfers, 20000);

if (teamCreateBtn) {
  teamCreateBtn.addEventListener('click', async () => {
    const payload = {
      name: teamNameEl.value.trim(),
      parent_id: teamParentEl.value || null,
      leaders: parseList(teamLeadersEl.value),
      members: parseList(teamMembersEl.value),
    };
    if (!payload.name) {
      setStatus(teamStatusEl, 'Team name required.', true);
      return;
    }
    const res = await apiFetch('/api/teams', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      setStatus(teamStatusEl, 'Team creation failed.', true);
      return;
    }
    teamNameEl.value = '';
    teamLeadersEl.value = '';
    teamMembersEl.value = '';
    setStatus(teamStatusEl, 'Team created.');
    await fetchTeams();
  });
}

if (projectCreateBtn) {
  projectCreateBtn.addEventListener('click', async () => {
    const payload = {
      name: projectNameEl.value.trim(),
      team_id: projectTeamEl.value || null,
      leaders: parseList(projectLeadersEl.value),
      contributors: parseList(projectContributorsEl.value),
      viewers: parseList(projectViewersEl.value),
      permissions: {
        read: parseList(projectPermReadEl.value),
        write: parseList(projectPermWriteEl.value),
        grant: parseList(projectPermGrantEl.value),
      },
    };
    if (!payload.name) {
      setStatus(projectStatusEl, 'Project name required.', true);
      return;
    }
    const res = await apiFetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      setStatus(projectStatusEl, 'Project creation failed.', true);
      return;
    }
    projectNameEl.value = '';
    projectLeadersEl.value = '';
    projectContributorsEl.value = '';
    projectViewersEl.value = '';
    projectPermReadEl.value = '';
    projectPermWriteEl.value = '';
    projectPermGrantEl.value = '';
    setStatus(projectStatusEl, 'Project created.');
    await fetchProjects();
  });
}

fetchTeams();
fetchProjects();
fetchRoomHistory();
setInterval(fetchRoomHistory, 20000);
