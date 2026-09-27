import { useEffect, useMemo, useState } from 'react'
import { Pagination, SortHeader } from './DataTable'

type Artifact = {
  artifact_id: string
  artifact_type: string
  producer: string
  owner_type: string
  owner_id: string
  source_type?: string | null
  source_id?: string | null
  created_utc: string
  canonical_path: string
  runtime_paths: string[]
  size_bytes: number
  sha256?: string | null
  status: string
  in_use: boolean
  retention_class: string
  deletable: boolean
  cleanable: boolean
  dependencies: string[]
  storage: string
}

type ArtifactPage = {
  runtime: { status: string; reason: string; data_root?: string | null }
  summary: {
    total_generated_storage: number
    optimizer_storage: number
    challenger_storage: number
    backtest_storage: number
    runtime_storage: number
    safe_cleanup_bytes: number
    active_in_use_bytes: number
    protected_bytes: number
  }
  page: number
  page_size: number
  pages: number
  total: number
  items: Artifact[]
}

type Preflight = {
  selected: number
  deletable: number
  cleanable: number
  blocked: number
  project_bytes: number
  runtime_bytes: number
  items: (Artifact & { blocked_reason?: string | null })[]
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let amount = value
  let index = 0
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024
    index += 1
  }
  return amount.toFixed(index === 0 ? 0 : amount >= 10 ? 1 : 2) + ' ' + units[index]
}

