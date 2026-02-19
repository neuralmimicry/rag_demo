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
const statusFiltersEl = document.getElementById('statusFilters');
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
const tabButtons = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');
const jobSecretListEl = document.getElementById('jobSecretList');
const jobSecretNameEl = document.getElementById('jobSecretName');
const jobSecretValueEl = document.getElementById('jobSecretValue');
const addJobSecretBtn = document.getElementById('addJobSecret');
const clearJobSecretBtn = document.getElementById('clearJobSecret');
const useDefaultSecretsEl = document.getElementById('useDefaultSecrets');
const projectSourceInputs = document.querySelectorAll('input[name="projectSource"]');
const deliverySourceInputs = document.querySelectorAll('input[name="deliverySource"]');
const sourcePanels = document.querySelectorAll('.source-panel');

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
const closeRepoBrowserBtn = document.getElementById('closeRepoBrowser');
const repoBrowserListEl = document.getElementById('repoBrowserList');
const repoBrowserStatusEl = document.getElementById('repoBrowserStatus');
const repoSearchEl = document.getElementById('repoSearch');
const browseButtons = document.querySelectorAll('.browse-btn');

let repoBrowserState = {
  items: [],
  targetId: null,
  mode: 'file',
  context: 'project',
};

const assistantMessagesEl = document.getElementById('assistantMessages');
const assistantInputEl = document.getElementById('assistantInput');
const assistantAskBtn = document.getElementById('assistantAsk');
const assistantDraftBtn = document.getElementById('assistantDraft');
const assistantInsertBtn = document.getElementById('assistantInsert');
const assistantClearBtn = document.getElementById('assistantClear');
const assistantStatusEl = document.getElementById('assistantStatus');
const requirementsTextEl = document.getElementById('requirementsText');
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

const STATUS_OPTIONS = ['all', 'queued', 'running', 'paused', 'stopped', 'completed', 'failed'];

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
        <div class="secret-meta">${secret.masked} · updated ${secret.updated_at || '--'}</div>
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

