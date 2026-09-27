import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ChallengersPage from './ChallengersPage'


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
    /Champion mutation/,
  ]) expect(text).not.toMatch(token)
}

const parameterNames = [
  'InpWeightTrend','InpWeightRange','InpWeightBreakout','InpWeightPullback',
  'InpWeightSession','InpWeightShock','InpWeightRelative','InpEntryThreshold',
  'InpExitReverseThreshold','InpMinConsensus','InpSL_ATR','InpTP_ATR',
  'InpMaxHoldBars','InpShockHaltATR','InpRelativeLookback','InpMinRelativeCorr',
]

const challengerId = 'STRAT-20260922-101450-R01-P7'

const listItem = {
  challenger_id: challengerId,
  status: 'CHALLENGER',
  role_origin: 'OPTIMIZER_WINNER',
  created_utc: '2026-09-22T10:14:50+00:00',
  updated_utc: '2026-09-22T10:14:50+00:00',
  retired_utc: null as string | null,
  source_job_id: '20260922_101450_deadbeef',
  source_round: 1,
  source_pass: 7,
  manifest_sha256: 'manifest-sha',
  kpi: {
    profit_factor: 1.2,
    recovery_factor: 0.4,
    mean_r: 0.2,
    weighted_r: 0.1,
    trades: 25,
    required_trades: 20,
  },
  integrity: 'VERIFIED',
}

const detail = {
  ...listItem,
  ea_version: '2.00',
  baseline_ea_sha256: 'baseline-sha',
  challenger_ea_sha256: 'challenger-ea-sha',
  set_sha256: 'set-sha',
  metadata_sha256: 'metadata-sha',
  manifest_sha256: 'manifest-sha',
  bundle_path: 'artifacts/strategy_challengers/' + challengerId,
  hard_gates: {
    minimum_trades: 20,
    min_profit_factor: 1,
    min_recovery_factor: 0,
    min_expectancy_r: 0,
    min_weighted_r: 0,
  },
  source_request: {
    symbol: 'XAUUSD.m',
    relative_symbol: 'EURUSD.m',
    period: 'H4',
    from_date: '2026.08.01',
    to_date: '2026.09.15',
    model: 1,
    deposit: 10000,
    leverage: 100,
  },
  provenance: {
    winner_evidence_sha256: 'winner-sha',
    run_nonce: 123,
    round_lineage: [],
  },
  artifact_integrity: {
    status: 'VERIFIED',
    winner_evidence: 'VERIFIED',
    mt5_xml: 'VERIFIED',
    weighted_r_sidecar: 'VERIFIED',
    ea_artifact: 'VERIFIED',
    set_artifact: 'VERIFIED',
    ea_set_parity: 'VERIFIED',
    manifest: 'VERIFIED',
  },
  parameter_comparison: parameterNames.map((parameter, index) => ({
    parameter,
    baseline: index + 1,
    challenger: index === 7 ? 0.2 : index + 1,
    different: index === 7,
  })),
  champion_mutation: 'NONE',
}

const championNone = {
  current: null,
  status: 'NONE',
  seed_authority: 'BASELINE_NOT_CHAMPION',
}

function registry(items = [listItem], view: 'active' | 'retired' = 'active') {
  return {
    view,
    query: '',
    sort: 'created',
    order: 'desc',
    page: 1,
    page_size: 25,
    pages: 1,
    total: items.length,
    items,
  }
}

