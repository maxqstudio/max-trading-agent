import { useCallback, useEffect, useMemo, useState } from 'react'
import { ActionButton, ActionProgress } from './ActionControls'
import {
  createOnnxSnapshot,
  fetchOnnxDataWorkspace,
  preflightOnnxData,
  resolveOnnxIdenticalDuplicates,
  validateOnnxWindows,
  type OnnxDataPreflight,
  type OnnxDataSnapshot,
  type OnnxDataWorkspace,
} from './onnxDataApi'

type WindowName = 'DISCOVERY' | 'TOURNAMENT' | 'FORWARD'
type WindowDraft = Record<WindowName, { from: string; to: string }>

const EMPTY_WINDOWS: WindowDraft = {
  DISCOVERY: { from: '', to: '' },
  TOURNAMENT: { from: '', to: '' },
  FORWARD: { from: '', to: '' },
}

function timestampValue(value: string) {
  return value.length === 16 ? `${value}:00` : value
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'ONNX data operation failed; the backend did not return trusted status.'
}

function DataIssues({ snapshot }: { snapshot: OnnxDataSnapshot }) {
  return (
    <section className="onnx-card onnx-data-issues" aria-labelledby="onnx-data-quality-title">
      <h2 id="onnx-data-quality-title">Data-quality audit</h2>
      <p>DQ status: <strong>{snapshot.dq_status}</strong></p>
      <dl className="onnx-facts">
        <div className="onnx-field"><dt>Rows</dt><dd>{snapshot.row_count}</dd></div>
        <div className="onnx-field"><dt>Identity</dt><dd>{snapshot.symbol ?? 'Unavailable'} · {snapshot.timeframe ?? 'Unavailable'}</dd></div>
        <div className="onnx-field"><dt>Timestamp basis</dt><dd>{snapshot.timestamp_timezone}</dd></div>
        <div className="onnx-field"><dt>Observed timestamp discontinuity</dt><dd>{snapshot.timestamp_discontinuity_status}</dd></div>
        <div className="onnx-field"><dt>Broker reconciliation</dt><dd>{snapshot.broker_reconciliation_status}</dd></div>
        <div className="onnx-field"><dt>Identical duplicate rows</dt><dd>{snapshot.dq.identical_duplicate_rows}</dd></div>
        <div className="onnx-field"><dt>Conflicting duplicate identities</dt><dd>{snapshot.dq.conflicting_duplicate_identities}</dd></div>
      </dl>
      {snapshot.dq.issues.length === 0
        ? <p className="onnx-empty">No mandatory DQ blockers were reported for this immutable snapshot.</p>
        : (
          <ul className="onnx-data-issues-list">
            {snapshot.dq.issues.map((issue, index) => (
              <li key={`${issue.code}-${index}`}>
                <strong>{issue.code}</strong> — {issue.message} <span>({issue.severity})</span>
              </li>
            ))}
          </ul>
        )}
    </section>
  )
}

