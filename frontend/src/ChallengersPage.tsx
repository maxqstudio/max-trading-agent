import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Pagination, SortHeader } from './DataTable'
import { ActionButton, ActionProgress } from './ActionControls'
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
  state: string
}

type BulkBacktestResult = {
  candidate: ChallengerListItem
  backtest: BacktestRecord
}

type View = 'active' | 'retired'
type DialogMode = 'promotion' | 'retirement' | 'delete-challenger' | 'delete-backtest' | 'clean-backtest-runtime' | 'bulk-backtest' | 'run-all-backtests' | null

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

function backtestDateOrdinal(value: string) {
  const match = /^(\d{4})\.(\d{2})\.(\d{2})$/.exec(value)
  if (!match) return null
  const [year, month, day] = match.slice(1).map(Number)
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31) return null
  const date = new Date(0)
  date.setUTCFullYear(year, month - 1, day)
  date.setUTCHours(0, 0, 0, 0)
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() !== month - 1
    || date.getUTCDate() !== day
  ) return null
  return date.getTime()
}

function validBacktestDateRange(fromDate: string, toDate: string) {
  const from = backtestDateOrdinal(fromDate)
  const to = backtestDateOrdinal(toDate)
  return from !== null && to !== null && from < to
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
  if (!blockers?.length) return 'No blocking dependency.'
  return blockers.map((blocker) => {
    if (blocker.startsWith('ACTIVE_BACKTEST:')) return 'A Backtest is still active; wait for it to finish.'
    if (blocker.startsWith('ACTIVE_PROMOTION:')) return 'A Champion promotion is still active; wait for it to finish.'
    if (blocker === 'CURRENT_CHAMPION_DEPENDS_ON_CHALLENGER') return 'The current Champion still depends on this Challenger.'
    if (blocker === 'BACKTEST_HISTORY_EXISTS_DELETE_BACKTESTS_FIRST') return 'Delete retained Backtest records first.'
    if (blocker === 'PROMOTION_HISTORY_PROTECTED') return 'Promotion history protects this Challenger from deletion.'
    if (blocker === 'LEGACY_OR_FOREIGN_CHALLENGER_ARTIFACT_PROTECTED') return 'The Challenger bundle is outside the deletable artifact authority.'
    return blocker
  }).join(' ')
}

function metricLabel(name: string) {
  return name
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (value) => value.toUpperCase())
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
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    view,
    q: query,
    sort,
    order,
    page: String(page),
    page_size: String(pageSize),
  })
  const response = await fetch('/api/challengers/registry?' + params.toString(), { signal })
  return await jsonOrError(response) as RegistryPage
}

async function fetchAllActiveChallengers() {
  const first = await fetchRegistry('active', '', 'created', 'desc', 1, 100)
  const items = [...first.items]
  for (let nextPage = 2; nextPage <= first.pages; nextPage += 1) {
    const next = await fetchRegistry('active', '', 'created', 'desc', nextPage, 100)
    if (next.total !== first.total || next.pages !== first.pages) {
      throw new Error('The active Challenger registry changed during preflight. Refresh and try again.')
    }
    items.push(...next.items)
  }
  const ids = new Set(items.map((item) => item.challenger_id))
  if (items.length !== first.total || ids.size !== items.length) {
    throw new Error('The active Challenger registry changed during preflight. Refresh and try again.')
  }
  return items
}

async function fetchChampion(signal?: AbortSignal) {
  const response = await fetch('/api/champion/summary', { signal })
  return await jsonOrError(response) as ChampionAuthority
}

