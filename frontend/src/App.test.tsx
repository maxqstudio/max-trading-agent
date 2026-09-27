import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

function response(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response
}

function assertOwnerLanguageClean(text: string) {
  for (const token of [
    /\bR0[0-9]\b/,
    /\bR10\b/,
    /PASS_WAITING_OWNER/,
    /READY_TO_CONFIGURE/,
    /CP32_PARITY_REQUIRED/,
    /PARENT_FIRST_BARRIER_CONTRACT/,
    /OWNER_PARTITION_BOUNDARIES_REQUIRED/,
    /\bBLOCKED\b/,
    /\bNONE\b/,
    /NOT YET AVAILABLE/,
    /Model training \/ ONNX/,
    /Research Challenger \/ Champion mutation/,
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

const providerSettings = {
  provider_key: 'ollama',
  base_url: 'http://127.0.0.1:11434/v1',
  auth_mode: 'OLLAMA_LOCAL',
  timeout_sec: 60,
  primary_model: 'model-a',
  autonomous_fallback: [],
  chat_fallback: [],
  models: ['model-a', 'model-b'],
  credential_status: 'NOT_REQUIRED',
  status: 'READY',
  source: 'SAVED_SETTINGS',
  recovered_from_backup: false,
  ui_state: {
    left_nav_open: true,
    scientist_drawer_open: true,
    scientist_chat_model: 'model-a',
    scientist_context: 'AUTO',
  },
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

const current = {
  status: 'ACTIVE_AUTHORITY',
  current: {
    parent_strategy_id: 'STRAT-PARENT',
    hardware_summary: {
      cpu: 'Test CPU', physical_cores: 6, logical_threads: 12,
      ram_total_bytes: 32 * 1024 ** 3, gpu: null,
      capacity_equation: 'Resource-aware capacity',
    },
  },
  owner_view: {
    current_stage: 'Data preparation',
    current_state: 'Ready for Owner review',
    next_step: 'Verify the Research data source and define research and protected windows.',
    sample_requirement: { value: 7, source: 'Current Research configuration' },
    execution_sample_requirement: { value: 7, source: 'Next execution will use current configuration' },
    capacity_authority: 'Bound by supported execution scope, available hardware, and scientific evidence.',
    owner_decisions: [{ label: 'Research label definition', status: 'Defined during Data validation' }],
    lifecycle,
  },
}

const foundationPreflight = {
  status: 'READY_TO_START',
  current_champion: { strategy_id: 'STRAT-PARENT', status: 'VERIFIED' },
  parent_authority_sha256: 'a'.repeat(64),
}

const dataPreflight = {
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
  owner_view: {
    state: 'Ready to configure',
    next_step: 'Verify the broker-backed Research data source.',
    sample_requirement: { value: 7, source: 'Current Research configuration' },
    execution_sample_requirement: { value: 7, source: 'Next Data validation will use current configuration' },
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
  },
}

function installFetch() {
  const uiPatches: Array<Record<string, unknown>> = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/api/scientist/provider-settings')) return Promise.resolve(response(providerSettings))
    if (url.endsWith('/api/scientist/status')) return Promise.resolve(response({
      knowledge_status: 'READY', provider_status: 'READY', provider: 'ollama', model: 'model-a',
    }))
    if (url.endsWith('/api/scientist/chat')) return Promise.resolve(response({
      thread_id: 'SCI-1', created_utc: '2026-09-01T00:00:00Z',
      updated_utc: '2026-09-01T00:00:00Z', title: 'Research',
    }))
    if (url.endsWith('/api/scientist/threads/SCI-1/messages')) return Promise.resolve(response([]))
    if (url.endsWith('/api/scientist/ui-settings') && method === 'PUT') {
      uiPatches.push(JSON.parse(String(init?.body ?? '{}')))
      return Promise.resolve(response({}))
    }
    if (url.endsWith('/api/overview')) return Promise.resolve(response({
      project: 'MAX', phase: 'M08', milestone: 'R01', backend: { status: 'READY' },
      database: { status: 'READY' }, ea_baseline: null,
      current_strategy_champion: { strategy_id: 'STRAT-PARENT' },
      optimizer_job: null, mt5: { status: 'READY_EXECUTABLE_AND_DATA_ROOT' },
    }))
    if (url.endsWith('/api/research/r00/preflight')) return Promise.resolve(response(foundationPreflight))
    if (url.endsWith('/api/research/current')) return Promise.resolve(response(current))
    if (url.endsWith('/api/research/sample-config')) return Promise.resolve(response({
      configured: true,
      h1_minimum_trades_per_month: 7,
      updated_utc: '2026-09-26T00:00:00+00:00',
    }))
    if (url.endsWith('/api/research/r01/preflight')) return Promise.resolve(response(dataPreflight))
    if (url.endsWith('/api/research/r01/source')) return Promise.resolve(response({
      source_count: 0, source: { status: 'NOT_READY', row_coverage: null },
    }))
    throw new Error('unexpected fetch ' + method + ' ' + url)
  }))
  return uiPatches
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('MAX Rebuild shell recovery', () => {
  it('preserves accepted workspace and Strategy navigation', async () => {
    installFetch()
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()

    const workspaceNav = screen.getByRole('complementary', { name: 'MAX workspace navigation' })
    expect(within(workspaceNav).getAllByRole('button').map((button) => button.textContent)).toEqual([
      '‹', 'Strategy', 'Research', 'Artifacts', 'Settings',
    ])
    const strategyNav = screen.getByRole('navigation', { name: 'MAX navigation' })
    expect(within(strategyNav).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Overview', 'Optimizer', 'Challengers', 'Champion',
    ])
    expect(screen.getByLabelText('Scientist chat drawer')).toBeInTheDocument()
  })

  it('renders backend-owned Research workflow labels without roadmap/build codes', async () => {
    installFetch()
    render(<App />)
    const workspaceNav = screen.getByRole('complementary', { name: 'MAX workspace navigation' })
    fireEvent.click(within(workspaceNav).getByRole('button', { name: 'Research' }))

    expect(await screen.findByRole('heading', { name: 'Research' })).toBeInTheDocument()
    const researchNav = screen.getByRole('navigation', { name: 'Research navigation' })
    expect(within(researchNav).getAllByRole('button').map((button) => button.textContent)).toEqual(
      lifecycle.map((item) => item.label),
    )
    assertOwnerLanguageClean(document.querySelector('.main-workspace')?.textContent ?? '')
  })

  it('keeps normal Owner workspaces free of engineering lifecycle noise', async () => {
    installFetch()
    render(<App />)
    const workspaceNav = screen.getByRole('complementary', { name: 'MAX workspace navigation' })

    for (const workspace of ['Strategy', 'Research', 'Settings']) {
      fireEvent.click(within(workspaceNav).getByRole('button', { name: workspace }))
      await new Promise((resolve) => setTimeout(resolve, 0))
      assertOwnerLanguageClean(document.querySelector('.main-workspace')?.textContent ?? '')
    }
  })

  it('keeps Scientist drawer state persistent', async () => {
    const patches = installFetch()
    render(<App />)
    await screen.findByRole('heading', { name: 'Overview' })

    const drawer = screen.getByLabelText('Scientist chat drawer')
    fireEvent.change(within(drawer).getByLabelText('Scientist model'), { target: { value: 'model-b' } })
    fireEvent.click(screen.getByRole('button', { name: 'Hide Scientist' }))
    fireEvent.click(screen.getByRole('button', { name: 'Hide navigation' }))
    fireEvent.click(screen.getByRole('button', { name: 'Show Scientist' }))
    fireEvent.click(screen.getByRole('button', { name: 'Show navigation' }))

    expect(within(screen.getByLabelText('Scientist chat drawer')).getByLabelText('Scientist model')).toHaveValue('model-b')
    expect(patches).toEqual(expect.arrayContaining([
      { scientist_chat_model: 'model-b' },
      { scientist_drawer_open: false },
      { scientist_drawer_open: true },
      { left_nav_open: false },
      { left_nav_open: true },
    ]))
  })
})
