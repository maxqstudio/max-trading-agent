import { useEffect, useState } from 'react'
import {
  fetchOnnxWorkspace,
  ONNX_STAGE_PAGE_IDS,
  type OnnxStagePageId,
  type OnnxStatus,
  type OnnxWorkspaceSnapshot,
  type StateSection,
  type ValueSection,
} from './onnxApi'

type OnnxPageId = 'overview' | OnnxStagePageId

const PAGE_DEFINITIONS: Array<{
  id: OnnxPageId
  label: string
  purpose: string
}> = [
  { id: 'overview', label: 'Overview', purpose: 'Read the current cycle and evidence availability.' },
  { id: 'data_intake', label: 'Data Intake', purpose: 'Review the future source-data and immutable-snapshot boundary.' },
  { id: 'discovery', label: 'Discovery', purpose: 'Review future Discovery stages and the Qualified Pool boundary.' },
  { id: 'cpcv', label: 'CPCV', purpose: 'Review cross-validation stage availability and its prerequisites.' },
  { id: 'tournament', label: 'Tournament', purpose: 'Review independent Tournament stage availability.' },
  { id: 'monte_carlo', label: 'Monte Carlo', purpose: 'Review robustness-stage availability and prerequisites.' },
  { id: 'challenger', label: 'Challenger', purpose: 'Review Forward and the later candidate-readiness gates.' },
  { id: 'champion', label: 'Champion', purpose: 'Review the Owner-only ONNX Champion boundary.' },
]

const STATUS_LABELS: Record<OnnxStatus, string> = {
  NOT_IMPLEMENTED: 'Not implemented',
  NOT_STARTED: 'Not started',
  NOT_PROVEN: 'Not proven',
  UNAVAILABLE: 'Unavailable',
  RECOVERY_REQUIRED: 'Recovery required',
}

function statusLabel(status: OnnxStatus) {
  return STATUS_LABELS[status]
}

function StatusBadge({ status }: { status: OnnxStatus }) {
  return <span className="onnx-status" data-status={status}>{statusLabel(status)}</span>
}

function StateCard({
  title,
  state,
  children,
}: {
  title: string
  state: StateSection
  children: React.ReactNode
}) {
  return (
    <section className="onnx-card" aria-label={title}>
      <div className="onnx-card-head">
        <h2>{title}</h2>
        <div className="onnx-card-states">
          <span><span className="onnx-state-label">State</span><StatusBadge status={state.status} /></span>
          <span><span className="onnx-state-label">Availability</span><StatusBadge status={state.availability} /></span>
        </div>
      </div>
      {children}
      <p className="onnx-reason">{state.reason}</p>
    </section>
  )
}

function FieldValue({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div className="onnx-field">
      <dt>{label}</dt>
      <dd>{value === null ? 'Not available' : String(value)}</dd>
    </div>
  )
}

function StateValueCard({
  title,
  state,
}: {
  title: string
  state: ValueSection
}) {
  return (
    <StateCard title={title} state={state}>
      <p className="onnx-value">{state.value === null ? 'Not available' : String(state.value)}</p>
    </StateCard>
  )
}

function EmptyEvidence({ children }: { children: React.ReactNode }) {
  return <p className="onnx-empty">{children}</p>
}

