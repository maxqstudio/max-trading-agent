import { type FormEvent, useEffect, useState } from 'react'
import { Pagination, SortHeader } from './DataTable'
import { compactNumber } from './tableFormat'

type KPI = {
  profit_factor?: number
  recovery_factor?: number
  mean_r?: number
  weighted_r?: number
  trades?: number
  required_trades?: number
}

type ChallengerListItem = {
  challenger_id: string
  status: string
  role_origin: string
  created_utc: string
  updated_utc?: string
  retired_utc?: string | null
  source_job_id: string
  source_round: number
  source_pass: number
  manifest_sha256?: string
  kpi: KPI
  integrity: string
}

type ChallengerDetail = ChallengerListItem & {
  ea_version: string
  baseline_ea_sha256: string
  challenger_ea_sha256: string
  set_sha256: string
  metadata_sha256: string
  manifest_sha256: string
  bundle_path: string
  hard_gates: Record<string, number>
  source_request: {
    symbol?: string
    relative_symbol?: string
    period?: string
    from_date?: string
    to_date?: string
    model?: number
    deposit?: number
    leverage?: number
  }
  artifact_integrity: {
    status: string
    candidate_evidence?: string
    winner_evidence?: string | null
    qualified_candidate_evidence?: string | null
    mt5_xml?: string
    weighted_r_sidecar?: string
    ea_artifact?: string
    set_artifact?: string
    ea_set_parity?: string
    manifest?: string
    reason?: string
  }
  parameter_comparison: Array<{
    parameter: string
    baseline: number
    challenger: number
    different: boolean
  }>
  champion_mutation: string
}

type RegistryPage = {
  view: 'active' | 'retired'
  query: string
  sort: string
  order: 'asc' | 'desc'
  page: number
  page_size: number
  pages: number
  total: number
  items: ChallengerListItem[]
}

type ChampionAuthority = {
  current: null | {
    strategy_id: string
    status: string
    kpi: KPI
    params: Record<string, number>
  }
  status: string
  seed_authority?: string
}

type BacktestMetrics = {
  total_net_profit?: number | null
  gross_profit?: number | null
  gross_loss?: number | null
  profit_factor?: number | null
  expected_payoff?: number | null
  recovery_factor?: number | null
  sharpe_ratio?: number | null
  total_trades?: number | null
  profit_trades_count?: number | null
  profit_trades_pct?: number | null
  loss_trades_count?: number | null
  loss_trades_pct?: number | null
  balance_drawdown_absolute?: number | null
  balance_drawdown_maximal_amount?: number | null
  balance_drawdown_maximal_pct?: number | null
  balance_drawdown_relative_amount?: number | null
  balance_drawdown_relative_pct?: number | null
  equity_drawdown_maximal_amount?: number | null
  equity_drawdown_maximal_pct?: number | null
  equity_drawdown_relative_amount?: number | null
  equity_drawdown_relative_pct?: number | null
}

type BacktestRecord = {
  backtest_id: string
  challenger_id: string
  state: string
  runtime_status?: string
  created_utc: string
  started_utc?: string | null
  completed_utc?: string | null
  source_manifest_sha256: string
  ex5_sha256?: string | null
  report_sha256?: string | null
  evidence_path: string
  error?: string | null
  request: {
    symbol?: string
    relative_symbol?: string
    period?: string
    from_date?: string
    to_date?: string
    strategy_geometry?: Record<string, unknown>
  }
  result?: {
    execution_truth?: string
    report_size?: number
    parameter_mutation?: string
    live_authority?: string
    metrics?: BacktestMetrics
    metrics_schema?: string
    report_source?: string
  } | null
  runtime_inventory?: {
    runtime_status: string
    size_bytes: number
    items: Array<{ type: string; path: string; exists: boolean; size_bytes: number }>
  }
  source_challenger?: {
    challenger_id: string
    role_origin: string
    source_job_id: string
    source_round: number
    source_pass: number
    manifest_sha256: string
    params: Record<string, number>
    optimizer_source_kpi: KPI
  }
}

type BacktestPage = {
  page: number
  page_size: number
  pages: number
  total: number
  items: BacktestRecord[]
}

type BacktestBulkPreflight = {
  action: 'clean' | 'delete'
  selected: number
  deletable: number
  cleanable: number
  blocked: number
  project_bytes: number
  runtime_bytes: number
  items: Array<{
    backtest_id: string
    state: string
    blockers: string[]
    project_bytes: number
    runtime_bytes: number
  }>
}

type ChallengerDeletePreflight = {
  challenger_id: string
  deletable: boolean
  blockers: string[]
  bundle_path: string
  size_bytes: number
}

type PendingBacktestDelete = {
  backtest_id: string
  challenger_id: string
}

type View = 'active' | 'retired'
type DialogMode = 'promotion' | 'retirement' | 'delete-challenger' | 'delete-backtest' | 'bulk-backtest' | null

function n(value: number | string | null | undefined) {
  return value === undefined || value === null ? '—' : String(value)
}

function maxDrawdownPct(metrics?: BacktestMetrics) {
  const values = [
    metrics?.balance_drawdown_maximal_pct,
    metrics?.balance_drawdown_relative_pct,
    metrics?.equity_drawdown_maximal_pct,
    metrics?.equity_drawdown_relative_pct,
  ].filter((value): value is number => value !== null && value !== undefined && Number.isFinite(value))
  return values.length ? Math.max(...values) : null
}

function ownerOperationalStatus(value?: string) {
  const labels: Record<string, string> = {
    CHALLENGER: 'Active Challenger',
    RETIRED: 'Retired',
    PROMOTED: 'Promoted',
    VERIFIED: 'Verified',
    PASS: 'Passed',
    READY: 'Ready',
    PREPARED: 'Prepared',
    RUNNING: 'Running',
    COMPLETED: 'Completed',
    FAILED: 'Failed',
    UNCONFIRMED: 'Verification pending',
    CLEANED: 'Runtime cleaned',
    PRESENT: 'Runtime retained',
    MISSING: 'Unavailable',
    COMMITTED: 'Completed',
    ROLLED_BACK: 'Rolled back',
  }
  if (!value) return 'Not available'
  return labels[value] ?? 'Review required'
}

