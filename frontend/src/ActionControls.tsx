import { useId, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

type ActionButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  blockedReason?: string
}

export function ActionButton({
  blockedReason,
  children,
  disabled = false,
  ...buttonProps
}: ActionButtonProps) {
  const tooltipId = useId()
  const wrapperRef = useRef<HTMLSpanElement>(null)
  const [tooltipPosition, setTooltipPosition] = useState<{ left: number; top: number } | null>(null)
  const reason = blockedReason?.trim() || 'This action is unavailable in the current state.'
  const blocked = Boolean(disabled)
  const showBlockedReason = () => {
    if (!blocked || !wrapperRef.current) return
    const bounds = wrapperRef.current.getBoundingClientRect()
    setTooltipPosition({
      left: Math.max(8, Math.min(bounds.left, window.innerWidth - 360)),
      top: Math.min(bounds.bottom + 6, window.innerHeight - 90),
    })
  }

  return (
    <span
      ref={wrapperRef}
      className="action-control-wrap"
      tabIndex={blocked ? 0 : undefined}
      aria-label={blocked ? 'Action blocked: ' + reason : undefined}
      aria-describedby={blocked ? tooltipId : undefined}
      onMouseEnter={showBlockedReason}
      onMouseLeave={() => setTooltipPosition(null)}
      onFocus={showBlockedReason}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setTooltipPosition(null)
        }
      }}
    >
      <button
        {...buttonProps}
        disabled={disabled}
        title={blocked ? undefined : buttonProps.title}
        aria-describedby={blocked ? tooltipId : buttonProps['aria-describedby']}
      >
        {children}
      </button>
      {blocked && (
        <span id={tooltipId} className="action-blocked-popout" role="tooltip">
          {reason}
        </span>
      )}
      {blocked && tooltipPosition && typeof document !== 'undefined' && createPortal(
        <span
          className="action-blocked-popout action-blocked-popout-portal"
          aria-hidden="true"
          style={{ left: tooltipPosition.left, top: tooltipPosition.top }}
        >
          {reason}
        </span>,
        document.body,
      )}
    </span>
  )
}

export function ActionProgress({
  active,
  idle,
  pending,
}: {
  active: boolean
  idle: ReactNode
  pending: ReactNode
}) {
  return (
    <span className="action-progress" role={active ? 'status' : undefined} aria-live={active ? 'polite' : undefined}>
      {active && <span className="button-spinner" aria-hidden="true" />}
      <span>{active ? pending : idle}</span>
    </span>
  )
}