function OnnxOverview({ data }: { data: OnnxWorkspaceSnapshot }) {
  const stages = new Map(data.stage_pages.map((stage) => [stage.page_id, stage]))
  const forward = data.challenger.forward
  return (
    <>
      <header className="page-head onnx-page-head">
        <div>
          <p className="eyebrow">MAX · ONNX research workspace</p>
          <h1>ONNX Overview</h1>
          <p className="onnx-intro">Read-only status from the backend. ONNX-01 has no operational research store or execution controls.</p>
        </div>
      </header>
      <div className="onnx-card-grid">
        <StateCard title="Cycle status and identity" state={data.operational_state}>
          <dl className="onnx-facts">
            <FieldValue label="Cycle identity" value={data.operational_state.cycle_id} />
            <FieldValue label="Persisted operational cycle" value={data.operational_state.persisted ? 'Yes' : 'No'} />
          </dl>
        </StateCard>

        <StateCard title="Dataset and snapshot" state={data.dataset}>
          <dl className="onnx-facts">
            <FieldValue label="Dataset identity" value={data.dataset.dataset_id} />
            <FieldValue label="Snapshot identity" value={data.dataset.snapshot_id} />
            <FieldValue label="Symbol" value={data.dataset.symbol} />
            <FieldValue label="Timeframe" value={data.dataset.timeframe} />
          </dl>
        </StateCard>

        <StateCard title="Frozen research windows" state={data.research_windows}>
          {data.research_windows.items === null
            ? <EmptyEvidence>No persisted cycle is available to provide window identities or dates.</EmptyEvidence>
            : <EmptyEvidence>No research windows are reported.</EmptyEvidence>}
        </StateCard>

        <StateCard title="Scientific authority" state={data.scientific_authority}>
          <p>ONNX-00 planning authority only; no scientific result is established.</p>
        </StateCard>

        <StateCard title="Hardware capacity" state={data.hardware_capacity}>
          <dl className="onnx-facts">
            <FieldValue label="Available GPU memory" value={data.hardware_capacity.gpu_vram_bytes} />
            <FieldValue label="Available system memory" value={data.hardware_capacity.system_ram_bytes} />
          </dl>
        </StateCard>

        <StateCard title="Discovery budget and progress" state={data.discovery}>
          <dl className="onnx-facts">
            <FieldValue label="Frozen budget" value={data.discovery.budget.value} />
            <FieldValue label="Completed experiments" value={data.discovery.experiment_progress.completed} />
            <FieldValue label="Total experiments" value={data.discovery.experiment_progress.total} />
          </dl>
        </StateCard>

        <StateCard title="Qualified Pool" state={data.discovery.qualified_pool}>
          <EmptyEvidence>No persisted pool or candidate count is available.</EmptyEvidence>
          <p className="onnx-reason">Cheap Screen never grants qualification authority.</p>
        </StateCard>

        {(['cpcv', 'tournament', 'monte_carlo'] as const).map((pageId) => {
          const stage = stages.get(pageId)
          if (!stage) return null
          const title = {
            cpcv: 'CPCV progression',
            tournament: 'Tournament progression',
            monte_carlo: 'Monte Carlo progression',
          }[pageId]
          return <StateCard key={pageId} title={title} state={stage}>
            <EmptyEvidence>No stage execution or evidence is persisted.</EmptyEvidence>
          </StateCard>
        })}

        <StateCard title="Forward progression" state={forward}>
          <EmptyEvidence>Forward is part of Challenger; no Forward outcomes are available.</EmptyEvidence>
        </StateCard>

        <StateCard title="Challenger candidates" state={data.challenger.candidates}>
          <EmptyEvidence>No persisted ONNX Challenger records are available.</EmptyEvidence>
        </StateCard>

        <StateCard title="ONNX Champion" state={data.champion}>
          <dl className="onnx-facts">
            <FieldValue label="Champion identity" value={data.champion.identity} />
          </dl>
        </StateCard>

        <StateValueCard title="Current stage" state={data.current_stage} />
        <StateCard title="Checkpoint" state={data.checkpoint}>
          <dl className="onnx-facts">
            <FieldValue label="Checkpoint identity" value={data.checkpoint.identity} />
          </dl>
        </StateCard>

        <StateCard title="First blocker" state={data.first_blocker}>
          <p>{data.first_blocker.message}</p>
        </StateCard>

        <StateCard title="Recovery availability" state={data.recovery}>
          <p>{data.recovery.available ? 'Available' : 'Unavailable'}</p>
        </StateCard>
      </div>
    </>
  )
}

