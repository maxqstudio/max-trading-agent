import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type ScientistStatus = {
  knowledge_status: string
  provider_status: string
  provider: string
  model: string
}

type Thread = {
  thread_id: string
  created_utc: string
  updated_utc: string
  title: string
}

type ProviderProvenance = {
  actual_model?: string | null
  configured_model?: string | null
}

type Message = {
  message_id: string
  thread_id: string
  sequence: number
  role: 'user' | 'assistant'
  content: string
  created_utc: string
  classification?: string | null
  evidence_refs?: string[]
  provider_provenance?: ProviderProvenance
}

type EvidenceItem = {
  title: string
  facts: unknown
}

type SendResponse = {
  status: string
  message: Message
  evidence?: Record<string, EvidenceItem>
}

type ClearResponse = {
  thread: Thread
  domain_authority_unchanged: boolean
}

type ScientistDrawerProps = {
  open: boolean
  models: string[]
  selectedModel: string
  contextScope: string
  statusOverride?: Partial<ScientistStatus>
  onClose: () => void
  onModelChange: (model: string) => void
  onContextChange: (context: string) => void
}

const CONTEXTS = [
  { value: 'AUTO', label: 'Automatic' },
  { value: 'STRATEGY', label: 'Strategy' },
  { value: 'OPTIMIZER', label: 'Optimizer' },
  { value: 'CHALLENGERS', label: 'Strategy Challengers' },
  { value: 'CHAMPION', label: 'Strategy Champion' },
  { value: 'RESEARCH', label: 'Research' },
  { value: 'PROJECT CONTRACT', label: 'Project authority' },
]

function contextLabel(value: string) {
  return CONTEXTS.find((context) => context.value === value)?.label ?? 'Automatic'
}

function providerStatusLabel(value?: string) {
  const labels: Record<string, string> = {
    READY: 'Ready',
    ROUTE_UNAVAILABLE: 'Unavailable',
    PROVIDER_UNAVAILABLE: 'Unavailable',
    MISSING_CREDENTIAL: 'Credential required',
    UNCONFIGURED: 'Not configured',
  }
  if (!value) return 'Unavailable'
  return labels[value] ?? 'Review required'
}

function newRequestId() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return 'SCI-REQ-' + crypto.randomUUID()
  }
  return 'SCI-REQ-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2)
}

function assistantModel(message: Message, selectedModel: string) {
  return (
    message.provider_provenance?.actual_model
    || message.provider_provenance?.configured_model
    || selectedModel
    || 'Scientist'
  )
}

