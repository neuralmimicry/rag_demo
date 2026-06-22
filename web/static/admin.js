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
const aiOrchestrationSummaryEl = document.getElementById('aiOrchestrationSummary');
const aiProviderListEl = document.getElementById('aiProviderList');
const aiEngineListEl = document.getElementById('aiEngineList');
const aiModelListEl = document.getElementById('aiModelList');
const aiCandidateListEl = document.getElementById('aiCandidateList');
const aiOrchestrationStatusEl = document.getElementById('aiOrchestrationStatus');
const aiOrchestrationSearchEl = document.getElementById('aiOrchestrationSearch');
const aiOrchestrationRuntimeFilterEl = document.getElementById('aiOrchestrationRuntimeFilter');
const aiOrchestrationRefreshBtn = document.getElementById('aiOrchestrationRefresh');
const aiOrchestrationProbeEl = document.getElementById('aiOrchestrationProbe');
const aiOrchestrationExportJsonBtn = document.getElementById('aiOrchestrationExportJson');
const aiOrchestrationExportCsvBtn = document.getElementById('aiOrchestrationExportCsv');
const aiProviderMetaEl = document.getElementById('aiProviderMeta');
const aiEngineMetaEl = document.getElementById('aiEngineMeta');
const aiModelMetaEl = document.getElementById('aiModelMeta');
const aiCandidateMetaEl = document.getElementById('aiCandidateMeta');
const aiProviderSortEl = document.getElementById('aiProviderSort');
const aiEngineSortEl = document.getElementById('aiEngineSort');
const aiModelSortEl = document.getElementById('aiModelSort');
const aiCandidateSortEl = document.getElementById('aiCandidateSort');
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
const adminVersionEl = document.getElementById('adminVersion');
const assistantAnalyticsOwnerEl = document.getElementById('assistantAnalyticsOwner');
const assistantAnalyticsRouteEl = document.getElementById('assistantAnalyticsRoute');
const assistantAnalyticsChannelEl = document.getElementById('assistantAnalyticsChannel');
const assistantAnalyticsProfileEl = document.getElementById('assistantAnalyticsProfile');
const assistantAnalyticsSinceHoursEl = document.getElementById('assistantAnalyticsSinceHours');
const assistantAnalyticsLimitEl = document.getElementById('assistantAnalyticsLimit');
const assistantAnalyticsRefreshBtn = document.getElementById('assistantAnalyticsRefresh');
const assistantAnalyticsStatusEl = document.getElementById('assistantAnalyticsStatus');
const assistantAnalyticsSummaryEl = document.getElementById('assistantAnalyticsSummary');
const assistantAnalyticsRouteBreakdownEl = document.getElementById('assistantAnalyticsRouteBreakdown');
const assistantAnalyticsChannelBreakdownEl = document.getElementById('assistantAnalyticsChannelBreakdown');
const assistantAnalyticsProfileBreakdownEl = document.getElementById('assistantAnalyticsProfileBreakdown');
const assistantAnalyticsSentimentBreakdownEl = document.getElementById('assistantAnalyticsSentimentBreakdown');
const assistantAnalyticsProviderBreakdownEl = document.getElementById('assistantAnalyticsProviderBreakdown');
const assistantAnalyticsErrorBreakdownEl = document.getElementById('assistantAnalyticsErrorBreakdown');

let teams = [];
let projects = [];
let aiOrchestrationSnapshot = null;
let assistantAnalyticsSnapshot = null;
const AI_ORCHESTRATION_LIMIT = 50;

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

function setAdminVersion(payload) {
  if (!adminVersionEl || !payload) return;
  const version = typeof payload.version === 'string' ? payload.version.trim() : '';
  const release = payload.release_version != null ? String(payload.release_version).trim() : '';
  const build = payload.build != null ? String(payload.build).trim() : '';
  const commit = payload.commit != null ? String(payload.commit).trim() : '';
  const fallbackVersion = adminVersionEl.dataset.localVersion || '';
  const visibleVersion = version || fallbackVersion;
  if (!visibleVersion) return;

  adminVersionEl.textContent = visibleVersion;
  const titleParts = [];
  if (release) {
    titleParts.push(`Release ${release}`);
  }
  if (version) {
    titleParts.push(`Version ${version}`);
  } else if (!release) {
    titleParts.push(`Version ${visibleVersion}`);
  }
  if (build) {
    titleParts.push(`build ${build}`);
  }
  if (commit && commit !== 'unknown') {
    titleParts.push(`commit ${commit}`);
  }
  adminVersionEl.title = titleParts.join(' · ');
  adminVersionEl.setAttribute('aria-label', `Admin Console version ${visibleVersion}`);
}