function initFilters() {
  statusFiltersEl.innerHTML = '';
  STATUS_OPTIONS.forEach((status) => {
    const btn = document.createElement('button');
    btn.className = `filter-btn ${status === currentFilter ? 'active' : ''}`;
    btn.textContent = status;
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
}

function clearAssistantChat() {
  assistantState.messages = [];
  assistantState.lastResponse = '';
  renderAssistantMessages();
  clearAssistantStatus();
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

function formatTokens(metrics) {
  if (!metrics || !metrics.token_usage) return '--';
  const total = metrics.token_usage.total;
  return total ? total.toString() : '--';
}

function renderJobs() {
  const visible = jobs.filter((job) => currentFilter === 'all' || job.status === currentFilter);
  jobListEl.innerHTML = '';
  if (!visible.length) {
    jobListEl.innerHTML = '<p class="subtitle">No jobs found for this status.</p>';
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

    jobListEl.appendChild(card);
  });
}

async function fetchJobs() {
  try {
    const res = await apiFetch('/api/jobs');
    const data = await res.json();
    jobs = data.jobs || [];
    jobCountEl.textContent = jobs.length;
    renderJobs();
    if (selectedJobId) {
      const stillExists = jobs.find((job) => job.id === selectedJobId);
      if (!stillExists) {
        selectedJobId = null;
        renderJobDetail(null);
      } else {
        refreshSelectedJob();
      }
    }
  } catch (err) {
    console.error('Failed to fetch jobs', err);
  }
}

async function fetchHealth() {
  const res = await apiFetch('/api/health');
  const data = await res.json();
  workerCountEl.textContent = data.workers;
}

async function selectJob(jobId) {
  selectedJobId = jobId;
  renderJobs();
  const res = await apiFetch(`/api/jobs/${jobId}`);
  if (!res.ok) {
    return;
  }
  const job = await res.json();
  renderJobDetail(job);
  await loadLogs(jobId);
  startLogStream(jobId);
}

async function refreshSelectedJob() {
  if (!selectedJobId) return;
  const res = await apiFetch(`/api/jobs/${selectedJobId}`);
  if (!res.ok) return;
  const job = await res.json();
  renderJobDetail(job);
}

function renderJobDetail(job) {
  if (!job) {
    jobDetailEl.innerHTML = '';
    jobHintEl.style.display = 'block';
    return;
  }
  jobHintEl.style.display = 'none';
  const stages = job.stages || [];
  const stageHtml = stages.length
    ? stages.map((stage) => `<span class="stage ${stage.status}">${stage.name}: ${stage.status}</span>`).join('')
    : '<span class="stage">No stages yet</span>';
  const repoInfo = job.repo_info || {};
  const repoMeta = repoInfo.fork_org && repoInfo.fork_repo
    ? `${repoInfo.fork_org}/${repoInfo.fork_repo}`
    : (repoInfo.owner ? `${repoInfo.owner}/${repoInfo.repo}` : '--');
  const repoBranch = repoInfo.branch || '--';
  const repoUrl = repoInfo.repo_url || (repoInfo.fork_org && repoInfo.fork_repo ? `https://github.com/${repoInfo.fork_org}/${repoInfo.fork_repo}` : '');
  const repoLink = repoUrl ? `<a class="link-inline" href="${repoUrl}" target="_blank" rel="noopener">Open repo</a>` : '--';

  const detailHtml = `
    <div class="detail-grid">
      <div class="detail-card"><span class="label">Status</span><div class="value">${job.status}</div></div>
      <div class="detail-card"><span class="label">Workflow</span><div class="value">${job.workflow}</div></div>
      <div class="detail-card"><span class="label">Project</span><div class="value">${job.project_name || 'Untitled'}</div></div>
      <div class="detail-card"><span class="label">Started</span><div class="value">${job.started_at || '--'}</div></div>
      <div class="detail-card"><span class="label">Duration</span><div class="value">${formatDuration(job.metrics?.runtime_sec)}</div></div>
      <div class="detail-card"><span class="label">Tokens</span><div class="value">${formatTokens(job.metrics)}</div></div>
      <div class="detail-card"><span class="label">Errors</span><div class="value">${job.metrics?.errors ?? 0}</div></div>
      <div class="detail-card"><span class="label">Resolved</span><div class="value">${job.metrics?.resolved ?? 0}</div></div>
      <div class="detail-card"><span class="label">Restarts</span><div class="value">${job.restart_count ?? 0}</div></div>
      <div class="detail-card"><span class="label">Exit Code</span><div class="value">${job.exit_code ?? '--'}</div></div>
      <div class="detail-card"><span class="label">Queue Wait</span><div class="value">${formatDuration(job.metrics?.queue_wait_sec)}</div></div>
      <div class="detail-card"><span class="label">Repo</span><div class="value">${repoMeta}</div></div>
      <div class="detail-card"><span class="label">Repo Link</span><div class="value">${repoLink}</div></div>
      <div class="detail-card"><span class="label">Branch</span><div class="value">${repoBranch}</div></div>
    </div>
    <div>
      <h3>Stages</h3>
      <div class="stage-list">${stageHtml}</div>
    </div>
    <div class="actions">
      <button type="button" class="ghost" data-action="pause">Pause</button>
      <button type="button" class="ghost" data-action="resume">Resume</button>
      <button type="button" class="ghost" data-action="stop">Stop</button>
      <button type="button" class="primary" data-action="restart">Restart</button>
    </div>
  `;
  jobDetailEl.innerHTML = detailHtml;
  jobDetailEl.querySelectorAll('button[data-action]').forEach((btn) => {
    btn.addEventListener('click', () => postAction(job.id, btn.dataset.action));
  });
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

function appendLog(entry) {
  const line = `[${entry.ts}] ${entry.line}`;
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

function buildPayload() {
  const workflow = workflowSelect.value;
  const payload = {
    workflow,
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
      showJobStatus(`Submission failed (status ${res.status}).`, true);
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
if (browseButtons.length) {
  browseButtons.forEach((btn) => {
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

initFilters();
updateWorkflowSections();
if (cliBubblesEl) {
  renderCliBubbles();
  renderCliPreview();
}
renderJobSecrets();
renderAssistantMessages();
renderFormSuggestions();
fetchSecrets();
fetchJobs();
fetchHealth();
setInterval(fetchJobs, 4000);
setInterval(fetchHealth, 15000);

tabButtons.forEach((btn) => {
  btn.addEventListener('click', () => setActiveTab(btn.dataset.tab));
});
const savedTab = localStorage.getItem('refiner_active_tab') || 'job';
setActiveTab(savedTab);
closeRepoBrowser();

const jobForm = document.getElementById('jobForm');
if (jobForm) {
  jobForm.addEventListener('input', persistFormState);
  jobForm.addEventListener('change', persistFormState);
}
restoreFormState();
