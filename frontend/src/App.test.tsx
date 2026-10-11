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
      '‹', 'Strategy', 'Artifacts', 'Settings',
    ])
    const strategyNav = screen.getByRole('navigation', { name: 'MAX navigation' })
    expect(within(strategyNav).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Overview', 'Optimizer', 'Challengers', 'Champion',
    ])
    expect(screen.getByLabelText('Scientist chat drawer')).toBeInTheDocument()
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
    expect(screen.queryByRole('navigation', { name: 'MAX navigation' })).not.toBeInTheDocument()
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
    const workspaceNav = screen.getByRole('complementary', { name: 'MAX workspace navigation' })

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
