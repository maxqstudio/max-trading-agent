import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import OptimizerPage from './OptimizerPage'


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

const specs: Record<string, [number, number, number, 'float' | 'int']> = {
  InpWeightTrend: [0.2, 2, 0.1, 'float'],
  InpWeightRange: [0.2, 2, 0.1, 'float'],
  InpWeightBreakout: [0.2, 2, 0.1, 'float'],
  InpWeightPullback: [0.2, 2, 0.1, 'float'],
  InpWeightSession: [0.1, 1.5, 0.1, 'float'],
  InpWeightShock: [0.1, 1.5, 0.1, 'float'],
  InpWeightRelative: [0.1, 1.5, 0.1, 'float'],
  InpEntryThreshold: [0.18, 0.6, 0.02, 'float'],
  InpExitReverseThreshold: [0.2, 0.75, 0.05, 'float'],
  InpMinConsensus: [0.1, 0.7, 0.05, 'float'],
  InpSL_ATR: [1, 3.2, 0.2, 'float'],
  InpTP_ATR: [1.2, 5, 0.2, 'float'],
  InpMaxHoldBars: [6, 72, 6, 'int'],
  InpShockHaltATR: [2.5, 7, 0.5, 'float'],
  InpRelativeLookback: [8, 60, 4, 'int'],
  InpMinRelativeCorr: [0.1, 0.8, 0.05, 'float'],
  InpRiskPct: [0.5, 5, 0.5, 'float'],
}

function contract() {
  const names = Object.keys(specs)
  return {
    parameters: names.map((name) => ({
      name,
      hard_min: specs[name][0],
      hard_max: specs[name][1],
      base_step: specs[name][2],
      type: specs[name][3],
      current_ea_default: specs[name][0],
    })),
    default_search_space: Object.fromEntries(names.map((name) => [
      name,
      { start: specs[name][0], step: specs[name][2], stop: specs[name][1] },
    ])),
    default_optimize_params: names,
    optimizer_parameter_count: 17,
    fixed_execution_authority: {
      InpMaxDailyLossPct: 5,
      risk_pct_upper_bound: 5,
    },
    default_kpi: {
      min_profit_factor: 1,
      min_recovery_factor: 0,
      min_expectancy_r: 0,
      min_weighted_r: 0,
      base_h1_trades_per_month: 20,
    },
    main_timeframes: ['M15', 'M20', 'M30', 'H1', 'H2', 'H3', 'H4', 'H6', 'H8', 'H12', 'D1'],
    role_timeframes: ['M1','M2','M3','M4','M5','M6','M10','M12','M15','M20','M30','H1','H2','H3','H4','H6','H8','H12','D1','W1','MN1'],
    strategy_contract: 'MAX_TRUE_MTF_DYNAMIC_V1',
    tick_models: [
      { value: 0, label: 'Every tick' },
      { value: 1, label: '1 minute OHLC' },
      { value: 2, label: 'Open prices only' },
      { value: 4, label: 'Every tick based on real ticks' },
    ],
    optimization_modes: [
      { value: 1, label: 'Slow Complete' },
      { value: 2, label: 'Fast Genetic' },
    ],
    defaults: {
      period: 'H1',
      model: 1,
      optimization: 2,
      max_rounds: 3,
      deposit: 10000,
      leverage: 100,
      optimizer_trade_exponent_alpha: 0.5,
    },
    scientist: {
      advisory_only: true,
      temperature: 0.1,
      top_pass_limit: 12,
      unavailable_behavior: 'DETERMINISTIC_FALLBACK',
      route: {
        provider: 'gemini',
        base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
        model: 'gemini-3.5-flash',
        api_key_env: 'COMPLEXPOLICY_LLM_API_KEY',
        timeout_sec: 60,
        status: 'READY',
        credential_status: 'AVAILABLE',
        temperature: 0.1,
        phase: 'STRATEGY_OPTIMIZER_RANGE_PROPOSAL',
      },
    },
  }
}

function defaultDraft() {
  const value = contract()
  return {
    symbol: '',
    relative_symbol: '',
    period: value.defaults.period,
    from_date: '2021.01.01',
    to_date: '2024.12.31',
    model: value.defaults.model,
    optimization: value.defaults.optimization,
    max_rounds: value.defaults.max_rounds,
    deposit: value.defaults.deposit,
    leverage: value.defaults.leverage,
    optimizer_trade_exponent_alpha: value.defaults.optimizer_trade_exponent_alpha,
    optimize_params: [...value.default_optimize_params],
    search_space: structuredClone(value.default_search_space),
    kpi: { ...value.default_kpi },
    scientist_assist: false,
  }
}

function draftResponse(draft = defaultDraft(), revision = 0) {
  return {
    status: 'READY',
    reason: null,
    revision,
    updated_utc: null,
    draft,
  }
}

