import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import OnnxDataIntake from './OnnxDataIntake'

const hash = (char: string) => char.repeat(64)

function dq() {
  return {
    status: 'PASS', schema_status: 'PASS', row_count: 6,
    symbol: 'EURUSD', timeframe: 'M5', period_enum: 5,
    timestamp_timezone: 'NAIVE_BROKER_SOURCE_TIME',
    timezone_provenance: 'Owner-declared broker server time; UTC offset not independently verified',
    timestamp_min: '2024-01-01T00:00:00', timestamp_max: '2024-01-01T00:25:00',
    identical_duplicate_rows: 0, conflicting_duplicate_identities: 0,
    timestamp_discontinuity_status: 'NO_OBSERVED_DISCONTINUITY',
    broker_reconciliation_status: 'BROKER_RECONCILIATION_PENDING', issues: [],
  }
}

function snapshot() {
  const sha = hash('a')
  return {
    snapshot_id: `SNP-${sha}`, dataset_id: `DS-${hash('b')}`, sha256: sha,
    parent_snapshot_id: null, filename: 'Max_MTF_Training.csv', source_path_sha256: hash('c'),
    source_fingerprint: { file_id: '1:10', size_bytes: 1800, modified_ns: '1780000000000000000', created_ns: '1770000000000000000' },
    size_bytes: 1800, row_count: 6, schema_id: 'MAX_MTF_TRAINING_CSV_49_V1', schema_version: '1.0',
    strategy_contract: 'MAX_TRUE_MTF_DYNAMIC_V1', feature_contract: 'CP32_TRUE_MTF_V1',
    ea_source_sha256: hash('d'), ea_manifest_sha256: hash('e'), symbol: 'EURUSD', timeframe: 'M5', period_enum: 5,
    timestamp_timezone: 'NAIVE_BROKER_SOURCE_TIME',
    timezone_provenance: 'Owner-declared broker server time; UTC offset not independently verified',
    timestamp_min: '2024-01-01T00:00:00', timestamp_max: '2024-01-01T00:25:00',
    timestamp_discontinuity_status: 'NO_OBSERVED_DISCONTINUITY',
    broker_reconciliation_status: 'BROKER_RECONCILIATION_PENDING', dq_status: 'PASS', dq: dq(), correction: null,
    evidence_class: 'SYNTHETIC_TEST_EVIDENCE', created_utc: '2026-10-10T00:00:00+00:00',
  }
}

function workspace(withSnapshot = false) {
  const current = withSnapshot ? snapshot() : null
  return {
    contract_version: '2.0', source: 'BACKEND_ONNX_02_DATA_API',
    status: withSnapshot ? 'SYNTHETIC_TEST_EVIDENCE' : 'NOT_STARTED',
    implementation_status: 'IMPLEMENTED', real_data_readiness: 'NOT_PROVEN',
    latest_snapshot: current, window_config: null, readiness_evidence: null,
    snapshots: current ? [current] : [], scientific_execution: 'NOT_IMPLEMENTED',
    first_blocker: withSnapshot ? 'THREE_WINDOWS_NOT_VALIDATED' : 'NO_IMMUTABLE_SNAPSHOT',
  }
}

function validPreflight() {
  return {
    contract_version: '2.0', source: 'BACKEND_ONNX_02_DATA_API', status: 'PREFLIGHT_PASS', snapshot_permitted: true,
    source_identity: {
      filename: 'Max_MTF_Training.csv', path_sha256: hash('f'), sha256: hash('1'),
      fingerprint: { file_id: '1:20', size_bytes: 1800, modified_ns: '1780000000000000000', created_ns: '1770000000000000000' },
      size_bytes: 1800,
    },
    authority: {
      strategy_contract: 'MAX_TRUE_MTF_DYNAMIC_V1', feature_contract: 'CP32_TRUE_MTF_V1',
      schema_id: 'MAX_MTF_TRAINING_CSV_49_V1', schema_version: '1.0',
      ea_source_sha256: hash('d'), ea_manifest_sha256: hash('e'),
    },
    data_quality: dq(), first_blocker: null, evidence_class: 'SYNTHETIC_TEST_EVIDENCE',
  }
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ONNX Data Intake controls', () => {
  it('shows progress while preflight runs and binds snapshot creation to the reviewed hash', async () => {
    let releasePreflight: ((value: Response) => void) | undefined
    let snapshotCreated = false
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url.endsWith('/workspace')) return Promise.resolve(response(workspace(snapshotCreated)))
      if (url.endsWith('/preflight')) return new Promise<Response>((resolve) => { releasePreflight = resolve })
      if (url.endsWith('/snapshots') && init?.method === 'POST') {
        snapshotCreated = true
        const current = snapshot()
        return Promise.resolve(response({
          contract_version: '2.0', source: 'BACKEND_ONNX_02_DATA_API', status: 'SYNTHETIC_TEST_EVIDENCE',
          snapshot: current, data_quality: current.dq, first_blocker: null, readiness: null,
        }))
      }
      throw new Error(`unexpected request ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<OnnxDataIntake />)
    await screen.findByRole('heading', { name: 'Data Intake' })
    await waitFor(() => expect(screen.getByText('NO_IMMUTABLE_SNAPSHOT')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/Timestamp timezone\/source provenance/), {
      target: { value: 'Owner-declared broker server time; UTC offset not independently verified' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Read-only source preflight/i }))
    expect(screen.getByRole('status')).toHaveTextContent(/Checking lock, identity, schema and DQ/i)
    expect(document.querySelector('.button-spinner')).toBeInTheDocument()

    releasePreflight?.(response(validPreflight()))
    expect(await screen.findByText(/Backend preflight: PREFLIGHT_PASS/)).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText(/I reviewed this source hash/))
    fireEvent.click(screen.getByRole('button', { name: /Create immutable snapshot/i }))
    expect(await screen.findByText(/Backend result: SYNTHETIC_TEST_EVIDENCE/)).toBeInTheDocument()
    expect(await screen.findByText(`SNP-${hash('a')}`)).toBeInTheDocument()

    const snapshotCall = calls.find((call) => call.url.endsWith('/snapshots') && call.init?.method === 'POST')
    expect(JSON.parse(String(snapshotCall?.init?.body))).toMatchObject({
      expected_source_sha256: hash('1'), confirmed: true,
    })
  })

  it('does not retain a previously rendered snapshot after malformed backend preflight', async () => {
    let preflightCalled = false
    const existing = snapshot()
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/workspace')) return Promise.resolve(response(workspace(true)))
      if (url.endsWith('/preflight')) {
        preflightCalled = true
        return Promise.resolve(response({ ...validPreflight(), contract_version: '1.0' }))
      }
      throw new Error(`unexpected request ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<OnnxDataIntake />)
    expect(await screen.findByText(existing.snapshot_id)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/Timestamp timezone\/source provenance/), {
      target: { value: 'Owner-declared broker server time' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Read-only source preflight/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/unsupported contract identity/i)
    await waitFor(() => expect(screen.queryByText(existing.snapshot_id)).not.toBeInTheDocument())
    expect(preflightCalled).toBe(true)
  })
})
