import { useEffect, useState } from 'react'
import './App.css'
import ArtifactsPage from './ArtifactsPage'
import ChallengersPage from './ChallengersPage'
import ChampionPage from './ChampionPage'
import OptimizerPage from './OptimizerPage'
import ResearchPage from './ResearchPage'
import ScientistPage from './ScientistPage'
import SettingsPage from './SettingsPage'
import type { ProviderSettings } from './SettingsPage'

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
  const [workspace, setWorkspace] = useState<'strategy' | 'research' | 'artifacts' | 'settings'>('strategy')
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

  useEffect(() => {
    fetch('/api/scientist/provider-settings')
      .then((response) => {
        if (!response.ok) throw new Error('HTTP ' + response.status)
        return response.json() as Promise<ProviderSettings>
      })
      .then((settings) => {
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
      .catch(() => undefined)
  }, [])

  function persistUi(patch: Record<string, unknown>) {
    fetch('/api/scientist/ui-settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }).catch(() => undefined)
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
          className={workspace === 'research' ? 'left-nav-active' : ''}
          onClick={() => setWorkspace('research')}
        >
          Research
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
        {workspace === 'research' && <ResearchPage />}
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