export default function OnnxDataIntake() {
  const [workspace, setWorkspace] = useState<OnnxDataWorkspace | null>(null)
  const [preflight, setPreflight] = useState<OnnxDataPreflight | null>(null)
  const [sourcePath, setSourcePath] = useState('')
  const [timezoneProvenance, setTimezoneProvenance] = useState('')
  const [confirmSnapshot, setConfirmSnapshot] = useState(false)
  const [confirmCorrection, setConfirmCorrection] = useState(false)
  const [windows, setWindows] = useState<WindowDraft>(EMPTY_WINDOWS)
  const [error, setError] = useState('')
  const [operationResult, setOperationResult] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)

  const snapshot = workspace?.latest_snapshot ?? null
  const firstBlocker = workspace?.first_blocker ?? preflight?.first_blocker ?? null
  const timezoneReady = timezoneProvenance.trim().length > 0
  const canCorrectDuplicates = Boolean(
    snapshot
    && snapshot.dq.identical_duplicate_rows > 0
    && snapshot.dq.conflicting_duplicate_identities === 0,
  )

  useEffect(() => {
    const controller = new AbortController()
    fetchOnnxDataWorkspace(controller.signal)
      .then((result) => {
        setWorkspace(result)
        setLoading(false)
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setWorkspace(null)
        setError(errorMessage(reason))
        setLoading(false)
      })
    return () => controller.abort()
  }, [retry])

  const refreshWorkspace = useCallback(async () => {
    const next = await fetchOnnxDataWorkspace()
    setWorkspace(next)
    setError('')
  }, [])

  const perform = useCallback(async (name: string, operation: () => Promise<unknown>) => {
    setBusy(true)
    setError('')
    setOperationResult('')
    try {
      const result = await operation()
      if (result && typeof result === 'object' && 'status' in result) {
        setOperationResult(`Backend result: ${String(result.status)}.`)
      } else {
        setOperationResult(`${name} completed with backend evidence.`)
      }
      await refreshWorkspace()
      return result
    } catch (reason: unknown) {
      setWorkspace(null)
      setError(errorMessage(reason))
      setOperationResult(`${name} was not confirmed by the backend.`)
      return null
    } finally {
      setBusy(false)
    }
  }, [refreshWorkspace])

  const runPreflight = async () => {
    setPreflight(null)
    setConfirmSnapshot(false)
    const payload = {
      ...(sourcePath.trim() ? { source_path: sourcePath.trim() } : {}),
      timezone_provenance: timezoneProvenance.trim(),
    }
    const result = await perform('Source preflight', async () => {
      const evidence = await preflightOnnxData(payload)
      setPreflight(evidence)
      return evidence
    })
    if (!result) setPreflight(null)
  }

  const createSnapshot = async () => {
    if (!preflight) return
    const evidence = preflight
    setPreflight(null)
    setConfirmSnapshot(false)
    await perform('Immutable snapshot', () => createOnnxSnapshot({
      ...(sourcePath.trim() ? { source_path: sourcePath.trim() } : {}),
      timezone_provenance: timezoneProvenance.trim(),
      expected_source_sha256: evidence.source_identity.sha256,
      confirmed: true,
    }))
  }

  const correctDuplicates = async () => {
    if (!snapshot) return
    setConfirmCorrection(false)
    await perform('Duplicate correction', () => resolveOnnxIdenticalDuplicates(snapshot))
  }

  const saveWindows = async () => {
    if (!snapshot) return
    const normalized = Object.fromEntries(
      Object.entries(windows).map(([name, range]) => [name, {
        from: timestampValue(range.from),
        to: timestampValue(range.to),
      }]),
    ) as WindowDraft
    await perform('Research-window validation', () => validateOnnxWindows({
      snapshot_id: snapshot.snapshot_id,
      snapshot_sha256: snapshot.sha256,
      timezone_provenance: timezoneProvenance.trim() || snapshot.timezone_provenance || '',
      windows: normalized,
    }))
  }

  const windowReady = useMemo(
    () => Object.values(windows).every((range) => range.from && range.to),
    [windows],
  )

  return (
    <div className="onnx-data-intake" aria-label="ONNX Data Intake">
      <header className="page-head onnx-page-head">
        <div>
          <p className="eyebrow">MAX · ONNX data authority</p>
          <h1>Data Intake</h1>
          <p className="onnx-intro">Inspect an approved MAX CP32 source, preserve exact bytes, review DQ, then validate the three research windows. These actions do not run scientific evaluation.</p>
        </div>
      </header>

      {loading && <p role="status" className="loading">Loading backend data-intake state…</p>}
      {!loading && error && (
        <div className="onnx-load-error" role="alert">
          <p>Data-intake state is unavailable; no cached or partial status is being shown.</p>
          <p>{error}</p>
          <button type="button" onClick={() => { setLoading(true); setError(''); setRetry((value) => value + 1) }}>Retry data-intake status</button>
        </div>
      )}

      {!loading && workspace && (
        <>
          <section className="onnx-card onnx-data-status" aria-label="Backend data-intake status">
            <div className="onnx-data-status-line">
              <div><span className="onnx-state-label">Backend state</span><strong>{workspace.status}</strong></div>
              <div><span className="onnx-state-label">Real data readiness</span><strong>{workspace.real_data_readiness}</strong></div>
              <div><span className="onnx-state-label">Scientific execution</span><strong>{workspace.scientific_execution}</strong></div>
            </div>
            {firstBlocker && <p className="onnx-reason"><strong>First blocker:</strong> {firstBlocker}</p>}
            {operationResult && <p role="status" className="onnx-action-result">{operationResult}</p>}
          </section>

          <section className="onnx-card" aria-labelledby="onnx-source-preflight-title">
            <h2 id="onnx-source-preflight-title">Source preflight</h2>
            <p className="onnx-reason">Default discovery checks only the canonical Max_MTF_Training.csv inside MetaTrader Common Files. An override must also remain under that approved directory. The live CSV is never edited.</p>
            <div className="form-grid onnx-data-form">
              <label htmlFor="onnx-source-path">Owner-selected source path override <span>(optional)</span>
                <input id="onnx-source-path" value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="Leave blank for canonical MAX filename" autoComplete="off" />
              </label>
              <label htmlFor="onnx-timezone-provenance">Timestamp timezone/source provenance <span>(required; no UTC assumption)</span>
                <input id="onnx-timezone-provenance" value={timezoneProvenance} onChange={(event) => setTimezoneProvenance(event.target.value)} placeholder="e.g. broker server time; UTC offset unknown" maxLength={160} />
              </label>
            </div>
            <div className="onnx-data-actions">
              <ActionButton type="button" onClick={runPreflight} disabled={busy || loading || !timezoneReady} blockedReason="Enter the source's broker/server timezone provenance before preflight.">
                <ActionProgress active={busy} idle="Read-only source preflight" pending="Checking lock, identity, schema and DQ…" />
              </ActionButton>
              <p>Preflight does not create a snapshot. It reads under the EA writer lock and returns a source hash for the next confirmation.</p>
            </div>
            {preflight && (
              <div className="onnx-data-result" aria-label="Source preflight result">
                <h3>Backend preflight: {preflight.status}</h3>
                <p>Source: <strong>{preflight.source_identity.filename}</strong></p>
                <p className="mono">SHA-256: {preflight.source_identity.sha256}</p>
                <p>Rows: {preflight.data_quality.row_count}; DQ: {preflight.data_quality.status}; timeframe: {preflight.data_quality.timeframe ?? 'unresolved'}.</p>
                {preflight.first_blocker && <p><strong>First blocker:</strong> {preflight.first_blocker}</p>}
                {preflight.data_quality.issues.length > 0 && (
                  <ul className="onnx-data-issues-list" aria-label="Preflight data-quality findings">
                    {preflight.data_quality.issues.map((issue, index) => (
                      <li key={`${issue.code}-${index}`}>
                        <strong>{issue.code}</strong> — {issue.message} <span>({issue.severity})</span>
                      </li>
                    ))}
                  </ul>
                )}
                {preflight.snapshot_permitted && (
                  <div className="onnx-confirmation">
                    <label>
                      <input type="checkbox" checked={confirmSnapshot} onChange={(event) => setConfirmSnapshot(event.target.checked)} />
                      I reviewed this source hash and authorize creating a private, immutable raw snapshot. DQ blockers remain blockers.
                    </label>
                    <ActionButton type="button" onClick={createSnapshot} disabled={!confirmSnapshot || busy} blockedReason="Review the exact source hash and confirm the snapshot operation.">
                      <ActionProgress active={busy} idle="Create immutable snapshot" pending="Copying and verifying exact source bytes…" />
                    </ActionButton>
                  </div>
                )}
              </div>
            )}
          </section>

          {snapshot && (
            <>
              <section className="onnx-card" aria-labelledby="onnx-snapshot-title">
                <h2 id="onnx-snapshot-title">Immutable snapshot</h2>
                <dl className="onnx-facts">
                  <div className="onnx-field"><dt>Snapshot ID</dt><dd className="mono">{snapshot.snapshot_id}</dd></div>
                  <div className="onnx-field"><dt>Dataset ID</dt><dd className="mono">{snapshot.dataset_id}</dd></div>
                  <div className="onnx-field"><dt>SHA-256</dt><dd className="mono">{snapshot.sha256}</dd></div>
                  <div className="onnx-field"><dt>Source contract</dt><dd>{snapshot.strategy_contract} · {snapshot.feature_contract}</dd></div>
                  <div className="onnx-field"><dt>Window coverage</dt><dd>{snapshot.timestamp_min ?? 'Unavailable'} → {snapshot.timestamp_max ?? 'Unavailable'}</dd></div>
                  <div className="onnx-field"><dt>Evidence class</dt><dd>{snapshot.evidence_class}</dd></div>
                </dl>
                {snapshot.parent_snapshot_id && <p>Derived from immutable parent <span className="mono">{snapshot.parent_snapshot_id}</span>; original remains retained.</p>}
              </section>

              <DataIssues snapshot={snapshot} />

              {canCorrectDuplicates && (
                <section className="onnx-card" aria-labelledby="onnx-duplicate-resolution-title">
                  <h2 id="onnx-duplicate-resolution-title">Identical duplicate resolution</h2>
                  <p>The original snapshot is the verified backup. Resolution writes a new derived snapshot and never edits the source file or original snapshot. Conflicting duplicates cannot use this operation.</p>
                  <label className="onnx-confirmation">
                    <input type="checkbox" checked={confirmCorrection} onChange={(event) => setConfirmCorrection(event.target.checked)} />
                    I explicitly authorize removing only byte-value-identical rows from this exact snapshot; its original SHA remains preserved.
                  </label>
                  <ActionButton type="button" onClick={correctDuplicates} disabled={!confirmCorrection || busy} blockedReason="Explicitly confirm correction of identical duplicates against the displayed snapshot hash.">
                    <ActionProgress active={busy} idle="Create corrected derived snapshot" pending="Verifying backup and publishing derived snapshot…" />
                  </ActionButton>
                </section>
              )}

              <section className="onnx-card" aria-labelledby="onnx-window-config-title">
                <h2 id="onnx-window-config-title">Three research windows</h2>
                <p>All six boundaries are exact naive broker/source wall-clock timestamps. Each range must contain source rows and be covered by the immutable snapshot. Forward does not expand automatically after validation.</p>
                <div className="form-grid onnx-data-form onnx-window-form">
                  {(['DISCOVERY', 'TOURNAMENT', 'FORWARD'] as const).map((name: WindowName) => (
                    <fieldset key={name} className="onnx-window-fieldset">
                      <legend>{name}</legend>
                      <label htmlFor={`onnx-${name.toLowerCase()}-from`}>From
                        <input id={`onnx-${name.toLowerCase()}-from`} type="datetime-local" step="60" value={windows[name].from} onChange={(event) => setWindows((current) => ({ ...current, [name]: { ...current[name], from: event.target.value } }))} />
                      </label>
                      <label htmlFor={`onnx-${name.toLowerCase()}-to`}>To
                        <input id={`onnx-${name.toLowerCase()}-to`} type="datetime-local" step="60" value={windows[name].to} onChange={(event) => setWindows((current) => ({ ...current, [name]: { ...current[name], to: event.target.value } }))} />
                      </label>
                    </fieldset>
                  ))}
                </div>
                <div className="onnx-data-actions">
                  <ActionButton type="button" onClick={saveWindows} disabled={busy || !windowReady} blockedReason="Set all six boundaries; backend will verify the exact three ordered windows and snapshot coverage.">
                    <ActionProgress active={busy} idle="Validate and save windows" pending="Checking chronology, coverage and row support…" />
                  </ActionButton>
                  <p>This validates configuration only. It does not start Discovery, CPCV, Tournament, Monte Carlo or Forward evaluation.</p>
                </div>
                {workspace.window_config && (
                  <p role="status">Backend has a persisted window configuration for snapshot <span className="mono">{String(workspace.window_config.snapshot_id ?? 'unknown')}</span>. Revalidation creates/reuses an immutable revision.</p>
                )}
              </section>
            </>
          )}
        </>
      )}
    </div>
  )
}
