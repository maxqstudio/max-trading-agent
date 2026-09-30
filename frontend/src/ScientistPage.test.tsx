import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ScientistPage from './ScientistPage'


function assertOwnerLanguageClean(text: string) {
  for (const token of [
    /\bR0[0-9]\b/,
    /\bR10\b/,
    /PASS_WAITING_OWNER/,
    /READY_TO_CONFIGURE/,
    /CP32_PARITY_REQUIRED/,
    /PARENT_FIRST_BARRIER_CONTRACT/,
    /OWNER_PARTITION_BOUNDARIES_REQUIRED/,
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

const readyStatus = {
  knowledge_status: 'READY',
  provider_status: 'READY',
  provider: 'ollama',
  model: 'gpt-oss:120b-cloud',
}

function activeThread(id = 'SCI-active') {
  return {
    thread_id: id,
    created_utc: '2026-09-23T00:00:00+00:00',
    updated_utc: '2026-09-23T00:00:00+00:00',
    title: 'Scientist Chat',
  }
}

function renderDrawer(overrides: Record<string, unknown> = {}) {
  const props = {
    open: true,
    models: ['gpt-oss:120b-cloud', 'gemma4:cloud'],
    selectedModel: 'gpt-oss:120b-cloud',
    contextScope: 'AUTO',
    onClose: vi.fn(),
    onModelChange: vi.fn(),
    onContextChange: vi.fn(),
    ...overrides,
  }
  return render(<ScientistPage {...props} />)
}

function baseFetch(messages: Array<Record<string, unknown>> = []) {
  const thread = activeThread()
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/api/scientist/status')) {
      return Promise.resolve({ ok: true, json: async () => readyStatus } as Response)
    }
    if (url.endsWith('/api/scientist/chat')) {
      return Promise.resolve({ ok: true, json: async () => thread } as Response)
    }
    if (url.endsWith('/api/scientist/threads/' + thread.thread_id + '/messages')) {
      return Promise.resolve({ ok: true, json: async () => messages } as Response)
    }
    throw new Error('unexpected fetch ' + url)
  })
}