const terminalJob = {
  job_id: 'JOB1',
  status: 'ELIGIBLE_WINNER_FOUND',
  active: false,
  current_round: 1,
  max_rounds: 3,
  created_utc: '2026-09-22T00:00:00Z',
  started_utc: '2026-09-22T00:00:01Z',
  message: 'winner',
  first_blocker: '',
  request: {
    symbol: 'XAUUSD.m',
    relative_symbol: 'EURUSD.m',
    period: 'H1',
    from_date: '2026.01.01',
    to_date: '2026.02.01',
    optimization_name: 'Fast Genetic',
    optimize_params: ['InpEntryThreshold'],
    scientist_assist: false,
    scientist: {
      provider: 'gemini',
      base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
      model: 'gemini-3.5-flash',
      api_key_env: 'COMPLEXPOLICY_LLM_API_KEY',
      timeout_sec: 60,
    },
    ea: { sha256: 'b5555f6741c60af3b60e920105caec859f7acf106c10f2ee80a1bdada50aba31' },
  },
  winner: {
    round: 1,
    mt5_pass: 7,
    profit_factor: 1.2,
    recovery_factor: 0.4,
    mean_r: 0.2,
    weighted_r: 0.1,
    trades: 25,
  },
  rounds: [{
    round_no: 1,
    phase: 'PARSED',
    report_sha256: 'abcdef1234567890',
    sidecar_sha256: '123456abcdef7890',
    parsed_passes: 1,
    eligible_passes: 1,
    winner_pass: 7,
    state: { optimizer_run_nonce: 123 },
    passes: [{
      pass_no: 7,
      profit_factor: 1.2,
      recovery_factor: 0.4,
      expectancy_r: 0.2,
      weighted_r: 0.1,
      trades: 25,
      minimum_trades_required: 20,
      eligibility: 'ELIGIBLE',
    }],
  }],
  scientist_calls: 0,
  challenger_created: 0,
  champion_mutation: 'NONE',
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

describe('M01 Optimizer UI', () => {
  it('renders 17 dense parameter rows, hard bounds, results, and winner terminology without future actions', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => terminalJob } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)

    expect(await screen.findByText('InpWeightTrend')).toBeInTheDocument()
    expect(screen.getByText('InpMinRelativeCorr')).toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')).toHaveLength(17)
    expect(screen.getByText('0.18–0.6')).toBeInTheDocument()
    expect(screen.getByText('ELIGIBLE WINNER')).toBeInTheDocument()
    expect(screen.getAllByText('0', { selector: 'dd' }).length).toBeGreaterThanOrEqual(1)

    expect(screen.queryByRole('button', { name: /promote/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /challenger/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/Run Scientist/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Scientist proposal/i)).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('EA SHA')
    expect(document.body.textContent).not.toContain('Report SHA')
    expect(document.body.textContent).not.toContain('b5555f6741c60af3b60e920105caec859f7acf106c10f2ee80a1bdada50aba31')
    expect(document.body.textContent).not.toContain('abcdef1234567890')
  })

  it('shows owner-readable risk controls and fixed daily loss authority', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => null } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)

    expect(await screen.findByText('Risk per trade (%)')).toBeInTheDocument()
    expect(screen.getByLabelText('Optimize Risk per trade (%)')).toBeChecked()
    expect(screen.getByLabelText('Start Risk per trade (%)')).toHaveValue(0.5)
    expect(screen.getByLabelText('Step Risk per trade (%)')).toHaveValue(0.5)
    expect(screen.getByLabelText('Stop Risk per trade (%)')).toHaveValue(5)
    expect(screen.getByText('Daily loss limit')).toBeInTheDocument()
    expect(screen.getByText('5.0%')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('InpMaxDailyLossPct')
  })

  it('offers only legal M07 Main timeframes while role-only periods stay internal', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => null } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)
    const select = await screen.findByLabelText('Timeframe')
    const options = Array.from(select.querySelectorAll('option')).map((item) => item.textContent)
    expect(options).toContain('M15')
    expect(options).toContain('M30')
    expect(options).toContain('H1')
    expect(options).not.toContain('M1')
    expect(options).not.toContain('M5')
    expect(options).not.toContain('M10')
    expect(options).not.toContain('M12')
  })

  it('submits the configured request on the single START action', async () => {
    let submitted: any = null
    const actionOrder: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        if (init?.method === 'PUT') {
          actionOrder.push('draft')
          const saved = JSON.parse(String(init.body))
          return Promise.resolve({ ok: true, json: async () => draftResponse(saved.draft, saved.revision) } as Response)
        }
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => null } as Response)
      }
      if (url.endsWith('/api/optimizer/preview')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: 'VALID',
            trade_sample: {
              timeframe: 'H1',
              scaled_trades_per_month: 20,
              calendar_months: 1.018,
              minimum_trades: 21,
            },
            search_space_cardinality: {
              raw_complete_grid_combinations: 10,
              authority: 'RAW_CARTESIAN_GRID_ONLY_NOT_MT5_GENETIC_TASK_COUNT',
            },
          }),
        } as Response)
      }
      if (url.endsWith('/api/optimizer/start')) {
        actionOrder.push('start')
        submitted = JSON.parse(String(init?.body))
        return Promise.resolve({ ok: true, json: async () => ({ job_id: 'JSTART', status: 'QUEUED', active: true }) } as Response)
      }
      if (url.endsWith('/api/optimizer/jobs/JSTART')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...terminalJob,
            job_id: 'JSTART',
            status: 'QUEUED',
            active: true,
            winner: null,
            rounds: [],
          }),
        } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)
    await screen.findByText('InpWeightTrend')

    const alpha = screen.getByLabelText('Trade Weight α')
    expect(alpha).toHaveValue(0.5)
    expect(alpha).toHaveAttribute('min', '0')
    expect(alpha).toHaveAttribute('max', '1')
    expect(alpha).toHaveAttribute('step', '0.05')
    fireEvent.change(alpha, { target: { value: '0.65' } })

    fireEvent.change(screen.getByLabelText('Main Symbol'), { target: { value: 'XAUUSD.m' } })
    fireEvent.change(screen.getByLabelText('Relative reference symbol'), { target: { value: 'EURUSD.m' } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'START OPTIMIZER' })).toBeEnabled(), { timeout: 1500 })
    await waitFor(() => expect(actionOrder).toEqual(['draft']), { timeout: 1500 })
    fireEvent.click(screen.getByRole('button', { name: 'START OPTIMIZER' }))

    await waitFor(() => expect(submitted).not.toBeNull())
    expect(submitted.symbol).toBe('XAUUSD.m')
    expect(submitted.relative_symbol).toBe('EURUSD.m')
    expect(submitted.scientist_assist).toBe(false)
    expect(submitted.optimizer_trade_exponent_alpha).toBe(0.65)
    expect(submitted.optimize_params).toHaveLength(17)
    expect(Object.keys(submitted.search_space)).toHaveLength(17)
    expect(actionOrder).toEqual(['draft', 'start'])
    expect(await screen.findByText('Queued')).toBeInTheDocument()
  })

  it('renders the first blocker from backend state', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...terminalJob,
            status: 'FAILED',
            first_blocker: 'WEIGHTED_R_SIDECAR_MISSING',
            winner: null,
            rounds: [],
          }),
        } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)
    expect(await screen.findByText('Optimizer evidence requires review before continuing.')).toBeInTheDocument()
  })

  it('submits STOP for the active optimizer job', async () => {
    const activeJob = {
      ...terminalJob,
      status: 'MT5_RUNNING',
      active: true,
      winner: null,
      rounds: [],
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => activeJob } as Response)
      }
      if (url.endsWith('/api/optimizer/jobs/JOB1/stop') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ...activeJob, status: 'STOPPED', active: false }),
        } as Response)
      }
      if (url.endsWith('/api/optimizer/jobs/JOB1')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ...activeJob, status: 'STOPPED', active: false }),
        } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<OptimizerPage />)
    const stop = await screen.findByRole('button', { name: 'STOP' })
    fireEvent.click(stop)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/optimizer/jobs/JOB1/stop',
        expect.objectContaining({ method: 'POST' }),
      )
    })
    expect(await screen.findByText('Stopped')).toBeInTheDocument()
  })

  it('submits RESUME only for a resumable checkpointed job', async () => {
    const waitingJob = {
      ...terminalJob,
      status: 'WAITING_FOR_REPORT',
      active: false,
      winner: null,
      rounds: [],
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => waitingJob } as Response)
      }
      if (url.endsWith('/api/optimizer/jobs/JOB1/resume') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ...waitingJob, status: 'RESUMING', active: true }),
        } as Response)
      }
      if (url.endsWith('/api/optimizer/jobs/JOB1')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ ...waitingJob, status: 'RESUMING', active: true }),
        } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<OptimizerPage />)
    const resume = await screen.findByRole('button', { name: 'RESUME' })
    fireEvent.click(resume)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/optimizer/jobs/JOB1/resume',
        expect.objectContaining({ method: 'POST' }),
      )
    })
    expect(await screen.findByText('Resuming')).toBeInTheDocument()
  })
})


