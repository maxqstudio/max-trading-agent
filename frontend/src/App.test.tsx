import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

const onnxWorkspace = {
  contract_version: '1.0',
  source: 'BACKEND_ONNX_01_SKELETON',
  operational_state: {
    status: 'NOT_STARTED',
    availability: 'NOT_IMPLEMENTED',
    persisted: false,
    cycle_id: null,
    reason: 'ONNX-01 has no operational cycle store; the planning state machine is not persisted operational state.',
  },
  dataset: {
    status: 'NOT_STARTED', availability: 'UNAVAILABLE', dataset_id: null,
    snapshot_id: null, symbol: null, timeframe: null,
    reason: 'Dataset intake and snapshots are not implemented in ONNX-01.',
  },
  research_windows: {
    status: 'NOT_STARTED', availability: 'UNAVAILABLE', items: null,
    reason: 'No persisted cycle exists from which to read frozen research windows.',
  },
  scientific_authority: {
    status: 'NOT_PROVEN', availability: 'NOT_IMPLEMENTED',
    reason: 'ONNX-00 is planning authority only; no scientific evidence is available.',
  },
  hardware_capacity: {
    status: 'NOT_PROVEN', availability: 'UNAVAILABLE', gpu_vram_bytes: null,
    system_ram_bytes: null, reason: 'ONNX-01 does not inspect hardware capacity.',
  },
  discovery: {
    status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED',
    reason: 'Discovery execution is not implemented.',
    budget: { status: 'NOT_STARTED', availability: 'UNAVAILABLE', value: null, reason: 'No frozen cycle budget exists.' },
    experiment_progress: { status: 'NOT_STARTED', availability: 'UNAVAILABLE', completed: null, total: null, reason: 'No operational experiment ledger exists.' },
    qualified_pool: { status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', candidates: null, reason: 'Pool admission is not implemented.' },
  },
  stage_pages: [
    { page_id: 'data_intake', status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', reason: 'Data intake is not implemented.', prerequisites: ['ONNX-02 authorization and accepted data authority'] },
    { page_id: 'discovery', status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', reason: 'Discovery is not implemented.', prerequisites: ['Verified DATA_READY snapshot and frozen KPI contract'] },
    { page_id: 'cpcv', status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', reason: 'CPCV is not implemented.', prerequisites: ['Sealed WFA-qualified pool'] },
    { page_id: 'tournament', status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', reason: 'Tournament is not implemented.', prerequisites: ['Terminal CPCV evidence'] },
    { page_id: 'monte_carlo', status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', reason: 'Monte Carlo is not implemented.', prerequisites: ['Tournament survivors and frozen simulation policy'] },
    { page_id: 'challenger', status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', reason: 'Challenger admission is not implemented.', prerequisites: ['Monte Carlo survivors and untouched Forward PASS'] },
    { page_id: 'champion', status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', reason: 'Champion lifecycle is not implemented.', prerequisites: ['Registered Challenger and explicit Owner promotion'] },
  ],
  challenger: {
    candidates: { status: 'NOT_STARTED', availability: 'UNAVAILABLE', items: null, reason: 'No persisted Challenger registry exists.' },
    forward: { status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', reason: 'Forward execution is not implemented.', prerequisites: ['Monte Carlo survivors'] },
  },
  champion: {
    status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', identity: null,
    reason: 'ONNX Champion publication is not implemented.',
  },
  current_stage: { status: 'NOT_STARTED', availability: 'UNAVAILABLE', value: null, reason: 'No operational cycle stage can be read.' },
  checkpoint: { status: 'NOT_STARTED', availability: 'NOT_IMPLEMENTED', identity: null, reason: 'ONNX checkpoint persistence is not implemented.' },
  first_blocker: { status: 'NOT_IMPLEMENTED', availability: 'NOT_IMPLEMENTED', reason: 'The first blocker is an explicit source-phase boundary.', code: 'ONNX_EXECUTION_NOT_IMPLEMENTED', message: 'ONNX-01 is a read-only workspace shell; scientific execution is not implemented or authorized.' },
  recovery: { status: 'UNAVAILABLE', availability: 'UNAVAILABLE', available: false, reason: 'No ONNX operational state or recovery cursor exists.' },
}

function installFetch(recoveryState: {
  status: string
  reason: string
  reset_confirmation?: string
  data_loss_boundary?: string[]
} = {
  status: 'READY',
  reason: 'DATABASE_INTEGRITY_OK',
}) {
  const uiPatches: Array<Record<string, unknown>> = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.endsWith('/api/recovery/status')) {
      return Promise.resolve(response(recoveryState))
    }
    if (url.endsWith('/api/recovery/reset') && method === 'POST') {
      return Promise.resolve(response({
        status: 'RECOVERED_EMPTY_OPERATIONAL_STATE',
        quarantine: 'D:/max/state/recovery_quarantine/max-corrupt.sqlite',
      }))
    }
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
    if (url.endsWith('/api/champion')) return Promise.resolve(response({
      status: 'BASELINE_NOT_CHAMPION', seed_authority: 'BASELINE_NOT_CHAMPION', current: null,
    }))
    if (url.endsWith('/api/promotions')) return Promise.resolve(response([]))
    if (url.startsWith('/api/artifacts?')) return Promise.resolve(response({
      runtime: { status: 'UNAVAILABLE', reason: 'NOT_CONFIGURED', data_root: null },
      summary: {
        total_generated_storage: 0, optimizer_storage: 0, challenger_storage: 0,
        backtest_storage: 0, runtime_storage: 0, safe_cleanup_bytes: 0, active_in_use_bytes: 0,
      },
      page: 1, page_size: 25, pages: 0, total: 0, items: [],
    }))
    if (url.endsWith('/api/v1/onnx/workspace')) return Promise.resolve(response(onnxWorkspace))
    if (url.endsWith('/api/v2/onnx/data/workspace')) return Promise.resolve(response({
      contract_version: '2.0',
      source: 'BACKEND_ONNX_02_DATA_API',
      status: 'NOT_STARTED',
      implementation_status: 'IMPLEMENTED',
      real_data_readiness: 'NOT_PROVEN',
      latest_snapshot: null,
      window_config: null,
      readiness_evidence: null,
      snapshots: [],
      scientific_execution: 'NOT_IMPLEMENTED',
      first_blocker: 'NO_IMMUTABLE_SNAPSHOT',
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

    const workspaceNav = screen.getByRole('navigation', { name: 'MAX workspaces' })
    expect(within(workspaceNav).getAllByRole('button', { name: /^(Strategy|ONNX|Artifacts|Settings)$/ })
      .map((button) => button.textContent)).toEqual([
      'Strategy', 'ONNX', 'Artifacts', 'Settings',
    ])
    const strategyNav = screen.getByRole('navigation', { name: 'Strategy navigation' })
    expect(within(strategyNav).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Overview', 'Optimizer', 'Challengers', 'Champion',
    ])
    expect(screen.getByLabelText('Scientist chat drawer')).toBeInTheDocument()

    fireEvent.click(within(strategyNav).getByRole('button', { name: 'Champion' }))
    expect(await screen.findByRole('heading', { name: 'Strategy Champion' })).toBeInTheDocument()
    fireEvent.click(within(workspaceNav).getByRole('button', { name: 'Artifacts' }))
    expect(await screen.findByRole('heading', { name: 'Artifacts' })).toBeInTheDocument()
    fireEvent.click(within(workspaceNav).getByRole('button', { name: 'Settings' }))
    expect(await screen.findByRole('heading', { name: 'Settings' })).toBeInTheDocument()
  })

  it('shows the exact ONNX page set and backend-reported empty state without mutation controls', async () => {
    installFetch()
    render(<App />)
    await screen.findByRole('heading', { name: 'Overview' })

    const workspaceNav = screen.getByRole('navigation', { name: 'MAX workspaces' })
    fireEvent.click(within(workspaceNav).getByRole('button', { name: 'ONNX' }))
    expect(await screen.findByRole('heading', { name: 'ONNX Overview' })).toBeInTheDocument()

    const onnxNav = screen.getByRole('navigation', { name: 'ONNX pages' })
    expect(within(onnxNav).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Overview', 'Data Intake', 'Discovery', 'CPCV', 'Tournament',
      'Monte Carlo', 'Challenger', 'Champion',
    ])
    expect(screen.getAllByText('Not implemented').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Not proven').length).toBeGreaterThan(0)
    expect(screen.getByText(/no operational cycle store/i)).toBeInTheDocument()
    expect(screen.getByText(/no scientific evidence is available/i)).toBeInTheDocument()
    expect(screen.queryByText(/^Cycle ID$/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /start|train|resume|export|promote/i })).not.toBeInTheDocument()

    fireEvent.click(within(onnxNav).getByRole('button', { name: 'Data Intake' }))
    expect(await screen.findByRole('heading', { name: 'Data Intake' })).toBeInTheDocument()
    expect(screen.getByText('NO_IMMUTABLE_SNAPSHOT')).toBeInTheDocument()
    expect(screen.getByText(/do not run scientific evaluation/i)).toBeInTheDocument()

    fireEvent.click(within(onnxNav).getByRole('button', { name: 'Discovery' }))
    expect(await screen.findByRole('heading', { name: 'Discovery' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Qualified Pool' })).toBeInTheDocument()
    expect(within(onnxNav).queryByRole('button', { name: 'Pool' })).not.toBeInTheDocument()

    fireEvent.click(within(onnxNav).getByRole('button', { name: 'Challenger' }))
    expect(await screen.findByRole('heading', { name: 'Challenger' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Forward' })).toBeInTheDocument()
    expect(within(onnxNav).queryByRole('button', { name: 'Forward' })).not.toBeInTheDocument()

    fireEvent.click(within(workspaceNav).getByRole('button', { name: 'Strategy' }))
    expect(await screen.findByRole('navigation', { name: 'Strategy navigation' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'ONNX Overview' })).not.toBeInTheDocument()
  })

  it('blocks the workspace and explains explicit recovery until exact confirmation', async () => {
    installFetch({
      status: 'RECOVERY_REQUIRED',
      reason: 'DATABASE_UNOPENABLE',
      reset_confirmation: 'BACKUP_AND_RESET_CORRUPT_MAX_STATE',
      data_loss_boundary: [
        'The damaged database is preserved.',
        'Generated Strategy state is not restored.',
      ],
    })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Application state needs recovery' })).toBeInTheDocument()
    expect(screen.getByText(/Normal Strategy actions are disabled/)).toBeInTheDocument()
    expect(screen.getByText('The damaged database is preserved.')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'MAX workspaces' })).not.toBeInTheDocument()
    const reset = screen.getByRole('button', { name: 'Backup and Reset Operational State' })
    expect(reset).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/Type BACKUP_AND_RESET_CORRUPT_MAX_STATE/), {
      target: { value: 'BACKUP_AND_RESET_CORRUPT_MAX_STATE' },
    })
    expect(reset).toBeEnabled()
    fireEvent.click(reset)
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()
    expect(await screen.findByRole('status')).toHaveTextContent('Recovery completed.')
  })

  it('keeps normal Owner workspaces free of engineering lifecycle noise', async () => {
    installFetch()
    render(<App />)
    await screen.findByRole('heading', { name: 'Overview' })
    const workspaceNav = screen.getByRole('navigation', { name: 'MAX workspaces' })

    for (const workspace of ['Strategy', 'Settings']) {
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
    await waitFor(() => expect(within(drawer).getByLabelText('Scientist model')).toHaveValue('model-a'))
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
