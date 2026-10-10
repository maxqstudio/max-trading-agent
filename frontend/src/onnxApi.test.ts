import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import OnnxWorkspace from './OnnxWorkspace'
import { fetchOnnxWorkspace, parseOnnxWorkspace } from './onnxApi'

type PathPart = string | number
type TestRecord = Record<string, unknown>

function state(status: string, availability: string, reason = 'Backend contract reason.') {
  return { status, availability, reason }
}

// Source-derived fixture: mirrors build_onnx_workspace_snapshot() in
// backend/max_backend/onnx_api.py, including the fixed status pairings.
function validSnapshot() {
  return {
    contract_version: '1.0',
    source: 'BACKEND_ONNX_01_SKELETON',
    operational_state: {
      ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'ONNX-01 has no operational cycle store; the planning state machine is not persisted operational state.'),
      persisted: false,
      cycle_id: null,
    },
    dataset: {
      ...state('NOT_STARTED', 'UNAVAILABLE', 'Dataset intake and immutable snapshots are not implemented in ONNX-01.'),
      dataset_id: null,
      snapshot_id: null,
      symbol: null,
      timeframe: null,
    },
    research_windows: {
      ...state('NOT_STARTED', 'UNAVAILABLE', 'No persisted cycle exists from which to read frozen research windows.'),
      items: null,
    },
    scientific_authority: state('NOT_PROVEN', 'NOT_IMPLEMENTED', 'ONNX-00 is frozen planning authority only; no scientific execution evidence exists.'),
    hardware_capacity: {
      ...state('NOT_PROVEN', 'UNAVAILABLE', 'ONNX-01 does not inspect or claim hardware capacity.'),
      gpu_vram_bytes: null,
      system_ram_bytes: null,
    },
    discovery: {
      ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'Discovery execution and its experiment ledger are not implemented.'),
      budget: {
        ...state('NOT_STARTED', 'UNAVAILABLE', 'No frozen operational cycle budget exists.'),
        value: null,
      },
      experiment_progress: {
        ...state('NOT_STARTED', 'UNAVAILABLE', 'No operational experiment ledger exists.'),
        completed: null,
        total: null,
      },
      qualified_pool: {
        ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'Qualified Pool admission is not implemented; Cheap Screen has no qualification authority.'),
        candidates: null,
      },
    },
    stage_pages: [
      {
        ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'Scientific execution and its operational state are not implemented in ONNX-01.'),
        page_id: 'data_intake',
        prerequisites: ['Separate ONNX-02 authorization and accepted data-source authority.'],
      },
      {
        ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'Scientific execution and its operational state are not implemented in ONNX-01.'),
        page_id: 'discovery',
        prerequisites: ['Verified DATA_READY snapshot, frozen windows, and a complete Owner-authorized KPI contract.'],
      },
      {
        ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'Scientific execution and its operational state are not implemented in ONNX-01.'),
        page_id: 'cpcv',
        prerequisites: ['Sealed WFA-qualified pool from Discovery.'],
      },
      {
        ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'Scientific execution and its operational state are not implemented in ONNX-01.'),
        page_id: 'tournament',
        prerequisites: ['Terminal sealed CPCV evidence.'],
      },
      {
        ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'Scientific execution and its operational state are not implemented in ONNX-01.'),
        page_id: 'monte_carlo',
        prerequisites: ['CPCV survivors and terminal Tournament evidence under the frozen KPI contract.'],
      },
      {
        ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'Scientific execution and its operational state are not implemented in ONNX-01.'),
        page_id: 'challenger',
        prerequisites: ['Monte Carlo survivors, untouched Forward PASS, final-fit/export/parity/manifest gates.'],
      },
      {
        ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'Scientific execution and its operational state are not implemented in ONNX-01.'),
        page_id: 'champion',
        prerequisites: ['Verified CHALLENGER_READY identity and explicit Owner promotion.'],
      },
    ],
    challenger: {
      candidates: {
        ...state('NOT_STARTED', 'UNAVAILABLE', 'No persisted ONNX Challenger registry exists.'),
        items: null,
      },
      forward: {
        ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'Scientific execution and its operational state are not implemented in ONNX-01.'),
        prerequisites: ['Monte Carlo survivor and frozen Forward window.'],
      },
    },
    champion: {
      ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'ONNX Champion publication is not implemented; promotion remains explicit Owner-only authority.'),
      identity: null,
    },
    current_stage: {
      ...state('NOT_STARTED', 'UNAVAILABLE', 'No operational cycle stage can be read.'),
      value: null,
    },
    checkpoint: {
      ...state('NOT_STARTED', 'NOT_IMPLEMENTED', 'ONNX checkpoint persistence is not implemented.'),
      identity: null,
    },
    first_blocker: {
      ...state('NOT_IMPLEMENTED', 'NOT_IMPLEMENTED', 'The current first blocker is the explicit ONNX-01 capability boundary.'),
      code: 'ONNX_EXECUTION_NOT_IMPLEMENTED',
      message: 'ONNX-01 is a read-only workspace shell; scientific execution is not implemented or authorized by this phase.',
    },
    recovery: {
      ...state('UNAVAILABLE', 'UNAVAILABLE', 'No ONNX operational state or candidate recovery cursor exists.'),
      available: false,
    },
  }
}

