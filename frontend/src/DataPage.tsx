import { useCallback, useEffect, useState } from 'react'

type Coverage = Record<string, { rows?: number | null }>

type VerifiedSource = {
  status: string
  source_id?: string | null
  broker?: string | null
  feed?: string | null
  source_timezone?: string | null
  main_symbol?: string | null
  relative_symbol?: string | null
  data_start_utc?: string | null
  data_end_utc?: string | null
  row_coverage?: Coverage | null
}

type DataOwnerView = {
  state: string
  next_step: string
  sample_requirement: { value: number | null; source: string }
  execution_sample_requirement: { value: number | null; source: string }
  source_status: string
  dataset_status: string
  feature_readiness: string
  label_readiness: string
  leakage_status: string
  data_quality_status: string
  protected_data_state: string
  integrity_status: string
  physical_integrity_state: string
  physical_integrity: null | {
    chronology: string
    duplicates: string
    missing_source_data: string
    multi_timeframe_alignment: string
    relative_symbol_alignment: string
    feature_completeness: string
  }
  row_identity: string
  boundary_safety: string
  protected_outcomes: string
  timeframes: string[]
}

type DependencyAuthority = {
  label_dependency_main_bars?: number | null
  full_base_dependency_main_bars?: number | null
  minimum_legal_purge_main_bars?: number | null
  minimum_legal_embargo_main_bars?: number | null
}

type DiscoverySupervision = {
  physical_rows?: number | null
  boundary_safe_physical_rows?: number | null
  boundary_excluded_from_supervision_rows?: number | null
  supervised_rows?: number | null
  context_only_rows?: number | null
}

type ProtectedWindows = {
  discovery?: { from?: string | null; to?: string | null }
  locked_oos?: { from?: string | null; to?: string | null }
  fresh_forward?: { from?: string | null; to?: string | null }
}

type DataPreflight = {
  status: string
  research_id: string | null
  parent_strategy_id: string | null
  date_window: null | { start?: string | null; end?: string | null }
  class_distribution: null | {
    sell?: number | null
    skip?: number | null
    buy?: number | null
  }
  verified_source: VerifiedSource
  existing_run: null | { state: string }
  dependency_authority?: DependencyAuthority | null
  discovery_supervision?: DiscoverySupervision | null
  protected_windows?: ProtectedWindows | null
  owner_view: DataOwnerView
}

type SourceOverview = {
  source: VerifiedSource
  source_count: number
}

function utcBoundary(value: string) {
  const token = value.trim()
  if (!token) return ''
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(token)) return token + ':00+00:00'
  if (/^\d{4}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(token)) return token + '+00:00'
  return token
}

function errorMessage(payload: unknown, fallback: string) {
  if (!payload || typeof payload !== 'object' || !('detail' in payload)) return fallback
  const detail = String((payload as { detail?: unknown }).detail ?? '')
  return detail && !/^[A-Z0-9_:.-]+$/.test(detail) ? detail : fallback
}

function coverageLabel(key: string) {
  const [side, role] = key.split(':')
  const source = side === 'relative' ? 'Relative symbol' : 'Main symbol'
  const roleLabel: Record<string, string> = {
    main: 'trade timeframe',
    ctx_a: 'context A',
    ctx_b: 'context B',
    ctx_c: 'context C',
  }
  return source + ' · ' + (roleLabel[role] ?? 'context')
}

function coverageRows(source: VerifiedSource) {
  const entries = Object.entries(source.row_coverage ?? {})
  if (!entries.length) return null
  return entries.map(([key, item]) => ({
    label: coverageLabel(key),
    rows: Number(item.rows ?? 0),
  }))
}

function windowText(value?: { from?: string | null; to?: string | null }) {
  return value?.from && value?.to ? value.from + ' → ' + value.to : null
}

function bars(value?: number | null) {
  return value == null ? null : String(value) + ' main bars'
}

