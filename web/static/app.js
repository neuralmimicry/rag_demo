const jobListEl = document.getElementById('jobsList');
const jobDetailEl = document.getElementById('jobDetail');
const jobHintEl = document.getElementById('jobHint');
const logOutputEl = document.getElementById('logOutput');
const workflowSelect = document.getElementById('workflow');
const resetButton = document.getElementById('resetForm');
const clearLogsButton = document.getElementById('clearLogs');
const autoScrollCheckbox = document.getElementById('autoScroll');
const workerCountEl = document.getElementById('workerCount');
const jobCountEl = document.getElementById('jobCount');
const queueQueuedCountEl = document.getElementById('queueQueuedCount');
const queueRunningCountEl = document.getElementById('queueRunningCount');
const queueFailedCountEl = document.getElementById('queueFailedCount');
const queueCompletedCountEl = document.getElementById('queueCompletedCount');
const statusFiltersEl = document.getElementById('statusFilters');
const scopeFiltersEl = document.getElementById('scopeFilters');
const deleteQueueBtn = document.getElementById('deleteQueue');
const deleteArchiveBtn = document.getElementById('deleteArchive');
const cliBubblesEl = document.getElementById('cliBubbles');
const logoutLink = document.getElementById('logoutLink');
const bubblePopover = document.getElementById('bubblePopover');
const bubbleTitleEl = document.getElementById('bubbleTitle');
const bubbleHelpEl = document.getElementById('bubbleHelp');
const bubbleOptionsEl = document.getElementById('bubbleOptions');
const cliArgsPreviewEl = document.getElementById('cliArgsPreview');
const applyCliArgsBtn = document.getElementById('applyCliArgs');
const clearCliArgsBtn = document.getElementById('clearCliArgs');
const secretListEl = document.getElementById('secretList');
const secretFormEl = document.getElementById('secretForm');
const saveSecretBtn = document.getElementById('saveSecret');
const clearSecretFormBtn = document.getElementById('clearSecretForm');
const secretNameEl = document.getElementById('secretName');
const secretValueEl = document.getElementById('secretValue');
const jobStatusEl = document.getElementById('jobStatus');
const tokenEstimateStatusEl = document.getElementById('tokenEstimate');
const tabButtons = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');
const toastContainer = document.getElementById('toastContainer');
const notifyEmailEl = document.getElementById('notifyEmail');
const saveNotifyEmailBtn = document.getElementById('saveNotifyEmail');
const clearNotifyEmailBtn = document.getElementById('clearNotifyEmail');
const notifyStatusEl = document.getElementById('notifyStatus');
const jobSecretListEl = document.getElementById('jobSecretList');
const jobSecretNameEl = document.getElementById('jobSecretName');
const jobSecretValueEl = document.getElementById('jobSecretValue');
const addJobSecretBtn = document.getElementById('addJobSecret');
const clearJobSecretBtn = document.getElementById('clearJobSecret');
const useDefaultSecretsEl = document.getElementById('useDefaultSecrets');
const projectSourceInputs = document.querySelectorAll('input[name="projectSource"]');
const deliverySourceInputs = document.querySelectorAll('input[name="deliverySource"]');
const sourcePanels = document.querySelectorAll('.source-panel');
const projectIdEl = document.getElementById('projectId');
const projectRoleHintEl = document.getElementById('projectRoleHint');
const todoPanelEl = document.getElementById('todoPanel');
const todoInputEl = document.getElementById('todoInput');
const todoDeferInputEl = document.getElementById('todoDeferInput');
const todoAddBtn = document.getElementById('todoAdd');
const todoRefreshBtn = document.getElementById('todoRefresh');
const todoNextIdleBtn = document.getElementById('todoNextIdle');
const todoStatusFiltersEl = document.getElementById('todoStatusFilters');
const todoDeferOnlyEl = document.getElementById('todoDeferOnly');
const todoStatusEl = document.getElementById('todoStatus');
const todoListEl = document.getElementById('todoList');

const tokenPanelEl = document.getElementById('tokenPanel');
const tokenBalanceEl = document.getElementById('tokenBalance');
const tokenAvailableEl = document.getElementById('tokenAvailable');
const tokenEstimateFillEl = document.getElementById('tokenEstimateFill');
const tokenInUseEl = document.getElementById('tokenInUse');
const tokenThresholdEl = document.getElementById('tokenThreshold');
const tokenAvailableLabelEl = document.getElementById('tokenAvailableLabel');
const tokenEstimateLabelEl = document.getElementById('tokenEstimateLabel');
const tokenInUseLabelEl = document.getElementById('tokenInUseLabel');
const tokenScopeLabelEl = document.getElementById('tokenScopeLabel');
const tokenBreakdownEl = document.getElementById('tokenBreakdown');
const tokenUserBalanceEl = document.getElementById('tokenUserBalance');
const tokenTeamBalanceEl = document.getElementById('tokenTeamBalance');
const transferModalEl = document.getElementById('transferModal');
const transferModalTitleEl = document.getElementById('transferModalTitle');
const transferModalDescEl = document.getElementById('transferModalDesc');
const transferTeamRowEl = document.getElementById('transferTeamRow');
const transferTeamSelectEl = document.getElementById('transferTeamSelect');
const transferProjectRowEl = document.getElementById('transferProjectRow');
const transferProjectSelectEl = document.getElementById('transferProjectSelect');
const transferUserRowEl = document.getElementById('transferUserRow');
const transferUserSelectEl = document.getElementById('transferUserSelect');
const transferModalStatusEl = document.getElementById('transferModalStatus');
const transferModalConfirmBtn = document.getElementById('transferModalConfirm');
const transferModalCancelBtn = document.getElementById('transferModalCancel');
const transferModalCloseBtn = document.getElementById('transferModalClose');

const repoUrlEl = document.getElementById('repoUrl');
const repoBranchEl = document.getElementById('repoBranch');
const workBranchEl = document.getElementById('workBranch');
const repoSubdirEl = document.getElementById('repoSubdir');
const requirementsRelPathEl = document.getElementById('requirementsRelPath');
const forkOrgEl = document.getElementById('forkOrg');
const commitMessageEl = document.getElementById('commitMessage');
const gitAuthorNameEl = document.getElementById('gitAuthorName');
const gitAuthorEmailEl = document.getElementById('gitAuthorEmail');

const deliveryRepoUrlEl = document.getElementById('deliveryRepoUrl');
const deliveryRepoBranchEl = document.getElementById('deliveryRepoBranch');
const deliveryWorkBranchEl = document.getElementById('deliveryWorkBranch');
const deliveryRepoSubdirEl = document.getElementById('deliveryRepoSubdir');
const deliveryForkOrgEl = document.getElementById('deliveryForkOrg');
const deliveryCommitMessageEl = document.getElementById('deliveryCommitMessage');
const deliveryAuthorEmailEl = document.getElementById('deliveryAuthorEmail');

const repoBrowserEl = document.getElementById('repoBrowser');
const capabilityCardEl = document.getElementById('capabilityCard');
const capabilityGeneratedAtEl = document.getElementById('capabilityGeneratedAt');
const capabilityFileCountEl = document.getElementById('capabilityFileCount');
const capabilityStatusEl = document.getElementById('capabilityStatus');
const capabilityWorkflowsEl = document.getElementById('capabilityWorkflows');
const capabilityFeaturesEl = document.getElementById('capabilityFeatures');

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
const closeRepoBrowserBtn = document.getElementById('closeRepoBrowser');
const repoBrowserListEl = document.getElementById('repoBrowserList');
const repoBrowserStatusEl = document.getElementById('repoBrowserStatus');
const repoSearchEl = document.getElementById('repoSearch');
const browseActionButtons = document.querySelectorAll('.browse-action');

let repoBrowserState = {
  items: [],
  targetId: null,
  mode: 'file',
  context: 'project',
};

let durationTimer = null;
let reqProgressTimer = null;
const requirementsProgressCache = new Map();
const requirementsSummaryCache = new Map();

let currentScope = 'team';
let projectOptions = [];
let activeSessionId = null;
let sessionStream = null;
let sessionSnapshot = null;
let workspaceSnapshot = null;
let workspaceCapabilities = null;
let jobsPollTimer = null;
let todoFilterStatus = 'todo';
let todoDeferOnly = false;

const assistantMessagesEl = document.getElementById('assistantMessages');
const assistantInputEl = document.getElementById('assistantInput');
const assistantAskBtn = document.getElementById('assistantAsk');
const assistantDraftBtn = document.getElementById('assistantDraft');
const assistantInsertBtn = document.getElementById('assistantInsert');
const assistantClearBtn = document.getElementById('assistantClear');
const assistantStatusEl = document.getElementById('assistantStatus');
const requirementsTextEl = document.getElementById('requirementsText');
const reqGridBodyEl = document.getElementById('reqGridBody');
const reqExtractBtn = document.getElementById('reqExtract');
const reqExtractJobBtn = document.getElementById('reqExtractJob');
const reqAddBtn = document.getElementById('reqAdd');
const reqApplyBtn = document.getElementById('reqApply');
const reqGridStatusEl = document.getElementById('reqGridStatus');
const reqImportBtn = document.getElementById('reqImport');
const reqImportFileEl = document.getElementById('reqImportFile');
const reqExportBtn = document.getElementById('reqExport');
const reqExportFormatEl = document.getElementById('reqExportFormat');
const formAssistantPromptEl = document.getElementById('formAssistantPrompt');
const formAssistantScopeEl = document.getElementById('formAssistantScope');
const formAssistantEmptyOnlyEl = document.getElementById('formAssistantEmptyOnly');
const formAssistantSuggestBtn = document.getElementById('formAssistantSuggest');
const formAssistantApplyAllBtn = document.getElementById('formAssistantApplyAll');
const formAssistantClearBtn = document.getElementById('formAssistantClear');
const formAssistantListEl = document.getElementById('formAssistantList');
const formAssistantStatusEl = document.getElementById('formAssistantStatus');

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

const apiEventSource = (path) => new EventSource(apiUrl(path), { withCredentials: true });

const assistantState = {
  messages: [],
  lastResponse: '',
};

let formAssistantSuggestions = [];

let jobs = [];
let selectedJobId = null;
let logStream = null;
let currentFilter = 'all';
let jobSecrets = [];
let requirementsGrid = [];
let jobStatusCache = {};
let hasLoadedJobs = false;
let tokenSnapshot = null;
let pendingEstimate = null;
let estimateTimer = null;
let refundPanelOpen = false;
let refundPanelJobId = null;
const refundDrafts = new Map();
let pendingSelectJobId = null;
let currentProfile = null;
let accessTeams = [];
let accessTeamIndex = {};

(() => {
  if (typeof window === 'undefined') return;
  const params = new URLSearchParams(window.location.search);
  const scopeParam = (params.get('scope') || '').toLowerCase();
  const jobId = params.get('job_id');
  if (scopeParam === 'personal' || scopeParam === 'my') {
    currentScope = 'personal';
  } else if (scopeParam === 'team') {
    currentScope = 'team';
  } else if (jobId) {
    currentScope = 'personal';
  }
  if (jobId) {
    pendingSelectJobId = jobId;
  }
})();

const STATUS_OPTIONS = ['all', 'queued', 'running', 'paused', 'stopped', 'completed', 'failed', 'archive'];
const FILTER_LABELS = {
  archive: 'Archive',
};
const REQ_LINE_RE = /^\s*(?:[-*+]\s*)?(REQ-\d{3,})\s*(?:[:\-–]\s*)?(.*)$/i;
const REQ_BLOCK_START = '<!-- REQ-REGISTER START -->';
const REQ_BLOCK_END = '<!-- REQ-REGISTER END -->';
const EDITOR_WORKFLOWS = new Set(['project_solver', 'project', 'topic_research']);

const CLI_BUBBLES = [
  {
    key: 'llm-provider',
    label: 'LLM Provider',
    type: 'select',
    flag: '--llm-provider',
    help: 'Override the LLM provider used by run_refiner.py.',
    options: [
      { label: 'OpenAI', value: 'openai' },
      { label: 'Gemini', value: 'gemini' },
      { label: 'Ollama', value: 'ollama' },
    ],
  },
  {
    key: 'llm-model',
    label: 'LLM Model',
    type: 'input',
    flag: '--llm-model',
    help: 'Override the model name for the selected provider.',
    placeholder: 'gpt-5.2-codex',
  },
  {
    key: 'llm-temperature',
    label: 'Temperature',
    type: 'input',
    flag: '--llm-temperature',
    help: 'Sampling temperature. Lower values are more deterministic.',
    placeholder: '0.2',
  },
  {
    key: 'llm-max-tokens',
    label: 'Max Tokens',
    type: 'input',
    flag: '--llm-max-tokens',
    help: 'Max output tokens per LLM call.',
    placeholder: '2000',
  },
  {
    key: 'llm-timeout',
    label: 'LLM Timeout',
    type: 'input',
    flag: '--llm-timeout',
    help: 'Timeout in seconds for LLM requests.',
    placeholder: '120',
  },
  {
    key: 'llm-reasoning',
    label: 'Reasoning Effort',
    type: 'select',
    flag: '--llm-reasoning-effort',
    help: 'Reasoning effort for supported models.',
    options: [
      { label: 'None', value: 'none' },
      { label: 'Low', value: 'low' },
      { label: 'Medium', value: 'medium' },
      { label: 'High', value: 'high' },
      { label: 'XHigh', value: 'xhigh' },
    ],
  },
  {
    key: 'dry-run',
    label: 'Dry Run',
    type: 'toggle',
    flag: '--dry-run',
    help: 'Skip LLM calls; only compute baseline metrics and UI.',
  },
  {
    key: 'action-plan',
    label: 'Action Plan',
    type: 'toggle',
    flag: '--action-plan',
    help: 'Include action plan sections in Jira/Confluence reports.',
  },
  {
    key: 'post-comments',
    label: 'Post Comments',
    type: 'toggle',
    flag: '--post-comments',
    help: 'Post AI-generated insights as comments.',
  },
  {
    key: 'post-target',
    label: 'Post Target',
    type: 'select',
    flag: '--post-target',
    help: 'Where to post comments.',
    options: [
      { label: 'Jira', value: 'jira' },
      { label: 'Confluence', value: 'confluence' },
      { label: 'Both', value: 'both' },
    ],
  },
  {
    key: 'use-rovo',
    label: 'Use Rovo',
    type: 'toggle',
    flag: '--use-rovo',
    help: 'Prefer Atlassian Rovo endpoints when available.',
  },
  {
    key: 'project-run',
    label: 'Project Run',
    type: 'toggle',
    flag: '--project-run',
    help: 'Allow project solver to run shell commands.',
  },
  {
    key: 'project-max-steps',
    label: 'Project Max Steps',
    type: 'input',
    flag: '--project-max-steps',
    help: 'Max number of steps to apply in project solver.',
    placeholder: '25',
  },
  {
    key: 'project-iterations',
    label: 'Project Iterations',
    type: 'input',
    flag: '--project-iterations',
    help: 'Max planning iterations for project solver.',
    placeholder: '3',
  },
  {
    key: 'delivery-run',
    label: 'Delivery Run',
    type: 'toggle',
    flag: '--delivery-run',
    help: 'Execute delivery pipeline commands (otherwise dry run).',
  },
  {
    key: 'delivery-allow-unfinished',
    label: 'Allow Unfinished',
    type: 'toggle',
    flag: '--delivery-allow-unfinished',
    help: 'Allow deploy stages even if solver output is incomplete.',
  },
  {
    key: 'delivery-enable-interim',
    label: 'Enable Interim',
    type: 'toggle',
    flag: '--delivery-enable-interim',
    help: 'Enable interim deploy/teardown stages.',
  },
  {
    key: 'disable-jira',
    label: 'Disable Jira',
    type: 'toggle',
    flag: '--disable-jira',
    help: 'Disable all Jira operations.',
  },
  {
    key: 'disable-confluence',
    label: 'Disable Confluence',
    type: 'toggle',
    flag: '--disable-confluence',
    help: 'Disable all Confluence operations.',
  },
];

const cliBuilderState = {
  flags: new Set(),
  values: {},
};

function renderCliBubbles() {
  cliBubblesEl.innerHTML = '';
  CLI_BUBBLES.forEach((bubble) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'bubble';
    btn.textContent = bubble.label;
    btn.dataset.bubbleKey = bubble.key;
    btn.dataset.tooltip = bubble.help;
    btn.addEventListener('click', (event) => {
      event.stopPropagation();
      if (bubble.type === 'toggle') {
        if (cliBuilderState.flags.has(bubble.flag)) {
          cliBuilderState.flags.delete(bubble.flag);
        } else {
          cliBuilderState.flags.add(bubble.flag);
        }
        closeBubblePopover();
        updateBubbleStates();
        renderCliPreview();
        return;
      }
      openBubblePopover(btn, bubble);
    });
    cliBubblesEl.appendChild(btn);
  });
  updateBubbleStates();
}

function updateBubbleStates() {
  const buttons = cliBubblesEl.querySelectorAll('.bubble');
  buttons.forEach((btn) => {
    const bubble = CLI_BUBBLES.find((item) => item.key === btn.dataset.bubbleKey);
    if (!bubble) return;
    const isActive =
      (bubble.type === 'toggle' && cliBuilderState.flags.has(bubble.flag)) ||
      (bubble.type !== 'toggle' && cliBuilderState.values[bubble.flag]);
    btn.classList.toggle('active', Boolean(isActive));
    if (bubble.help) {
      btn.setAttribute('title', bubble.help);
    }
  });
}

function openBubblePopover(target, bubble) {
  if (!bubblePopover) {
    return;
  }
  bubbleTitleEl.textContent = bubble.label;
  bubbleHelpEl.textContent = bubble.help || '';
  bubbleOptionsEl.innerHTML = '';

  if (bubble.type === 'toggle') {
    const enabled = cliBuilderState.flags.has(bubble.flag);
    const option = document.createElement('div');
    option.className = `bubble-option ${enabled ? 'active' : ''}`;
    option.textContent = enabled ? 'Remove flag' : 'Add flag';
    option.addEventListener('click', () => {
      if (enabled) {
        cliBuilderState.flags.delete(bubble.flag);
      } else {
        cliBuilderState.flags.add(bubble.flag);
      }
      closeBubblePopover();
      updateBubbleStates();
      renderCliPreview();
    });
    bubbleOptionsEl.appendChild(option);
  } else if (bubble.type === 'select') {
    bubble.options.forEach((opt) => {
      const option = document.createElement('div');
      const active = cliBuilderState.values[bubble.flag] === opt.value;
      option.className = `bubble-option ${active ? 'active' : ''}`;
      option.textContent = opt.label;
      option.addEventListener('click', () => {
        cliBuilderState.values[bubble.flag] = opt.value;
        closeBubblePopover();
        updateBubbleStates();
        renderCliPreview();
      });
      bubbleOptionsEl.appendChild(option);
    });
    if (cliBuilderState.values[bubble.flag]) {
      const clearOption = document.createElement('div');
      clearOption.className = 'bubble-option';
      clearOption.textContent = 'Clear selection';
      clearOption.addEventListener('click', () => {
        delete cliBuilderState.values[bubble.flag];
        closeBubblePopover();
        updateBubbleStates();
        renderCliPreview();
      });
      bubbleOptionsEl.appendChild(clearOption);
    }
  } else if (bubble.type === 'input') {
    const wrapper = document.createElement('div');
    wrapper.className = 'bubble-input';
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = bubble.placeholder || 'value';
    input.value = cliBuilderState.values[bubble.flag] || '';
    wrapper.appendChild(input);
    bubbleOptionsEl.appendChild(wrapper);

    const actions = document.createElement('div');
    actions.className = 'bubble-actions';
    const applyBtn = document.createElement('button');
    applyBtn.type = 'button';
    applyBtn.className = 'bubble-option';
    applyBtn.textContent = 'Apply value';
    applyBtn.addEventListener('click', () => {
      const value = input.value.trim();
      if (value) {
        cliBuilderState.values[bubble.flag] = value;
      } else {
        delete cliBuilderState.values[bubble.flag];
      }
      closeBubblePopover();
      updateBubbleStates();
      renderCliPreview();
    });
    const clearBtn = document.createElement('button');
    clearBtn.type = 'button';
    clearBtn.className = 'bubble-option';
    clearBtn.textContent = 'Clear value';
    clearBtn.addEventListener('click', () => {
      delete cliBuilderState.values[bubble.flag];
      closeBubblePopover();
      updateBubbleStates();
      renderCliPreview();
    });
    actions.appendChild(applyBtn);
    actions.appendChild(clearBtn);
    bubbleOptionsEl.appendChild(actions);
  }

  bubblePopover.hidden = false;

  const rect = target.getBoundingClientRect();
  const container = bubblePopover.parentElement || document.body;
  const containerRect = container.getBoundingClientRect();
  const popRect = bubblePopover.getBoundingClientRect();
  let top = rect.bottom - containerRect.top + 8;
  let left = rect.left - containerRect.left;
  const maxLeft = containerRect.width - popRect.width - 8;
  if (left < 8) left = 8;
  if (left > maxLeft) left = Math.max(8, maxLeft);
  if (top + popRect.height > containerRect.height) {
    top = rect.top - containerRect.top - popRect.height - 8;
  }
  bubblePopover.style.top = `${top}px`;
  bubblePopover.style.left = `${left}px`;
}

