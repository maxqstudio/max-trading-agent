import { useEffect, useMemo, useState } from 'react'
import { Pagination, SortHeader } from './DataTable'
import { compactNumber } from './tableFormat'

type ParameterContract = {
  name: string
  hard_min: number
  hard_max: number
  base_step: number
  type: 'float' | 'int'
  current_ea_default: number
}

type Contract = {
  parameters: ParameterContract[]
  default_search_space: Record<string, { start: number; step: number; stop: number }>
  default_optimize_params: string[]
  optimizer_parameter_count: number
  fixed_execution_authority: {
    InpMaxDailyLossPct: number
    risk_pct_upper_bound: number
  }
  default_kpi: {
    min_profit_factor: number
    min_recovery_factor: number
    min_expectancy_r: number
    min_weighted_r: number
    base_h1_trades_per_month: number
  }
  main_timeframes: string[]
  role_timeframes: string[]
  strategy_contract: string
  tick_models: { value: number; label: string }[]
  optimization_modes: { value: number; label: string }[]
  defaults: {
    period: string
    model: number
    optimization: number
    max_rounds: number
    deposit: number
    leverage: number
    optimizer_trade_exponent_alpha: number
  }
  scientist: {
    advisory_only: boolean
    temperature: number
    top_pass_limit: number
    unavailable_behavior: string
    route: {
      provider: string
      base_url: string
      model: string
      api_key_env: string
      timeout_sec: number
      status: string
      credential_status: string
      temperature: number
      phase: string
    }
  }
}

type Job = {
  job_id: string
  status: string
  active: boolean
  current_round: number
  max_rounds: number
  optimizer_result_workflow: 'QUALIFIED_POOL_OWNER_SELECTION' | 'LEGACY_AUTOMATIC_WINNER'
  created_utc: string
  started_utc?: string
  message: string
  first_blocker?: string
  terminal_result?: string
  request: {
    schema?: string
    symbol: string
    relative_symbol: string
    period: string
    from_date: string
    to_date: string
    optimization_name: string
    optimize_params?: string[]
    scientist_assist?: boolean
    optimizer_fitness?: {
      schema: string
      formula: string
      trade_exponent_alpha: number
      mean_r_authority: string
      trade_count_authority: string
    }
    scientist?: {
      provider: string
      base_url: string
      model: string
      api_key_env: string
      timeout_sec: number
    }
    ea: { sha256: string }
  }
  winner?: {
    round: number
    mt5_pass: number
    profit_factor: number
    recovery_factor: number
    mean_r: number
    weighted_r: number
    trades: number
  } | null
  rounds: {
    round_no: number
    phase: string
    report_path?: string
    report_sha256?: string
    sidecar_sha256?: string
    parsed_passes?: number
    eligible_passes?: number
    winner_pass?: number
    state: {
      optimizer_run_nonce?: number
      report_selection_mode?: string
      search_space?: Record<string, { start: number; step: number; stop: number }>
    }
    scientist_decision?: {
      mode: string
      actual_llm_call: boolean
      accepted: boolean
      reason: string
      error_category?: string
      validation?: { status?: string; reason?: string }
      effective_range_source: string
      proposal?: { ranges?: Record<string, { start: number; step: number; stop: number }> } | null
      proposed_ranges?: Record<string, { start: number; step: number; stop: number }>
      effective_ranges: Record<string, { start: number; step: number; stop: number }>
      provider_provenance?: {
        configured_provider?: string
        configured_model?: string
        actual_provider?: string
        actual_model?: string
        status?: string
      }
    } | null
    passes: {
      pass_no: number
      profit_factor: number
      recovery_factor: number
      expectancy_r: number
      weighted_r: number | null
      trades: number
      minimum_trades_required: number
      eligibility: string
    }[]
  }[]
  scientist_calls: number
  scientist_counters?: {
    proposal_transitions: number
    actual_provider_calls: number
    confirmed_provider_calls?: number
    unconfirmed_provider_attempts?: number
    accepted_proposals: number
    rejected_proposals: number
    fallbacks: number
  }
  challenger_created: number
  champion_mutation: string
}

type Preview = {
  status: string
  trade_sample: {
    timeframe: string
    scaled_trades_per_month: number
    calendar_months: number
    minimum_trades: number
  }
  search_space_cardinality: {
    raw_complete_grid_combinations: number
    authority: string
  }
  optimizer_fitness?: {
    schema: string
    formula: string
    trade_exponent_alpha: number
  }
}

type QualifiedCandidate = {
  rank: number
  pass: number
  round: number
  mean_r: number
  custom_fitness: number | null
  weighted_r: number
  profit_factor: number
  recovery_factor: number
  trades: number
  required_trades: number
}

