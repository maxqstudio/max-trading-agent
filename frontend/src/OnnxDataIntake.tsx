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
  const [reviewedSource, setReviewedSource] = useState<{ source_path?: string; timezone_provenance?: string } | null>(null)
  const [confirmSnapshot, setConfirmSnapshot] = useState(false)
  const [confirmCorrection, setConfirmCorrection] = useState(false)
  const [windows, setWindows] = useState<WindowDraft>(EMPTY_WINDOWS)
  const [error, setError] = useState('')
  const [operationResult, setOperationResult] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)

  const snapshot = workspace?.latest_snapshot ?? null
  const firstBlocker = preflight?.first_blocker ?? workspace?.first_blocker ?? null
  const hasAdvancedSourceInput = Boolean(sourcePath.trim() || timezoneProvenance.trim())
  const visibleStatus = busy ? 'Sedang memproses' : preflight
    ? (preflight.data_quality.status === 'PASS' || (preflight.snapshot_permitted
      && preflight.data_quality.issues.every((issue) => issue.code === 'TIMEZONE_PROVENANCE_REQUIRED' || issue.severity === 'RECONCILIATION_PENDING')))
      ? 'Data ditemukan' : 'Data perlu perhatian'
    : snapshot ? 'Snapshot tersimpan' : 'Belum diperiksa'
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
        setOperationResult(name === 'Pemeriksaan data' ? '' : `${name} selesai.`)
      } else {
        setOperationResult(`${name} selesai.`)
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
    setReviewedSource(null)
    setConfirmSnapshot(false)
    const payload = {
      ...(sourcePath.trim() ? { source_path: sourcePath.trim() } : {}),
      ...(timezoneProvenance.trim() ? { timezone_provenance: timezoneProvenance.trim() } : {}),
    }
    const result = await perform('Pemeriksaan data', async () => {
      const evidence = await preflightOnnxData(payload)
      setPreflight(evidence)
      setReviewedSource(payload)
      return evidence
    })
    if (!result) { setPreflight(null); setReviewedSource(null) }
  }

  const createSnapshot = async () => {
    if (!preflight || !reviewedSource || !preflight.snapshot_permitted) return
    const evidence = preflight
    setPreflight(null)
    setReviewedSource(null)
    setConfirmSnapshot(false)
    await perform('Penyimpanan snapshot', () => createOnnxSnapshot({
      ...reviewedSource,
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
        <h1>Data Intake</h1>
      </header>

      {loading && <p role="status" className="loading">Memuat sumber data…</p>}
      {!loading && error && (
        <div className="onnx-load-error" role="alert">
          <p><strong>Pemeriksaan belum berhasil.</strong> {error}</p>
          <button type="button" onClick={() => { setLoading(true); setError(''); setRetry((value) => value + 1) }}>Coba lagi</button>
        </div>
      )}

      {!loading && workspace && (
        <>
          <section className="onnx-card onnx-data-primary" aria-labelledby="onnx-source-preflight-title">
            <div className="onnx-data-source-row">
              <div>
                <h2 id="onnx-source-preflight-title">Sumber data</h2>
                <p className="onnx-data-filename">{sourcePath.trim() ? 'Lokasi alternatif dipilih' : 'Max_MTF_Training.csv'}</p>
                <p className="onnx-data-muted">{sourcePath.trim()
                  ? 'Lokasi alternatif akan divalidasi agar tetap berada di MT5 Common Files.'
                  : 'MT5 Common Files · ditemukan otomatis saat diperiksa'}</p>
              </div>
              <ActionButton type="button" onClick={runPreflight} disabled={busy || loading}>
                <ActionProgress active={busy} idle="Periksa Data" pending="Memeriksa…" />
              </ActionButton>
            </div>
            <p className="onnx-data-state" role="status">{visibleStatus}</p>
            {preflight?.first_blocker === 'TIMEZONE_PROVENANCE_REQUIRED' && (
              <p className="onnx-data-muted">Data bisa diperiksa. Zona waktu broker belum terverifikasi; kesiapan riset tetap tertahan.</p>
            )}
            {operationResult && <p className="onnx-data-muted">{operationResult}</p>}
            <details className="onnx-data-advanced" open={hasAdvancedSourceInput}>
              <summary>Pengaturan lanjutan</summary>
              <div className="form-grid onnx-data-form">
                <label htmlFor="onnx-source-path">Lokasi file alternatif
                  <input id="onnx-source-path" value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="Kosongkan untuk lokasi default MT5" autoComplete="off" />
                </label>
                <label htmlFor="onnx-timezone-provenance">Provenance waktu broker (opsional untuk periksa data)
                  <input id="onnx-timezone-provenance" value={timezoneProvenance} onChange={(event) => setTimezoneProvenance(event.target.value)} placeholder="Isi hanya jika sumber waktunya diketahui" maxLength={160} />
                </label>
              </div>
              <p className="onnx-data-muted">Lokasi alternatif wajib berada dalam MT5 Common Files. Waktu dari CSV tidak otomatis dianggap UTC.</p>
            </details>
            {preflight && (
              <div className="onnx-data-result" aria-label="Source preflight result">
                <p><strong>{preflight.data_quality.row_count.toLocaleString('id-ID')} baris</strong> · {preflight.data_quality.symbol ?? 'Simbol tidak diketahui'} · {preflight.data_quality.timeframe ?? 'Timeframe tidak diketahui'}</p>
                <details className="onnx-data-advanced">
                  <summary>Hasil pemeriksaan lengkap</summary>
                  <p>Status: {preflight.status} · SHA-256: <span className="mono">{preflight.source_identity.sha256}</span></p>
                  {firstBlocker && <p>Blocker: {firstBlocker}</p>}
                  {preflight.data_quality.issues.length > 0 && (
                    <ul className="onnx-data-issues-list" aria-label="Preflight data-quality findings">
                      {preflight.data_quality.issues.map((issue, index) => (
                        <li key={`${issue.code}-${index}`}>
                          <strong>{issue.code}</strong> — {issue.message} <span>({issue.severity})</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </details>
                {preflight.snapshot_permitted && (
                  <div className="onnx-confirmation">
                    <label>
                      <input type="checkbox" checked={confirmSnapshot} onChange={(event) => setConfirmSnapshot(event.target.checked)} />
                      Saya menyetujui pembuatan snapshot dari file yang diperiksa. Status pemeriksaan tidak diubah.
                    </label>
                    <ActionButton type="button" onClick={createSnapshot} disabled={!confirmSnapshot || busy} blockedReason="Konfirmasi pembuatan snapshot terlebih dahulu.">
                      <ActionProgress active={busy} idle="Simpan snapshot" pending="Menyimpan dan memverifikasi…" />
                    </ActionButton>
                  </div>
                )}
              </div>
            )}
          </section>

          {snapshot && (
            <>
              <section className="onnx-card" aria-labelledby="onnx-snapshot-title">
                <h2 id="onnx-snapshot-title">Snapshot tersimpan</h2>
                <p className="onnx-data-muted">{snapshot.row_count.toLocaleString('id-ID')} baris · {snapshot.symbol ?? 'Simbol tidak diketahui'} · {snapshot.timeframe ?? 'Timeframe tidak diketahui'}</p>
                <details className="onnx-data-advanced">
                  <summary>Identitas snapshot</summary>
                  <dl className="onnx-facts">
                  <div className="onnx-field"><dt>Snapshot ID</dt><dd className="mono">{snapshot.snapshot_id}</dd></div>
                  <div className="onnx-field"><dt>Dataset ID</dt><dd className="mono">{snapshot.dataset_id}</dd></div>
                  <div className="onnx-field"><dt>SHA-256</dt><dd className="mono">{snapshot.sha256}</dd></div>
                  <div className="onnx-field"><dt>Source contract</dt><dd>{snapshot.strategy_contract} · {snapshot.feature_contract}</dd></div>
                  <div className="onnx-field"><dt>Window coverage</dt><dd>{snapshot.timestamp_min ?? 'Unavailable'} → {snapshot.timestamp_max ?? 'Unavailable'}</dd></div>
                  <div className="onnx-field"><dt>Evidence class</dt><dd>{snapshot.evidence_class}</dd></div>
                </dl>
                  {snapshot.parent_snapshot_id && <p>Induk snapshot tetap tersimpan: <span className="mono">{snapshot.parent_snapshot_id}</span>.</p>}
                </details>
                <details className="onnx-data-advanced">
                  <summary>Audit kualitas data</summary>
                  <DataIssues snapshot={snapshot} />
                </details>
              </section>

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

              <details className="onnx-card onnx-data-workflow" aria-labelledby="onnx-window-config-title">
                <summary id="onnx-window-config-title">Atur periode riset</summary>
                <p className="onnx-data-muted">Periode divalidasi dari snapshot ini. Tidak ada riset yang dijalankan.</p>
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
                  <p className="onnx-data-muted">Hanya menyimpan konfigurasi, tanpa menjalankan eksperimen.</p>
                </div>
                {workspace.window_config && (
                  <p role="status">Backend has a persisted window configuration for snapshot <span className="mono">{String(workspace.window_config.snapshot_id ?? 'unknown')}</span>. Revalidation creates/reuses an immutable revision.</p>
                )}
              </details>
            </>
          )}
        </>
      )}
    </div>
  )
}