function IntegrityValue({ value }: { value?: string }) {
  return <strong>{ownerOperationalStatus(value)}</strong>
}

function blockerSummary(blockers?: string[]) {
  return blockers?.length ? 'Protected by current authority or retained dependencies' : 'No blocking dependency'
}

function metricLabel(name: string) {
  return name
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (value) => value.toUpperCase())
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

function ownerErrorMessage(value: unknown, fallback: string) {
  const detail = typeof value === 'string' ? value.trim() : ''
  if (!detail) return fallback
  if (/^[A-Z0-9_:.-]+$/.test(detail) || /[A-Z0-9]+_[A-Z0-9_]+/.test(detail)) {
    return fallback + ' Technical evidence is available in Artifacts.'
  }
  return detail
}

async function jsonOrError(response: Response) {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(ownerErrorMessage(body?.detail, 'The Challenger operation could not be completed.'))
  }
  return body
}

async function fetchRegistry(
  view: View,
  query: string,
  sort: string,
  order: 'asc' | 'desc',
  page: number,
  pageSize: number,
) {
  const params = new URLSearchParams({
    view,
    q: query,
    sort,
    order,
    page: String(page),
    page_size: String(pageSize),
  })
  const response = await fetch('/api/challengers/registry?' + params.toString())
  return await jsonOrError(response) as RegistryPage
}

async function fetchChampion() {
  const response = await fetch('/api/champion')
  return await jsonOrError(response) as ChampionAuthority
}

async function fetchDeletePreflight(challengerId: string) {
  try {
    const response = await fetch(
      '/api/challengers/' + encodeURIComponent(challengerId) + '/delete-preflight',
    )
    return await jsonOrError(response) as ChallengerDeletePreflight
  } catch {
    return {
      challenger_id: challengerId,
      deletable: false,
      blockers: ['DELETE_PREFLIGHT_UNAVAILABLE'],
      bundle_path: '',
      size_bytes: 0,
    } as ChallengerDeletePreflight
  }
}

async function fetchDetail(challengerId: string) {
  const response = await fetch('/api/challengers/' + encodeURIComponent(challengerId))
  return await jsonOrError(response) as ChallengerDetail
}

async function fetchBacktests(
  challengerId: string,
  query: string,
  state: string,
  sort: string,
  order: 'asc' | 'desc',
  page: number,
  pageSize: number,
) {
  const params = new URLSearchParams({
    challenger_id: challengerId,
    q: query,
    state,
    sort,
    order,
    page: String(page),
    page_size: String(pageSize),
  })
  try {
    const response = await fetch('/api/challengers/backtests?' + params.toString())
    return await jsonOrError(response) as BacktestPage
  } catch {
    const legacy = await fetch(
      '/api/challengers/' + encodeURIComponent(challengerId) + '/backtests',
    )
    const items = await jsonOrError(legacy) as BacktestRecord[]
    return {
      page: 1,
      page_size: Math.max(25, items.length),
      pages: 1,
      total: items.length,
      items,
    } as BacktestPage
  }
}