export default function ArtifactsPage() {
  const [data, setData] = useState<ArtifactPage | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [query, setQuery] = useState('')
  const [type, setType] = useState('')
  const [producer, setProducer] = useState('')
  const [retention, setRetention] = useState('')
  const [status, setStatus] = useState('')
  const [inUse, setInUse] = useState('')
  const [storage, setStorage] = useState('')
  const [sort, setSort] = useState('created')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [selection, setSelection] = useState<Set<string>>(new Set())
  const [refresh, setRefresh] = useState(0)
  const [preflight, setPreflight] = useState<Preflight | null>(null)
  const [pendingAction, setPendingAction] = useState<'clean' | 'delete' | null>(null)
  const [pendingIds, setPendingIds] = useState<string[]>([])
  const [globalPreflight, setGlobalPreflight] = useState<any>(null)
  const [trace, setTrace] = useState<any>(null)
  const [message, setMessage] = useState('')

  useEffect(() => {
    const params = new URLSearchParams({
      q: query,
      type,
      producer,
      retention,
      status,
      in_use: inUse,
      storage,
      sort,
      order,
      page: String(page),
      page_size: String(pageSize),
    })
    fetch('/api/artifacts?' + params.toString())
      .then(async (response) => {
        const body = await response.json()
        if (!response.ok) throw new Error(body.detail ?? 'Artifact inventory unavailable')
        return body as ArtifactPage
      })
      .then((body) => {
        setData(body)
        if (body.page !== page) setPage(body.page)
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false))
  }, [query, type, producer, retention, status, inUse, storage, sort, order, page, pageSize, refresh])

  const allPageSelected = useMemo(
    () => Boolean(data?.items.length)
      && data!.items.every((item) => selection.has(item.artifact_id)),
    [data, selection],
  )

  function changeSort(field: string, nextOrder: 'asc' | 'desc') {
    setSort(field)
    setOrder(nextOrder)
    setPage(1)
  }

  function toggle(item: Artifact) {
    setSelection((current) => {
      const next = new Set(current)
      if (next.has(item.artifact_id)) next.delete(item.artifact_id)
      else next.add(item.artifact_id)
      return next
    })
  }

  function togglePage() {
    if (!data?.items.length) return
    setSelection((current) => {
      const next = new Set(current)
      for (const item of data.items) {
        if (allPageSelected) next.delete(item.artifact_id)
        else next.add(item.artifact_id)
      }
      return next
    })
  }

  async function prepare(action: 'clean' | 'delete', ids: string[]) {
    setError('')
    setMessage('')
    const response = await fetch('/api/artifacts/preflight', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_ids: ids }),
    })
    const body = await response.json()
    if (!response.ok) {
      setError(body.detail ?? 'Artifact preflight failed')
      return
    }
    setPreflight(body)
    setPendingAction(action)
    setPendingIds(ids)
  }

  async function executePending() {
    if (!pendingAction || !pendingIds.length) return
    setLoading(true)
    try {
      const response = await fetch('/api/artifacts/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          artifact_ids: pendingIds,
          action: pendingAction,
          confirmed: true,
        }),
      })
      const body = await response.json()
      if (!response.ok) throw new Error(body.detail ?? 'Artifact action failed')
      setMessage(
        pendingAction === 'clean'
          ? 'Selected runtime/artifacts cleaned.'
          : 'Selected generated artifacts deleted.',
      )
      setSelection(new Set())
      setPreflight(null)
      setPendingAction(null)
      setPendingIds([])
      setRefresh((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLoading(false)
    }
  }

  async function prepareGlobalCleanup() {
    setError('')
    const response = await fetch('/api/artifacts/cleanup/preflight')
    const body = await response.json()
    if (!response.ok) {
      setError(body.detail ?? 'Global cleanup preflight failed')
      return
    }
    setGlobalPreflight(body)
  }

  async function executeGlobalCleanup() {
    setLoading(true)
    try {
      const response = await fetch('/api/artifacts/cleanup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmed: true }),
      })
      const body = await response.json()
      if (!response.ok) throw new Error(body.detail ?? 'Global cleanup failed')
      setMessage('Generated data cleanup completed.')
      setGlobalPreflight(null)
      setSelection(new Set())
      setRefresh((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLoading(false)
    }
  }

  async function openTrace(item: Artifact) {
    setError('')
    const response = await fetch('/api/artifacts/' + item.artifact_id + '/trace')
    const body = await response.json()
    if (!response.ok) {
      setError(body.detail ?? 'Artifact trace unavailable')
      return
    }
    setTrace(body)
  }

  return (
    <div>
      <header className="page-head">
        <div>
          <p className="eyebrow">Generated-data control plane</p>
          <h1>Artifacts</h1>
        </div>
        <button type="button" onClick={prepareGlobalCleanup}>Clean Generated Data</button>
      </header>

      {error && <p role="alert" className="error">{error}</p>}
      {message && <p role="status" className="success">{message}</p>}
      {loading && <p role="status" className="loading">Reconciling MAX artifacts…</p>}

      {data && (
        <>
          <section aria-labelledby="artifact-storage-summary">
            <h2 id="artifact-storage-summary">Storage</h2>
            <div className="result-summary artifact-summary">
              <div><span>Total Generated Storage</span><strong>{formatBytes(data.summary.total_generated_storage)}</strong></div>
              <div><span>Optimizer Storage</span><strong>{formatBytes(data.summary.optimizer_storage)}</strong></div>
              <div><span>Challenger Storage</span><strong>{formatBytes(data.summary.challenger_storage)}</strong></div>
              <div><span>Backtest Storage</span><strong>{formatBytes(data.summary.backtest_storage)}</strong></div>
              <div><span>Runtime Storage</span><strong>{formatBytes(data.summary.runtime_storage)}</strong></div>
              <div><span>Safe Cleanup Bytes</span><strong>{formatBytes(data.summary.safe_cleanup_bytes)}</strong></div>
              <div><span>Active / In Use</span><strong>{formatBytes(data.summary.active_in_use_bytes)}</strong></div>
              <div><span>Protected Authority</span><strong>{formatBytes(data.summary.protected_bytes)}</strong></div>
            </div>
            <p className="muted">
              MT5 runtime: {data.runtime.status}
              {data.runtime.reason && <> · {data.runtime.reason}</>}
              {data.runtime.data_root && <> · <span className="mono">{data.runtime.data_root}</span></>}
            </p>
          </section>

          <section aria-labelledby="artifact-inventory">
            <h2 id="artifact-inventory">Inventory</h2>
            <div className="table-toolbar">
              <input
                aria-label="Search artifacts"
                placeholder="Artifact, job, Challenger, Backtest, source or path"
                value={query}
                onChange={(event) => { setQuery(event.target.value); setPage(1) }}
              />
              <select aria-label="Artifact type filter" value={type} onChange={(event) => { setType(event.target.value); setPage(1) }}>
                <option value="">All types</option>
                <option value="OPTIMIZER_JOB">Optimizer</option>
                <option value="STRATEGY_CHALLENGER">Challenger</option>
                <option value="CHALLENGER_BACKTEST">Backtest</option>
                <option value="ORPHAN_RUNTIME">Orphan runtime</option>
                <option value="ACCEPTED_MILESTONE_EVIDENCE">Accepted evidence</option>
                <option value="BASELINE_EA">Baseline EA</option>
              </select>
              <input
                aria-label="Artifact producer filter"
                placeholder="Producer"
                value={producer}
                onChange={(event) => { setProducer(event.target.value); setPage(1) }}
              />
              <select aria-label="Retention filter" value={retention} onChange={(event) => { setRetention(event.target.value); setPage(1) }}>
                <option value="">All retention</option>
                <option value="ACTIVE_AUTHORITY">Active authority</option>
                <option value="USER_GENERATED">User generated</option>
                <option value="TEMPORARY_RUNTIME">Temporary runtime</option>
                <option value="REGENERABLE">Regenerable</option>
              </select>
              <select aria-label="In-use filter" value={inUse} onChange={(event) => { setInUse(event.target.value); setPage(1) }}>
                <option value="">All usage</option>
                <option value="true">In use</option>
                <option value="false">Not in use</option>
              </select>
              <select aria-label="Storage filter" value={storage} onChange={(event) => { setStorage(event.target.value); setPage(1) }}>
                <option value="">All storage</option>
                <option value="PROJECT">Project</option>
                <option value="MT5_RUNTIME">MT5 runtime</option>
              </select>
              <input
                aria-label="Artifact status filter"
                placeholder="Status"
                value={status}
                onChange={(event) => { setStatus(event.target.value); setPage(1) }}
              />
              <button type="button" onClick={togglePage}>{allPageSelected ? 'Clear current page' : 'Select current page'}</button>
              <button type="button" disabled={!selection.size} onClick={() => setSelection(new Set())}>Clear selection</button>
              <span>{selection.size} selected</span>
              <button type="button" disabled={!selection.size} onClick={() => prepare('clean', Array.from(selection))}>Clean Selected Runtime</button>
              <button type="button" disabled={!selection.size} onClick={() => prepare('delete', Array.from(selection))}>Delete Selected</button>
            </div>

            {!loading && data.items.length === 0 && <p className="empty-state">No artifacts match the current filter.</p>}
            {data.items.length > 0 && (
              <div className="table-wrap data-table-wrap">
                <table>
                    <thead>
                      <tr>
                        <th>Select</th>
                        {[
                          ['type', 'Type'],
                          ['owner', 'Object'],
                          ['created', 'Created'],
                          ['size', 'Size'],
                          ['status', 'Status'],
                          ['storage', 'Storage'],
                          ['retention', 'Retention'],
                        ].map(([field, label]) => (
                          <SortHeader
                            key={field}
                            label={label}
                            field={field}
                            sort={sort}
                            order={order}
                            onSort={changeSort}
                          />
                        ))}
                        <th>Owner / Source</th>
                        <th>In Use</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((item) => (
                        <tr key={item.artifact_id}>
                          <td>
                            <input
                              aria-label={'Select artifact ' + item.owner_id}
                              type="checkbox"
                              checked={selection.has(item.artifact_id)}
                              onChange={() => toggle(item)}
                            />
                          </td>
                          <td>{item.artifact_type}</td>
                          <td>
                            <strong>{item.owner_id}</strong>
                            <div className="mono path-cell">{item.canonical_path}</div>
                          </td>
                          <td>{item.created_utc}</td>
                          <td>{formatBytes(item.size_bytes)}</td>
                          <td>{item.status}</td>
                          <td>{item.storage}</td>
                          <td>{item.retention_class}</td>
                          <td>
                            {item.owner_type}
                            {item.source_id && <div>{item.source_type}: {item.source_id}</div>}
                          </td>
                          <td>{item.in_use ? 'YES' : 'NO'}</td>
                          <td>
                            <div className="row-actions">
                              <button type="button" onClick={() => openTrace(item)}>Trace</button>
                              {item.cleanable && <button type="button" onClick={() => prepare('clean', [item.artifact_id])}>Clean</button>}
                              {item.deletable && <button type="button" onClick={() => prepare('delete', [item.artifact_id])}>Delete</button>}
                            </div>
                            {item.dependencies.length > 0 && <div className="error-text">{item.dependencies.join(', ')}</div>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                </table>
              </div>
            )}
            <Pagination
              page={data.page}
              pages={data.pages}
              pageSize={data.page_size}
              total={data.total}
              onPage={setPage}
              onPageSize={(size) => { setPageSize(size); setPage(1) }}
            />
          </section>
        </>
      )}

      {preflight && pendingAction && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal" role="dialog" aria-modal="true" aria-label="Artifact action preflight">
            <h2>{pendingAction === 'clean' ? 'Clean Selected Runtime' : 'Delete Selected'}</h2>
            <dl className="facts compact">
              <div><dt>Selected</dt><dd>{preflight.selected}</dd></div>
              <div><dt>Deletable</dt><dd>{preflight.deletable}</dd></div>
              <div><dt>Cleanable</dt><dd>{preflight.cleanable}</dd></div>
              <div><dt>Blocked</dt><dd>{preflight.blocked}</dd></div>
              <div><dt>Project bytes</dt><dd>{formatBytes(preflight.project_bytes)}</dd></div>
              <div><dt>Runtime bytes</dt><dd>{formatBytes(preflight.runtime_bytes)}</dd></div>
            </dl>
            {preflight.items.some((item) => item.blocked_reason) && (
              <ul>
                {preflight.items.filter((item) => item.blocked_reason).map((item) => (
                  <li key={item.artifact_id}>{item.owner_id}: {item.blocked_reason}</li>
                ))}
              </ul>
            )}
            <div className="actions">
              <button type="button" onClick={() => { setPreflight(null); setPendingAction(null); setPendingIds([]) }}>Cancel</button>
              <button type="button" disabled={preflight.blocked > 0} onClick={executePending}>
                CONFIRM {pendingAction.toUpperCase()}
              </button>
            </div>
          </div>
        </div>
      )}

      {globalPreflight && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal" role="dialog" aria-modal="true" aria-label="Clean generated data preflight">
            <h2>Clean Generated Data</h2>
            <dl className="facts compact">
              <div><dt>Status</dt><dd>{globalPreflight.status}</dd></div>
              <div><dt>Generated objects</dt><dd>{globalPreflight.selected}</dd></div>
              <div><dt>Project bytes</dt><dd>{formatBytes(globalPreflight.project_bytes)}</dd></div>
              <div><dt>Runtime bytes</dt><dd>{formatBytes(globalPreflight.runtime_bytes)}</dd></div>
            </dl>
            {globalPreflight.blockers?.length > 0 && (
              <p className="error">Blocked: {globalPreflight.blockers.join(', ')}</p>
            )}
            <div className="actions">
              <button type="button" onClick={() => setGlobalPreflight(null)}>Cancel</button>
              <button type="button" disabled={globalPreflight.status !== 'READY'} onClick={executeGlobalCleanup}>CONFIRM CLEAN GENERATED DATA</button>
            </div>
          </div>
        </div>
      )}

      {trace && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal trace-modal" role="dialog" aria-modal="true" aria-label="Artifact lineage">
            <h2>Artifact lineage</h2>
            <pre className="trace-json">{JSON.stringify(trace, null, 2)}</pre>
            <div className="actions"><button type="button" onClick={() => setTrace(null)}>Close</button></div>
          </div>
        </div>
      )}
    </div>
  )
}
