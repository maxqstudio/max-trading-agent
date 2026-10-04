import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { ActionButton, ActionProgress } from './ActionControls'

export type ProviderSettings = {
  provider_key: string
  base_url: string
  auth_mode: string
  timeout_sec: number
  primary_model: string
  autonomous_fallback: string[]
  chat_fallback: string[]
  models: string[]
  credential_status?: string
  status?: string
  source?: string
  recovered_from_backup?: boolean
  ui_state?: {
    left_nav_open: boolean
    scientist_drawer_open: boolean
    scientist_chat_model: string
    scientist_context: string
  }
}

type ProviderCatalogItem = {
  key: string
  display_name: string
  default_base_url: string
  requires_api_key: boolean
  description: string
}

type ConnectionResult = {
  status: string
  provider_key: string
  base_url: string
  models: string[]
  model_count: number
  discovery_source: string
  latency_ms: number
  credential_status: string
}

type SettingsPageProps = {
  chatModel?: string
  onSaved?: (settings: ProviderSettings) => void
}

type ModelPickerProps = {
  label: string
  models: string[]
  selected: string[]
  excluded: string[]
  ordered?: boolean
  disabled?: boolean
  emptyHint: string
  onChange: (models: string[]) => void
}

function ownerStatus(value?: string) {
  const labels: Record<string, string> = {
    READY: 'Ready',
    UNCONFIGURED: 'Not configured',
    CONFIGURED: 'Configured',
    NOT_REQUIRED: 'Not required',
    ROUTE_UNAVAILABLE: 'Unavailable',
    PROVIDER_UNAVAILABLE: 'Unavailable',
    MISSING_CREDENTIAL: 'Credential required',
  }
  if (!value) return 'Not configured'
  return labels[value] ?? 'Review required'
}

function uniqueModels(values: string[]) {
  const seen = new Set<string>()
  return values.filter((value) => {
    if (!value || seen.has(value)) return false
    seen.add(value)
    return true
  })
}

function sanitizeSettings(value: ProviderSettings, currentChatModel = '') {
  const available = new Set(value.models ?? [])
  const primary = available.has(value.primary_model) ? value.primary_model : ''
  const selectedChatModel = currentChatModel || value.ui_state?.scientist_chat_model || ''
  return {
    ...value,
    primary_model: primary,
    autonomous_fallback: uniqueModels(value.autonomous_fallback ?? [])
      .filter((model) => available.has(model) && model !== primary),
    chat_fallback: uniqueModels(value.chat_fallback ?? [])
      .filter((model) => available.has(model) && model !== selectedChatModel),
    models: [...(value.models ?? [])],
  }
}

