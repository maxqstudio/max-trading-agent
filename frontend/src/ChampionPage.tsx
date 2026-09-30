import { useEffect, useState } from 'react'

type Champion = {
  strategy_id: string
  status: string
  source_challenger_id: string
  source_job_id: string
  source_round: number
  source_pass: number
  params: Record<string, number>
  kpi: {
    profit_factor: number
    recovery_factor: number
    mean_r: number
    weighted_r: number
    trades: number
    required_trades: number
  }
  champion_ea_sha256: string
  champion_set_sha256: string
  deployed_ea_sha256: string
  deployed_ex5_sha256: string
  tester_set_sha256: string
  promoted_utc: string
  promotion_id: string
}

type ChampionResponse = {
  current: Champion | null
  status: string
  seed_authority?: string
  baseline_sha256: string
  integrity?: {
    status: string
    project_ea_sha256: string
    project_set_sha256: string
    deployed_ea_sha256: string
    deployed_ex5_sha256: string
    tester_set_sha256: string
  }
}

type Promotion = {
  promotion_id: string
  previous_champion_id: string | null
  new_champion_id: string
  state: string
  completed_utc: string | null
}

function n(value: number | undefined) {
  return value === undefined || value === null ? '—' : String(value)
}

function promotionStateLabel(state: string) {
  const labels: Record<string, string> = {
    COMMITTED: 'Completed',
    FAILED: 'Failed',
    ROLLED_BACK: 'Rolled back',
  }
  return labels[state] ?? 'Review required'
}

function championIntegrityLabel(status?: string) {
  if (!status) return '—'
  return status === 'VERIFIED' ? 'Verified' : 'Review required'
}

export default function ChampionPage() {
  const [data, setData] = useState<ChampionResponse | null>(null)
  const [history, setHistory] = useState<Promotion[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      fetch('/api/champion').then(async (response) => {
        if (!response.ok) throw new Error('Champion HTTP ' + response.status)
        return response.json()
      }),
      fetch('/api/promotions').then(async (response) => {
        if (!response.ok) throw new Error('Promotions HTTP ' + response.status)
        return response.json()
      }),
    ])
      .then(([champion, promotions]) => {
        setData(champion)
        setHistory(promotions)
      })
      .catch((reason: Error) => setError(reason.message))
  }, [])

  return (
    <>
      <header className="page-head">
        <div>
          <p className="eyebrow">MAX · Strategy lifecycle</p>
          <h1>Strategy Champion</h1>
        </div>
      </header>

      {error && <p role="alert" className="error">Champion authority unavailable: {error}</p>}
      {!data && !error && <p role="status" className="loading">Loading Champion authority…</p>}

      {data?.current === null && (
        <section aria-labelledby="champion-empty">
          <h2 id="champion-empty">Current Strategy Champion</h2>
          <dl className="facts compact">
            <div><dt>Current</dt><dd><strong>Not selected</strong></dd></div>
            <div><dt>Starting point</dt><dd>{data.seed_authority === 'BASELINE_NOT_CHAMPION' ? 'Strategy baseline' : 'Existing Strategy authority'}</dd></div>
          </dl>
        </section>
      )}

      {data?.current && (
        <>
          <section aria-labelledby="champion-current">
            <h2 id="champion-current">Current Strategy Champion</h2>
            <dl className="facts compact">
              <div><dt>Status</dt><dd><strong>Current Champion</strong></dd></div>
              <div><dt>Promoted</dt><dd>{data.current.promoted_utc}</dd></div>
              <div><dt>Source</dt><dd>Promoted from a retained Strategy Challenger</dd></div>
              <div><dt>Integrity</dt><dd><strong>{championIntegrityLabel(data.integrity?.status)}</strong></dd></div>
            </dl>
          </section>

          <section aria-labelledby="champion-kpi">
            <h2 id="champion-kpi">Retained Strategy performance evidence</h2>
            <dl className="facts compact">
              <div><dt>Profit Factor</dt><dd>{n(data.current.kpi.profit_factor)}</dd></div>
              <div><dt>Recovery Factor</dt><dd>{n(data.current.kpi.recovery_factor)}</dd></div>
              <div><dt>Mean R</dt><dd>{n(data.current.kpi.mean_r)}</dd></div>
              <div><dt>Weighted R</dt><dd>{n(data.current.kpi.weighted_r)}</dd></div>
              <div><dt>Trades</dt><dd>{n(data.current.kpi.trades)} / required {n(data.current.kpi.required_trades)}</dd></div>
            </dl>
          </section>

          <section aria-labelledby="champion-params">
            <h2 id="champion-params">Champion parameters</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Parameter</th><th>Value</th></tr></thead>
                <tbody>
                  {Object.entries(data.current.params).map(([name, value]) => (
                    <tr key={name}><td className="mono">{name}</td><td>{n(value)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      <section aria-labelledby="promotion-history">
        <h2 id="promotion-history">Promotion history</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Completed</th><th>Promotion</th><th>Status</th></tr>
            </thead>
            <tbody>
              {history.length === 0 && <tr><td colSpan={3}>No completed promotion history.</td></tr>}
              {history.map((row) => (
                <tr key={row.promotion_id}>
                  <td>{row.completed_utc ?? '—'}</td>
                  <td>{row.previous_champion_id ? 'Champion changed by Owner promotion' : 'Initial Champion selected'}</td>
                  <td>{promotionStateLabel(row.state)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}
