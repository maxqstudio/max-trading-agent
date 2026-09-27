import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ResearchPage from './ResearchPage'

function response(payload: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => payload } as Response
}

function assertClean(text: string) {
  for (const token of [
    /\bR0[0-9]\b/, /\bR10\b/, /PASS_WAITING_OWNER/, /READY_TO_CONFIGURE/,
    /\bBLOCKED\b/, /\bNONE\b/, /NOT YET AVAILABLE/,
    /Model training \/ ONNX/, /Research Challenger \/ Champion mutation/,
    /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/,
    /\bSTRAT-[A-Z0-9-]+\b/i,
    /\bJOB-[A-Z0-9-]+\b/i,
    /\bBT-[A-Z0-9-]+\b/i,
    /\b[a-f0-9]{64}\b/i,
    /SHA-256/i,
    /\bManifest\b/i,
    /\bBundle\b/i,
    /Job ID/i,
    /Source optimizer/i,
  ]) expect(text).not.toMatch(token)
}

const lifecycle = [
  { key: 'overview', label: 'Overview', availability: 'Available', description: 'Research identity and lifecycle.', scientific_rule: 'Research controls lifecycle authority.', required_authority: 'Current Research authority' },
  { key: 'data', label: 'Data', availability: 'Available', description: 'Research data authority.', scientific_rule: 'Data remains separate from lifecycle control.', required_authority: 'Initialized Research authority' },
  { key: 'model-discovery', label: 'Model Discovery', availability: 'Waiting for Owner authorization', description: 'Proposal, search and cheap screening allocate compute.', scientific_rule: 'Cheap Screen does not qualify candidates for the Qualified Pool.', required_authority: 'Accepted Data validation plus explicit Owner authorization' },
  { key: 'wfa-pool', label: 'WFA / Qualified Pool', availability: 'Waiting for qualified Discovery candidates', description: 'Full Walk-Forward qualification establishes Qualified Pool admission.', scientific_rule: 'Only Full Walk-Forward evidence qualifies Pool admission.', required_authority: 'Authorized Discovery completion' },
  { key: 'cpcv', label: 'CPCV', availability: 'Waiting for qualified Pool and Owner selection', description: 'Owner-selected candidates enter validation.', scientific_rule: 'CPCV is separate from Discovery.', required_authority: 'Qualified Pool plus Owner selection' },
  { key: 'tournament', label: 'Tournament', availability: 'Waiting for CPCV qualification', description: 'Deterministic comparison.', scientific_rule: 'Tournament cannot rescue a failed CPCV candidate.', required_authority: 'Accepted CPCV evidence' },
  { key: 'monte-carlo', label: 'Monte Carlo', availability: 'Waiting for Tournament survivors', description: 'Robustness simulation.', scientific_rule: 'Candidate identity remains immutable.', required_authority: 'Accepted Tournament survivors' },
  { key: 'locked-oos', label: 'Locked OOS', availability: 'Waiting for robustness qualification', description: 'Protected out-of-sample evaluation.', scientific_rule: 'Protected outcomes cannot become tuning feedback.', required_authority: 'Accepted robustness evidence' },
  { key: 'fresh-forward', label: 'Fresh / Forward', availability: 'Waiting for Locked OOS acceptance', description: 'Fresh protected evaluation.', scientific_rule: 'Locked OOS and Fresh / Forward remain separate protected authorities.', required_authority: 'Accepted Locked OOS evidence' },
  { key: 'final-fit', label: 'Final Fit / ONNX', availability: 'Waiting for protected validation', description: 'Final fit and model export.', scientific_rule: 'Protected validation cannot be recycled into tuning.', required_authority: 'Accepted Fresh / Forward evidence' },
  { key: 'research-challenger', label: 'Research Challenger', availability: 'Waiting for eligible final model package', description: 'Eligible model packages may become Research Challengers.', scientific_rule: 'Research cannot self-promote.', required_authority: 'Accepted final model authority' },
]

const preflight = {
  status: 'READY_TO_START',
  current_champion: { strategy_id: 'STRAT-PARENT', status: 'VERIFIED' },
  parent_authority_sha256: 'a'.repeat(64),
}

function ownerView(sample = 7) {
  return {
    current_stage: 'Data preparation',
    current_state: 'Ready for Owner review',
    next_step: 'Verify the Research data source and define research and protected windows.',
    sample_requirement: { value: sample, source: 'Current Research configuration' },
    execution_sample_requirement: { value: sample, source: 'Next execution will use current configuration' },
    capacity_authority: 'Bound by supported execution scope, available hardware, and scientific evidence.',
    owner_decisions: [{ label: 'Research label definition', status: 'Defined during Data validation' }],
    lifecycle,
  }
}

function currentAuthority(sample = 7) {
  return {
    status: 'ACTIVE_AUTHORITY',
    current: {
      parent_strategy_id: 'STRAT-PARENT',
      hardware_summary: {
        cpu: 'Test CPU', physical_cores: 6, logical_threads: 12,
        ram_total_bytes: 32 * 1024 ** 3, gpu: null,
        capacity_equation: 'Resource-aware capacity',
      },
    },
    owner_view: ownerView(sample),
  }
}