type QualifiedPage = {
  job_id: string
  raw_count: number
  qualified_count: number
  historical_qualified_count: number
  consumed_count: number
  rejected_count: number
  page: number
  page_size: number
  pages: number
  total: number
  sort: string
  order: 'asc' | 'desc'
  items: QualifiedCandidate[]
}

function rangeText(value?: { start: number; step: number; stop: number }) {
  return value ? value.start + ' / ' + value.step + ' / ' + value.stop : '—'
}

function parameterLabel(name: string) {
  if (name === 'InpRiskPct') return 'Risk per trade (%)'
  return name
}

function ownerErrorMessage(value: unknown, fallback: string) {
  const detail = typeof value === 'string' ? value.trim() : ''
  if (!detail) return fallback
  if (/^[A-Z0-9_:.-]+$/.test(detail) || /[A-Z0-9]+_[A-Z0-9_]+/.test(detail)) {
    return fallback
  }
  return detail
}

function ownerOperationalStatus(value?: string) {
  const labels: Record<string, string> = {
    QUEUED: 'Queued',
    COMPILING_EA: 'Preparing strategy',
    PREPARING_MT5: 'Preparing MT5',
    MT5_RUNNING: 'MT5 running',
    MT5_COMPLETE: 'MT5 completed',
    MT5_COMPLETE_UNCONFIRMED: 'MT5 completed · verification pending',
    WAITING_FOR_REPORT: 'Waiting for MT5 report',
    REPORT_READY: 'Report ready',
    PARSING_RESULTS: 'Reading results',
    PARSED: 'Results ready',
    ROUND_COMPLETE_NO_WINNER: 'Round complete · no qualified winner',
    SCIENTIST_REQUESTING: 'Scientist advisory in progress',
    REGISTERING_CHALLENGER: 'Creating Strategy Challenger',
    RESUMING: 'Resuming',
    QUALIFIED_POOL_READY: 'Qualified candidates ready',
    ELIGIBLE_WINNER_FOUND: 'Qualified winner found',
    STRATEGY_CHALLENGER_FOUND: 'Strategy Challenger created',
    CHALLENGER_REGISTRATION_FAILED: 'Challenger creation needs review',
    NO_ELIGIBLE_WINNER_MAX_ROUNDS: 'Completed · no qualified winner',
    FAILED: 'Failed',
    STOPPED: 'Stopped',
    READY: 'Ready',
    CONFIGURED: 'Configured',
    NOT_REQUIRED: 'Not required',
    UNCONFIGURED: 'Not configured',
    VALID: 'Valid',
    PASS: 'Passed',
    REJECTED: 'Rejected',
    ACCEPTED: 'Accepted',
    FALLBACK: 'Deterministic fallback',
    AVAILABLE: 'Available',
  }
  if (!value) return 'Not available'
  return labels[value] ?? 'Review required'
}

function candidateKey(row: { round: number; pass: number }) {
  return row.round + ':' + row.pass
}

function ActionProgress({
  active,
  idle,
  pending,
}: {
  active: boolean
  idle: string
  pending: string
}) {
  return (
    <>
      {active && <span className="button-spinner" aria-hidden="true" />}
      <span>{active ? pending : idle}</span>
    </>
  )
}

