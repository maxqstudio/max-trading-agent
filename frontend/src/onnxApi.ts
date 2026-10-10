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

const ONNX_STATUS_SET: ReadonlySet<string> = new Set(ONNX_STATUS_CODES)

function isStatus(value: unknown): value is OnnxStatus {
  return typeof value === 'string' && ONNX_STATUS_SET.has(value)
}

function contractError(detail: string): Error {
  return new Error(`ONNX workspace response violates the read contract: ${detail}`)
}

function assertRecordWithKeys(
  value: unknown,
  expectedKeys: readonly string[],
  label: string,
): asserts value is Record<string, unknown> {
  if (!isRecord(value)) throw contractError(`${label} must be an object.`)
  const keys = Object.keys(value)
  const expected = new Set(expectedKeys)
  if (keys.length !== expected.size || keys.some((key) => !expected.has(key))) {
    throw contractError(`${label} has missing or unexpected fields.`)
  }
}

function assertNonEmptyString(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw contractError(`${label} must be a non-empty string.`)
  }
}

function assertLiteralNull(value: unknown, label: string) {
  if (value !== null) throw contractError(`${label} must be null in contract version 1.0.`)
}

function assertStatus(value: unknown, label: string): asserts value is OnnxStatus {
  if (!isStatus(value)) throw contractError(`${label} uses an unknown status.`)
}

function assertFixedState(
  value: unknown,
  label: string,
  status: OnnxStatus,
  availability: OnnxStatus,
  additionalKeys: readonly string[] = [],
): asserts value is Record<string, unknown> {
  assertRecordWithKeys(value, ['status', 'availability', 'reason', ...additionalKeys], label)
  assertStatus(value.status, `${label} status`)
  assertStatus(value.availability, `${label} availability`)
  assertNonEmptyString(value.reason, `${label} reason`)
  if (value.status !== status || value.availability !== availability) {
    throw contractError(`${label} has a status/availability pairing outside the frozen skeleton contract.`)
  }
}

function assertPrerequisites(value: unknown, label: string) {
  if (!Array.isArray(value) || value.length === 0) {
    throw contractError(`${label} must contain at least one prerequisite.`)
  }
  value.forEach((prerequisite, index) => {
    assertNonEmptyString(prerequisite, `${label} prerequisite ${index + 1}`)
  })
}

function assertNullFields(record: Record<string, unknown>, fields: readonly string[], label: string) {
  fields.forEach((field) => assertLiteralNull(record[field], `${label}.${field}`))
}

