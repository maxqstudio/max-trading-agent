import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DataPage from './DataPage'

function response(payload: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => payload } as Response
}

function assertClean(text: string) {
  for (const token of [
    /\bR0[0-9]\b/, /\bR10\b/, /PASS_WAITING_OWNER/, /READY_TO_CONFIGURE/,
    /CP32_PARITY_REQUIRED/, /PARENT_FIRST_BARRIER_CONTRACT/,
    /OWNER_PARTITION_BOUNDARIES_REQUIRED/, /\bBLOCKED\b/, /\bNONE\b/,
    /NOT YET AVAILABLE/, /Model training \/ ONNX/, /Research Challenger \/ Champion mutation/,
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
  ]) expect(text).not.toMatch(token)
}

function ownerView(overrides: Record<string, unknown> = {}) {
  return {
    state: 'Ready to configure',
    next_step: 'Verify the broker-backed Research data source.',
    sample_requirement: { value: 11, source: 'Current Research configuration' },
    execution_sample_requirement: { value: 11, source: 'Next Data validation will use current configuration' },
    source_status: 'Not ready',
    dataset_status: 'Not started',
    feature_readiness: 'Feature parity validation required',
    label_readiness: 'Parent-bound label validation required',
    leakage_status: 'Not started',
    data_quality_status: 'Not started',
    protected_data_state: 'Research and protected windows require Owner input',
    integrity_status: 'Not started',
    physical_integrity_state: 'Physical-integrity evidence is not available until Data validation completes',
    physical_integrity: null,
    row_identity: 'Original physical source rows',
    boundary_safety: 'A target must end before the next protected window to be eligible.',
    protected_outcomes: 'Locked OOS and Fresh / Forward outcomes remain protected until their authorized validation stages.',
    timeframes: ['H1', 'H4', 'D1'],
    ...overrides,
  }
}

function preflight(overrides: Record<string, unknown> = {}) {
  return {
    status: 'READY_TO_CONFIGURE',
    research_id: 'RSRCH-1',
    parent_strategy_id: 'STRAT-PARENT',
    date_window: null,
    class_distribution: null,
    verified_source: { status: 'NOT_READY', source_id: null, row_coverage: null },
    existing_run: null,
    dependency_authority: null,
    discovery_supervision: null,
    protected_windows: null,
    owner_view: ownerView(),
    ...overrides,
  }
}