function dataOwnerView() {
  return {
    state: 'Ready to configure',
    next_step: 'Verify the broker-backed Research data source.',
    sample_requirement: { value: 7, source: 'Current Research configuration' },
    execution_sample_requirement: { value: 7, source: 'Next Data validation will use current configuration' },
    capacity_authority: 'Bound by supported execution scope, available hardware, and scientific evidence.',
    source_status: 'Not ready',
    dataset_status: 'Not started',
    feature_readiness: 'Feature parity validation required',
    label_readiness: 'Parent-bound label validation required',
    leakage_status: 'Not started',
    data_quality_status: 'Not started',
    protected_data_state: 'Research and protected windows require Owner input',
    integrity_status: 'Not started',
    physical_integrity_state: 'Physical-integrity evidence is not available until Data validation completes',
    physical_integrity: null,
    row_identity: 'Original physical source rows',
    boundary_safety: 'A target must end before the next protected window to be eligible.',
    protected_outcomes: 'Locked OOS and Fresh / Forward outcomes remain protected until their authorized validation stages.',
    timeframes: ['H1', 'H4'],
  }
}

function dataPreflight(overrides: Record<string, unknown> = {}) {
  return {
    status: 'READY_TO_CONFIGURE',
    research_id: 'RSRCH-1',
    parent_strategy_id: 'STRAT-PARENT',
    date_window: null,
    class_distribution: null,
    verified_source: { status: 'NOT_READY', source_id: null, row_coverage: null },
    existing_run: null,
    dependency_authority: null,
    discovery_supervision: null,
    protected_windows: null,
    owner_view: dataOwnerView(),
    ...overrides,
  }
}

