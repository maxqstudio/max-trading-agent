import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ChampionPage from './ChampionPage'

function response(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response
}

function assertOwnerLanguageClean(text: string) {
  for (const token of [
    /\bR0[0-9]\b/,
    /\bR10\b/,
    /PASS_WAITING_OWNER/,
    /READY_TO_CONFIGURE/,
    /\bBLOCKED\b/,
    /\bNONE\b/,
    /NOT YET AVAILABLE/,
    /Model training \/ ONNX/,
    /Research Challenger \/ Champion mutation/,
    /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/,
    /\bSTRAT-[A-Z0-9-]+\b/i,
    /\bJOB-[A-Z0-9-]+\b/i,
    /\bBT-[A-Z0-9-]+\b/i,
    /\b[a-f0-9]{64}\b/i,
    /SHA-256/i,
    /\bManifest\b/i,
    /\bBundle\b/i,
    /Job ID/i,
    /Source optimizer/i,
    /Champion mutation/,
  ]) expect(text).not.toMatch(token)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Strategy Champion Owner presentation', () => {
  it('shows semantic Champion authority without internal lineage identifiers', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/champion')) {
        return Promise.resolve(response({
          status: 'CURRENT_STRATEGY_CHAMPION',
          baseline_sha256: 'baseline-secret-hash',
          current: {
            strategy_id: 'STRAT-20260926-R01-INTERNAL',
            status: 'CURRENT',
            source_challenger_id: 'STRAT-CHALLENGER-R01',
            source_job_id: 'JOB-R01',
            source_round: 3,
            source_pass: 17,
            params: { InpRiskPct: 1.0 },
            kpi: {
              profit_factor: 1.5,
              recovery_factor: 2.1,
              mean_r: 0.2,
              weighted_r: 0.19,
              trades: 42,
              required_trades: 30,
            },
            champion_ea_sha256: 'ea-secret-hash',
            champion_set_sha256: 'set-secret-hash',
            deployed_ea_sha256: 'deployed-secret-hash',
            deployed_ex5_sha256: 'ex5-secret-hash',
            tester_set_sha256: 'tester-secret-hash',
            promoted_utc: '2026-09-26T01:00:00+00:00',
            promotion_id: 'PROMOTE-R01-INTERNAL',
          },
          integrity: { status: 'VERIFIED' },
        }))
      }
      if (url.endsWith('/api/promotions')) {
        return Promise.resolve(response([
          {
            promotion_id: 'PROMOTE-R01-INTERNAL',
            previous_champion_id: 'STRAT-OLD-R00',
            new_champion_id: 'STRAT-20260926-R01-INTERNAL',
            state: 'COMMITTED',
            completed_utc: '2026-09-26T01:00:00+00:00',
          },
        ]))
      }
      throw new Error('unexpected fetch ' + url)
    }))

    render(<ChampionPage />)

    expect(await screen.findByRole('heading', { name: 'Current Strategy Champion' })).toBeInTheDocument()
    expect(screen.getByText('Current Champion')).toBeInTheDocument()
    expect(screen.getByText('Promoted from a retained Strategy Challenger')).toBeInTheDocument()
    expect(screen.getByText('Champion changed by Owner promotion')).toBeInTheDocument()

    const text = document.body.textContent ?? ''
    assertOwnerLanguageClean(text)
    expect(text).not.toContain('STRAT-20260926-R01-INTERNAL')
    expect(text).not.toContain('PROMOTE-R01-INTERNAL')
    expect(text).not.toContain('secret-hash')
  })
})
