import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ArtifactsPage from './ArtifactsPage'

const generated = {
  artifact_id: 'ART-GEN',
  artifact_type: 'CHALLENGER_BACKTEST',
  producer: 'MT5_STRATEGY_TESTER',
  owner_type: 'BACKTEST',
  owner_id: 'BT-001',
  source_type: 'CHALLENGER',
  source_id: 'STRAT-001',
  created_utc: '2026-09-24T00:00:00+00:00',
  canonical_path: 'D:/MAX_REBUILD/artifacts/backtests/BT-001',
  runtime_paths: ['D:/MT5/BT-001'],
  size_bytes: 2048,
  sha256: 'a'.repeat(64),
  status: 'COMPLETED',
  in_use: false,
  retention_class: 'USER_GENERATED',
  deletable: true,
  cleanable: true,
  dependencies: [],
  storage: 'PROJECT',
}

const protectedItem = {
  artifact_id: 'ART-AUTH',
  artifact_type: 'BASELINE_EA',
  producer: 'STRATEGY_AUTHORITY',
  owner_type: 'STRATEGY_BASELINE',
  owner_id: 'Max_MTF',
  source_type: null,
  source_id: null,
  created_utc: '2026-09-23T00:00:00+00:00',
  canonical_path: 'D:/MAX_REBUILD/ea/baseline/Max_MTF.mq5',
  runtime_paths: [],
  size_bytes: 1024,
  sha256: 'b'.repeat(64),
  status: 'ACTIVE',
  in_use: true,
  retention_class: 'ACTIVE_AUTHORITY',
  deletable: false,
  cleanable: false,
  dependencies: ['CURRENT STRATEGY AUTHORITY'],
  storage: 'PROJECT',
}