async function fetchDeletePreflight(challengerId: string, signal?: AbortSignal) {
  try {
    const response = await fetch(
      '/api/challengers/' + encodeURIComponent(challengerId) + '/delete-preflight',
      { signal },
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

async function fetchDetail(challengerId: string, signal?: AbortSignal) {
  const response = await fetch('/api/challengers/' + encodeURIComponent(challengerId), { signal })
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
  signal?: AbortSignal,
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
  const response = await fetch('/api/challengers/backtests?' + params.toString(), { signal })
  if (response.status === 404) {
    const legacy = await fetch(
      '/api/challengers/' + encodeURIComponent(challengerId) + '/backtests',
      { signal },
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
  return await jsonOrError(response) as BacktestPage
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
  const [pendingBacktestClean, setPendingBacktestClean] = useState<BacktestRecord | null>(null)
  const [pendingBacktestDelete, setPendingBacktestDelete] = useState<PendingBacktestDelete | null>(null)
  const [backtestSelection, setBacktestSelection] = useState<Set<string>>(new Set())
  const [backtestBulkPreflight, setBacktestBulkPreflight] = useState<BacktestBulkPreflight | null>(null)
  const [backtestBulkAction, setBacktestBulkAction] = useState<'clean' | 'delete' | null>(null)
  const [pendingBulkBacktests, setPendingBulkBacktests] = useState<ChallengerListItem[]>([])
  const [pendingBulkDateRange, setPendingBulkDateRange] = useState<{ from_date: string; to_date: string } | null>(null)
  const [bulkBacktestResults, setBulkBacktestResults] = useState<BulkBacktestResult[]>([])
  const [bulkRunIndex, setBulkRunIndex] = useState(0)
  const [champion, setChampion] = useState<ChampionAuthority | null>(null)
  const [dialogMode, setDialogMode] = useState<DialogMode>(null)
  const [busy, setBusy] = useState('')
  const [registryAction, setRegistryAction] = useState('')
  const [backtestListAction, setBacktestListAction] = useState('')
  const [operationResult, setOperationResult] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailLoadingId, setDetailLoadingId] = useState('')
  const [loadedBacktestKey, setLoadedBacktestKey] = useState('')
  const backtestRequestKey = JSON.stringify([
    detail?.challenger_id, backtestQuery, backtestState, backtestSort, backtestOrder, backtestPage, backtestPageSize,
  ])
  const backtestsLoading = Boolean(detail?.challenger_id) && loadedBacktestKey !== backtestRequestKey
  const [backtestDetailLoadingId, setBacktestDetailLoadingId] = useState('')
  const detailController = useRef<AbortController | null>(null)
  const [backtestForm, setBacktestForm] = useState({
    symbol: '',
    relative_symbol: '',
    period: '',
    from_date: '',
    to_date: '',
  })

  useEffect(() => {
    if (dialogMode !== 'run-all-backtests' || busy !== '') return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setDialogMode(null)
        setPendingBulkBacktests([])
        setPendingBulkDateRange(null)
        setOperationResult('Bulk Backtest cancelled. No Backtest was started.')
      }
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [dialogMode, busy])

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

  const detailVerified = detail?.artifact_integrity.status === 'VERIFIED'
  const registryControlsBlocked = loading || detailLoading || busy !== ''
  const registryControlsBlockedReason = loading
    ? 'Wait for the Challenger registry to finish loading.'
    : detailLoading ? 'Wait for the selected Challenger details to finish loading.'
      : 'Wait for the current Challenger operation to finish.'

  const blockAction = (reason: string) => {
    setOperationResult('Action blocked: ' + reason)
  }

  const loadDetail = async (challengerId: string, requestedController?: AbortController) => {
    detailController.current?.abort()
    const controller = requestedController ?? new AbortController()
    detailController.current = controller
    setDetailLoading(true)
    setDetailLoadingId(challengerId)
    setOperationResult('Loading selected Challenger and safety checks…')
    setError('')
    applyDetail(null)
    setDeletePreflight(null)
    setBacktests(null)
    try {
      const [nextDetail, preflight] = await Promise.all([
        fetchDetail(challengerId, controller.signal),
        fetchDeletePreflight(challengerId, controller.signal),
      ])
      if (controller.signal.aborted) return
      applyDetail(nextDetail)
      setDeletePreflight(preflight)
      setBacktestSelection(new Set())
      setBacktestBulkPreflight(null)
      setBacktestBulkAction(null)
      setBacktestPage(1)
    } catch (reason) {
      if (!controller.signal.aborted) setError((reason as Error).message)
    } finally {
      if (!controller.signal.aborted) {
        setOperationResult('')
        setDetailLoading(false)
        setDetailLoadingId('')
        if (detailController.current === controller) detailController.current = null
      }
    }
  }

  const invalidateSelectedDetail = () => {
    detailController.current?.abort()
    detailController.current = null
    setDetailLoading(false)
    setDetailLoadingId('')
    applyDetail(null)
    setDeletePreflight(null)
    setBacktests(null)
    setBacktestSelection(new Set())
    setBacktestBulkPreflight(null)
    setBacktestBulkAction(null)
    setPendingBulkBacktests([])
    setPendingBulkDateRange(null)
    setBacktestDetail(null)
    setPendingBacktestClean(null)
    setPendingBacktestDelete(null)
    setDialogMode(null)
    setOperationResult('')
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

  const refresh = async (preferredId?: string, completedMessage?: string) => {
    invalidateSelectedDetail()
    setLoading(true)
    setError('')
    setOperationResult(completedMessage
      ? completedMessage + ' Refreshing Challenger and Champion authority…'
      : 'Refreshing Challenger and Champion authority…')
    try {
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
        setOperationResult(completedMessage ?? 'Authority refreshed. No Challenger is available in this view.')
        return
      }
      await loadDetail(selectedId)
      if (completedMessage) setOperationResult(completedMessage)
    } catch (reason) {
      setOperationResult('Authority refresh did not complete; actions remain blocked until it succeeds.')
      throw reason
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    Promise.all([
      fetchRegistry(view, query, sort, order, page, pageSize, controller.signal),
      fetchChampion(controller.signal),
    ])
      .then(async ([nextRegistry, nextChampion]) => {
        if (!active) return
        setRegistry(nextRegistry)
        setChampion(nextChampion)
        if (nextRegistry.items.length === 0) {
          applyDetail(null)
          setDeletePreflight(null)
          setBacktests(null)
          setDetailLoading(false)
          setOperationResult('')
          return
        }
        const selectedId = nextRegistry.items[0].challenger_id
        if (!active) return
        await loadDetail(selectedId, controller)
      })
      .catch((reason: Error) => {
        if (active && !controller.signal.aborted) setError(reason.message)
      })
      .finally(() => {
        if (active) {
          setLoading(false)
          setRegistryAction('')
        }
      })
    return () => {
      active = false
      controller.abort()
      if (detailController.current === controller) detailController.current = null
    }
  }, [view, query, sort, order, page, pageSize])

  useEffect(() => {
    if (!detail?.challenger_id) {
      return
    }
    let active = true
    const controller = new AbortController()
    fetchBacktests(
      detail.challenger_id,
      backtestQuery,
      backtestState,
      backtestSort,
      backtestOrder,
      backtestPage,
      backtestPageSize,
      controller.signal,
    )
      .then((next) => {
        if (!active) return
        setBacktests(next)
        setLoadedBacktestKey(backtestRequestKey)
        if (next.page !== backtestPage) setBacktestPage(next.page)
      })
      .catch((reason: Error) => {
        if (active && !controller.signal.aborted) {
          setError(reason.message)
          setLoadedBacktestKey(backtestRequestKey)
        }
      })
      .finally(() => {
        if (active) setBacktestListAction('')
      })
    return () => { active = false; controller.abort() }
  }, [
    detail?.challenger_id,
    backtestQuery,
    backtestState,
    backtestSort,
    backtestOrder,
    backtestPage,
    backtestPageSize,
    backtestRequestKey,
  ])

  const searchRegistry = (event: FormEvent) => {
    event.preventDefault()
    if (registryControlsBlocked) return blockAction(registryControlsBlockedReason)
    const nextQuery = queryDraft.trim()
    if (nextQuery === query && page === 1) {
      setOperationResult('Search criteria are already applied; no new request was sent.')
      return
    }
    setRegistryAction('search')
    invalidateSelectedDetail()
    setLoading(true)
    setError('')
    setPage(1)
    setQuery(nextQuery)
  }

  const clearRegistrySearch = () => {
    if (registryControlsBlocked) return blockAction(registryControlsBlockedReason)
    if (queryDraft.trim() === '' && query === '' && page === 1) {
      setOperationResult('Search filters are already clear; no new request was sent.')
      return
    }
    setRegistryAction('clear')
    invalidateSelectedDetail()
    setLoading(true)
    setError('')
    setQueryDraft('')
    setQuery('')
    setPage(1)
  }

  const switchView = (next: View) => {
    if (registryControlsBlocked) return blockAction(registryControlsBlockedReason)
    if (next === view) return
    setRegistryAction(next)
    invalidateSelectedDetail()
    setDialogMode(null)
    setOperationResult('')
    setError('')
    setLoading(true)
    setPage(1)
    setView(next)
  }

  const confirmPromotion = async () => {
    if (!detail) return blockAction('select and load a Challenger before promoting.')
    if (!detailVerified) return blockAction('Challenger evidence is not verified; no promotion was submitted.')
    if (detail.status !== 'CHALLENGER') return blockAction('only an active Challenger can be promoted.')
    if (busy !== '' || detailLoading || loading) return blockAction('another authority operation is in progress; wait for it to finish.')
    setBusy('promotion')
    setError('')
    setOperationResult('Submitting Champion promotion and verifying the resulting authority…')
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
      await refresh(detail.challenger_id, 'Promotion completed.')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  const confirmRetirement = async () => {
    if (!detail) return blockAction('select and load a Challenger before retiring it.')
    if (!detailVerified) return blockAction('Challenger evidence is not verified; no retirement was submitted.')
    if (detail.status !== 'CHALLENGER') return blockAction('only an active Challenger can be retired.')
    if (busy !== '' || detailLoading || loading) return blockAction('another authority operation is in progress; wait for it to finish.')
    setBusy('retirement')
    setError('')
    setOperationResult('Retiring Challenger, removing its compiled MT5 EA, and preserving its source parameters…')
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
      const completion = result.runtime_ea === 'REMOVED'
        ? 'Challenger retired. Its compiled MT5 EA was removed; DB parameters, source bundle, and backtest history were preserved.'
        : 'Challenger retired. No compiled MT5 EA was present; DB parameters, source bundle, and backtest history were preserved.'
      setOperationResult(completion)
      setDialogMode(null)
      await refresh(undefined, completion)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  const runBacktest = async () => {
    if (!detail) return blockAction('select and load a Challenger before starting a Backtest.')
    if (!detailVerified) return blockAction('Challenger evidence is not verified; no Backtest was submitted.')
    if (busy !== '' || detailLoading || backtestsLoading || loading) return blockAction('Challenger or Backtest authority is still loading, or another action is running.')
    if (!validBacktestDateRange(backtestForm.from_date, backtestForm.to_date)) {
      return blockAction(
        'Use valid YYYY.MM.DD dates, with Backtest From earlier than Backtest To. No Backtest was started.',
      )
    }
    setBusy('backtest')
    setError('')
    setOperationResult('Submitting the request to MT5 Strategy Tester…')
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
        'Backtest request returned status: ' + ownerOperationalStatus(result.state) + '. Refreshing retained history…',
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

  const prepareAllBacktests = async () => {
    if (view !== 'active' || !detail || detail.status !== 'CHALLENGER') {
      return blockAction('select a verified active Challenger before preparing the all-active Backtest.')
    }
    if (!detailVerified) return blockAction('Challenger evidence is not verified; no Backtest was prepared.')
    if (busy !== '' || detailLoading || loading || backtestsLoading) {
      return blockAction('wait for Challenger authority and Backtest history to finish loading.')
    }
    if (!validBacktestDateRange(backtestForm.from_date, backtestForm.to_date)) {
      return blockAction('Use a valid Backtest date range before preparing the all-active run. No Backtests were started.')
    }
    const selectedDateRange = {
      from_date: backtestForm.from_date,
      to_date: backtestForm.to_date,
    }
    setBusy('bulk-backtest-preflight')
    setError('')
    setBulkBacktestResults([])
    setOperationResult('Checking every active Challenger before any Backtest can start…')
    try {
      const candidates = await fetchAllActiveChallengers()
      if (!candidates.length) {
        throw new Error('No active Challengers are available for a bulk Backtest.')
      }
      const details = await Promise.all(
        candidates.map((candidate) => fetchDetail(candidate.challenger_id)),
      )
      const allVerified = details.every((candidate, index) => (
        candidate.challenger_id === candidates[index].challenger_id
        && candidate.status === 'CHALLENGER'
        && candidate.artifact_integrity.status === 'VERIFIED'
      ))
      if (!allVerified) {
        throw new Error(
          'No Backtests were started because at least one active Challenger failed integrity verification.',
        )
      }
      setPendingBulkBacktests(candidates)
      setPendingBulkDateRange(selectedDateRange)
      setBulkRunIndex(0)
      setDialogMode('run-all-backtests')
      setOperationResult(
        'Integrity preflight passed for ' + candidates.length + ' active Challengers. Review before starting.',
      )
    } catch (reason) {
      setError((reason as Error).message)
      setOperationResult('Bulk Backtest preflight stopped. No Backtests were started.')
    } finally {
      setBusy('')
    }
  }

  const confirmAllBacktests = async () => {
    if (view !== 'active' || pendingBulkBacktests.length === 0 || !pendingBulkDateRange) {
      return blockAction('the verified active Challenger snapshot is missing; no Backtests were started.')
    }
    if (busy !== '') return blockAction('another authority operation is running; no bulk Backtest was started.')
    const candidates = [...pendingBulkBacktests]
    const dateRange = { ...pendingBulkDateRange }
    setBusy('bulk-run-backtests')
    setError('')

    let completed = 0
    let failure: { index: number; message: string } | null = null
    for (let index = 0; index < candidates.length; index += 1) {
      const candidate = candidates[index]
      setBulkRunIndex(index + 1)
      setOperationResult(
        'Running Backtest ' + (index + 1) + ' of ' + candidates.length
        + ' in MT5 Strategy Tester. Completed results are retained.',
      )
      try {
        const response = await fetch(
          '/api/challengers/' + encodeURIComponent(candidate.challenger_id) + '/backtest',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(dateRange),
          },
        )
        const result = await jsonOrError(response) as BacktestRecord
        setBulkBacktestResults((current) => [...current, { candidate, backtest: result }])
        if (result.state !== 'COMPLETED') {
          throw new Error('The Backtest did not return a confirmed completed state.')
        }
        completed += 1
      } catch (reason) {
        failure = {
          index,
          message: (reason as Error).message || 'The Backtest response was not confirmed.',
        }
        break
      }
    }

    setDialogMode(null)
    setPendingBulkBacktests([])
    setPendingBulkDateRange(null)
    let refreshFailure = ''
    try {
      await refresh(detail?.challenger_id)
    } catch {
      refreshFailure = ' Challenger authority refresh did not complete.'
    }
    setBusy('')
    setBulkRunIndex(0)

    if (failure) {
      const remaining = candidates.length - completed - 1
      setError('Bulk Backtest stopped at candidate ' + (failure.index + 1) + ': ' + failure.message)
      setOperationResult(
        'Stopped after ' + completed + ' of ' + candidates.length + '. '
        + remaining + ' remaining candidate(s) were not started. Check Backtest history before retrying; '
        + 'the last request may have reached MT5.' + refreshFailure,
      )
      return
    }

    setOperationResult(
      'Completed Backtests for ' + completed + ' of ' + candidates.length
      + ' active Challengers.' + refreshFailure,
    )
    if (refreshFailure) setError(refreshFailure.trim())
  }

  function changeRegistrySort(field: string, nextOrder: 'asc' | 'desc') {
    setRegistryAction('sort:' + field)
    invalidateSelectedDetail()
    setSort(field)
    setOrder(nextOrder)
    setPage(1)
    setLoading(true)
  }

  function changeBacktestSort(field: string, nextOrder: 'asc' | 'desc') {
    setBacktestListAction('sort:' + field)
    setBacktestSort(field)
    setBacktestOrder(nextOrder)
    setBacktestPage(1)
  }

  async function viewBacktest(backtestId: string) {
    if (busy !== '' || backtestsLoading) {
      return blockAction('Backtest history is loading or another action is in progress.')
    }
    setBacktestDetail(null)
    setBacktestDetailLoadingId(backtestId)
    setOperationResult('Loading retained Backtest details…')
    setError('')
    try {
      const response = await fetch(
        '/api/challengers/backtests/' + encodeURIComponent(backtestId),
      )
      setBacktestDetail(await jsonOrError(response) as BacktestRecord)
      setOperationResult('Backtest details loaded.')
    } catch (reason) {
      setError((reason as Error).message)
      setOperationResult('Backtest details could not be loaded; no data was changed.')
    } finally {
      setBacktestDetailLoadingId('')
    }
  }

  function openBacktestReport(backtestId: string, reportAvailable: boolean) {
    if (!reportAvailable) {
      return blockAction('no retained report exists for this Backtest.')
    }
    window.open(
      '/api/challengers/backtests/' + encodeURIComponent(backtestId) + '/report',
      '_blank',
      'noopener,noreferrer',
    )
    setOperationResult('Report opened in a new tab if allowed by the browser. If no tab appeared, allow pop-ups for this site.')
  }

  function prepareBacktestRuntimeClean(record: BacktestRecord) {
    if (busy !== '') return blockAction('another action is in progress; no runtime files were changed.')
    if (['PREPARED', 'RUNNING'].includes(record.state)) {
      return blockAction('runtime cleanup is blocked while this Backtest is active; no files were changed.')
    }
    if (record.runtime_status === 'CLEANED') {
      return blockAction('this Backtest’s temporary files are already cleaned; retained results remain available and the shared Challenger EA is unchanged.')
    }
    setPendingBacktestClean(record)
    setDialogMode('clean-backtest-runtime')
    setOperationResult('Review the runtime files that will be removed before confirming cleanup.')
  }

  async function confirmBacktestRuntimeClean() {
    if (!pendingBacktestClean) return blockAction('no Backtest runtime is selected for cleanup; no files were changed.')
    if (busy !== '') return blockAction('another action is in progress; no runtime files were changed.')
    if (pendingBacktestClean.challenger_id !== detail?.challenger_id) {
      return blockAction('the selected Challenger changed; refresh history and run the safety check again.')
    }
    if (['PREPARED', 'RUNNING'].includes(pendingBacktestClean.state)) {
      return blockAction('runtime cleanup is blocked while this Backtest is active; no files were changed.')
    }
    if (pendingBacktestClean.runtime_status === 'CLEANED') {
      setPendingBacktestClean(null)
      setDialogMode(null)
      return blockAction('this Backtest runtime is already cleaned. No files were changed.')
    }
    const pending = pendingBacktestClean
    const busyKey = 'clean-backtest:' + pending.backtest_id
    setBusy(busyKey)
    setError('')
    setOperationResult('Removing the confirmed Backtest runtime files; retained results will remain available…')
    try {
      const response = await fetch(
        '/api/challengers/backtests/' + encodeURIComponent(pending.backtest_id) + '/clean-runtime',
        { method: 'POST' },
      )
      const result = await jsonOrError(response)
      setOperationResult(
        'Runtime cleaned · removed ' + String(result.removed_bytes ?? 0) + ' bytes. Backtest results and retained report remain available.',
      )
      setPendingBacktestClean(null)
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

  async function confirmBacktestDelete() {
    if (!pendingBacktestDelete) return blockAction('no Backtest is selected for deletion.')
    if (busy !== '') return blockAction('another action is in progress; no Backtest was deleted.')
    if (['PREPARED', 'RUNNING'].includes(pendingBacktestDelete.state)) {
      return blockAction('deletion is blocked while this Backtest is active; no data was deleted.')
    }
    if (pendingBacktestDelete.challenger_id !== detail?.challenger_id) {
      return blockAction('the selected Challenger changed; refresh history and run the safety check again.')
    }
    const pending = pendingBacktestDelete
    const busyKey = 'delete-backtest:' + pending.backtest_id
    setBusy(busyKey)
    setError('')
    setOperationResult('Deleting the explicitly selected retained Backtest…')
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
    if (!backtestSelection.size) return blockAction('select one or more retained Backtests first.')
    if (busy !== '' || backtestsLoading || loading) return blockAction('wait for the current history or authority operation to finish.')
    setBusy('bulk-preflight-' + action)
    setError('')
    setOperationResult('Checking safety and dependencies for the selected Backtests…')
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
    if (!detail || !backtestBulkAction || !backtestSelection.size) {
      return blockAction('the Backtest selection or safety check is missing; no bulk action was submitted.')
    }
    const selectedIds = Array.from(backtestSelection).sort()
    const preflightIds = backtestBulkPreflight?.items.map((item) => item.backtest_id).sort() ?? []
    if (!backtestBulkPreflight || backtestBulkPreflight.action !== backtestBulkAction
      || backtestBulkPreflight.blocked > 0
      || preflightIds.length !== selectedIds.length
      || preflightIds.some((id, index) => id !== selectedIds[index])) {
      return blockAction('the current selection is blocked or differs from its safety check; rerun preflight. No items were changed.')
    }
    if (busy !== '' || backtestsLoading || loading) return blockAction('wait for the current history or authority operation to finish.')
    setBusy('bulk-' + backtestBulkAction)
    setError('')
    setOperationResult('Applying the preflighted Backtest action…')
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
    if (!detail) return blockAction('select and load a Challenger before deleting it.')
    if (deletePreflight?.challenger_id !== detail.challenger_id || !deletePreflight.deletable) {
      return blockAction('Challenger deletion is not cleared by the current safety check; no deletion was submitted.')
    }
    if (busy !== '' || detailLoading || loading) return blockAction('another authority operation is in progress; wait for it to finish.')
    setBusy('delete-challenger')
    setError('')
    setOperationResult('Deleting the preflighted generated Challenger record and bundle…')
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
      await refresh(undefined, 'Challenger deleted.')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy('')
    }
  }

  const isActive = detail?.status === 'CHALLENGER'
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

      {bulkBacktestResults.length > 0 && (
        <section aria-labelledby="bulk-backtest-results">
          <div className="section-head">
            <div>
              <h2 id="bulk-backtest-results">All-active Backtest results</h2>
              <p className="subtle">
                Each row is a retained MT5 result. Range: {bulkBacktestResults[0].backtest.request.from_date}
                {' '}to {bulkBacktestResults[0].backtest.request.to_date}. The already-compiled Challenger EA was reused.
              </p>
            </div>
          </div>
          <div className="table-wrap data-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Challenger</th>
                  <th>Backtest</th>
                  <th>Net profit</th>
                  <th>PF</th>
                  <th>Trades</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {bulkBacktestResults.map(({ candidate, backtest }) => (
                  <tr key={backtest.backtest_id}>
                    <td>Round {candidate.source_round} · Pass {candidate.source_pass}</td>
                    <td>
                      <strong>{ownerOperationalStatus(backtest.state)}</strong>
                      <div><code>{backtest.backtest_id}</code></div>
                      {backtest.error && <small role="note">{ownerErrorMessage(backtest.error, 'Review retained Backtest history for the failure.')}</small>}
                    </td>
                    <td>{compactNumber(backtest.result?.metrics?.total_net_profit)}</td>
                    <td>{compactNumber(backtest.result?.metrics?.profit_factor)}</td>
                    <td>{backtest.result?.metrics?.total_trades ?? '—'}</td>
                    <td>
                      <ActionButton
                        type="button"
                        disabled={!backtest.report_sha256 || busy !== ''}
                        blockedReason={!backtest.report_sha256
                          ? 'No retained report is available for this Backtest.'
                          : 'Wait for the current Challenger operation.'}
                        onClick={() => openBacktestReport(backtest.backtest_id, Boolean(backtest.report_sha256))}
                      >
                        Open Report
                      </ActionButton>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section aria-labelledby="challenger-registry">
        <div className="section-head">
          <div>
            <h2 id="challenger-registry">Strategy Challenger registry</h2>
            <p className="subtle">
              Active and Retired / Archive are status views of the same preserved Strategy registry.
            </p>
          </div>
          <div className="view-tabs" aria-label="Challenger registry view">
            <ActionButton
              type="button"
              aria-pressed={view === 'active'}
              onClick={() => switchView('active')}
              disabled={registryControlsBlocked || detailLoading || view === 'active'}
              blockedReason={registryControlsBlocked
                ? registryControlsBlockedReason
                : detailLoading ? 'Wait for the selected Challenger details to finish loading.'
                  : 'The Active view is already selected.'}
            >
              <ActionProgress active={loading && registryAction === 'active'} idle="Active" pending="Loading Active…" />
            </ActionButton>
            <ActionButton
              type="button"
              aria-pressed={view === 'retired'}
              onClick={() => switchView('retired')}
              disabled={registryControlsBlocked || detailLoading || view === 'retired'}
              blockedReason={registryControlsBlocked
                ? registryControlsBlockedReason
                : detailLoading ? 'Wait for the selected Challenger details to finish loading.'
                  : 'The Retired / Archive view is already selected.'}
            >
              <ActionProgress active={loading && registryAction === 'retired'} idle="Retired / Archive" pending="Loading archive…" />
            </ActionButton>
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
              invalidateSelectedDetail()
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
                invalidateSelectedDetail()
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
            <ActionButton
              type="submit"
              disabled={registryControlsBlocked || (queryDraft.trim() === query && page === 1)}
              blockedReason={registryControlsBlocked
                ? registryControlsBlockedReason
                : 'Search criteria are already applied; no new request would be sent.'}
            >
              <ActionProgress active={loading && registryAction === 'search'} idle="Search" pending="Searching…" />
            </ActionButton>
            <ActionButton
              type="button"
              disabled={registryControlsBlocked || (queryDraft.trim() === '' && query === '' && page === 1)}
              blockedReason={registryControlsBlocked
                ? registryControlsBlockedReason
                : 'Search filters are already clear; no new request would be sent.'}
              onClick={clearRegistrySearch}
            >
              <ActionProgress active={loading && registryAction === 'clear'} idle="Clear" pending="Clearing…" />
            </ActionButton>
          </div>
        </form>

        {loading && <p role="status" className="loading">Loading Challenger registry…</p>}

        {registry && (
          <>
            <p className="subtle">
              {registry.total} {view === 'active' ? 'active' : 'retired'} Strategies
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <SortHeader label="Challenger" field="id" sort={sort} order={order} onSort={changeRegistrySort} loading={registryControlsBlocked} blockedReason={registryControlsBlockedReason} pending={registryAction === 'sort:id'} />
                    <th>Status</th>
                    <SortHeader label="Created" field="created" sort={sort} order={order} onSort={changeRegistrySort} loading={registryControlsBlocked} blockedReason={registryControlsBlockedReason} pending={registryAction === 'sort:created'} />
                    {view === 'retired' && (
                      <SortHeader label="Retired" field="retired" sort={sort} order={order} onSort={changeRegistrySort} loading={registryControlsBlocked} blockedReason={registryControlsBlockedReason} pending={registryAction === 'sort:retired'} />
                    )}
                    <SortHeader label="Source" field="source_job" sort={sort} order={order} onSort={changeRegistrySort} loading={registryControlsBlocked} blockedReason={registryControlsBlockedReason} pending={registryAction === 'sort:source_job'} />
                    <th>Round</th><th>Pass</th>
                    <SortHeader label="PF" field="profit_factor" sort={sort} order={order} onSort={changeRegistrySort} loading={registryControlsBlocked} blockedReason={registryControlsBlockedReason} pending={registryAction === 'sort:profit_factor'} />
                    <SortHeader label="RF" field="recovery_factor" sort={sort} order={order} onSort={changeRegistrySort} loading={registryControlsBlocked} blockedReason={registryControlsBlockedReason} pending={registryAction === 'sort:recovery_factor'} />
                    <SortHeader label="Mean R" field="mean_r" sort={sort} order={order} onSort={changeRegistrySort} loading={registryControlsBlocked} blockedReason={registryControlsBlockedReason} pending={registryAction === 'sort:mean_r'} />
                    <SortHeader label="Weighted R" field="weighted_r" sort={sort} order={order} onSort={changeRegistrySort} loading={registryControlsBlocked} blockedReason={registryControlsBlockedReason} pending={registryAction === 'sort:weighted_r'} />
                    <SortHeader label="Trades" field="trades" sort={sort} order={order} onSort={changeRegistrySort} loading={registryControlsBlocked} blockedReason={registryControlsBlockedReason} pending={registryAction === 'sort:trades'} />
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
                        <ActionButton
                          className="text-button mono"
                          onClick={() => loadDetail(item.challenger_id)}
                          disabled={loading || detailLoading || busy !== ''}
                          blockedReason={busy !== ''
                            ? 'Wait for the current Challenger action to finish before changing the selected item.'
                            : loading ? 'Wait for the Challenger list to finish loading.'
                              : 'Wait for the selected Challenger details to finish loading.'}
                        >
                          <ActionProgress
                            active={detailLoadingId === item.challenger_id}
                            idle={'Round ' + item.source_round + ' · Pass ' + item.source_pass}
                            pending="Loading Challenger…"
                          />
                        </ActionButton>
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
              loading={registryControlsBlocked}
              blockedReason={registryControlsBlockedReason}
              pendingDirection={registryAction === 'page-previous' ? 'previous' : registryAction === 'page-next' ? 'next' : ''}
              onPage={(next, direction) => { setRegistryAction('page-' + direction); invalidateSelectedDetail(); setLoading(true); setPage(next) }}
              onPageSize={(size) => { invalidateSelectedDetail(); setPageSize(size); setPage(1); setLoading(true) }}
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
                  <ActionButton
                    type="button"
                    onClick={() => setDialogMode('promotion')}
                    disabled={!detailVerified || busy !== '' || detailLoading}
                    blockedReason={!detailVerified
                      ? 'Promotion is blocked until this Challenger’s complete artifact evidence is verified.'
                      : busy !== '' ? 'Wait for the current Challenger operation.'
                        : 'Wait for the Challenger details to finish loading.'}
                  >
                    Promote to Champion
                  </ActionButton>
                  <ActionButton
                    type="button"
                    onClick={() => setDialogMode('retirement')}
                    disabled={!detailVerified || busy !== '' || detailLoading}
                    blockedReason={!detailVerified
                      ? 'Retirement is blocked until this Challenger’s complete artifact evidence is verified.'
                      : busy !== '' ? 'Wait for the current Challenger operation.'
                        : 'Wait for the Challenger details to finish loading.'}
                  >
                    Retire / Archive
                  </ActionButton>
                  <ActionButton
                    type="button"
                    onClick={() => setDialogMode('delete-challenger')}
                    disabled={!deletePreflight?.deletable || busy !== '' || detailLoading}
                    blockedReason={busy !== ''
                      ? 'Wait for the current Challenger operation.'
                      : detailLoading ? 'Wait for the Challenger details to finish loading.'
                        : !deletePreflight ? 'Deletion safety check has not completed.'
                          : blockerSummary(deletePreflight.blockers)}
                  >
                    Delete Challenger
                  </ActionButton>
                </div>
              )}
            </div>
            {isActive && deletePreflight && deletePreflight.blockers.length > 0 && (
              <p className="error-text">
                {deletePreflight.blockers.includes('DELETE_PREFLIGHT_UNAVAILABLE')
                  ? 'Deletion is blocked because its safety check could not be completed. Retry the check before deleting; no deletion was submitted.'
                  : 'Deletion is not allowed while this Challenger is protected by current authority or retained dependencies.'}
              </p>
            )}
            {!detailVerified && (
              <p role="alert" className="error-text">
                Promotion, retirement, and backtest actions are blocked until Challenger evidence passes integrity verification.
              </p>
            )}
            <dl className="facts compact">
              <div><dt>Candidate</dt><dd>Optimizer round {detail.source_round} · pass {detail.source_pass}</dd></div>
              <div><dt>Status</dt><dd><strong>{ownerOperationalStatus(detail.status)}</strong></dd></div>
              <div><dt>Created</dt><dd>{detail.created_utc}</dd></div>
              {detail.retired_utc && <div><dt>Retired</dt><dd>{detail.retired_utc}</dd></div>}
              <div><dt>Source</dt><dd>Retained qualified Optimizer evidence</dd></div>
              <div><dt>EA version</dt><dd>{detail.ea_version}</dd></div>
              {detailVerified && (
                <>
                  <div><dt>Challenger EA</dt><dd>
                    <a href={'/api/challengers/' + encodeURIComponent(detail.challenger_id) + '/ea'}>
                      Download Challenger EA source
                    </a>
                  </dd></div>
                  <div><dt>Parameter preset</dt><dd>
                    <a href={'/api/challengers/' + encodeURIComponent(detail.challenger_id) + '/set'}>
                      Download Challenger parameter preset
                    </a>
                  </dd></div>
                  <div><dt>MT5 executable</dt><dd>Compiled when this Challenger is created; Backtests reuse the verified executable. Promotion installs it as Champion.</dd></div>
                </>
              )}
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
                  <ActionButton
                    type="button"
                    onClick={runBacktest}
                    disabled={!detailVerified || busy !== '' || detailLoading || backtestsLoading}
                    blockedReason={!detailVerified
                      ? 'Backtest is blocked until this Challenger’s complete artifact evidence is verified.'
                      : busy !== '' ? 'Wait for the current operation to finish.'
                        : detailLoading ? 'Wait for Challenger details to finish loading.'
                          : 'Backtest history is still loading.'}
                  >
                    <ActionProgress
                      active={busy === 'backtest'}
                      idle="Run Backtest"
                      pending="Running..."
                    />
                  </ActionButton>
                  <ActionButton
                    type="button"
                    onClick={prepareAllBacktests}
                    disabled={!detailVerified || busy !== '' || detailLoading || backtestsLoading || loading}
                    blockedReason={!detailVerified
                      ? 'Bulk Backtest is blocked until the selected Challenger’s integrity is verified.'
                      : busy !== '' ? 'Wait for the current operation to finish.'
                        : detailLoading || backtestsLoading || loading
                          ? 'Wait for current Challenger and Backtest data to finish loading.'
                          : 'Bulk run preflights all active Challengers and uses the selected date range.'}
                  >
                    <ActionProgress
                      active={busy === 'bulk-backtest-preflight' || busy === 'bulk-run-backtests'}
                      idle="Run Backtest All Active"
                      pending={busy === 'bulk-backtest-preflight'
                        ? 'Checking all Challengers...'
                        : 'Running ' + bulkRunIndex + ' of ' + pendingBulkBacktests.length + '...'}
                    />
                  </ActionButton>
                </div>
              </div>
              <p id="backtest-window-help" className="subtle">
                The Backtest date range is independent of the Optimizer run dates. Candidate identity, symbols, timeframe, and parameters stay fixed.
              </p>
              <div className="form-grid">
                <label>Main Symbol
                  <input
                    value={backtestForm.symbol}
                    readOnly
                    title="Fixed by the retained Challenger source contract."
                  />
                </label>
                <label>Relative Symbol
                  <input
                    value={backtestForm.relative_symbol}
                    readOnly
                    title="Fixed by the retained Challenger source contract."
                  />
                </label>
                <label>Timeframe
                  <input
                    value={backtestForm.period}
                    readOnly
                    title="Fixed by the retained Challenger source contract."
                  />
                </label>
                <label>Backtest From
                  <input
                    value={backtestForm.from_date}
                    onChange={(event) => setBacktestForm({ ...backtestForm, from_date: event.target.value })}
                    placeholder="YYYY.MM.DD"
                    aria-describedby="backtest-window-help"
                  />
                </label>
                <label>Backtest To
                  <input
                    value={backtestForm.to_date}
                    onChange={(event) => setBacktestForm({ ...backtestForm, to_date: event.target.value })}
                    placeholder="YYYY.MM.DD"
                    aria-describedby="backtest-window-help"
                  />
                </label>
              </div>
            </section>
          )}

          <section aria-labelledby="backtest-history">
            <h2 id="backtest-history">Backtest history</h2>
            <p className="subtle">
              Clean Backtest Files removes only this run’s temporary Tester preset and source MT5 report. Delete Backtest removes the retained result and its evidence. Both preserve the shared deployed Challenger EA.
            </p>
            {backtestsLoading && <p role="status" className="loading">Loading Backtest history; cleanup and bulk actions are temporarily disabled…</p>}
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
              <ActionButton
                type="button"
                onClick={toggleBacktestPageSelection}
                disabled={!backtests?.items.length || busy !== '' || backtestsLoading}
                blockedReason={!backtests?.items.length ? 'There are no Backtest rows on this page to select.' : backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current action to finish.'}
              >
                Select current page
              </ActionButton>
              <ActionButton
                type="button"
                onClick={() => setBacktestSelection(new Set())}
                disabled={!backtestSelection.size || busy !== '' || backtestsLoading}
                blockedReason={!backtestSelection.size ? 'Select at least one Backtest before clearing the selection.' : backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current action to finish.'}
              >
                Clear selection
              </ActionButton>
              <span>{backtestSelection.size} selected</span>
              <ActionButton
                type="button"
                onClick={() => prepareBacktestBulk('clean')}
                disabled={!backtestSelection.size || busy !== '' || backtestsLoading}
                blockedReason={!backtestSelection.size ? 'Select at least one Backtest to preflight runtime cleanup.' : backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current action to finish.'}
              >
                <ActionProgress
                  active={busy === 'bulk-preflight-clean'}
                  idle="Clean Selected Backtest Files"
                  pending="Checking safety..."
                />
              </ActionButton>
              <ActionButton
                type="button"
                onClick={() => prepareBacktestBulk('delete')}
                disabled={!backtestSelection.size || busy !== '' || backtestsLoading}
                blockedReason={!backtestSelection.size ? 'Select at least one Backtest to preflight deletion.' : backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current action to finish.'}
              >
                <ActionProgress
                  active={busy === 'bulk-preflight-delete'}
                  idle="Delete Selected"
                  pending="Checking safety..."
                />
              </ActionButton>
            </div>
            {!backtestsLoading && backtestSelection.size === 0 && backtests?.items.length !== 0 && (
              <p className="subtle">Select one or more retained Backtests to enable bulk cleanup or deletion.</p>
            )}
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
                        <SortHeader label="Backtest" field="id" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} loading={backtestsLoading || busy !== ''} blockedReason={backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current operation to finish.'} pending={backtestListAction === 'sort:id'} />
                        <SortHeader label="State" field="state" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} loading={backtestsLoading || busy !== ''} blockedReason={backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current operation to finish.'} pending={backtestListAction === 'sort:state'} />
                        <th>Market</th>
                        <th>TF</th>
                        <th>Period</th>
                        <SortHeader label="Net Profit" field="net_profit" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} loading={backtestsLoading || busy !== ''} blockedReason={backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current operation to finish.'} pending={backtestListAction === 'sort:net_profit'} />
                        <SortHeader label="PF" field="profit_factor" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} loading={backtestsLoading || busy !== ''} blockedReason={backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current operation to finish.'} pending={backtestListAction === 'sort:profit_factor'} />
                        <SortHeader label="RF" field="recovery_factor" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} loading={backtestsLoading || busy !== ''} blockedReason={backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current operation to finish.'} pending={backtestListAction === 'sort:recovery_factor'} />
                        <SortHeader label="Sharpe" field="sharpe" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} loading={backtestsLoading || busy !== ''} blockedReason={backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current operation to finish.'} pending={backtestListAction === 'sort:sharpe'} />
                        <SortHeader label="Trades" field="trades" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} loading={backtestsLoading || busy !== ''} blockedReason={backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current operation to finish.'} pending={backtestListAction === 'sort:trades'} />
                        <th>Win %</th>
                        <th>Max DD %</th>
                        <SortHeader label="Created" field="created" sort={backtestSort} order={backtestOrder} onSort={changeBacktestSort} loading={backtestsLoading || busy !== ''} blockedReason={backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current operation to finish.'} pending={backtestListAction === 'sort:created'} />
                        <th>Runtime Status</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {backtests.items.map((record) => {
                        const metrics = record.result?.metrics
                        const runtimeCleaned = record.runtime_status === 'CLEANED'
                        const runtimeActive = ['PREPARED', 'RUNNING'].includes(record.state)
                        return (
                          <tr key={record.backtest_id}>
                            <td>
                              <input
                                aria-label={'Select retained Backtest created ' + record.created_utc}
                                type="checkbox"
                                checked={backtestSelection.has(record.backtest_id)}
                                disabled={busy !== '' || backtestsLoading}
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
                                <ActionButton
                                  type="button"
                                  disabled={busy !== '' || backtestsLoading || backtestDetailLoadingId !== ''}
                                  blockedReason={busy !== '' ? 'Wait for the current operation to finish.' : backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Another Backtest detail is loading.'}
                                  onClick={() => viewBacktest(record.backtest_id)}
                                >
                                  <ActionProgress active={backtestDetailLoadingId === record.backtest_id} idle="View Details" pending="Loading details…" />
                                </ActionButton>
                                <ActionButton
                                  type="button"
                                  disabled={!record.report_sha256 || busy !== '' || backtestsLoading}
                                  blockedReason={!record.report_sha256 ? 'No retained MT5 report is available for this Backtest.' : backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current operation to finish.'}
                                  onClick={() => openBacktestReport(record.backtest_id, Boolean(record.report_sha256))}
                                >
                                  Open Report
                                </ActionButton>
                                <ActionButton
                                  type="button"
                                  disabled={busy !== '' || backtestsLoading || runtimeActive || runtimeCleaned}
                                  blockedReason={runtimeCleaned
                                    ? 'Temporary files for this Backtest are already cleaned; retained metrics and reports remain available.'
                                    : runtimeActive
                                      ? 'Runtime cleanup is blocked while the Backtest is active.'
                                      : backtestsLoading ? 'Wait for Backtest history to finish loading.'
                                        : 'Wait for the current operation to finish.'}
                                  onClick={() => prepareBacktestRuntimeClean(record)}
                                >
                                  <ActionProgress
                                    active={busy === 'clean-backtest:' + record.backtest_id}
                                    idle="Clean Backtest Files"
                                    pending="Cleaning..."
                                  />
                                </ActionButton>
                                <ActionButton
                                  type="button"
                                  disabled={busy !== '' || backtestsLoading || ['PREPARED', 'RUNNING'].includes(record.state)}
                                  blockedReason={['PREPARED', 'RUNNING'].includes(record.state)
                                    ? 'Deletion is blocked while this Backtest is active; no data was deleted.'
                                    : backtestsLoading ? 'Wait for Backtest history to finish loading.'
                                      : 'Wait for the current operation to finish.'}
                                  onClick={() => {
                                    if (['PREPARED', 'RUNNING'].includes(record.state)) {
                                      blockAction('deletion is blocked while this Backtest is active; no data was deleted.')
                                      return
                                    }
                                    setPendingBacktestDelete({
                                      backtest_id: record.backtest_id,
                                      challenger_id: record.challenger_id,
                                      state: record.state,
                                    })
                                    setDialogMode('delete-backtest')
                                  }}
                                >
                                  Delete Backtest
                                </ActionButton>
                                {['PREPARED', 'RUNNING'].includes(record.state) && <small role="note">Actions blocked while Backtest is active.</small>}
                                {runtimeCleaned && <small role="note">This Backtest’s temporary files were cleaned. Retained metrics and reports remain available; the deployed Challenger EA is unchanged.</small>}
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
                  loading={backtestsLoading || busy !== ''}
                  blockedReason={backtestsLoading ? 'Wait for Backtest history to finish loading.' : 'Wait for the current operation to finish.'}
                  pendingDirection={backtestListAction === 'page-previous' ? 'previous' : backtestListAction === 'page-next' ? 'next' : ''}
                  onPage={(next, direction) => { setBacktestListAction('page-' + direction); setBacktestPage(next) }}
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
                  <ActionButton
                    type="button"
                    disabled={!backtestDetail.report_sha256}
                    blockedReason="No retained MT5 report is available for this Backtest."
                    onClick={() => openBacktestReport(backtestDetail.backtest_id, Boolean(backtestDetail.report_sha256))}
                  >Open Report</ActionButton>
                  {!backtestDetail.report_sha256 && <small role="note">No retained report exists; opening a report is unavailable.</small>}
                  <button type="button" onClick={() => setBacktestDetail(null)}>Close</button>
                </div>
              </div>
            </div>
          )}

          {dialogMode === 'run-all-backtests' && pendingBulkBacktests.length > 0 && (
            <section role="dialog" aria-modal="true" aria-labelledby="run-all-backtests-title" className="confirmation">
              <h2 id="run-all-backtests-title">Run all active Challenger Backtests</h2>
              <p>
                {pendingBulkBacktests.length} active Challengers passed the integrity preflight. They will run one at a time
                in MT5 Strategy Tester. The selected Backtest date range applies to all; each Challenger keeps its own
                symbol, timeframe, parameter set, and verified precompiled EA.
              </p>
              {pendingBulkDateRange && (
                <dl className="facts compact">
                  <div><dt>Backtest From</dt><dd>{pendingBulkDateRange.from_date}</dd></div>
                  <div><dt>Backtest To</dt><dd>{pendingBulkDateRange.to_date}</dd></div>
                </dl>
              )}
              <p>
                The sequence stops at the first unconfirmed result. Completed Backtests remain saved, remaining candidates
                are not started, and there are no automatic retries.
              </p>
              <ul aria-label="Active Challengers queued for Backtest">
                {pendingBulkBacktests.map((candidate) => (
                  <li key={candidate.challenger_id}>
                    Round {candidate.source_round}, pass {candidate.source_pass}
                  </li>
                ))}
              </ul>
              {busy === 'bulk-run-backtests' && (
                <p role="status" className="notice">
                  Running Backtest {bulkRunIndex} of {pendingBulkBacktests.length}. Keep MAX open; do not submit another run.
                </p>
              )}
              <div className="actions">
                  <ActionButton
                    type="button"
                    onClick={confirmAllBacktests}
                    disabled={busy !== '' || view !== 'active' || !pendingBulkBacktests.length}
                    blockedReason={view !== 'active'
                      ? 'Bulk Backtest is only available in the Active Challenger view.'
                      : !pendingBulkBacktests.length ? 'No verified active Challengers are queued.'
                        : 'Wait for the current operation to finish.'}
                  >
                  <ActionProgress
                    active={busy === 'bulk-run-backtests'}
                    idle="RUN ALL BACKTESTS"
                    pending={'RUNNING ' + bulkRunIndex + ' OF ' + pendingBulkBacktests.length}
                  />
                  </ActionButton>
                  <ActionButton
                    type="button"
                  onClick={() => {
                    setDialogMode(null)
                    setPendingBulkBacktests([])
                    setPendingBulkDateRange(null)
                    setOperationResult('Bulk Backtest cancelled. No Backtest was started.')
                    }}
                    disabled={busy !== ''}
                    blockedReason="The running Backtest batch must finish before it can be dismissed."
                  >
                    Cancel
                  </ActionButton>
              </div>
            </section>
          )}

          {dialogMode === 'bulk-backtest' && backtestBulkPreflight && backtestBulkAction && (
            <section role="dialog" aria-modal="true" aria-labelledby="backtest-bulk-confirmation" className="confirmation">
              <h2 id="backtest-bulk-confirmation">
                {backtestBulkAction === 'clean' ? 'Clean Selected Backtest Files' : 'Delete Selected Backtests'}
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
              {backtestBulkPreflight.blocked > 0 && (
                <p role="status" className="error-text">
                  This action is blocked: {backtestBulkPreflight.blocked} selected Backtest(s) failed the safety check. No items will be changed.
                </p>
              )}
              <div className="actions">
                <ActionButton
                  type="button"
                  onClick={confirmBacktestBulk}
                  disabled={backtestBulkPreflight.blocked > 0 || busy !== ''}
                  blockedReason={backtestBulkPreflight.blocked > 0
                    ? 'Safety preflight blocked ' + backtestBulkPreflight.blocked + ' selected Backtest(s); no items will be changed.'
                    : 'Wait for the current operation to finish.'}
                >
                  <ActionProgress
                    active={busy === 'bulk-clean' || busy === 'bulk-delete'}
                    idle="CONFIRM"
                    pending={busy === 'bulk-clean' ? 'Cleaning...' : 'Deleting...'}
                  />
                </ActionButton>
                <ActionButton
                  type="button"
                  onClick={() => {
                    setBacktestBulkPreflight(null)
                    setBacktestBulkAction(null)
                    setDialogMode(null)
                  }}
                  disabled={busy !== ''}
                  blockedReason="The cleanup/deletion batch must finish before this dialog can be dismissed."
                >
                  Cancel
                </ActionButton>
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
              {!detailVerified && <p role="alert" className="error-text">Promotion is blocked until Challenger evidence is verified. No change has been submitted.</p>}
              <div className="actions">
                <ActionButton
                  type="button"
                  onClick={confirmPromotion}
                  disabled={!detailVerified || busy !== '' || detailLoading || loading}
                  blockedReason={!detailVerified
                    ? 'Promotion is blocked because Challenger evidence is not verified. No change was submitted.'
                    : busy !== '' ? 'Wait for the current Challenger operation to finish.'
                      : detailLoading || loading ? 'Wait for Challenger details and registry checks to finish loading.'
                        : undefined}
                >
                  <ActionProgress
                    active={busy === 'promotion'}
                    idle="CONFIRM PROMOTION"
                    pending="Promoting..."
                  />
                </ActionButton>
                <ActionButton type="button" onClick={() => setDialogMode(null)} disabled={busy !== ''} blockedReason="Promotion must finish before this dialog can be dismissed.">
                  Cancel
                </ActionButton>
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
              {deletePreflight?.blockers.includes('DELETE_PREFLIGHT_UNAVAILABLE') && (
                <p role="alert" className="error-text">Deletion is blocked because its safety check is unavailable. Retry after the check succeeds; no deletion was submitted.</p>
              )}
              <div className="actions">
                <ActionButton
                  type="button"
                  disabled={!deletePreflight?.deletable || busy !== ''}
                  blockedReason={busy !== '' ? 'Wait for the current Challenger operation to finish.' : blockerSummary(deletePreflight?.blockers)}
                  onClick={confirmChallengerDelete}
                >
                  <ActionProgress
                    active={busy === 'delete-challenger'}
                    idle="CONFIRM DELETE CHALLENGER"
                    pending="Deleting..."
                  />
                </ActionButton>
                <ActionButton type="button" onClick={() => setDialogMode(null)} disabled={busy !== ''} blockedReason="Deletion must finish before this dialog can be dismissed.">Cancel</ActionButton>
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
                <ActionButton type="button" disabled={busy !== ''} blockedReason="Another Backtest operation is running." onClick={confirmBacktestDelete}>
                  <ActionProgress
                    active={busy === 'delete-backtest:' + pendingBacktestDelete.backtest_id}
                    idle="CONFIRM DELETE BACKTEST"
                    pending="Deleting..."
                  />
                </ActionButton>
                <ActionButton type="button" onClick={() => { setPendingBacktestDelete(null); setDialogMode(null) }} disabled={busy !== ''} blockedReason="Deletion must finish before this dialog can be dismissed.">Cancel</ActionButton>
              </div>
            </section>
          )}

          {dialogMode === 'clean-backtest-runtime' && pendingBacktestClean && (
            <section role="dialog" aria-modal="true" aria-labelledby="backtest-runtime-clean-confirmation" className="confirmation">
              <h2 id="backtest-runtime-clean-confirmation">Clean Backtest Files?</h2>
              <p>
                This removes only this Backtest’s temporary preset and source MT5 report. The retained Backtest record, metrics, archived report, and shared deployed Challenger EA are preserved.
              </p>
              <dl className="facts compact">
                <div><dt>Backtest</dt><dd>{pendingBacktestClean.backtest_id}</dd></div>
                <div><dt>Runtime</dt><dd>{ownerOperationalStatus(pendingBacktestClean.runtime_status)}</dd></div>
                <div><dt>Retained results</dt><dd>Preserved</dd></div>
              </dl>
              <div className="actions">
                <ActionButton type="button" disabled={busy !== ''} blockedReason="Another Backtest operation is running." onClick={confirmBacktestRuntimeClean}>
                  <ActionProgress
                    active={busy === 'clean-backtest:' + pendingBacktestClean.backtest_id}
                    idle="CONFIRM CLEAN BACKTEST FILES"
                    pending="Cleaning..."
                  />
                </ActionButton>
                <ActionButton type="button" onClick={() => { setPendingBacktestClean(null); setDialogMode(null); setOperationResult('Cleanup canceled; no runtime files were changed.') }} disabled={busy !== ''} blockedReason="Cleanup must finish before this dialog can be dismissed.">Cancel</ActionButton>
              </div>
            </section>
          )}

          {dialogMode === 'retirement' && (
            <section role="dialog" aria-modal="true" aria-labelledby="retirement-confirmation" className="confirmation">
              <h2 id="retirement-confirmation">Confirm Challenger retirement</h2>
              <p>
                This removes the compiled Challenger deployment from MT5 Experts (MQ5, SET, and EX5). Its database parameters, retained source bundle, manifest, optimizer lineage, promotion history, and backtest history stay available. Only an active Challenger can be retired; the Champion is not touched.
              </p>
              <dl className="facts compact">
                <div><dt>Challenger</dt><dd>Selected retained Strategy Challenger</dd></div>
                <div><dt>Artifact integrity</dt><dd><IntegrityValue value={detail.artifact_integrity.status} /></dd></div>
                <div><dt>Existing backtests</dt><dd>{backtests?.total ?? 0}</dd></div>
                <div><dt>Runtime MT5 EA</dt><dd><strong>Will be removed if verified</strong></dd></div>
                <div><dt>Saved parameters / source</dt><dd><strong>Preserved</strong></dd></div>
              </dl>
              {!detailVerified && <p role="alert" className="error-text">Retirement is blocked until Challenger evidence is verified. No change has been submitted.</p>}
              <div className="actions">
                <ActionButton
                  type="button"
                  onClick={confirmRetirement}
                  disabled={!detailVerified || busy !== '' || detailLoading || loading}
                  blockedReason={!detailVerified
                    ? 'Retirement is blocked because Challenger evidence is not verified. No EA or database rows were changed.'
                    : busy !== '' ? 'Wait for the current operation to finish.'
                      : detailLoading || loading ? 'Wait for Challenger safety checks to finish.'
                        : undefined}
                >
                  <ActionProgress
                    active={busy === 'retirement'}
                    idle="CONFIRM RETIRE + REMOVE MT5 EA"
                    pending="Retiring..."
                  />
                </ActionButton>
                <ActionButton type="button" onClick={() => setDialogMode(null)} disabled={busy !== ''} blockedReason="Retirement must finish before this dialog can be dismissed.">
                  Cancel
                </ActionButton>
              </div>
            </section>
          )}
        </>
      )}
    </>
  )
}