function ModelPicker({
  label,
  models,
  selected,
  excluded,
  ordered = false,
  disabled = false,
  emptyHint,
  onChange,
}: ModelPickerProps) {
  const [query, setQuery] = useState('')
  const excludedSet = useMemo(
    () => new Set([...selected, ...excluded]),
    [selected, excluded],
  )
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return models
      .filter((model) => !excludedSet.has(model))
      .filter((model) => !needle || model.toLowerCase().includes(needle))
      .slice(0, 12)
  }, [models, excludedSet, query])

  function add(model: string) {
    if (!models.includes(model) || excludedSet.has(model)) return
    onChange(uniqueModels([...selected, model]))
    setQuery('')
  }

  function remove(model: string) {
    onChange(selected.filter((item) => item !== model))
  }

  function move(index: number, offset: number) {
    const target = index + offset
    if (target < 0 || target >= selected.length) return
    const next = [...selected]
    const [item] = next.splice(index, 1)
    next.splice(target, 0, item)
    onChange(next)
  }

  return (
    <div className="model-picker">
      <span className="field-label">{label}</span>
      <div className="model-chip-list" aria-label={label + ' selected models'}>
        {selected.map((model, index) => (
          <span className="model-chip" key={model}>
            <span>{model}</span>
            {ordered && (
              <>
                <ActionButton
                  type="button"
                  aria-label={'Move ' + model + ' up'}
                  onClick={() => move(index, -1)}
                  disabled={disabled || index === 0}
                  blockedReason={disabled ? 'Wait for the provider settings operation to finish.' : 'This model is already first in the fallback order.'}
                >
                  ↑
                </ActionButton>
                <ActionButton
                  type="button"
                  aria-label={'Move ' + model + ' down'}
                  onClick={() => move(index, 1)}
                  disabled={disabled || index === selected.length - 1}
                  blockedReason={disabled ? 'Wait for the provider settings operation to finish.' : 'This model is already last in the fallback order.'}
                >
                  ↓
                </ActionButton>
              </>
            )}
            <ActionButton
              type="button"
              aria-label={'Remove ' + model}
              onClick={() => remove(model)}
              disabled={disabled}
              blockedReason="Wait for the provider settings operation to finish."
            >
              ×
            </ActionButton>
          </span>
        ))}
        {selected.length === 0 && <span className="model-empty">{emptyHint}</span>}
      </div>
      <div className="model-search">
        <input
          aria-label={label + ' search'}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={models.length ? 'Search discovered models…' : 'Connect provider first'}
          disabled={disabled || models.length === 0}
          autoComplete="off"
        />
        {query.trim() && !disabled && models.length > 0 && (
          <div className="model-suggestions" role="listbox" aria-label={label + ' matches'}>
            {matches.map((model) => (
              <button
                type="button"
                role="option"
                aria-selected="false"
                key={model}
                onClick={() => add(model)}
              >
                {model}
              </button>
            ))}
            {matches.length === 0 && (
              <span className="model-no-match">No discovered model matches.</span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default function SettingsPage({ chatModel = '', onSaved }: SettingsPageProps) {
  const [catalog, setCatalog] = useState<ProviderCatalogItem[]>([])
  const [saved, setSaved] = useState<ProviderSettings | null>(null)
  const [draft, setDraft] = useState<ProviderSettings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [connection, setConnection] = useState<ConnectionResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [busyAction, setBusyAction] = useState<'connect' | 'save' | ''>('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  async function readJson<T>(url: string, init?: RequestInit): Promise<T> {
    const response = await fetch(url, init)
    if (!response.ok) {
      let detail = 'HTTP ' + response.status
      try {
        const body = await response.json()
        detail = String(body.detail ?? detail)
      } catch {
        // Keep HTTP fallback.
      }
      if (/^[A-Z0-9_:.-]+$/.test(detail) || /[A-Z0-9]+_[A-Z0-9_]+/.test(detail)) {
        detail = 'The request could not be completed. Review the current configuration or retained evidence.'
      }
      throw new Error(detail)
    }
    return response.json()
  }

  function applySettings(value: ProviderSettings) {
    const validated = sanitizeSettings(value, chatModel)
    setSaved(validated)
    setDraft(validated)
    setApiKey('')
  }

  useEffect(() => {
    let cancelled = false
    Promise.all([
      readJson<ProviderCatalogItem[]>('/api/scientist/provider-catalog'),
      readJson<ProviderSettings>('/api/scientist/provider-settings'),
    ])
      .then(([providerCatalog, settings]) => {
        if (cancelled) return
        const validated = sanitizeSettings(settings)
        setCatalog(providerCatalog)
        setSaved(validated)
        setDraft(validated)
        setApiKey('')
      })
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message)
      })
    return () => { cancelled = true }
  }, [])

  const providerSpec = useMemo(
    () => catalog.find((item) => item.key === draft?.provider_key) ?? null,
    [catalog, draft?.provider_key],
  )

  const requiresApiKey = Boolean(
    providerSpec?.requires_api_key
    && !(draft?.provider_key === 'custom' && draft.auth_mode === 'NONE'),
  )

  function chooseProvider(key: string) {
    const spec = catalog.find((item) => item.key === key)
    if (!spec || !draft) return
    setDraft({
      ...draft,
      provider_key: key,
      base_url: spec.default_base_url,
      auth_mode: key === 'ollama' ? 'OLLAMA_LOCAL' : 'API_KEY',
      primary_model: '',
      autonomous_fallback: [],
      chat_fallback: [],
      models: [],
    })
    setApiKey('')
    setConnection(null)
    setMessage('')
    setError('')
  }

  function requestBody() {
    if (!draft) return null
    const currentChatModel = chatModel || draft.ui_state?.scientist_chat_model || ''
    return {
      provider_key: draft.provider_key,
      base_url: draft.base_url,
      auth_mode: draft.auth_mode,
      timeout_sec: Number(draft.timeout_sec),
      primary_model: draft.primary_model,
      autonomous_fallback: uniqueModels(draft.autonomous_fallback)
        .filter((model) => model !== draft.primary_model && draft.models.includes(model)),
      chat_fallback: uniqueModels(draft.chat_fallback)
        .filter((model) => model !== currentChatModel && draft.models.includes(model)),
      models: draft.models,
      ...(apiKey ? { api_key: apiKey } : {}),
    }
  }

  async function connect() {
    const current = draft
    const body = requestBody()
    if (!current || !body || busy) return
    setBusy(true)
    setBusyAction('connect')
    setError('')
    setMessage('')
    setConnection(null)
    try {
      const result = await readJson<ConnectionResult>(
        '/api/scientist/provider-settings/connect',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
      )
      setConnection(result)
      const available = new Set(result.models)
      const primary = available.has(current.primary_model)
        ? current.primary_model
        : result.models[0] ?? ''
      const selectedChatModel = chatModel || current.ui_state?.scientist_chat_model || ''
      const autonomous = current.autonomous_fallback
        .filter((model) => available.has(model) && model !== primary)
      const chat = current.chat_fallback
        .filter((model) => available.has(model) && model !== selectedChatModel)
      const removed = (
        current.autonomous_fallback.length - autonomous.length
        + current.chat_fallback.length - chat.length
      )
      if (removed > 0) {
        setMessage('Connection refreshed · removed ' + removed + ' stale fallback selection' + (removed === 1 ? '' : 's'))
      }
      setDraft({
        ...current,
        models: result.models,
        primary_model: primary,
        autonomous_fallback: autonomous,
        chat_fallback: chat,
      })
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
      setBusyAction('')
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    const body = requestBody()
    if (!body || busy) return
    setBusy(true)
    setBusyAction('save')
    setError('')
    setMessage('')
    try {
      const result = await readJson<ProviderSettings>(
        '/api/scientist/provider-settings',
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
      )
      applySettings(result)
      onSaved?.(result)
      setMessage('Saved')
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
      setBusyAction('')
    }
  }

  if (!draft) {
    return (
      <>
        <header className="page-head">
          <div>
            <p className="eyebrow">MAX · Configuration</p>
            <h1>Settings</h1>
          </div>
        </header>
        {error
          ? <p role="alert" className="error">Settings: {error}</p>
          : <p role="status" className="loading">Loading settings…</p>}
      </>
    )
  }

  const selectedChatModel = chatModel || draft.ui_state?.scientist_chat_model || ''
  const autonomousExcluded = [draft.primary_model]
  const chatExcluded = selectedChatModel ? [selectedChatModel] : []
  const visibleChatFallback = draft.chat_fallback.filter(
    (model) => model !== selectedChatModel && draft.models.includes(model),
  )

  return (
    <>
      <header className="page-head">
        <div>
          <p className="eyebrow">MAX · Configuration</p>
          <h1>Settings</h1>
        </div>
      </header>

      <section className="settings-card" aria-labelledby="scientist-provider-settings">
        <div className="settings-title">
          <div>
            <h2 id="scientist-provider-settings">Scientist Provider</h2>
            <p className="subtle">
              Shared inference route for bounded Scientist workloads. Credentials never return to the UI.
            </p>
          </div>
          <span className="settings-health">
            Route health · {ownerStatus(saved?.status)}
          </span>
        </div>

        <form className="provider-settings-form" onSubmit={save}>
          <section className="settings-section settings-connection" aria-labelledby="settings-connection">
            <h3 id="settings-connection">Connection</h3>
            <div className="connection-grid">
              <label className="connection-provider">
                Provider
                <select
                  aria-label="Provider"
                  value={draft.provider_key}
                  onChange={(event) => chooseProvider(event.target.value)}
                  disabled={busy}
                >
                  {catalog.map((provider) => (
                    <option key={provider.key} value={provider.key}>
                      {provider.display_name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="connection-endpoint">
                Endpoint
                <input
                  aria-label="Endpoint"
                  value={draft.base_url}
                  onChange={(event) => setDraft({ ...draft, base_url: event.target.value })}
                  disabled={busy || (!!providerSpec && draft.provider_key !== 'custom')}
                />
              </label>

              {draft.provider_key === 'custom' && (
                <label className="connection-auth">
                  Authentication
                  <select
                    aria-label="Authentication"
                    value={draft.auth_mode}
                    onChange={(event) => setDraft({ ...draft, auth_mode: event.target.value })}
                    disabled={busy}
                  >
                    <option value="API_KEY">API key</option>
                    <option value="NONE">None · localhost only</option>
                  </select>
                </label>
              )}

              <label className="connection-credential">
                API Key
                {requiresApiKey ? (
                  <input
                    aria-label="API Key"
                    type="password"
                    autoComplete="off"
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder={
                      saved?.credential_status === 'CONFIGURED'
                        ? 'Configured · leave blank to keep'
                        : 'Required'
                    }
                    disabled={busy}
                  />
                ) : (
                  <input aria-label="API Key" value="Not required" disabled />
                )}
              </label>

              <label className="connection-timeout">
                Timeout
                <input
                  aria-label="Timeout"
                  type="number"
                  min={1}
                  max={120}
                  value={draft.timeout_sec}
                  onChange={(event) => setDraft({
                    ...draft,
                    timeout_sec: Number(event.target.value),
                  })}
                  disabled={busy}
                />
              </label>
            </div>

            <div className="settings-connect-row">
              <ActionButton type="button" onClick={connect} disabled={busy} blockedReason="Wait for the current provider operation to finish.">
                <ActionProgress active={busyAction === 'connect'} idle="Connect" pending="Connecting…" />
              </ActionButton>
              <span>
                {connection
                  ? 'Connected · ' + connection.model_count + ' models · ' + connection.latency_ms + ' ms'
                  : 'Connect to discover available models'}
              </span>
            </div>
          </section>

          <section className="settings-section settings-routing" aria-labelledby="settings-routing">
            <h3 id="settings-routing">Model routing</h3>
            <div className="routing-grid">
              <label className="routing-primary">
                Primary model
                <select
                  aria-label="Primary model"
                  value={draft.primary_model}
                  onChange={(event) => {
                    const primary = event.target.value
                    setDraft({
                      ...draft,
                      primary_model: primary,
                      autonomous_fallback: draft.autonomous_fallback.filter((model) => model !== primary),
                    })
                  }}
                  disabled={busy || draft.models.length === 0}
                >
                  {!draft.primary_model && <option value="">Connect first</option>}
                  {draft.models.map((model) => (
                    <option key={model} value={model}>{model}</option>
                  ))}
                </select>
              </label>

              <ModelPicker
                label="Autonomous fallback · ordered"
                models={draft.models}
                selected={draft.autonomous_fallback}
                excluded={autonomousExcluded}
                ordered
                disabled={busy}
                emptyHint="No autonomous fallback configured."
                onChange={(models) => setDraft({ ...draft, autonomous_fallback: models })}
              />

              <ModelPicker
                label="Scientist Chat fallback · explicit"
                models={draft.models}
                selected={visibleChatFallback}
                excluded={chatExcluded}
                disabled={busy}
                emptyHint="Empty · selected Chat model never falls back."
                onChange={(models) => setDraft({ ...draft, chat_fallback: models })}
              />
            </div>
          </section>

          <section className="settings-section settings-actions-section" aria-labelledby="settings-actions">
            <h3 id="settings-actions">Actions</h3>
            <div className="settings-actions">
              <ActionButton type="submit" className="settings-save" disabled={busy || !draft.primary_model} blockedReason={busy ? 'Wait for the current provider operation to finish.' : 'Connect and select a primary model before saving settings.'}>
                <ActionProgress active={busyAction === 'save'} idle="Save" pending="Saving…" />
              </ActionButton>
              <ActionButton
                type="button"
                disabled={busy || !saved}
                blockedReason={busy ? 'Wait for the current provider operation to finish.' : 'Settings are not loaded yet; there is no saved configuration to restore.'}
                onClick={() => saved && applySettings(saved)}
              >
                Cancel
              </ActionButton>
            </div>
          </section>
        </form>

        {saved?.credential_status && (
          <p className="subtle">Credential · {ownerStatus(saved.credential_status)}</p>
        )}
        {saved?.recovered_from_backup && (
          <p className="notice" role="status">Settings recovered from backup.</p>
        )}
        {message && <p className="provider-success" role="status">{message}</p>}
        {error && <p className="error" role="alert">Settings: {error}</p>}
      </section>
    </>
  )
}