function closeBubblePopover() {
  bubblePopover.hidden = true;
}

function renderCliPreview() {
  const args = [];
  CLI_BUBBLES.forEach((bubble) => {
    if (bubble.type === 'toggle' && cliBuilderState.flags.has(bubble.flag)) {
      args.push(bubble.flag);
    } else if (bubble.type !== 'toggle' && cliBuilderState.values[bubble.flag]) {
      args.push(bubble.flag, cliBuilderState.values[bubble.flag]);
    }
  });
  cliArgsPreviewEl.value = args.join(' ');
}

function applyCliArgs() {
  document.getElementById('extraArgs').value = cliArgsPreviewEl.value.trim();
  persistFormState();
}

function clearCliArgs() {
  cliBuilderState.flags.clear();
  cliBuilderState.values = {};
  renderCliPreview();
  updateBubbleStates();
}

function renderJobSecrets() {
  if (!jobSecretListEl) return;
  jobSecretListEl.innerHTML = '';
  if (!jobSecrets.length) {
    jobSecretListEl.innerHTML = '<p class="subtitle">No per-job secrets added.</p>';
    return;
  }
  jobSecrets.forEach((secret, index) => {
    const item = document.createElement('div');
    item.className = 'secret-item';
    item.innerHTML = `
      <div>
        <div class="secret-name">${secret.name}</div>
        <div class="secret-meta">Value set · masked</div>
      </div>
      <div class="secret-actions">
        <button type="button" class="ghost" data-secret-index="${index}">Remove</button>
      </div>
    `;
    item.querySelector('button[data-secret-index]').addEventListener('click', () => {
      jobSecrets = jobSecrets.filter((_, i) => i !== index);
      renderJobSecrets();
    });
    jobSecretListEl.appendChild(item);
  });
}

function addJobSecret() {
  const name = jobSecretNameEl.value.trim();
  const value = jobSecretValueEl.value.trim();
  if (!name || !value) return;
  jobSecrets.push({ name, value });
  jobSecretNameEl.value = '';
  jobSecretValueEl.value = '';
  renderJobSecrets();
}

function clearJobSecretForm() {
  jobSecretNameEl.value = '';
  jobSecretValueEl.value = '';
}

async function fetchSecrets() {
  if (!secretListEl) return;
  try {
    const res = await apiFetch('/api/secrets');
    if (!res.ok) return;
    const data = await res.json();
    renderSecrets(data.secrets || []);
  } catch (err) {
    console.error('Failed to fetch secrets', err);
  }
}

function renderSecrets(secrets) {
  secretListEl.innerHTML = '';
  if (!secrets.length) {
    secretListEl.innerHTML = '<p class="subtitle">No secrets stored yet.</p>';
    return;
  }
  secrets.forEach((secret) => {
    const item = document.createElement('div');
    item.className = 'secret-item';
    item.innerHTML = `
      <div>
        <div class="secret-name">${secret.name}</div>
        <div class="secret-meta">${secret.masked} · updated ${formatAbsoluteTime(secret.updated_at)}</div>
      </div>
      <div class="secret-actions">
        <button type="button" class="ghost" data-secret="${secret.name}">Delete</button>
      </div>
    `;
    item.querySelector('button[data-secret]').addEventListener('click', () => deleteSecret(secret.name));
    secretListEl.appendChild(item);
  });
}

async function submitSecret() {
  const name = secretNameEl.value.trim();
  const value = secretValueEl.value.trim();
  if (!name || !value) return;
  try {
    const res = await apiFetch('/api/secrets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, value }),
    });
    if (!res.ok) {
      const data = await res.json();
      console.error('Secret save failed', data);
      return;
    }
    secretValueEl.value = '';
    await fetchSecrets();
  } catch (err) {
    console.error('Secret save error', err);
  }
}

async function deleteSecret(name) {
  if (!name) return;
  try {
    const res = await apiFetch(`/api/secrets/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
    if (!res.ok) {
      console.error('Delete failed');
      return;
    }
    await fetchSecrets();
  } catch (err) {
    console.error('Delete error', err);
  }
}

async function fetchProfile() {
  try {
    const res = await apiFetch('/api/profile');
    if (!res.ok) return;
    const data = await res.json();
    currentProfile = data;
    if (notifyEmailEl) notifyEmailEl.value = data.email || '';
    await fetchAccessTree();
  } catch (err) {
    console.error('Failed to fetch profile', err);
  }
}

async function updateNotifyEmail(email) {
  if (!notifyEmailEl) return;
  clearNotifyStatus();
  try {
    const res = await apiFetch('/api/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await res.json();
    if (!res.ok) {
      showNotifyStatus(data.details || 'Failed to save notification email.', true);
      return;
    }
    notifyEmailEl.value = data.email || '';
    showNotifyStatus('Notification email saved.');
    setTimeout(clearNotifyStatus, 3000);
  } catch (err) {
    showNotifyStatus('Failed to save notification email.', true);
  }
}

function initFilters() {
  statusFiltersEl.innerHTML = '';
  STATUS_OPTIONS.forEach((status) => {
    const btn = document.createElement('button');
    btn.className = `filter-btn ${status === currentFilter ? 'active' : ''}`;
    btn.textContent = FILTER_LABELS[status] || status;
    btn.addEventListener('click', () => {
      currentFilter = status;
      document.querySelectorAll('.filter-btn').forEach((el) => el.classList.remove('active'));
      btn.classList.add('active');
      renderJobs();
    });
    statusFiltersEl.appendChild(btn);
  });
}

function setActiveTab(tabKey) {
  tabButtons.forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === tabKey);
  });
  tabPanels.forEach((panel) => {
    panel.classList.toggle('active', panel.dataset.tab === tabKey);
  });
  try {
    localStorage.setItem('refiner_active_tab', tabKey);
  } catch (err) {
    // ignore storage failures
  }
}

function showJobStatus(message, isError = false) {
  if (!jobStatusEl) return;
  jobStatusEl.textContent = message;
  jobStatusEl.hidden = false;
  jobStatusEl.classList.toggle('error', Boolean(isError));
}

function clearJobStatus() {
  if (!jobStatusEl) return;
  jobStatusEl.hidden = true;
  jobStatusEl.textContent = '';
  jobStatusEl.classList.remove('error');
}

function showTokenEstimateStatus(message, isError = false) {
  if (!tokenEstimateStatusEl) return;
  tokenEstimateStatusEl.textContent = message;
  tokenEstimateStatusEl.hidden = false;
  tokenEstimateStatusEl.classList.toggle('error', Boolean(isError));
}

function clearTokenEstimateStatus() {
  if (!tokenEstimateStatusEl) return;
  tokenEstimateStatusEl.hidden = true;
  tokenEstimateStatusEl.textContent = '';
  tokenEstimateStatusEl.classList.remove('error');
}

function showNotifyStatus(message, isError = false) {
  if (!notifyStatusEl) return;
  notifyStatusEl.textContent = message;
  notifyStatusEl.hidden = false;
  notifyStatusEl.classList.toggle('error', Boolean(isError));
}

function clearNotifyStatus() {
  if (!notifyStatusEl) return;
  notifyStatusEl.hidden = true;
  notifyStatusEl.textContent = '';
  notifyStatusEl.classList.remove('error');
}

function showTodoStatus(message, isError = false) {
  if (!todoStatusEl) return;
  todoStatusEl.textContent = message;
  todoStatusEl.hidden = false;
  todoStatusEl.classList.toggle('error', Boolean(isError));
}

function clearTodoStatus() {
  if (!todoStatusEl) return;
  todoStatusEl.hidden = true;
  todoStatusEl.textContent = '';
  todoStatusEl.classList.remove('error');
}

function showToast(title, message, tone = 'success', onClick = null) {
  if (!toastContainer) return;
  const toast = document.createElement('div');
  toast.className = `toast ${tone}`;
  toast.innerHTML = `
    <div>
      <div class="toast-title">${title}</div>
      <div class="toast-body">${message}</div>
    </div>
    <button type="button" class="toast-close" aria-label="Dismiss">×</button>
  `;
  const closeBtn = toast.querySelector('.toast-close');
  closeBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    toast.remove();
  });
  toast.addEventListener('click', () => {
    if (onClick) onClick();
    toast.remove();
  });
  toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 7000);
}

function renderTodoList(items = []) {
  if (!todoListEl) return;
  todoListEl.innerHTML = '';
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'todo-empty';
    empty.textContent = 'No thoughts captured yet.';
    todoListEl.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'todo-item';
    row.dataset.todoId = item.id;
    const status = item.status || 'todo';
    const badge = `<span class="todo-badge ${status}">${status}</span>`;
    const metaParts = [];
    if (item.source) metaParts.push(`Source: ${item.source}`);
    if (item.device) metaParts.push(`Device: ${item.device}`);
    if (item.defer_until_idle) metaParts.push('Idle-only');
    if (item.created_at) metaParts.push(`Created ${formatAbsoluteTime(item.created_at)}`);
    row.innerHTML = `
      <div class="todo-item-header">
        <div class="todo-text">${escapeHtml(item.text || '')}</div>
        ${badge}
      </div>
      <div class="todo-meta">${metaParts.join(' · ')}</div>
      <div class="todo-actions">
        ${status === 'todo' ? '<button type="button" class="ghost" data-todo-action="done">Done</button>' : ''}
        ${status !== 'todo' ? '<button type="button" class="ghost" data-todo-action="reopen">Reopen</button>' : ''}
        ${status !== 'archived' ? '<button type="button" class="ghost" data-todo-action="archive">Archive</button>' : ''}
        <button type="button" class="ghost" data-todo-action="delete">Delete</button>
      </div>
    `;
    todoListEl.appendChild(row);
  });
}

async function fetchTodos(showStatus = false) {
  if (!todoPanelEl) return;
  const params = new URLSearchParams();
  if (todoFilterStatus) params.set('status', todoFilterStatus);
  if (todoDeferOnly) params.set('defer', '1');
  params.set('limit', '50');
  try {
    const res = await apiFetch(`/api/todos?${params.toString()}`);
    if (res.status === 401 || res.redirected) {
      showTodoStatus('Sign in to view todos.', true);
      return;
    }
    const data = await res.json();
    if (!res.ok) {
      showTodoStatus(data.error || 'Failed to load todos.', true);
      return;
    }
    renderTodoList(data.items || []);
    if (showStatus) {
      clearTodoStatus();
    }
  } catch (err) {
    showTodoStatus('Failed to load todos.', true);
  }
}

async function createTodo(text, deferUntilIdle) {
  const payload = {
    text,
    source: 'manual',
    defer_until_idle: Boolean(deferUntilIdle),
  };
  const res = await apiFetch('/api/todos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || 'Failed to add todo.');
  }
  return data.todo;
}

async function updateTodo(todoId, updates) {
  const res = await apiFetch(`/api/todos/${todoId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || 'Failed to update todo.');
  }
  return data.todo;
}

async function deleteTodo(todoId) {
  const res = await apiFetch(`/api/todos/${todoId}`, { method: 'DELETE' });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || 'Failed to delete todo.');
  }
  return data;
}

function initTodoPanel() {
  if (!todoPanelEl) return;
  if (todoStatusFiltersEl) {
    todoStatusFiltersEl.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-status]');
      if (!btn) return;
      todoStatusFiltersEl.querySelectorAll('.filter-btn').forEach((el) => el.classList.remove('active'));
      btn.classList.add('active');
      todoFilterStatus = btn.dataset.status || 'todo';
      fetchTodos(true);
    });
  }
  if (todoDeferOnlyEl) {
    todoDeferOnlyEl.addEventListener('change', () => {
      todoDeferOnly = Boolean(todoDeferOnlyEl.checked);
      fetchTodos(true);
    });
  }
  if (todoRefreshBtn) {
    todoRefreshBtn.addEventListener('click', () => fetchTodos(true));
  }
  if (todoAddBtn) {
    todoAddBtn.addEventListener('click', async () => {
      const text = todoInputEl?.value?.trim() || '';
      if (!text) {
        showTodoStatus('Enter a thought to capture.', true);
        return;
      }
      showTodoStatus('Saving...');
      try {
        await createTodo(text, todoDeferInputEl?.checked);
        if (todoInputEl) todoInputEl.value = '';
        showTodoStatus('Captured.');
        fetchTodos();
        setTimeout(clearTodoStatus, 2000);
      } catch (err) {
        showTodoStatus(err.message || 'Failed to add todo.', true);
      }
    });
  }
  if (todoInputEl) {
    todoInputEl.addEventListener('keydown', (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        todoAddBtn?.click();
      }
    });
  }
  if (todoNextIdleBtn) {
    todoNextIdleBtn.addEventListener('click', async () => {
      showTodoStatus('Checking idle queue...');
      try {
        const res = await apiFetch('/api/todos/next?idle=1');
        const data = await res.json();
        if (res.status === 409) {
          showTodoStatus('Refiner is busy. Next idle thought will wait.', true);
          return;
        }
        if (!res.ok) {
          showTodoStatus(data.error || 'Failed to fetch next todo.', true);
          return;
        }
        if (!data.todo) {
          showTodoStatus('No idle-only todos queued.');
          return;
        }
        showTodoStatus(`Next up: ${data.todo.text}`);
        fetchTodos();
      } catch (err) {
        showTodoStatus('Failed to fetch next todo.', true);
      }
    });
  }
  if (todoListEl) {
    todoListEl.addEventListener('click', async (event) => {
      const btn = event.target.closest('[data-todo-action]');
      if (!btn) return;
      const card = btn.closest('[data-todo-id]');
      const todoId = card?.dataset?.todoId;
      if (!todoId) return;
      const action = btn.dataset.todoAction;
      try {
        if (action === 'delete') {
          await deleteTodo(todoId);
        } else if (action === 'done') {
          await updateTodo(todoId, { status: 'done' });
        } else if (action === 'archive') {
          await updateTodo(todoId, { status: 'archived' });
        } else if (action === 'reopen') {
          await updateTodo(todoId, { status: 'todo' });
        }
        fetchTodos();
      } catch (err) {
        showTodoStatus(err.message || 'Todo update failed.', true);
      }
    });
  }
  fetchTodos();
}

function updateTokenMeter(snapshot, estimate = null) {
  if (!tokenPanelEl || !snapshot) return;
  const balance = snapshot.balance ?? 0;
  const reserved = snapshot.reserved ?? 0;
  const inUse = snapshot.in_use ?? 0;
  const available = snapshot.available ?? Math.max(0, balance - reserved);
  const capacity = snapshot.display_capacity ?? snapshot.capacity ?? balance ?? 1;
  const displayCapacity = Math.max(1, capacity);
  const estimateOnly = Math.max(0, reserved - inUse);
  const hasTeamScope = snapshot.scope === 'team' || snapshot.team_id || snapshot.team_balance !== undefined;

  const availablePct = Math.min(100, (available / displayCapacity) * 100);
  const estimatePct = Math.min(100, (estimateOnly / displayCapacity) * 100);
  let inUsePct = Math.min(100, (inUse / displayCapacity) * 100);
  const remainingPct = Math.max(0, 100 - availablePct - estimatePct);
  inUsePct = Math.min(inUsePct, remainingPct);
  const threshold = snapshot.low_threshold ?? 0;
  const thresholdPct = Math.min(100, (threshold / displayCapacity) * 100);

  tokenPanelEl.classList.toggle('low', snapshot.status === 'low');
  tokenPanelEl.classList.toggle('full', snapshot.capacity && balance >= snapshot.capacity);
  if (tokenBalanceEl) tokenBalanceEl.textContent = balance.toString();
  if (tokenScopeLabelEl) {
    tokenScopeLabelEl.textContent = hasTeamScope ? 'Token Balance (Personal + Team)' : 'Token Balance';
  }
  if (tokenAvailableEl) {
    tokenAvailableEl.style.left = '0%';
    tokenAvailableEl.style.width = `${availablePct}%`;
  }
  if (tokenEstimateFillEl) {
    tokenEstimateFillEl.style.left = `${availablePct}%`;
    tokenEstimateFillEl.style.width = `${estimatePct}%`;
  }
  if (tokenInUseEl) {
    tokenInUseEl.style.left = `${availablePct + estimatePct}%`;
    tokenInUseEl.style.width = `${inUsePct}%`;
  }
  if (tokenThresholdEl) tokenThresholdEl.style.left = `${thresholdPct}%`;
  if (tokenAvailableLabelEl) tokenAvailableLabelEl.textContent = `Available: ${available}`;
  if (tokenEstimateLabelEl) tokenEstimateLabelEl.textContent = `In estimate: ${estimateOnly}`;
  if (tokenInUseLabelEl) tokenInUseLabelEl.textContent = `In use: ${inUse}`;
  if (tokenBreakdownEl) {
    tokenBreakdownEl.hidden = !hasTeamScope;
  }
  if (hasTeamScope) {
    const userBalance = snapshot.user_balance ?? snapshot.balance ?? 0;
    const teamBalance = snapshot.team_balance ?? 0;
    const teamName = snapshot.team_name ? `Team (${snapshot.team_name})` : 'Team';
    if (tokenUserBalanceEl) tokenUserBalanceEl.textContent = `Personal: ${userBalance}`;
    if (tokenTeamBalanceEl) tokenTeamBalanceEl.textContent = `${teamName}: ${teamBalance}`;
  }

  if (estimate !== null && Number.isFinite(estimate)) {
    showTokenEstimateStatus(`Current job estimate: ${estimate} tokens`);
  } else {
    clearTokenEstimateStatus();
  }
}

function showJobToast(job) {
  const label = job.status === 'failed' ? 'Job failed' : 'Job completed';
  const detail = `${job.project_name || 'Untitled'} · ${job.workflow} · ${job.id.slice(0, 8)}`;
  const tone = job.status === 'failed' ? 'error' : 'success';
  showToast(label, detail, tone, () => selectJob(job.id));
}

function showRepoStatus(message, isError = false) {
  if (!repoBrowserStatusEl) return;
  repoBrowserStatusEl.textContent = message;
  repoBrowserStatusEl.hidden = false;
  repoBrowserStatusEl.classList.toggle('error', Boolean(isError));
}

function clearRepoStatus() {
  if (!repoBrowserStatusEl) return;
  repoBrowserStatusEl.hidden = true;
  repoBrowserStatusEl.textContent = '';
  repoBrowserStatusEl.classList.remove('error');
}

function showAssistantStatus(message, isError = false) {
  if (!assistantStatusEl) return;
  assistantStatusEl.textContent = message;
  assistantStatusEl.hidden = false;
  assistantStatusEl.classList.toggle('error', Boolean(isError));
}

function clearAssistantStatus() {
  if (!assistantStatusEl) return;
  assistantStatusEl.textContent = '';
  assistantStatusEl.hidden = true;
  assistantStatusEl.classList.remove('error');
}

function renderAssistantMessages() {
  if (!assistantMessagesEl) return;
  assistantMessagesEl.innerHTML = '';
  if (!assistantState.messages.length) {
    assistantMessagesEl.innerHTML = '<p class="subtitle">Ask a question to get started.</p>';
    return;
  }
  assistantState.messages.forEach((msg) => {
    const item = document.createElement('div');
    item.className = `assistant-message ${msg.role}`;
    item.textContent = msg.content;
    assistantMessagesEl.appendChild(item);
  });
  assistantMessagesEl.scrollTop = assistantMessagesEl.scrollHeight;
}