describe('M02 Scientist advisory UI', () => {
  it('shows toggle and sanitized route status without secret input', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => null } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)

    const advisory = await screen.findByRole('combobox', { name: 'Scientist advisory' })
    expect(advisory).toHaveValue('OFF')
    expect(screen.getByLabelText('Scientist provider')).toHaveValue('gemini')
    expect(screen.getByLabelText('Scientist model')).toHaveValue('gemini-3.5-flash')
    expect(screen.getByLabelText('Scientist credential status')).toHaveValue('Available')
    expect(screen.getByText('Ready')).toBeInTheDocument()
    expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument()
    expect(screen.queryByText('COMPLEXPOLICY_LLM_API_KEY')).not.toBeInTheDocument()
    assertOwnerLanguageClean(document.body.textContent ?? '')

    fireEvent.change(advisory, { target: { value: 'ON' } })
    expect(advisory).toHaveValue('ON')
  })

  it('renders accepted Scientist provenance and range delta', async () => {
    const scientistJob = {
      ...terminalJob,
      status: 'NO_ELIGIBLE_WINNER_MAX_ROUNDS',
      winner: null,
      request: {
        ...terminalJob.request,
        scientist_assist: true,
        optimize_params: ['InpEntryThreshold'],
      },
      scientist_calls: 1,
      scientist_counters: {
        proposal_transitions: 1,
        actual_provider_calls: 1,
        accepted_proposals: 1,
        rejected_proposals: 0,
        fallbacks: 0,
      },
      rounds: [{
        ...terminalJob.rounds[0],
        eligible_passes: 0,
        winner_pass: undefined,
        state: {
          optimizer_run_nonce: 123,
          search_space: {
            InpEntryThreshold: { start: 0.18, step: 0.02, stop: 0.40 },
          },
        },
        scientist_decision: {
          mode: 'SCIENTIST_PROPOSAL',
          actual_llm_call: true,
          accepted: true,
          reason: 'Evidence supports a narrower bounded range.',
          validation: { status: 'ACCEPTED', reason: 'validated' },
          effective_range_source: 'SCIENTIST_PROPOSAL',
          proposed_ranges: {
            InpEntryThreshold: { start: 0.22, step: 0.02, stop: 0.38 },
          },
          effective_ranges: {
            InpEntryThreshold: { start: 0.22, step: 0.02, stop: 0.38 },
          },
          provider_provenance: {
            actual_provider: 'gemini',
            actual_model: 'gemini-3.5-flash',
          },
        },
      }],
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => scientistJob } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)

    expect(await screen.findByText('Round 1 Scientist advisory')).toBeInTheDocument()
    expect(screen.getByText('Scientist-assisted')).toBeInTheDocument()
    expect(screen.getByText('Used')).toBeInTheDocument()
    expect(screen.getByText('Accepted')).toBeInTheDocument()
    expect(screen.getByText('Evidence supports a narrower bounded range.')).toBeInTheDocument()
    expect(screen.getByText('0.18 / 0.02 / 0.4')).toBeInTheDocument()
    expect(screen.getAllByText('0.22 / 0.02 / 0.38')).toHaveLength(2)
  })

  it('keeps rejected and fallback decisions visible without authority actions', async () => {
    const roundBase = terminalJob.rounds[0]
    const scientistJob = {
      ...terminalJob,
      status: 'NO_ELIGIBLE_WINNER_MAX_ROUNDS',
      winner: null,
      request: {
        ...terminalJob.request,
        scientist_assist: true,
        optimize_params: ['InpEntryThreshold'],
      },
      scientist_calls: 1,
      scientist_counters: {
        proposal_transitions: 2,
        actual_provider_calls: 1,
        accepted_proposals: 0,
        rejected_proposals: 1,
        fallbacks: 2,
      },
      rounds: [
        {
          ...roundBase,
          round_no: 1,
          state: {
            optimizer_run_nonce: 101,
            search_space: {
              InpEntryThreshold: { start: 0.18, step: 0.02, stop: 0.40 },
            },
          },
          scientist_decision: {
            mode: 'SCIENTIST_PROPOSAL',
            actual_llm_call: true,
            accepted: false,
            reason: 'SCIENTIST_OUT_OF_BOUNDS',
            error_category: 'SCIENTIST_OUT_OF_BOUNDS',
            validation: { status: 'REJECTED', reason: 'SCIENTIST_OUT_OF_BOUNDS' },
            effective_range_source: 'DETERMINISTIC_REFINEMENT_AFTER_REJECTED_SCIENTIST_PROPOSAL',
            proposal: {
              ranges: {
                InpEntryThreshold: { start: 0.10, step: 0.02, stop: 0.20 },
              },
            },
            effective_ranges: {
              InpEntryThreshold: { start: 0.18, step: 0.02, stop: 0.28 },
            },
            provider_provenance: {
              actual_provider: 'gemini',
              actual_model: 'gemini-3.5-flash',
            },
          },
        },
        {
          ...roundBase,
          round_no: 2,
          state: {
            optimizer_run_nonce: 202,
            search_space: {
              InpEntryThreshold: { start: 0.18, step: 0.02, stop: 0.28 },
            },
          },
          scientist_decision: {
            mode: 'DETERMINISTIC_FALLBACK',
            actual_llm_call: false,
            accepted: false,
            reason: 'Scientist route unavailable.',
            error_category: 'SCIENTIST_ROUTE_UNAVAILABLE',
            validation: { status: 'FALLBACK', reason: 'SCIENTIST_ROUTE_UNAVAILABLE' },
            effective_range_source: 'DETERMINISTIC_REFINEMENT_AFTER_ROUTE_UNAVAILABLE',
            proposal: null,
            effective_ranges: {
              InpEntryThreshold: { start: 0.18, step: 0.02, stop: 0.24 },
            },
            provider_provenance: {
              configured_provider: 'gemini',
              configured_model: 'gemini-3.5-flash',
            },
          },
        },
      ],
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => scientistJob } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)

    expect(await screen.findByText('Rejected')).toBeInTheDocument()
    expect(screen.getAllByText('Review required').length).toBeGreaterThan(0)
    expect(screen.getByText('Deterministic fallback')).toBeInTheDocument()
    expect(screen.getByText('Scientist route unavailable.')).toBeInTheDocument()
    expect(screen.getAllByText('Deterministic range authority').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /promote/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /challenger/i })).not.toBeInTheDocument()
  })
})