describe('Scientist right drawer', () => {
  it('offers only the supported Strategy evidence contexts to the Owner', async () => {
    vi.stubGlobal('fetch', baseFetch([]))
    const onContextChange = vi.fn()

    renderDrawer({ contextScope: 'AUTO', onContextChange })

    const contextSelect = await screen.findByLabelText(/context/i)
    expect(contextSelect).toBeInTheDocument()
    const values = Array.from((contextSelect as HTMLSelectElement).options).map((item) => item.value)
    expect(values).toEqual(['AUTO', 'STRATEGY', 'OPTIMIZER', 'CHALLENGERS', 'CHAMPION', 'PROJECT CONTRACT'])
    expect(values).not.toContain('RESEARCH')
    fireEvent.change(contextSelect, { target: { value: 'STRATEGY' } })
    expect(onContextChange).toHaveBeenCalledWith('STRATEGY')
    assertOwnerLanguageClean(document.body.textContent ?? '')
  })

  it('uses actual responding model first and never classification as the badge', async () => {
    const messages = [{
      message_id: 'm1',
      thread_id: 'SCI-active',
      sequence: 2,
      role: 'assistant',
      content: 'The current Champion is retained.',
      created_utc: '2026-09-23T00:00:00+00:00',
      classification: 'EXISTING',
      evidence_refs: ['contract:strategy-promotion'],
      provider_provenance: {
        configured_model: 'gpt-oss:120b-cloud',
        actual_model: 'gemma4:cloud',
      },
    }]
    vi.stubGlobal('fetch', baseFetch(messages))

    renderDrawer()

    expect(await screen.findByText('The current Champion is retained.')).toBeInTheDocument()
    expect(document.querySelector('.message-model')).toHaveTextContent('gemma4:cloud')
    expect(document.querySelector('.message-model')).not.toHaveTextContent('gpt-oss:120b-cloud')
    expect(screen.queryByText('Existing')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear Chat' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '+ New Chat' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Scientist chat room')).not.toBeInTheDocument()
    expect(screen.getByText('contract:strategy-promotion')).toBeInTheDocument()
  })

  it('falls back to configured model when actual model is absent', async () => {
    vi.stubGlobal('fetch', baseFetch([{
      message_id: 'm-configured',
      thread_id: 'SCI-active',
      sequence: 2,
      role: 'assistant',
      content: 'Configured fallback.',
      created_utc: '2026-09-23T00:00:00+00:00',
      classification: 'EXISTING',
      evidence_refs: [],
      provider_provenance: {
        configured_model: 'gemma4:cloud',
      },
    }]))

    renderDrawer({ selectedModel: 'gpt-oss:120b-cloud' })

    expect(await screen.findByText('Configured fallback.')).toBeInTheDocument()
    expect(document.querySelector('.message-model')).toHaveTextContent('gemma4:cloud')
  })

  it('falls back to selected Chat model when provenance models are absent', async () => {
    vi.stubGlobal('fetch', baseFetch([{
      message_id: 'm-selected',
      thread_id: 'SCI-active',
      sequence: 2,
      role: 'assistant',
      content: 'Selected fallback.',
      created_utc: '2026-09-23T00:00:00+00:00',
      classification: 'EXISTING',
      evidence_refs: [],
      provider_provenance: {},
    }]))

    renderDrawer({ selectedModel: 'gpt-oss:120b-cloud' })

    expect(await screen.findByText('Selected fallback.')).toBeInTheDocument()
    expect(document.querySelector('.message-model')).toHaveTextContent('gpt-oss:120b-cloud')
    expect(screen.queryByText('Existing')).not.toBeInTheDocument()
  })

  it('clear chat rotates to a fresh thread and old history is not loaded again', async () => {
    let current = activeThread('SCI-old')
    let messages: Array<Record<string, unknown>> = [{
      message_id: 'old-a',
      thread_id: 'SCI-old',
      sequence: 1,
      role: 'assistant',
      content: 'Old answer',
      created_utc: '2026-09-23T00:00:00+00:00',
      classification: 'EXISTING',
      evidence_refs: [],
      provider_provenance: { actual_model: 'gpt-oss:120b-cloud' },
    }]

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/api/scientist/status')) {
        return Promise.resolve({ ok: true, json: async () => readyStatus } as Response)
      }
      if (url.endsWith('/api/scientist/chat') && method === 'GET') {
        return Promise.resolve({ ok: true, json: async () => current } as Response)
      }
      if (url.endsWith('/api/scientist/chat/clear') && method === 'POST') {
        current = activeThread('SCI-new')
        messages = []
        return Promise.resolve({
          ok: true,
          json: async () => ({ thread: current, domain_authority_unchanged: true }),
        } as Response)
      }
      if (url.endsWith('/messages') && method === 'GET') {
        return Promise.resolve({ ok: true, json: async () => messages } as Response)
      }
      throw new Error('unexpected fetch ' + method + ' ' + url)
    }))

    renderDrawer()
    expect(await screen.findByText('Old answer')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Clear Chat' }))

    expect(await screen.findByText('Ask Scientist about committed MAX authority.')).toBeInTheDocument()
    expect(screen.queryByText('Old answer')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Scientist chat room')).not.toBeInTheDocument()
  })

  it('renders safe Markdown and GFM structures without executing raw HTML', async () => {
    const tick = String.fromCharCode(96)
    const fence = tick.repeat(3)
    const markdown = [
      '# Summary',
      '',
      'Readable **bold** and *italic* with ' + tick + 'inline_code' + tick + '.',
      '',
      '## Why',
      '- alpha',
      '- beta',
      '',
      '1. first',
      '2. second',
      '',
      '> quoted evidence',
      '',
      fence + 'python',
      'print("ok")',
      fence,
      '',
      '| Field | Value |',
      '| --- | --- |',
      '| Champion | Current |',
      '',
      '[External](https://example.com)',
      '',
      '<script data-testid="raw-html">window.bad = true</script>',
    ].join('\n')
    vi.stubGlobal('fetch', baseFetch([{
      message_id: 'md1',
      thread_id: 'SCI-active',
      sequence: 2,
      role: 'assistant',
      content: markdown,
      created_utc: '2026-09-23T00:00:00+00:00',
      classification: 'EXISTING',
      evidence_refs: ['contract:' + 'a'.repeat(96)],
      provider_provenance: { actual_model: 'gpt-oss:120b-cloud' },
    }]))

    renderDrawer()

    expect(await screen.findByRole('heading', { level: 1, name: 'Summary' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Why' })).toBeInTheDocument()
    expect(screen.getByText('bold').tagName).toBe('STRONG')
    expect(screen.getByText('italic').tagName).toBe('EM')
    expect(screen.getByText('inline_code').tagName).toBe('CODE')
    expect(screen.getByText('quoted evidence').closest('blockquote')).not.toBeNull()
    expect(screen.getByText('print("ok")').tagName).toBe('CODE')
    expect(screen.getByRole('table')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'External' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.queryByTestId('raw-html')).not.toBeInTheDocument()
    expect(screen.queryByText('window.bad = true')).not.toBeInTheDocument()
    expect(screen.getByText('Evidence · 1').closest('details')).not.toHaveAttribute('open')
  })

  it('shows optimistic user turn and one thinking row, then reconciles success', async () => {
    let serverMessages: Array<Record<string, unknown>> = []
    let posted: Record<string, unknown> | null = null
    let resolvePost!: (value: Response) => void
    const postPromise = new Promise<Response>((resolve) => { resolvePost = resolve })
    const thread = activeThread()

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/api/scientist/status')) {
        return Promise.resolve({ ok: true, json: async () => readyStatus } as Response)
      }
      if (url.endsWith('/api/scientist/chat')) {
        return Promise.resolve({ ok: true, json: async () => thread } as Response)
      }
      if (url.endsWith('/messages') && method === 'GET') {
        return Promise.resolve({ ok: true, json: async () => serverMessages } as Response)
      }
      if (url.endsWith('/messages') && method === 'POST') {
        posted = JSON.parse(String(init?.body ?? '{}'))
        return postPromise
      }
      throw new Error('unexpected fetch ' + method + ' ' + url)
    }))

    renderDrawer({ selectedModel: 'gemma4:cloud', contextScope: 'CHAMPION' })
    const input = await screen.findByRole('textbox', { name: 'Message Scientist' })
    fireEvent.change(input, { target: { value: 'Why is this the Champion?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(screen.getByText('Why is this the Champion?')).toBeInTheDocument()
    expect(screen.getAllByLabelText('Scientist is reviewing committed evidence')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Clear Chat' })).toBeDisabled()
    expect(posted).toMatchObject({
      content: 'Why is this the Champion?',
      model: 'gemma4:cloud',
      context_scope: 'CHAMPION',
    })

    const canonicalUser = {
      message_id: 'u1',
      thread_id: thread.thread_id,
      sequence: 1,
      role: 'user',
      content: 'Why is this the Champion?',
      created_utc: '2026-09-23T00:01:00+00:00',
    }
    const assistant = {
      message_id: 'a1',
      thread_id: thread.thread_id,
      sequence: 2,
      role: 'assistant',
      content: '## Answer\n\nThe Champion is current.',
      created_utc: '2026-09-23T00:01:01+00:00',
      classification: 'EXISTING',
      evidence_refs: ['contract:strategy-promotion'],
      provider_provenance: {
        configured_model: 'gemma4:cloud',
        actual_model: 'gemma4:cloud',
      },
    }
    serverMessages = [canonicalUser, assistant]
    resolvePost({
      ok: true,
      json: async () => ({
        status: 'COMPLETED',
        message: assistant,
        evidence: {
          'contract:strategy-promotion': {
            title: 'Strategy promotion contract',
            facts: {},
          },
        },
      }),
    } as Response)

    expect(await screen.findByText('The Champion is current.')).toBeInTheDocument()
    expect(screen.getAllByText('Why is this the Champion?')).toHaveLength(1)
    expect(screen.queryByLabelText('Scientist is reviewing committed evidence')).not.toBeInTheDocument()
    expect(document.querySelector('.message-model')).toHaveTextContent('gemma4:cloud')
    expect(screen.queryByText('Existing')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear Chat' })).not.toBeDisabled()
  })

  it('removes thinking on failure and keeps canonical persisted user turn', async () => {
    let serverMessages: Array<Record<string, unknown>> = []
    let resolvePost!: (value: Response) => void
    const postPromise = new Promise<Response>((resolve) => { resolvePost = resolve })
    const thread = activeThread()

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/api/scientist/status')) {
        return Promise.resolve({ ok: true, json: async () => readyStatus } as Response)
      }
      if (url.endsWith('/api/scientist/chat')) {
        return Promise.resolve({ ok: true, json: async () => thread } as Response)
      }
      if (url.endsWith('/messages') && method === 'GET') {
        return Promise.resolve({ ok: true, json: async () => serverMessages } as Response)
      }
      if (url.endsWith('/messages') && method === 'POST') {
        return postPromise
      }
      throw new Error('unexpected fetch ' + method + ' ' + url)
    }))

    renderDrawer()
    const input = await screen.findByRole('textbox', { name: 'Message Scientist' })
    fireEvent.change(input, { target: { value: 'Read-only question' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    serverMessages = [{
      message_id: 'u-failed',
      thread_id: thread.thread_id,
      sequence: 1,
      role: 'user',
      content: 'Read-only question',
      created_utc: '2026-09-23T00:02:00+00:00',
    }]
    resolvePost({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'SCIENTIST_OLLAMA_UNAVAILABLE' }),
    } as Response)

    expect(await screen.findByText('Scientist request failed: The request could not be completed. Review the current configuration or retained evidence.')).toBeInTheDocument()
    expect(screen.getAllByText('Read-only question')).toHaveLength(1)
    expect(screen.queryByLabelText('Scientist is reviewing committed evidence')).not.toBeInTheDocument()
    expect(input).not.toBeDisabled()
  })

  it('preserves manual upward reading and keeps composer outside the scroll region', async () => {
    const thread = activeThread()
    let resolvePost!: (value: Response) => void
    const postPromise = new Promise<Response>((resolve) => { resolvePost = resolve })
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
      callback(0)
      return 1
    }))
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/api/scientist/status')) {
        return Promise.resolve({ ok: true, json: async () => readyStatus } as Response)
      }
      if (url.endsWith('/api/scientist/chat')) {
        return Promise.resolve({ ok: true, json: async () => thread } as Response)
      }
      if (url.endsWith('/messages') && method === 'GET') {
        return Promise.resolve({ ok: true, json: async () => [] } as Response)
      }
      if (url.endsWith('/messages') && method === 'POST') {
        return postPromise
      }
      throw new Error('unexpected fetch ' + method + ' ' + url)
    }))

    renderDrawer()
    const input = await screen.findByRole('textbox', { name: 'Message Scientist' })
    const history = screen.getByTestId('scientist-history')
    const composer = input.closest('form')
    expect(composer).toHaveClass('drawer-composer')
    expect(history.contains(composer)).toBe(false)

    Object.defineProperty(history, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(history, 'clientHeight', { configurable: true, value: 200 })
    history.scrollTop = 100
    fireEvent.scroll(history)

    fireEvent.change(input, { target: { value: 'Do not yank my scroll' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByText('Do not yank my scroll')
    expect(history.scrollTop).toBe(100)

    resolvePost({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'SCIENTIST_TIMEOUT' }),
    } as Response)
    await screen.findByText('Scientist request failed: The request could not be completed. Review the current configuration or retained evidence.')
  })

  it('keeps hydration distinct from thinking and provider fail-closed state', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))
    const { unmount } = renderDrawer()
    expect(screen.getByText('Loading Scientist…')).toBeInTheDocument()
    expect(screen.queryByLabelText('Scientist is reviewing committed evidence')).not.toBeInTheDocument()
    unmount()

    vi.stubGlobal('fetch', baseFetch([]))
    renderDrawer({
      statusOverride: { provider_status: 'MISSING_CREDENTIAL' },
    })
    expect(await screen.findByText('Provider unavailable: Credential required')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Message Scientist' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })
})