async function sendAssistant(mode) {
  const prompt = assistantInputEl ? assistantInputEl.value.trim() : '';
  if (mode === 'ask' && !prompt) {
    showAssistantStatus('Enter a prompt for the assistant.', true);
    return;
  }
  clearAssistantStatus();
  if (prompt && mode === 'ask') {
    assistantState.messages.push({ role: 'user', content: prompt });
    renderAssistantMessages();
    assistantInputEl.value = '';
  }

  const payload = {
    mode,
    prompt,
    requirements_text: requirementsTextEl ? requirementsTextEl.value.trim() : '',
    messages: assistantState.messages,
    provider: document.getElementById('llmProvider')?.value || undefined,
    model: document.getElementById('llmModel')?.value.trim() || undefined,
    temperature: parseFloat(document.getElementById('llmTemperature')?.value || '0.2'),
    max_tokens: parseInt(document.getElementById('llmMaxTokens')?.value || '', 10),
  };
  if (Number.isNaN(payload.temperature)) delete payload.temperature;
  if (Number.isNaN(payload.max_tokens)) delete payload.max_tokens;

  showAssistantStatus('Thinking...');
  try {
    const res = await apiFetch('/api/assistant/requirements', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    const data = await res.json();
    if (!res.ok) {
      showAssistantStatus(data.details || data.error || 'Assistant request failed.', true);
      return;
    }
    assistantState.messages.push({ role: 'assistant', content: data.reply });
    assistantState.lastResponse = data.reply;
    renderAssistantMessages();
    showAssistantStatus('Response ready.');
  } catch (err) {
    console.error(err);
    showAssistantStatus('Assistant request failed. Check console.', true);
  }
}

function insertAssistantResponse() {
  if (!assistantState.lastResponse || !requirementsTextEl) return;
  const current = requirementsTextEl.value.trim();
  const next = current ? `${current}\n\n${assistantState.lastResponse}` : assistantState.lastResponse;
  requirementsTextEl.value = next;
  extractRequirementsFromField();
}

function clearAssistantChat() {
  assistantState.messages = [];
  assistantState.lastResponse = '';
  renderAssistantMessages();
  clearAssistantStatus();
}

function showReqGridStatus(message, isError = false) {
  if (!reqGridStatusEl) return;
  reqGridStatusEl.textContent = message;
  reqGridStatusEl.hidden = false;
  reqGridStatusEl.classList.toggle('error', Boolean(isError));
}

function clearReqGridStatus() {
  if (!reqGridStatusEl) return;
  reqGridStatusEl.textContent = '';
  reqGridStatusEl.hidden = true;
  reqGridStatusEl.classList.remove('error');
}

function extractRequirementsFromText(text) {
  if (!text) return [];
  const lines = text.split(/\r?\n/);
  const items = [];
  let current = null;
  lines.forEach((raw) => {
    const line = raw.trim();
    const match = REQ_LINE_RE.exec(line);
    if (match) {
      if (current) items.push(current);
      const id = match[1].toUpperCase();
      const title = (match[2] || '').trim();
      current = { id, title, description: '' };
      return;
    }
    if (current) {
      if (!line) {
        if (current.description) {
          current.description = current.description.trim();
        }
        items.push(current);
        current = null;
        return;
      }
      if (!REQ_LINE_RE.test(line)) {
        current.description += (current.description ? '\n' : '') + line;
      }
    }
  });
  if (current) {
    current.description = current.description.trim();
    items.push(current);
  }
  const seen = new Set();
  return items.filter((item) => {
    if (!item.id || seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

function renderRequirementsGrid() {
  if (!reqGridBodyEl) return;
  reqGridBodyEl.innerHTML = '';
  if (!requirementsGrid.length) {
    reqGridBodyEl.innerHTML = '<div class="req-grid-empty">No REQ items detected yet.</div>';
    return;
  }
  requirementsGrid.forEach((req, index) => {
    const row = document.createElement('div');
    row.className = 'req-grid-row';
    const idEl = document.createElement('input');
    idEl.type = 'text';
    idEl.className = 'req-id';
    idEl.placeholder = 'REQ-001';
    idEl.value = req.id || '';

    const titleEl = document.createElement('input');
    titleEl.type = 'text';
    titleEl.className = 'req-title';
    titleEl.placeholder = 'Short title';
    titleEl.value = req.title || '';

    const descEl = document.createElement('textarea');
    descEl.className = 'req-desc';
    descEl.placeholder = 'Description';
    descEl.value = req.description || '';

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'ghost req-remove';
    removeBtn.textContent = 'Remove';

    row.appendChild(idEl);
    row.appendChild(titleEl);
    row.appendChild(descEl);
    row.appendChild(removeBtn);
    idEl.addEventListener('input', (event) => {
      requirementsGrid[index].id = event.target.value.trim().toUpperCase();
    });
    titleEl.addEventListener('input', (event) => {
      requirementsGrid[index].title = event.target.value;
    });
    descEl.addEventListener('input', (event) => {
      requirementsGrid[index].description = event.target.value;
    });
    removeBtn.addEventListener('click', () => {
      requirementsGrid.splice(index, 1);
      renderRequirementsGrid();
    });
    reqGridBodyEl.appendChild(row);
  });
}

function nextRequirementId() {
  let max = 0;
  requirementsGrid.forEach((req) => {
    const match = /REQ-(\d+)/i.exec(req.id || '');
    if (match) {
      max = Math.max(max, parseInt(match[1], 10));
    }
  });
  const next = String(max + 1).padStart(3, '0');
  return `REQ-${next}`;
}

function addRequirementRow() {
  requirementsGrid.push({ id: nextRequirementId(), title: '', description: '' });
  renderRequirementsGrid();
}

function buildRequirementsBlock() {
  const lines = [REQ_BLOCK_START, '## Requirements Register'];
  let max = 0;
  requirementsGrid.forEach((req) => {
    const match = /REQ-(\d+)/i.exec(req.id || '');
    if (match) {
      max = Math.max(max, parseInt(match[1], 10));
    }
  });
  let counter = max + 1;
  requirementsGrid.forEach((req) => {
    const title = (req.title || '').trim() || 'Untitled requirement';
    let id = (req.id || '').trim().toUpperCase();
    if (!id) {
      id = `REQ-${String(counter).padStart(3, '0')}`;
      counter += 1;
    }
    lines.push(`- ${id}: ${title}`);
    if (req.description) {
      req.description.split(/\r?\n/).forEach((line) => {
        const cleaned = line.trim();
        if (cleaned) {
          lines.push(`  - ${cleaned}`);
        }
      });
    }
  });
  lines.push(REQ_BLOCK_END);
  return lines.join('\n');
}

function applyRequirementsToText() {
  if (!requirementsTextEl) return;
  if (!requirementsGrid.length) {
    showReqGridStatus('No requirements to apply.', true);
    return;
  }
  const block = buildRequirementsBlock();
  const current = requirementsTextEl.value || '';
  const start = current.indexOf(REQ_BLOCK_START);
  const end = current.indexOf(REQ_BLOCK_END);
  let nextText = '';
  if (start !== -1 && end !== -1 && end > start) {
    const before = current.slice(0, start).trimEnd();
    const after = current.slice(end + REQ_BLOCK_END.length).trimStart();
    nextText = `${before}\n\n${block}\n\n${after}`.trim() + '\n';
  } else {
    nextText = current.trimEnd();
    if (nextText) nextText += '\n\n';
    nextText += `${block}\n`;
  }
  requirementsTextEl.value = nextText;
  showReqGridStatus('Requirements applied to text.');
}

async function importRequirementsFile(file) {
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  showReqGridStatus('Importing requirements...');
  try {
    const res = await apiFetch('/api/requirements/import', {
      method: 'POST',
      body: formData,
    });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    const data = await res.json();
    if (!res.ok) {
      showReqGridStatus(data.details || data.error || 'Import failed.', true);
      return;
    }
    requirementsGrid = Array.isArray(data.items) ? data.items : [];
    renderRequirementsGrid();
    showReqGridStatus(`Imported ${requirementsGrid.length} requirement(s).`);
  } catch (err) {
    console.error(err);
    showReqGridStatus('Import failed. Check console.', true);
  }
}

async function exportRequirements(format) {
  if (!requirementsGrid.length) {
    showReqGridStatus('No requirements to export.', true);
    return;
  }
  const payload = { format, items: requirementsGrid };
  showReqGridStatus('Preparing export...');
  try {
    const res = await apiFetch('/api/requirements/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      showReqGridStatus(data.details || data.error || 'Export failed.', true);
      return;
    }
    const blob = await res.blob();
    const disposition = res.headers.get('content-disposition') || '';
    const match = /filename=\"?([^\";]+)\"?/i.exec(disposition);
    const filename = match ? match[1] : `requirements_register.${format}`;
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    showReqGridStatus('Export ready.');
  } catch (err) {
    console.error(err);
    showReqGridStatus('Export failed. Check console.', true);
  }
}

function extractRequirementsFromField(force = false) {
  if (!requirementsTextEl) return;
  const extracted = extractRequirementsFromText(requirementsTextEl.value);
  if (!extracted.length) {
    if (force) showReqGridStatus('No REQ items detected in the text.', true);
    return;
  }
  if (!requirementsGrid.length || force) {
    requirementsGrid = extracted;
    renderRequirementsGrid();
    showReqGridStatus(`Detected ${extracted.length} requirement(s).`);
  }
}

function mergeRequirementLists(existing, incoming) {
  const merged = (existing || []).map((req) => ({
    id: (req.id || '').trim().toUpperCase(),
    title: req.title || '',
    description: req.description || '',
  }));
  const used = new Set();
  let max = 0;
  merged.forEach((req) => {
    if (req.id) {
      used.add(req.id);
      const match = /REQ-(\d+)/i.exec(req.id);
      if (match) {
        max = Math.max(max, parseInt(match[1], 10));
      }
    }
  });
  let counter = max + 1;
  (incoming || []).forEach((req) => {
    if (!req) return;
    let id = String(req.id || '').trim().toUpperCase();
    if (!id || used.has(id)) {
      id = `REQ-${String(counter).padStart(3, '0')}`;
      counter += 1;
    }
    used.add(id);
    merged.push({
      id,
      title: req.title || '',
      description: req.description || '',
    });
  });
  return merged;
}

function requirementsFromSummaryItems(items) {
  if (!Array.isArray(items)) return [];
  let max = 0;
  items.forEach((item) => {
    const match = /REQ-(\d+)/i.exec(item?.id || '');
    if (match) {
      max = Math.max(max, parseInt(match[1], 10));
    }
  });
  let counter = max + 1;
  const mapped = [];
  items.forEach((item) => {
    const text = (item?.text || '').trim();
    if (!text) return;
    let title = text;
    let description = '';
    const splitIdx = text.indexOf('. ');
    if (splitIdx > 0 && splitIdx < 160) {
      title = text.slice(0, splitIdx).trim();
      description = text.slice(splitIdx + 2).trim();
    }
    let id = String(item?.id || '').trim().toUpperCase();
    if (!id) {
      id = `REQ-${String(counter).padStart(3, '0')}`;
      counter += 1;
    }
    mapped.push({ id, title, description });
  });
  return mapped;
}

async function loadRequirementSummary(jobId, force = false) {
  if (!jobId) return null;
  if (!force && requirementsSummaryCache.has(jobId)) {
    const cached = requirementsSummaryCache.get(jobId);
    if (cached?.data) return cached.data;
  }
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/requirements/summary`);
    if (!res.ok) return null;
    const data = await res.json();
    requirementsSummaryCache.set(jobId, { data, status: data.status || '', ts: Date.now() });
    return data;
  } catch (err) {
    return null;
  }
}

function showFormAssistantStatus(message, isError = false) {
  if (!formAssistantStatusEl) return;
  formAssistantStatusEl.textContent = message;
  formAssistantStatusEl.hidden = false;
  formAssistantStatusEl.classList.toggle('error', Boolean(isError));
}

function clearFormAssistantStatus() {
  if (!formAssistantStatusEl) return;
  formAssistantStatusEl.textContent = '';
  formAssistantStatusEl.hidden = true;
  formAssistantStatusEl.classList.remove('error');
}

function renderFormSuggestions() {
  if (!formAssistantListEl) return;
  formAssistantListEl.innerHTML = '';
  if (!formAssistantSuggestions.length) {
    formAssistantListEl.innerHTML = '<p class="subtitle">No suggestions yet.</p>';
    return;
  }
  formAssistantSuggestions.forEach((item, index) => {
    const card = document.createElement('div');
    card.className = 'suggestion-card';
    card.innerHTML = `
      <div class="label">${item.label || item.field_id}</div>
      <div class="value">${item.value === undefined ? '' : String(item.value)}</div>
      ${item.rationale ? `<div class="subtitle">${item.rationale}</div>` : ''}
      <div class="suggestion-actions">
        <button type="button" class="ghost" data-apply="${index}">Apply</button>
        <button type="button" class="ghost" data-remove="${index}">Remove</button>
      </div>
    `;
    card.querySelector('[data-apply]').addEventListener('click', () => {
      applySuggestion(item);
      formAssistantSuggestions.splice(index, 1);
      renderFormSuggestions();
    });
    card.querySelector('[data-remove]').addEventListener('click', () => {
      formAssistantSuggestions.splice(index, 1);
      renderFormSuggestions();
    });
    formAssistantListEl.appendChild(card);
  });
}

function collectAssistFields(scope, emptyOnly) {
  const fields = [];
  const form = document.getElementById('jobForm');
  if (!form) return fields;
  const elements = form.querySelectorAll('input, select, textarea');
  const excludedIds = new Set([
    'secretName',
    'secretValue',
    'jobSecretName',
    'jobSecretValue',
    'assistantInput',
    'assistantStatus',
    'assistantMessages',
    'formAssistantPrompt',
    'formAssistantStatus',
    'repoSearch',
  ]);
  const radioGroups = {};

  elements.forEach((el) => {
    if (!el.id && el.type !== 'radio') return;
    if (excludedIds.has(el.id)) return;
    if (el.type === 'password') return;
    if (el.closest('.assistant-panel')) return;
    if (el.closest('.secret-form')) return;

    if (el.type === 'radio') {
      const name = el.name;
      if (!name) return;
      if (!radioGroups[name]) {
        radioGroups[name] = { options: [], value: null, type: 'radio' };
      }
      radioGroups[name].options.push({ value: el.value, label: el.parentElement?.textContent?.trim() || el.value });
      if (el.checked) radioGroups[name].value = el.value;
      return;
    }

    const labelEl = form.querySelector(`label[for="${el.id}"]`);
    const label = labelEl ? labelEl.textContent.trim() : el.id;
    const tooltip = labelEl?.dataset?.tooltip;
    const type = el.type || el.tagName.toLowerCase();
    let value;
    if (type === 'checkbox') {
      value = Boolean(el.checked);
      if (emptyOnly && value) return;
    } else {
      value = el.value;
      if (emptyOnly && value) return;
    }

    const panel = el.closest('.tab-panel');
    const panelKey = panel?.dataset?.tab;
    const workflowSection = el.closest('.workflow-section');
    const workflow = workflowSection?.dataset?.workflow;

    if (scope === 'workflow') {
      const selected = workflowSelect.value;
      if (workflow && workflow !== selected) return;
      if (panelKey && panelKey !== 'job') return;
    }
    if (scope === 'job' && panelKey !== 'job') return;
    if (scope === 'global' && panelKey !== 'global') return;

    fields.push({
      id: el.id,
      label,
      description: tooltip,
      type,
      value,
      options: el.tagName.toLowerCase() === 'select' ? Array.from(el.options).map((o) => o.value) : undefined,
    });
  });

  Object.keys(radioGroups).forEach((name) => {
    const panel = form.querySelector(`input[name="${name}"]`)?.closest('.tab-panel');
    const panelKey = panel?.dataset?.tab;
    if (scope === 'job' && panelKey !== 'job') return;
    if (scope === 'global' && panelKey !== 'global') return;
    fields.push({
      id: name,
      label: name,
      type: 'radio',
      value: radioGroups[name].value,
      options: radioGroups[name].options.map((opt) => opt.value),
      description: 'Select one option from the list.',
    });
  });

  return fields;
}

async function requestFormSuggestions() {
  clearFormAssistantStatus();
  const scope = formAssistantScopeEl?.value || 'workflow';
  const emptyOnly = formAssistantEmptyOnlyEl?.checked ?? true;
  const fields = collectAssistFields(scope, emptyOnly);
  if (!fields.length) {
    showFormAssistantStatus('No fields available for this scope.', true);
    return;
  }
  const payload = {
    prompt: formAssistantPromptEl?.value.trim() || '',
    scope,
    workflow: workflowSelect.value,
    fields,
    provider: document.getElementById('llmProvider')?.value || undefined,
    model: document.getElementById('llmModel')?.value.trim() || undefined,
    temperature: parseFloat(document.getElementById('llmTemperature')?.value || '0.2'),
    max_tokens: parseInt(document.getElementById('llmMaxTokens')?.value || '', 10),
  };
  if (Number.isNaN(payload.temperature)) delete payload.temperature;
  if (Number.isNaN(payload.max_tokens)) delete payload.max_tokens;

  showFormAssistantStatus('Requesting suggestions...');
  try {
    const res = await apiFetch('/api/assistant/form-fill', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    const data = await res.json();
    if (!res.ok) {
      showFormAssistantStatus(data.details || data.error || 'Suggestion failed.', true);
      return;
    }
    formAssistantSuggestions = data.suggestions || [];
    formAssistantSuggestions = formAssistantSuggestions.map((item) => {
      const field = fields.find((f) => f.id === item.field_id);
      return {
        ...item,
        label: field?.label || item.field_id,
      };
    });
    renderFormSuggestions();
    showFormAssistantStatus('Suggestions ready.');
  } catch (err) {
    console.error(err);
    showFormAssistantStatus('Suggestion request failed.', true);
  }
}

function applySuggestion(suggestion) {
  const fieldId = suggestion.field_id;
  if (!fieldId) return;
  if (fieldId === 'projectSource' || fieldId === 'deliverySource') {
    const radios = document.querySelectorAll(`input[name="${fieldId}"]`);
    radios.forEach((radio) => {
      radio.checked = String(radio.value) === String(suggestion.value);
    });
    updateSourcePanels(fieldId === 'projectSource' ? 'project' : 'delivery');
    persistFormState();
    return;
  }
  const el = document.getElementById(fieldId);
  if (!el) return;
  if (el.type === 'checkbox') {
    el.checked = Boolean(suggestion.value);
  } else if (el.tagName.toLowerCase() === 'select') {
    el.value = suggestion.value;
  } else {
    el.value = suggestion.value == null ? '' : suggestion.value;
  }
  updateWorkflowSections();
  persistFormState();
}

function applyAllSuggestions() {
  formAssistantSuggestions.forEach((item) => applySuggestion(item));
  formAssistantSuggestions = [];
  renderFormSuggestions();
}

function clearFormSuggestions() {
  formAssistantSuggestions = [];
  renderFormSuggestions();
  clearFormAssistantStatus();
}

function setRepoBrowserOpen(isOpen) {
  if (!repoBrowserEl) return;
  repoBrowserEl.hidden = !isOpen;
  repoBrowserEl.dataset.open = isOpen ? 'true' : 'false';
  repoBrowserEl.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
}

let transferModalState = null;

function setTransferModalOpen(isOpen) {
  if (!transferModalEl) return;
  transferModalEl.hidden = !isOpen;
  transferModalEl.dataset.open = isOpen ? 'true' : 'false';
  transferModalEl.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
}

function clearTransferModalStatus() {
  if (!transferModalStatusEl) return;
  transferModalStatusEl.hidden = true;
  transferModalStatusEl.textContent = '';
  transferModalStatusEl.classList.remove('error');
}

function showTransferModalStatus(message, isError = false) {
  if (!transferModalStatusEl) return;
  transferModalStatusEl.textContent = message;
  transferModalStatusEl.hidden = false;
  transferModalStatusEl.classList.toggle('error', Boolean(isError));
}

function resetTransferModal() {
  if (transferTeamSelectEl) transferTeamSelectEl.innerHTML = '';
  if (transferProjectSelectEl) transferProjectSelectEl.innerHTML = '';
  if (transferUserSelectEl) transferUserSelectEl.innerHTML = '';
  if (transferTeamSelectEl) transferTeamSelectEl.disabled = false;
  clearTransferModalStatus();
}

function closeTransferModal() {
  setTransferModalOpen(false);
  transferModalState = null;
  resetTransferModal();
}

function populateSelect(selectEl, options, placeholder = null) {
  if (!selectEl) return;
  selectEl.innerHTML = '';
  if (placeholder !== null) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = placeholder;
    selectEl.appendChild(opt);
  }
  (options || []).forEach((item) => {
    const opt = document.createElement('option');
    opt.value = item.value;
    opt.textContent = item.label;
    selectEl.appendChild(opt);
  });
}

function openTransferModal(mode, job) {
  if (!transferModalEl || !job) return;
  resetTransferModal();
  transferModalState = { mode, job };
  const transfer = job.transfer_request || {};
  const teamId = transfer.team_id || job.team_id;

  if (transferTeamRowEl) transferTeamRowEl.hidden = true;
  if (transferProjectRowEl) transferProjectRowEl.hidden = true;
  if (transferUserRowEl) transferUserRowEl.hidden = true;
  if (transferModalConfirmBtn) transferModalConfirmBtn.disabled = false;

  if (mode === 'invite') {
    if (transferModalTitleEl) transferModalTitleEl.textContent = 'Invite Team Leader';
    if (transferModalDescEl) transferModalDescEl.textContent = 'Select a team to invite its leader(s) to take over this job.';
    if (transferTeamRowEl) transferTeamRowEl.hidden = false;
    const eligible = accessTeams.filter((team) => isTeamMember(team.id));
    const options = eligible.map((team) => ({ value: team.id, label: team.name }));
    populateSelect(transferTeamSelectEl, options, 'Select a team');
    if (!options.length && transferModalConfirmBtn) {
      transferModalConfirmBtn.disabled = true;
      showTransferModalStatus('No team memberships available for transfer.', true);
    }
  } else if (mode === 'accept') {
    if (transferModalTitleEl) transferModalTitleEl.textContent = 'Accept Team Transfer';
    if (transferModalDescEl) transferModalDescEl.textContent = `Assign this job to ${teamName(teamId)} and optionally attach it to a team project.`;
    if (transferTeamRowEl) transferTeamRowEl.hidden = false;
    populateSelect(transferTeamSelectEl, [{ value: teamId, label: teamName(teamId) }]);
    if (transferTeamSelectEl) transferTeamSelectEl.disabled = true;
    if (transferProjectRowEl) transferProjectRowEl.hidden = false;
    const teamProjects = projectOptions.filter((project) => project.team_id === teamId);
    const projectOptionsList = teamProjects.map((project) => ({ value: project.id, label: project.name }));
    populateSelect(transferProjectSelectEl, projectOptionsList, 'No project');
  } else if (mode === 'assign') {
    if (transferModalTitleEl) transferModalTitleEl.textContent = 'Assign To Individual';
    if (transferModalDescEl) transferModalDescEl.textContent = `Move this team job into an individual queue for ${teamName(teamId)}.`;
    if (transferTeamRowEl) transferTeamRowEl.hidden = false;
    populateSelect(transferTeamSelectEl, [{ value: teamId, label: teamName(teamId) }]);
    if (transferTeamSelectEl) transferTeamSelectEl.disabled = true;
    if (transferUserRowEl) transferUserRowEl.hidden = false;
    const team = accessTeamIndex[teamId];
    const candidates = team
      ? [...new Set([...(team.leaders || []), ...(team.members || [])])]
      : [];
    const userOptions = candidates.map((user) => ({ value: user, label: user }));
    populateSelect(transferUserSelectEl, userOptions, 'Select a user');
    if (!userOptions.length && transferModalConfirmBtn) {
      transferModalConfirmBtn.disabled = true;
      showTransferModalStatus('No team members available for assignment.', true);
    }
  }

  setTransferModalOpen(true);
}

async function openRepoBrowser(targetId, mode, context) {
  const selectedSource = context === 'delivery' ? getSelectedSource('delivery') : getSelectedSource('project');
  if (selectedSource !== 'github') {
    showJobStatus('Switch Project Source to GitHub to browse repo paths.', true);
    closeRepoBrowser();
    return;
  }
  const repoUrl = context === 'delivery' ? deliveryRepoUrlEl?.value.trim() : repoUrlEl?.value.trim();
  const repoBranch = context === 'delivery' ? deliveryRepoBranchEl?.value.trim() : repoBranchEl?.value.trim();
  if (!repoUrl) {
    showJobStatus('Enter a GitHub repo first to browse paths.', true);
    closeRepoBrowser();
    return;
  }
  repoBrowserState = { targetId, mode, context, items: [] };
  setRepoBrowserOpen(true);
  if (repoSearchEl) repoSearchEl.value = '';
  repoBrowserListEl.innerHTML = '';
  clearRepoStatus();
  showRepoStatus('Loading repo tree...');
  try {
    const res = await apiFetch('/api/github/tree', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_url: repoUrl, branch: repoBranch }),
    });
    if (!res.ok) {
      const data = await res.json();
      showRepoStatus(data.error || 'Repo lookup failed.', true);
      return;
    }
    const data = await res.json();
    repoBrowserState.items = data.items || [];
    renderRepoList();
    showRepoStatus(`Loaded ${repoBrowserState.items.length} items from ${data.owner}/${data.repo}@${data.branch}.`);
  } catch (err) {
    showRepoStatus('Repo lookup failed. Check console for details.', true);
    console.error(err);
  }
}

function renderRepoList() {
  const query = repoSearchEl?.value.trim().toLowerCase() || '';
  const filtered = repoBrowserState.items.filter((item) => {
    if (repoBrowserState.mode === 'file' && item.type !== 'blob') return false;
    if (repoBrowserState.mode === 'dir' && item.type !== 'tree') return false;
    if (!query) return true;
    return item.path.toLowerCase().includes(query);
  });
  repoBrowserListEl.innerHTML = '';
  if (!filtered.length) {
    repoBrowserListEl.innerHTML = '<p class="subtitle">No matching paths.</p>';
    return;
  }
  filtered.slice(0, 800).forEach((item) => {
    const row = document.createElement('div');
    row.className = 'repo-item';
    row.innerHTML = `<div>${item.path}</div><small>${item.type === 'tree' ? 'dir' : 'file'}</small>`;
    row.addEventListener('click', () => {
      const target = document.getElementById(repoBrowserState.targetId);
      if (target) {
        target.value = item.path;
      }
      closeRepoBrowser();
    });
    repoBrowserListEl.appendChild(row);
  });
}

function closeRepoBrowser() {
  setRepoBrowserOpen(false);
  repoBrowserState = { items: [], targetId: null, mode: 'file', context: 'project' };
  if (repoSearchEl) repoSearchEl.value = '';
}

function updateWorkflowSections() {
  const selected = workflowSelect.value;
  document.querySelectorAll('.workflow-section').forEach((section) => {
    const workflow = section.dataset.workflow;
    section.hidden = workflow !== selected;
  });
  updateSourcePanels('project');
  updateSourcePanels('delivery');
}

function getSelectedSource(groupName) {
  const inputs = groupName === 'project' ? projectSourceInputs : deliverySourceInputs;
  for (const input of inputs) {
    if (input.checked) return input.value;
  }
  return 'local';
}

function updateSourcePanels(groupName) {
  const selected = getSelectedSource(groupName);
  sourcePanels.forEach((panel) => {
    if (panel.dataset.sourceGroup !== groupName) return;
    panel.hidden = panel.dataset.source !== selected;
  });
  if (selected !== 'github') {
    closeRepoBrowser();
  }
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '--';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${r}s`;
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

function formatAbsoluteTime(value) {
  const ts = parseTimestamp(value);
  if (!ts) return value || '--';
  return formatAbsoluteTimeFromMs(ts);
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

function flattenTeams(nodes, acc = []) {
  if (!Array.isArray(nodes)) return acc;
  nodes.forEach((node) => {
    if (!node || !node.id) return;
    acc.push(node);
    if (Array.isArray(node.children) && node.children.length) {
      flattenTeams(node.children, acc);
    }
  });
  return acc;
}

function buildTeamIndex(tree) {
  const list = flattenTeams(tree || [], []);
  const index = {};
  list.forEach((team) => {
    index[team.id] = team;
  });
  return { list, index };
}

function isTeamLeader(teamId) {
  if (!teamId) return false;
  if (currentProfile?.role === 'admin') return true;
  const team = accessTeamIndex[teamId];
  if (!team || !currentProfile?.user) return false;
  return Array.isArray(team.leaders) && team.leaders.includes(currentProfile.user);
}

function isTeamMember(teamId) {
  if (!teamId) return false;
  if (currentProfile?.role === 'admin') return true;
  const team = accessTeamIndex[teamId];
  if (!team || !currentProfile?.user) return false;
  const leaders = Array.isArray(team.leaders) ? team.leaders : [];
  const members = Array.isArray(team.members) ? team.members : [];
  return leaders.includes(currentProfile.user) || members.includes(currentProfile.user);
}

function teamName(teamId) {
  const team = accessTeamIndex[teamId];
  return team?.name || teamId || '--';
}

function computeRuntimeSeconds(startedAt, runtimeSec) {
  const base = Number.isFinite(runtimeSec) ? runtimeSec : null;
  const startMs = parseTimestamp(startedAt);
  if (!startMs) return base;
  const nowMs = Date.now();
  const computed = Math.max(0, (nowMs - startMs) / 1000);
  return computed;
}

function clearDurationTimer() {
  if (durationTimer) {
    clearInterval(durationTimer);
    durationTimer = null;
  }
}

function updateDurationValue() {
  const durationEl = document.getElementById('jobDurationValue');
  if (!durationEl) return;
  const status = durationEl.dataset.status || '';
  if (status !== 'running') return;
  const startedAt = durationEl.dataset.startedAt || '';
  const runtimeSec = parseFloat(durationEl.dataset.runtimeSec || '');
  const next = computeRuntimeSeconds(startedAt, Number.isNaN(runtimeSec) ? null : runtimeSec);
  durationEl.textContent = formatDuration(next);
}

function scheduleDurationUpdates() {
  clearDurationTimer();
  const durationEl = document.getElementById('jobDurationValue');
  if (!durationEl) return;
  if ((durationEl.dataset.status || '') !== 'running') return;
  updateDurationValue();
  durationTimer = setInterval(updateDurationValue, 1000);
}

function clearReqProgressTimer() {
  if (reqProgressTimer) {
    clearInterval(reqProgressTimer);
    reqProgressTimer = null;
  }
}

function updateRequirementProgressUI(data) {
  const progressEl = document.getElementById('reqProgress');
  if (!progressEl) return;
  const summaryEl = document.getElementById('reqProgressSummary');
  const statusEl = document.getElementById('reqProgressStatus');
  const completedEl = document.getElementById('reqCompleted');
  const inProgressEl = document.getElementById('reqInProgress');
  const remainingEl = document.getElementById('reqRemaining');
  const completedLabelEl = document.getElementById('reqCompletedLabel');
  const inProgressLabelEl = document.getElementById('reqInProgressLabel');
  const remainingLabelEl = document.getElementById('reqRemainingLabel');

  const total = Number(data?.total ?? 0);
  const completed = Number(data?.completed ?? 0);
  const inProgress = Number(data?.in_progress ?? 0);
  const remaining = Number(data?.remaining ?? Math.max(total - completed - inProgress, 0));

  if (summaryEl) {
    summaryEl.textContent = total > 0 ? `Total: ${total}` : '--';
  }

  if (completedLabelEl) completedLabelEl.textContent = `Completed: ${completed}`;
  if (inProgressLabelEl) inProgressLabelEl.textContent = `In progress: ${inProgress}`;
  if (remainingLabelEl) remainingLabelEl.textContent = `Remaining: ${remaining}`;

  if (total > 0) {
    const completedPct = Math.max(0, (completed / total) * 100);
    const inProgressPct = Math.max(0, (inProgress / total) * 100);
    const remainingPct = Math.max(0, 100 - completedPct - inProgressPct);
    if (completedEl) completedEl.style.width = `${completedPct}%`;
    if (inProgressEl) inProgressEl.style.width = `${inProgressPct}%`;
    if (remainingEl) remainingEl.style.width = `${remainingPct}%`;
  } else {
    if (completedEl) completedEl.style.width = '0%';
    if (inProgressEl) inProgressEl.style.width = '0%';
    if (remainingEl) remainingEl.style.width = '100%';
  }

  const message = data?.message || '';
  if (statusEl) {
    if (message) {
      statusEl.textContent = message;
      statusEl.hidden = false;
    } else {
      statusEl.textContent = '';
      statusEl.hidden = true;
    }
  }
}

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value ?? '';
  return div.innerHTML;
}

function formatRelativeTime(value) {
  if (!value) return '';
  const ts = parseTimestamp(value);
  if (!ts) return value;
  const diffSec = Math.floor((Date.now() - ts) / 1000);
  if (diffSec < 10) return 'just now';
  if (diffSec < 60) return `${diffSec} seconds ago`;
  const mins = Math.floor(diffSec / 60);
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? '' : 's'} ago`;
  return formatAbsoluteTimeFromMs(ts);
}

function updateRequirementSummaryUI(data) {
  const summaryEl = document.getElementById('reqSummaryLead');
  const listEl = document.getElementById('reqSummaryList');
  const metaEl = document.getElementById('reqSummaryMeta');
  const statusEl = document.getElementById('reqSummaryStatus');
  const redactionEl = document.getElementById('reqSummaryRedaction');
  if (!listEl) return;
  const items = Array.isArray(data?.items) ? data.items : [];
  const summaryText = data?.summary || '';
  const updatedAt = data?.updated_at;
  const total = Number(data?.total ?? items.length);
  const source = data?.source || 'none';
  const redacted = Boolean(data?.redacted);
  if (summaryEl) {
    summaryEl.textContent = summaryText || 'No requirements summary available yet.';
  }
  if (redactionEl) {
    redactionEl.hidden = !redacted;
  }
  if (metaEl) {
    const timeLabel = updatedAt ? `Updated ${formatRelativeTime(updatedAt)}` : '';
    const parts = [timeLabel, total ? `${total} requirement${total === 1 ? '' : 's'}` : '', source && source !== 'none' ? source : '']
      .filter(Boolean);
    metaEl.textContent = parts.length ? parts.join(' • ') : '--';
  }
  if (!items.length) {
    listEl.innerHTML = '<p class="subtitle">No requirement steps available.</p>';
  } else {
    listEl.innerHTML = items
      .map((item, idx) => {
        const id = item?.id ? `<span class="req-summary-id">${escapeHtml(item.id)}</span>` : '';
        const text = escapeHtml(item?.text || '');
        return `
          <div class="req-summary-item">
            <span class="req-summary-index">${idx + 1}</span>
            <div class="req-summary-text">${id}${text}</div>
          </div>
        `;
      })
      .join('');
  }
  if (statusEl) {
    const message = data?.message || '';
    if (message) {
      statusEl.textContent = message;
      statusEl.hidden = false;
    } else {
      statusEl.textContent = '';
      statusEl.hidden = true;
    }
  }
}

async function fetchRequirementSummary(jobId, jobStatus = '', force = false) {
  if (!jobId) return;
  if (!force && requirementsSummaryCache.has(jobId)) {
    const cached = requirementsSummaryCache.get(jobId);
    if (cached?.data) updateRequirementSummaryUI(cached.data);
    return;
  }
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/requirements/summary`);
    if (!res.ok) {
      updateRequirementSummaryUI({ message: 'Requirements summary unavailable.' });
      return;
    }
    const data = await res.json();
    requirementsSummaryCache.set(jobId, { data, status: jobStatus, ts: Date.now() });
    if (selectedJobId && selectedJobId !== jobId) return;
    updateRequirementSummaryUI(data);
  } catch (err) {
    console.error(err);
    updateRequirementSummaryUI({ message: 'Requirements summary unavailable.' });
  }
}

function scheduleRequirementSummary(job) {
  if (!job) return;
  const cached = requirementsSummaryCache.get(job.id);
  if (cached?.data) {
    updateRequirementSummaryUI(cached.data);
  }
  const shouldRefresh = !cached || cached.status !== job.status || ['running', 'paused'].includes(job.status);
  if (shouldRefresh) {
    fetchRequirementSummary(job.id, job.status, true);
  }
}

async function fetchRequirementProgress(jobId, jobStatus = '') {
  const res = await apiFetch(`/api/jobs/${jobId}/requirements/progress`);
  if (!res.ok) {
    updateRequirementProgressUI({ message: 'Requirements progress unavailable.' });
    return;
  }
  const data = await res.json();
  requirementsProgressCache.set(jobId, { data, status: jobStatus, ts: Date.now() });
  if (selectedJobId && selectedJobId !== jobId) return;
  updateRequirementProgressUI(data);
}

function scheduleRequirementProgressUpdates(job) {
  clearReqProgressTimer();
  if (!job) return;
  const cached = requirementsProgressCache.get(job.id);
  if (cached?.data) {
    updateRequirementProgressUI(cached.data);
  }
  const isActive = ['running', 'paused'].includes(job.status);
  const needsRefresh = !cached || cached.status !== job.status;
  if (isActive || needsRefresh) {
    fetchRequirementProgress(job.id, job.status);
  }
  if (isActive) {
    reqProgressTimer = setInterval(() => {
      if (selectedJobId !== job.id) {
        clearReqProgressTimer();
        return;
      }
      fetchRequirementProgress(job.id, job.status);
    }, 4000);
  }
}

function formatTokens(metrics) {
  if (!metrics || !metrics.token_usage) return '--';
  const total = metrics.token_usage.total;
  return total ? total.toString() : '--';
}

function formatAmount(value) {
  if (value === null || value === undefined || value === '') return '--';
  const num = Number(value);
  if (Number.isNaN(num)) return '--';
  return Math.round(num).toString();
}

function getLatestRefund(job) {
  const refunds = Array.isArray(job?.refunds) ? job.refunds : [];
  if (!refunds.length) return null;
  return refunds
    .slice()
    .sort((a, b) => (parseTimestamp(a.requested_at) || 0) - (parseTimestamp(b.requested_at) || 0))
    .pop();
}

function showRefundStatus(message, isError = false) {
  const statusEl = document.getElementById('refundStatus');
  if (!statusEl) return;
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.toggle('error', Boolean(isError));
}

function clearRefundStatus() {
  const statusEl = document.getElementById('refundStatus');
  if (!statusEl) return;
  statusEl.textContent = '';
  statusEl.hidden = true;
  statusEl.classList.remove('error');
}

function updateRefundFilesList(files) {
  const listEl = document.getElementById('refundFiles');
  if (!listEl) return;
  if (!files || !files.length) {
    listEl.innerHTML = '<span class="subtitle">No screenshots selected.</span>';
    return;
  }
  listEl.innerHTML = Array.from(files)
    .map((file) => `<span class="badge">${file.name}</span>`)
    .join('');
}

function getRefundDraft(jobId) {
  if (!jobId) return null;
  return refundDrafts.get(jobId) || null;
}

function setRefundDraft(jobId, draft) {
  if (!jobId) return;
  refundDrafts.set(jobId, {
    amount: draft?.amount ?? '',
    reason: draft?.reason ?? '',
    details: draft?.details ?? '',
  });
}

function restoreRefundDraft(jobId, fields) {
  const draft = getRefundDraft(jobId);
  if (!draft) return;
  if (fields.amount && draft.amount !== undefined) fields.amount.value = draft.amount;
  if (fields.reason && draft.reason !== undefined) fields.reason.value = draft.reason;
  if (fields.details && draft.details !== undefined) fields.details.value = draft.details;
}

function persistRefundDraft(jobId, fields) {
  setRefundDraft(jobId, {
    amount: fields.amount ? fields.amount.value : '',
    reason: fields.reason ? fields.reason.value : '',
    details: fields.details ? fields.details.value : '',
  });
}

async function submitRefundRequest(jobId) {
  const amountEl = document.getElementById('refundAmount');
  const reasonEl = document.getElementById('refundReason');
  const detailsEl = document.getElementById('refundDetails');
  const filesEl = document.getElementById('refundScreenshots');
  if (!amountEl || !reasonEl || !filesEl) return;
  const amountRaw = amountEl.value.trim();
  const amount = parseFloat(amountRaw.replace(/[^\d.]/g, ''));
  const reason = reasonEl.value.trim();
  const details = detailsEl ? detailsEl.value.trim() : '';
  if (!Number.isFinite(amount) || amount <= 0) {
    showRefundStatus('Enter a valid refund amount.', true);
    return;
  }
  if (!reason) {
    showRefundStatus('Provide a reason for the refund request.', true);
    return;
  }
  const files = filesEl.files;
  if (!files || !files.length) {
    showRefundStatus('Attach at least one screenshot.', true);
    return;
  }
  const formData = new FormData();
  formData.append('amount', String(Math.round(amount)));
  formData.append('reason', reason);
  formData.append('details', details);
  Array.from(files).forEach((file) => {
    formData.append('screenshots', file);
  });
  showRefundStatus('Submitting refund request...');
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/refunds`, {
      method: 'POST',
      body: formData,
    });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    const data = await res.json();
    if (!res.ok) {
      showRefundStatus(data.details || data.error || 'Refund request failed.', true);
      return;
    }
    showRefundStatus('Refund request submitted.');
    refundDrafts.delete(jobId);
    await refreshSelectedJob({ preserveEditor: true });
  } catch (err) {
    console.error(err);
    showRefundStatus('Refund request failed. Check console.', true);
  }
}

const ACTIVE_JOB_STATUSES = new Set(['queued', 'running', 'paused']);

function isJobActive(job) {
  return ACTIVE_JOB_STATUSES.has(job.status);
}

function getQueueJobs() {
  return jobs.filter((job) => !job.archived);
}

function getArchiveJobs() {
  return jobs.filter((job) => Boolean(job.archived));
}

function updateBulkButtons() {
  if (!deleteQueueBtn || !deleteArchiveBtn) return;
  const queueJobs = getQueueJobs();
  const archiveJobs = getArchiveJobs();
  deleteQueueBtn.disabled = queueJobs.length === 0;
  deleteArchiveBtn.disabled = archiveJobs.length === 0;
}

async function toggleArchive(job) {
  if (!job) return;
  const nextArchived = !job.archived;
  if (nextArchived) {
    const active = isJobActive(job);
    const warning = active
      ? 'This will stop the job and move it into the archive. Continue?'
      : 'Move this job into the archive?';
    if (!window.confirm(warning)) return;
  } else {
    if (!window.confirm('Move this job back into the queue?')) return;
  }
  try {
    const res = await apiFetch(`/api/jobs/${job.id}/archive`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ archived: nextArchived, stop: nextArchived && isJobActive(job) }),
    });
    if (!res.ok) {
      const data = await res.json();
      showJobStatus(data.details || data.error || 'Archive update failed.', true);
      return;
    }
    await fetchJobs();
  } catch (err) {
    console.error(err);
    showJobStatus('Archive update failed. Check console.', true);
  }
}

async function deleteJob(job) {
  if (!job) return;
  const active = isJobActive(job);
  const warning = active
    ? 'This will stop the job and permanently delete it. Continue?'
    : 'Permanently delete this job? This cannot be undone.';
  if (!window.confirm(warning)) return;
  try {
    const res = await apiFetch(`/api/jobs/${job.id}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stop: active }),
    });
    if (!res.ok) {
      const data = await res.json();
      showJobStatus(data.details || data.error || 'Delete failed.', true);
      return;
    }
    await fetchJobs();
  } catch (err) {
    console.error(err);
    showJobStatus('Delete failed. Check console.', true);
  }
}