function cloneSnapshot(): TestRecord {
  return JSON.parse(JSON.stringify(validSnapshot())) as TestRecord
}

function isTestRecord(value: unknown): value is TestRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function setPath(root: TestRecord, path: PathPart[], value: unknown) {
  let parent: unknown = root
  for (const part of path.slice(0, -1)) {
    if (Array.isArray(parent) && typeof part === 'number') parent = parent[part]
    else if (isTestRecord(parent) && typeof part === 'string') parent = parent[part]
    else throw new Error(`Invalid test path: ${path.join('.')}`)
  }
  const finalPart = path[path.length - 1]
  if (Array.isArray(parent) && typeof finalPart === 'number') parent[finalPart] = value
  else if (isTestRecord(parent) && typeof finalPart === 'string') parent[finalPart] = value
  else throw new Error(`Invalid test path: ${path.join('.')}`)
}

function removePath(root: TestRecord, path: string[]) {
  let parent: unknown = root
  for (const part of path.slice(0, -1)) {
    if (!isTestRecord(parent)) throw new Error(`Invalid test path: ${path.join('.')}`)
    parent = parent[part]
  }
  if (!isTestRecord(parent)) throw new Error(`Invalid test path: ${path.join('.')}`)
  delete parent[path[path.length - 1]]
}

function invalidSnapshot(change: (snapshot: TestRecord) => void): TestRecord {
  const snapshot = cloneSnapshot()
  change(snapshot)
  return snapshot
}