function installStartedFetch(data = dataPreflight(), sample = 7) {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/api/research/r00/preflight')) return Promise.resolve(response(preflight))
    if (url.endsWith('/api/research/current')) return Promise.resolve(response(currentAuthority(sample)))
    if (url.endsWith('/api/research/sample-config')) return Promise.resolve(response({
      configured: true,
      h1_minimum_trades_per_month: sample,
      updated_utc: '2026-09-26T00:00:00+00:00',
    }))
    if (url.endsWith('/api/research/r01/preflight')) return Promise.resolve(response(data))
    if (url.endsWith('/api/research/r01/source')) return Promise.resolve(response({
      source_count: 0, source: { status: 'NOT_READY', row_coverage: null },
    }))
    throw new Error('unexpected fetch ' + url)
  }))
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Research Owner presentation', () => {
  it('separates lifecycle Overview from detailed Data authority', async () => {
    installStartedFetch()
    render(<ResearchPage />)

    expect(await screen.findByRole('heading', { name: 'Research' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Research authority' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Dataset identity' })).not.toBeInTheDocument()
    expect(screen.getByText('7 trades/month')).toBeInTheDocument()
    expect(screen.getByText('Current Research configuration')).toBeInTheDocument()
    expect(screen.getByText('Next execution will use current configuration · 7 trades/month')).toBeInTheDocument()
    expect(screen.getByText('Bound by supported execution scope, available hardware, and scientific evidence.')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('EXECUTABLE_CAPACITY=MIN')

    fireEvent.click(within(screen.getByRole('navigation', { name: 'Research navigation' })).getByRole('button', { name: 'Data' }))
    expect(await screen.findByRole('heading', { name: 'Data' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Dataset identity' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Physical data integrity' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Scientific readiness' })).toBeInTheDocument()
    assertClean(document.querySelector('.main-workspace')?.textContent ?? document.body.textContent ?? '')
  })

  it('renders future conceptual inspectors entirely from backend semantic authority', async () => {
    installStartedFetch()
    render(<ResearchPage />)
    await screen.findByRole('heading', { name: 'Research' })
    const nav = screen.getByRole('navigation', { name: 'Research navigation' })

    fireEvent.click(within(nav).getByRole('button', { name: 'Model Discovery' }))
    expect(await screen.findByRole('heading', { name: 'Model Discovery' })).toBeInTheDocument()
    expect(screen.getByLabelText('Research H1 minimum trades per month')).toHaveValue(7)
    expect(screen.getByText('Waiting for Owner authorization')).toBeInTheDocument()
    expect(screen.getByText('Cheap Screen does not qualify candidates for the Qualified Pool.')).toBeInTheDocument()

    fireEvent.click(within(nav).getByRole('button', { name: 'Research Challenger' }))
    expect(await screen.findByRole('heading', { name: 'Research Challenger' })).toBeInTheDocument()
    expect(screen.getByText('Research cannot self-promote.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /start|run|promote|train|export/i })).not.toBeInTheDocument()
    assertClean(document.body.textContent ?? '')
  })

  it('displays the backend-configured sample requirement rather than a frontend default', async () => {
    installStartedFetch(dataPreflight(), 13)
    render(<ResearchPage />)
    expect(await screen.findByText('13 trades/month')).toBeInTheDocument()
    expect(screen.queryByText('4 trades/month')).not.toBeInTheDocument()
    expect(screen.queryByText('8 trades/month')).not.toBeInTheDocument()
  })

  it('retains explicit Owner initialization without exposing internal authorization tokens', async () => {
    let posted: Record<string, unknown> | null = null
    let started = false
    let configuredSample: number | null = null
    const notStarted = () => ({
      status: 'NOT_STARTED',
      current: null,
      owner_view: {
        current_stage: 'Research setup',
        current_state: 'Ready to initialize',
        next_step: 'Configure the Research sample requirement and initialize Research.',
        sample_requirement: {
          value: configuredSample,
          source: configuredSample == null
            ? 'Owner configuration required'
            : 'Current Research configuration',
        },
        execution_sample_requirement: {
          value: null,
          source: 'No Research execution is running',
        },
        capacity_authority: 'Bound by supported execution scope, available hardware, and scientific evidence.',
        owner_decisions: [],
        lifecycle: lifecycle.map((item) => item.key === 'data' ? { ...item, availability: 'Waiting for Research setup' } : item),
      },
    })
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/api/research/r00/preflight')) return Promise.resolve(response(preflight))
      if (url.endsWith('/api/research/current')) {
        return Promise.resolve(response(started ? currentAuthority(9) : notStarted()))
      }
      if (url.endsWith('/api/research/sample-config') && method === 'GET') {
        return Promise.resolve(response({
          configured: configuredSample != null,
          h1_minimum_trades_per_month: configuredSample,
          updated_utc: configuredSample == null ? null : '2026-09-26T00:00:00+00:00',
        }))
      }
      if (url.endsWith('/api/research/sample-config') && method === 'PUT') {
        configuredSample = Number(JSON.parse(String(init?.body ?? '{}')).h1_minimum_trades_per_month)
        return Promise.resolve(response({
          configured: true,
          h1_minimum_trades_per_month: configuredSample,
          updated_utc: '2026-09-26T00:00:00+00:00',
        }))
      }
      if (url.endsWith('/api/research/r00/start') && method === 'POST') {
        posted = JSON.parse(String(init?.body ?? '{}'))
        started = true
        return Promise.resolve(response({}))
      }
      throw new Error('unexpected fetch ' + method + ' ' + url)
    }))

    render(<ResearchPage />)
    fireEvent.change(await screen.findByLabelText('Research H1 minimum trades per month'), { target: { value: '9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save requirement' }))
    expect(await screen.findByText('Current Research sample requirement saved.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Initialize Research' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Initialize Research' }))
    expect(await screen.findByText('Research is ready for Owner review.')).toBeInTheDocument()
    expect(posted).toMatchObject({ h1_minimum_trades_per_month: 9, confirmed: true })
    expect(document.body.textContent).not.toContain('OWNER_EXPLICIT')
    assertClean(document.body.textContent ?? '')
  })

  it('renders a fresh epoch with no Strategy Champion as a blocked empty state', async () => {
    const emptyPreflight = {
      status: 'BLOCKED',
      current_champion: null,
      parent_authority_sha256: null,
    }
    const emptyCurrent = {
      status: 'NOT_STARTED',
      current: null,
      preflight: emptyPreflight,
      owner_view: {
        ...ownerView(),
        current_state: 'Blocked',
        next_step: 'Establish a verified Strategy Champion before initializing Research.',
      },
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/research/r00/preflight')) return Promise.resolve(response(emptyPreflight))
      if (url.endsWith('/api/research/current')) return Promise.resolve(response(emptyCurrent))
      if (url.endsWith('/api/research/sample-config')) return Promise.resolve(response({
        configured: false,
        h1_minimum_trades_per_month: null,
        updated_utc: null,
      }))
      if (url.endsWith('/api/research/r01/preflight')) return Promise.resolve(response({
        status: 'BLOCKED',
        research_id: null,
        parent_strategy_id: null,
        owner_view: {
          next_step: 'Establish a verified Strategy Champion and initialize Research before preparing Data.',
        },
      }))
      if (url.endsWith('/api/research/r01/source')) return Promise.resolve(response({
        status: 'NOT_INITIALIZED',
        research_id: null,
        parent_strategy_id: null,
        source_count: 0,
        source: { status: 'NOT_STARTED', source_id: null, row_coverage: null },
      }))
      throw new Error('unexpected fetch ' + url)
    }))

    render(<ResearchPage />)

    expect(await screen.findByText('Research not initialized')).toBeInTheDocument()
    expect(screen.getByText('No current Strategy Champion')).toBeInTheDocument()
    expect(screen.getByText('Establish a verified Strategy Champion before initializing Research.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Initialize Research' })).toBeDisabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Data' }))

    expect(await screen.findByText('No Research dataset yet')).toBeInTheDocument()
    expect(screen.getByText('Establish a verified Strategy Champion and initialize Research before preparing Data.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Source from date UTC')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Verify Research data source' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Prepare Data validation' })).not.toBeInTheDocument()
  })
})