async function deleteJobsBulk(scope) {
  if (!scope) return;
  const isArchiveScope = scope === 'archive';
  const targetJobs = isArchiveScope ? getArchiveJobs() : getQueueJobs();
  if (!targetJobs.length) return;
  const activeCount = targetJobs.filter(isJobActive).length;
  const scopeLabel = isArchiveScope ? 'archive' : 'queue';
  const warning = activeCount
    ? `This will stop ${activeCount} in-progress job(s) and permanently delete ${targetJobs.length} job(s) from the ${scopeLabel}. Continue?`
    : `Permanently delete ${targetJobs.length} job(s) from the ${scopeLabel}? This cannot be undone.`;
  if (!window.confirm(warning)) return;
  try {
    const res = await apiFetch('/api/jobs/bulk-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: scopeLabel, stop: activeCount > 0 }),
    });
    if (!res.ok) {
      const data = await res.json();
      showJobStatus(data.details || data.error || 'Bulk delete failed.', true);
      return;
    }
    await fetchJobs();
  } catch (err) {
    console.error(err);
    showJobStatus('Bulk delete failed. Check console.', true);
  }
}

function renderJobs() {
  const visible = jobs.filter((job) => {
    if (currentFilter === 'archive') {
      return Boolean(job.archived);
    }
    if (job.archived) {
      return false;
    }
    return currentFilter === 'all' || job.status === currentFilter;
  });
  jobListEl.innerHTML = '';
  if (!visible.length) {
    const emptyMessage = currentFilter === 'archive'
      ? 'No archived jobs yet.'
      : 'No jobs found for this status.';
    jobListEl.innerHTML = `<p class="subtitle">${emptyMessage}</p>`;
    return;
  }
  visible.forEach((job) => {
    const card = document.createElement('div');
    card.className = `job-card ${job.id === selectedJobId ? 'active' : ''}`;
    card.addEventListener('click', () => selectJob(job.id));

    const badge = document.createElement('span');
    badge.className = `badge ${job.status}`;
    badge.textContent = job.status;

    const progress = document.createElement('div');
    progress.className = 'progress';
    const progressInner = document.createElement('span');
    progressInner.style.width = `${job.progress || 0}%`;
    progress.appendChild(progressInner);

    card.innerHTML = `
      <div class="job-head">
        <div>
          <strong>${job.workflow}</strong>
          <div class="job-id">${job.project_name || 'Untitled'}</div>
          <div class="job-id">Owner: ${job.owner || '--'}${job.team_name ? ` · Team: ${job.team_name}` : ''}</div>
          <div class="job-id">${job.id.slice(0, 8)}</div>
        </div>
      </div>
    `;
    card.querySelector('.job-head').appendChild(badge);
    card.appendChild(progress);

    const metrics = document.createElement('div');
    metrics.className = 'job-metrics';
    metrics.innerHTML = `
      <span>Tokens: ${formatTokens(job.metrics)}</span>
      <span>Errors: ${job.metrics?.errors ?? 0}</span>
      <span>Resolved: ${job.metrics?.resolved ?? 0}</span>
    `;
    card.appendChild(metrics);

    const actions = document.createElement('div');
    actions.className = 'job-actions';
    const canManageJob = Boolean(job.project_capabilities?.write);
    const archiveBtn = document.createElement('button');
    archiveBtn.type = 'button';
    archiveBtn.className = 'ghost';
    archiveBtn.textContent = job.archived ? 'Unarchive' : 'Archive';
    archiveBtn.disabled = !canManageJob;
    archiveBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      toggleArchive(job);
    });
    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'ghost danger';
    deleteBtn.textContent = 'Delete';
    deleteBtn.disabled = !canManageJob;
    deleteBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      deleteJob(job);
    });
    actions.appendChild(archiveBtn);
    actions.appendChild(deleteBtn);
    card.appendChild(actions);

    jobListEl.appendChild(card);
  });
}