function validateSnapshotStructure(value: unknown): asserts value is OnnxWorkspaceSnapshot {
  const topLevelKeys = [
    'contract_version',
    'source',
    'operational_state',
    'dataset',
    'research_windows',
    'scientific_authority',
    'hardware_capacity',
    'discovery',
    'stage_pages',
    'challenger',
    'champion',
    'current_stage',
    'checkpoint',
    'first_blocker',
    'recovery',
  ]
  assertRecordWithKeys(value, topLevelKeys, 'snapshot')
  if (value.contract_version !== '1.0') {
    throw contractError('contract_version must be "1.0".')
  }
  if (value.source !== 'BACKEND_ONNX_01_SKELETON') {
    throw contractError('source must be BACKEND_ONNX_01_SKELETON.')
  }

  const operational = value.operational_state
  assertFixedState(operational, 'operational_state', 'NOT_STARTED', 'NOT_IMPLEMENTED', ['persisted', 'cycle_id'])
  if (operational.persisted !== false) throw contractError('operational_state.persisted must be false.')
  assertLiteralNull(operational.cycle_id, 'operational_state.cycle_id')

  const dataset = value.dataset
  assertFixedState(dataset, 'dataset', 'NOT_STARTED', 'UNAVAILABLE', [
    'dataset_id', 'snapshot_id', 'symbol', 'timeframe',
  ])
  assertNullFields(dataset, ['dataset_id', 'snapshot_id', 'symbol', 'timeframe'], 'dataset')

  const researchWindows = value.research_windows
  assertFixedState(researchWindows, 'research_windows', 'NOT_STARTED', 'UNAVAILABLE', ['items'])
  assertLiteralNull(researchWindows.items, 'research_windows.items')

  assertFixedState(value.scientific_authority, 'scientific_authority', 'NOT_PROVEN', 'NOT_IMPLEMENTED')

  const hardware = value.hardware_capacity
  assertFixedState(hardware, 'hardware_capacity', 'NOT_PROVEN', 'UNAVAILABLE', [
    'gpu_vram_bytes', 'system_ram_bytes',
  ])
  assertNullFields(hardware, ['gpu_vram_bytes', 'system_ram_bytes'], 'hardware_capacity')

  const discovery = value.discovery
  assertFixedState(discovery, 'discovery', 'NOT_STARTED', 'NOT_IMPLEMENTED', [
    'budget', 'experiment_progress', 'qualified_pool',
  ])

  const budget = discovery.budget
  assertFixedState(budget, 'discovery.budget', 'NOT_STARTED', 'UNAVAILABLE', ['value'])
  assertLiteralNull(budget.value, 'discovery.budget.value')

  const progress = discovery.experiment_progress
  assertFixedState(progress, 'discovery.experiment_progress', 'NOT_STARTED', 'UNAVAILABLE', [
    'completed', 'total',
  ])
  assertNullFields(progress, ['completed', 'total'], 'discovery.experiment_progress')

  const qualifiedPool = discovery.qualified_pool
  assertFixedState(qualifiedPool, 'discovery.qualified_pool', 'NOT_STARTED', 'NOT_IMPLEMENTED', ['candidates'])
  assertLiteralNull(qualifiedPool.candidates, 'discovery.qualified_pool.candidates')

  if (!Array.isArray(value.stage_pages) || value.stage_pages.length !== ONNX_STAGE_PAGE_IDS.length) {
    throw contractError('stage_pages must contain exactly the seven frozen stage pages.')
  }
  value.stage_pages.forEach((page, index) => {
    const pageId = ONNX_STAGE_PAGE_IDS[index]
    assertFixedState(page, `stage_pages[${index}]`, 'NOT_STARTED', 'NOT_IMPLEMENTED', [
      'page_id', 'prerequisites',
    ])
    if (!isRecord(page) || page.page_id !== pageId) {
      throw contractError('stage_pages has an unknown, duplicate, or incorrectly ordered page identifier.')
    }
    assertPrerequisites(page.prerequisites, `stage_pages[${index}].prerequisites`)
  })

  const challenger = value.challenger
  assertRecordWithKeys(challenger, ['candidates', 'forward'], 'challenger')

  const candidates = challenger.candidates
  assertFixedState(candidates, 'challenger.candidates', 'NOT_STARTED', 'UNAVAILABLE', ['items'])
  assertLiteralNull(candidates.items, 'challenger.candidates.items')

  const forward = challenger.forward
  assertFixedState(forward, 'challenger.forward', 'NOT_STARTED', 'NOT_IMPLEMENTED', ['prerequisites'])
  assertPrerequisites(forward.prerequisites, 'challenger.forward.prerequisites')

  const champion = value.champion
  assertFixedState(champion, 'champion', 'NOT_STARTED', 'NOT_IMPLEMENTED', ['identity'])
  assertLiteralNull(champion.identity, 'champion.identity')

  const currentStage = value.current_stage
  assertFixedState(currentStage, 'current_stage', 'NOT_STARTED', 'UNAVAILABLE', ['value'])
  assertLiteralNull(currentStage.value, 'current_stage.value')

  const checkpoint = value.checkpoint
  assertFixedState(checkpoint, 'checkpoint', 'NOT_STARTED', 'NOT_IMPLEMENTED', ['identity'])
  assertLiteralNull(checkpoint.identity, 'checkpoint.identity')

  const firstBlocker = value.first_blocker
  assertFixedState(firstBlocker, 'first_blocker', 'NOT_IMPLEMENTED', 'NOT_IMPLEMENTED', ['code', 'message'])
  assertNonEmptyString(firstBlocker.code, 'first_blocker.code')
  assertNonEmptyString(firstBlocker.message, 'first_blocker.message')

  const recovery = value.recovery
  assertFixedState(recovery, 'recovery', 'UNAVAILABLE', 'UNAVAILABLE', ['available'])
  if (recovery.available !== false) throw contractError('recovery.available must be false.')
}

export function parseOnnxWorkspace(value: unknown): OnnxWorkspaceSnapshot {
  validateSnapshotStructure(value)
  return value
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
