import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ActionButton, ActionProgress } from './ActionControls'

describe('shared action feedback', () => {
  it('explains a blocked action in a focusable popout and does not submit it', () => {
    const onClick = vi.fn()
    render(
      <ActionButton disabled blockedReason="Select a retained Backtest first." onClick={onClick}>
        Delete Backtest
      </ActionButton>,
    )

    expect(screen.getByRole('button', { name: 'Delete Backtest' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete Backtest' })).not.toHaveAttribute('title')
    expect(screen.getByRole('tooltip')).toHaveTextContent('Select a retained Backtest first.')
    fireEvent.mouseEnter(screen.getByRole('button', { name: 'Delete Backtest' }).parentElement!)
    expect(document.body.querySelector('.action-blocked-popout-portal')).toHaveTextContent('Select a retained Backtest first.')
    fireEvent.click(screen.getByRole('button', { name: 'Delete Backtest' }))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('shows an accessible spinner and pending text while an action is running', () => {
    render(
      <ActionButton>
        <ActionProgress active idle="Run Backtest" pending="Running in MT5…" />
      </ActionButton>,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Running in MT5…')
    expect(document.querySelector('.button-spinner')).toBeInTheDocument()
  })

  it('opens the blocked-action popout from keyboard focus', () => {
    render(
      <ActionButton disabled blockedReason="Wait for the current operation to finish.">
        Promote to Champion
      </ActionButton>,
    )

    const wrapper = screen.getByRole('button', { name: 'Promote to Champion' }).parentElement!
    expect(wrapper).toHaveAttribute('tabindex', '0')
    fireEvent.focus(wrapper)
    expect(Array.from(document.body.querySelectorAll('.action-blocked-popout-portal'))
      .some((popout) => popout.textContent?.includes('Wait for the current operation to finish.'))).toBe(true)
  })
})