function updateScopeFilters() {
  if (!scopeFiltersEl) return;
  scopeFiltersEl.querySelectorAll('button[data-scope]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.scope === currentScope);
  });
}

function initScopeFilters() {
  if (!scopeFiltersEl) return;
  scopeFiltersEl.querySelectorAll('button[data-scope]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const scope = btn.dataset.scope;
      if (!scope || scope === currentScope) return;
      currentScope = scope;
      updateScopeFilters();
      fetchJobs();
    });
  });
  updateScopeFilters();
}

function updateQueueSummary() {
  if (!queueQueuedCountEl) return;
  const counts = { queued: 0, running: 0, failed: 0, completed: 0 };
  jobs.forEach((job) => {
    if (job.archived) return;
    switch (job.status) {
      case 'queued':
        counts.queued += 1;
        break;
      case 'running':
        counts.running += 1;
        break;
      case 'completed':
        counts.completed += 1;
        break;
      case 'failed':
      case 'stopped':
        counts.failed += 1;
        break;
      default:
        break;
    }
  });
  queueQueuedCountEl.textContent = counts.queued;
  if (queueRunningCountEl) queueRunningCountEl.textContent = counts.running;
  if (queueFailedCountEl) queueFailedCountEl.textContent = counts.failed;
  if (queueCompletedCountEl) queueCompletedCountEl.textContent = counts.completed;
}

function scheduleJobsPoll(delayMs) {
  if (jobsPollTimer) {
    clearTimeout(jobsPollTimer);
    jobsPollTimer = null;
  }
  if (!delayMs && delayMs !== 0) return;
  jobsPollTimer = setTimeout(() => {
    fetchJobs();
  }, delayMs);
}

async function fetchJobs() {
  try {
    const res = await apiFetch(`/api/jobs?scope=${encodeURIComponent(currentScope)}`);
    const data = await res.json();
    const fetchedJobs = data.jobs || [];
    if (hasLoadedJobs && !document.hidden) {
      fetchedJobs.forEach((job) => {
        const prevStatus = jobStatusCache[job.id];
        if (prevStatus && prevStatus !== job.status && (job.status === 'completed' || job.status === 'failed')) {
          showJobToast(job);
        }
      });
    }
    jobStatusCache = {};
    fetchedJobs.forEach((job) => {
      jobStatusCache[job.id] = job.status;
    });
    hasLoadedJobs = true;
    jobs = fetchedJobs;
    jobCountEl.textContent = jobs.length;
    updateQueueSummary();
    renderJobs();
    updateBulkButtons();
    if (pendingSelectJobId) {
      const match = jobs.find((job) => job.id === pendingSelectJobId);
      if (match && selectedJobId !== pendingSelectJobId) {
        const targetId = pendingSelectJobId;
        pendingSelectJobId = null;
        selectJob(targetId).catch((err) => console.error('Failed to select job', err));
      }
    }
  if (selectedJobId) {
    const stillExists = jobs.find((job) => job.id === selectedJobId);
    if (!stillExists) {
      selectedJobId = null;
      renderJobDetail(null);
    } else {
      refreshSelectedJob({ preserveEditor: true });
    }
  }
    const hasActiveJobs = jobs.some((job) => ['queued', 'running', 'paused'].includes(job.status));
    if (hasActiveJobs) {
      scheduleJobsPoll(4000);
    } else {
      scheduleJobsPoll(null);
    }
  } catch (err) {
    console.error('Failed to fetch jobs', err);
  }
}

async function fetchProjects() {
  if (!projectIdEl) return;
  try {
    const res = await apiFetch('/api/projects');
    if (!res.ok) return;
    const data = await res.json();
    projectOptions = data.projects || [];
    projectIdEl.innerHTML = '<option value="">Personal / Unassigned</option>';
    projectOptions.forEach((project) => {
      const option = document.createElement('option');
      option.value = project.id;
      const teamName = project.team_name ? ` · ${project.team_name}` : '';
      option.textContent = `${project.name}${teamName}`;
      option.dataset.role = project.role || '';
      option.dataset.capRead = String(Boolean(project.capabilities?.read));
      option.dataset.capWrite = String(Boolean(project.capabilities?.write));
      option.dataset.capGrant = String(Boolean(project.capabilities?.grant));
      projectIdEl.appendChild(option);
    });
    updateProjectRoleHint();
  } catch (err) {
    console.error('Failed to fetch projects', err);
  }
}

async function fetchAccessTree() {
  try {
    const res = await apiFetch('/api/access/tree');
    if (!res.ok) return;
    const data = await res.json();
    const tree = data.tree || [];
    const built = buildTeamIndex(tree);
    accessTeams = built.list;
    accessTeamIndex = built.index;
  } catch (err) {
    console.error('Failed to fetch access tree', err);
  }
}

function updateProjectRoleHint() {
  if (!projectRoleHintEl || !projectIdEl) return;
  const selected = projectIdEl.options[projectIdEl.selectedIndex];
  if (!selected || !selected.value) {
    projectRoleHintEl.textContent = 'Project access: personal';
    return;
  }
  const role = selected.dataset.role || 'member';
  const canRead = selected.dataset.capRead === 'true';
  const canWrite = selected.dataset.capWrite === 'true';
  const canGrant = selected.dataset.capGrant === 'true';
  const caps = [canRead && 'read', canWrite && 'write', canGrant && 'grant'].filter(Boolean).join('/');
  projectRoleHintEl.textContent = `Project access: ${role}${caps ? ` (${caps})` : ''}`;
}

async function fetchTokens() {
  if (!tokenPanelEl) return;
  const projectId = projectIdEl?.value;
  const tokenUrl = projectId ? `/api/tokens?project_id=${encodeURIComponent(projectId)}` : '/api/tokens';
  try {
    const res = await apiFetch(tokenUrl);
    if (!res.ok) return;
    const data = await res.json();
    tokenSnapshot = data;
    updateTokenMeter(tokenSnapshot, pendingEstimate);
  } catch (err) {
    console.error('Failed to fetch tokens', err);
  }
}

function scheduleEstimate() {
  if (estimateTimer) {
    clearTimeout(estimateTimer);
  }
  estimateTimer = setTimeout(requestEstimate, 600);
}

async function requestEstimate() {
  if (!tokenPanelEl) return;
  const payload = buildPayload();
  try {
    const res = await apiFetch('/api/jobs/estimate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      pendingEstimate = null;
      updateTokenMeter(tokenSnapshot, pendingEstimate);
      return;
    }
    const data = await res.json();
    pendingEstimate = data.estimate;
    if (tokenSnapshot) {
      tokenSnapshot = { ...tokenSnapshot, ...data };
    } else {
      tokenSnapshot = data;
    }
    updateTokenMeter(tokenSnapshot, pendingEstimate);
    if (data.estimate && data.available !== undefined && data.estimate > data.available) {
      showTokenEstimateStatus(`Estimate ${data.estimate} exceeds available ${data.available}.`, true);
    }
  } catch (err) {
    pendingEstimate = null;
  }
}

async function fetchHealth() {
  const res = await apiFetch('/api/health');
  const data = await res.json();
  workerCountEl.textContent = data.workers;
}

function renderCapabilityItems(container, items, emptyText) {
  if (!container) return;
  if (!Array.isArray(items) || items.length === 0) {
    container.innerHTML = `<div class="capability-item"><p>${escapeHtml(emptyText || 'No items found.')}</p></div>`;
    return;
  }
  const html = items
    .map((item) => {
      const name = escapeHtml(item.name || item.id || 'Capability');
      const desc = escapeHtml(item.description || item.summary || '');
      const metaBits = [];
      if (Array.isArray(item.triggers) && item.triggers.length) {
        metaBits.push(`Triggers: ${item.triggers.join(', ')}`);
      }
      if (Array.isArray(item.outputs) && item.outputs.length) {
        metaBits.push(`Outputs: ${item.outputs.join(', ')}`);
      }
      if (item.evidence_count) {
        metaBits.push(`Evidence: ${item.evidence_count} files`);
      }
      const metaHtml = metaBits.length
        ? `<div class="capability-meta-line">${escapeHtml(metaBits.join(' · '))}</div>`
        : '';
      const descHtml = desc ? `<p>${desc}</p>` : '';
      return `<div class="capability-item"><h4>${name}</h4>${descHtml}${metaHtml}</div>`;
    })
    .join('');
  container.innerHTML = html;
}

function renderCapabilities(data) {
  if (!capabilityCardEl) return;
  const analysis = data?.analysis || {};
  if (capabilityGeneratedAtEl) {
    capabilityGeneratedAtEl.textContent = analysis.generated_at || '--';
  }
  if (capabilityFileCountEl) {
    const count = analysis.files_scanned;
    capabilityFileCountEl.textContent = Number.isFinite(count) ? `${count}` : '--';
  }
  const workflows = Array.isArray(data?.workflows) && data.workflows.length
    ? data.workflows
    : (analysis.workflows_detected || []);
  const features = analysis.features || [];
  renderCapabilityItems(capabilityWorkflowsEl, workflows.slice(0, 6), 'No workflows detected yet.');
  renderCapabilityItems(capabilityFeaturesEl, features.slice(0, 6), 'No capability signals detected yet.');
}

async function fetchCapabilities() {
  if (!capabilityCardEl) return;
  try {
    const res = await apiFetch('/api/capabilities');
    if (!res.ok) {
      if (capabilityStatusEl) {
        capabilityStatusEl.textContent = 'Unable to load capabilities right now.';
        capabilityStatusEl.hidden = false;
      }
      return;
    }
    const data = await res.json();
    if (capabilityStatusEl) {
      capabilityStatusEl.hidden = true;
      capabilityStatusEl.textContent = '';
    }
    renderCapabilities(data);
  } catch (err) {
    if (capabilityStatusEl) {
      capabilityStatusEl.textContent = 'Unable to load capabilities right now.';
      capabilityStatusEl.hidden = false;
    }
  }
}

async function selectJob(jobId) {
  const nextJobId = jobId;
  if (selectedJobId !== nextJobId) {
    refundPanelOpen = false;
    refundPanelJobId = nextJobId;
  }
  selectedJobId = nextJobId;
  renderJobs();
  const res = await apiFetch(`/api/jobs/${jobId}`);
  if (!res.ok) {
    return;
  }
  const job = await res.json();
  renderJobDetail(job);
  await loadLogs(jobId);
  startLogStream(jobId);
  await startSession(job);
}

async function refreshSelectedJob(options = {}) {
  if (!selectedJobId) return;
  const res = await apiFetch(`/api/jobs/${selectedJobId}`);
  if (!res.ok) return;
  const job = await res.json();
  renderJobDetail(job, options);
  renderSessionPanel(sessionSnapshot);
}