function ok(body: unknown) {
  return Promise.resolve({ ok: true, json: async () => body } as Response)
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('M06 Strategy Challenger operations UI', () => {
  it('renders scalable active registry and keeps explicit promotion confirmation', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/challengers/registry?')) return ok(registry())
      if (url.endsWith('/api/champion')) return ok(championNone)
      if (url.endsWith('/api/challengers/' + challengerId + '/backtests')) return ok([])
      if (url.endsWith('/api/challengers/' + challengerId)) return ok(detail)
      throw new Error('unexpected fetch ' + url)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChallengersPage />)

    expect(await screen.findByRole('button', { name: 'Round 1 · Pass 7' })).toBeInTheDocument()
    const viewTabs = screen.getByLabelText('Challenger registry view')
    expect(viewTabs).toHaveClass('view-tabs')
    expect(within(viewTabs).getByRole('button', { name: 'Active' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(viewTabs).getByRole('button', { name: 'Retired / Archive' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByLabelText('Challenger page size')).not.toBeInTheDocument()
    expect(screen.getAllByLabelText('Rows per page').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Showing 1–1 of 1')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Search Challenger evidence')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run Backtest' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Retire / Archive' })).toBeEnabled()
    expect(await screen.findByText('No retained Challenger backtest history.')).toBeInTheDocument()
    expect(screen.getByText('Showing 0 of 0')).toBeInTheDocument()
    expect(screen.getAllByLabelText('Rows per page')).toHaveLength(2)

    expect(screen.getByText('Retained qualified Optimizer evidence')).toBeInTheDocument()
    expect(screen.queryByText('challenger-ea-sha')).not.toBeInTheDocument()
    expect(screen.queryByText('set-sha')).not.toBeInTheDocument()
    expect(screen.getAllByText('Verified').length).toBeGreaterThanOrEqual(8)
    expect(screen.getByText('XAUUSD.m · H4 · 2026.08.01 → 2026.09.15')).toBeInTheDocument()
    assertOwnerLanguageClean(document.body.textContent ?? '')

    for (const name of parameterNames) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
    expect(screen.getAllByText('Unchanged')).toHaveLength(15)
    expect(screen.getByText('Changed')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Promote to Champion' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Current Champion').parentElement).toHaveTextContent('Not selected')
    expect(screen.getByText('After promotion').parentElement).toHaveTextContent('Selected Challenger becomes the Strategy Champion')

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('fails closed on tampered Challenger for all authority-changing operations', async () => {
    const badDetail = {
      ...detail,
      artifact_integrity: {
        status: 'INTEGRITY_FAIL',
        reason: 'CHALLENGER_MANIFEST_HASH_MISMATCH:challenger.json',
      },
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/challengers/registry?')) {
        return ok(registry([{ ...listItem, integrity: 'INTEGRITY_FAIL' }]))
      }
      if (url.endsWith('/api/champion')) return ok(championNone)
      if (url.endsWith('/api/challengers/' + challengerId + '/backtests')) return ok([])
      if (url.endsWith('/api/challengers/' + challengerId)) return ok(badDetail)
      throw new Error('unexpected fetch ' + url)
    }))

    render(<ChallengersPage />)

    expect(await screen.findByText(/Integrity evidence requires review/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Promote to Champion' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Retire / Archive' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Run Backtest' })).toBeDisabled()
  })

  it('submits stale-protected promotion, blocks duplicates, and refreshes after completion', async () => {
    let promoted = false
    let promoteCalls = 0
    const promotionGate = deferred<Response>()
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/challengers/registry?') && !init?.method) {
        return ok(registry(promoted ? [] : [listItem]))
      }
      if (url.endsWith('/api/champion')) {
        return ok({
          current: promoted ? {
            strategy_id: challengerId,
            status: 'CURRENT',
            kpi: detail.kpi,
            params: Object.fromEntries(parameterNames.map((name) => [name, 1])),
          } : null,
          status: promoted ? 'CURRENT_STRATEGY_CHAMPION' : 'NONE',
        })
      }
      if (url.endsWith('/api/challengers/' + challengerId + '/backtests')) return ok([])
      if (url.endsWith('/api/challengers/' + challengerId) && !init?.method) return ok(detail)
      if (url.endsWith('/api/challengers/' + challengerId + '/promote') && init?.method === 'POST') {
        promoteCalls += 1
        const body = JSON.parse(String(init.body))
        expect(body).toEqual({
          expected_challenger_manifest_sha256: 'manifest-sha',
          expected_current_champion_id: null,
          confirmed: true,
        })
        return promotionGate.promise
      }
      throw new Error('unexpected fetch ' + url)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChallengersPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Promote to Champion' }))
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM PROMOTION' }))

    const pending = screen.getByRole('button', { name: 'Promoting...' })
    expect(pending).toBeDisabled()
    expect(within(pending).getByText('Promoting...')).toBeInTheDocument()
    fireEvent.click(pending)
    expect(promoteCalls).toBe(1)

    promoted = true
    promotionGate.resolve({
      ok: true,
      json: async () => ({
        promotion_id: 'PROMOTE-TEST',
        new_champion: challengerId,
        status: 'COMMITTED',
      }),
    } as Response)

    expect(await screen.findByText('Promotion completed.')).toBeInTheDocument()
    expect(await screen.findByText('No active Strategy Challenger.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('retires non-destructively with exact manifest confirmation', async () => {
    let retired = false
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/challengers/registry?') && !init?.method) {
        return ok(registry(retired ? [] : [listItem]))
      }
      if (url.endsWith('/api/champion')) return ok(championNone)
      if (url.endsWith('/api/challengers/' + challengerId + '/backtests')) return ok([])
      if (url.endsWith('/api/challengers/' + challengerId) && !init?.method) return ok(detail)
      if (url.endsWith('/api/challengers/' + challengerId + '/retire') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body))
        expect(body).toEqual({
          expected_challenger_manifest_sha256: 'manifest-sha',
          confirmed: true,
        })
        retired = true
        return ok({
          challenger_id: challengerId,
          status: 'RETIRED',
          bundle_preserved: true,
          backtest_history_preserved: 0,
        })
      }
      throw new Error('unexpected fetch ' + url)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChallengersPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Retire / Archive' }))
    expect(screen.getByText('Delete files').parentElement).toHaveTextContent('No')
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM RETIRE / ARCHIVE' }))

    expect(await screen.findByText(/Challenger retired non-destructively/)).toBeInTheDocument()
    expect(await screen.findByText('No active Strategy Challenger.')).toBeInTheDocument()
  })

  it('runs MT5 backtest from the selected Challenger contract and retains history', async () => {
    const backtestId = 'BT-20260923-150000-deadbeef'
    let completed = false
    const history = {
      backtest_id: backtestId,
      challenger_id: challengerId,
      state: 'COMPLETED',
      created_utc: '2026-09-23T15:00:00+00:00',
      source_manifest_sha256: 'manifest-sha',
      ex5_sha256: 'ex5-sha',
      report_sha256: 'report-sha',
      evidence_path: 'evidence/m06/challenger_backtests/' + backtestId,
      error: null,
      request: {
        symbol: 'XAUUSD.m',
        relative_symbol: 'EURUSD.m',
        period: 'H4',
        from_date: '2026.08.01',
        to_date: '2026.09.15',
      },
      result: {
        execution_truth: 'MT5_STRATEGY_TESTER',
        parameter_mutation: 'NONE',
        live_authority: 'NONE',
      },
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/challengers/registry?')) return ok(registry())
      if (url.endsWith('/api/champion')) return ok(championNone)
      if (url.endsWith('/api/challengers/' + challengerId) && !init?.method) return ok(detail)
      if (url.endsWith('/api/challengers/' + challengerId + '/backtests') && !init?.method) {
        return ok(completed ? [history] : [])
      }
      if (url.endsWith('/api/challengers/' + challengerId + '/backtest') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body))
        expect(body).toEqual({
          symbol: 'XAUUSD.m',
          relative_symbol: 'EURUSD.m',
          period: 'H4',
          from_date: '2026.08.01',
          to_date: '2026.09.15',
        })
        completed = true
        return ok(history)
      }
      throw new Error('unexpected fetch ' + url)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChallengersPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Run Backtest' }))

    expect(await screen.findByText(/Backtest Completed/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('Retained test')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Open Report' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Delete Backtest' })).toBeEnabled()
  })

  it('shows retained archive history without exposing active operations', async () => {
    const retiredItem = {
      ...listItem,
      status: 'RETIRED',
      retired_utc: '2026-09-23T16:00:00+00:00',
    }
    const retiredDetail = {
      ...detail,
      status: 'RETIRED',
      retired_utc: '2026-09-23T16:00:00+00:00',
    }
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/challengers/registry?')) {
        return ok(url.includes('view=retired') ? registry([retiredItem], 'retired') : registry([]))
      }
      if (url.endsWith('/api/champion')) return ok(championNone)
      if (url.endsWith('/api/challengers/' + challengerId + '/backtests')) return ok([])
      if (url.endsWith('/api/challengers/' + challengerId)) return ok(retiredDetail)
      throw new Error('unexpected fetch ' + url)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChallengersPage />)
    expect(await screen.findByText('No active Strategy Challenger.')).toBeInTheDocument()
    expect(screen.getByText('Showing 0 of 0')).toBeInTheDocument()
    const viewTabs = screen.getByLabelText('Challenger registry view')
    const activeTab = within(viewTabs).getByRole('button', { name: 'Active' })
    const retiredTab = within(viewTabs).getByRole('button', { name: 'Retired / Archive' })
    expect(activeTab).toHaveAttribute('aria-pressed', 'true')
    expect(retiredTab).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(retiredTab)

    expect(await screen.findByRole('button', { name: 'Round 1 · Pass 7' })).toBeInTheDocument()
    expect(activeTab).toHaveAttribute('aria-pressed', 'false')
    expect(retiredTab).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByText(/Strategies · page/i)).not.toBeInTheDocument()
    expect(screen.getAllByText('Retired').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('2026-09-23T16:00:00+00:00').length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByRole('button', { name: 'Promote to Champion' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run Backtest' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'CONFIRM RETIRE / ARCHIVE' })).not.toBeInTheDocument()
  })
})