function AssistantMarkdown({ content }: { content: string }) {
  return (
    <div className="message-content markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a({ href, children }) {
            const external = /^https?:\/\//i.test(href ?? '')
            return (
              <a
                href={href}
                target={external ? '_blank' : undefined}
                rel={external ? 'noopener noreferrer' : undefined}
              >
                {children}
              </a>
            )
          },
          table({ children }) {
            return (
              <div className="markdown-table-wrap">
                <table>{children}</table>
              </div>
            )
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function ThinkingRow() {
  return (
    <div className="thinking-row" role="status" aria-label="Scientist is reviewing committed evidence">
      <strong>Scientist</strong>
      <span>Scientist is reviewing committed evidence</span>
      <span className="thinking-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
    </div>
  )
}

export default function ScientistPage({
  open,
  models,
  selectedModel,
  contextScope,
  statusOverride,
  onClose,
  onModelChange,
  onContextChange,
}: ScientistDrawerProps) {
  const [status, setStatus] = useState<ScientistStatus | null>(null)
  const [thread, setThread] = useState<Thread | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [evidence, setEvidence] = useState<Record<string, EvidenceItem>>({})
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [chatError, setChatError] = useState('')
  const [optimisticUser, setOptimisticUser] = useState<Message | null>(null)
  const historyRef = useRef<HTMLDivElement | null>(null)
  const nearBottomRef = useRef(true)

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

  async function refreshMessages(threadId: string) {
    setMessages(
      await readJson<Message[]>('/api/scientist/threads/' + threadId + '/messages'),
    )
  }

  function updateNearBottom() {
    const node = historyRef.current
    if (!node) return
    nearBottomRef.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 80
  }

  function scrollToLatestIfAppropriate() {
    if (!nearBottomRef.current) return
    requestAnimationFrame(() => {
      const node = historyRef.current
      if (!node || !nearBottomRef.current) return
      node.scrollTop = node.scrollHeight
    })
  }

  useEffect(() => {
    scrollToLatestIfAppropriate()
  }, [messages, optimisticUser, sending, chatError])

  useEffect(() => {
    let cancelled = false
    Promise.all([
      readJson<ScientistStatus>('/api/scientist/status'),
      readJson<Thread>('/api/scientist/chat'),
    ])
      .then(([scientistStatus, activeThread]) => {
        if (cancelled) return
        setStatus(scientistStatus)
        setThread(activeThread)
        readJson<Message[]>('/api/scientist/threads/' + activeThread.thread_id + '/messages')
          .then((initialMessages) => {
            if (!cancelled) setMessages(initialMessages)
          })
          .catch((reason: Error) => {
            if (!cancelled) setLoadError(reason.message)
          })
      })
      .catch((reason: Error) => {
        if (!cancelled) setLoadError(reason.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  async function clearChat() {
    if (sending || clearing) return
    setClearing(true)
    setLoadError('')
    setChatError('')
    try {
      const result = await readJson<ClearResponse>('/api/scientist/chat/clear', {
        method: 'POST',
      })
      setThread(result.thread)
      setMessages([])
      setEvidence({})
      setOptimisticUser(null)
      setDraft('')
      nearBottomRef.current = true
    } catch (reason) {
      setChatError((reason as Error).message)
    } finally {
      setClearing(false)
    }
  }

  async function send(event: FormEvent) {
    event.preventDefault()
    if (!thread || !draft.trim() || sending || clearing || !selectedModel) return
    const content = draft.trim()
    const requestId = newRequestId()
    const activeThreadId = thread.thread_id
    const pendingUser: Message = {
      message_id: 'optimistic-' + requestId,
      thread_id: activeThreadId,
      sequence: Number.MAX_SAFE_INTEGER,
      role: 'user',
      content,
      created_utc: new Date().toISOString(),
    }

    setSending(true)
    setChatError('')
    setOptimisticUser(pendingUser)
    setDraft('')

    try {
      const result = await readJson<SendResponse>(
        '/api/scientist/threads/' + activeThreadId + '/messages',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            request_id: requestId,
            content,
            model: selectedModel,
            context_scope: contextScope,
          }),
        },
      )
      if (result.evidence) {
        setEvidence((current) => ({ ...current, ...result.evidence }))
      }
      await refreshMessages(activeThreadId)
      setOptimisticUser(null)
      setSending(false)
    } catch (reason) {
      await refreshMessages(activeThreadId).catch(() => undefined)
      setOptimisticUser(null)
      setSending(false)
      setChatError((reason as Error).message)
    }
  }

  const effectiveStatus = status
    ? { ...status, ...(statusOverride ?? {}) }
    : status
  const providerReady = effectiveStatus?.provider_status === 'READY'
  const knowledgeReady = effectiveStatus?.knowledge_status === 'READY'
  const visibleMessages = optimisticUser ? [...messages, optimisticUser] : messages

  return (
    <aside
      className={'scientist-drawer ' + (open ? 'scientist-drawer-open' : 'scientist-drawer-closed')}
      aria-label="Scientist chat drawer"
      aria-hidden={!open}
    >
      <div className="drawer-head">
        <div>
          <strong>Scientist</strong>
          <span>Read-only</span>
        </div>
        <button type="button" onClick={onClose} aria-label="Hide Scientist">×</button>
      </div>

      <div className="drawer-controls">
        <label>
          Model
          <select
            aria-label="Scientist model"
            value={selectedModel}
            onChange={(event) => onModelChange(event.target.value)}
          >
            {!models.includes(selectedModel) && selectedModel && (
              <option value={selectedModel}>{selectedModel}</option>
            )}
            {models.map((model) => (
              <option key={model} value={model}>{model}</option>
            ))}
          </select>
        </label>
        <label>
          Context
          <select
            aria-label="Scientist context"
            value={contextScope}
            onChange={(event) => onContextChange(event.target.value)}
          >
            {CONTEXTS.map((context) => (
              <option key={context.value} value={context.value}>{context.label}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="drawer-chat-actions">
        <span className="single-chat-label">Scientist Chat</span>
        <button
          type="button"
          className="drawer-clear-chat"
          onClick={clearChat}
          disabled={sending || clearing}
        >
          {clearing ? 'Clearing…' : 'Clear Chat'}
        </button>
      </div>

      {!loading && effectiveStatus && !providerReady && (
        <p className="notice drawer-note" role="status">
          Provider unavailable: {providerStatusLabel(effectiveStatus.provider_status)}
        </p>
      )}

      <div
        className="drawer-messages"
        data-testid="scientist-history"
        aria-live="polite"
        ref={historyRef}
        onScroll={updateNearBottom}
      >
        {loading && (
          <div role="status" className="hydration-row">
            <span className="hydration-dot" aria-hidden="true" />
            Loading Scientist…
          </div>
        )}
        {!loading && loadError && (
          <div role="alert" className="chat-system-error">Scientist: {loadError}</div>
        )}
        {!loading && thread && visibleMessages.length === 0 && !sending && (
          <p className="conversation-empty">Ask Scientist about committed MAX authority.</p>
        )}

        {visibleMessages.map((message) => (
          <article key={message.message_id} className={'message message-' + message.role}>
            <div className="message-meta">
              <strong>{message.role === 'user' ? 'You' : 'Scientist'}</strong>
              {message.role === 'assistant' && (
                <span className="message-model">
                  {assistantModel(message, selectedModel)}
                </span>
              )}
            </div>

            {message.role === 'assistant'
              ? <AssistantMarkdown content={message.content} />
              : <div className="message-content user-message-content">{message.content}</div>}

            {message.role === 'assistant' && (message.evidence_refs?.length ?? 0) > 0 && (
              <details className="evidence-list">
                <summary>Evidence · {message.evidence_refs!.length}</summary>
                <ul>
                  {message.evidence_refs!.map((ref) => (
                    <li key={ref}>
                      <code>{ref}</code>
                      {evidence[ref]?.title && <span> — {evidence[ref].title}</span>}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </article>
        ))}

        {sending && <ThinkingRow />}

        {!sending && chatError && (
          <div role="alert" className="chat-system-error">
            Scientist request failed: {chatError}
          </div>
        )}
      </div>

      <form className="drawer-composer" onSubmit={send}>
        <textarea
          aria-label="Message Scientist"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={3}
          maxLength={8000}
          placeholder="Ask Scientist…"
          disabled={sending || clearing || !providerReady || !knowledgeReady || !thread}
        />
        <div className="composer-actions">
          <span>{contextLabel(contextScope)}</span>
          <button
            type="submit"
            disabled={
              sending
              || clearing
              || !providerReady
              || !knowledgeReady
              || !thread
              || !selectedModel
              || !draft.trim()
            }
          >
            Send
          </button>
        </div>
      </form>
    </aside>
  )
}
