import { cleanup, fireEvent, render, screen, within, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SettingsPage from './SettingsPage'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const catalog = [
  {
    key: 'ollama',
    display_name: 'Ollama',
    default_base_url: 'http://127.0.0.1:11434/v1',
    requires_api_key: false,
    description: 'Local Ollama daemon',
  },
]

function settings(overrides: Record<string, unknown> = {}) {
  return {
    provider_key: 'ollama',
    base_url: 'http://127.0.0.1:11434/v1',
    auth_mode: 'OLLAMA_LOCAL',
    timeout_sec: 60,
    primary_model: 'gpt-oss:120b-cloud',
    autonomous_fallback: ['glm-5.3-flash:cloud'],
    chat_fallback: [],
    models: [
      'gpt-oss:120b-cloud',
      'gemma4:cloud',
      'glm-5.3-flash:cloud',
      'lfm2.5:8b',
    ],
    credential_status: 'NOT_REQUIRED',
    status: 'READY',
    source: 'SAVED_SETTINGS',
    recovered_from_backup: false,
    ui_state: {
      left_nav_open: true,
      scientist_drawer_open: true,
      scientist_chat_model: 'gemma4:cloud',
      scientist_context: 'AUTO',
    },
    ...overrides,
  }
}

describe('Scientist Provider settings compaction and model routing', () => {
  it('uses discovered-model autocomplete chips and ordered autonomous fallback', async () => {
    let savedBody: Record<string, unknown> | null = null
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/api/scientist/provider-catalog')) {
        return Promise.resolve({ ok: true, json: async () => catalog } as Response)
      }
      if (url.endsWith('/api/scientist/provider-settings') && method === 'GET') {
        return Promise.resolve({ ok: true, json: async () => settings() } as Response)
      }
      if (url.endsWith('/api/scientist/provider-settings') && method === 'PUT') {
        savedBody = JSON.parse(String(init?.body ?? '{}'))
        return Promise.resolve({ ok: true, json: async () => settings(savedBody ?? {}) } as Response)
      }
      throw new Error('unexpected fetch ' + method + ' ' + url)
    }))

    render(<SettingsPage chatModel="gemma4:cloud" />)

    expect(await screen.findByRole('heading', { name: 'Connection' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Model routing' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Actions' })).toBeInTheDocument()

    const autoSearch = screen.getByLabelText('Autonomous fallback · ordered search')
    fireEvent.change(autoSearch, { target: { value: 'gem' } })
    const autoMatches = screen.getByRole('listbox', { name: 'Autonomous fallback · ordered matches' })
    expect(within(autoMatches).getByRole('option', { name: 'gemma4:cloud' })).toBeInTheDocument()
    fireEvent.click(within(autoMatches).getByRole('option', { name: 'gemma4:cloud' }))

    const autoSelected = screen.getByLabelText('Autonomous fallback · ordered selected models')
    expect(autoSelected).toHaveTextContent('glm-5.3-flash:cloud')
    expect(autoSelected).toHaveTextContent('gemma4:cloud')

    fireEvent.click(screen.getByRole('button', { name: 'Move gemma4:cloud up' }))
    expect(autoSelected.textContent?.indexOf('gemma4:cloud')).toBeLessThan(
      autoSelected.textContent?.indexOf('glm-5.3-flash:cloud') ?? 9999,
    )

    fireEvent.change(autoSearch, { target: { value: 'gem' } })
    const autoMatchesAfterSelect = screen.getByRole('listbox', { name: 'Autonomous fallback · ordered matches' })
    expect(within(autoMatchesAfterSelect).queryByRole('option', { name: 'gemma4:cloud' })).not.toBeInTheDocument()

    const chatSearch = screen.getByLabelText('Scientist Chat fallback · explicit search')
    fireEvent.change(chatSearch, { target: { value: 'gem' } })
    const chatMatches = screen.getByRole('listbox', { name: 'Scientist Chat fallback · explicit matches' })
    expect(within(chatMatches).queryByRole('option', { name: 'gemma4:cloud' })).not.toBeInTheDocument()
    expect(within(chatMatches).getByText('No discovered model matches.')).toBeInTheDocument()

    fireEvent.change(chatSearch, { target: { value: 'glm' } })
    const chatMatchesWithGlm = screen.getByRole('listbox', { name: 'Scientist Chat fallback · explicit matches' })
    fireEvent.click(within(chatMatchesWithGlm).getByRole('option', { name: 'glm-5.3-flash:cloud' }))
    const chatSelected = screen.getByLabelText('Scientist Chat fallback · explicit selected models')
    expect(chatSelected).toHaveTextContent('glm-5.3-flash:cloud')

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(savedBody).not.toBeNull())
    expect(savedBody).toMatchObject({
      primary_model: 'gpt-oss:120b-cloud',
      autonomous_fallback: ['gemma4:cloud', 'glm-5.3-flash:cloud'],
      chat_fallback: ['glm-5.3-flash:cloud'],
    })
  })

  it('revalidates and removes stale fallback models after provider discovery changes', async () => {
    const initial = settings({
      autonomous_fallback: ['gemma4:cloud', 'glm-5.3-flash:cloud'],
      chat_fallback: ['glm-5.3-flash:cloud'],
    })
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/api/scientist/provider-catalog')) {
        return Promise.resolve({ ok: true, json: async () => catalog } as Response)
      }
      if (url.endsWith('/api/scientist/provider-settings') && method === 'GET') {
        return Promise.resolve({ ok: true, json: async () => initial } as Response)
      }
      if (url.endsWith('/api/scientist/provider-settings/connect') && method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: 'CONNECTED',
            provider_key: 'ollama',
            base_url: 'http://127.0.0.1:11434/v1',
            models: ['gpt-oss:120b-cloud', 'gemma4:cloud'],
            model_count: 2,
            discovery_source: 'OPENAI_MODELS',
            latency_ms: 18,
            credential_status: 'NOT_REQUIRED',
          }),
        } as Response)
      }
      throw new Error('unexpected fetch ' + method + ' ' + url)
    }))

    render(<SettingsPage chatModel="gemma4:cloud" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }))

    expect(await screen.findByText('Connected · 2 models · 18 ms')).toBeInTheDocument()
    expect(await screen.findByText('Connection refreshed · removed 2 stale fallback selections')).toBeInTheDocument()

    const autoSelected = screen.getByLabelText('Autonomous fallback · ordered selected models')
    expect(autoSelected).toHaveTextContent('gemma4:cloud')
    expect(autoSelected).not.toHaveTextContent('glm-5.3-flash:cloud')
    const chatSelected = screen.getByLabelText('Scientist Chat fallback · explicit selected models')
    expect(chatSelected).toHaveTextContent('Empty · selected Chat model never falls back.')
    expect(screen.queryByText('glm-5.3-flash:cloud')).not.toBeInTheDocument()
  })
})