function renderJobDetail(job, options = {}) {
  if (!job) {
    jobDetailEl.innerHTML = '';
    jobHintEl.style.display = 'block';
    clearDurationTimer();
    clearReqProgressTimer();
    refundPanelOpen = false;
    refundPanelJobId = null;
    stopSession();
    return;
  }
  jobHintEl.style.display = 'none';
  const stages = job.stages || [];
  const stageHtml = stages.length
    ? stages.map((stage) => `<span class="stage ${stage.status}">${stage.name}: ${stage.status}</span>`).join('')
    : '<span class="stage">No stages yet</span>';
  const liveTokenTotal = job.metrics?.token_usage?.total;
  const actualTokenTotal = job.tokens?.actual;
  const showLiveTokens = ['running', 'paused'].includes(job.status)
    || (liveTokenTotal !== undefined && liveTokenTotal !== null
      && (actualTokenTotal === undefined || actualTokenTotal === null || liveTokenTotal !== actualTokenTotal));
  const tokensLiveRow = showLiveTokens
    ? `<div class="detail-card"><span class="label has-tip" data-tooltip="Live token usage reported during the run. Updates while the job is running.">Tokens (live)</span><div class="value">${formatTokens(job.metrics)}</div></div>`
    : '';
  const transfer = job.transfer_request;
  const isOwner = currentProfile?.user && job.owner === currentProfile.user;
  const isAdmin = currentProfile?.role === 'admin';
  const pendingTransfer = transfer?.status === 'pending';
  const transferTeamId = transfer?.team_id;
  const canAcceptTransfer = pendingTransfer && (isAdmin || isTeamLeader(transferTeamId));
  const canCancelTransfer = pendingTransfer && isOwner;
  const canRequestTransfer = !pendingTransfer && !job.team_id && isOwner;
  const canAssignUser = job.team_id && (isAdmin || isTeamLeader(job.team_id));
  let transferStatus = 'Personal queue entry.';
  if (job.team_id) {
    transferStatus = `Assigned to team ${teamName(job.team_id)}.`;
  }
  if (pendingTransfer) {
    const requester = transfer?.requested_by || 'unknown';
    transferStatus = `Pending invite to ${teamName(transferTeamId)} from ${requester}.`;
  }
  const transferActions = [];
  if (canRequestTransfer) {
    transferActions.push('<button type="button" class="ghost" id="transferInvite">Invite Team Leader</button>');
  }
  if (canCancelTransfer) {
    transferActions.push('<button type="button" class="ghost danger" id="transferCancel">Cancel Invite</button>');
  }
  if (canAcceptTransfer) {
    transferActions.push('<button type="button" class="ghost" id="transferAccept">Accept To Team</button>');
    transferActions.push('<button type="button" class="ghost danger" id="transferDecline">Decline Invite</button>');
  }
  if (canAssignUser) {
    transferActions.push('<button type="button" class="ghost" id="transferAssign">Assign To Individual</button>');
  }
  const transferPanelHtml = transferActions.length
    ? `
      <div class="transfer-panel">
        <div class="label">Queue Assignment</div>
        <div class="value">${transferStatus}</div>
        <div class="transfer-actions">
          ${transferActions.join('')}
        </div>
      </div>
    `
    : '';
  const repoInfo = job.repo_info || {};
  const canManageJob = Boolean(job.project_capabilities?.write);
  const repoMeta = repoInfo.fork_org && repoInfo.fork_repo
    ? `${repoInfo.fork_org}/${repoInfo.fork_repo}`
    : (repoInfo.owner ? `${repoInfo.owner}/${repoInfo.repo}` : '--');
  const repoBranch = repoInfo.branch || '--';
  const repoUrl = repoInfo.repo_url || (repoInfo.fork_org && repoInfo.fork_repo ? `https://github.com/${repoInfo.fork_org}/${repoInfo.fork_repo}` : '');
  const repoLink = repoUrl ? `<a class="link-inline" href="${repoUrl}" target="_blank" rel="noopener">Open repo</a>` : '--';
  const refund = getLatestRefund(job);
  const refundStatus = refund?.status || 'none';
  const refundApproved = refund?.approved_amount ?? refund?.admin_decision?.amount;
  const canRequestRefund = !refund || ['rejected', 'settled', 'partial-refund'].includes(refundStatus);
  const showRefundPanel = refundPanelOpen && refundPanelJobId === job.id;
  const showEditor = EDITOR_WORKFLOWS.has(job.workflow);
  const existingEditorPanel = document.getElementById('editorPanel');
  const preserveEditor = Boolean(options.preserveEditor)
    && existingEditorPanel
    && existingEditorPanel.dataset.jobId === job.id;
  const refundSummary = refund ? `
      <div class="refund-meta">
        <div><span>Status</span><strong>${refundStatus}</strong></div>
        <div><span>Requested</span><strong>${formatAmount(refund.requested_amount)}</strong></div>
        <div><span>Approved</span><strong>${formatAmount(refundApproved)}</strong></div>
        <div><span>Submitted</span><strong>${formatAbsoluteTime(refund.requested_at)}</strong></div>
      </div>
      <p class="subtitle">${refund.reason || 'No reason provided.'}</p>
  ` : '<p class="subtitle">No refund request submitted for this job.</p>';
  const refundForm = canRequestRefund ? `
      <div class="refund-form">
        <div class="field grid-2">
          <div>
            <label for="refundAmount">Requested Amount</label>
            <input id="refundAmount" type="text" inputmode="numeric" pattern="[0-9]*" placeholder="Tokens">
          </div>
          <div>
            <label for="refundReason">Reason</label>
            <input id="refundReason" type="text" placeholder="Short reason">
          </div>
        </div>
        <div class="field">
          <label for="refundDetails">Additional Details</label>
          <textarea id="refundDetails" rows="3" placeholder="Explain what went wrong and expected outcome..."></textarea>
        </div>
        <div class="field">
          <label for="refundScreenshots">Screenshots</label>
          <input id="refundScreenshots" type="file" accept="image/*" multiple>
          <div id="refundFiles" class="refund-files"></div>
        </div>
        <button type="button" id="refundSubmit" class="primary">Submit Refund Request</button>
      </div>
  ` : `<p class="subtitle">Refund request is ${refundStatus}. Awaiting admin response.</p>`;
  const refundPanelAttrs = showRefundPanel ? '' : ' hidden style="display: none;"';
  const refundHtml = `
    <div id="refundPanel" class="refund-panel"${refundPanelAttrs}>
      <div class="card-header">
        <h3>Refund Request</h3>
        <p>Submit a token refund request with reason, details, and screenshots.</p>
      </div>
      ${refundSummary}
      ${refundForm}
      <div id="refundStatus" class="status-banner" hidden></div>
    </div>
  `;
  const workspaceHtml = `
    <div class="workspace-panel" id="workspacePanel" data-job-id="${job.id}">
      <div class="workspace-head">
        <div>
          <span class="label">Interactive IDE + Preview</span>
          <div class="value" id="workspaceStatus">Loading...</div>
        </div>
        <div class="workspace-actions">
          <button type="button" class="primary" id="workspaceLaunch">Launch IDE</button>
          <button type="button" class="ghost" id="workspaceAttachToggle">Attach URLs</button>
          <button type="button" class="ghost" id="workspaceRefresh">Refresh</button>
          <button type="button" class="ghost" id="workspaceClear">Clear</button>
        </div>
      </div>
      <div class="workspace-meta" id="workspaceMeta"></div>
      <div class="workspace-attach" id="workspaceAttach" hidden>
        <div class="field grid-2">
          <div>
            <label for="workspaceIdeInput">IDE URL</label>
            <input id="workspaceIdeInput" type="url" placeholder="https://ide.example.com">
          </div>
          <div>
            <label for="workspacePreviewInput">Preview URL</label>
            <input id="workspacePreviewInput" type="url" placeholder="https://preview.example.com">
          </div>
        </div>
        <div class="workspace-attach-actions">
          <button type="button" class="primary" id="workspaceAttachSave">Save URLs</button>
        </div>
      </div>
      <div class="workspace-links" id="workspaceLinks"></div>
      <div class="workspace-frames" id="workspaceFrames"></div>
      <div class="status-banner" id="workspaceStatusBanner" hidden></div>
    </div>
  `;
  const editorHtml = showEditor
    ? `
      <div class="editor-panel" id="editorPanel" data-job-id="${job.id}">
        <div class="card-header">
          <h3>Workspace Editor</h3>
          <p>Edit project files or research documents directly from Control Room.</p>
        </div>
        <div class="editor-toolbar">
          <select id="editorRootSelect"></select>
          <button type="button" class="ghost" id="editorUpBtn">Up</button>
          <input id="editorPath" type="text" readonly placeholder="Select a file to edit">
          <button type="button" class="ghost" id="editorReload">Reload</button>
          <button type="button" class="primary" id="editorSave" disabled>Save</button>
          <button type="button" class="ghost" id="editorNewFile">New File</button>
          <button type="button" class="ghost" id="editorNewFolder">New Folder</button>
          <button type="button" class="ghost" id="editorRename">Rename</button>
          <button type="button" class="ghost" id="editorMove">Move</button>
          <button type="button" class="ghost" id="editorDelete">Delete</button>
        </div>
        <div class="editor-body">
          <div class="editor-sidebar">
            <div id="editorFileList" class="editor-file-list"></div>
          </div>
          <div class="editor-main">
            <textarea id="editorContent" spellcheck="false" placeholder="Select a file to edit."></textarea>
          </div>
        </div>
        <div class="status-banner" id="editorStatus" hidden></div>
      </div>
    `
    : '';
  const editorBlock = showEditor
    ? (preserveEditor ? '<div id="editorAnchor"></div>' : editorHtml)
    : '';

  const projectAccess = job.project_capabilities
    ? [job.project_capabilities.read && 'read', job.project_capabilities.write && 'write', job.project_capabilities.grant && 'grant']
      .filter(Boolean)
      .join('/') || '--'
    : '--';
  const detailHtml = `
    <div class="detail-grid">
      <div class="detail-card"><span class="label">Status</span><div class="value">${job.status}</div></div>
      <div class="detail-card"><span class="label">Workflow</span><div class="value">${job.workflow}</div></div>
      <div class="detail-card"><span class="label">Project</span><div class="value">${job.project_name || 'Untitled'}</div></div>
      <div class="detail-card"><span class="label">Owner</span><div class="value">${job.owner || '--'}</div></div>
      <div class="detail-card"><span class="label">Team</span><div class="value">${job.team_name || '--'}</div></div>
      <div class="detail-card"><span class="label">Project Role</span><div class="value">${job.project_role || '--'}</div></div>
      <div class="detail-card"><span class="label">Project Access</span><div class="value">${projectAccess}</div></div>
      <div class="detail-card"><span class="label">Started</span><div class="value">${formatAbsoluteTime(job.started_at)}</div></div>
      <div class="detail-card">
        <span class="label">Duration</span>
        <div class="value" id="jobDurationValue" data-status="${job.status || ''}" data-started-at="${job.started_at || ''}" data-runtime-sec="${job.metrics?.runtime_sec ?? ''}">
          ${formatDuration(job.metrics?.runtime_sec)}
        </div>
      </div>
      ${tokensLiveRow}
      <div class="detail-card"><span class="label">Token Estimate</span><div class="value">${job.tokens?.estimate ?? '--'}</div></div>
      <div class="detail-card"><span class="label">Token Reserved</span><div class="value">${job.tokens?.reserved ?? 0}</div></div>
      <div class="detail-card"><span class="label has-tip" data-tooltip="Final billed tokens after settlement.">Token Actual (settled)</span><div class="value">${job.tokens?.actual ?? 0}</div></div>
      <div class="detail-card"><span class="label">Token Shortfall</span><div class="value">${job.tokens?.shortfall ?? 0}</div></div>
      <div class="detail-card"><span class="label">Errors</span><div class="value">${job.metrics?.errors ?? 0}</div></div>
      <div class="detail-card"><span class="label">Resolved</span><div class="value">${job.metrics?.resolved ?? 0}</div></div>
      <div class="detail-card"><span class="label">Restarts</span><div class="value">${job.restart_count ?? 0}</div></div>
      <div class="detail-card"><span class="label">Exit Code</span><div class="value">${job.exit_code ?? '--'}</div></div>
      <div class="detail-card"><span class="label">Queue Wait</span><div class="value">${formatDuration(job.metrics?.queue_wait_sec)}</div></div>
      <div class="detail-card"><span class="label">Repo</span><div class="value">${repoMeta}</div></div>
      <div class="detail-card"><span class="label">Repo Link</span><div class="value">${repoLink}</div></div>
      <div class="detail-card"><span class="label">Branch</span><div class="value">${repoBranch}</div></div>
    </div>
    ${transferPanelHtml}
    <div class="session-panel" id="sessionPanel" data-job-id="${job.id}">
      <div class="label">Workspace Session</div>
      <div class="value" id="sessionStatus">Connecting...</div>
      <div class="session-room">
        <label for="sessionRoomInput">Room ID</label>
        <div class="session-room-row">
          <input id="sessionRoomInput" type="text" placeholder="Enter room ID">
          <button type="button" class="ghost" id="sessionRoomJoin">Join</button>
        </div>
      </div>
      <div class="session-participants" id="sessionParticipants"></div>
      <div class="session-history" id="sessionHistory"></div>
    </div>
    ${workspaceHtml}
    ${editorBlock}
    <div>
      <h3>Stages</h3>
      <div class="req-progress" id="reqProgress" data-job-id="${job.id}">
        <div class="req-head">
          <span class="label">Requirements Progress</span>
          <span class="value" id="reqProgressSummary">--</span>
        </div>
        <div class="req-bar">
          <span class="req-segment completed" id="reqCompleted"></span>
          <span class="req-segment in-progress" id="reqInProgress"></span>
          <span class="req-segment remaining" id="reqRemaining"></span>
        </div>
        <div class="req-meta">
          <span id="reqCompletedLabel">Completed: --</span>
          <span id="reqInProgressLabel">In progress: --</span>
          <span id="reqRemainingLabel">Remaining: --</span>
        </div>
        <div class="req-status" id="reqProgressStatus" hidden></div>
      </div>
      <div class="req-summary" id="reqSummary" data-job-id="${job.id}">
        <div class="req-head">
          <span class="label">Requirements Summary</span>
          <span class="badge redacted" id="reqSummaryRedaction" hidden>Global requirements redacted</span>
          <span class="value" id="reqSummaryMeta">--</span>
        </div>
        <div class="req-summary-lead" id="reqSummaryLead">--</div>
        <div class="req-summary-list" id="reqSummaryList"></div>
        <div class="req-status" id="reqSummaryStatus" hidden></div>
      </div>
      <div class="stage-list">${stageHtml}</div>
    </div>
    ${refundHtml}
    <div class="actions">
      <button type="button" class="ghost" data-action="pause"${canManageJob ? '' : ' disabled'}>Pause</button>
      <button type="button" class="ghost" data-action="resume"${canManageJob ? '' : ' disabled'}>Resume</button>
      <button type="button" class="ghost" data-action="stop"${canManageJob ? '' : ' disabled'}>Stop</button>
      <button type="button" class="ghost" id="refundToggle">Refund</button>
      <button type="button" class="primary" data-action="restart"${canManageJob ? '' : ' disabled'}>Restart</button>
    </div>
  `;
  jobDetailEl.innerHTML = detailHtml;
  if (preserveEditor && existingEditorPanel) {
    const anchor = document.getElementById('editorAnchor');
    if (anchor) {
      anchor.replaceWith(existingEditorPanel);
      if (editorMirror) {
        editorMirror.refresh();
      }
    }
  }
  jobDetailEl.querySelectorAll('button[data-action]').forEach((btn) => {
    btn.addEventListener('click', () => postAction(job.id, btn.dataset.action));
  });
  const refundToggleBtn = document.getElementById('refundToggle');
  if (refundToggleBtn) {
    refundToggleBtn.addEventListener('click', () => {
      const panel = document.getElementById('refundPanel');
      if (!panel) return;
      const isOpen = !panel.hidden;
      panel.hidden = isOpen;
      panel.style.display = isOpen ? 'none' : '';
      refundPanelOpen = !isOpen;
      refundPanelJobId = job.id;
    });
  }
  const refundSubmitBtn = document.getElementById('refundSubmit');
  if (refundSubmitBtn) {
    refundSubmitBtn.addEventListener('click', () => submitRefundRequest(job.id));
  }
  const refundAmountEl = document.getElementById('refundAmount');
  const refundReasonEl = document.getElementById('refundReason');
  const refundDetailsEl = document.getElementById('refundDetails');
  if (refundAmountEl || refundReasonEl || refundDetailsEl) {
    const fields = {
      amount: refundAmountEl,
      reason: refundReasonEl,
      details: refundDetailsEl,
    };
    restoreRefundDraft(job.id, fields);
    const onInput = () => persistRefundDraft(job.id, fields);
    if (refundAmountEl) refundAmountEl.addEventListener('input', onInput);
    if (refundReasonEl) refundReasonEl.addEventListener('input', onInput);
    if (refundDetailsEl) refundDetailsEl.addEventListener('input', onInput);
  }
  const refundFilesEl = document.getElementById('refundScreenshots');
  if (refundFilesEl) {
    refundFilesEl.addEventListener('change', () => updateRefundFilesList(refundFilesEl.files));
    updateRefundFilesList(refundFilesEl.files);
  }
  const transferInviteBtn = document.getElementById('transferInvite');
  if (transferInviteBtn) {
    transferInviteBtn.addEventListener('click', () => openTransferModal('invite', job));
  }
  const transferCancelBtn = document.getElementById('transferCancel');
  if (transferCancelBtn) {
    transferCancelBtn.addEventListener('click', async () => {
      showJobStatus('Cancelling invite...');
      const result = await postTransfer(job.id, { action: 'cancel' });
      if (result) {
        renderJobDetail(result);
        await fetchJobs();
      }
    });
  }
  const transferAcceptBtn = document.getElementById('transferAccept');
  if (transferAcceptBtn) {
    transferAcceptBtn.addEventListener('click', () => openTransferModal('accept', job));
  }
  const transferDeclineBtn = document.getElementById('transferDecline');
  if (transferDeclineBtn) {
    transferDeclineBtn.addEventListener('click', async () => {
      showJobStatus('Declining invite...');
      const result = await postTransfer(job.id, { action: 'decline' });
      if (result) {
        renderJobDetail(result);
        await fetchJobs();
      }
    });
  }
  const transferAssignBtn = document.getElementById('transferAssign');
  if (transferAssignBtn) {
    transferAssignBtn.addEventListener('click', () => openTransferModal('assign', job));
  }
  initWorkspaceControls(job);
  renderWorkspacePanel(job.workspace_env || {}, {});
  loadWorkspace(job.id);
  if (EDITOR_WORKFLOWS.has(job.workflow) && !preserveEditor) {
    initEditorControls(job, canManageJob);
  }
  scheduleDurationUpdates();
  scheduleRequirementProgressUpdates(job);
  scheduleRequirementSummary(job);
}

async function loadLogs(jobId) {
  const res = await apiFetch(`/api/jobs/${jobId}/logs`);
  const data = await res.json();
  logOutputEl.textContent = '';
  if (data.logs) {
    data.logs.forEach((entry) => appendLog(entry));
  }
}

function startLogStream(jobId) {
  if (logStream) {
    logStream.close();
  }
  logStream = apiEventSource(`/api/jobs/${jobId}/logs/stream`);
  logStream.onmessage = (event) => {
    const entry = JSON.parse(event.data);
    appendLog(entry);
  };
  logStream.onerror = () => {
    logStream.close();
  };
}

async function startSession(job, roomId) {
  if (!job || !job.id) return;
  await stopSession();
  try {
    const res = await apiFetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: job.id, room_id: roomId }),
    });
    if (!res.ok) {
      renderSessionPanel(null, 'Session unavailable');
      return;
    }
    const data = await res.json();
    activeSessionId = data.room_id || data.session_id;
    sessionSnapshot = data;
    renderSessionPanel(sessionSnapshot);
    fetchSessionHistory(activeSessionId);
    sessionStream = apiEventSource(`/api/sessions/${activeSessionId}/stream`);
    sessionStream.addEventListener('presence', (event) => {
      const payload = JSON.parse(event.data);
      sessionSnapshot = payload;
      renderSessionPanel(sessionSnapshot);
    });
    sessionStream.addEventListener('job', (event) => {
      const payload = JSON.parse(event.data);
      if (payload.job_id === selectedJobId) {
        refreshSelectedJob({ preserveEditor: true });
      }
    });
    sessionStream.onerror = () => {
      if (sessionStream) {
        sessionStream.close();
      }
    };
  } catch (err) {
    console.error('Failed to start session', err);
    renderSessionPanel(null, 'Session unavailable');
  }
}

async function stopSession() {
  if (sessionStream) {
    sessionStream.close();
    sessionStream = null;
  }
  if (activeSessionId) {
    try {
      await apiFetch(`/api/sessions/${activeSessionId}/leave`, { method: 'POST' });
    } catch (err) {
      // ignore
    }
    activeSessionId = null;
  }
  sessionSnapshot = null;
  renderSessionPanel(null);
  renderSessionHistory([]);
}

function renderSessionPanel(snapshot, overrideStatus) {
  const statusEl = document.getElementById('sessionStatus');
  const participantsEl = document.getElementById('sessionParticipants');
  const roomInputEl = document.getElementById('sessionRoomInput');
  const roomJoinBtn = document.getElementById('sessionRoomJoin');
  if (!statusEl || !participantsEl) return;
  if (!snapshot) {
    statusEl.textContent = overrideStatus || 'No active session';
    participantsEl.innerHTML = '<span class="subtitle">No participants yet.</span>';
    if (roomInputEl) roomInputEl.value = '';
    return;
  }
  const participantCount = Array.isArray(snapshot.participants) ? snapshot.participants.length : 0;
  const sessionLabel = snapshot.room_id || snapshot.session_id || '--';
  statusEl.textContent = overrideStatus || `Session ${sessionLabel} · ${participantCount} participant${participantCount === 1 ? '' : 's'}`;
  if (roomInputEl) {
    roomInputEl.value = snapshot.room_id || snapshot.session_id || '';
  }
  if (roomJoinBtn) {
    roomJoinBtn.onclick = () => {
      const value = (roomInputEl?.value || '').trim();
      if (!value || !selectedJobId) return;
      const currentJob = jobs.find((j) => j.id === selectedJobId);
      if (currentJob) {
        startSession(currentJob, value);
      }
    };
  }
  if (!participantCount) {
    participantsEl.innerHTML = '<span class="subtitle">No participants yet.</span>';
    return;
  }
  participantsEl.innerHTML = snapshot.participants
    .map((entry) => {
      const name = escapeHtml(entry.user || '');
      const role = entry.role ? ` (${escapeHtml(entry.role)})` : '';
      return `<span class="session-pill">${name}${role}</span>`;
    })
    .join('');
}

async function fetchSessionHistory(roomId) {
  if (!roomId) return;
  try {
    const res = await apiFetch(`/api/sessions/${roomId}/history`);
    if (!res.ok) {
      renderSessionHistory([]);
      return;
    }
    const data = await res.json();
    renderSessionHistory(data.history || []);
  } catch (err) {
    renderSessionHistory([]);
  }
}

function renderSessionHistory(events) {
  const historyEl = document.getElementById('sessionHistory');
  if (!historyEl) return;
  if (!Array.isArray(events) || !events.length) {
    historyEl.innerHTML = '<span class="subtitle">No session history yet.</span>';
    return;
  }
  const recent = events.slice(-12).reverse();
  historyEl.innerHTML = recent
    .map((event) => {
      const when = escapeHtml(event.ts || '');
      const type = escapeHtml(event.type || '');
      const user = escapeHtml(event.user || 'system');
      const detail = event.detail ? escapeHtml(JSON.stringify(event.detail)) : '';
      return `<div class="session-history-item"><strong>${type}</strong> · ${user} · ${when}${detail ? ` · ${detail}` : ''}</div>`;
    })
    .join('');
}

function showWorkspaceStatus(message, isError = false) {
  const statusEl = document.getElementById('workspaceStatusBanner');
  if (!statusEl) return;
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.toggle('error', Boolean(isError));
}

function clearWorkspaceStatus() {
  const statusEl = document.getElementById('workspaceStatusBanner');
  if (!statusEl) return;
  statusEl.textContent = '';
  statusEl.hidden = true;
  statusEl.classList.remove('error');
}

function buildWorkspaceFrame(label, url) {
  const safeLabel = escapeHtml(label);
  const safeUrl = escapeHtml(url || '');
  if (!url) {
    return `
      <div class="workspace-frame empty">
        <div class="frame-header">${safeLabel}</div>
        <div class="frame-body">
          <p class="subtitle">No ${safeLabel.toLowerCase()} URL configured.</p>
        </div>
      </div>
    `;
  }
  return `
    <div class="workspace-frame">
      <div class="frame-header">${safeLabel}</div>
      <div class="frame-body">
        <iframe src="${safeUrl}" title="${safeLabel}" loading="lazy" referrerpolicy="no-referrer"></iframe>
      </div>
    </div>
  `;
}

function renderWorkspacePanel(workspace, capabilities) {
  const statusEl = document.getElementById('workspaceStatus');
  const metaEl = document.getElementById('workspaceMeta');
  const linksEl = document.getElementById('workspaceLinks');
  const framesEl = document.getElementById('workspaceFrames');
  const ideInput = document.getElementById('workspaceIdeInput');
  const previewInput = document.getElementById('workspacePreviewInput');
  const launchBtn = document.getElementById('workspaceLaunch');
  const refreshBtn = document.getElementById('workspaceRefresh');
  const clearBtn = document.getElementById('workspaceClear');
  workspaceSnapshot = workspace && typeof workspace === 'object' ? workspace : {};
  workspaceCapabilities = capabilities && typeof capabilities === 'object' ? capabilities : {};

  const provider = workspaceSnapshot.provider || '';
  const status = workspaceSnapshot.status || '';
  let statusLabel = '';
  if (provider || status) {
    statusLabel = [provider, status].filter(Boolean).join(' · ');
  } else if (workspaceCapabilities?.continuum_ready) {
    statusLabel = 'No workspace yet';
  } else if (workspaceCapabilities?.continuum) {
    statusLabel = 'Continuum missing configuration';
  } else {
    statusLabel = 'No workspace configured';
  }
  if (statusEl) statusEl.textContent = statusLabel;

  const metaParts = [];
  if (workspaceSnapshot.vm_id) metaParts.push(`VM: ${workspaceSnapshot.vm_id}`);
  if (workspaceSnapshot.updated_at) metaParts.push(`Updated: ${formatAbsoluteTime(workspaceSnapshot.updated_at)}`);
  if (workspaceSnapshot.details) metaParts.push(workspaceSnapshot.details);
  if (!metaParts.length) {
    metaParts.push('Attach URLs or launch a Continuum workspace.');
  }
  if (metaEl) {
    metaEl.innerHTML = metaParts.map((entry) => `<span>${escapeHtml(entry)}</span>`).join('');
  }

  if (ideInput) ideInput.value = workspaceSnapshot.ide_url || '';
  if (previewInput) previewInput.value = workspaceSnapshot.preview_url || '';

  if (linksEl) {
    const linkParts = [];
    if (workspaceSnapshot.ide_url) {
      linkParts.push(`<a class="link-inline" href="${escapeHtml(workspaceSnapshot.ide_url)}" target="_blank" rel="noopener">Open IDE</a>`);
    }
    if (workspaceSnapshot.preview_url) {
      linkParts.push(`<a class="link-inline" href="${escapeHtml(workspaceSnapshot.preview_url)}" target="_blank" rel="noopener">Open Preview</a>`);
    }
    linksEl.innerHTML = linkParts.length ? linkParts.join(' · ') : '<span class="subtitle">No workspace links yet.</span>';
  }

  if (framesEl) {
    framesEl.innerHTML = `${buildWorkspaceFrame('IDE', workspaceSnapshot.ide_url)}${buildWorkspaceFrame('Preview', workspaceSnapshot.preview_url)}`;
  }

  if (launchBtn) {
    const canLaunch = Boolean(workspaceCapabilities?.continuum_ready);
    launchBtn.disabled = !canLaunch;
    launchBtn.title = canLaunch ? 'Launch a Continuum workspace' : 'Continuum not configured';
  }
  if (refreshBtn) {
    refreshBtn.disabled = !(workspaceSnapshot.provider || workspaceSnapshot.vm_id || workspaceCapabilities?.continuum_ready);
  }
  if (clearBtn) {
    clearBtn.disabled = !workspaceSnapshot || (!workspaceSnapshot.ide_url && !workspaceSnapshot.preview_url && !workspaceSnapshot.vm_id);
  }
}