const fixedPairs: Array<{ label: string; path: PathPart[]; status: string; availability: string }> = [
  { label: 'operational state', path: ['operational_state'], status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED' },
  { label: 'dataset', path: ['dataset'], status: 'NOT_STARTED', availability: 'UNAVAILABLE' },
  { label: 'research windows', path: ['research_windows'], status: 'NOT_STARTED', availability: 'UNAVAILABLE' },
  { label: 'scientific authority', path: ['scientific_authority'], status: 'NOT_PROVEN', availability: 'NOT_IMPLEMENTED' },
  { label: 'hardware capacity', path: ['hardware_capacity'], status: 'NOT_PROVEN', availability: 'UNAVAILABLE' },
  { label: 'Discovery', path: ['discovery'], status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED' },
  { label: 'Discovery budget', path: ['discovery', 'budget'], status: 'NOT_STARTED', availability: 'UNAVAILABLE' },
  { label: 'experiment progress', path: ['discovery', 'experiment_progress'], status: 'NOT_STARTED', availability: 'UNAVAILABLE' },
  { label: 'Qualified Pool', path: ['discovery', 'qualified_pool'], status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED' },
  ...['data_intake', 'discovery', 'cpcv', 'tournament', 'monte_carlo', 'challenger', 'champion'].map((pageId, index) => ({
    label: `stage page ${pageId}`,
    path: ['stage_pages', index],
    status: 'NOT_STARTED',
    availability: 'NOT_IMPLEMENTED',
  })),
  { label: 'Challenger candidates', path: ['challenger', 'candidates'], status: 'NOT_STARTED', availability: 'UNAVAILABLE' },
  { label: 'Forward', path: ['challenger', 'forward'], status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED' },
  { label: 'Champion', path: ['champion'], status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED' },
  { label: 'current stage', path: ['current_stage'], status: 'NOT_STARTED', availability: 'UNAVAILABLE' },
  { label: 'checkpoint', path: ['checkpoint'], status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED' },
  { label: 'first blocker', path: ['first_blocker'], status: 'NOT_IMPLEMENTED', availability: 'NOT_IMPLEMENTED' },
  { label: 'recovery', path: ['recovery'], status: 'UNAVAILABLE', availability: 'UNAVAILABLE' },
]

const literalNullMutations: Array<{ label: string; path: PathPart[]; value: unknown }> = [
  { label: 'operational persisted flag', path: ['operational_state', 'persisted'], value: true },
  { label: 'operational cycle identity', path: ['operational_state', 'cycle_id'], value: 'CYCLE-FAKE' },
  { label: 'dataset identity', path: ['dataset', 'dataset_id'], value: 'FAKE_DATASET' },
  { label: 'snapshot identity', path: ['dataset', 'snapshot_id'], value: 'FAKE_SNAPSHOT' },
  { label: 'dataset symbol', path: ['dataset', 'symbol'], value: 'EURUSD' },
  { label: 'dataset timeframe', path: ['dataset', 'timeframe'], value: 'H1' },
  { label: 'research-window identities', path: ['research_windows', 'items'], value: [] },
  { label: 'GPU memory', path: ['hardware_capacity', 'gpu_vram_bytes'], value: 1 },
  { label: 'system memory', path: ['hardware_capacity', 'system_ram_bytes'], value: 1 },
  { label: 'Discovery budget', path: ['discovery', 'budget', 'value'], value: 100 },
  { label: 'completed experiment count', path: ['discovery', 'experiment_progress', 'completed'], value: 10 },
  { label: 'total experiment count', path: ['discovery', 'experiment_progress', 'total'], value: 100 },
  { label: 'Qualified Pool candidates', path: ['discovery', 'qualified_pool', 'candidates'], value: [] },
  { label: 'Challenger candidates', path: ['challenger', 'candidates', 'items'], value: [] },
  { label: 'Champion identity', path: ['champion', 'identity'], value: 'CHAMPION-FAKE' },
  { label: 'current stage value', path: ['current_stage', 'value'], value: 'FORWARD_RUNNING' },
  { label: 'checkpoint identity', path: ['checkpoint', 'identity'], value: 'CHECKPOINT-FAKE' },
  { label: 'recovery availability', path: ['recovery', 'available'], value: true },
]

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ONNX read-only API contract', () => {
  it('accepts a complete backend-derived ONNX-01 snapshot', () => {
    expect(parseOnnxWorkspace(validSnapshot())).toEqual(validSnapshot())
  })

  it.each([
    ['unsupported contract version', (snapshot: TestRecord) => setPath(snapshot, ['contract_version'], '2.0')],
    ['unsupported source', (snapshot: TestRecord) => setPath(snapshot, ['source'], 'FRONTEND_DEFAULT')],
    ['unknown status vocabulary', (snapshot: TestRecord) => setPath(snapshot, ['operational_state', 'status'], 'READY')],
    ['missing required section', (snapshot: TestRecord) => removePath(snapshot, ['scientific_authority'])],
    ['extra top-level section', (snapshot: TestRecord) => setPath(snapshot, ['fabricated_results'], [])],
    ['malformed object section', (snapshot: TestRecord) => setPath(snapshot, ['dataset'], [])],
  ])('rejects %s', (_label, change) => {
    expect(() => parseOnnxWorkspace(invalidSnapshot(change))).toThrow(/read contract/i)
  })

  it.each(literalNullMutations)('rejects a non-null or altered %s', ({ path, value }) => {
    expect(() => parseOnnxWorkspace(invalidSnapshot((snapshot) => setPath(snapshot, path, value))))
      .toThrow(/read contract/i)
  })

  it.each(fixedPairs.flatMap((pair) => [
    {
      label: `${pair.label} status`,
      path: [...pair.path, 'status'],
      value: pair.status === 'NOT_STARTED' ? 'NOT_PROVEN' : 'NOT_STARTED',
    },
    {
      label: `${pair.label} availability`,
      path: [...pair.path, 'availability'],
      value: pair.availability === 'UNAVAILABLE' ? 'NOT_IMPLEMENTED' : 'UNAVAILABLE',
    },
  ]))('rejects an invalid fixed pairing for %s', ({ path, value }) => {
    expect(() => parseOnnxWorkspace(invalidSnapshot((snapshot) => setPath(snapshot, path, value))))
      .toThrow(/read contract/i)
  })

  it.each([
    ['missing a stage page', (snapshot: TestRecord) => setPath(snapshot, ['stage_pages'], (snapshot.stage_pages as unknown[]).slice(0, -1))],
    ['containing an extra stage page', (snapshot: TestRecord) => setPath(snapshot, ['stage_pages'], [...snapshot.stage_pages as unknown[], { page_id: 'pool' }])],
    ['with reordered stage pages', (snapshot: TestRecord) => {
      const pages = [...snapshot.stage_pages as unknown[]]
      ;[pages[0], pages[1]] = [pages[1], pages[0]]
      setPath(snapshot, ['stage_pages'], pages)
    }],
    ['with duplicate stage page identifiers', (snapshot: TestRecord) => {
      setPath(snapshot, ['stage_pages', 1, 'page_id'], 'data_intake')
    }],
    ['with an unknown stage page identifier', (snapshot: TestRecord) => {
      setPath(snapshot, ['stage_pages', 6, 'page_id'], 'forward')
    }],
    ['with an empty prerequisite', (snapshot: TestRecord) => {
      setPath(snapshot, ['stage_pages', 0, 'prerequisites'], ['  '])
    }],
    ['with a non-string prerequisite', (snapshot: TestRecord) => {
      setPath(snapshot, ['stage_pages', 0, 'prerequisites'], [1])
    }],
    ['with no prerequisites', (snapshot: TestRecord) => {
      setPath(snapshot, ['stage_pages', 0, 'prerequisites'], [])
    }],
    ['with Forward as a standalone stage page', (snapshot: TestRecord) => {
      setPath(snapshot, ['stage_pages', 6, 'page_id'], 'forward')
    }],
    ['with a page identifier on nested Forward', (snapshot: TestRecord) => {
      setPath(snapshot, ['challenger', 'forward', 'page_id'], 'forward')
    }],
    ['with a malformed nested Forward prerequisite', (snapshot: TestRecord) => {
      setPath(snapshot, ['challenger', 'forward', 'prerequisites'], [''])
    }],
    ['with an empty blocker code', (snapshot: TestRecord) => {
      setPath(snapshot, ['first_blocker', 'code'], '  ')
    }],
    ['with an empty blocker message', (snapshot: TestRecord) => {
      setPath(snapshot, ['first_blocker', 'message'], '')
    }],
    ['with an empty backend reason', (snapshot: TestRecord) => {
      setPath(snapshot, ['dataset', 'reason'], '   ')
    }],
    ['with an extra field in a contract section', (snapshot: TestRecord) => {
      setPath(snapshot, ['dataset', 'unexpected'], 'value')
    }],
  ])('rejects a response %s', (_label, change) => {
    expect(() => parseOnnxWorkspace(invalidSnapshot(change))).toThrow(/read contract/i)
  })

  it('rejects null, arrays, and malformed JSON rather than constructing fallback state', async () => {
    expect(() => parseOnnxWorkspace(null)).toThrow(/read contract/i)
    expect(() => parseOnnxWorkspace([])).toThrow(/read contract/i)
    const fetcher = vi.fn<typeof fetch>(async () => new Response('{', {
      headers: { 'Content-Type': 'application/json' },
    }))
    await expect(fetchOnnxWorkspace(undefined, fetcher)).rejects.toThrow(/read contract/i)
  })

  it('uses only GET and preserves application recovery-required HTTP 503 behavior', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse({
      detail: 'RECOVERY_REQUIRED: application operations are disabled',
    }, 503))

    await expect(fetchOnnxWorkspace(undefined, fetcher))
      .rejects.toThrow(/application recovery is required/i)
    expect(fetcher).toHaveBeenCalledWith('/api/v1/onnx/workspace', expect.objectContaining({ method: 'GET' }))
  })

  it('does not render partially trusted data and Retry recovers from a malformed response', async () => {
    const invalid = invalidSnapshot((snapshot) => setPath(snapshot, ['dataset', 'dataset_id'], 'FAKE_DATASET'))
    const responses = [jsonResponse(invalid), jsonResponse(validSnapshot())]
    const fetcher = vi.fn<typeof fetch>(async () => responses.shift()!)
    vi.stubGlobal('fetch', fetcher)

    render(createElement(OnnxWorkspace))
    expect(await screen.findByRole('alert')).toHaveTextContent(/read contract/i)
    expect(screen.queryByText('FAKE_DATASET')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'ONNX Overview' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Retry ONNX status read' }))
    expect(await screen.findByRole('heading', { name: 'ONNX Overview' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
  })
})