export default function ChallengersPage() {
  const [view, setView] = useState<View>('active')
  const [queryDraft, setQueryDraft] = useState('')
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('created')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [registry, setRegistry] = useState<RegistryPage | null>(null)
  const [detail, setDetail] = useState<ChallengerDetail | null>(null)
  const [backtests, setBacktests] = useState<BacktestPage | null>(null)
  const [backtestQuery, setBacktestQuery] = useState('')
  const [backtestState, setBacktestState] = useState('')
  const [backtestSort, setBacktestSort] = useState('created')
  const [backtestOrder, setBacktestOrder] = useState<'asc' | 'desc'>('desc')
  const [backtestPage, setBacktestPage] = useState(1)
  const [backtestPageSize, setBacktestPageSize] = useState(25)
  const [backtestDetail, setBacktestDetail] = useState<BacktestRecord | null>(null)
  const [deletePreflight, setDeletePreflight] = useState<ChallengerDeletePreflight | null>(null)
  const [pendingBacktestDelete, setPendingBacktestDelete] = useState<PendingBacktestDelete | null>(null)
  const [backtestSelection, setBacktestSelection] = useState<Set<string>>(new Set())
  const [backtestBulkPreflight, setBacktestBulkPreflight] = useState<BacktestBulkPreflight | null>(null)
  const [backtestBulkAction, setBacktestBulkAction] = useState<'clean' | 'delete' | null>(null)
  const [champion, setChampion] = useState<ChampionAuthority | null>(null)
  const [dialogMode, setDialogMode] = useState<DialogMode>(null)
  const [busy, setBusy] = useState('')
  const [operationResult, setOperationResult] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [backtestForm, setBacktestForm] = useState({
    symbol: '',
    relative_symbol: '',
    period: '',
    from_date: '',
    to_date: '',
  })

  const applyDetail = (next: ChallengerDetail | null) => {
    setDetail(next)
    if (next) {
      setBacktestForm({
        symbol: next.source_request.symbol ?? '',
        relative_symbol: next.source_request.relative_symbol ?? '',
        period: next.source_request.period ?? '',
        from_date: next.source_request.from_date ?? '',
        to_date: next.source_request.to_date ?? '',
      })
    }
  }

  const loadDetail = async (challengerId: string) => {
    setError('')
    try {
      const [nextDetail, preflight] = await Promise.all([
        fetchDetail(challengerId),
        fetchDeletePreflight(challengerId),
      ])
      applyDetail(nextDetail)
      setDeletePreflight(preflight)
      setBacktestSelection(new Set())
      setBacktestBulkPreflight(null)
      setBacktestBulkAction(null)
      setBacktestPage(1)
    } catch (reason) {
      setError((reason as Error).message)
    }
  }

  const refreshBacktests = async (challengerId: string) => {
    const next = await fetchBacktests(
      challengerId,
      backtestQuery,
      backtestState,
      backtestSort,
      backtestOrder,
      backtestPage,
      backtestPageSize,
    )
    setBacktests(next)
    if (next.page !== backtestPage) setBacktestPage(next.page)
  }

  const refresh = async (preferredId?: string) => {
    const [nextRegistry, nextChampion] = await Promise.all([
      fetchRegistry(view, query, sort, order, page, pageSize),
      fetchChampion(),
    ])
    setRegistry(nextRegistry)
    setChampion(nextChampion)
    const selectedId = preferredId && nextRegistry.items.some(
      (item) => item.challenger_id === preferredId,
    )
      ? preferredId
      : nextRegistry.items[0]?.challenger_id
    if (!selectedId) {
      applyDetail(null)
      setDeletePreflight(null)
      setBacktests(null)
      return
    }
    await loadDetail(selectedId)
  }

  useEffect(() => {
    let active = true
    Promise.all([
      fetchRegistry(view, query, sort, order, page, pageSize),
      fetchChampion(),
    ])
      .then(async ([nextRegistry, nextChampion]) => {
        if (!active) return
        setRegistry(nextRegistry)
        setChampion(nextChampion)
        if (nextRegistry.items.length === 0) {
          applyDetail(null)
          setDeletePreflight(null)
          setBacktests(null)
          return
        }
        const selectedId = nextRegistry.items[0].challenger_id
        const [nextDetail, preflight] = await Promise.all([
          fetchDetail(selectedId),
          fetchDeletePreflight(selectedId),
        ])
        if (!active) return
        applyDetail(nextDetail)
        setDeletePreflight(preflight)
        setBacktestPage(1)
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [view, query, sort, order, page, pageSize])

  useEffect(() => {
    if (!detail?.challenger_id) {
      return
    }
    let active = true
    fetchBacktests(
      detail.challenger_id,
      backtestQuery,
      backtestState,
      backtestSort,
      backtestOrder,
      backtestPage,
      backtestPageSize,
    )
      .then((next) => {
        if (!active) return
        setBacktests(next)
        if (next.page !== backtestPage) setBacktestPage(next.page)
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message)
      })
    return () => { active = false }
  }, [
    detail?.challenger_id,
    backtestQuery,
    backtestState,
    backtestSort,
    backtestOrder,
    backtestPage,
    backtestPageSize,
  ])

  const searchRegistry = (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError('')
    setPage(1)
    setQuery(queryDraft.trim())
  }

  const switchView = (next: View) => {
    setDialogMode(null)
    setOperationResult('')
    setError('')
    setLoading(true)
    setPage(1)
    setView(next)
  }

  const confirmPromotion = async () => {
    if (!detail || busy !== '') return
    setBusy('promotion')
    setError('')
    try {
      const response = await fetch(
        '/api/challengers/' + encodeURIComponent(detail.challenger_id) + '/promote',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_challenger_manifest_sha256: detail.manifest_sha256,
            expected_current_champion_id: champion?.current?.strategy_id ?? null,
            confirmed: true,
          }),
        },
      )
      await jsonOrError(response)
      setOperationResult('Promotion completed.')
      setDialogMode(null)
      await refresh(detail.challenger_id)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  const confirmRetirement = async () => {
    if (!detail || busy !== '') return
    setBusy('retirement')
    setError('')
    try {
      const response = await fetch(
        '/api/challengers/' + encodeURIComponent(detail.challenger_id) + '/retire',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_challenger_manifest_sha256: detail.manifest_sha256,
            confirmed: true,
          }),
        },
      )
      const result = await jsonOrError(response)
      setOperationResult(
        result.backtest_history_preserved
          ? 'Challenger retired non-destructively. Retained backtest history was preserved.'
          : 'Challenger retired non-destructively.',
      )
      setDialogMode(null)
      await refresh()
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  const runBacktest = async () => {
    if (!detail || busy !== '') return
    setBusy('backtest')
    setError('')
    try {
      const response = await fetch(
        '/api/challengers/' + encodeURIComponent(detail.challenger_id) + '/backtest',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(backtestForm),
        },
      )
      const result = await jsonOrError(response) as BacktestRecord
      setOperationResult(
        'Backtest ' + ownerOperationalStatus(result.state) + '. MT5 Strategy Tester evidence retained.',
      )
      setBacktestPage(1)
      await refreshBacktests(detail.challenger_id)
      setDeletePreflight(await fetchDeletePreflight(detail.challenger_id))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  function changeRegistrySort(field: string, nextOrder: 'asc' | 'desc') {
    setSort(field)
    setOrder(nextOrder)
    setPage(1)
    setLoading(true)
  }

  function changeBacktestSort(field: string, nextOrder: 'asc' | 'desc') {
    setBacktestSort(field)
    setBacktestOrder(nextOrder)
    setBacktestPage(1)
  }

  async function viewBacktest(backtestId: string) {
    setError('')
    try {
      const response = await fetch(
        '/api/challengers/backtests/' + encodeURIComponent(backtestId),
      )
      setBacktestDetail(await jsonOrError(response) as BacktestRecord)
    } catch (reason) {
      setError((reason as Error).message)
    }
  }

  function openBacktestReport(backtestId: string) {
    window.open(
      '/api/challengers/backtests/' + encodeURIComponent(backtestId) + '/report',
      '_blank',
      'noopener,noreferrer',
    )
  }

  async function cleanBacktestRuntime(record: BacktestRecord) {
    if (busy !== '') return
    const busyKey = 'clean-backtest:' + record.backtest_id
    setBusy(busyKey)
    setError('')
    try {
      const response = await fetch(
        '/api/challengers/backtests/' + encodeURIComponent(record.backtest_id) + '/clean-runtime',
        { method: 'POST' },
      )
      const result = await jsonOrError(response)
      setOperationResult(
        'Runtime cleaned · removed ' + String(result.removed_bytes ?? 0) + ' bytes.',
      )
      await refreshBacktests(record.challenger_id)
      if (detail?.challenger_id === record.challenger_id) {
        setDeletePreflight(await fetchDeletePreflight(record.challenger_id))
      }
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  async function confirmBacktestDelete() {
    if (!pendingBacktestDelete || busy !== '') return
    const pending = pendingBacktestDelete
    const busyKey = 'delete-backtest:' + pending.backtest_id
    setBusy(busyKey)
    setError('')
    try {
      const response = await fetch(
        '/api/challengers/backtests/' + encodeURIComponent(pending.backtest_id),
        {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirmed: true }),
        },
      )
      await jsonOrError(response)
      setOperationResult('Backtest deleted.')
      if (backtestDetail?.backtest_id === pending.backtest_id) {
        setBacktestDetail(null)
      }
      setPendingBacktestDelete(null)
      setDialogMode(null)
      await refreshBacktests(pending.challenger_id)
      if (detail?.challenger_id === pending.challenger_id) {
        setDeletePreflight(await fetchDeletePreflight(pending.challenger_id))
      }
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  function toggleBacktestSelection(backtestId: string) {
    setBacktestSelection((current) => {
      const next = new Set(current)
      if (next.has(backtestId)) next.delete(backtestId)
      else next.add(backtestId)
      return next
    })
  }

  function toggleBacktestPageSelection() {
    const ids = backtests?.items.map((item) => item.backtest_id) ?? []
    if (!ids.length) return
    const allSelected = ids.every((id) => backtestSelection.has(id))
    setBacktestSelection((current) => {
      const next = new Set(current)
      for (const id of ids) {
        if (allSelected) next.delete(id)
        else next.add(id)
      }
      return next
    })
  }

  async function prepareBacktestBulk(action: 'clean' | 'delete') {
    if (!backtestSelection.size || busy !== '') return
    setBusy('bulk-preflight-' + action)
    setError('')
    try {
      const response = await fetch('/api/challengers/backtests/preflight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          backtest_ids: Array.from(backtestSelection).sort(),
          action,
          confirmed: false,
        }),
      })
      const result = await jsonOrError(response) as BacktestBulkPreflight
      setBacktestBulkPreflight(result)
      setBacktestBulkAction(action)
      setDialogMode('bulk-backtest')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  async function confirmBacktestBulk() {
    if (!detail || !backtestBulkAction || !backtestSelection.size || busy !== '') return
    setBusy('bulk-' + backtestBulkAction)
    setError('')
    try {
      const response = await fetch('/api/challengers/backtests/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          backtest_ids: Array.from(backtestSelection).sort(),
          action: backtestBulkAction,
          confirmed: true,
        }),
      })
      const result = await jsonOrError(response)
      setOperationResult(
        (backtestBulkAction === 'clean' ? 'Runtime cleaned for ' : 'Deleted ')
        + String(result.results?.length ?? backtestSelection.size)
        + ' selected Backtest(s).',
      )
      setBacktestSelection(new Set())
      setBacktestBulkPreflight(null)
      setBacktestBulkAction(null)
      setDialogMode(null)
      await refreshBacktests(detail.challenger_id)
      setDeletePreflight(await fetchDeletePreflight(detail.challenger_id))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  async function confirmChallengerDelete() {
    if (!detail || busy !== '') return
    setBusy('delete-challenger')
    setError('')
    try {
      const response = await fetch(
        '/api/challengers/' + encodeURIComponent(detail.challenger_id),
        {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirmed: true }),
        },
      )
      await jsonOrError(response)
      setOperationResult('Challenger deleted.')
      setDialogMode(null)
      await refresh()
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  const isActive = detail?.status === 'CHALLENGER'
  const detailVerified = detail?.artifact_integrity.status === 'VERIFIED'

  return (
    <>
      <header className="page-head">
        <div>
          <p className="eyebrow">MAX · Strategy lifecycle</p>
          <h1>Challengers</h1>
        </div>
      </header>

      {error && <p role="alert" className="error">Challenger authority unavailable: {error}</p>}
      {operationResult && <p role="status" className="notice">{operationResult}</p>}

      <section aria-labelledby="challenger-registry">
        <div className="section-head">
          <div>
            <h2 id="challenger-registry">Strategy Challenger registry</h2>
            <p className="subtle">
              Active and Retired / Archive are status views of the same preserved Strategy registry.
            </p>
          </div>
          <div className="view-tabs" aria-label="Challenger registry view">
            <button
              type="button"
              aria-pressed={view === 'active'}
              onClick={() => switchView('active')}
            >
              Active
            </button>
            <button
              type="button"
              aria-pressed={view === 'retired'}
              onClick={() => switchView('retired')}
            >
              Retired / Archive
            </button>
          </div>
        </div>

        <form className="form-grid" onSubmit={searchRegistry}>
          <label>
            Search
            <input
              value={queryDraft}
              onChange={(event) => setQueryDraft(event.target.value)}
              placeholder="Search Challenger evidence"
            />
          </label>
          <label>
            Sort
            <select value={sort} onChange={(event) => {
              setLoading(true)
              setPage(1)
              setSort(event.target.value)
            }}>
              <option value="created">Created</option>
              <option value="retired">Retired</option>
              <option value="id">Candidate</option>
              <option value="profit_factor">Profit Factor</option>
              <option value="recovery_factor">Recovery Factor</option>
              <option value="mean_r">Mean R</option>
              <option value="weighted_r">Weighted R</option>
              <option value="trades">Trades</option>
            </select>
          </label>
          <label>
            Order
            <select
              value={order}
              onChange={(event) => {
                setLoading(true)
                setPage(1)
                setOrder(event.target.value as 'asc' | 'desc')
              }}
            >
              <option value="desc">Descending</option>
              <option value="asc">Ascending</option>
            </select>
          </label>
          <div className="actions form-action">
            <button type="submit">Search</button>
            <button
              type="button"
              onClick={() => {
                setLoading(true)
                setQueryDraft('')
                setQuery('')
                setPage(1)
              }}
            >
              Clear
            </button>
          </div>
        </form>

        {loading && <p role="status" className="loading">Loading Challenger registry…</p>}

        {!loading && registry && (
          <>
            <p className="subtle">
              {registry.total} {view === 'active' ? 'active' : 'retired'} Strategies
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <SortHeader label="Challenger" field="id" sort={sort} order={order} onSort={changeRegistrySort} />
                    <th>Status</th>
                    <SortHeader label="Created" field="created" sort={sort} order={order} onSort={changeRegistrySort} />
                    {view === 'retired' && (
                      <SortHeader label="Retired" field="retired" sort={sort} order={order} onSort={changeRegistrySort} />
                    )}
                    <SortHeader label="Source" field="source_job" sort={sort} order={order} onSort={changeRegistrySort} />
                    <th>Round</th><th>Pass</th>
                    <SortHeader label="PF" field="profit_factor" sort={sort} order={order} onSort={changeRegistrySort} />
                    <SortHeader label="RF" field="recovery_factor" sort={sort} order={order} onSort={changeRegistrySort} />
                    <SortHeader label="Mean R" field="mean_r" sort={sort} order={order} onSort={changeRegistrySort} />
                    <SortHeader label="Weighted R" field="weighted_r" sort={sort} order={order} onSort={changeRegistrySort} />
                    <SortHeader label="Trades" field="trades" sort={sort} order={order} onSort={changeRegistrySort} />
                    <th>Integrity</th>
                  </tr>
                </thead>
                <tbody>
                  {registry.items.length === 0 && (
                    <tr>
                      <td colSpan={view === 'retired' ? 13 : 12}>
                        {view === 'active'
                          ? 'No active Strategy Challenger.'
                          : 'No retired Strategy Challenger.'}
                      </td>
                    </tr>
                  )}
                  {registry.items.map((item) => (
                    <tr key={item.challenger_id}>
                      <td>
                        <button
                          className="text-button mono"
                          onClick={() => loadDetail(item.challenger_id)}
                        >
                          Round {item.source_round} · Pass {item.source_pass}
                        </button>
                      </td>
                      <td><strong>{ownerOperationalStatus(item.status)}</strong></td>
                      <td>{item.created_utc}</td>
                      {view === 'retired' && <td>{item.retired_utc ?? '—'}</td>}
                      <td>Optimizer evidence</td>
                      <td>{item.source_round}</td>
                      <td>{item.source_pass}</td>
                      <td>{n(item.kpi.profit_factor)}</td>
                      <td>{n(item.kpi.recovery_factor)}</td>
                      <td>{n(item.kpi.mean_r)}</td>
                      <td>{n(item.kpi.weighted_r)}</td>
                      <td>{n(item.kpi.trades)}</td>
                      <td><IntegrityValue value={item.integrity} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              page={registry.page}
              pages={registry.pages}
              pageSize={registry.page_size}
              total={registry.total}
              onPage={(next) => { setLoading(true); setPage(next) }}
              onPageSize={(size) => { setPageSize(size); setPage(1); setLoading(true) }}
            />
          </>
        )}
      </section>

      {detail && (
        <>
          <section aria-labelledby="challenger-identity">
            <div className="section-head">
              <h2 id="challenger-identity">Candidate identity</h2>
              {isActive && (
                <div className="actions">
                  <button
                    type="button"
                    onClick={() => setDialogMode('promotion')}
                    disabled={!detailVerified || busy !== ''}
                  >
                    Promote to Champion
                  </button>
                  <button
                    type="button"
                    onClick={() => setDialogMode('retirement')}
                    disabled={!detailVerified || busy !== ''}
                  >
                    Retire / Archive
                  </button>
                  <button
                    type="button"
                    onClick={() => setDialogMode('delete-challenger')}
                    disabled={!deletePreflight?.deletable || busy !== ''}
                    title={deletePreflight?.blockers.length ? 'Deletion is protected by current authority' : 'Delete generated Challenger'}
                  >
                    Delete Challenger
                  </button>
                </div>
              )}
            </div>
            {isActive && deletePreflight && deletePreflight.blockers.length > 0 && (
              <p className="error-text">Deletion is not allowed while this Challenger is protected by current authority or retained dependencies.</p>
            )}
            <dl className="facts compact">
              <div><dt>Candidate</dt><dd>Optimizer round {detail.source_round} · pass {detail.source_pass}</dd></div>
              <div><dt>Status</dt><dd><strong>{ownerOperationalStatus(detail.status)}</strong></dd></div>
              <div><dt>Created</dt><dd>{detail.created_utc}</dd></div>
              {detail.retired_utc && <div><dt>Retired</dt><dd>{detail.retired_utc}</dd></div>}
              <div><dt>Source</dt><dd>Retained qualified Optimizer evidence</dd></div>
              <div><dt>EA version</dt><dd>{detail.ea_version}</dd></div>
            </dl>
          </section>

          <section aria-labelledby="challenger-kpi">
            <h2 id="challenger-kpi">Candidate evidence</h2>
            <dl className="facts compact">
              <div><dt>Profit Factor</dt><dd>{n(detail.kpi.profit_factor)}</dd></div>
              <div><dt>Recovery Factor</dt><dd>{n(detail.kpi.recovery_factor)}</dd></div>
              <div><dt>Mean R</dt><dd>{n(detail.kpi.mean_r)}</dd></div>
              <div><dt>Weighted R</dt><dd>{n(detail.kpi.weighted_r)}</dd></div>
              <div><dt>Trades</dt><dd>{n(detail.kpi.trades)} / required {n(detail.kpi.required_trades)}</dd></div>
              <div><dt>Market</dt><dd>{detail.source_request.symbol ?? '—'} · {detail.source_request.period ?? '—'} · {detail.source_request.from_date ?? '—'} → {detail.source_request.to_date ?? '—'}</dd></div>
              <div><dt>Relative reference</dt><dd>{detail.source_request.relative_symbol ?? '—'}</dd></div>
            </dl>
          </section>

          <section aria-labelledby="challenger-integrity">
            <h2 id="challenger-integrity">Evidence integrity</h2>
            <dl className="facts compact">
              <div><dt>Overall</dt><dd><IntegrityValue value={detail.artifact_integrity.status} /></dd></div>
              <div><dt>Candidate evidence</dt><dd><IntegrityValue value={detail.artifact_integrity.candidate_evidence} /></dd></div>
              {detail.artifact_integrity.winner_evidence && <div><dt>Historical winner evidence</dt><dd><IntegrityValue value={detail.artifact_integrity.winner_evidence} /></dd></div>}
              {detail.artifact_integrity.qualified_candidate_evidence && <div><dt>Qualified candidate evidence</dt><dd><IntegrityValue value={detail.artifact_integrity.qualified_candidate_evidence} /></dd></div>}
              <div><dt>MT5 report evidence</dt><dd><IntegrityValue value={detail.artifact_integrity.mt5_xml} /></dd></div>
              <div><dt>Risk / result evidence</dt><dd><IntegrityValue value={detail.artifact_integrity.weighted_r_sidecar} /></dd></div>
              <div><dt>EA package</dt><dd><IntegrityValue value={detail.artifact_integrity.ea_artifact} /></dd></div>
              <div><dt>Parameter package</dt><dd><IntegrityValue value={detail.artifact_integrity.set_artifact} /></dd></div>
              <div><dt>EA / parameter parity</dt><dd><IntegrityValue value={detail.artifact_integrity.ea_set_parity} /></dd></div>
              <div><dt>Evidence index</dt><dd><IntegrityValue value={detail.artifact_integrity.manifest} /></dd></div>
              {detail.artifact_integrity.reason && <div><dt>Reason</dt><dd className="error-text">Integrity evidence requires review. Technical detail is available in Artifacts.</dd></div>}
            </dl>
          </section>

          {isActive && (
            <section aria-labelledby="challenger-backtest">
              <div className="section-head">
                <div>
                  <h2 id="challenger-backtest">Challenger Backtest</h2>
                  <p className="subtle">
                    MT5 Strategy Tester only. Strategy parameters come from retained Challenger evidence; this grants no Live authority.
                  </p>
                </div>
                <div className="actions">
                  <button
                    type="button"
                    onClick={runBacktest}
                    disabled={!detailVerified || busy !== ''}
                  >
                    <ActionProgress
                      active={busy === 'backtest'}
                      idle="Run Backtest"
                      pending="Running..."
                    />
                  </button>
                </div>
              </div>
              <div className="form-grid">
                <label>Main Symbol
                  <input
                    value={backtestForm.symbol}
                    onChange={(event) => setBacktestForm({ ...backtestForm, symbol: event.target.value })}
                  />
                </label>
                <label>Relative Symbol
                  <input
                    value={backtestForm.relative_symbol}
                    onChange={(event) => setBacktestForm({ ...backtestForm, relative_symbol: event.target.value })}
                  />
                </label>
                <label>Timeframe
                  <input
                    value={backtestForm.period}
                    onChange={(event) => setBacktestForm({ ...backtestForm, period: event.target.value.toUpperCase() })}
                  />
                </label>
                <label>From
                  <input
                    value={backtestForm.from_date}
                    onChange={(event) => setBacktestForm({ ...backtestForm, from_date: event.target.value })}
                    placeholder="YYYY.MM.DD"
                  />
                </label>
                <label>To
                  <input
                    value={backtestForm.to_date}
                    onChange={(event) => setBacktestForm({ ...backtestForm, to_date: event.target.value })}
                    placeholder="YYYY.MM.DD"
                  />
                </label>
              </div>
            </section>
          )}

          <section aria-labelledby="backtest-history">
            <h2 id="backtest-history">Backtest history</h2>
            <div className="table-toolbar">
              <input
                aria-label="Search Backtests"
                placeholder="Backtest ID, Challenger or symbol"
                value={backtestQuery}
                onChange={(event) => {
                  setBacktestQuery(event.target.value)
                  setBacktestPage(1)
                }}
              />
              <select
                aria-label="Backtest state filter"
                value={backtestState}
                onChange={(event) => {
                  setBacktestState(event.target.value)
                  setBacktestPage(1)
                }}
              >
                <option value="">All states</option>
                <option value="COMPLETED">Completed</option>
                <option value="FAILED">Failed</option>
                <option value="UNCONFIRMED">Unconfirmed</option>
                <option value="PREPARED">Prepared</option>
                <option value="RUNNING">Running</option>
              </select>
              <button
                type="button"
                onClick={toggleBacktestPageSelection}
                disabled={!backtests?.items.length || busy !== ''}
              >
                Select current page
              </button>
              <button
                type="button"
                onClick={() => setBacktestSelection(new Set())}
                disabled={!backtestSelection.size || busy !== ''}
              >
                Clear selection
              </button>
              <span>{backtestSelection.size} selected</span>
              <button
                type="button"
                onClick={() => prepareBacktestBulk('clean')}
                disabled={!backtestSelection.size || busy !== ''}
              >
                <ActionProgress
                  active={busy === 'bulk-preflight-clean'}
                  idle="Clean Selected Runtime"
                  pending="Cleaning..."
                />
              </button>
              <button
                type="button"
                onClick={() => prepareBacktestBulk('delete')}
                disabled={!backtestSelection.size || busy !== ''}
              >
                <ActionProgress
                  active={busy === 'bulk-preflight-delete'}
                  idle="Delete Selected"
                  pending="Deleting..."
                />
              </button>
            </div>
            {backtests && (
              <>
                {backtests.items.length === 0 && (
                  <p className="empty-state">No retained Challenger backtest history.</p>
                )}
                {backtests.items.length > 0 && (
                  <div className="table-wrap data-table-wrap">
                    <table>
                    <thead>
                      <tr>
                        <th>Select</th>
                        <SortHeader label="Backtest" field="id" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} />
                        <SortHeader label="State" field="state" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} />
                        <th>Market</th>
                        <th>TF</th>
                        <th>Period</th>
                        <SortHeader label="Net Profit" field="net_profit" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} />
                        <SortHeader label="PF" field="profit_factor" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} />
                        <SortHeader label="RF" field="recovery_factor" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} />
                        <SortHeader label="Sharpe" field="sharpe" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} />
                        <SortHeader label="Trades" field="trades" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} />
                        <th>Win %</th>
                        <th>Max DD %</th>
                        <SortHeader label="Created" field="created" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} />
                        <th>Runtime Status</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {backtests.items.map((record) => {
                        const metrics = record.result?.metrics
                        return (
                          <tr key={record.backtest_id}>
                            <td>
                              <input
                                aria-label={'Select retained Backtest created ' + record.created_utc}
                                type="checkbox"
                                checked={backtestSelection.has(record.backtest_id)}
                                disabled={busy !== ''}
                                onChange={() => toggleBacktestSelection(record.backtest_id)}
                              />
                            </td>
                            <td>Retained test</td>
                            <td><strong>{ownerOperationalStatus(record.state)}</strong>{record.error ? ' · Review required' : ''}</td>
                            <td>{record.request.symbol ?? '—'}</td>
                            <td>{record.request.period ?? '—'}</td>
                            <td>{record.request.from_date ?? '—'} → {record.request.to_date ?? '—'}</td>
                            <td>{compactNumber(metrics?.total_net_profit)}</td>
                            <td>{compactNumber(metrics?.profit_factor)}</td>
                            <td>{compactNumber(metrics?.recovery_factor)}</td>
                            <td>{compactNumber(metrics?.sharpe_ratio)}</td>
                            <td>{metrics?.total_trades ?? '—'}</td>
                            <td>{metrics?.profit_trades_pct === null || metrics?.profit_trades_pct === undefined ? '—' : compactNumber(metrics.profit_trades_pct, 2) + '%'}</td>
                            <td>{maxDrawdownPct(metrics) === null ? '—' : compactNumber(maxDrawdownPct(metrics), 2) + '%'}</td>
                            <td>{record.created_utc}</td>
                            <td>{ownerOperationalStatus(record.runtime_status)}</td>
                            <td>
                              <div className="row-actions">
                                <button type="button" onClick={() => viewBacktest(record.backtest_id)}>View Details</button>
                                <button type="button" disabled={!record.report_sha256} onClick={() => openBacktestReport(record.backtest_id)}>Open Report</button>
                                <button
                                  type="button"
                                  disabled={busy !== '' || ['PREPARED', 'RUNNING'].includes(record.state)}
                                  onClick={() => cleanBacktestRuntime(record)}
                                >
                                  <ActionProgress
                                    active={busy === 'clean-backtest:' + record.backtest_id}
                                    idle="Clean Runtime"
                                    pending="Cleaning..."
                                  />
                                </button>
                                <button
                                  type="button"
                                  disabled={busy !== '' || ['PREPARED', 'RUNNING'].includes(record.state)}
                                  onClick={() => {
                                    setPendingBacktestDelete({
                                      backtest_id: record.backtest_id,
                                      challenger_id: record.challenger_id,
                                    })
                                    setDialogMode('delete-backtest')
                                  }}
                                >
                                  Delete Backtest
                                </button>
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                    </table>
                  </div>
                )}
                <Pagination
                  page={backtests.page}
                  pages={backtests.pages}
                  pageSize={backtests.page_size}
                  total={backtests.total}
                  onPage={setBacktestPage}
                  onPageSize={(size) => { setBacktestPageSize(size); setBacktestPage(1) }}
                />
              </>
            )}
          </section>

          <section aria-labelledby="parameter-comparison">
            <h2 id="parameter-comparison">Parameter comparison</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Parameter</th><th>Baseline</th><th>Challenger</th><th>Difference</th></tr>
                </thead>
                <tbody>
                  {detail.parameter_comparison.map((row) => (
                    <tr key={row.parameter}>
                      <td className="mono">{row.parameter}</td>
                      <td>{n(row.baseline)}</td>
                      <td>{n(row.challenger)}</td>
                      <td>{row.different ? 'Changed' : 'Unchanged'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {backtestDetail && (
            <div className="modal-backdrop" role="presentation">
              <div className="modal backtest-detail-modal" role="dialog" aria-modal="true" aria-label="Backtest details">
                <h2>Backtest details</h2>
                <dl className="facts compact">
                  <div><dt>State</dt><dd>{ownerOperationalStatus(backtestDetail.state)}</dd></div>
                  <div><dt>Runtime</dt><dd>{ownerOperationalStatus(backtestDetail.runtime_inventory?.runtime_status ?? backtestDetail.runtime_status)}</dd></div>
                  <div><dt>Market</dt><dd>{backtestDetail.request.symbol ?? '—'} · {backtestDetail.request.period ?? '—'}</dd></div>
                  <div><dt>Window</dt><dd>{backtestDetail.request.from_date ?? '—'} → {backtestDetail.request.to_date ?? '—'}</dd></div>
                  <div><dt>Relative symbol</dt><dd>{backtestDetail.request.relative_symbol ?? '—'}</dd></div>
                  <div><dt>Source</dt><dd>{backtestDetail.source_challenger ? 'Strategy Challenger · optimizer round ' + backtestDetail.source_challenger.source_round + ' · pass ' + backtestDetail.source_challenger.source_pass : 'Retained Strategy Challenger'}</dd></div>
                  <div><dt>Evidence</dt><dd>{backtestDetail.report_sha256 ? 'MT5 report retained' : 'Report unavailable'}</dd></div>
                  {backtestDetail.error && <div><dt>Error</dt><dd className="error-text">Backtest evidence requires review. Technical detail is available in Artifacts.</dd></div>}
                </dl>
                <h3>MT5 Strategy Tester metrics</h3>
                <dl className="facts compact">
                  {Object.entries(backtestDetail.result?.metrics ?? {}).map(([key, value]) => (
                    <div key={key}><dt>{metricLabel(key)}</dt><dd>{value === null || value === undefined ? '—' : String(value)}</dd></div>
                  ))}
                </dl>
                <p className="subtle">Strategy geometry, parameter lineage and runtime-file details remain available through retained evidence in Artifacts.</p>
                <div className="actions">
                  <button type="button" disabled={!backtestDetail.report_sha256} onClick={() => openBacktestReport(backtestDetail.backtest_id)}>Open Report</button>
                  <button type="button" onClick={() => setBacktestDetail(null)}>Close</button>
                </div>
              </div>
            </div>
          )}

          {dialogMode === 'bulk-backtest' && backtestBulkPreflight && backtestBulkAction && (
            <section role="dialog" aria-modal="true" aria-labelledby="backtest-bulk-confirmation" className="confirmation">
              <h2 id="backtest-bulk-confirmation">
                {backtestBulkAction === 'clean' ? 'Clean Selected Runtime' : 'Delete Selected Backtests'}
              </h2>
              <p>All selected Backtests are preflighted before mutation. Any blocked selection prevents the batch from starting.</p>
              <dl className="facts compact">
                <div><dt>Selected</dt><dd>{backtestBulkPreflight.selected}</dd></div>
                <div><dt>Allowed</dt><dd>{backtestBulkAction === 'clean' ? backtestBulkPreflight.cleanable : backtestBulkPreflight.deletable}</dd></div>
                <div><dt>Blocked</dt><dd>{backtestBulkPreflight.blocked}</dd></div>
                <div><dt>Project bytes</dt><dd>{backtestBulkPreflight.project_bytes}</dd></div>
                <div><dt>Runtime bytes</dt><dd>{backtestBulkPreflight.runtime_bytes}</dd></div>
              </dl>
              {backtestBulkPreflight.items.some((item) => item.blockers.length > 0) && (
                <ul>
                  {backtestBulkPreflight.items.filter((item) => item.blockers.length > 0).map((item) => (
                    <li key={item.backtest_id}>A selected Backtest is protected by current authority or retained dependencies.</li>
                  ))}
                </ul>
              )}
              <div className="actions">
                <button
                  type="button"
                  onClick={confirmBacktestBulk}
                  disabled={backtestBulkPreflight.blocked > 0 || busy !== ''}
                >
                  <ActionProgress
                    active={busy === 'bulk-clean' || busy === 'bulk-delete'}
                    idle="CONFIRM"
                    pending={busy === 'bulk-clean' ? 'Cleaning...' : 'Deleting...'}
                  />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setBacktestBulkPreflight(null)
                    setBacktestBulkAction(null)
                    setDialogMode(null)
                  }}
                  disabled={busy !== ''}
                >
                  Cancel
                </button>
              </div>
            </section>
          )}

          {dialogMode === 'promotion' && (
            <section role="dialog" aria-modal="true" aria-labelledby="promotion-confirmation" className="confirmation">
              <h2 id="promotion-confirmation">Confirm Strategy Champion promotion</h2>
              <p>This is an explicit governance change. No live trading authority is granted.</p>
              <dl className="facts compact">
                <div><dt>Challenger</dt><dd>Selected retained Strategy Challenger</dd></div>
                <div><dt>Source</dt><dd>Optimizer round {detail.source_round} · pass {detail.source_pass}</dd></div>
                <div><dt>PF / RF</dt><dd>{n(detail.kpi.profit_factor)} / {n(detail.kpi.recovery_factor)}</dd></div>
                <div><dt>Mean R / Weighted R</dt><dd>{n(detail.kpi.mean_r)} / {n(detail.kpi.weighted_r)}</dd></div>
                <div><dt>Trades</dt><dd>{n(detail.kpi.trades)} / required {n(detail.kpi.required_trades)}</dd></div>
                <div><dt>Artifact integrity</dt><dd><IntegrityValue value={detail.artifact_integrity.status} /></dd></div>
                <div><dt>Current Champion</dt><dd>{champion?.current ? 'Selected' : 'Not selected'}</dd></div>
                <div><dt>After promotion</dt><dd>Selected Challenger becomes the Strategy Champion</dd></div>
              </dl>
              <div className="actions">
                <button type="button" onClick={confirmPromotion} disabled={busy !== ''}>
                  <ActionProgress
                    active={busy === 'promotion'}
                    idle="CONFIRM PROMOTION"
                    pending="Promoting..."
                  />
                </button>
                <button type="button" onClick={() => setDialogMode(null)} disabled={busy !== ''}>
                  Cancel
                </button>
              </div>
            </section>
          )}

          {dialogMode === 'delete-challenger' && (
            <section role="dialog" aria-modal="true" aria-labelledby="challenger-delete-confirmation" className="confirmation">
              <h2 id="challenger-delete-confirmation">Delete Challenger</h2>
              <p>This permanently deletes the generated Challenger DB row and bundle. Dependencies must be cleared first.</p>
              <dl className="facts compact">
                <div><dt>Challenger</dt><dd>Selected generated Strategy Challenger</dd></div>
                <div><dt>Deletion</dt><dd>{deletePreflight?.deletable ? 'Allowed' : 'Not allowed'}</dd></div>
                <div><dt>Dependency state</dt><dd>{blockerSummary(deletePreflight?.blockers)}</dd></div>
              </dl>
              <div className="actions">
                <button type="button" disabled={!deletePreflight?.deletable || busy !== ''} onClick={confirmChallengerDelete}>
                  <ActionProgress
                    active={busy === 'delete-challenger'}
                    idle="CONFIRM DELETE CHALLENGER"
                    pending="Deleting..."
                  />
                </button>
                <button type="button" onClick={() => setDialogMode(null)} disabled={busy !== ''}>Cancel</button>
              </div>
            </section>
          )}

          {dialogMode === 'delete-backtest' && pendingBacktestDelete && (
            <section role="dialog" aria-modal="true" aria-labelledby="backtest-delete-confirmation" className="confirmation">
              <h2 id="backtest-delete-confirmation">Delete Backtest</h2>
              <p>This permanently deletes the retained generated Backtest record/artifacts and registered runtime residue.</p>
              <dl className="facts compact">
                <div><dt>Backtest</dt><dd>Selected retained Backtest</dd></div>
                <div><dt>Source</dt><dd>Retained Strategy Challenger</dd></div>
              </dl>
              <div className="actions">
                <button type="button" disabled={busy !== ''} onClick={confirmBacktestDelete}>
                  <ActionProgress
                    active={busy === 'delete-backtest:' + pendingBacktestDelete.backtest_id}
                    idle="CONFIRM DELETE BACKTEST"
                    pending="Deleting..."
                  />
                </button>
                <button type="button" onClick={() => { setPendingBacktestDelete(null); setDialogMode(null) }} disabled={busy !== ''}>Cancel</button>
              </div>
            </section>
          )}

          {dialogMode === 'retirement' && (
            <section role="dialog" aria-modal="true" aria-labelledby="retirement-confirmation" className="confirmation">
              <h2 id="retirement-confirmation">Confirm non-destructive retirement</h2>
              <p>
                This removes the Strategy from the Active Challenger view only. MQ5, SET, compiled EX5 evidence when present, manifest, optimizer lineage, promotion history and backtest history are preserved.
              </p>
              <dl className="facts compact">
                <div><dt>Challenger</dt><dd>Selected retained Strategy Challenger</dd></div>
                <div><dt>Artifact integrity</dt><dd><IntegrityValue value={detail.artifact_integrity.status} /></dd></div>
                <div><dt>Existing backtests</dt><dd>{backtests?.total ?? 0}</dd></div>
                <div><dt>Delete files</dt><dd><strong>No</strong></dd></div>
              </dl>
              <div className="actions">
                <button type="button" onClick={confirmRetirement} disabled={busy !== ''}>
                  <ActionProgress
                    active={busy === 'retirement'}
                    idle="CONFIRM RETIRE / ARCHIVE"
                    pending="Retiring..."
                  />
                </button>
                <button type="button" onClick={() => setDialogMode(null)} disabled={busy !== ''}>
                  Cancel
                </button>
              </div>
            </section>
          )}
        </>
      )}
    </>
  )
}
