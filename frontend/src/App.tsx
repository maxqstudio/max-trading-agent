import { useEffect, useState } from 'react'
import './App.css'
import ArtifactsPage from './ArtifactsPage'
import ChallengersPage from './ChallengersPage'
import ChampionPage from './ChampionPage'
import OptimizerPage from './OptimizerPage'
import ScientistPage from './ScientistPage'
import SettingsPage from './SettingsPage'
import type { ProviderSettings } from './SettingsPage'
import { ActionButton, ActionProgress } from './ActionControls'

type Overview = {
  project: string
  phase: string
  milestone: string
  backend: { status: string }
  database: { status: string; schema_version?: number; reason?: string }
  ea_baseline: null | {
    ea_version: string
    sha256: string
    status: string
    snapshot_path: string
  }
  current_strategy_champion: null | { strategy_id: string }
  optimizer_job?: null | {
    job_id: string
    status: string
    active: boolean
    current_round: number
    max_rounds: number
    first_blocker?: string
  }
  mt5: {
    status: string
    terminal?: string
    metaeditor?: string
    data_root?: string
    reason?: string
  }
}

type RecoveryState = {
  status: string
  reason: string
  reset_confirmation?: string
  data_loss_boundary?: string[]
}

function RecoveryRequiredPage({
  state,
  onRecovered,
}: {
  state: RecoveryState
  onRecovered: (result: Record<string, unknown>) => void
}) {
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState('')

  async function backupAndReset() {
    if (!state.reset_confirmation || confirmation !== state.reset_confirmation || busy) return
    setBusy(true)
    setError('')
    setProgress('Preserving the damaged database before rebuilding current schema…')
    try {
      const response = await fetch('/api/recovery/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(body.detail ?? 'Recovery could not be completed.')
      onRecovered(body as Record<string, unknown>)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setProgress('Recovery did not complete. The existing database was not reported as reset.')
    } finally {
      setBusy(false)
    }
  }

  const canReset = state.status === 'RECOVERY_REQUIRED'
    && Boolean(state.reset_confirmation)
    && confirmation === state.reset_confirmation
    && !busy

  return (
    <main className="recovery-screen" aria-labelledby="recovery-title">
      <p className="eyebrow">MAX · Safe startup recovery</p>
      <h1 id="recovery-title">Application state needs recovery</h1>
      <p role="alert" className="error">
        Normal Strategy actions are disabled. Reason: {state.reason || 'Recovery status could not be verified.'}
      </p>
      <section aria-labelledby="recovery-effects">
        <h2 id="recovery-effects">What the explicit recovery will do</h2>
        <ul>
          {(state.data_loss_boundary ?? [
            'The corrupt operational database is preserved in local quarantine.',
            'A new current-schema database is created with the accepted EA baseline.',
            'Generated Strategy and Research state is not restored.',
            'External Scientist provider settings are left untouched.',
          ]).map((item) => <li key={item}>{item}</li>)}
        </ul>
      </section>
      {state.status === 'RECOVERY_REQUIRED' ? (
        <section aria-labelledby="recovery-confirmation">
          <h2 id="recovery-confirmation">Confirm backup and reset</h2>
          <label htmlFor="recovery-confirmation-input">
            Type <code>{state.reset_confirmation}</code> to enable recovery.
          </label>
          <input
            id="recovery-confirmation-input"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            disabled={busy}
          />
          <ActionButton
            type="button"
            disabled={!canReset}
            blockedReason={state.status !== 'RECOVERY_REQUIRED'
              ? 'Recovery status is not verified; reset is intentionally disabled.'
              : !state.reset_confirmation ? 'The server did not provide a recovery confirmation token.'
                : confirmation !== state.reset_confirmation ? 'Enter the exact confirmation text shown above.'
                  : 'Recovery is already in progress.'}
            onClick={backupAndReset}
          >
            <ActionProgress active={busy} idle="Backup and Reset Operational State" pending="Backing up and rebuilding…" />
          </ActionButton>
          {!canReset && !busy && <p role="status">Recovery is blocked until the exact confirmation is entered.</p>}
        </section>
      ) : (
        <p role="status">Recovery status is unavailable; destructive recovery is disabled.</p>
      )}
      {progress && <p role="status">{progress}</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </main>
  )
}

function Value({ children }: { children: React.ReactNode }) {
  return <span className="value">{children}</span>
}

function ownerStatusLabel(value?: string | null) {
  if (!value) return 'Not available'
  const labels: Record<string, string> = {
    READY: 'Ready',
    READY_EXECUTABLE_AND_DATA_ROOT: 'Ready',
    BASELINE_NOT_CHAMPION: 'Baseline only',
    QUALIFIED_POOL_READY: 'Qualified pool ready',
    IDLE: 'Idle',
    NONE: 'None',
    UNAVAILABLE: 'Unavailable',
  }
  if (labels[value]) return labels[value]
  return value.includes('_')
    ? value.toLowerCase().split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ')
    : value
}

function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/api/overview')
      .then((response) => {
        if (!response.ok) throw new Error('HTTP ' + response.status)
        return response.json()
      })
      .then(setData)
      .catch((reason: Error) => setError(reason.message))
  }, [])

  return (
    <>
      <header className="page-head">
        <div>
          <p className="eyebrow">MAX · Strategy lifecycle</p>
          <h1>Overview</h1>
        </div>
      </header>

      {error && <p role="alert" className="error">Application services unavailable: {error}</p>}
      {!data && !error && <p role="status" className="loading">Loading workspace status…</p>}

      {data && (
        <>
          <section aria-labelledby="foundation-status">
            <h2 id="foundation-status">Foundation status</h2>
            <dl className="facts">
              <div><dt>Core services</dt><dd><Value>{ownerStatusLabel(data.backend.status)}</Value></dd></div>
              <div><dt>Application data</dt><dd><Value>{ownerStatusLabel(data.database.status)}</Value></dd></div>
            </dl>
          </section>

          <section aria-labelledby="strategy-authority">
            <h2 id="strategy-authority">Strategy authority</h2>
            <dl className="facts">
              <div><dt>Strategy baseline</dt><dd><Value>{data.ea_baseline ? 'Version ' + data.ea_baseline.ea_version : 'Unavailable'}</Value></dd></div>
              <div><dt>Baseline status</dt><dd><Value>{ownerStatusLabel(data.ea_baseline?.status ?? 'UNAVAILABLE')}</Value></dd></div>
              <div><dt>Strategy Champion</dt><dd><Value>{data.current_strategy_champion ? 'Selected' : 'Not selected'}</Value></dd></div>
            </dl>
          </section>

          <section aria-labelledby="runtime-authority">
            <h2 id="runtime-authority">Execution environment</h2>
            <dl className="facts">
              <div><dt>MT5 readiness</dt><dd><Value>{ownerStatusLabel(data.mt5.status)}</Value></dd></div>
            </dl>
          </section>

          <section aria-labelledby="optimizer-summary">
            <h2 id="optimizer-summary">Strategy Optimizer</h2>
            <dl className="facts">
              <div><dt>Availability</dt><dd>{data.optimizer_job ? 'Optimization available' : 'No optimization yet'}</dd></div>
              <div><dt>Status</dt><dd>{ownerStatusLabel(data.optimizer_job?.status ?? 'IDLE')}</dd></div>
            </dl>
          </section>
        </>
      )}
    </>
  )
}