export default function OptimizerPage() {
  const [contract, setContract] = useState<Contract | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [busyAction, setBusyAction] = useState('')
  const [config, setConfig] = useState<any>(null)
  const [qualified, setQualified] = useState<QualifiedPage | null>(null)
  const [qualifiedError, setQualifiedError] = useState('')
  const [qualifiedLoading, setQualifiedLoading] = useState(true)
  const [candidateSort, setCandidateSort] = useState('mean_r')
  const [candidateOrder, setCandidateOrder] = useState<'asc' | 'desc'>('desc')
  const [candidatePage, setCandidatePage] = useState(1)
  const [candidatePageSize, setCandidatePageSize] = useState(25)
  const [candidateQuery, setCandidateQuery] = useState('')
  const [candidateRound, setCandidateRound] = useState('')
  const [candidateSelection, setCandidateSelection] = useState<Set<string>>(new Set())
  const [candidateRefresh, setCandidateRefresh] = useState(0)
  const [promotionMessage, setPromotionMessage] = useState('')

  useEffect(() => {
    Promise.all([
      fetch('/api/optimizer/contract').then((r) => {
        if (!r.ok) throw new Error('Optimizer contract HTTP ' + r.status)
        return r.json()
      }),
      fetch('/api/optimizer/current').then((r) => {
        if (!r.ok) throw new Error('Optimizer current HTTP ' + r.status)
        return r.json()
      }),
    ])
      .then(([c, current]) => {
        setContract(c)
        setJob(current)
        setConfig({
          symbol: '',
          relative_symbol: '',
          period: c.defaults.period,
          from_date: '2021.01.01',
          to_date: '2024.12.31',
          model: c.defaults.model,
          optimization: c.defaults.optimization,
          max_rounds: c.defaults.max_rounds,
          deposit: c.defaults.deposit,
          leverage: c.defaults.leverage,
          optimizer_trade_exponent_alpha: c.defaults.optimizer_trade_exponent_alpha,
          optimize_params: [...c.default_optimize_params],
          search_space: structuredClone(c.default_search_space),
          kpi: {
            min_profit_factor: c.default_kpi.min_profit_factor,
            min_recovery_factor: c.default_kpi.min_recovery_factor,
            min_expectancy_r: c.default_kpi.min_expectancy_r,
            min_weighted_r: c.default_kpi.min_weighted_r,
            base_h1_trades_per_month: c.default_kpi.base_h1_trades_per_month,
          },
          scientist_assist: false,
        })
      })
      .catch((reason: Error) => setError(reason.message))
  }, [])

  useEffect(() => {
    if (!job?.active) return
    const timer = window.setInterval(() => {
      fetch('/api/optimizer/jobs/' + job.job_id)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error('Optimizer update unavailable'))))
        .then(setJob)
        .catch((reason: Error) => setError(reason.message))
    }, 2000)
    return () => window.clearInterval(timer)
  }, [job?.active, job?.job_id])

  useEffect(() => {
    if (!job?.job_id || job.optimizer_result_workflow !== 'QUALIFIED_POOL_OWNER_SELECTION') {
      return
    }
    const params = new URLSearchParams({
      sort: candidateSort,
      order: candidateOrder,
      page: String(candidatePage),
      page_size: String(candidatePageSize),
      q: candidateQuery,
    })
    if (candidateRound) params.set('round', candidateRound)
    fetch('/api/optimizer/jobs/' + job.job_id + '/qualified-candidates?' + params.toString())
      .then(async (response) => {
        const body = await response.json()
        if (!response.ok) throw new Error(ownerErrorMessage(body.detail, 'Qualified candidates unavailable'))
        return body as QualifiedPage
      })
      .then((body) => {
        setQualifiedError('')
        setQualified(body)
        if (body.page !== candidatePage) setCandidatePage(body.page)
      })
      .catch((reason: Error) => setQualifiedError(reason.message))
      .finally(() => setQualifiedLoading(false))
  }, [
    job?.job_id,
    job?.status,
    job?.optimizer_result_workflow,
    candidateSort,
    candidateOrder,
    candidatePage,
    candidatePageSize,
    candidateQuery,
    candidateRound,
    candidateRefresh,
  ])

  useEffect(() => {
    if (!config?.symbol || !config?.relative_symbol || !config?.from_date || !config?.to_date) {
      return
    }
    const timer = window.setTimeout(() => {
      fetch('/api/optimizer/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
        .then(async (r) => {
          if (!r.ok) throw new Error(ownerErrorMessage((await r.json()).detail, 'Preview unavailable'))
          return r.json()
        })
        .then((value) => {
          setPreview(value)
          setError('')
        })
        .catch((reason: Error) => {
          setPreview(null)
          setError(reason.message)
        })
    }, 350)
    return () => window.clearTimeout(timer)
  }, [config])

  const parameterByName = useMemo(() => {
    const map = new Map<string, ParameterContract>()
    contract?.parameters.forEach((parameter) => map.set(parameter.name, parameter))
    return map
  }, [contract])

  if (!contract || !config) {
    return <p role="status" className="loading">Loading optimizer contract…</p>
  }

  const selectedParams = new Set<string>(config.optimize_params)
  const visiblePreview =
    config.symbol && config.relative_symbol && config.from_date && config.to_date
      ? preview
      : null
  const currentQualifiedWorkflow =
    job?.optimizer_result_workflow === 'QUALIFIED_POOL_OWNER_SELECTION'

  function updateConfig(key: string, value: unknown) {
    setConfig((current: any) => ({ ...current, [key]: value }))
  }

  function toggleParameter(name: string) {
    const next = new Set<string>(config.optimize_params)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    updateConfig('optimize_params', Array.from(next))
  }

  function updateRange(name: string, key: 'start' | 'step' | 'stop', raw: string) {
    const parameter = parameterByName.get(name)
    if (!parameter) return
    const value = parameter.type === 'int' ? Number.parseInt(raw, 10) : Number.parseFloat(raw)
    setConfig((current: any) => ({
      ...current,
      search_space: {
        ...current.search_space,
        [name]: { ...current.search_space[name], [key]: value },
      },
    }))
  }

  async function refreshJob(jobId: string) {
    const response = await fetch('/api/optimizer/jobs/' + jobId)
    if (!response.ok) throw new Error('Optimizer state unavailable')
    setJob(await response.json())
  }

  async function start() {
    if (busy) return
    setBusy(true)
    setBusyAction('start')
    setError('')
    try {
      const response = await fetch('/api/optimizer/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      const body = await response.json()
      if (!response.ok) throw new Error(ownerErrorMessage(body.detail, 'Optimizer could not start'))
      setCandidateSelection(new Set())
      setCandidatePage(1)
      setPromotionMessage('')
      setQualifiedLoading(true)
      await refreshJob(body.job_id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
      setBusyAction('')
    }
  }

  async function action(kind: 'stop' | 'resume') {
    if (!job || busy) return
    setBusy(true)
    setBusyAction(kind)
    setError('')
    try {
      const response = await fetch('/api/optimizer/jobs/' + job.job_id + '/' + kind, {
        method: 'POST',
      })
      const body = await response.json()
      if (!response.ok) throw new Error(ownerErrorMessage(body.detail, 'Optimizer action could not be completed'))
      await refreshJob(job.job_id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
      setBusyAction('')
    }
  }

  function changeCandidateSort(field: string, nextOrder: 'asc' | 'desc') {
    setCandidateSort(field)
    setCandidateOrder(nextOrder)
    setCandidatePage(1)
  }

  function toggleCandidate(row: QualifiedCandidate) {
    const key = candidateKey(row)
    setCandidateSelection((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function toggleCurrentPage() {
    const rows = qualified?.items ?? []
    if (!rows.length) return
    const keys = rows.map(candidateKey)
    const allSelected = keys.every((key) => candidateSelection.has(key))
    setCandidateSelection((current) => {
      const next = new Set(current)
      for (const key of keys) {
        if (allSelected) next.delete(key)
        else next.add(key)
      }
      return next
    })
  }

  async function promoteSelectedCandidates() {
    if (!job || candidateSelection.size === 0 || busy) return
    setBusy(true)
    setBusyAction('promote-selected')
    setError('')
    setPromotionMessage('')
    try {
      const selections = Array.from(candidateSelection)
        .map((key) => {
          const [round, pass] = key.split(':').map(Number)
          return { round, pass }
        })
        .sort((a, b) => a.round - b.round || a.pass - b.pass)
      const response = await fetch('/api/optimizer/jobs/' + job.job_id + '/challengers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selections }),
      })
      const body = await response.json()
      if (!response.ok) throw new Error(ownerErrorMessage(body.detail, 'Strategy Challenger creation could not be completed'))
      setPromotionMessage(
        String(body.result?.count ?? body.challengers?.length ?? selections.length)
        + ' qualified candidate(s) created as independent Strategy Challengers.',
      )
      setCandidateSelection(new Set())
      setQualifiedLoading(true)
      setCandidateRefresh((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
      setBusyAction('')
    }
  }

  return (
    <div>
      <header className="page-head">
        <div>
          <p className="eyebrow">MT5 native strategy optimization</p>
          <h1>Strategy Optimizer</h1>
        </div>
      </header>

      {error && <p role="alert" className="error">{error}</p>}

      <section aria-labelledby="optimizer-config">
        <h2 id="optimizer-config">Configuration</h2>
        <div className="form-grid">
          <label>Main Symbol<input aria-label="Main Symbol" value={config.symbol} onChange={(e) => updateConfig('symbol', e.target.value)} /></label>
          <label>Relative reference symbol<input aria-label="Relative reference symbol" value={config.relative_symbol} onChange={(e) => updateConfig('relative_symbol', e.target.value)} /></label>
          <label>Timeframe<select aria-label="Timeframe" value={config.period} onChange={(e) => updateConfig('period', e.target.value)}>{contract.main_timeframes.map((tf) => <option key={tf}>{tf}</option>)}</select></label>
          <label>Optimization From<input aria-label="Optimization From" value={config.from_date} onChange={(e) => updateConfig('from_date', e.target.value)} /></label>
          <label>Optimization To<input aria-label="Optimization To" value={config.to_date} onChange={(e) => updateConfig('to_date', e.target.value)} /></label>
          <label>MT5 Tick Model<select aria-label="MT5 Tick Model" value={config.model} onChange={(e) => updateConfig('model', Number(e.target.value))}>{contract.tick_models.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          <label>Native Optimizer<select aria-label="Native Optimizer" value={config.optimization} onChange={(e) => updateConfig('optimization', Number(e.target.value))}>{contract.optimization_modes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          <label>
            Trade Weight α
            <input
              aria-label="Trade Weight α"
              type="number"
              min="0"
              max="1"
              step="0.05"
              value={config.optimizer_trade_exponent_alpha}
              onChange={(e) => updateConfig('optimizer_trade_exponent_alpha', Number(e.target.value))}
            />
          </label>
          <label>Maximum MT5 Rounds<input aria-label="Maximum MT5 Rounds" type="number" min="1" max="5" value={config.max_rounds} onChange={(e) => updateConfig('max_rounds', Number(e.target.value))} /></label>
        </div>
      </section>

      <section aria-labelledby="scientist-advisory">
        <h2 id="scientist-advisory">Scientist advisory</h2>
        <div className="form-grid">
          <label>
            Advisory
            <select
              aria-label="Scientist advisory"
              value={config.scientist_assist ? 'ON' : 'OFF'}
              onChange={(e) => updateConfig('scientist_assist', e.target.value === 'ON')}
            >
              <option value="OFF">Disabled</option>
              <option value="ON">Enabled</option>
            </select>
          </label>
          <label>Provider<input aria-label="Scientist provider" value={contract.scientist.route.provider || '—'} disabled /></label>
          <label>Model<input aria-label="Scientist model" value={contract.scientist.route.model || '—'} disabled /></label>
          <label>Credential<input aria-label="Scientist credential status" value={ownerOperationalStatus(contract.scientist.route.credential_status)} disabled /></label>
        </div>
        <dl className="facts compact">
          <div><dt>Route status</dt><dd><strong>{ownerOperationalStatus(contract.scientist.route.status)}</strong></dd></div>
          <div><dt>Authority</dt><dd>Read-only range advice · deterministic validation remains authoritative</dd></div>
          <div><dt>Unavailable behavior</dt><dd>Deterministic fallback; optimizer start remains legal</dd></div>
        </dl>
      </section>

      <section aria-labelledby="optimizer-kpi">
        <h2 id="optimizer-kpi">Strategy Optimizer KPI</h2>
        <div className="form-grid">
          <label>Min PF<input aria-label="Min PF" type="number" step="0.01" value={config.kpi.min_profit_factor} onChange={(e) => updateConfig('kpi', { ...config.kpi, min_profit_factor: Number(e.target.value) })} /></label>
          <label>Min RF<input aria-label="Min RF" type="number" step="0.01" value={config.kpi.min_recovery_factor} onChange={(e) => updateConfig('kpi', { ...config.kpi, min_recovery_factor: Number(e.target.value) })} /></label>
          <label>Min Mean R<input aria-label="Min Mean R" type="number" step="0.01" value={config.kpi.min_expectancy_r} onChange={(e) => updateConfig('kpi', { ...config.kpi, min_expectancy_r: Number(e.target.value) })} /></label>
          <label>Min Weighted R<input aria-label="Min Weighted R" type="number" step="0.01" value={config.kpi.min_weighted_r} onChange={(e) => updateConfig('kpi', { ...config.kpi, min_weighted_r: Number(e.target.value) })} /></label>
          <label>H1 min trades/month<input aria-label="H1 min trades/month" type="number" min="1" value={config.kpi.base_h1_trades_per_month} onChange={(e) => updateConfig('kpi', { ...config.kpi, base_h1_trades_per_month: Number(e.target.value) })} /></label>
        </div>
        <dl className="facts compact">
          <div><dt>Current timeframe</dt><dd>{visiblePreview?.trade_sample.timeframe ?? config.period}</dd></div>
          <div><dt>Scaled min trades/month</dt><dd>{visiblePreview?.trade_sample.scaled_trades_per_month ?? '—'}</dd></div>
          <div><dt>Exact range duration</dt><dd>{visiblePreview ? visiblePreview.trade_sample.calendar_months.toFixed(3) + ' months' : '—'}</dd></div>
          <div><dt>Required minimum closed trades</dt><dd>{visiblePreview?.trade_sample.minimum_trades ?? '—'}</dd></div>
          <div><dt>Raw complete grid combinations</dt><dd>{visiblePreview?.search_space_cardinality.raw_complete_grid_combinations?.toLocaleString() ?? '—'} · context only, not expected MT5 genetic tasks</dd></div>
        </dl>
      </section>

      <section aria-labelledby="optimizer-parameters">
        <h2 id="optimizer-parameters">Parameters</h2>
        <dl className="facts compact">
          <div><dt>Daily loss limit</dt><dd>{contract.fixed_execution_authority.InpMaxDailyLossPct.toFixed(1)}%</dd></div>
        </dl>
        <div className="table-wrap optimizer-parameter-wrap">
          <table className="optimizer-parameter-table">
            <colgroup>
              <col className="optimizer-param-name" />
              <col className="optimizer-param-toggle" />
              <col className="optimizer-param-current" />
              <col className="optimizer-param-range" />
              <col className="optimizer-param-range" />
              <col className="optimizer-param-range" />
              <col className="optimizer-param-bound" />
            </colgroup>
            <thead><tr><th>Parameter</th><th>Optimize</th><th>Current</th><th>Start</th><th>Step</th><th>Stop</th><th>Hard Bound</th></tr></thead>
            <tbody>
              {contract.parameters.map((parameter) => {
                const spec = config.search_space[parameter.name]
                const isSelected = selectedParams.has(parameter.name)
                return (
                  <tr key={parameter.name}>
                    <td className={parameter.name === 'InpRiskPct' ? '' : 'mono'}>{parameterLabel(parameter.name)}</td>
                    <td><input aria-label={'Optimize ' + parameterLabel(parameter.name)} type="checkbox" checked={isSelected} onChange={() => toggleParameter(parameter.name)} /></td>
                    <td>{parameter.current_ea_default}</td>
                    <td><input aria-label={'Start ' + parameterLabel(parameter.name)} disabled={!isSelected} type="number" value={spec.start} step={parameter.base_step} onChange={(e) => updateRange(parameter.name, 'start', e.target.value)} /></td>
                    <td><input aria-label={'Step ' + parameterLabel(parameter.name)} disabled={!isSelected} type="number" value={spec.step} step={parameter.base_step} onChange={(e) => updateRange(parameter.name, 'step', e.target.value)} /></td>
                    <td><input aria-label={'Stop ' + parameterLabel(parameter.name)} disabled={!isSelected} type="number" value={spec.stop} step={parameter.base_step} onChange={(e) => updateRange(parameter.name, 'stop', e.target.value)} /></td>
                    <td>{parameter.hard_min}–{parameter.hard_max}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="optimizer-actions">
        <h2 id="optimizer-actions">Owner actions</h2>
        <div className="actions">
          <button onClick={start} disabled={busy || Boolean(job?.active)}>
            <ActionProgress active={busyAction === 'start'} idle="START OPTIMIZER" pending="Starting..." />
          </button>
          <button onClick={() => action('stop')} disabled={busy || !job?.active}>
            <ActionProgress active={busyAction === 'stop'} idle="STOP" pending="Stopping..." />
          </button>
          <button onClick={() => action('resume')} disabled={busy || !job || !['WAITING_FOR_REPORT', 'MT5_RUNNING', 'MT5_COMPLETE', 'MT5_COMPLETE_UNCONFIRMED', 'REPORT_READY', 'ROUND_COMPLETE_NO_WINNER', 'SCIENTIST_REQUESTING', 'REGISTERING_CHALLENGER', 'CHALLENGER_REGISTRATION_FAILED', 'RESUMING'].includes(job.status)}>
            <ActionProgress active={busyAction === 'resume'} idle="RESUME" pending="Resuming..." />
          </button>
        </div>
      </section>

      {job && (
        <section aria-labelledby="active-job">
          <h2 id="active-job">Latest optimizer job</h2>
          <dl className="facts compact">
            <div><dt>State</dt><dd><strong>{ownerOperationalStatus(job.status)}</strong></dd></div>
            <div><dt>Round</dt><dd>{job.current_round} / {job.max_rounds}</dd></div>
            <div><dt>MT5 method</dt><dd>{job.request.optimization_name}</dd></div>
            <div><dt>Trade Weight α</dt><dd>{job.request.optimizer_fitness?.trade_exponent_alpha ?? 'Legacy Mean R'}</dd></div>
            <div><dt>Market</dt><dd>{job.request.symbol} / {job.request.relative_symbol} · {job.request.period} · {job.request.from_date} → {job.request.to_date}</dd></div>
            <div><dt>Started</dt><dd>{job.started_utc ?? job.created_utc}</dd></div>
            <div><dt>Scheduling</dt><dd>MT5 owns native pass/task scheduling</dd></div>
            {job.first_blocker && <div><dt>Constraint</dt><dd className="error-text">Optimizer evidence requires review before continuing.</dd></div>}
            <div><dt>Scientist advisory</dt><dd>{job.request.scientist_assist ? 'Enabled' : 'Disabled'}</dd></div>
            <div><dt>Scientist route</dt><dd>{job.request.scientist ? job.request.scientist.provider + ' · ' + job.request.scientist.model : 'Legacy deterministic job'}</dd></div>
            <div><dt>Scientist calls</dt><dd>{job.scientist_calls}</dd></div>
            <div><dt>Scientist fallbacks</dt><dd>{job.scientist_counters?.fallbacks ?? 0}</dd></div>
          </dl>

          <h2>Round history</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Round</th><th>Status</th><th>Parsed</th><th>Qualified</th></tr></thead>
              <tbody>
                {job.rounds.map((round) => (
                  <tr key={round.round_no}>
                    <td>{round.round_no}</td>
                    <td>{ownerOperationalStatus(round.phase)}</td>
                    <td>{round.parsed_passes ?? '—'}</td>
                    <td>{round.eligible_passes ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {job.rounds.map((round) => round.scientist_decision && (
            <div key={'scientist-' + round.round_no}>
              <h2>Round {round.round_no} Scientist advisory</h2>
              <dl className="facts compact">
                <div><dt>Mode</dt><dd>{round.scientist_decision.actual_llm_call ? 'Scientist-assisted' : 'Deterministic'}</dd></div>
                <div><dt>Scientist call</dt><dd>{round.scientist_decision.actual_llm_call ? 'Used' : 'Not used'}</dd></div>
                <div><dt>Validation</dt><dd>{ownerOperationalStatus(round.scientist_decision.validation?.status)}</dd></div>
                <div><dt>Provider</dt><dd>{round.scientist_decision.provider_provenance?.actual_provider ?? round.scientist_decision.provider_provenance?.configured_provider ?? '—'}</dd></div>
                <div><dt>Model</dt><dd>{round.scientist_decision.provider_provenance?.actual_model ?? round.scientist_decision.provider_provenance?.configured_model ?? '—'}</dd></div>
                <div><dt>Effective range</dt><dd>{round.scientist_decision.actual_llm_call ? 'Validated Scientist proposal' : 'Deterministic range authority'}</dd></div>
                {round.scientist_decision.error_category && <div><dt>Advisory state</dt><dd>Review required</dd></div>}
                <div><dt>Reason</dt><dd>{ownerErrorMessage(round.scientist_decision.reason, 'Scientist advisory was unavailable or rejected; deterministic authority was used.')}</dd></div>
              </dl>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Parameter</th><th>Previous</th><th>Proposed</th><th>Effective</th></tr></thead>
                  <tbody>
                    {(job.request.optimize_params ?? []).map((name) => {
                      const proposed = round.scientist_decision?.proposed_ranges?.[name]
                        ?? round.scientist_decision?.proposal?.ranges?.[name]
                      return (
                        <tr key={name}>
                          <td className="mono">{name}</td>
                          <td>{rangeText(round.state.search_space?.[name])}</td>
                          <td>{rangeText(proposed)}</td>
                          <td>{rangeText(round.scientist_decision?.effective_ranges?.[name])}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ))}

          {currentQualifiedWorkflow ? (
          <div className="results-control">
            <h2>Qualified candidates</h2>
            <div className="result-summary" aria-label="Optimizer result summary">
              <div><span>Raw Passes</span><strong>{qualified?.raw_count ?? '—'}</strong></div>
              <div><span>Available qualified</span><strong>{qualified?.qualified_count ?? '—'}</strong></div>
              <div><span>Already used</span><strong>{qualified?.consumed_count ?? '—'}</strong></div>
              <div><span>Rejected</span><strong>{qualified?.rejected_count ?? '—'}</strong></div>
            </div>

            <div className="table-toolbar">
              <input
                aria-label="Search qualified candidates"
                placeholder="Search pass, round, parameter"
                value={candidateQuery}
                onChange={(event) => {
                  setQualifiedLoading(true)
                  setCandidateQuery(event.target.value)
                  setCandidatePage(1)
                }}
              />
              <select
                aria-label="Filter candidate round"
                value={candidateRound}
                onChange={(event) => {
                  setQualifiedLoading(true)
                  setCandidateRound(event.target.value)
                  setCandidatePage(1)
                }}
              >
                <option value="">All rounds</option>
                {job.rounds.filter((round) => round.phase === 'PARSED').map((round) => (
                  <option key={round.round_no} value={round.round_no}>Round {round.round_no}</option>
                ))}
              </select>
              <button type="button" onClick={toggleCurrentPage} disabled={!qualified?.items.length || busy}>
                Select current page
              </button>
              <button type="button" onClick={() => setCandidateSelection(new Set())} disabled={!candidateSelection.size || busy}>
                Clear selection
              </button>
              <span>{candidateSelection.size} selected</span>
              <button
                type="button"
                onClick={promoteSelectedCandidates}
                disabled={busy || candidateSelection.size === 0}
              >
                <ActionProgress
                  active={busyAction === 'promote-selected'}
                  idle="Promote Selected to Challengers"
                  pending="Promoting..."
                />
              </button>
            </div>

            {promotionMessage && <p role="status" className="success">{promotionMessage}</p>}
            {qualifiedError && <p role="alert" className="error">{qualifiedError}</p>}
            {qualifiedLoading && <p role="status" className="loading">Loading qualified candidates…</p>}
            {!qualifiedLoading && qualified && (
              <>
                {qualified.items.length === 0 && (
                  <p className="empty-state">No qualified candidates match the current filter.</p>
                )}
                {qualified.items.length > 0 && (
                  <div className="table-wrap data-table-wrap">
                    <table>
                    <thead>
                      <tr>
                        <th>Select</th>
                        {[
                          ['rank', 'Rank'],
                          ['pass', 'Pass'],
                          ['mean_r', 'Mean R'],
                          ['custom_fitness', 'Custom Result / Fitness'],
                          ['weighted_r', 'Weighted R'],
                          ['profit_factor', 'Profit Factor'],
                          ['recovery_factor', 'Recovery Factor'],
                          ['trades', 'Trades'],
                          ['required_trades', 'Required Trades'],
                          ['round', 'Round'],
                        ].map(([field, label]) => (
                          <SortHeader
                            key={field}
                            label={label}
                            field={field}
                            sort={candidateSort}
                            order={candidateOrder}
                            onSort={changeCandidateSort}
                          />
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {qualified.items.map((row) => (
                        <tr key={candidateKey(row)}>
                          <td>
                            <input
                              aria-label={'Select candidate R' + row.round + ' P' + row.pass}
                              type="checkbox"
                              checked={candidateSelection.has(candidateKey(row))}
                              disabled={busy}
                              onChange={() => toggleCandidate(row)}
                            />
                          </td>
                          <td>{row.rank}</td>
                          <td>{row.pass}</td>
                          <td>{compactNumber(row.mean_r, 4)}</td>
                          <td>{row.custom_fitness === null ? '—' : compactNumber(row.custom_fitness, 4)}</td>
                          <td>{compactNumber(row.weighted_r, 4)}</td>
                          <td>{compactNumber(row.profit_factor, 4)}</td>
                          <td>{compactNumber(row.recovery_factor, 4)}</td>
                          <td>{row.trades}</td>
                          <td>{row.required_trades}</td>
                          <td>{row.round}</td>
                        </tr>
                      ))}
                    </tbody>
                    </table>
                  </div>
                )}
                <Pagination
                  page={qualified.page}
                  pages={qualified.pages}
                  pageSize={qualified.page_size}
                  total={qualified.total}
                  onPage={setCandidatePage}
                  onPageSize={(size) => { setCandidatePageSize(size); setCandidatePage(1) }}
                />
              </>
            )}
          </div>
          ) : (
            <div className="results-control">
              <h2>Historical optimizer pass evidence</h2>
              <p className="subtle">Legacy jobs retain their original winner-era semantics for audit only. No new automatic Challenger action is exposed.</p>
              {job.rounds.map((round) => round.passes.length > 0 && (
                <div key={'legacy-passes-' + round.round_no}>
                  <h3>Round {round.round_no}</h3>
                  <div className="table-wrap data-table-wrap">
                    <table>
                      <thead>
                        <tr><th>Pass</th><th>PF</th><th>RF</th><th>Mean R</th><th>Weighted R</th><th>Trades</th><th>Required Trades</th><th>Eligibility</th></tr>
                      </thead>
                      <tbody>
                        {round.passes.slice(0, 200).map((row) => (
                          <tr key={row.pass_no}>
                            <td>{row.pass_no}</td>
                            <td>{row.profit_factor}</td>
                            <td>{row.recovery_factor}</td>
                            <td>{row.expectancy_r}</td>
                            <td>{row.weighted_r ?? 'MISSING'}</td>
                            <td>{row.trades}</td>
                            <td>{row.minimum_trades_required}</td>
                            <td>{job.winner?.round === round.round_no && job.winner.mt5_pass === row.pass_no ? 'ELIGIBLE WINNER' : row.eligibility}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          )}

        </section>
      )}
    </div>
  )
}