async function loadWorkspace(jobId) {
  if (!jobId) return;
  clearWorkspaceStatus();
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/workspace`);
    const data = await res.json();
    if (selectedJobId !== jobId) return;
    if (!res.ok) {
      showWorkspaceStatus(data.details || data.error || 'Workspace unavailable.', true);
      renderWorkspacePanel(null, data.capabilities || {});
      return;
    }
    renderWorkspacePanel(data.workspace || {}, data.capabilities || {});
  } catch (err) {
    if (selectedJobId !== jobId) return;
    showWorkspaceStatus('Workspace unavailable.', true);
  }
}

async function postWorkspaceAction(jobId, body) {
  if (!jobId) return null;
  clearWorkspaceStatus();
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/workspace`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (selectedJobId !== jobId) return data;
    if (!res.ok) {
      showWorkspaceStatus(data.details || data.error || 'Workspace update failed.', true);
      return null;
    }
    renderWorkspacePanel(data.workspace || {}, data.capabilities || {});
    if (data.status) {
      showWorkspaceStatus(`Workspace ${data.status}.`);
    } else {
      showWorkspaceStatus('Workspace updated.');
    }
    return data;
  } catch (err) {
    if (selectedJobId === jobId) {
      showWorkspaceStatus('Workspace update failed.', true);
    }
    return null;
  }
}

function initWorkspaceControls(job) {
  const launchBtn = document.getElementById('workspaceLaunch');
  const attachToggleBtn = document.getElementById('workspaceAttachToggle');
  const attachEl = document.getElementById('workspaceAttach');
  const attachSaveBtn = document.getElementById('workspaceAttachSave');
  const refreshBtn = document.getElementById('workspaceRefresh');
  const clearBtn = document.getElementById('workspaceClear');
  const ideInput = document.getElementById('workspaceIdeInput');
  const previewInput = document.getElementById('workspacePreviewInput');

  if (launchBtn) {
    launchBtn.addEventListener('click', async () => {
      showWorkspaceStatus('Launching workspace...');
      await postWorkspaceAction(job.id, { action: 'create' });
    });
  }
  if (attachToggleBtn && attachEl) {
    attachToggleBtn.addEventListener('click', () => {
      attachEl.hidden = !attachEl.hidden;
    });
  }
  if (attachSaveBtn) {
    attachSaveBtn.addEventListener('click', async () => {
      const ideUrl = ideInput ? ideInput.value.trim() : '';
      const previewUrl = previewInput ? previewInput.value.trim() : '';
      showWorkspaceStatus('Saving workspace URLs...');
      await postWorkspaceAction(job.id, { action: 'attach', ide_url: ideUrl, preview_url: previewUrl });
    });
  }
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      showWorkspaceStatus('Refreshing workspace...');
      await postWorkspaceAction(job.id, { action: 'refresh' });
    });
  }
  if (clearBtn) {
    clearBtn.addEventListener('click', async () => {
      showWorkspaceStatus('Clearing workspace...');
      await postWorkspaceAction(job.id, { action: 'clear' });
    });
  }
}

const editorState = {
  jobId: null,
  rootId: null,
  cwd: '',
  openPath: '',
  dirty: false,
  canWrite: false,
  selectedPath: '',
  selectedType: '',
  suspendChange: false,
  lastEditorError: '',
};
let editorMirror = null;

function setEditorStatus(message, isError = false) {
  const statusEl = document.getElementById('editorStatus');
  if (!statusEl) return;
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.toggle('error', Boolean(isError));
}

function clearEditorStatus() {
  const statusEl = document.getElementById('editorStatus');
  if (!statusEl) return;
  statusEl.textContent = '';
  statusEl.hidden = true;
  statusEl.classList.remove('error');
  editorState.lastEditorError = '';
}

function editorModeForPath(path) {
  const lowered = (path || '').toLowerCase();
  if (lowered.endsWith('.md') || lowered.endsWith('.markdown')) return 'markdown';
  if (lowered.endsWith('.js') || lowered.endsWith('.jsx') || lowered.endsWith('.ts') || lowered.endsWith('.tsx')) return 'javascript';
  if (lowered.endsWith('.json')) return { name: 'javascript', json: true };
  if (lowered.endsWith('.py')) return 'python';
  if (lowered.endsWith('.css')) return 'css';
  if (lowered.endsWith('.html') || lowered.endsWith('.htm')) return 'htmlmixed';
  if (lowered.endsWith('.xml') || lowered.endsWith('.svg')) return 'xml';
  if (lowered.endsWith('.yml') || lowered.endsWith('.yaml')) return 'yaml';
  if (lowered.endsWith('.sh') || lowered.endsWith('.bash')) return 'shell';
  if (lowered.endsWith('.sql')) return 'sql';
  return 'text/plain';
}

function ensureEditorMirror() {
  if (editorMirror || typeof window === 'undefined') return;
  const textarea = document.getElementById('editorContent');
  if (!textarea || !window.CodeMirror) return;
  editorMirror = window.CodeMirror.fromTextArea(textarea, {
    lineNumbers: true,
    lineWrapping: true,
    theme: 'material-darker',
    mode: 'text/plain',
  });
  editorMirror.on('change', () => {
    if (editorState.suspendChange) return;
    editorState.dirty = true;
    updateEditorSaveState();
  });
  editorMirror.on('blur', () => {
    if (editorState.suspendChange) return;
    editorState.dirty = true;
    updateEditorSaveState();
  });
}

function setEditorContent(value, path = '') {
  if (editorMirror) {
    editorState.suspendChange = true;
    editorMirror.setOption('mode', editorModeForPath(path));
    editorMirror.setValue(value || '');
    editorMirror.refresh();
    editorState.suspendChange = false;
  } else {
    const textarea = document.getElementById('editorContent');
    if (textarea) textarea.value = value || '';
  }
}

function getEditorContent() {
  if (editorMirror) {
    return editorMirror.getValue();
  }
  const textarea = document.getElementById('editorContent');
  return textarea ? textarea.value : '';
}

function updateEditorSaveState() {
  const saveBtn = document.getElementById('editorSave');
  if (!saveBtn) return;
  if (!editorState.openPath || !editorState.canWrite) {
    saveBtn.disabled = true;
    saveBtn.textContent = 'Save';
    return;
  }
  saveBtn.disabled = false;
  saveBtn.textContent = editorState.dirty ? 'Save *' : 'Save';
}

function renderEditorEntries(entries, truncated) {
  const listEl = document.getElementById('editorFileList');
  if (!listEl) return;
  if (!entries.length) {
    listEl.innerHTML = '<span class="subtitle">No files found.</span>';
    return;
  }
  const rows = entries.map((entry) => {
    const type = entry.type || 'file';
    const name = escapeHtml(entry.name || entry.path || '');
    const path = escapeHtml(entry.path || '');
    const size = entry.size ? `${entry.size}b` : '';
    const selected = entry.path && (entry.path === editorState.selectedPath || entry.path === editorState.openPath);
    return `
      <button type="button" class="editor-entry ${type}${selected ? ' selected' : ''}" data-path="${path}" data-type="${type}">
        <span class="entry-name">${name}</span>
        ${size ? `<span class="entry-meta">${size}</span>` : ''}
      </button>
    `;
  });
  if (truncated) {
    rows.push('<span class="subtitle">Listing truncated.</span>');
  }
  listEl.innerHTML = rows.join('');
  listEl.querySelectorAll('.editor-entry').forEach((btn) => {
    btn.addEventListener('click', () => {
      const path = btn.dataset.path || '';
      const type = btn.dataset.type || 'file';
      if (!editorState.jobId || !editorState.rootId) return;
      listEl.querySelectorAll('.editor-entry').forEach((entry) => entry.classList.remove('selected'));
      btn.classList.add('selected');
      editorState.selectedPath = path;
      editorState.selectedType = type;
      if (type === 'dir') {
        listEditorDir(editorState.jobId, editorState.rootId, path);
        editorState.selectedPath = '';
        editorState.selectedType = '';
      } else {
        openEditorFile(editorState.jobId, editorState.rootId, path);
      }
    });
    btn.addEventListener('dblclick', () => {
      const path = btn.dataset.path || '';
      const type = btn.dataset.type || 'file';
      if (type !== 'file') return;
      if (!editorState.jobId || !editorState.rootId) return;
      openFileInIDE(editorState.jobId, editorState.rootId, path);
    });
  });
}

function updateEditorPathDisplay() {
  const pathEl = document.getElementById('editorPath');
  if (!pathEl) return;
  pathEl.value = editorState.openPath || editorState.cwd || '';
}

async function loadEditorRoots(job) {
  if (!job?.id) return;
  try {
    const res = await apiFetch(`/api/jobs/${job.id}/editor/roots`);
    const data = await res.json();
    if (!res.ok) {
      setEditorStatus(data.details || data.error || 'Editor unavailable.', true);
      return;
    }
    const roots = Array.isArray(data.roots) ? data.roots : [];
    const selectEl = document.getElementById('editorRootSelect');
    if (!selectEl) return;
    selectEl.innerHTML = '';
    roots.forEach((root, idx) => {
      const opt = document.createElement('option');
      opt.value = root.id;
      opt.textContent = root.label || root.id;
      opt.dataset.defaultPath = root.default_path || '';
      selectEl.appendChild(opt);
      if (idx === 0) {
        editorState.rootId = root.id;
      }
    });
    if (!roots.length) {
      setEditorStatus('No editable roots available.', true);
      return;
    }
    selectEl.onchange = () => {
      editorState.rootId = selectEl.value;
      editorState.cwd = '';
      editorState.openPath = '';
      editorState.dirty = false;
      updateEditorSaveState();
      updateEditorPathDisplay();
      const nextDefault = selectEl.selectedOptions[0]?.dataset?.defaultPath || '';
      if (nextDefault) {
        openEditorFile(job.id, editorState.rootId, nextDefault);
      } else {
        listEditorDir(job.id, editorState.rootId, '');
      }
    };
    const defaultPath = selectEl.selectedOptions[0]?.dataset?.defaultPath || '';
    if (defaultPath) {
      const opened = await openEditorFile(job.id, editorState.rootId, defaultPath);
      if (!opened) {
        await listEditorDir(job.id, editorState.rootId, '');
      }
    } else {
      await listEditorDir(job.id, editorState.rootId, '');
    }
  } catch (err) {
    setEditorStatus('Editor unavailable.', true);
  }
}

async function listEditorDir(jobId, rootId, path) {
  clearEditorStatus();
  try {
    const params = new URLSearchParams({ root: rootId, path: path || '' });
    const res = await apiFetch(`/api/jobs/${jobId}/editor/list?${params.toString()}`);
    const data = await res.json();
    if (!res.ok) {
      editorState.lastEditorError = data.error || '';
      setEditorStatus(data.details || data.error || 'Unable to list directory.', true);
      return;
    }
    editorState.cwd = data.path || '';
    renderEditorEntries(Array.isArray(data.entries) ? data.entries : [], Boolean(data.truncated));
    updateEditorPathDisplay();
  } catch (err) {
    setEditorStatus('Unable to list directory.', true);
  }
}

async function postEditorOp(jobId, payload) {
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/editor/ops`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      editorState.lastEditorError = data.error || '';
      setEditorStatus(data.details || data.error || 'Editor operation failed.', true);
      return null;
    }
    return data;
  } catch (err) {
    editorState.lastEditorError = 'request_failed';
    setEditorStatus('Editor operation failed.', true);
    return null;
  }
}

function editorResolvePath(input) {
  if (!input) return '';
  const trimmed = input.replace(/^\/+/, '');
  if (trimmed.includes('/')) return trimmed;
  if (editorState.cwd) return `${editorState.cwd}/${trimmed}`;
  return trimmed;
}

async function createEditorFile(jobId) {
  if (!editorState.rootId) {
    setEditorStatus('Select a root first.', true);
    return;
  }
  const name = window.prompt('New file path (relative to root or current folder):');
  if (!name) return;
  const rel = editorResolvePath(name.trim());
  if (!rel) return;
  clearEditorStatus();
  const data = await postEditorOp(jobId, { action: 'create', root: editorState.rootId, path: rel });
  if (!data) return;
  await openEditorFile(jobId, editorState.rootId, data.path || rel);
}

async function createEditorFolder(jobId) {
  if (!editorState.rootId) {
    setEditorStatus('Select a root first.', true);
    return;
  }
  const name = window.prompt('New folder path (relative to root or current folder):');
  if (!name) return;
  const rel = editorResolvePath(name.trim());
  if (!rel) return;
  clearEditorStatus();
  const data = await postEditorOp(jobId, { action: 'mkdir', root: editorState.rootId, path: rel });
  if (!data) return;
  const parent = rel.includes('/') ? rel.split('/').slice(0, -1).join('/') : '';
  await listEditorDir(jobId, editorState.rootId, parent);
}

async function renameEditorEntry(jobId) {
  if (!editorState.rootId) {
    setEditorStatus('Select a root first.', true);
    return;
  }
  const target = editorState.openPath || editorState.selectedPath;
  if (!target) {
    setEditorStatus('Select a file or folder to rename.', true);
    return;
  }
  const baseDir = target.includes('/') ? target.split('/').slice(0, -1).join('/') : '';
  const name = window.prompt('New name or path:', target);
  if (!name) return;
  const rel = name.includes('/') ? name.replace(/^\/+/, '') : (baseDir ? `${baseDir}/${name}` : name);
  clearEditorStatus();
  const data = await postEditorOp(jobId, { action: 'rename', root: editorState.rootId, path: target, new_path: rel });
  if (!data) return;
  editorState.openPath = data.path || rel;
  editorState.selectedPath = '';
  const newDir = editorState.openPath.includes('/') ? editorState.openPath.split('/').slice(0, -1).join('/') : '';
  await listEditorDir(jobId, editorState.rootId, newDir);
  updateEditorPathDisplay();
  if (editorState.openPath) {
    openEditorFile(jobId, editorState.rootId, editorState.openPath);
  }
}

async function moveEditorEntry(jobId) {
  if (!editorState.rootId) {
    setEditorStatus('Select a root first.', true);
    return;
  }
  const target = editorState.openPath || editorState.selectedPath;
  if (!target) {
    setEditorStatus('Select a file or folder to move.', true);
    return;
  }
  const destDir = window.prompt('Move to folder (relative to root):', editorState.cwd || '');
  if (!destDir) return;
  const normalized = destDir.replace(/^\/+/, '').replace(/\/+$/, '');
  const name = target.split('/').slice(-1)[0];
  const newPath = normalized ? `${normalized}/${name}` : name;
  clearEditorStatus();
  const data = await postEditorOp(jobId, { action: 'move', root: editorState.rootId, path: target, new_path: newPath });
  if (!data) return;
  editorState.openPath = data.path || newPath;
  editorState.selectedPath = '';
  const newDir = editorState.openPath.includes('/') ? editorState.openPath.split('/').slice(0, -1).join('/') : '';
  await listEditorDir(jobId, editorState.rootId, newDir);
  updateEditorPathDisplay();
  if (editorState.openPath) {
    openEditorFile(jobId, editorState.rootId, editorState.openPath);
  }
}

async function deleteEditorEntry(jobId) {
  if (!editorState.rootId) {
    setEditorStatus('Select a root first.', true);
    return;
  }
  const target = editorState.openPath || editorState.selectedPath;
  if (!target) {
    setEditorStatus('Select a file or folder to delete.', true);
    return;
  }
  const confirmed = window.confirm(`Delete ${target}?`);
  if (!confirmed) return;
  clearEditorStatus();
  let data = await postEditorOp(jobId, { action: 'delete', root: editorState.rootId, path: target });
  if (!data && editorState.canWrite && editorState.lastEditorError === 'dir_not_empty') {
    const forceConfirm = window.confirm('Folder not empty. Delete everything inside?');
    if (!forceConfirm) return;
    clearEditorStatus();
    data = await postEditorOp(jobId, { action: 'delete', root: editorState.rootId, path: target, force: true });
    if (!data) return;
  }
  if (editorState.openPath === target) {
    editorState.openPath = '';
    editorState.dirty = false;
    setEditorContent('', '');
    updateEditorSaveState();
  }
  await listEditorDir(jobId, editorState.rootId, editorState.cwd);
}

async function openEditorFile(jobId, rootId, path) {
  clearEditorStatus();
  try {
    const params = new URLSearchParams({ root: rootId, path });
    const res = await apiFetch(`/api/jobs/${jobId}/editor/file?${params.toString()}`);
    const data = await res.json();
    if (!res.ok) {
      editorState.lastEditorError = data.error || '';
      setEditorStatus(data.details || data.error || 'Unable to open file.', true);
      return false;
    }
    editorState.openPath = data.path || path;
    editorState.selectedPath = editorState.openPath;
    editorState.selectedType = 'file';
    editorState.dirty = false;
    setEditorContent(data.content || '', editorState.openPath);
    const parent = editorState.openPath.includes('/') ? editorState.openPath.split('/').slice(0, -1).join('/') : '';
    await listEditorDir(jobId, rootId, parent);
    updateEditorSaveState();
    updateEditorPathDisplay();
    return true;
  } catch (err) {
    setEditorStatus('Unable to open file.', true);
    return false;
  }
}

async function openFileInIDE(jobId, rootId, path) {
  if (!jobId || !rootId || !path) return;
  clearWorkspaceStatus();
  try {
    if (!workspaceSnapshot?.ide_url && workspaceCapabilities?.continuum_ready) {
      showWorkspaceStatus('Launching IDE...');
      await postWorkspaceAction(jobId, { action: 'create' });
      await loadWorkspace(jobId);
    }
    const res = await apiFetch(`/api/jobs/${jobId}/workspace/open`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root: rootId, path }),
    });
    const data = await res.json();
    if (!res.ok) {
      showWorkspaceStatus(data.details || data.error || 'Unable to open IDE.', true);
      return;
    }
    const url = data.url || workspaceSnapshot?.ide_url;
    if (url) {
      window.open(url, '_blank', 'noopener');
      if (!data.opened_file) {
        showWorkspaceStatus('IDE opened. File open template not configured.');
      }
    } else {
      showWorkspaceStatus('IDE URL not available.', true);
    }
  } catch (err) {
    showWorkspaceStatus('Unable to open IDE.', true);
  }
}

async function saveEditorFile(jobId) {
  if (!editorState.openPath || !editorState.rootId) {
    setEditorStatus('Select a file to save.', true);
    return;
  }
  clearEditorStatus();
  const content = getEditorContent();
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/editor/file`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root: editorState.rootId, path: editorState.openPath, content }),
    });
    const data = await res.json();
    if (!res.ok) {
      editorState.lastEditorError = data.error || '';
      setEditorStatus(data.details || data.error || 'Unable to save file.', true);
      return;
    }
    editorState.dirty = false;
    updateEditorSaveState();
    setEditorStatus('File saved.');
  } catch (err) {
    setEditorStatus('Unable to save file.', true);
  }
}

