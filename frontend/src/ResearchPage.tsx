import { useCallback, useEffect, useState } from 'react'
import DataPage from './DataPage'

type OwnerLifecycleItem = {
  key: string
  label: string
  availability: string
  description: string
  scientific_rule: string
  required_authority: string
}

type OwnerResearchView = {
  current_stage: string
  current_state: string
  next_step: string
  sample_requirement: {
    value: number | null
    source: string
  }
  execution_sample_requirement: {
    value: number | null
    source: string
  }
  capacity_authority: string
  owner_decisions: Array<{ label: string; status: string }>
  lifecycle: OwnerLifecycleItem[]
}

type Preflight = {
  status: string
  current_champion: { strategy_id: string; status: string } | null
  parent_authority_sha256: string | null
}

type ResearchAuthority = {
  parent_strategy_id: string
  hardware_summary: {
    cpu?: string
    physical_cores?: number | null
    logical_threads?: number | null
    ram_total_bytes?: number | null
    gpu?: string | null
  }
}

type ResearchCurrent = {
  status: string
  current: ResearchAuthority | null
  owner_view: OwnerResearchView
}

type SampleConfiguration = {
  configured: boolean
  h1_minimum_trades_per_month: number | null
  updated_utc: string | null
}

function ownerErrorMessage(value: unknown, fallback: string) {
  const detail = typeof value === 'string' ? value.trim() : ''
  if (!detail) return fallback
  if (/^[A-Z0-9_:.-]+$/.test(detail) || /[A-Z0-9]+_[A-Z0-9_]+/.test(detail)) {
    return fallback
  }
  return detail
}

function formatBytes(value?: number | null) {
  if (!value) return 'Unavailable'
  return (value / 1024 ** 3).toFixed(1) + ' GiB'
}

function LifecycleInspector({ item }: { item: OwnerLifecycleItem }) {
  return (
    <section className="research-stage-view" aria-labelledby={'research-view-' + item.key}>
      <div className="research-stage-heading">
        <div>
          <p className="eyebrow">Research workflow</p>
          <h2 id={'research-view-' + item.key}>{item.label}</h2>
        </div>
        <span className="research-lock-state">{item.availability}</span>
      </div>
      <p className="subtle">{item.description}</p>
      <dl className="facts compact">
        <div><dt>Scientific rule</dt><dd>{item.scientific_rule}</dd></div>
        <div><dt>Required authority</dt><dd>{item.required_authority}</dd></div>
      </dl>
    </section>
  )
}

