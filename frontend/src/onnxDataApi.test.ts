import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  fetchOnnxDataWorkspace,
  parseOnnxDataPreflight,
  parseOnnxDataSnapshot,
  parseOnnxDataWorkspace,
  preflightOnnxData,
  validateOnnxWindows,
} from './onnxDataApi'

const hash = (char: string) => char.repeat(64)

function dataQuality(status: 'PASS' | 'BLOCKED' = 'PASS') {
  return {
    status,
    schema_status: 'PASS',
    row_count: 6,
    symbol: 'EURUSD',
    timeframe: 'M5',
    period_enum: 5,
    timestamp_timezone: 'NAIVE_BROKER_SOURCE_TIME',
    timezone_provenance: 'Owner-declared broker server time; UTC offset not independently verified',
    timestamp_min: '2024-01-01T00:00:00',
    timestamp_max: '2024-01-01T00:25:00',
    identical_duplicate_rows: 0,
    conflicting_duplicate_identities: 0,
    timestamp_discontinuity_status: 'NO_OBSERVED_DISCONTINUITY',
    broker_reconciliation_status: 'BROKER_RECONCILIATION_PENDING',
    issues: [],
  }
}

function snapshot() {
  const digest = hash('a')
  return {
    snapshot_id: `SNP-${digest}`,
    dataset_id: `DS-${hash('b')}`,
    sha256: digest,
    parent_snapshot_id: null,
    filename: 'Max_MTF_Training.csv',
    source_path_sha256: hash('c'),
    source_fingerprint: {
      file_id: '1:10', size_bytes: 1800, modified_ns: '1780000000000000000', created_ns: '1770000000000000000',
    },
    size_bytes: 1800,
    row_count: 6,
    schema_id: 'MAX_MTF_TRAINING_CSV_49_V1',
    schema_version: '1.0',
    strategy_contract: 'MAX_TRUE_MTF_DYNAMIC_V1',
    feature_contract: 'CP32_TRUE_MTF_V1',
    ea_source_sha256: hash('d'),
    ea_manifest_sha256: hash('e'),
    symbol: 'EURUSD',
    timeframe: 'M5',
    period_enum: 5,
    timestamp_timezone: 'NAIVE_BROKER_SOURCE_TIME',
    timezone_provenance: 'Owner-declared broker server time; UTC offset not independently verified',
    timestamp_min: '2024-01-01T00:00:00',
    timestamp_max: '2024-01-01T00:25:00',
    timestamp_discontinuity_status: 'NO_OBSERVED_DISCONTINUITY',
    broker_reconciliation_status: 'BROKER_RECONCILIATION_PENDING',
    dq_status: 'PASS',
    dq: dataQuality(),
    correction: null,
    evidence_class: 'SYNTHETIC_TEST_EVIDENCE',
    created_utc: '2026-10-10T00:00:00+00:00',
  }
}

function workspace(withWindows = false) {
  return {
    contract_version: '2.0',
    source: 'BACKEND_ONNX_02_DATA_API',
    status: 'SYNTHETIC_TEST_EVIDENCE',
    implementation_status: 'IMPLEMENTED',
    real_data_readiness: 'NOT_PROVEN',
    latest_snapshot: snapshot(),
    window_config: withWindows ? validWindowConfig() : null,
    readiness_evidence: null,
    snapshots: [snapshot()],
    scientific_execution: 'NOT_IMPLEMENTED',
    first_blocker: withWindows ? 'SYNTHETIC_TEST_EVIDENCE_NOT_REAL_DATA' : 'THREE_WINDOWS_NOT_VALIDATED',
  }
}