async function fetchVersion() {
  if (!adminVersionEl) return;
  try {
    const res = await apiFetch('/api/version', { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    setAdminVersion(data);
  } catch (_err) {
    // Keep server-rendered fallback when version endpoint is unavailable.
  }
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

function formatInteger(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '--';
  return Math.round(num).toLocaleString('en-GB');
}

function formatPercent(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '--';
  const decimals = num > 0 && num < 0.995 ? 1 : 0;
  return `${(num * 100).toFixed(decimals)}%`;
}

function formatMetricNumber(value, digits = 2) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '--';
  return num.toFixed(digits);
}

function formatLatencyValue(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '--';
  return `${Math.round(num)} ms`;
}

function parseClampedInteger(value, fallback, min, max) {
  const parsed = Number.parseInt(String(value ?? ''), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(parsed, max));
}

function formatBytes(value) {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return '--';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let current = num;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  const digits = current >= 100 || index === 0 ? 0 : current >= 10 ? 1 : 2;
  return `${current.toFixed(digits)} ${units[index]}`;
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

function normalizeText(value) {
  return String(value ?? '').trim().toLowerCase();
}

function joinSearchParts(parts) {
  const flat = [];
  (parts || []).forEach((part) => {
    if (Array.isArray(part)) {
      part.forEach((inner) => flat.push(inner));
      return;
    }
    flat.push(part);
  });
  return flat
    .filter((part) => part !== null && part !== undefined && String(part).trim())
    .map((part) => String(part).toLowerCase())
    .join(' ');
}

function tagListHtml(items) {
  if (!Array.isArray(items) || !items.length) {
    return '<span class="subtitle">--</span>';
  }
  return items
    .map((item) => `<span class="admin-chip">${escapeHtml(String(item))}</span>`)
    .join('');
}

function formatTimestamp(value) {
  if (value === null || value === undefined || value === '') return '--';
  if (typeof value === 'number') return formatAuditTime(value);
  const parsed = parseTimestamp(value);
  if (parsed) return formatAbsoluteTimeFromMs(parsed);
  return String(value);
}

function setStatus(el, message, isError = false) {
  if (!el) return;
  el.textContent = message;
  el.hidden = !message;
  el.classList.toggle('error', Boolean(isError));
}

function compareText(left, right) {
  return String(left ?? '').localeCompare(String(right ?? ''), 'en', {
    sensitivity: 'base',
    numeric: true,
  });
}

function compareMaybeNumber(left, right, direction = 'asc') {
  const a = Number(left);
  const b = Number(right);
  const aFinite = Number.isFinite(a);
  const bFinite = Number.isFinite(b);
  if (!aFinite && !bFinite) return 0;
  if (!aFinite) return 1;
  if (!bFinite) return -1;
  return direction === 'desc' ? b - a : a - b;
}

function compareBoolean(left, right, direction = 'desc') {
  const a = left ? 1 : 0;
  const b = right ? 1 : 0;
  return direction === 'asc' ? a - b : b - a;
}

function normalizeTimeValue(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 1_000_000_000_000 ? value : value * 1000;
  }
  const parsed = parseTimestamp(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function compareMaybeTime(left, right, direction = 'desc') {
  const a = normalizeTimeValue(left);
  const b = normalizeTimeValue(right);
  const aFinite = Number.isFinite(a);
  const bFinite = Number.isFinite(b);
  if (!aFinite && !bFinite) return 0;
  if (!aFinite) return 1;
  if (!bFinite) return -1;
  return direction === 'asc' ? a - b : b - a;
}

function stableSort(items, comparator) {
  return (items || [])
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      const primary = comparator(left.item, right.item);
      return primary || (left.index - right.index);
    })
    .map((entry) => entry.item);
}

function aiCurrentFilters() {
  return {
    search: String(aiOrchestrationSearchEl?.value || '').trim(),
    runtime_state: aiOrchestrationRuntimeFilterEl?.value || 'all',
    provider_sort: aiProviderSortEl?.value || 'weight_desc',
    engine_sort: aiEngineSortEl?.value || 'availability_desc',
    model_sort: aiModelSortEl?.value || 'ready_desc',
    candidate_sort: aiCandidateSortEl?.value || 'success_desc',
  };
}

function aiRuntimeState(kind, item) {
  if (kind === 'engine') {
    if (item?.health?.ok === true || item?.available === true) return 'ok';
    if (item?.health?.ok === false || item?.available === false) return 'degraded';
    return 'unknown';
  }
  if (kind === 'candidate') {
    if (item?.health_ok === true) return 'ok';
    if (item?.health_ok === false) return 'degraded';
    return 'unknown';
  }
  if (kind === 'model') {
    if (item?.runtime_ready || item?.download_recommended || item?.fit_status === 'ready' || item?.fit_status === 'download_candidate') {
      return 'ok';
    }
    if (item?.fit_status) return 'degraded';
    return 'unknown';
  }
  return 'config';
}

function aiProviderSearchText(provider) {
  return joinSearchParts([
    provider?.name,
    provider?.provider,
    provider?.model,
    provider?.source,
    provider?.roles,
    provider?.specialties,
    provider?.base_url,
  ]);
}

function aiEngineSearchText(engine) {
  return joinSearchParts([
    engine?.name,
    engine?.type,
    engine?.roles,
    engine?.specialties,
    engine?.endpoint,
    engine?.socket_path,
    engine?.repo_root,
    engine?.health?.mode,
    engine?.health?.details?.reason,
    engine?.health?.details?.endpoint,
    engine?.health?.details?.socket_path,
  ]);
}

function aiCandidateSearchText(candidate) {
  return joinSearchParts([
    candidate?.candidate_id,
    candidate?.provider,
    candidate?.model,
    candidate?.specialties,
    candidate?.health_mode,
    candidate?.last_status,
    candidate?.last_error,
  ]);
}

function aiModelSearchText(model) {
  return joinSearchParts([
    model?.model,
    model?.sources,
    model?.capabilities,
    model?.matched_capabilities,
    model?.fit_status,
    model?.modality,
    model?.family,
    model?.quantization,
  ]);
}

function aiMatchesFilters(kind, item, filters) {
  const search = normalizeText(filters?.search);
  const runtimeState = filters?.runtime_state || 'all';
  let haystack = '';
  if (kind === 'provider') {
    haystack = aiProviderSearchText(item);
  } else if (kind === 'engine') {
    haystack = aiEngineSearchText(item);
  } else if (kind === 'model') {
    haystack = aiModelSearchText(item);
  } else {
    haystack = aiCandidateSearchText(item);
  }
  if (search && !haystack.includes(search)) {
    return false;
  }
  if (runtimeState !== 'all' && kind !== 'provider') {
    return aiRuntimeState(kind, item) === runtimeState;
  }
  return true;
}

function sortAiProviders(items, sortKey) {
  return stableSort(items, (left, right) => {
    switch (sortKey) {
      case 'preferred_first':
        return (
          compareBoolean(left?.preferred, right?.preferred, 'desc')
          || compareMaybeNumber(left?.weight, right?.weight, 'desc')
          || compareText(left?.name || left?.provider, right?.name || right?.provider)
        );
      case 'name_asc':
        return (
          compareText(left?.name || left?.provider, right?.name || right?.provider)
          || compareText(left?.model, right?.model)
        );
      case 'provider_asc':
        return (
          compareText(left?.provider, right?.provider)
          || compareText(left?.model, right?.model)
          || compareText(left?.name, right?.name)
        );
      case 'weight_desc':
      default:
        return (
          compareMaybeNumber(left?.weight, right?.weight, 'desc')
          || compareBoolean(left?.preferred, right?.preferred, 'desc')
          || compareText(left?.name || left?.provider, right?.name || right?.provider)
        );
    }
  });
}

function sortAiModels(items, sortKey) {
  return stableSort(items, (left, right) => {
    switch (sortKey) {
      case 'relevance_desc':
        return (
          compareMaybeNumber(left?.relevance_score, right?.relevance_score, 'desc')
          || compareBoolean(left?.runtime_ready, right?.runtime_ready, 'desc')
          || compareText(left?.model, right?.model)
        );
      case 'size_asc':
        return (
          compareMaybeNumber(left?.size_bytes, right?.size_bytes, 'asc')
          || compareBoolean(left?.runtime_ready, right?.runtime_ready, 'desc')
          || compareText(left?.model, right?.model)
        );
      case 'name_asc':
        return compareText(left?.model, right?.model);
      case 'ready_desc':
      default:
        return (
          compareBoolean(left?.runtime_ready, right?.runtime_ready, 'desc')
          || compareBoolean(left?.download_recommended, right?.download_recommended, 'desc')
          || compareMaybeNumber(left?.relevance_score, right?.relevance_score, 'desc')
          || compareMaybeNumber(left?.size_bytes, right?.size_bytes, 'asc')
          || compareText(left?.model, right?.model)
        );
    }
  });
}

function sortAiEngines(items, sortKey) {
  return stableSort(items, (left, right) => {
    switch (sortKey) {
      case 'latency_asc':
        return (
          compareMaybeNumber(left?.health?.latency_ms, right?.health?.latency_ms, 'asc')
          || compareBoolean(left?.available, right?.available, 'desc')
          || compareText(left?.name || left?.type, right?.name || right?.type)
        );
      case 'name_asc':
        return compareText(left?.name || left?.type, right?.name || right?.type);
      case 'type_asc':
        return (
          compareText(left?.type, right?.type)
          || compareText(left?.name, right?.name)
        );
      case 'availability_desc':
      default:
        return (
          compareBoolean(left?.available, right?.available, 'desc')
          || compareBoolean(left?.health?.ok, right?.health?.ok, 'desc')
          || compareMaybeNumber(left?.health?.latency_ms, right?.health?.latency_ms, 'asc')
          || compareText(left?.name || left?.type, right?.name || right?.type)
        );
    }
  });
}

function sortAiCandidates(items, sortKey) {
  return stableSort(items, (left, right) => {
    switch (sortKey) {
      case 'quality_desc':
        return (
          compareMaybeNumber(left?.ewma_quality, right?.ewma_quality, 'desc')
          || compareMaybeNumber(left?.success_rate, right?.success_rate, 'desc')
          || compareText(left?.candidate_id, right?.candidate_id)
        );
      case 'latency_asc':
        return (
          compareMaybeNumber(left?.ewma_latency_ms, right?.ewma_latency_ms, 'asc')
          || compareMaybeNumber(left?.success_rate, right?.success_rate, 'desc')
          || compareText(left?.candidate_id, right?.candidate_id)
        );
      case 'updated_desc':
        return (
          compareMaybeTime(left?.updated_at, right?.updated_at, 'desc')
          || compareMaybeNumber(left?.success_rate, right?.success_rate, 'desc')
          || compareText(left?.candidate_id, right?.candidate_id)
        );
      case 'name_asc':
        return compareText(left?.candidate_id || left?.provider, right?.candidate_id || right?.provider);
      case 'success_desc':
      default:
        return (
          compareMaybeNumber(left?.success_rate, right?.success_rate, 'desc')
          || compareMaybeNumber(left?.total, right?.total, 'desc')
          || compareMaybeNumber(left?.ewma_quality, right?.ewma_quality, 'desc')
          || compareText(left?.candidate_id, right?.candidate_id)
        );
    }
  });
}

function aiViewFor(data) {
  const filters = aiCurrentFilters();
  const providersRaw = Array.isArray(data?.providers) ? data.providers : [];
  const enginesRaw = Array.isArray(data?.engines) ? data.engines : [];
  const modelsRaw = Array.isArray(data?.model_inventory?.models) ? data.model_inventory.models : [];
  const candidatesRaw = Array.isArray(data?.metrics?.candidates) ? data.metrics.candidates : [];
  const providers = sortAiProviders(
    providersRaw.filter((item) => aiMatchesFilters('provider', item, filters)),
    filters.provider_sort,
  );
  const engines = sortAiEngines(
    enginesRaw.filter((item) => aiMatchesFilters('engine', item, filters)),
    filters.engine_sort,
  );
  const models = sortAiModels(
    modelsRaw.filter((item) => aiMatchesFilters('model', item, filters)),
    filters.model_sort,
  );
  const candidates = sortAiCandidates(
    candidatesRaw.filter((item) => aiMatchesFilters('candidate', item, filters)),
    filters.candidate_sort,
  );
  return {
    filters,
    providers,
    providers_total: providersRaw.length,
    engines,
    engines_total: enginesRaw.length,
    models,
    models_total: modelsRaw.length,
    candidates,
    candidates_total: candidatesRaw.length,
  };
}

function renderAiSectionMeta(el, visibleCount, totalCount) {
  if (!el) return;
  el.textContent = totalCount > 0
    ? `${formatInteger(visibleCount)} visible of ${formatInteger(totalCount)}`
    : 'No records available';
}

function downloadTextFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function exportStamp() {
  return new Date().toISOString().replace(/[:.]/g, '-');
}

function aiExportPayload() {
  if (!aiOrchestrationSnapshot) return null;
  const view = aiViewFor(aiOrchestrationSnapshot);
  return {
    exported_at: new Date().toISOString(),
    filters: view.filters,
    source: {
      config_path: aiOrchestrationSnapshot?.config_path || null,
      fetched_at: aiOrchestrationSnapshot?.fetched_at || null,
      probe_engines: Boolean(aiOrchestrationSnapshot?.probe_engines),
      limit: aiOrchestrationSnapshot?.limit ?? AI_ORCHESTRATION_LIMIT,
      selection_mode: aiOrchestrationSnapshot?.selection_mode || null,
      max_parallel_candidates: aiOrchestrationSnapshot?.max_parallel_candidates ?? null,
      metrics_path: aiOrchestrationSnapshot?.metrics?.path || null,
      model_inventory_path: aiOrchestrationSnapshot?.model_inventory?.path || null,
    },
    visible_counts: {
      providers: view.providers.length,
      engines: view.engines.length,
      models: view.models.length,
      candidates: view.candidates.length,
    },
    providers: view.providers,
    engines: view.engines,
    models: view.models,
    candidates: view.candidates,
  };
}

function aiCsvValue(value) {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function aiExportCsv() {
  const payload = aiExportPayload();
  if (!payload) return null;
  const rows = [
    [
      'section',
      'name',
      'provider',
      'model',
      'type',
      'source',
      'status',
      'roles',
      'specialties',
      'weight',
      'preferred',
      'available',
      'health_ok',
      'health_mode',
      'latency_ms',
      'success_rate',
      'ewma_quality',
      'ewma_latency_ms',
      'total',
      'last_status',
      'updated_at',
      'location',
      'matched_capabilities',
      'required_ram_bytes',
      'download_recommended',
      'relevance_score',
    ],
  ];

  payload.providers.forEach((provider) => {
    rows.push([
      'provider',
      provider?.name || '',
      provider?.provider || '',
      provider?.model || '',
      '',
      provider?.source || '',
      '',
      (provider?.roles || []).join('; '),
      (provider?.specialties || []).join('; '),
      provider?.weight ?? '',
      provider?.preferred ? 'yes' : 'no',
      '',
      '',
      '',
      '',
      '',
      '',
      '',
      '',
      '',
      '',
      provider?.base_url || '',
      '',
      '',
      '',
      '',
    ]);
  });

  payload.engines.forEach((engine) => {
    rows.push([
      'engine',
      engine?.name || '',
      '',
      '',
      engine?.type || '',
      '',
      '',
      (engine?.roles || []).join('; '),
      (engine?.specialties || []).join('; '),
      '',
      '',
      engine?.available ? 'yes' : 'no',
      engine?.health?.ok === true ? 'yes' : engine?.health?.ok === false ? 'no' : '',
      engine?.health?.mode || '',
      engine?.health?.latency_ms ?? '',
      '',
      '',
      '',
      '',
      '',
      '',
      engine?.endpoint || engine?.socket_path || engine?.repo_root || '',
      '',
      '',
      '',
      '',
    ]);
  });

  payload.models.forEach((model) => {
    rows.push([
      'model',
      model?.model || '',
      'ollama',
      model?.model || '',
      model?.modality || '',
      (model?.sources || []).join('; '),
      model?.fit_status || '',
      '',
      (model?.capabilities || []).join('; '),
      '',
      '',
      model?.installed ? 'yes' : 'no',
      model?.runtime_ready ? 'yes' : model?.fits_memory === false ? 'no' : '',
      model?.fit_status || '',
      '',
      '',
      '',
      '',
      '',
      '',
      '',
      (model?.matched_capabilities || []).join('; '),
      model?.required_ram_bytes ?? '',
      model?.download_recommended ? 'yes' : 'no',
      model?.relevance_score ?? '',
    ]);
  });

  payload.candidates.forEach((candidate) => {
    rows.push([
      'candidate',
      candidate?.candidate_id || '',
      candidate?.provider || '',
      candidate?.model || '',
      '',
      '',
      '',
      '',
      (candidate?.specialties || []).join('; '),
      '',
      '',
      '',
      candidate?.health_ok === true ? 'yes' : candidate?.health_ok === false ? 'no' : '',
      candidate?.health_mode || '',
      '',
      candidate?.success_rate ?? '',
      candidate?.ewma_quality ?? '',
      candidate?.ewma_latency_ms ?? '',
      candidate?.total ?? '',
      candidate?.last_status || '',
      candidate?.updated_at ?? '',
      '',
      '',
      '',
      '',
      '',
    ]);
  });

  return rows.map((row) => row.map(aiCsvValue).join(',')).join('\n');
}

function assistantAnalyticsFilters() {
  const owner = String(assistantAnalyticsOwnerEl?.value || '').trim();
  const route = String(assistantAnalyticsRouteEl?.value || '').trim();
  const channel = String(assistantAnalyticsChannelEl?.value || '').trim().toLowerCase();
  const assistantProfile = String(assistantAnalyticsProfileEl?.value || '').trim().toLowerCase();
  const sinceHours = parseClampedInteger(assistantAnalyticsSinceHoursEl?.value, 24, 1, 720);
  const limit = parseClampedInteger(assistantAnalyticsLimitEl?.value, 2000, 1, 10000);
  if (assistantAnalyticsSinceHoursEl) assistantAnalyticsSinceHoursEl.value = String(sinceHours);
  if (assistantAnalyticsLimitEl) assistantAnalyticsLimitEl.value = String(limit);
  return {
    owner,
    route,
    channel,
    assistant_profile: assistantProfile,
    since_hours: sinceHours,
    limit,
  };
}

function renderAssistantAnalyticsBreakdown(el, rows, totalTraces, emptyText) {
  if (!el) return;
  const entries = Array.isArray(rows) ? rows.slice(0, 8) : [];
  if (!entries.length) {
    el.innerHTML = `<p class="subtitle">${escapeHtml(emptyText)}</p>`;
    return;
  }
  el.innerHTML = '';
  entries.forEach((entry) => {
    const count = Number(entry?.count) || 0;
    const share = totalTraces > 0 ? formatPercent(count / totalTraces) : '--';
    const row = document.createElement('div');
    row.className = 'admin-list-item';
    const label = document.createElement('div');
    label.textContent = String(entry?.label || 'unknown');
    const meta = document.createElement('small');
    meta.textContent = `${formatInteger(count)} · ${share}`;
    row.appendChild(label);
    row.appendChild(meta);
    el.appendChild(row);
  });
}

function renderAssistantAnalyticsPanel(snapshot) {
  const analytics = snapshot?.analytics || {};
  const filters = snapshot?.filters || {};
  const breakdowns = analytics?.breakdowns || {};
  const totalTraces = Number(analytics?.total_traces) || 0;

  if (assistantAnalyticsSummaryEl) {
    const cards = [
      {
        label: 'Traces',
        value: formatInteger(totalTraces),
        meta: `Window ${formatInteger(filters?.since_hours || analytics?.window_hours || 24)}h`,
      },
      {
        label: 'Success Rate',
        value: formatPercent(analytics?.success_rate),
        meta: 'Completed responses',
      },
      {
        label: 'Cache Hit Rate',
        value: formatPercent(analytics?.cache_hit_rate),
        meta: 'Semantic cache reuse',
      },
      {
        label: 'Handoff Rate',
        value: formatPercent(analytics?.handoff_rate),
        meta: 'Human handoff requests',
      },
      {
        label: 'Conversion Rate',
        value: formatPercent(analytics?.conversion_rate),
        meta: 'Completed conversion markers',
      },
      {
        label: 'Average Duration',
        value: formatLatencyValue(analytics?.avg_duration_ms),
        meta: 'Trace duration',
      },
    ];
    assistantAnalyticsSummaryEl.innerHTML = cards
      .map(
        (card) => `
          <div class="admin-status-card">
            <span>${escapeHtml(card.label)}<small>${escapeHtml(card.meta)}</small></span>
            <strong>${escapeHtml(card.value)}</strong>
          </div>
        `,
      )
      .join('');
  }

  renderAssistantAnalyticsBreakdown(
    assistantAnalyticsRouteBreakdownEl,
    breakdowns?.route,
    totalTraces,
    'No route data available.',
  );
  renderAssistantAnalyticsBreakdown(
    assistantAnalyticsChannelBreakdownEl,
    breakdowns?.channel,
    totalTraces,
    'No channel data available.',
  );
  renderAssistantAnalyticsBreakdown(
    assistantAnalyticsProfileBreakdownEl,
    breakdowns?.assistant_profile,
    totalTraces,
    'No assistant profile data available.',
  );
  renderAssistantAnalyticsBreakdown(
    assistantAnalyticsSentimentBreakdownEl,
    breakdowns?.sentiment,
    totalTraces,
    'No sentiment data available.',
  );
  renderAssistantAnalyticsBreakdown(
    assistantAnalyticsProviderBreakdownEl,
    breakdowns?.provider,
    totalTraces,
    'No provider data available.',
  );
  renderAssistantAnalyticsBreakdown(
    assistantAnalyticsErrorBreakdownEl,
    breakdowns?.error_code,
    totalTraces,
    'No error data recorded.',
  );
}

async function loadAssistantAnalytics({ silent = false } = {}) {
  if (
    !assistantAnalyticsSummaryEl
    && !assistantAnalyticsRouteBreakdownEl
    && !assistantAnalyticsStatusEl
  ) {
    return;
  }
  if (!silent) {
    setStatus(assistantAnalyticsStatusEl, 'Loading assistant analytics...', false);
  }
  const filters = assistantAnalyticsFilters();
  try {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value === null || value === undefined || value === '') return;
      params.set(key, String(value));
    });
    const res = await apiFetch(`/api/admin/assistant/analytics?${params.toString()}`, { cache: 'no-store' });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    const data = await res.json();
    if (res.status === 403) {
      setStatus(assistantAnalyticsStatusEl, 'Admin access required.', true);
      return;
    }
    if (!res.ok) {
      setStatus(
        assistantAnalyticsStatusEl,
        data?.message || data?.details || data?.error || 'Unable to load assistant analytics.',
        true,
      );
      return;
    }
    assistantAnalyticsSnapshot = data;
    renderAssistantAnalyticsPanel(assistantAnalyticsSnapshot);
    if (!silent) {
      setStatus(assistantAnalyticsStatusEl, 'Assistant analytics refreshed.', false);
    }
  } catch (err) {
    console.error(err);
    setStatus(assistantAnalyticsStatusEl, 'Unable to load assistant analytics.', true);
  }
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

function renderAiOrchestrationSummary(data, view) {
  if (!aiOrchestrationSummaryEl) return;
  const metrics = data?.metrics || {};
  const modelInventory = data?.model_inventory || {};
  const modelCounts = modelInventory?.counts || {};
  const modelMonitor = modelInventory?.monitor || {};
  const filters = view?.filters || {};
  const filterSummary = filters.search
    ? `Search: ${filters.search}`
    : filters.runtime_state === 'all'
      ? 'No runtime filter'
      : filters.runtime_state === 'ok'
        ? 'Runtime: healthy / available'
        : 'Runtime: degraded / unavailable';
  const cards = [
    {
      label: 'Orchestration',
      value: data?.enabled ? 'Enabled' : 'Disabled',
      meta: data?.selection_mode ? `Mode ${data.selection_mode}` : 'No selection mode',
    },
    {
      label: 'Configured Providers',
      value: formatInteger(data?.provider_count),
      meta: `${formatInteger(view?.providers?.length)} visible of ${formatInteger(view?.providers_total)}`,
    },
    {
      label: 'Configured Engines',
      value: formatInteger(data?.engine_count),
      meta: `${formatInteger(view?.engines?.length)} visible of ${formatInteger(view?.engines_total)}`,
    },
    {
      label: 'Ready Local Models',
      value: formatInteger(modelCounts?.ready_models),
      meta: `${formatInteger(view?.models?.length)} visible of ${formatInteger(view?.models_total)}`,
    },
    {
      label: 'Download Shortlist',
      value: formatInteger(modelCounts?.download_candidates),
      meta: modelInventory?.provider?.auto_pull_guard ? 'Auto-pull guarded' : 'Auto-pull allowed',
    },
    {
      label: 'Parallel Candidates',
      value: formatInteger(data?.max_parallel_candidates),
      meta: `Health TTL ${formatDuration(Number(data?.health_ttl_seconds || 0))}`,
    },
    {
      label: 'Tracked Candidates',
      value: formatInteger(metrics?.candidate_count),
      meta: `${formatInteger(view?.candidates?.length)} visible of ${formatInteger(view?.candidates_total)}`,
    },
    {
      label: 'Healthy / Degraded',
      value: `${formatInteger(metrics?.healthy_candidates)} / ${formatInteger(metrics?.degraded_candidates)}`,
      meta: metrics?.exists ? 'Metrics file present' : 'Metrics file missing',
    },
    {
      label: 'Metrics Updated',
      value: formatTimestamp(metrics?.updated_at),
      meta: data?.fetched_at ? `Fetched ${formatTimestamp(data.fetched_at)}` : 'No fetch time',
    },
    {
      label: 'Inventory Updated',
      value: formatTimestamp(modelInventory?.generated_at),
      meta: modelMonitor?.running ? `Monitor polling every ${formatDuration(Number(modelMonitor.poll_sec || 0))}` : 'Monitor idle',
    },
    {
      label: 'Candidate Limit',
      value: formatInteger(data?.limit),
      meta: filterSummary,
    },
  ];
  aiOrchestrationSummaryEl.innerHTML = cards
    .map(
      (card) => `
        <div class="admin-status-card">
          <span>${escapeHtml(card.label)}<small>${escapeHtml(card.meta)}</small></span>
          <strong>${escapeHtml(card.value)}</strong>
        </div>
      `,
    )
    .join('');
}

function renderAiProviderList(items, totalCount) {
  if (!aiProviderListEl) return;
  renderAiSectionMeta(aiProviderMetaEl, items.length, totalCount);
  if (!Array.isArray(items) || !items.length) {
    aiProviderListEl.innerHTML = totalCount > 0
      ? '<p class="subtitle">No providers match the current filters.</p>'
      : '<p class="subtitle">No orchestrated provider registry entries configured.</p>';
    return;
  }
  aiProviderListEl.innerHTML = '';
  items.forEach((provider) => {
    const card = document.createElement('div');
    card.className = 'admin-item';
    card.innerHTML = `
      <div class="admin-item-head">
        <strong>${escapeHtml(provider.name || provider.provider || 'provider')}</strong>
        <span class="subtitle">${escapeHtml(provider.provider || 'unknown')} · ${escapeHtml(provider.model || 'default')} · ${escapeHtml(provider.source || 'config')}</span>
      </div>
      <div class="admin-form-grid">
        <div class="field grid-2">
          <div>
            <label>Roles</label>
            <div class="admin-chip-list">${tagListHtml(provider.roles)}</div>
          </div>
          <div>
            <label>Specialties</label>
            <div class="admin-chip-list">${tagListHtml(provider.specialties)}</div>
          </div>
        </div>
        <div class="field grid-3">
          <div>
            <label>Weight</label>
            <div class="input-static">${escapeHtml(formatMetricNumber(provider.weight, 2))}</div>
          </div>
          <div>
            <label>Preferred</label>
            <div class="input-static">${provider.preferred ? 'yes' : 'no'}</div>
          </div>
          <div>
            <label>Base URL</label>
            <div class="input-static">${escapeHtml(provider.base_url || '--')}</div>
          </div>
        </div>
      </div>
    `;
    aiProviderListEl.appendChild(card);
  });
}

function renderAiEngineList(items, totalCount) {
  if (!aiEngineListEl) return;
  renderAiSectionMeta(aiEngineMetaEl, items.length, totalCount);
  if (!Array.isArray(items) || !items.length) {
    aiEngineListEl.innerHTML = totalCount > 0
      ? '<p class="subtitle">No engines match the current filters.</p>'
      : '<p class="subtitle">No specialist engine registry entries configured.</p>';
    return;
  }
  aiEngineListEl.innerHTML = '';
  items.forEach((engine) => {
    const health = engine?.health || {};
    const details = health?.details || {};
    const location = engine.endpoint || engine.socket_path || engine.repo_root || '--';
    const card = document.createElement('div');
    card.className = 'admin-item';
    card.innerHTML = `
      <div class="admin-item-head">
        <strong>${escapeHtml(engine.name || engine.type || 'engine')}</strong>
        <span class="subtitle">${escapeHtml(engine.type || 'unknown')} · ${engine.available ? 'available' : 'unavailable'} · ${escapeHtml(health.mode || '--')}</span>
      </div>
      <div class="admin-form-grid">
        <div class="field grid-2">
          <div>
            <label>Roles</label>
            <div class="admin-chip-list">${tagListHtml(engine.roles)}</div>
          </div>
          <div>
            <label>Specialties</label>
            <div class="admin-chip-list">${tagListHtml(engine.specialties)}</div>
          </div>
        </div>
        <div class="field grid-3">
          <div>
            <label>Location</label>
            <div class="input-static">${escapeHtml(location)}</div>
          </div>
          <div>
            <label>Health</label>
            <div class="input-static">${health.ok === true ? 'ok' : health.ok === false ? 'degraded' : '--'}</div>
          </div>
          <div>
            <label>Latency</label>
            <div class="input-static">${escapeHtml(formatLatencyValue(health.latency_ms))}</div>
          </div>
        </div>
        <div class="field grid-2">
          <div>
            <label>AER Bases</label>
            <div class="input-static">${escapeHtml(`${engine.aer_sensory_base ?? '--'} / ${engine.aer_output_base ?? '--'}`)}</div>
          </div>
          <div>
            <label>Health Detail</label>
            <div class="input-static">${escapeHtml(details.reason || details.endpoint || details.socket_path || '--')}</div>
          </div>
        </div>
      </div>
    `;
    aiEngineListEl.appendChild(card);
  });
}

function renderAiModelList(items, totalCount) {
  if (!aiModelListEl) return;
  renderAiSectionMeta(aiModelMetaEl, items.length, totalCount);
  if (!Array.isArray(items) || !items.length) {
    aiModelListEl.innerHTML = totalCount > 0
      ? '<p class="subtitle">No local models match the current filters.</p>'
      : '<p class="subtitle">No cached local model inventory yet.</p>';
    return;
  }
  aiModelListEl.innerHTML = '';
  items.forEach((model) => {
    const sourceLabel = Array.isArray(model?.sources) && model.sources.length ? model.sources.join(' · ') : '--';
    const card = document.createElement('div');
    card.className = 'admin-item';
    card.innerHTML = `
      <div class="admin-item-head">
        <strong>${escapeHtml(model.model || 'model')}</strong>
        <span class="subtitle">${model.runtime_ready ? 'ready' : model.download_recommended ? 'download candidate' : escapeHtml(model.fit_status || 'unknown')} · ${escapeHtml(model.modality || 'text')} · ${escapeHtml(sourceLabel)}</span>
      </div>
      <div class="admin-form-grid">
        <div class="field grid-2">
          <div>
            <label>Capabilities</label>
            <div class="admin-chip-list">${tagListHtml(model.capabilities)}</div>
          </div>
          <div>
            <label>Matches Need</label>
            <div class="admin-chip-list">${tagListHtml(model.matched_capabilities)}</div>
          </div>
        </div>
        <div class="field grid-3">
          <div>
            <label>Installed</label>
            <div class="input-static">${model.installed ? 'yes' : 'no'}</div>
          </div>
          <div>
            <label>Size</label>
            <div class="input-static">${escapeHtml(formatBytes(model.size_bytes))}</div>
          </div>
          <div>
            <label>RAM Needed</label>
            <div class="input-static">${escapeHtml(formatBytes(model.required_ram_bytes))}</div>
          </div>
        </div>
        <div class="field grid-3">
          <div>
            <label>Fits Memory</label>
            <div class="input-static">${model.fits_memory === true ? 'yes' : model.fits_memory === false ? 'no' : '--'}</div>
          </div>
          <div>
            <label>Fits Disk</label>
            <div class="input-static">${model.fits_disk === true ? 'yes' : model.fits_disk === false ? 'no' : '--'}</div>
          </div>
          <div>
            <label>Relevance</label>
            <div class="input-static">${escapeHtml(formatMetricNumber(model.relevance_score, 2))}</div>
          </div>
        </div>
      </div>
    `;
    aiModelListEl.appendChild(card);
  });
}

function renderAiCandidateList(items, totalCount) {
  if (!aiCandidateListEl) return;
  renderAiSectionMeta(aiCandidateMetaEl, items.length, totalCount);
  if (!Array.isArray(items) || !items.length) {
    aiCandidateListEl.innerHTML = totalCount > 0
      ? '<p class="subtitle">No candidates match the current filters.</p>'
      : '<p class="subtitle">No candidate telemetry recorded yet.</p>';
    return;
  }
  aiCandidateListEl.innerHTML = '';
  items.forEach((candidate) => {
    const card = document.createElement('div');
    card.className = 'admin-item';
    card.innerHTML = `
      <div class="admin-item-head">
        <strong>${escapeHtml(candidate.candidate_id || `${candidate.provider || 'provider'}/${candidate.model || 'default'}`)}</strong>
        <span class="subtitle">${escapeHtml(candidate.provider || 'unknown')} · ${escapeHtml(candidate.model || 'default')} · ${escapeHtml(candidate.health_mode || '--')}</span>
      </div>
      <div class="admin-form-grid">
        <div class="field grid-3">
          <div>
            <label>Total</label>
            <div class="input-static">${escapeHtml(formatInteger(candidate.total))}</div>
          </div>
          <div>
            <label>Success Rate</label>
            <div class="input-static">${escapeHtml(formatPercent(candidate.success_rate))}</div>
          </div>
          <div>
            <label>Health</label>
            <div class="input-static">${candidate.health_ok === true ? 'ok' : candidate.health_ok === false ? 'degraded' : '--'}</div>
          </div>
        </div>
        <div class="field grid-3">
          <div>
            <label>EWMA Latency</label>
            <div class="input-static">${escapeHtml(formatLatencyValue(candidate.ewma_latency_ms))}</div>
          </div>
          <div>
            <label>EWMA Quality</label>
            <div class="input-static">${escapeHtml(formatMetricNumber(candidate.ewma_quality, 2))}</div>
          </div>
          <div>
            <label>Last Status</label>
            <div class="input-static">${escapeHtml(candidate.last_status || '--')}</div>
          </div>
        </div>
        <div class="field grid-2">
          <div>
            <label>Specialties</label>
            <div class="admin-chip-list">${tagListHtml(candidate.specialties)}</div>
          </div>
          <div>
            <label>Updated</label>
            <div class="input-static">${escapeHtml(formatTimestamp(candidate.updated_at))}</div>
          </div>
        </div>
      </div>
    `;
    aiCandidateListEl.appendChild(card);
  });
}

function renderAiOrchestrationPanel(data) {
  if (!data) return;
  const view = aiViewFor(data);
  renderAiOrchestrationSummary(data, view);
  renderAiProviderList(view.providers, view.providers_total);
  renderAiEngineList(view.engines, view.engines_total);
  renderAiModelList(view.models, view.models_total);
  renderAiCandidateList(view.candidates, view.candidates_total);
}

async function loadAiOrchestration({ probe = false, silent = false } = {}) {
  if (!aiOrchestrationSummaryEl && !aiProviderListEl && !aiEngineListEl && !aiModelListEl && !aiCandidateListEl) return;
  if (!silent) {
    setStatus(
      aiOrchestrationStatusEl,
      probe ? 'Probing orchestration engines and refreshing candidate telemetry...' : 'Loading orchestration registry...',
      false,
    );
  }
  try {
    const params = new URLSearchParams({ limit: String(AI_ORCHESTRATION_LIMIT) });
    if (probe) params.set('probe_engines', '1');
    const res = await apiFetch(`/api/admin/ai-orchestration?${params.toString()}`, { cache: 'no-store' });
    if (res.status === 401 || res.redirected) {
      window.location.href = res.url || '/login';
      return;
    }
    const data = await res.json();
    if (res.status === 403) {
      setStatus(aiOrchestrationStatusEl, 'Admin access required.', true);
      return;
    }
    if (!res.ok) {
      setStatus(aiOrchestrationStatusEl, data?.message || data?.details || data?.error || 'Unable to load AI orchestration.', true);
      return;
    }
    aiOrchestrationSnapshot = data;
    renderAiOrchestrationPanel(aiOrchestrationSnapshot);
    if (!silent) {
      setStatus(
        aiOrchestrationStatusEl,
        probe ? 'Engine probe complete.' : 'Orchestration registry refreshed.',
        false,
      );
    }
  } catch (err) {
    console.error(err);
    setStatus(aiOrchestrationStatusEl, 'Unable to load AI orchestration.', true);
  }
}

function rerenderAiOrchestration() {
  if (!aiOrchestrationSnapshot) return;
  renderAiOrchestrationPanel(aiOrchestrationSnapshot);
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
loadAiOrchestration({ probe: false });
setInterval(() => {
  loadAiOrchestration({ probe: false, silent: true });
}, 30000);
loadRefunds();
setInterval(loadRefunds, 20000);
loadAuditTransfers();
setInterval(loadAuditTransfers, 20000);
loadAssistantAnalytics({ silent: false });
setInterval(() => {
  loadAssistantAnalytics({ silent: true });
}, 30000);

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

if (aiOrchestrationRefreshBtn) {
  aiOrchestrationRefreshBtn.addEventListener('click', async () => {
    await loadAiOrchestration({ probe: Boolean(aiOrchestrationProbeEl?.checked) });
  });
}

if (assistantAnalyticsRefreshBtn) {
  assistantAnalyticsRefreshBtn.addEventListener('click', async () => {
    await loadAssistantAnalytics({ silent: false });
  });
}

[
  assistantAnalyticsOwnerEl,
  assistantAnalyticsRouteEl,
  assistantAnalyticsChannelEl,
  assistantAnalyticsProfileEl,
  assistantAnalyticsSinceHoursEl,
  assistantAnalyticsLimitEl,
].forEach((el) => {
  if (!el) return;
  el.addEventListener('change', () => {
    loadAssistantAnalytics({ silent: false });
  });
  el.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    loadAssistantAnalytics({ silent: false });
  });
});