function source(value: Record<string, unknown> = {}) {
  return {
    source_count: value.status === 'VERIFIED' ? 1 : 0,
    source: { status: 'NOT_READY', source_id: null, row_coverage: null, ...value },
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Data Owner presentation', () => {
  it('answers source, integrity, readiness, windows and next-step questions without engineering codes', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/research/r01/preflight')) return Promise.resolve(response(preflight()))
      if (url.endsWith('/api/research/r01/source')) return Promise.resolve(response(source()))
      throw new Error('unexpected ' + url)
    }))
    render(<DataPage />)

    expect(await screen.findByRole('heading', { name: 'Data' })).toBeInTheDocument()
    for (const heading of ['Dataset identity', 'Physical data integrity', 'Scientific readiness', 'Research & protected windows']) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }
    expect(screen.getByText('11 trades/month')).toBeInTheDocument()
    expect(screen.getByText('Current Research configuration')).toBeInTheDocument()
    expect(screen.getByText('Next Data validation will use current configuration · 11 trades/month')).toBeInTheDocument()
    expect(screen.getByText('H1 / H4 / D1')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Target dependency & purge authority' })).not.toBeInTheDocument()
    assertClean(document.body.textContent ?? '')
  })

  it('shows only Discovery outcome balance plus validated structural evidence', async () => {
    const verified = {
      status: 'VERIFIED', source_id: 'RSRC-1', broker: 'Monex', feed: 'Monex-Demo',
      source_timezone: 'UTC', main_symbol: 'XAUUSD.m', relative_symbol: 'EURUSD.m',
      data_start_utc: '2021-01-01T00:00:00+00:00', data_end_utc: '2026-06-30T00:00:00+00:00',
      row_coverage: { 'main:main': { rows: 12000 }, 'relative:main': { rows: 11998 } },
    }
    const sealed = preflight({
      status: 'PASS_WAITING_OWNER',
      verified_source: verified,
      existing_run: { state: 'PASS_WAITING_OWNER' },
      date_window: { start: '2021-01-01Z', end: '2026-06-30Z' },
      class_distribution: { sell: 10, skip: 20, buy: 11 },
      dependency_authority: {
        label_dependency_main_bars: 72,
        full_base_dependency_main_bars: 120,
        minimum_legal_purge_main_bars: 120,
        minimum_legal_embargo_main_bars: 120,
      },
      discovery_supervision: {
        physical_rows: 5000, boundary_safe_physical_rows: 4880,
        supervised_rows: 4700, context_only_rows: 300,
        boundary_excluded_from_supervision_rows: 120,
      },
      protected_windows: {
        discovery: { from: '2021-01-01Z', to: '2024-01-01Z' },
        locked_oos: { from: '2024-01-01Z', to: '2026-01-01Z' },
        fresh_forward: { from: '2026-01-01Z', to: '2026-06-30Z' },
      },
      owner_view: ownerView({
        state: 'Ready for Owner review',
        next_step: 'Review Data validation evidence before authorizing the next Research stage.',
        source_status: 'Verified',
        dataset_status: 'Sealed',
        feature_readiness: 'Ready',
        label_readiness: 'Ready',
        leakage_status: 'Passed',
        data_quality_status: 'Passed',
        protected_data_state: 'Protected',
        integrity_status: 'Verified',
        physical_integrity_state: 'Validated physical-integrity evidence available',
        physical_integrity: {
          chronology: 'Passed',
          duplicates: 'No duplicate timestamps found',
          missing_source_data: 'No missing source rows found',
          multi_timeframe_alignment: 'Passed',
          relative_symbol_alignment: 'Passed',
          feature_completeness: 'Passed',
        },
      }),
    })

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/research/r01/preflight')) return Promise.resolve(response(sealed))
      if (url.endsWith('/api/research/r01/source')) return Promise.resolve(response(source(verified)))
      throw new Error('unexpected ' + url)
    }))
    render(<DataPage />)

    expect(await screen.findByText('SELL 10 · SKIP 20 · BUY 11')).toBeInTheDocument()
    expect(screen.getByText('Protected outcomes are excluded.')).toBeInTheDocument()
    expect(screen.getByText('No duplicate timestamps found')).toBeInTheDocument()
    expect(screen.getAllByText('120 main bars')).toHaveLength(3)
    expect(screen.getByText('5000')).toBeInTheDocument()
    expect(screen.getByText('4700')).toBeInTheDocument()
    expect(screen.getByText('2024-01-01Z → 2026-01-01Z')).toBeInTheDocument()
    const visible = document.body.textContent ?? ''
    expect(visible).not.toContain('Locked SELL')
    expect(visible).not.toContain('Fresh BUY')
    assertClean(visible)
  })

  it('preserves verified-source and explicit UTC window requests without exposing internal tokens', async () => {
    let prepared = false
    let startBody: Record<string, unknown> | null = null
    const verified = {
      status: 'VERIFIED', source_id: 'RSRC-1', broker: 'Monex', feed: 'Monex-Demo',
      source_timezone: 'UTC', main_symbol: 'XAUUSD.m', relative_symbol: 'EURUSD.m',
      data_start_utc: '2021-01-01T00:00:00+00:00', data_end_utc: '2026-06-30T00:00:00+00:00',
      row_coverage: { 'main:main': { rows: 12000 } },
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/api/research/r01/preflight')) {
        return Promise.resolve(response(preflight({
          verified_source: prepared ? verified : { status: 'NOT_READY', source_id: null, row_coverage: null },
          owner_view: ownerView(prepared ? {
            source_status: 'Verified',
            next_step: 'Define research and protected windows, then prepare Data validation.',
          } : {}),
        })))
      }
      if (url.endsWith('/api/research/r01/source')) return Promise.resolve(response(source(prepared ? verified : {})))
      if (url.endsWith('/api/research/r01/source/prepare') && method === 'POST') {
        prepared = true
        return Promise.resolve(response({ verified: true }))
      }
      if (url.endsWith('/api/research/r01/start') && method === 'POST') {
        startBody = JSON.parse(String(init?.body ?? '{}'))
        return Promise.resolve(response({}))
      }
      throw new Error('unexpected ' + method + ' ' + url)
    }))

    render(<DataPage />)
    fireEvent.change(await screen.findByLabelText('Source from date UTC'), { target: { value: '2021-01-01' } })
    fireEvent.change(screen.getByLabelText('Source to date UTC'), { target: { value: '2026-06-30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify Research data source' }))
    expect(await screen.findByText('Research data source verified.')).toBeInTheDocument()

    for (const [label, value] of [
      ['Discovery from UTC', '2021-01-01T00:00'],
      ['Discovery to UTC', '2024-01-01T00:00'],
      ['Locked OOS from UTC', '2024-01-01T00:00'],
      ['Locked OOS to UTC', '2026-01-01T00:00'],
      ['Fresh forward from UTC', '2026-01-01T00:00'],
      ['Fresh forward to UTC', '2026-06-30T00:00'],
    ]) fireEvent.change(screen.getByLabelText(label), { target: { value } })

    fireEvent.click(screen.getByRole('button', { name: 'Prepare Data validation' }))
    expect(await screen.findByText('Data validation is ready for Owner review.')).toBeInTheDocument()
    expect(startBody).toMatchObject({
      source_id: 'RSRC-1',
      discovery_from: '2021-01-01T00:00:00+00:00',
      discovery_to: '2024-01-01T00:00:00+00:00',
      locked_oos_from: '2024-01-01T00:00:00+00:00',
      locked_oos_to: '2026-01-01T00:00:00+00:00',
      fresh_forward_from: '2026-01-01T00:00:00+00:00',
      fresh_forward_to: '2026-06-30T00:00:00+00:00',
      confirmed: true,
    })
    expect(document.body.textContent).not.toContain('OWNER_EXPLICIT')
    assertClean(document.body.textContent ?? '')
  })

  it('shows no dataset and keeps Data actions unavailable before Research initialization', async () => {
    const emptyData = preflight({
      status: 'NOT_INITIALIZED',
      research_id: null,
      parent_strategy_id: null,
      verified_source: { status: 'NOT_STARTED', source_id: null, row_coverage: null },
      owner_view: ownerView({
        state: 'Research not initialized',
        next_step: 'Establish a verified Strategy Champion and initialize Research before preparing Data.',
      }),
    })
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/research/r01/preflight')) return Promise.resolve(response(emptyData))
      if (url.endsWith('/api/research/r01/source')) return Promise.resolve(response({
        status: 'NOT_INITIALIZED',
        research_id: null,
        parent_strategy_id: null,
        source_count: 0,
        source: { status: 'NOT_STARTED', source_id: null, row_coverage: null },
      }))
      throw new Error('unexpected ' + url)
    }))

    render(<DataPage />)

    expect(await screen.findByText('No Research dataset yet')).toBeInTheDocument()
    expect(screen.getByText('Establish a verified Strategy Champion and initialize Research before preparing Data.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Source from date UTC')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Discovery from UTC')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Verify Research data source' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Prepare Data validation' })).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