export default function DataPage({ embedded = false }: { embedded?: boolean }) {
  const [data, setData] = useState<DataPreflight | null>(null)
  const [source, setSource] = useState<SourceOverview | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [sourceFrom, setSourceFrom] = useState('')
  const [sourceTo, setSourceTo] = useState('')
  const [partitions, setPartitions] = useState({
    discovery_from: '',
    discovery_to: '',
    locked_oos_from: '',
    locked_oos_to: '',
    fresh_forward_from: '',
    fresh_forward_to: '',
  })

  const load = useCallback(async () => {
    const [dataResponse, sourceResponse] = await Promise.all([
      fetch('/api/research/r01/preflight'),
      fetch('/api/research/r01/source'),
    ])
    const dataBody = await dataResponse.json()
    const sourceBody = await sourceResponse.json()
    if (!dataResponse.ok) throw new Error(errorMessage(dataBody, 'Data validation is unavailable'))
    if (!sourceResponse.ok) throw new Error(errorMessage(sourceBody, 'Research data source is unavailable'))
    return {
      data: dataBody as DataPreflight,
      source: sourceBody as SourceOverview,
    }
  }, [])

  useEffect(() => {
    let active = true
    load().then((loaded) => {
      if (!active) return
      setData(loaded.data)
      setSource(loaded.source)
    }).catch((reason: Error) => active && setError(reason.message))
    return () => { active = false }
  }, [load])

  async function refresh() {
    const loaded = await load()
    setData(loaded.data)
    setSource(loaded.source)
  }

  async function prepareSource() {
    if (!data || !hasResearchAuthority || !sourceFrom || !sourceTo || busy) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const response = await fetch('/api/research/r01/source/prepare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          research_id: data.research_id,
          expected_parent_strategy_id: data.parent_strategy_id,
          owner_confirmation: 'OWNER_EXPLICIT_R01_SOURCE_PREPARE',
          from_date: sourceFrom,
          to_date: sourceTo,
          confirmed: true,
        }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(errorMessage(payload, 'Research data source verification failed'))
      setMessage('Research data source verified.')
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function startDataValidation() {
    if (!data || !hasResearchAuthority || busy) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const response = await fetch('/api/research/r01/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          research_id: data.research_id,
          expected_parent_strategy_id: data.parent_strategy_id,
          owner_confirmation: 'OWNER_EXPLICIT_R01_START',
          source_id: data.verified_source.source_id,
          discovery_from: utcBoundary(partitions.discovery_from),
          discovery_to: utcBoundary(partitions.discovery_to),
          locked_oos_from: utcBoundary(partitions.locked_oos_from),
          locked_oos_to: utcBoundary(partitions.locked_oos_to),
          fresh_forward_from: utcBoundary(partitions.fresh_forward_from),
          fresh_forward_to: utcBoundary(partitions.fresh_forward_to),
          confirmed: true,
        }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(errorMessage(payload, 'Data validation failed'))
      setMessage('Data validation is ready for Owner review.')
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const owner = data?.owner_view
  const hasResearchAuthority = Boolean(data?.research_id && data.parent_strategy_id)
  const verified = data?.verified_source ?? source?.source
  const rows = verified ? coverageRows(verified) : null
  const completePartitions = Object.values(partitions).every((value) => value.trim())
  const canStart = Boolean(
    data
    && hasResearchAuthority
    && !data.existing_run
    && verified?.status === 'VERIFIED'
    && verified.source_id
    && completePartitions,
  )

  return (
    <>
      {embedded ? (
        <div className="research-view-head">
          <div>
            <p className="eyebrow">Research data</p>
            <h2>Data</h2>
            <p className="subtle">Source provenance, physical integrity, scientific readiness and protected windows.</p>
          </div>
          {owner && <span className="milestone">{owner.state}</span>}
        </div>
      ) : (
        <header className="page-head">
          <div>
            <p className="eyebrow">MAX · Research data</p>
            <h1>Data</h1>
          </div>
          {owner && <span className="milestone">{owner.state}</span>}
        </header>
      )}

      {error && <p role="alert" className="error">{error}</p>}
      {message && <p role="status" className="notice">{message}</p>}
      {!data && !error && <p role="status" className="loading">Loading Data authority…</p>}

      {data && owner && (
        <>
          {!hasResearchAuthority ? (
            <section className="authority-panel" aria-labelledby="empty-research-dataset">
              <h2 id="empty-research-dataset">No Research dataset yet</h2>
              <p>{owner.next_step}</p>
            </section>
          ) : (
            <>
          <section className="authority-panel" aria-labelledby="dataset-identity">
            <div className="section-head">
              <div><h2 id="dataset-identity">Dataset identity</h2><p>Canonical source and Strategy-parent authority.</p></div>
            </div>
            <dl className="facts compact">
              <div><dt>Parent Strategy</dt><dd>Frozen Strategy Champion</dd></div>
              <div><dt>Dataset state</dt><dd className="value">{owner.dataset_status}</dd></div>
              <div><dt>Source state</dt><dd>{owner.source_status}</dd></div>
              <div><dt>Broker / feed</dt><dd>{verified?.broker && verified.feed ? verified.broker + ' / ' + verified.feed : 'No verified broker source yet'}</dd></div>
              <div><dt>Main / relative symbol</dt><dd>{verified?.main_symbol && verified.relative_symbol ? verified.main_symbol + ' / ' + verified.relative_symbol : 'Available after source verification'}</dd></div>
              <div><dt>Timeframes</dt><dd>{owner.timeframes.length ? owner.timeframes.join(' / ') : 'Available after Strategy geometry is bound'}</dd></div>
              <div><dt>Source coverage</dt><dd>{verified?.data_start_utc && verified.data_end_utc ? verified.data_start_utc + ' → ' + verified.data_end_utc : 'Available after source verification'}</dd></div>
              <div><dt>Dataset coverage</dt><dd>{data.date_window?.start && data.date_window.end ? data.date_window.start + ' → ' + data.date_window.end : 'Available after validated dataset creation'}</dd></div>
            </dl>

            {rows && (
              <div className="table-wrap compact-table">
                <table>
                  <thead><tr><th>Physical source</th><th>Rows</th></tr></thead>
                  <tbody>{rows.map((item) => <tr key={item.label}><td>{item.label}</td><td>{item.rows}</td></tr>)}</tbody>
                </table>
              </div>
            )}

            {!data.existing_run && (
              <>
                <div className="form-grid data-source-form">
                  <label>Source from date (UTC)<input aria-label="Source from date UTC" type="date" value={sourceFrom} onChange={(e) => setSourceFrom(e.target.value)} disabled={busy} /></label>
                  <label>Source to date (UTC)<input aria-label="Source to date UTC" type="date" value={sourceTo} onChange={(e) => setSourceTo(e.target.value)} disabled={busy} /></label>
                </div>
                <div className="actions">
                  <button type="button" onClick={prepareSource} disabled={!sourceFrom || !sourceTo || busy}>
                    {busy ? 'Verifying…' : 'Verify Research data source'}
                  </button>
                </div>
              </>
            )}
          </section>

          <section className="authority-panel" aria-labelledby="physical-integrity">
            <div className="section-head">
              <div><h2 id="physical-integrity">Physical data integrity</h2><p>{owner.physical_integrity_state}</p></div>
            </div>
            {owner.physical_integrity ? (
              <dl className="facts compact">
                <div><dt>Chronology</dt><dd>{owner.physical_integrity.chronology}</dd></div>
                <div><dt>Duplicate timestamps</dt><dd>{owner.physical_integrity.duplicates}</dd></div>
                <div><dt>Missing source data</dt><dd>{owner.physical_integrity.missing_source_data}</dd></div>
                <div><dt>Multi-timeframe alignment</dt><dd>{owner.physical_integrity.multi_timeframe_alignment}</dd></div>
                <div><dt>Relative-symbol alignment</dt><dd>{owner.physical_integrity.relative_symbol_alignment}</dd></div>
                <div><dt>Feature completeness</dt><dd>{owner.physical_integrity.feature_completeness}</dd></div>
                <div><dt>Row identity authority</dt><dd>{owner.row_identity}</dd></div>
              </dl>
            ) : <p className="subtle">{owner.row_identity} remain the chronology authority when validation is executed.</p>}
          </section>

          <section className="authority-panel" aria-labelledby="scientific-readiness">
            <div className="section-head">
              <div><h2 id="scientific-readiness">Scientific readiness</h2><p>Deterministic validation remains fail closed.</p></div>
            </div>
            <div className="readiness-grid">
              <div><span>Data Quality</span><strong>{owner.data_quality_status}</strong></div>
              <div><span>Feature readiness (CP32)</span><strong>{owner.feature_readiness}</strong></div>
              <div><span>Label readiness</span><strong>{owner.label_readiness}</strong></div>
              <div><span>Leakage validation</span><strong>{owner.leakage_status}</strong></div>
            </div>
            <dl className="facts compact">
              <div>
                <dt>Current Research requirement</dt>
                <dd>
                  {owner.sample_requirement.value == null
                    ? 'Configuration required'
                    : owner.sample_requirement.value + ' trades/month'}
                </dd>
              </div>
              <div><dt>Requirement source</dt><dd>{owner.sample_requirement.source}</dd></div>
              <div>
                <dt>Execution use</dt>
                <dd>
                  {owner.execution_sample_requirement.value == null
                    ? owner.execution_sample_requirement.source
                    : owner.execution_sample_requirement.source
                      + ' · '
                      + owner.execution_sample_requirement.value
                      + ' trades/month'}
                </dd>
              </div>
              <div><dt>Next legal step</dt><dd>{owner.next_step}</dd></div>
            </dl>
          </section>

          <section className="authority-panel" aria-labelledby="research-windows">
            <div className="section-head">
              <div><h2 id="research-windows">Research &amp; protected windows</h2><p>{owner.protected_outcomes}</p></div>
              <span className="status-pill">{owner.protected_data_state}</span>
            </div>

            {data.protected_windows ? (
              <div className="window-grid">
                <div><span>Discovery</span><strong>{windowText(data.protected_windows.discovery)}</strong></div>
                <div className="protected-window"><span>Locked OOS</span><strong>{windowText(data.protected_windows.locked_oos)}</strong></div>
                <div className="protected-window"><span>Fresh / Forward</span><strong>{windowText(data.protected_windows.fresh_forward)}</strong></div>
              </div>
            ) : !data.existing_run ? (
              <div className="window-editor">
                <div className="window-group">
                  <h3>Discovery</h3>
                  <p>Research-visible data.</p>
                  <label>From UTC<input aria-label="Discovery from UTC" type="datetime-local" value={partitions.discovery_from} onChange={(e) => setPartitions((v) => ({ ...v, discovery_from: e.target.value }))} /></label>
                  <label>To UTC<input aria-label="Discovery to UTC" type="datetime-local" value={partitions.discovery_to} onChange={(e) => setPartitions((v) => ({ ...v, discovery_to: e.target.value }))} /></label>
                </div>
                <div className="window-group protected-window">
                  <h3>Locked OOS</h3>
                  <p>Protected validation data.</p>
                  <label>From UTC<input aria-label="Locked OOS from UTC" type="datetime-local" value={partitions.locked_oos_from} onChange={(e) => setPartitions((v) => ({ ...v, locked_oos_from: e.target.value }))} /></label>
                  <label>To UTC<input aria-label="Locked OOS to UTC" type="datetime-local" value={partitions.locked_oos_to} onChange={(e) => setPartitions((v) => ({ ...v, locked_oos_to: e.target.value }))} /></label>
                </div>
                <div className="window-group protected-window">
                  <h3>Fresh / Forward</h3>
                  <p>Fresh protected validation data.</p>
                  <label>From UTC<input aria-label="Fresh forward from UTC" type="datetime-local" value={partitions.fresh_forward_from} onChange={(e) => setPartitions((v) => ({ ...v, fresh_forward_from: e.target.value }))} /></label>
                  <label>To UTC<input aria-label="Fresh forward to UTC" type="datetime-local" value={partitions.fresh_forward_to} onChange={(e) => setPartitions((v) => ({ ...v, fresh_forward_to: e.target.value }))} /></label>
                </div>
              </div>
            ) : null}
          </section>

          {data.dependency_authority && (
            <section className="authority-panel" aria-labelledby="dependency-authority">
              <div className="section-head">
                <div><h2 id="dependency-authority">Target dependency &amp; purge authority</h2><p>{owner.boundary_safety}</p></div>
              </div>
              <dl className="facts compact">
                <div><dt>Target horizon</dt><dd>{bars(data.dependency_authority.label_dependency_main_bars)}</dd></div>
                <div><dt>Full dependency horizon</dt><dd>{bars(data.dependency_authority.full_base_dependency_main_bars)}</dd></div>
                <div><dt>Minimum purge</dt><dd>{bars(data.dependency_authority.minimum_legal_purge_main_bars)}</dd></div>
                <div><dt>Minimum embargo</dt><dd>{bars(data.dependency_authority.minimum_legal_embargo_main_bars)}</dd></div>
              </dl>
            </section>
          )}

          {data.discovery_supervision && (
            <section className="authority-panel" aria-labelledby="sample-preview">
              <div className="section-head">
                <div><h2 id="sample-preview">Discovery sample readiness</h2><p>Supervised eligibility is separate from physical context availability.</p></div>
              </div>
              <dl className="facts compact">
                <div><dt>Physical Discovery rows</dt><dd>{data.discovery_supervision.physical_rows ?? 0}</dd></div>
                <div><dt>Boundary-safe rows</dt><dd>{data.discovery_supervision.boundary_safe_physical_rows ?? 0}</dd></div>
                <div><dt>Supervised eligible rows</dt><dd>{data.discovery_supervision.supervised_rows ?? 0}</dd></div>
                <div><dt>Context-only rows</dt><dd>{data.discovery_supervision.context_only_rows ?? 0}</dd></div>
                <div><dt>Boundary-excluded rows</dt><dd>{data.discovery_supervision.boundary_excluded_from_supervision_rows ?? 0}</dd></div>
              </dl>
            </section>
          )}

          {data.class_distribution && (
            <section className="authority-panel" aria-labelledby="discovery-label-balance">
              <div className="section-head">
                <div><h2 id="discovery-label-balance">Discovery label balance</h2><p>Only research-visible Discovery outcomes contribute here.</p></div>
              </div>
              <div className="discovery-distribution">
                <span>Discovery only</span>
                <strong>SELL {data.class_distribution.sell ?? 0} · SKIP {data.class_distribution.skip ?? 0} · BUY {data.class_distribution.buy ?? 0}</strong>
                <small>Protected outcomes are excluded.</small>
              </div>
            </section>
          )}

          {!data.existing_run && (
            <section className="authority-panel data-start" aria-labelledby="data-owner-action">
              <div className="section-head">
                <div><h2 id="data-owner-action">Owner action</h2><p>{owner.next_step}</p></div>
              </div>
              <div className="actions">
                <button type="button" onClick={startDataValidation} disabled={!canStart || busy}>
                  {busy ? 'Preparing…' : 'Prepare Data validation'}
                </button>
              </div>
            </section>
          )}
            </>
          )}
        </>
      )}
    </>
  )
}