function page(items = [generated, protectedItem]) {
  return {
    runtime: {
      status: 'READY',
      reason: 'READY',
      data_root: 'D:/MT5DATA',
    },
    summary: {
      total_generated_storage: 3072,
      optimizer_storage: 0,
      challenger_storage: 0,
      backtest_storage: 2048,
      runtime_storage: 0,
      safe_cleanup_bytes: 2048,
      active_in_use_bytes: 1024,
    },
    page: 1,
    page_size: 25,
    pages: 1,
    total: items.length,
    items,
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('M08 Artifacts workspace', () => {
  it('shows storage, filters/sorts, traceability and performs confirmed generated deletion', async () => {
    const inventoryUrls: string[] = []
    let actionBody: any = null
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/artifacts?')) {
        inventoryUrls.push(url)
        return Promise.resolve({ ok: true, json: async () => page() } as Response)
      }
      if (url === '/api/artifacts/ART-GEN/trace') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            artifact: generated,
            backtest: { backtest_id: 'BT-001', state: 'COMPLETED' },
            challenger: { challenger_id: 'STRAT-001', source_job_id: 'JOB-001' },
          }),
        } as Response)
      }
      if (url === '/api/artifacts/preflight' && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            selected: 1,
            deletable: 1,
            cleanable: 1,
            blocked: 0,
            project_bytes: 2048,
            runtime_bytes: 0,
            items: [{ ...generated, blocked_reason: null }],
          }),
        } as Response)
      }
      if (url === '/api/artifacts/action' && init?.method === 'POST') {
        actionBody = JSON.parse(String(init.body))
        return Promise.resolve({
          ok: true,
          json: async () => ({ status: 'COMPLETED', results: [{ backtest_id: 'BT-001', status: 'DELETED' }] }),
        } as Response)
      }
      if (url === '/api/artifacts/cleanup/preflight') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: 'READY',
            blockers: [],
            selected: 1,
            project_bytes: 2048,
            runtime_bytes: 0,
          }),
        } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<ArtifactsPage />)

    expect(await screen.findByText('Total Generated Storage')).toBeInTheDocument()
    expect(screen.getByText('3.00 KB')).toBeInTheDocument()
    expect(screen.getByText('BT-001')).toBeInTheDocument()
    expect(screen.getByText('Max_MTF')).toBeInTheDocument()
    expect(screen.getByText(/MT5 runtime: READY/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Artifact page size')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Rows per page')).toHaveValue('25')
    expect(screen.getByText('Showing 1–2 of 2')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Search artifacts'), { target: { value: 'BT-001' } })
    fireEvent.change(screen.getByLabelText('Artifact type filter'), { target: { value: 'CHALLENGER_BACKTEST' } })
    fireEvent.change(screen.getByLabelText('Artifact producer filter'), { target: { value: 'MT5_STRATEGY_TESTER' } })
    fireEvent.change(screen.getByLabelText('In-use filter'), { target: { value: 'false' } })
    fireEvent.click(screen.getByRole('button', { name: 'Size' }))
    await waitFor(() => {
      expect(inventoryUrls.some((url) =>
        url.includes('q=BT-001')
        && url.includes('type=CHALLENGER_BACKTEST')
        && url.includes('producer=MT5_STRATEGY_TESTER')
        && url.includes('in_use=false')
        && url.includes('sort=size'),
      )).toBe(true)
    })

    fireEvent.click(screen.getAllByRole('button', { name: 'Trace' })[0])
    expect(await screen.findByRole('dialog', { name: 'Artifact lineage' })).toHaveTextContent('JOB-001')
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))

    fireEvent.click(screen.getByLabelText('Select artifact BT-001'))
    expect(screen.getByText('1 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Delete Selected' }))
    const dialog = await screen.findByRole('dialog', { name: 'Artifact action preflight' })
    expect(dialog).toHaveTextContent('Selected')
    expect(dialog).toHaveTextContent('1')
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM DELETE' }))

    await waitFor(() => expect(actionBody).not.toBeNull())
    expect(actionBody).toEqual({
      artifact_ids: ['ART-GEN'],
      action: 'delete',
      confirmed: true,
    })
    expect(await screen.findByText('Selected generated artifacts deleted.')).toBeInTheDocument()
  })

  it('keeps shared pagination visible for an empty inventory page', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.startsWith('/api/artifacts?')) {
        return Promise.resolve({ ok: true, json: async () => page([]) } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<ArtifactsPage />)

    expect(await screen.findByText('No artifacts match the current filter.')).toBeInTheDocument()
    expect(screen.getByText('Showing 0 of 0')).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(screen.getByLabelText('Rows per page')).toHaveValue('25')
    expect(screen.queryByLabelText('Artifact page size')).not.toBeInTheDocument()
  })

  it('blocks protected bulk actions and shows global-clean blockers', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/artifacts?')) {
        return Promise.resolve({ ok: true, json: async () => page() } as Response)
      }
      if (url === '/api/artifacts/preflight' && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            selected: 2,
            deletable: 1,
            cleanable: 1,
            blocked: 1,
            project_bytes: 3072,
            runtime_bytes: 0,
            items: [
              { ...generated, blocked_reason: null },
              { ...protectedItem, blocked_reason: 'ACTIVE_AUTHORITY' },
            ],
          }),
        } as Response)
      }
      if (url === '/api/artifacts/cleanup/preflight') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: 'BLOCKED',
            blockers: ['ACTIVE_OPTIMIZER:JOB-X'],
            selected: 1,
            project_bytes: 2048,
            runtime_bytes: 0,
          }),
        } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<ArtifactsPage />)
    await screen.findByText('BT-001')
    fireEvent.click(screen.getByRole('button', { name: 'Select current page' }))
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Delete Selected' }))
    const bulk = await screen.findByRole('dialog', { name: 'Artifact action preflight' })
    expect(bulk).toHaveTextContent('Max_MTF: ACTIVE_AUTHORITY')
    expect(screen.getByRole('button', { name: 'CONFIRM DELETE' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    fireEvent.click(screen.getByRole('button', { name: 'Clean Generated Data' }))
    const global = await screen.findByRole('dialog', { name: 'Clean generated data preflight' })
    expect(global).toHaveTextContent('ACTIVE_OPTIMIZER:JOB-X')
    expect(screen.getByRole('button', { name: 'CONFIRM CLEAN GENERATED DATA' })).toBeDisabled()
  })
})