describe('M08 qualified candidate control', () => {
  it('shows qualified-only summary, preserves selection across sort/page, and batches Owner selections', async () => {
    const currentJob = {
      ...terminalJob,
      status: 'QUALIFIED_POOL_READY',
      terminal_result: 'QUALIFIED_POOL_READY',
      winner: null,
      optimizer_result_workflow: 'QUALIFIED_POOL_OWNER_SELECTION',
      request: {
        ...terminalJob.request,
        schema: 'MAX_REBUILD_OPTIMIZER_REQUEST_V5',
        optimizer_result_workflow: 'QUALIFIED_POOL_OWNER_SELECTION',
        optimizer_fitness: {
          schema: 'MAX_OPTIMIZER_FITNESS_V2',
          formula: 'MEAN_R_X_TRADES_POW_ALPHA',
          trade_exponent_alpha: 0.5,
          mean_r_authority: 'R_ACCOUNTED_ARITHMETIC_MEAN',
          trade_count_authority: 'R_ACCOUNTED_CLOSED_TRADES',
        },
      },
      rounds: [{
        ...terminalJob.rounds[0],
        parsed_passes: 5,
        eligible_passes: 3,
        winner_pass: undefined,
        passes: [],
      }],
    }
    const row = (pass: number, mean: number) => ({
      job_id: 'JOB1',
      rank: pass,
      pass,
      round: 1,
      mean_r: mean,
      custom_fitness: mean * Math.sqrt(40 + pass),
      weighted_r: 0.1 + pass / 100,
      profit_factor: 1.4 + pass / 100,
      recovery_factor: 0.8 + pass / 100,
      trades: 40 + pass,
      required_trades: 20,
      params: { InpEntryThreshold: 0.2 + pass / 100 },
    })
    let promotedBody: any = null
    let promoteCalls = 0
    const promotionGate = deferred<Response>()
    const qualifiedRequests: string[] = []

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => currentJob } as Response)
      }
      if (url.includes('/api/optimizer/jobs/JOB1/qualified-candidates?')) {
        qualifiedRequests.push(url)
        const params = new URL(url, 'http://local').searchParams
        const page = Number(params.get('page') ?? 1)
        const items = page === 1
          ? [row(1, 0.31), row(2, 0.29)]
          : [row(3, 0.27)]
        return Promise.resolve({
          ok: true,
          json: async () => ({
            job_id: 'JOB1',
            raw_count: 5,
            qualified_count: 3,
            historical_qualified_count: 3,
            consumed_count: 0,
            deduplicated_count: 1,
            rejected_count: 1,
            page,
            page_size: Number(params.get('page_size') ?? 25),
            pages: 2,
            total: 3,
            sort: params.get('sort') ?? 'mean_r',
            order: params.get('order') ?? 'desc',
            query: params.get('q') ?? '',
            round: null,
            items,
          }),
        } as Response)
      }
      if (url.endsWith('/api/optimizer/jobs/JOB1/challengers') && init?.method === 'POST') {
        promoteCalls += 1
        promotedBody = JSON.parse(String(init.body))
        return promotionGate.promise
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)

    const summary = await screen.findByLabelText('Optimizer result summary')
    await waitFor(() => {
      expect(summary).toHaveTextContent('Raw Passes5')
      expect(summary).toHaveTextContent('Available qualified3')
      expect(summary).toHaveTextContent('Already used0')
      expect(summary).toHaveTextContent('Exact duplicates removed1')
      expect(summary).toHaveTextContent('Rejected1')
    })
    expect(screen.queryByText('ELIGIBLE WINNER')).not.toBeInTheDocument()
    expect(screen.queryByText('Historical optimizer pass evidence')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mean R' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Custom Result / Fitness' })).toBeInTheDocument()
    expect(screen.getByText('0.5', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Candidate page size')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Rows per page')).toHaveValue('25')
    expect(screen.getByRole('button', { name: 'Promote Selected to Challengers' })).toBeDisabled()

    const first = await screen.findByLabelText('Select candidate R1 P1')
    fireEvent.click(first)
    expect(screen.getByText('1 selected')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Custom Result / Fitness' }))
    await waitFor(() => {
      expect(qualifiedRequests.some((url) => url.includes('sort=custom_fitness'))).toBe(true)
    })
    fireEvent.click(screen.getByRole('button', { name: 'Profit Factor' }))
    await waitFor(() => {
      expect(qualifiedRequests.some((url) => url.includes('sort=profit_factor'))).toBe(true)
    })
    expect(screen.getByLabelText('Select candidate R1 P1')).toBeChecked()

    fireEvent.click(screen.getByRole('button', { name: 'Select current page' }))
    expect(screen.getByText('2 selected')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByLabelText('Select candidate R1 P3')).toBeInTheDocument()
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Select candidate R1 P3'))
    expect(screen.getByText('3 selected')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Promote Selected to Challengers' }))
    await waitFor(() => expect(promotedBody).not.toBeNull())
    expect(promotedBody).toEqual({
      selections: [
        { round: 1, pass: 1 },
        { round: 1, pass: 2 },
        { round: 1, pass: 3 },
      ],
    })
    const pendingPromotion = screen.getByRole('button', { name: 'Promoting...' })
    expect(pendingPromotion).toBeDisabled()
    fireEvent.click(pendingPromotion)
    expect(promoteCalls).toBe(1)

    promotionGate.resolve({
      ok: true,
      json: async () => ({
        state: 'COMMITTED',
        result: { count: promotedBody.selections.length },
      }),
    } as Response)

    expect(await screen.findByText(/3 qualified candidate\(s\) created/)).toBeInTheDocument()
    expect(screen.getByText('0 selected')).toBeInTheDocument()
  })

  it('retains shared pagination for an empty future-schema qualified pool', async () => {
    const currentJob = {
      ...terminalJob,
      status: 'QUALIFIED_POOL_READY',
      terminal_result: 'QUALIFIED_POOL_READY',
      winner: null,
      optimizer_result_workflow: 'QUALIFIED_POOL_OWNER_SELECTION',
      request: {
        ...terminalJob.request,
        schema: 'MAX_REBUILD_OPTIMIZER_REQUEST_V5',
        optimizer_result_workflow: 'QUALIFIED_POOL_OWNER_SELECTION',
      },
      rounds: [{
        ...terminalJob.rounds[0],
        parsed_passes: 4,
        eligible_passes: 0,
        winner_pass: undefined,
        passes: [],
      }],
    }

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/draft')) {
        return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      }
      if (url.endsWith('/api/optimizer/contract')) {
        return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      }
      if (url.endsWith('/api/optimizer/current')) {
        return Promise.resolve({ ok: true, json: async () => currentJob } as Response)
      }
      if (url.includes('/api/optimizer/jobs/JOB1/qualified-candidates?')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            job_id: 'JOB1',
            raw_count: 4,
            qualified_count: 0,
            historical_qualified_count: 0,
            consumed_count: 0,
            deduplicated_count: 0,
            rejected_count: 4,
            page: 1,
            page_size: 25,
            pages: 1,
            total: 0,
            sort: 'mean_r',
            order: 'desc',
            query: '',
            round: null,
            items: [],
          }),
        } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)

    expect(await screen.findByText('No qualified candidates match the current filter.')).toBeInTheDocument()
    expect(screen.getByText('Showing 0 of 0')).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(screen.getByLabelText('Rows per page')).toHaveValue('25')
    expect(screen.queryByLabelText('Candidate page size')).not.toBeInTheDocument()
    expect(screen.queryByText('Historical optimizer pass evidence')).not.toBeInTheDocument()
  })

  it('restores the complete saved draft, including ranges, account settings, and KPI values', async () => {
    const saved = defaultDraft()
    saved.symbol = 'GBPUSD.m'
    saved.deposit = 25000
    saved.leverage = 200
    saved.kpi.min_weighted_r = 0.35
    saved.search_space.InpEntryThreshold.stop = 0.42

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/contract')) return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      if (url.endsWith('/api/optimizer/current')) return Promise.resolve({ ok: true, json: async () => null } as Response)
      if (url.endsWith('/api/optimizer/draft')) return Promise.resolve({ ok: true, json: async () => draftResponse(saved, 8) } as Response)
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)

    expect(await screen.findByLabelText('Main Symbol')).toHaveValue('GBPUSD.m')
    expect(screen.getByLabelText('Initial deposit')).toHaveValue(25000)
    expect(screen.getByLabelText('Leverage')).toHaveValue(200)
    expect(screen.getByLabelText('Min Weighted R')).toHaveValue(0.35)
    expect(screen.getByLabelText('Stop InpEntryThreshold')).toHaveValue(0.42)
    expect(screen.getByText('Saved configuration restored from this device.')).toBeInTheDocument()
  })

  it('does not start from a corrupt draft until the Owner explicitly saves the shown defaults', async () => {
    let startCalls = 0
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/contract')) return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      if (url.endsWith('/api/optimizer/current')) return Promise.resolve({ ok: true, json: async () => null } as Response)
      if (url.endsWith('/api/optimizer/draft')) {
        if (init?.method === 'PUT') {
          const saved = JSON.parse(String(init.body))
          return Promise.resolve({ ok: true, json: async () => draftResponse(saved.draft, saved.revision) } as Response)
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({ ...draftResponse(), status: 'RECOVERY_REQUIRED', reason: 'DRAFT_CORRUPT', revision: 4 }),
        } as Response)
      }
      if (url.endsWith('/api/optimizer/start')) {
        startCalls += 1
        return Promise.resolve({ ok: true, json: async () => ({ job_id: 'JSTART', status: 'QUEUED', active: true }) } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)
    const start = await screen.findByRole('button', { name: 'START OPTIMIZER' })
    expect(start).toBeDisabled()
    expect(screen.getByText(/Defaults are shown but were not saved/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'SAVE DRAFT' }))
    expect(await screen.findByText('Configuration saved on this device.')).toBeInTheDocument()
    expect(startCalls).toBe(0)
  })

  it('locks duplicate START clicks immediately and exposes pending progress', async () => {
    const startGate = deferred<Response>()
    const saved = defaultDraft()
    saved.symbol = 'XAUUSD.m'
    saved.relative_symbol = 'EURUSD.m'
    let startCalls = 0
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/contract')) return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      if (url.endsWith('/api/optimizer/current')) return Promise.resolve({ ok: true, json: async () => null } as Response)
      if (url.endsWith('/api/optimizer/draft') && init?.method === 'PUT') {
        return Promise.resolve({ ok: true, json: async () => draftResponse(JSON.parse(String(init.body)).draft, JSON.parse(String(init.body)).revision) } as Response)
      }
      if (url.endsWith('/api/optimizer/draft')) return Promise.resolve({ ok: true, json: async () => draftResponse(saved, 3) } as Response)
      if (url.endsWith('/api/optimizer/preview')) return Promise.resolve({ ok: true, json: async () => ({ status: 'VALID', trade_sample: { timeframe: 'H1', scaled_trades_per_month: 20, calendar_months: 1, minimum_trades: 20 }, search_space_cardinality: { raw_complete_grid_combinations: 10, authority: 'SYNTHETIC_TEST' }, resource_preflight: { status: 'BLOCKED' } }) } as Response)
      if (url.endsWith('/api/optimizer/start')) {
        startCalls += 1
        return startGate.promise
      }
      if (url.endsWith('/api/optimizer/jobs/JSTART')) return Promise.resolve({ ok: true, json: async () => ({ ...terminalJob, job_id: 'JSTART', status: 'QUEUED', active: true, rounds: [] }) } as Response)
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)
    const start = await screen.findByRole('button', { name: 'START OPTIMIZER' })
    await waitFor(() => expect(start).toBeEnabled(), { timeout: 1500 })
    expect(screen.getByText(/MT5 uses its configured tester agents/)).toBeInTheDocument()
    expect(screen.queryByText('Agent limit')).not.toBeInTheDocument()
    act(() => {
      start.click()
      start.click()
    })
    expect(screen.getByRole('button', { name: 'Starting...' })).toBeDisabled()
    expect(startCalls).toBe(1)

    startGate.resolve({ ok: true, json: async () => ({ job_id: 'JSTART', status: 'QUEUED', active: true }) } as Response)
    expect(await screen.findByText('Queued')).toBeInTheDocument()
    expect(startCalls).toBe(1)
  })

  it('keeps the latest preview when an older preview response arrives last', async () => {
    const earlier = deferred<Response>()
    const later = deferred<Response>()
    let previewCalls = 0
    const saved = defaultDraft()
    saved.symbol = 'XAUUSD.m'
    saved.relative_symbol = 'EURUSD.m'

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/contract')) return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      if (url.endsWith('/api/optimizer/current')) return Promise.resolve({ ok: true, json: async () => null } as Response)
      if (url.endsWith('/api/optimizer/draft')) return Promise.resolve({ ok: true, json: async () => draftResponse(saved, 3) } as Response)
      if (url.endsWith('/api/optimizer/preview')) {
        previewCalls += 1
        return previewCalls === 1 ? earlier.promise : later.promise
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)
    await screen.findByLabelText('Main Symbol')
    await waitFor(() => expect(previewCalls).toBe(1), { timeout: 1500 })
    fireEvent.change(screen.getByLabelText('Main Symbol'), { target: { value: 'GBPUSD.m' } })
    await waitFor(() => expect(previewCalls).toBe(2), { timeout: 1500 })

    const preview = (trades: number) => ({
      ok: true,
      json: async () => ({
        status: 'VALID',
        trade_sample: { timeframe: 'H1', scaled_trades_per_month: trades, calendar_months: 1, minimum_trades: trades },
        search_space_cardinality: { raw_complete_grid_combinations: 1, authority: 'SYNTHETIC_TEST' },
      }),
    } as Response)
    later.resolve(preview(77))
    expect((await screen.findAllByText('77')).length).toBeGreaterThanOrEqual(1)
    earlier.resolve(preview(11))
    await new Promise((resolve) => window.setTimeout(resolve, 20))
    expect(screen.getAllByText('77').length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText('11')).not.toBeInTheDocument()
  })

  it('offers explicit stop and resume controls for an uncertain execution and blocks a new job', async () => {
    const uncertain = {
      ...terminalJob,
      status: 'EXECUTION_UNCERTAIN',
      active: false,
      winner: null,
      rounds: [],
      message: 'The previous execution needs process reconciliation.',
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/contract')) return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      if (url.endsWith('/api/optimizer/current')) return Promise.resolve({ ok: true, json: async () => uncertain } as Response)
      if (url.endsWith('/api/optimizer/draft')) return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)
    expect(await screen.findByRole('button', { name: 'START OPTIMIZER' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'STOP' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'RESUME' })).toBeEnabled()
    expect(screen.getByText('Execution needs reconciliation')).toBeInTheDocument()
    expect(screen.getByText('The previous execution needs process reconciliation.')).toBeInTheDocument()
  })

  it('refreshes authoritative resource evidence after STOP before terminal polling ends', async () => {
    const staleRuntime = {
      resource_state: 'SAFE', resolved_max_local_agents: 2, actual_max_active_agents: 0,
      min_available_ram_bytes: 16 * 1024 ** 3, peak_mt5_working_set_bytes: 80 * 1024 ** 2,
    }
    const finalRuntime = {
      resource_state: 'SAFE', resolved_max_local_agents: 2, actual_max_active_agents: 2,
      min_available_ram_bytes: 14 * 1024 ** 3, peak_mt5_working_set_bytes: 900 * 1024 ** 2,
    }
    const running = {
      ...terminalJob, status: 'MT5_RUNNING', active: true, winner: null,
      request: { ...terminalJob.request },
      rounds: [{ ...terminalJob.rounds[0], phase: 'MT5_PROCESS_CONFIRMED', state: { optimizer_run_nonce: 123, resource_runtime: staleRuntime } }],
    }
    const stopped = {
      ...running, status: 'STOPPED', active: false, message: 'Stopped by Owner',
      rounds: [{ ...running.rounds[0], state: { optimizer_run_nonce: 123, resource_runtime: finalRuntime } }],
    }
    let detailCalls = 0
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/contract')) return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      if (url.endsWith('/api/optimizer/current')) return Promise.resolve({ ok: true, json: async () => running } as Response)
      if (url.endsWith('/api/optimizer/draft')) return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      if (url.endsWith('/api/optimizer/preview')) return Promise.resolve({ ok: true, json: async () => ({ status: 'VALID', trade_sample: { timeframe: 'H1', scaled_trades_per_month: 20, calendar_months: 1, minimum_trades: 20 }, search_space_cardinality: { raw_complete_grid_combinations: 10, authority: 'SYNTHETIC_TEST' } }) } as Response)
      if (url.endsWith('/api/optimizer/jobs/JOB1/stop')) return Promise.resolve({ ok: true, json: async () => ({ job_id: 'JOB1', status: 'STOPPED', active: false }) } as Response)
      if (url.endsWith('/api/optimizer/jobs/JOB1')) {
        detailCalls += 1
        return Promise.resolve({ ok: true, json: async () => stopped } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)
    const stop = await screen.findByRole('button', { name: 'STOP' })
    expect(stop).toBeEnabled()
    expect(screen.getByText('0 / 2')).toBeInTheDocument()
    fireEvent.click(stop)
    await waitFor(() => expect(screen.getByText('2 / 2')).toBeInTheDocument())
    expect(detailCalls).toBe(1)
    expect(screen.getByText('Stopped')).toBeInTheDocument()
  })

  it('debounces candidate search and ignores a stale response after the newer filter wins', async () => {
    const stale = deferred<Response>()
    const currentJob = {
      ...terminalJob,
      status: 'QUALIFIED_POOL_READY',
      active: false,
      optimizer_result_workflow: 'QUALIFIED_POOL_OWNER_SELECTION',
      request: { ...terminalJob.request, optimizer_result_workflow: 'QUALIFIED_POOL_OWNER_SELECTION' },
    }
    const requests: string[] = []
    const page = (pass: number, query: string) => ({
      job_id: 'JOB1',
      raw_count: 2,
      qualified_count: 2,
      historical_qualified_count: 2,
      consumed_count: 0,
      deduplicated_count: 0,
      rejected_count: 0,
      page: 1,
      page_size: 25,
      pages: 1,
      total: 1,
      sort: 'mean_r',
      order: 'desc',
      query,
      round: null,
      items: [{
        job_id: 'JOB1',
        rank: pass,
        pass,
        round: 1,
        mean_r: pass / 100,
        custom_fitness: pass / 10,
        weighted_r: 0.1,
        profit_factor: 1.2,
        recovery_factor: 0.4,
        trades: 25,
        required_trades: 20,
        params: { InpEntryThreshold: 0.2 },
      }],
    })

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/optimizer/contract')) return Promise.resolve({ ok: true, json: async () => contract() } as Response)
      if (url.endsWith('/api/optimizer/current')) return Promise.resolve({ ok: true, json: async () => currentJob } as Response)
      if (url.endsWith('/api/optimizer/draft')) return Promise.resolve({ ok: true, json: async () => draftResponse() } as Response)
      if (url.includes('/api/optimizer/jobs/JOB1/qualified-candidates?')) {
        requests.push(url)
        const query = new URL(url, 'http://local').searchParams.get('q') ?? ''
        if (query === 'first') return stale.promise
        return Promise.resolve({ ok: true, json: async () => page(77, query) } as Response)
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<OptimizerPage />)
    await screen.findByLabelText('Search qualified candidates')
    await waitFor(() => expect(requests.some((url) => new URL(url, 'http://local').searchParams.get('q') === '')).toBe(true))
    fireEvent.change(screen.getByLabelText('Search qualified candidates'), { target: { value: 'first' } })
    await waitFor(() => expect(requests.some((url) => new URL(url, 'http://local').searchParams.get('q') === 'first')).toBe(true), { timeout: 1500 })
    fireEvent.change(screen.getByLabelText('Search qualified candidates'), { target: { value: 'latest' } })
    expect(await screen.findByLabelText('Select candidate R1 P77')).toBeInTheDocument()
    stale.resolve({ ok: true, json: async () => page(11, 'first') } as Response)
    await new Promise((resolve) => window.setTimeout(resolve, 20))
    expect(screen.getByLabelText('Select candidate R1 P77')).toBeInTheDocument()
    expect(screen.queryByLabelText('Select candidate R1 P11')).not.toBeInTheDocument()
    expect(requests.filter((url) => new URL(url, 'http://local').searchParams.get('q') === 'latest')).toHaveLength(1)
  })
})