function validWindowConfig() {
  const snap = snapshot()
  const ranges = {
    DISCOVERY: { from: '2024-01-01T00:00:00', to: '2024-01-01T00:05:00' },
    TOURNAMENT: { from: '2024-01-01T00:10:00', to: '2024-01-01T00:15:00' },
    FORWARD: { from: '2024-01-01T00:20:00', to: '2024-01-01T00:25:00' },
  }
  return {
    window_config_id: `WIN-${hash('f')}`,
    snapshot_id: snap.snapshot_id,
    snapshot_sha256: snap.sha256,
    revision: 1,
    timezone_provenance: snap.timezone_provenance,
    windows: ranges,
    validation: {
      status: 'PASS',
      timezone_provenance: snap.timezone_provenance,
      windows: [
        { name: 'DISCOVERY', ...ranges.DISCOVERY, row_count: 2 },
        { name: 'TOURNAMENT', ...ranges.TOURNAMENT, row_count: 2 },
        { name: 'FORWARD', ...ranges.FORWARD, row_count: 2 },
      ],
      issues: [],
    },
    created_utc: '2026-10-10T00:00:00+00:00',
  }
}

function preflight() {
  return {
    contract_version: '2.0',
    source: 'BACKEND_ONNX_02_DATA_API',
    status: 'PREFLIGHT_PASS',
    snapshot_permitted: true,
    source_identity: {
      filename: 'Max_MTF_Training.csv',
      path_sha256: hash('f'),
      sha256: hash('1'),
      fingerprint: { file_id: '1:20', size_bytes: 1800, modified_ns: '1780000000000000000', created_ns: '1770000000000000000' },
      size_bytes: 1800,
    },
    authority: {
      strategy_contract: 'MAX_TRUE_MTF_DYNAMIC_V1',
      feature_contract: 'CP32_TRUE_MTF_V1',
      schema_id: 'MAX_MTF_TRAINING_CSV_49_V1',
      schema_version: '1.0',
      ea_source_sha256: hash('d'),
      ea_manifest_sha256: hash('e'),
    },
    data_quality: dataQuality(),
    first_blocker: null,
    evidence_class: 'SYNTHETIC_TEST_EVIDENCE',
  }
}