function StageWorkspacePage({
  pageId,
  data,
}: {
  pageId: OnnxStagePageId
  data: OnnxWorkspaceSnapshot
}) {
  const page = PAGE_DEFINITIONS.find((item) => item.id === pageId)
  const stage = data.stage_pages.find((item) => item.page_id === pageId)
  if (!page || !stage) return <p role="alert">The backend did not provide this ONNX page state.</p>

  return (
    <>
      <header className="page-head onnx-page-head">
        <div>
          <p className="eyebrow">MAX · ONNX research workspace</p>
          <h1>{page.label}</h1>
          <p className="onnx-intro">{page.purpose}</p>
        </div>
      </header>

      <div className="onnx-card-grid">
        <StateCard title="Stage availability" state={stage}>
          <p>Execution remains unavailable in ONNX-01.</p>
        </StateCard>

        <section className="onnx-card" aria-labelledby="onnx-prerequisites-title">
          <h2 id="onnx-prerequisites-title">Required prerequisites</h2>
          <ul className="onnx-prerequisites">
            {stage.prerequisites.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </section>

        <section className="onnx-card" aria-labelledby="onnx-evidence-title">
          <h2 id="onnx-evidence-title">Progress and evidence</h2>
          <EmptyEvidence>{stage.reason} No evidence entries are available.</EmptyEvidence>
        </section>

        {pageId === 'discovery' && (
          <StateCard title="Qualified Pool" state={data.discovery.qualified_pool}>
            <EmptyEvidence>No persisted Qualified Pool is available; this is not a candidate result.</EmptyEvidence>
          </StateCard>
        )}

        {pageId === 'challenger' && (
          <>
            <StateCard title="Forward" state={data.challenger.forward}>
              <EmptyEvidence>Forward is nested in Challenger. No Forward outcome is available.</EmptyEvidence>
            </StateCard>
            <StateCard title="Candidate readiness" state={data.challenger.candidates}>
              <EmptyEvidence>No persisted Challenger candidates are available.</EmptyEvidence>
            </StateCard>
          </>
        )}

        {pageId === 'champion' && (
          <StateCard title="ONNX Champion" state={data.champion}>
            <dl className="onnx-facts">
              <FieldValue label="Champion identity" value={data.champion.identity} />
            </dl>
            <p>Promotion requires explicit Owner selection after every scientific and runtime gate.</p>
          </StateCard>
        )}
      </div>
    </>
  )
}

export default function OnnxWorkspace() {
  const [pageId, setPageId] = useState<OnnxPageId>('overview')
  const [data, setData] = useState<OnnxWorkspaceSnapshot | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    fetchOnnxWorkspace(controller.signal)
      .then((snapshot) => {
        setData(snapshot)
        setLoading(false)
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setError(reason instanceof Error ? reason.message : 'ONNX workspace status could not be loaded.')
        setData(null)
        setLoading(false)
      })
    return () => controller.abort()
  }, [retryToken])

  return (
    <section className="onnx-workspace" aria-label="ONNX workspace">
      <nav className="nav onnx-page-nav" aria-label="ONNX pages">
        {PAGE_DEFINITIONS.map((page) => (
          <button
            key={page.id}
            type="button"
            className={pageId === page.id ? 'nav-active' : ''}
            aria-current={pageId === page.id ? 'page' : undefined}
            onClick={() => setPageId(page.id)}
          >
            {page.label}
          </button>
        ))}
      </nav>

      {loading && <p role="status" className="loading">Loading ONNX workspace status…</p>}
      {!loading && error && (
        <div className="onnx-load-error" role="alert">
          <p>{error}</p>
          <button
            type="button"
            onClick={() => {
              setLoading(true)
              setError('')
              setRetryToken((value) => value + 1)
            }}
          >
            Retry ONNX status read
          </button>
        </div>
      )}
      {!loading && data && pageId === 'overview' && <OnnxOverview data={data} />}
      {!loading && data && pageId !== 'overview' && (
        <StageWorkspacePage pageId={pageId} data={data} />
      )}
    </section>
  )
}

export { ONNX_STAGE_PAGE_IDS }