export default function ResearchPage() {
  const [view, setView] = useState('overview')
  const [preflight, setPreflight] = useState<Preflight | null>(null)
  const [current, setCurrent] = useState<ResearchCurrent | null>(null)
  const [h1Minimum, setH1Minimum] = useState('')
  const [sampleConfig, setSampleConfig] = useState<SampleConfiguration | null>(null)
  const [savingSample, setSavingSample] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState('')

  const load = useCallback(async () => {
    const [preflightResponse, currentResponse, sampleResponse] = await Promise.all([
      fetch('/api/research/r00/preflight'),
      fetch('/api/research/current'),
      fetch('/api/research/sample-config'),
    ])
    const preflightBody = await preflightResponse.json()
    const currentBody = await currentResponse.json()
    const sampleBody = await sampleResponse.json()
    if (!preflightResponse.ok) throw new Error(ownerErrorMessage(preflightBody.detail, 'Research preflight failed'))
    if (!currentResponse.ok) throw new Error(ownerErrorMessage(currentBody.detail, 'Research authority is unavailable'))
    if (!sampleResponse.ok) throw new Error(ownerErrorMessage(sampleBody.detail, 'Research sample configuration is unavailable'))
    return {
      preflight: preflightBody as Preflight,
      current: currentBody as ResearchCurrent,
      sampleConfig: sampleBody as SampleConfiguration,
    }
  }, [])

  useEffect(() => {
    let active = true
    load().then((loaded) => {
      if (!active) return
      setPreflight(loaded.preflight)
      setCurrent(loaded.current)
      setSampleConfig(loaded.sampleConfig)
      setH1Minimum(
        loaded.sampleConfig.h1_minimum_trades_per_month == null
          ? ''
          : String(loaded.sampleConfig.h1_minimum_trades_per_month),
      )
    }).catch((reason: Error) => active && setError(reason.message))
    return () => { active = false }
  }, [load])

  async function saveSampleRequirement() {
    const value = Number(h1Minimum)
    if (
      savingSample
      || !Number.isInteger(value)
      || value <= 0
    ) return
    setSavingSample(true)
    setError('')
    setResult('')
    try {
      const response = await fetch('/api/research/sample-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          h1_minimum_trades_per_month: value,
        }),
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(
          ownerErrorMessage(
            payload.detail,
            'Research sample configuration could not be saved',
          ),
        )
      }
      const refreshed = await load()
      setPreflight(refreshed.preflight)
      setCurrent(refreshed.current)
      setSampleConfig(refreshed.sampleConfig)
      setH1Minimum(String(refreshed.sampleConfig.h1_minimum_trades_per_month ?? ''))
      setResult('Current Research sample requirement saved.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSavingSample(false)
    }
  }

  async function startFoundation() {
    if (!preflight || !preflight.current_champion || !preflight.parent_authority_sha256 || starting) return
    setStarting(true)
    setError('')
    setResult('')
    try {
      const response = await fetch('/api/research/r00/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expected_parent_strategy_id: preflight.current_champion.strategy_id,
          expected_parent_authority_sha256: preflight.parent_authority_sha256,
          owner_confirmation: 'OWNER_EXPLICIT_R00_START',
          cumulative_strategy_e2e_authority: 'OWNER_EXPLICIT_STRATEGY_E2E_SATISFIED',
          h1_minimum_trades_per_month:
            sampleConfig?.h1_minimum_trades_per_month ?? null,
          confirmed: true,
        }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(ownerErrorMessage(payload.detail, 'Research could not be initialized'))
      setResult('Research is ready for Owner review.')
      const refreshed = await load()
      setPreflight(refreshed.preflight)
      setCurrent(refreshed.current)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setStarting(false)
    }
  }

  const owner = current?.owner_view
  const authority = current?.current
  const lifecycle = owner?.lifecycle ?? []
  const selected = lifecycle.find((item) => item.key === view) ?? lifecycle[0]
  const canStart = Boolean(
    preflight
    && !authority
    && preflight.status === 'READY_TO_START'
    && preflight.current_champion?.status === 'VERIFIED'
    && Boolean(preflight.parent_authority_sha256)
    && sampleConfig?.configured
    && sampleConfig.h1_minimum_trades_per_month != null
    && h1Minimum.trim()
    && Number.isInteger(Number(h1Minimum))
    && Number(h1Minimum) === sampleConfig.h1_minimum_trades_per_month,
  )
  const sampleDirty = Boolean(
    sampleConfig
    && h1Minimum.trim()
    && Number.isInteger(Number(h1Minimum))
    && Number(h1Minimum) > 0
    && Number(h1Minimum) !== sampleConfig.h1_minimum_trades_per_month,
  )

  return (
    <>
      <header className="page-head">
        <div>
          <p className="eyebrow">MAX · Research</p>
          <h1>Research</h1>
          <p className="page-subtitle">Lifecycle authority, frozen Strategy parent and next legal Research action.</p>
        </div>
        {owner && <span className="milestone">{owner.current_state}</span>}
      </header>

      {error && <p role="alert" className="error">{error}</p>}
      {result && <p role="status" className="notice">{result}</p>}
      {!current && !error && <p role="status" className="loading">Loading Research…</p>}

      {owner && (
        <nav className="nav research-nav" aria-label="Research navigation">
          {owner.lifecycle.map((item) => (
            <button
              key={item.key}
              type="button"
              className={view === item.key ? 'nav-active' : ''}
              onClick={() => setView(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      )}

      {owner && (
        <section aria-labelledby="research-sample-authority">
          <h2 id="research-sample-authority">Research sample requirement</h2>
          <div className="form-grid research-owner-input">
            <label>
              Current Research requirement
              <input
                aria-label="Research H1 minimum trades per month"
                type="number"
                min="1"
                step="1"
                value={h1Minimum}
                onChange={(event) => setH1Minimum(event.target.value)}
                disabled={savingSample}
              />
            </label>
          </div>
          <div className="actions">
            <button
              type="button"
              onClick={saveSampleRequirement}
              disabled={
                savingSample
                || !h1Minimum.trim()
                || !Number.isInteger(Number(h1Minimum))
                || Number(h1Minimum) <= 0
                || (
                  sampleConfig?.configured
                  && Number(h1Minimum)
                    === sampleConfig.h1_minimum_trades_per_month
                )
              }
            >
              {savingSample ? 'Saving…' : 'Save requirement'}
            </button>
          </div>
          <dl className="facts compact">
            <div>
              <dt>Saved configuration</dt>
              <dd>
                {owner.sample_requirement.value == null
                  ? 'Configuration required'
                  : owner.sample_requirement.value + ' trades/month'}
              </dd>
            </div>
            <div><dt>Authority source</dt><dd>{owner.sample_requirement.source}</dd></div>
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
          </dl>
          {sampleDirty && (
            <p className="subtle">
              Unsaved change. Running or completed Research evidence is not modified.
            </p>
          )}
        </section>
      )}

      {owner && selected?.key === 'data' && <DataPage embedded />}

      {owner && selected && selected.key !== 'overview' && selected.key !== 'data' && (
        <LifecycleInspector item={selected} />
      )}

      {owner && selected?.key === 'overview' && preflight && (
        <>
          <section aria-labelledby="research-authority">
            <h2 id="research-authority">Research authority</h2>
            {!authority && <p role="status">Research not initialized</p>}
            <dl className="facts compact">
              <div><dt>Current Strategy Champion</dt><dd>{!preflight.current_champion ? 'No current Strategy Champion' : preflight.current_champion.status === 'VERIFIED' ? 'Selected and verified' : 'Review required'}</dd></div>
              <div><dt>Frozen Research Parent</dt><dd>{authority ? 'Frozen current Strategy Champion' : 'Not frozen'}</dd></div>
              <div><dt>Current stage</dt><dd className="value">{owner.current_stage}</dd></div>
              <div><dt>Current state</dt><dd>{owner.current_state}</dd></div>
              <div><dt>Next legal step</dt><dd>{owner.next_step}</dd></div>
            </dl>
          </section>

          <section aria-labelledby="research-hardware">
            <h2 id="research-hardware">Hardware &amp; capacity</h2>
            {authority ? (
              <dl className="facts compact">
                <div><dt>CPU</dt><dd>{authority.hardware_summary.cpu ?? 'Unavailable'}</dd></div>
                <div><dt>CPU topology</dt><dd>{authority.hardware_summary.physical_cores ?? 'Unavailable'} physical · {authority.hardware_summary.logical_threads ?? 'Unavailable'} logical</dd></div>
                <div><dt>RAM</dt><dd>{formatBytes(authority.hardware_summary.ram_total_bytes)}</dd></div>
                <div><dt>GPU</dt><dd>{authority.hardware_summary.gpu ?? 'Unavailable'}</dd></div>
                <div><dt>Capacity authority</dt><dd>{owner.capacity_authority}</dd></div>
              </dl>
            ) : <p className="subtle">Captured and frozen when Research is initialized.</p>}
          </section>

          <section aria-labelledby="research-decisions">
            <h2 id="research-decisions">Owner decisions</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Decision</th><th>Status</th></tr></thead>
                <tbody>
                  {owner.owner_decisions.map((item) => (
                    <tr key={item.label}><td>{item.label}</td><td>{item.status}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>

            {!authority && (
              <>
                <p className="subtle">
                  Initialization records the saved sample requirement in immutable
                  execution evidence. Later configuration edits do not rewrite it.
                </p>
                <div className="actions">
                  <button type="button" onClick={startFoundation} disabled={!canStart || starting}>
                    {starting ? 'Initializing…' : 'Initialize Research'}
                  </button>
                </div>
              </>
            )}
          </section>
        </>
      )}
    </>
  )
}