function initEditorControls(job, canWrite) {
  if (!EDITOR_WORKFLOWS.has(job.workflow)) return;
  editorState.jobId = job.id;
  editorState.rootId = null;
  editorState.cwd = '';
  editorState.openPath = '';
  editorState.dirty = false;
  editorState.canWrite = Boolean(canWrite);
  editorState.selectedPath = '';
  editorState.selectedType = '';
  ensureEditorMirror();
  const contentEl = document.getElementById('editorContent');
  if (contentEl) {
    if (!editorMirror) contentEl.value = '';
    contentEl.readOnly = !editorState.canWrite;
    contentEl.oninput = () => {
      editorState.dirty = true;
      updateEditorSaveState();
    };
  }
  if (editorMirror) {
    editorMirror.setOption('readOnly', !editorState.canWrite);
  }
  const saveBtn = document.getElementById('editorSave');
  if (saveBtn) {
    saveBtn.onclick = () => saveEditorFile(job.id);
    saveBtn.disabled = !editorState.canWrite;
  }
  const pathEl = document.getElementById('editorPath');
  if (pathEl) {
    pathEl.title = editorState.canWrite ? 'Current file path' : 'Read-only';
  }
  const newBtn = document.getElementById('editorNewFile');
  if (newBtn) {
    newBtn.disabled = !editorState.canWrite;
    newBtn.onclick = () => createEditorFile(job.id);
  }
  const renameBtn = document.getElementById('editorRename');
  if (renameBtn) {
    renameBtn.disabled = !editorState.canWrite;
    renameBtn.onclick = () => renameEditorEntry(job.id);
  }
  const moveBtn = document.getElementById('editorMove');
  if (moveBtn) {
    moveBtn.disabled = !editorState.canWrite;
    moveBtn.onclick = () => moveEditorEntry(job.id);
  }
  const newFolderBtn = document.getElementById('editorNewFolder');
  if (newFolderBtn) {
    newFolderBtn.disabled = !editorState.canWrite;
    newFolderBtn.onclick = () => createEditorFolder(job.id);
  }
  const deleteBtn = document.getElementById('editorDelete');
  if (deleteBtn) {
    deleteBtn.disabled = !editorState.canWrite;
    deleteBtn.onclick = () => deleteEditorEntry(job.id);
  }
  const reloadBtn = document.getElementById('editorReload');
  if (reloadBtn) reloadBtn.onclick = () => {
    if (editorState.openPath) {
      openEditorFile(job.id, editorState.rootId, editorState.openPath);
    } else if (editorState.rootId) {
      listEditorDir(job.id, editorState.rootId, editorState.cwd);
    }
  };
  const upBtn = document.getElementById('editorUpBtn');
  if (upBtn) {
    upBtn.onclick = () => {
      if (!editorState.cwd) return;
      const parent = editorState.cwd.includes('/') ? editorState.cwd.split('/').slice(0, -1).join('/') : '';
      listEditorDir(job.id, editorState.rootId, parent);
    };
  }
  updateEditorSaveState();
  loadEditorRoots(job);
}

function appendLog(entry) {
  const line = `[${formatAbsoluteTime(entry.ts)}] ${entry.line}`;
  const shouldScroll = autoScrollCheckbox.checked && logOutputEl.scrollTop + logOutputEl.clientHeight >= logOutputEl.scrollHeight - 24;
  logOutputEl.textContent += `${line}\n`;
  if (shouldScroll) {
    logOutputEl.scrollTop = logOutputEl.scrollHeight;
  }
}

async function postAction(jobId, action) {
  const res = await apiFetch(`/api/jobs/${jobId}/actions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
  if (!res.ok) {
    return;
  }
  const job = await res.json();
  renderJobDetail(job);
  await fetchJobs();
}

async function postTransfer(jobId, payload) {
  const res = await apiFetch(`/api/jobs/${jobId}/transfer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) {
    showJobStatus(data.details || data.error || 'Transfer action failed.', true);
    return null;
  }
  return data;
}

function buildPayload() {
  const workflow = workflowSelect.value;
  const payload = {
    workflow,
    project_id: projectIdEl?.value || '',
    project_name: document.getElementById('projectName').value.trim(),
    project_root: document.getElementById('projectRoot').value.trim(),
    create_project: document.getElementById('createProject').checked,
    requirements_text: document.getElementById('requirementsText').value.trim(),
    requirements_path: document.getElementById('requirementsPath').value.trim(),
    project_run: document.getElementById('projectRun').checked,
    topic_source: document.getElementById('topicSource')?.value.trim(),
    topic_output: document.getElementById('topicOutput')?.value.trim(),
    projects: document.getElementById('jiraProjects')?.value.trim(),
    jql: document.getElementById('jiraJql')?.value.trim(),
    action_plan: document.getElementById('jiraActionPlan')?.checked || document.getElementById('confluenceActionPlan')?.checked,
    dry_run: document.getElementById('jiraDryRun')?.checked || document.getElementById('confluenceDryRun')?.checked,
    space: document.getElementById('confluenceSpace')?.value.trim(),
    use_rovo: document.getElementById('useRovo')?.checked,
    delivery_project_root: document.getElementById('deliveryProjectRoot')?.value.trim(),
    delivery_config: document.getElementById('deliveryConfig')?.value.trim(),
    delivery_run: document.getElementById('deliveryRun')?.checked,
    delivery_allow_unfinished: document.getElementById('deliveryAllowUnfinished')?.checked,
    llm_provider: document.getElementById('llmProvider').value,
    llm_model: document.getElementById('llmModel').value.trim(),
    llm_temperature: parseFloat(document.getElementById('llmTemperature').value),
    llm_max_tokens: parseInt(document.getElementById('llmMaxTokens').value, 10),
    extra_args: document.getElementById('extraArgs').value.trim(),
    verbose: document.getElementById('verbose').checked,
    debug: document.getElementById('debug').checked,
    use_default_secrets: useDefaultSecretsEl ? useDefaultSecretsEl.checked : true,
  };
  const projectSource = getSelectedSource('project');
  const deliverySource = getSelectedSource('delivery');

  if (workflow === 'project_solver' && projectSource === 'github') {
    payload.repo_url = repoUrlEl?.value.trim();
    payload.repo_branch = repoBranchEl?.value.trim();
    payload.work_branch = workBranchEl?.value.trim();
    payload.repo_subdir = repoSubdirEl?.value.trim();
    payload.requirements_relpath = requirementsRelPathEl?.value.trim();
    payload.fork_org = forkOrgEl?.value.trim();
    payload.commit_message = commitMessageEl?.value.trim();
    payload.git_author_name = gitAuthorNameEl?.value.trim();
    payload.git_author_email = gitAuthorEmailEl?.value.trim();
    payload.project_root = '';
    payload.create_project = false;
    payload.requirements_path = '';
  }

  if (workflow === 'delivery_pipeline' && deliverySource === 'github') {
    payload.repo_url = deliveryRepoUrlEl?.value.trim();
    payload.repo_branch = deliveryRepoBranchEl?.value.trim();
    payload.work_branch = deliveryWorkBranchEl?.value.trim();
    payload.repo_subdir = deliveryRepoSubdirEl?.value.trim();
    payload.fork_org = deliveryForkOrgEl?.value.trim();
    payload.commit_message = deliveryCommitMessageEl?.value.trim();
    payload.git_author_email = deliveryAuthorEmailEl?.value.trim();
    payload.delivery_project_root = '';
  }
  if (!payload.project_id) delete payload.project_id;
  if (!payload.project_name) delete payload.project_name;
  if (!payload.project_root) delete payload.project_root;
  if (!payload.requirements_text) delete payload.requirements_text;
  if (!payload.requirements_path) delete payload.requirements_path;
  if (!payload.topic_source) delete payload.topic_source;
  if (!payload.topic_output) delete payload.topic_output;
  if (!payload.projects) delete payload.projects;
  if (!payload.jql) delete payload.jql;
  if (!payload.space) delete payload.space;
  if (!payload.delivery_project_root) delete payload.delivery_project_root;
  if (!payload.delivery_config) delete payload.delivery_config;
  if (!payload.llm_provider) delete payload.llm_provider;
  if (!payload.llm_model) delete payload.llm_model;
  if (!payload.topic_output) delete payload.topic_output;
  if (!payload.repo_url) delete payload.repo_url;
  if (!payload.repo_branch) delete payload.repo_branch;
  if (!payload.work_branch) delete payload.work_branch;
  if (!payload.repo_subdir) delete payload.repo_subdir;
  if (!payload.requirements_relpath) delete payload.requirements_relpath;
  if (!payload.fork_org) delete payload.fork_org;
  if (!payload.commit_message) delete payload.commit_message;
  if (!payload.git_author_name) delete payload.git_author_name;
  if (!payload.git_author_email) delete payload.git_author_email;
  if (Number.isNaN(payload.llm_temperature)) delete payload.llm_temperature;
  if (Number.isNaN(payload.llm_max_tokens)) delete payload.llm_max_tokens;
  if (!payload.extra_args) delete payload.extra_args;
  if (jobSecrets.length) {
    payload.job_secrets = jobSecrets;
  }
  return payload;
}

async function submitJob(event) {
  event.preventDefault();
  const payload = buildPayload();
  if (payload.workflow === 'project_solver' && getSelectedSource('project') === 'github' && !payload.repo_url) {
    showJobStatus('GitHub repo is required for GitHub source.', true);
    return;
  }
  if (payload.workflow === 'delivery_pipeline' && getSelectedSource('delivery') === 'github' && !payload.repo_url) {
    showJobStatus('GitHub repo is required for GitHub source.', true);
    return;
  }
  if (payload.workflow === 'delivery_pipeline' && payload.delivery_project_root) {
    payload.project_root = payload.delivery_project_root;
  }
  showJobStatus('Submitting job...');
  try {
    const res = await apiFetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    if (!res.ok) {
      let errData = {};
      try {
        errData = await res.json();
      } catch (err) {
        errData = {};
      }
      if (res.status === 402 && errData.error === 'insufficient_tokens') {
        showJobStatus(`Insufficient tokens: need ${errData.estimate}, available ${errData.available}.`, true);
      } else {
        showJobStatus(`Submission failed (status ${res.status}).`, true);
      }
      return;
    }
    const contentType = res.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      showJobStatus('Submission failed (unexpected response). Please re-login.', true);
      return;
    }
    const job = await res.json();
    await fetchJobs();
    selectJob(job.id).catch((err) => console.error('Failed to select job', err));
    jobSecrets = [];
    renderJobSecrets();
    showJobStatus('Job queued successfully.');
    pendingEstimate = null;
    clearTokenEstimateStatus();
    fetchTokens();
  } catch (err) {
    console.error('Job submission error', err);
    showJobStatus('Submission failed. Check console for details.', true);
  }
}

function resetForm() {
  document.getElementById('jobForm').reset();
  updateWorkflowSections();
  jobSecrets = [];
  renderJobSecrets();
  clearJobStatus();
  pendingEstimate = null;
  clearTokenEstimateStatus();
  persistFormState();
}

function persistFormState() {
  try {
    const form = document.getElementById('jobForm');
    if (!form) return;
    const state = {};
    const elements = form.querySelectorAll('input, select, textarea');
    const skipIds = new Set([
      'secretName',
      'secretValue',
      'jobSecretName',
      'jobSecretValue',
      'assistantInput',
      'formAssistantPrompt',
    ]);
    elements.forEach((el) => {
      if (el.type === 'password') return;
      if (skipIds.has(el.id)) return;
      if (el.type === 'radio') {
        if (el.checked) {
          state[el.name] = el.value;
        }
        return;
      }
      if (el.type === 'checkbox') {
        state[el.id] = el.checked;
        return;
      }
      if (el.id) {
        state[el.id] = el.value;
      }
    });
    localStorage.setItem('refiner_form_state', JSON.stringify(state));
  } catch (err) {
    // ignore storage errors
  }
}

function restoreFormState() {
  try {
    const raw = localStorage.getItem('refiner_form_state');
    if (!raw) return;
    const state = JSON.parse(raw);
    const form = document.getElementById('jobForm');
    if (!form) return;
    Object.keys(state).forEach((key) => {
      const value = state[key];
      const radios = form.querySelectorAll(`input[type="radio"][name="${key}"]`);
      if (radios.length) {
        radios.forEach((radio) => {
          radio.checked = String(radio.value) === String(value);
        });
        return;
      }
      const el = document.getElementById(key);
      if (!el) return;
      if (el.type === 'checkbox') {
        el.checked = Boolean(value);
      } else {
        el.value = value;
      }
    });
    updateWorkflowSections();
  } catch (err) {
    // ignore restore errors
  }
}

workflowSelect.addEventListener('change', updateWorkflowSections);
projectSourceInputs.forEach((input) => {
  input.addEventListener('change', () => updateSourcePanels('project'));
});
deliverySourceInputs.forEach((input) => {
  input.addEventListener('change', () => updateSourcePanels('delivery'));
});
document.getElementById('jobForm').addEventListener('submit', submitJob);
resetButton.addEventListener('click', resetForm);
clearLogsButton.addEventListener('click', () => {
  logOutputEl.textContent = '';
});

if (logoutLink) {
  logoutLink.addEventListener('click', async (event) => {
    event.preventDefault();
    try {
      await apiFetch('/api/logout', { method: 'POST' });
    } catch (err) {
      // ignore logout failures
    } finally {
      window.location.href = '/login';
    }
  });
}

applyCliArgsBtn.addEventListener('click', applyCliArgs);
clearCliArgsBtn.addEventListener('click', clearCliArgs);
document.addEventListener('click', (event) => {
  if (!bubblePopover.hidden && !bubblePopover.contains(event.target)) {
    closeBubblePopover();
  }
});
if (saveSecretBtn) {
  saveSecretBtn.addEventListener('click', submitSecret);
}
if (clearSecretFormBtn) {
  clearSecretFormBtn.addEventListener('click', () => {
    secretNameEl.value = '';
    secretValueEl.value = '';
  });
}
if (saveNotifyEmailBtn) {
  saveNotifyEmailBtn.addEventListener('click', () => {
    const email = (notifyEmailEl?.value || '').trim();
    updateNotifyEmail(email);
  });
}
if (clearNotifyEmailBtn) {
  clearNotifyEmailBtn.addEventListener('click', () => updateNotifyEmail(''));
}
if (reqExtractBtn) {
  reqExtractBtn.addEventListener('click', () => {
    clearReqGridStatus();
    if (!requirementsTextEl) return;
    const extracted = extractRequirementsFromText(requirementsTextEl.value);
    if (!extracted.length) {
      showReqGridStatus('No REQ items detected in the text.', true);
      return;
    }
    if (requirementsGrid.length) {
      const merge = window.confirm(
        'Merge extracted requirements with existing ones?\nOK = merge, Cancel = replace.'
      );
      if (merge) {
        requirementsGrid = mergeRequirementLists(requirementsGrid, extracted);
        renderRequirementsGrid();
        showReqGridStatus(`Merged ${extracted.length} requirement(s) into the register.`);
        return;
      }
    }
    requirementsGrid = extracted;
    renderRequirementsGrid();
    showReqGridStatus(`Detected ${extracted.length} requirement(s).`);
  });
}
if (reqExtractJobBtn) {
  reqExtractJobBtn.addEventListener('click', async () => {
    clearReqGridStatus();
    if (!selectedJobId) {
      showReqGridStatus('Select a job in Job Detail first.', true);
      return;
    }
    showReqGridStatus('Extracting requirements from job...');
    const summary = await loadRequirementSummary(selectedJobId, true);
    const items = summary?.items || [];
    const extracted = requirementsFromSummaryItems(items);
    if (!extracted.length) {
      showReqGridStatus('No requirements found in the selected job summary.', true);
      return;
    }
    if (requirementsGrid.length) {
      const merge = window.confirm(
        'Merge requirements from job summary with existing ones?\nOK = merge, Cancel = replace.'
      );
      if (merge) {
        requirementsGrid = mergeRequirementLists(requirementsGrid, extracted);
        renderRequirementsGrid();
        showReqGridStatus(`Merged ${extracted.length} requirement(s) from job summary.`);
        return;
      }
    }
    requirementsGrid = extracted;
    renderRequirementsGrid();
    showReqGridStatus(`Loaded ${extracted.length} requirement(s) from job summary.`);
  });
}
if (reqAddBtn) {
  reqAddBtn.addEventListener('click', () => {
    clearReqGridStatus();
    addRequirementRow();
  });
}
if (reqApplyBtn) {
  reqApplyBtn.addEventListener('click', () => {
    clearReqGridStatus();
    applyRequirementsToText();
  });
}
if (reqImportBtn && reqImportFileEl) {
  reqImportBtn.addEventListener('click', () => {
    reqImportFileEl.click();
  });
}
if (reqImportFileEl) {
  reqImportFileEl.addEventListener('change', () => {
    const file = reqImportFileEl.files && reqImportFileEl.files[0];
    if (file) {
      importRequirementsFile(file);
    }
    reqImportFileEl.value = '';
  });
}
if (reqExportBtn) {
  reqExportBtn.addEventListener('click', () => {
    const format = reqExportFormatEl?.value || 'csv';
    exportRequirements(format);
  });
}
if (browseActionButtons.length) {
  browseActionButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.browseTarget;
      const mode = btn.dataset.browseMode || 'file';
      const context = btn.dataset.browseContext || 'project';
      openRepoBrowser(targetId, mode, context);
    });
  });
}
if (closeRepoBrowserBtn) {
  closeRepoBrowserBtn.addEventListener('click', closeRepoBrowser);
}
if (repoSearchEl) {
  repoSearchEl.addEventListener('input', renderRepoList);
}
if (assistantAskBtn) {
  assistantAskBtn.addEventListener('click', () => sendAssistant('ask'));
}
if (assistantDraftBtn) {
  assistantDraftBtn.addEventListener('click', () => sendAssistant('draft'));
}
if (assistantInsertBtn) {
  assistantInsertBtn.addEventListener('click', insertAssistantResponse);
}
if (assistantClearBtn) {
  assistantClearBtn.addEventListener('click', clearAssistantChat);
}
if (formAssistantSuggestBtn) {
  formAssistantSuggestBtn.addEventListener('click', requestFormSuggestions);
}
if (formAssistantApplyAllBtn) {
  formAssistantApplyAllBtn.addEventListener('click', applyAllSuggestions);
}
if (formAssistantClearBtn) {
  formAssistantClearBtn.addEventListener('click', clearFormSuggestions);
}
if (addJobSecretBtn) {
  addJobSecretBtn.addEventListener('click', addJobSecret);
}
if (clearJobSecretBtn) {
  clearJobSecretBtn.addEventListener('click', clearJobSecretForm);
}
if (deleteQueueBtn) {
  deleteQueueBtn.addEventListener('click', () => deleteJobsBulk('queue'));
}
if (deleteArchiveBtn) {
  deleteArchiveBtn.addEventListener('click', () => deleteJobsBulk('archive'));
}
if (projectIdEl) {
  projectIdEl.addEventListener('change', () => {
    updateProjectRoleHint();
    fetchTokens();
    scheduleEstimate();
  });
}
if (transferModalCancelBtn) {
  transferModalCancelBtn.addEventListener('click', closeTransferModal);
}
if (transferModalCloseBtn) {
  transferModalCloseBtn.addEventListener('click', closeTransferModal);
}
if (transferModalEl) {
  transferModalEl.addEventListener('click', (event) => {
    if (event.target === transferModalEl) {
      closeTransferModal();
    }
  });
}
if (transferModalConfirmBtn) {
  transferModalConfirmBtn.addEventListener('click', async () => {
    if (!transferModalState?.job) return;
    const { mode, job } = transferModalState;
    clearTransferModalStatus();
    showTransferModalStatus('Submitting...');
    if (mode === 'invite') {
      const teamId = transferTeamSelectEl?.value;
      if (!teamId) {
        showTransferModalStatus('Select a team.', true);
        return;
      }
      const result = await postTransfer(job.id, { action: 'request', team_id: teamId });
      if (result) {
        closeTransferModal();
        renderJobDetail(result);
        await fetchJobs();
      }
      return;
    }
    if (mode === 'accept') {
      const projectId = transferProjectSelectEl?.value;
      const payload = { action: 'accept' };
      if (projectId) payload.project_id = projectId;
      const result = await postTransfer(job.id, payload);
      if (result) {
        closeTransferModal();
        renderJobDetail(result);
        await fetchJobs();
      }
      return;
    }
    if (mode === 'assign') {
      const targetUser = transferUserSelectEl?.value;
      if (!targetUser) {
        showTransferModalStatus('Select a user.', true);
        return;
      }
      const result = await postTransfer(job.id, { action: 'assign_user', target_user: targetUser });
      if (result) {
        closeTransferModal();
        renderJobDetail(result);
        await fetchJobs();
      }
    }
  });
}

initFilters();
initScopeFilters();
initTodoPanel();
updateWorkflowSections();
if (cliBubblesEl) {
  renderCliBubbles();
  renderCliPreview();
}
renderJobSecrets();
renderAssistantMessages();
renderFormSuggestions();
fetchSecrets();
fetchProfile();
fetchProjects();
fetchAccessTree();
fetchJobs();
fetchHealth();
fetchCapabilities();
fetchTokens();
setInterval(fetchHealth, 15000);
setInterval(fetchTokens, 12000);

tabButtons.forEach((btn) => {
  btn.addEventListener('click', () => setActiveTab(btn.dataset.tab));
});
const savedTab = localStorage.getItem('refiner_active_tab') || 'job';
setActiveTab(savedTab);
closeRepoBrowser();

const jobForm = document.getElementById('jobForm');
if (jobForm) {
  jobForm.addEventListener('input', () => {
    persistFormState();
    scheduleEstimate();
  });
  jobForm.addEventListener('change', () => {
    persistFormState();
    scheduleEstimate();
  });
}
if (requirementsTextEl) {
  requirementsTextEl.addEventListener('input', () => {
    extractRequirementsFromField();
  });
}
restoreFormState();
updateProjectRoleHint();
scheduleEstimate();
renderRequirementsGrid();
extractRequirementsFromField();

window.addEventListener('beforeunload', () => {
  stopSession();
});