describe('M08 Backtest result control UI', () => {
  it('shows retained MT5 KPIs, details, report opening, cleanup and real delete controls', async () => {
    const backtestId = 'BT-20260924-081500-deadbeef'
    let deleted = false
    let cleaned = false
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)

    const metrics = {
      total_net_profit: 125.5,
      gross_profit: 240,
      gross_loss: -114.5,
      profit_factor: 2.096,
      expected_payoff: 5.02,
      recovery_factor: 1.75,
      sharpe_ratio: 1.31,
      total_trades: 25,
      profit_trades_count: 15,
      profit_trades_pct: 60,
      loss_trades_count: 10,
      loss_trades_pct: 40,
      balance_drawdown_absolute: 12,
      balance_drawdown_maximal_amount: 71.7,
      balance_drawdown_maximal_pct: 0.72,
      balance_drawdown_relative_amount: 71.7,
      balance_drawdown_relative_pct: 0.72,
      equity_drawdown_maximal_amount: 81.2,
      equity_drawdown_maximal_pct: 0.81,
      equity_drawdown_relative_amount: 81.2,
      equity_drawdown_relative_pct: 0.81,
    }
    const backtest = {
      backtest_id: backtestId,
      challenger_id: challengerId,
      state: 'COMPLETED',
      runtime_status: cleaned ? 'CLEANED' : 'PRESENT',
      created_utc: '2026-09-24T00:15:00+00:00',
      source_manifest_sha256: 'manifest-sha',
      ex5_sha256: 'ex5-sha',
      report_sha256: 'report-sha',
      evidence_path: 'artifacts/backtests/' + backtestId,
      error: null,
      request: {
        symbol: 'XAUUSD.m',
        relative_symbol: 'EURUSD.m',
        period: 'H4',
        from_date: '2026.08.01',
        to_date: '2026.09.15',
        strategy_geometry: {
          context_tf: 'D1',
          structure_tf: 'H8',
          main_tf: 'H4',
          timing_tf: 'H1',
        },
      },
      result: {
        execution_truth: 'MT5_STRATEGY_TESTER',
        metrics_schema: 'MAX_MT5_BACKTEST_METRICS_V1',
        metrics,
        parameter_mutation: 'NONE',
        live_authority: 'NONE',
      },
      source_challenger: {
        challenger_id: challengerId,
        role_origin: 'OWNER_SELECTED_QUALIFIED_CANDIDATE',
        source_job_id: 'JOB-M08',
        source_round: 1,
        source_pass: 7,
        manifest_sha256: 'manifest-sha',
        params: { InpEntryThreshold: 0.28 },
        optimizer_source_kpi: {
          profit_factor: 1.4,
          recovery_factor: 0.8,
          mean_r: 0.22,
          weighted_r: 0.11,
          trades: 40,
          required_trades: 20,
        },
      },
      runtime_inventory: {
        runtime_status: cleaned ? 'CLEANED' : 'PRESENT',
        size_bytes: cleaned ? 0 : 4096,
        items: cleaned ? [] : [{
          type: 'EXPERT_DEPLOYMENT',
          path: 'D:/MT5/BT',
          exists: true,
          size_bytes: 4096,
        }],
      },
    }

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/api/challengers/registry?')) return ok(registry())
      if (url.endsWith('/api/champion')) return ok(championNone)
      if (url.endsWith('/api/challengers/' + challengerId + '/delete-preflight')) {
        return ok({
          challenger_id: challengerId,
          deletable: deleted,
          blockers: deleted ? [] : ['BACKTEST_HISTORY_EXISTS_DELETE_BACKTESTS_FIRST'],
          bundle_path: detail.bundle_path,
          size_bytes: 2048,
        })
      }
      if (url.endsWith('/api/challengers/' + challengerId) && method === 'GET') {
        return ok({
          ...detail,
          role_origin: 'OWNER_SELECTED_QUALIFIED_CANDIDATE',
          artifact_integrity: {
            ...detail.artifact_integrity,
            candidate_evidence: 'VERIFIED',
            winner_evidence: null,
            qualified_candidate_evidence: 'VERIFIED',
          },
        })
      }
      if (url.startsWith('/api/challengers/backtests?')) {
        return ok({
          page: 1,
          page_size: 25,
          pages: 1,
          total: deleted ? 0 : 1,
          items: deleted ? [] : [{ ...backtest, runtime_status: cleaned ? 'CLEANED' : 'PRESENT' }],
        })
      }
      if (url.endsWith('/api/challengers/backtests/' + backtestId) && method === 'GET') {
        return ok({
          ...backtest,
          runtime_status: cleaned ? 'CLEANED' : 'PRESENT',
          runtime_inventory: {
            ...backtest.runtime_inventory,
            runtime_status: cleaned ? 'CLEANED' : 'PRESENT',
            items: cleaned ? [] : backtest.runtime_inventory.items,
          },
        })
      }
      if (url.endsWith('/api/challengers/backtests/' + backtestId + '/clean-runtime') && method === 'POST') {
        cleaned = true
        return ok({ backtest_id: backtestId, runtime_status: 'CLEANED', removed_bytes: 4096 })
      }
      if (url.endsWith('/api/challengers/backtests/' + backtestId) && method === 'DELETE') {
        expect(JSON.parse(String(init?.body))).toEqual({ confirmed: true })
        deleted = true
        return ok({ backtest_id: backtestId, status: 'DELETED', removed_project_bytes: 2048, removed_runtime_bytes: 0 })
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<ChallengersPage />)

    expect(await screen.findByText('125.5')).toBeInTheDocument()
    expect(screen.getByText('2.096')).toBeInTheDocument()
    expect(screen.getByText('1.75')).toBeInTheDocument()
    expect(screen.getByText('1.31')).toBeInTheDocument()
    expect(screen.getByText('60%')).toBeInTheDocument()
    expect(screen.getByText('0.81%')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'View Details' }))
    const detailsDialog = await screen.findByRole('dialog', { name: 'Backtest details' })
    expect(detailsDialog).toHaveTextContent('MT5 report retained')
    expect(detailsDialog).toHaveTextContent('Strategy Challenger')
    expect(detailsDialog).toHaveTextContent('runtime-file details remain available through retained evidence in Artifacts')
    expect(detailsDialog).not.toHaveTextContent('report-sha')
    expect(detailsDialog).not.toHaveTextContent('JOB-M08')
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))

    fireEvent.click(screen.getByRole('button', { name: 'Open Report' }))
    expect(openSpy).toHaveBeenCalledWith(
      '/api/challengers/backtests/' + backtestId + '/report',
      '_blank',
      'noopener,noreferrer',
    )

    fireEvent.click(screen.getByRole('button', { name: 'Clean Runtime' }))
    expect(await screen.findByText(/Runtime cleaned · removed/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('Runtime cleaned')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Delete Backtest' }))
    expect(await screen.findByRole('dialog', { name: /Delete Backtest/i })).toHaveTextContent('Selected retained Backtest')
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM DELETE BACKTEST' }))
    expect(await screen.findByText('Backtest deleted.')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('No retained Challenger backtest history.')).toBeInTheDocument()
    })
  })

  it('shows delete loading, blocks duplicate submit, and re-enables after backend failure', async () => {
    const backtestId = 'BT-20260924-081600-fail0001'
    let deleteCalls = 0
    const deleteGate = deferred<Response>()
    const backtest = {
      backtest_id: backtestId,
      challenger_id: challengerId,
      state: 'COMPLETED',
      runtime_status: 'CLEANED',
      created_utc: '2026-09-24T00:16:00+00:00',
      source_manifest_sha256: 'manifest-sha',
      report_sha256: 'report-sha',
      evidence_path: 'artifacts/backtests/' + backtestId,
      error: null,
      request: {
        symbol: 'XAUUSD.m',
        relative_symbol: 'EURUSD.m',
        period: 'H4',
        from_date: '2026.08.01',
        to_date: '2026.09.15',
      },
      result: { metrics: { total_trades: 25 } },
    }

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/api/challengers/registry?')) return ok(registry())
      if (url.endsWith('/api/champion')) return ok(championNone)
      if (url.endsWith('/api/challengers/' + challengerId + '/delete-preflight')) {
        return ok({
          challenger_id: challengerId,
          deletable: false,
          blockers: ['BACKTEST_HISTORY_EXISTS_DELETE_BACKTESTS_FIRST'],
          bundle_path: detail.bundle_path,
          size_bytes: 2048,
        })
      }
      if (url.endsWith('/api/challengers/' + challengerId) && method === 'GET') return ok(detail)
      if (url.startsWith('/api/challengers/backtests?')) {
        return ok({
          page: 1,
          page_size: 25,
          pages: 1,
          total: 1,
          items: [backtest],
        })
      }
      if (url.endsWith('/api/challengers/backtests/' + backtestId) && method === 'DELETE') {
        deleteCalls += 1
        return deleteGate.promise
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<ChallengersPage />)
    expect(await screen.findByText('Retained test')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Delete Backtest' }))
    fireEvent.click(screen.getByRole('button', { name: 'CONFIRM DELETE BACKTEST' }))

    const pending = screen.getByRole('button', { name: 'Deleting...' })
    expect(pending).toBeDisabled()
    fireEvent.click(pending)
    expect(deleteCalls).toBe(1)

    deleteGate.resolve({
      ok: false,
      json: async () => ({ detail: 'BACKTEST_DELETE_BLOCKED_TEST' }),
    } as Response)

    expect(await screen.findByRole('alert')).toHaveTextContent('The Challenger operation could not be completed.')
    expect(screen.getByRole('button', { name: 'CONFIRM DELETE BACKTEST' })).toBeEnabled()
  })

  it('preflights and executes selected Backtests as an all-or-blocked bulk action', async () => {
    const ids = ['BT-20260924-090000-aaaa0001', 'BT-20260924-090100-bbbb0002']
    let bulkExecuted = false
    const makeBacktest = (backtestId: string) => ({
      backtest_id: backtestId,
      challenger_id: challengerId,
      state: 'COMPLETED',
      runtime_status: 'PRESENT',
      created_utc: '2026-09-24T01:00:00+00:00',
      source_manifest_sha256: 'manifest-sha',
      report_sha256: 'report-' + backtestId,
      evidence_path: 'artifacts/backtests/' + backtestId,
      error: null,
      request: {
        symbol: 'XAUUSD.m',
        relative_symbol: 'EURUSD.m',
        period: 'H4',
        from_date: '2026.08.01',
        to_date: '2026.09.15',
      },
      result: {
        metrics: {
          total_net_profit: 100,
          profit_factor: 1.5,
          recovery_factor: 1.2,
          sharpe_ratio: 1.1,
          total_trades: 20,
          profit_trades_pct: 55,
          equity_drawdown_maximal_pct: 1.2,
        },
      },
    })

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/api/challengers/registry?')) return ok(registry())
      if (url.endsWith('/api/champion')) return ok(championNone)
      if (url.endsWith('/api/challengers/' + challengerId + '/delete-preflight')) {
        return ok({
          challenger_id: challengerId,
          deletable: false,
          blockers: ['BACKTEST_HISTORY_EXISTS_DELETE_BACKTESTS_FIRST'],
          bundle_path: detail.bundle_path,
          size_bytes: 2048,
        })
      }
      if (url.endsWith('/api/challengers/' + challengerId) && method === 'GET') {
        return ok(detail)
      }
      if (url.startsWith('/api/challengers/backtests?')) {
        return ok({
          page: 1,
          page_size: 25,
          pages: 1,
          total: bulkExecuted ? 0 : 2,
          items: bulkExecuted ? [] : ids.map(makeBacktest),
        })
      }
      if (url.endsWith('/api/challengers/backtests/preflight') && method === 'POST') {
        const body = JSON.parse(String(init?.body))
        expect(body).toEqual({
          backtest_ids: [...ids].sort(),
          action: 'delete',
          confirmed: false,
        })
        return ok({
          action: 'delete',
          selected: 2,
          deletable: 2,
          cleanable: 0,
          blocked: 0,
          project_bytes: 4096,
          runtime_bytes: 2048,
          items: ids.map((backtest_id) => ({
            backtest_id,
            state: 'COMPLETED',
            blockers: [],
            project_bytes: 2048,
            runtime_bytes: 1024,
          })),
        })
      }
      if (url.endsWith('/api/challengers/backtests/action') && method === 'POST') {
        const body = JSON.parse(String(init?.body))
        expect(body).toEqual({
          backtest_ids: [...ids].sort(),
          action: 'delete',
          confirmed: true,
        })
        bulkExecuted = true
        return ok({
          action: 'delete',
          status: 'COMPLETED',
          results: ids.map((backtest_id) => ({ backtest_id, status: 'DELETED' })),
        })
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<ChallengersPage />)
    expect((await screen.findAllByText('Retained test')).length).toBe(2)

    fireEvent.click(screen.getByRole('button', { name: 'Select current page' }))
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Delete Selected' }))

    const dialog = await screen.findByRole('dialog', { name: 'Delete Selected Backtests' })
    expect(dialog).toHaveTextContent('Selected')
    expect(dialog).toHaveTextContent('4096')
    expect(dialog).toHaveTextContent('2048')
    fireEvent.click(within(dialog).getByRole('button', { name: 'CONFIRM' }))

    expect(await screen.findByText(/Deleted 2 selected Backtest/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('No retained Challenger backtest history.')).toBeInTheDocument()
    })
  })
})