export default function App() {
  const [workspace, setWorkspace] = useState<'strategy' | 'artifacts' | 'settings'>('strategy')
  const [page, setPage] = useState<'overview' | 'optimizer' | 'challengers' | 'champion'>('overview')
  const [leftOpen, setLeftOpen] = useState(true)
  const [scientistOpen, setScientistOpen] = useState(true)
  const [models, setModels] = useState<string[]>([])
  const [chatModel, setChatModel] = useState('')
  const [contextScope, setContextScope] = useState('AUTO')
  const [scientistStatusOverride, setScientistStatusOverride] = useState<{
    provider_status: string
    provider: string
    model: string
  } | undefined>(undefined)
  const [recoveryState, setRecoveryState] = useState<RecoveryState | null>(null)
  const [recoveryChecked, setRecoveryChecked] = useState(false)
  const [startupError, setStartupError] = useState('')
  const [uiStatus, setUiStatus] = useState('')

  useEffect(() => {
    let active = true
    fetch('/api/recovery/status')
      .then((response) => {
        if (!response.ok) throw new Error('Recovery status HTTP ' + response.status)
        return response.json() as Promise<RecoveryState>
      })
      .then((state) => {
        if (!state || !['READY', 'RECOVERY_REQUIRED'].includes(state.status)) {
          throw new Error('Recovery status response was invalid.')
        }
        if (active) setRecoveryState(state)
      })
      .catch((reason: Error) => {
        if (active) {
          setRecoveryState({ status: 'RECOVERY_STATUS_UNAVAILABLE', reason: reason.message })
          setStartupError('Recovery status could not be verified; workspace actions remain disabled.')
        }
      })
      .finally(() => {
        if (active) setRecoveryChecked(true)
      })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!recoveryChecked || recoveryState?.status !== 'READY') return
    let active = true
    fetch('/api/scientist/provider-settings')
      .then((response) => {
        if (!response.ok) throw new Error('Provider settings HTTP ' + response.status)
        return response.json() as Promise<ProviderSettings>
      })
      .then((settings) => {
        if (!active) return
        setModels(settings.models ?? [])
        setChatModel(
          settings.ui_state?.scientist_chat_model
          || settings.primary_model
          || '',
        )
        setContextScope(settings.ui_state?.scientist_context || 'AUTO')
        setLeftOpen(settings.ui_state?.left_nav_open ?? true)
        setScientistOpen(settings.ui_state?.scientist_drawer_open ?? true)
      })
      .catch((reason: Error) => {
        if (active) setStartupError('Saved workspace preferences could not be loaded: ' + reason.message)
      })
    return () => { active = false }
  }, [recoveryChecked, recoveryState?.status])

  function persistUi(patch: Record<string, unknown>) {
    fetch('/api/scientist/ui-settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
      .then(async (response) => {
        const body = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(body.detail ?? 'Preference update was rejected.')
        setUiStatus('Preference saved.')
      })
      .catch((reason: Error) => setUiStatus('Preference was not saved: ' + reason.message))
  }

  function changeLeft(open: boolean) {
    setLeftOpen(open)
    persistUi({ left_nav_open: open })
  }

  function changeScientist(open: boolean) {
    setScientistOpen(open)
    persistUi({ scientist_drawer_open: open })
  }

  function changeChatModel(model: string) {
    setChatModel(model)
    persistUi({ scientist_chat_model: model })
  }

  function changeContext(context: string) {
    setContextScope(context)
    persistUi({ scientist_context: context })
  }

  function providerSaved(settings: ProviderSettings) {
    setModels(settings.models ?? [])
    const nextModel = settings.ui_state?.scientist_chat_model
      || settings.primary_model
      || ''
    setChatModel(nextModel)
    setScientistStatusOverride({
      provider_status: settings.status ?? 'ROUTE_UNAVAILABLE',
      provider: settings.provider_key,
      model: nextModel,
    })
  }

  if (!recoveryChecked) {
    return <main className="recovery-screen"><p role="status">Checking whether workspace actions are safe…</p></main>
  }
  if (recoveryState?.status === 'RECOVERY_REQUIRED'
    || recoveryState?.status === 'RECOVERY_STATUS_UNAVAILABLE') {
    return (
      <RecoveryRequiredPage
        state={recoveryState}
        onRecovered={(result) => {
          setRecoveryState({ status: 'READY', reason: 'Operational state rebuilt.' })
          setUiStatus('Recovery completed. Preserved database: ' + String(
            (result.backup as { path?: string } | undefined)?.path
            ?? result.quarantine
            ?? 'local quarantine',
          ))
        }}
      />
    )
  }

  return (
    <div className={'app-shell ' + (!leftOpen ? 'left-collapsed ' : '') + (!scientistOpen ? 'scientist-collapsed' : '')}>
      <aside className={'left-nav ' + (leftOpen ? 'left-nav-open' : 'left-nav-closed')} aria-label="MAX workspace navigation">
        <div className="left-nav-head">
          <strong>MAX</strong>
          <button type="button" onClick={() => changeLeft(false)} aria-label="Hide navigation">‹</button>
        </div>
        <button
          className={workspace === 'strategy' ? 'left-nav-active' : ''}
          onClick={() => setWorkspace('strategy')}
        >
          Strategy
        </button>
        <button
          className={workspace === 'artifacts' ? 'left-nav-active' : ''}
          onClick={() => setWorkspace('artifacts')}
        >
          Artifacts
        </button>
        <div className="left-nav-separator" />
        <button
          className={workspace === 'settings' ? 'left-nav-active' : ''}
          onClick={() => setWorkspace('settings')}
        >
          Settings
        </button>
      </aside>

      {!leftOpen && (
        <button
          type="button"
          className="left-nav-toggle"
          aria-label="Show navigation"
          onClick={() => changeLeft(true)}
        >
          ›
        </button>
      )}

      <main className="shell main-workspace">
        {startupError && <p role="alert" className="error">{startupError}</p>}
        {uiStatus && <p role="status" className="notice">{uiStatus}</p>}
        {workspace === 'strategy' && (
          <>
            <nav className="nav" aria-label="MAX navigation">
              <button className={page === 'overview' ? 'nav-active' : ''} onClick={() => setPage('overview')}>Overview</button>
              <button className={page === 'optimizer' ? 'nav-active' : ''} onClick={() => setPage('optimizer')}>Optimizer</button>
              <button className={page === 'challengers' ? 'nav-active' : ''} onClick={() => setPage('challengers')}>Challengers</button>
              <button className={page === 'champion' ? 'nav-active' : ''} onClick={() => setPage('champion')}>Champion</button>
            </nav>
            {page === 'overview' && <OverviewPage />}
            {page === 'optimizer' && <OptimizerPage />}
            {page === 'challengers' && <ChallengersPage />}
            {page === 'champion' && <ChampionPage />}
          </>
        )}
        {workspace === 'artifacts' && <ArtifactsPage />}
        {workspace === 'settings' && <SettingsPage chatModel={chatModel} onSaved={providerSaved} />}
      </main>

      {!scientistOpen && (
        <button
          type="button"
          className="scientist-drawer-toggle"
          onClick={() => changeScientist(true)}
        >
          Show Scientist
        </button>
      )}

      <ScientistPage
        open={scientistOpen}
        models={models}
        selectedModel={chatModel}
        contextScope={contextScope}
        statusOverride={scientistStatusOverride}
        onClose={() => changeScientist(false)}
        onModelChange={changeChatModel}
        onContextChange={changeContext}
      />
    </div>
  )
}