function mutate<T>(value: T, change: (draft: Record<string, unknown>) => void): T {
  const draft = structuredClone(value) as Record<string, unknown>
  change(draft)
  return draft as T
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ONNX-02 v2 API contract', () => {
  it('parses complete backend workspace, snapshot, and source preflight fixtures', () => {
    expect(parseOnnxDataWorkspace(workspace()).latest_snapshot?.sha256).toBe(hash('a'))
    expect(parseOnnxDataSnapshot(snapshot()).dq_status).toBe('PASS')
    expect(parseOnnxDataPreflight(preflight()).source_identity.sha256).toBe(hash('1'))
  })

  it.each([
    ['unsupported version', (body: Record<string, unknown>) => { body.contract_version = '1.0' }],
    ['unsupported source', (body: Record<string, unknown>) => { body.source = 'FRONTEND' }],
    ['unknown status', (body: Record<string, unknown>) => { body.status = 'READY' }],
    ['missing required field', (body: Record<string, unknown>) => { delete body.real_data_readiness }],
    ['invented real readiness', (body: Record<string, unknown>) => { body.real_data_readiness = 'DATA_READY' }],
  ])('rejects %s before the workspace can render', (_name, change) => {
    expect(() => parseOnnxDataWorkspace(mutate(workspace(), change))).toThrow(/v2 read contract/i)
  })

  it.each([
    ['fabricated dataset identity', (body: Record<string, unknown>) => { (body.latest_snapshot as Record<string, unknown>).dataset_id = 'FAKE_DATASET' }],
    ['snapshot and DQ identity disagreement', (body: Record<string, unknown>) => { (body.latest_snapshot as Record<string, unknown>).symbol = 'GBPUSD' }],
    ['false DATA_READY status', (body: Record<string, unknown>) => { body.status = 'DATA_READY' }],
    ['missing snapshot history', (body: Record<string, unknown>) => { body.snapshots = [] }],
    ['false scientific execution', (body: Record<string, unknown>) => { body.scientific_execution = 'TRAINING' }],
    ['false blocker absence', (body: Record<string, unknown>) => { body.first_blocker = null }],
  ])('rejects %s as a contradictory workspace snapshot', (_name, change) => {
    expect(() => parseOnnxDataWorkspace(mutate(workspace(true), change))).toThrow(/v2 read contract/i)
  })

  it('accepts only a complete, ordered, snapshot-bound three-window configuration', () => {
    const valid = mutate(workspace(true), (body) => {
      body.window_config = validWindowConfig()
      body.first_blocker = 'SYNTHETIC_TEST_EVIDENCE_NOT_REAL_DATA'
    })
    expect(parseOnnxDataWorkspace(valid).window_config).toMatchObject({ revision: 1 })

    const malformed = mutate(valid, (body) => {
      const config = body.window_config as Record<string, unknown>
      const ranges = config.windows as Record<string, Record<string, unknown>>
      ranges.FORWARD.from = '2023-12-31T23:59:00'
    })
    expect(() => parseOnnxDataWorkspace(malformed)).toThrow(/v2 read contract/i)
  })

  it('rejects a snapshot whose id/hash or DQ summary contradict each other', () => {
    expect(() => parseOnnxDataSnapshot(mutate(snapshot(), (body) => { body.sha256 = hash('9') }))).toThrow(/bind its hash/i)
    expect(() => parseOnnxDataSnapshot(mutate(snapshot(), (body) => {
      const dq = structuredClone(body.dq) as Record<string, unknown>
      dq.status = 'BLOCKED'
      body.dq = dq
    }))).toThrow(/data-quality status contradicts|snapshot summary contradicts/i)
  })

  it('rejects malformed preflight structure and unknown evidence class', () => {
    expect(() => parseOnnxDataPreflight(mutate(preflight(), (body) => { delete body.authority }))).toThrow(/missing or unexpected fields/i)
    expect(() => parseOnnxDataPreflight(mutate(preflight(), (body) => { body.evidence_class = 'REAL_DATA_READY' }))).toThrow(/evidence class is unknown/i)
    expect(() => parseOnnxDataPreflight(mutate(preflight(), (body) => { body.first_blocker = 'DATA_QUALITY_BLOCKED' }))).toThrow(/status, DQ result/i)
  })

  it('keeps recovery-required 503 visible and rejects non-JSON responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: 'RECOVERY_REQUIRED: application operations are disabled',
      reason: 'synthetic recovery gate test',
    }), { status: 503, headers: { 'Content-Type': 'application/json' } })))
    await expect(fetchOnnxDataWorkspace()).rejects.toThrow(/RECOVERY_REQUIRED/)

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not-json', { status: 200 })))
    await expect(fetchOnnxDataWorkspace()).rejects.toThrow(/valid JSON/i)
  })

  it('preserves caller cancellation instead of reporting it as a request timeout', async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => {
        reject(new DOMException('The operation was aborted.', 'AbortError'))
      }, { once: true })
    }))
    vi.stubGlobal('fetch', fetchMock)

    const pending = fetchOnnxDataWorkspace(controller.signal)
    controller.abort()

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('parses a complete backend window result and rejects a contradictory frozen range', async () => {
    const config = validWindowConfig()
    const body = {
      contract_version: '2.0',
      source: 'BACKEND_ONNX_02_DATA_API',
      status: 'SYNTHETIC_TEST_EVIDENCE',
      window_config: config,
      validation: config.validation,
      readiness: null,
      first_blocker: null,
    }
    const payload = {
      snapshot_id: snapshot().snapshot_id,
      snapshot_sha256: snapshot().sha256,
      timezone_provenance: snapshot().timezone_provenance as string,
      windows: config.windows,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200 })))
    await expect(validateOnnxWindows(payload)).resolves.toMatchObject({ status: 'SYNTHETIC_TEST_EVIDENCE' })

    const contradicted = structuredClone(body)
    contradicted.window_config.windows.FORWARD.from = '2024-01-01T00:19:00'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(contradicted), { status: 200 })))
    await expect(validateOnnxWindows(payload)).rejects.toThrow(/v2 read contract/i)
  })

  it('sends only the selected source and timezone provenance in read-only preflight', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(preflight()), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const response = await preflightOnnxData({ source_path: 'C:\\Common\\Files\\Max_MTF_Training.csv', timezone_provenance: 'broker server time' })
    expect(response.status).toBe('PREFLIGHT_PASS')
    expect(fetchMock).toHaveBeenCalledWith('/api/v2/onnx/data/preflight', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ source_path: 'C:\\Common\\Files\\Max_MTF_Training.csv', timezone_provenance: 'broker server time' }),
    }))
  })
})