if (aiOrchestrationSearchEl) {
  aiOrchestrationSearchEl.addEventListener('input', () => {
    rerenderAiOrchestration();
  });
}

if (aiOrchestrationRuntimeFilterEl) {
  aiOrchestrationRuntimeFilterEl.addEventListener('change', () => {
    rerenderAiOrchestration();
  });
}

[aiProviderSortEl, aiEngineSortEl, aiModelSortEl, aiCandidateSortEl].forEach((selectEl) => {
  if (!selectEl) return;
  selectEl.addEventListener('change', () => {
    rerenderAiOrchestration();
  });
});

if (aiOrchestrationExportJsonBtn) {
  aiOrchestrationExportJsonBtn.addEventListener('click', () => {
    const payload = aiExportPayload();
    if (!payload) {
      setStatus(aiOrchestrationStatusEl, 'Load AI orchestration data before exporting.', true);
      return;
    }
    downloadTextFile(
      `ai-orchestration-${exportStamp()}.json`,
      JSON.stringify(payload, null, 2),
      'application/json',
    );
    setStatus(aiOrchestrationStatusEl, 'Exported filtered AI orchestration JSON.', false);
  });
}

if (aiOrchestrationExportCsvBtn) {
  aiOrchestrationExportCsvBtn.addEventListener('click', () => {
    const csv = aiExportCsv();
    if (!csv) {
      setStatus(aiOrchestrationStatusEl, 'Load AI orchestration data before exporting.', true);
      return;
    }
    downloadTextFile(
      `ai-orchestration-${exportStamp()}.csv`,
      csv,
      'text/csv;charset=utf-8',
    );
    setStatus(aiOrchestrationStatusEl, 'Exported filtered AI orchestration CSV.', false);
  });
}

fetchTeams();
fetchProjects();
fetchRoomHistory();
fetchVersion();
setInterval(fetchRoomHistory, 20000);
