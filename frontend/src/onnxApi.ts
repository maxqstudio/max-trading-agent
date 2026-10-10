export const ONNX_STATUS_CODES = [
  'NOT_IMPLEMENTED',
  'NOT_STARTED',
  'NOT_PROVEN',
  'UNAVAILABLE',
  'RECOVERY_REQUIRED',
] as const

export type OnnxStatus = typeof ONNX_STATUS_CODES[number]

export const ONNX_STAGE_PAGE_IDS = [
  'data_intake',
  'discovery',
  'cpcv',
  'tournament',
  'monte_carlo',
  'challenger',
  'champion',
] as const

export type OnnxStagePageId = typeof ONNX_STAGE_PAGE_IDS[number]

export interface StateSection {
  status: OnnxStatus
  availability: OnnxStatus
  reason: string
}

export interface ValueSection extends StateSection {
  value: string | number | null
}

export interface StagePageState extends StateSection {
  page_id: OnnxStagePageId
  prerequisites: string[]
}

export interface ForwardState extends StateSection {
  prerequisites: string[]
}

export interface OnnxWorkspaceSnapshot {
  contract_version: '1.0'
  source: 'BACKEND_ONNX_01_SKELETON'
  operational_state: StateSection & {
    persisted: false
    cycle_id: null
  }
  dataset: StateSection & {
    dataset_id: null
    snapshot_id: null
    symbol: null
    timeframe: null
  }
  research_windows: StateSection & { items: null }
  scientific_authority: StateSection
  hardware_capacity: StateSection & {
    gpu_vram_bytes: null
    system_ram_bytes: null
  }
  discovery: StateSection & {
    budget: ValueSection
    experiment_progress: StateSection & { completed: null; total: null }
    qualified_pool: StateSection & { candidates: null }
  }
  stage_pages: StagePageState[]
  challenger: {
    candidates: StateSection & { items: null }
    forward: ForwardState
  }
  champion: StateSection & { identity: null }
  current_stage: ValueSection
  checkpoint: StateSection & { identity: null }
  first_blocker: {
    status: OnnxStatus
    availability: OnnxStatus
    reason: string
    code: string
    message: string
  }
  recovery: StateSection & { available: false }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStatus(value: unknown): value is OnnxStatus {
  return typeof value === 'string'
    && (ONNX_STATUS_CODES as readonly string[]).includes(value)
}

function assertState(value: unknown, label: string): asserts value is StateSection {
  if (!isRecord(value)
    || !isStatus(value.status)
    || !isStatus(value.availability)
    || typeof value.reason !== 'string'
    || value.reason.length === 0) {
    throw new Error(`ONNX workspace response has an invalid ${label} state.`)
  }
}

export function parseOnnxWorkspace(value: unknown): OnnxWorkspaceSnapshot {
  if (!isRecord(value)
    || value.contract_version !== '1.0'
    || value.source !== 'BACKEND_ONNX_01_SKELETON') {
    throw new Error('ONNX workspace response has an unsupported contract version or source.')
  }

  const challenger = isRecord(value.challenger) ? value.challenger : null
  const statePaths: Array<[unknown, string]> = [
    [value.operational_state, 'operational'],
    [value.dataset, 'dataset'],
    [value.research_windows, 'research windows'],
    [value.scientific_authority, 'scientific authority'],
    [value.hardware_capacity, 'hardware capacity'],
    [value.discovery, 'Discovery'],
    [challenger?.candidates, 'Challenger candidates'],
    [challenger?.forward, 'Forward'],
    [value.champion, 'Champion'],
    [value.current_stage, 'current stage'],
    [value.checkpoint, 'checkpoint'],
    [value.recovery, 'recovery'],
  ]
  for (const [state, label] of statePaths) assertState(state, label)

  if (!isRecord(value.operational_state)
    || value.operational_state.persisted !== false
    || value.operational_state.cycle_id !== null) {
    throw new Error('ONNX workspace response incorrectly claims a persisted operational cycle.')
  }

  if (!Array.isArray(value.stage_pages)
    || value.stage_pages.length !== ONNX_STAGE_PAGE_IDS.length) {
    throw new Error('ONNX workspace response has an invalid page-state set.')
  }
  value.stage_pages.forEach((page, index) => {
    assertState(page, `stage page ${index + 1}`)
    if (!isRecord(page)
      || page.page_id !== ONNX_STAGE_PAGE_IDS[index]
      || !Array.isArray(page.prerequisites)
      || !page.prerequisites.every((item) => typeof item === 'string')) {
      throw new Error('ONNX workspace response has an unexpected or malformed stage page.')
    }
  })

  if (!isRecord(challenger?.forward)
    || !Array.isArray(challenger.forward.prerequisites)
    || !challenger.forward.prerequisites.every((item) => typeof item === 'string')
    || 'page_id' in challenger.forward) {
    throw new Error('ONNX workspace Forward state must remain nested under Challenger.')
  }

  const discovery = value.discovery
  if (!isRecord(discovery)) throw new Error('ONNX workspace Discovery state is missing.')
  assertState(discovery.budget, 'Discovery budget')
  assertState(discovery.experiment_progress, 'Discovery progress')
  assertState(discovery.qualified_pool, 'Qualified Pool')
  assertState(value.first_blocker, 'first blocker')

  return value as unknown as OnnxWorkspaceSnapshot
}

export async function fetchOnnxWorkspace(
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<OnnxWorkspaceSnapshot> {
  const response = await fetcher('/api/v1/onnx/workspace', { method: 'GET', signal })
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = isRecord(payload) && typeof payload.detail === 'string'
      ? payload.detail
      : `HTTP ${response.status}`
    if (response.status === 503 || detail.includes('RECOVERY_REQUIRED')) {
      throw new Error('Application recovery is required; ONNX reads are blocked.')
    }
    throw new Error(`ONNX workspace could not be loaded: ${detail}`)
  }
  return parseOnnxWorkspace(payload)
}
