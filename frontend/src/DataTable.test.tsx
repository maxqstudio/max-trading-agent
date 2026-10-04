import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Pagination, SortHeader } from './DataTable'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('shared Pagination', () => {
  it('renders the three-zone page contract and accessible compact navigation', () => {
    const onPage = vi.fn()
    const onPageSize = vi.fn()
    render(
      <Pagination
        page={1}
        pages={6}
        pageSize={25}
        total={137}
        onPage={onPage}
        onPageSize={onPageSize}
      />,
    )

    expect(screen.getByText('Rows per page:')).toBeInTheDocument()
    const size = screen.getByLabelText('Rows per page')
    expect(size).toHaveValue('25')
    expect(within(size).getAllByRole('option').map((option) => option.textContent)).toEqual([
      '25',
      '50',
      '100',
    ])
    expect(screen.getByText('1 / 6')).toBeInTheDocument()
    expect(screen.getByText('Showing 1–25 of 137')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Previous' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
  })

  it('renders the final-page visible range', () => {
    render(
      <Pagination
        page={6}
        pages={6}
        pageSize={25}
        total={137}
        onPage={vi.fn()}
        onPageSize={vi.fn()}
      />,
    )

    expect(screen.getByText('6 / 6')).toBeInTheDocument()
    expect(screen.getByText('Showing 126–137 of 137')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
  })

  it('retains the footer for an empty API page', () => {
    render(
      <Pagination
        page={1}
        pages={1}
        pageSize={25}
        total={0}
        onPage={vi.fn()}
        onPageSize={vi.fn()}
      />,
    )

    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(screen.getByText('Showing 0 of 0')).toBeInTheDocument()
    expect(screen.getByLabelText('Rows per page')).toHaveValue('25')
  })

  it('shows a spinner and a reason while pagination is blocked by the pending request', () => {
    const onPage = vi.fn()
    const { rerender } = render(
      <Pagination
        page={1}
        pages={3}
        pageSize={25}
        total={62}
        onPage={onPage}
        onPageSize={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(onPage).toHaveBeenCalledWith(2, 'next')
    rerender(
      <Pagination
        page={1}
        pages={3}
        pageSize={25}
        total={62}
        onPage={onPage}
        onPageSize={vi.fn()}
        loading
        pendingDirection="next"
      />,
    )

    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Loading next page')
    expect(document.querySelector('.pagination-center .button-spinner')).toBeInTheDocument()
    expect(screen.getAllByRole('tooltip').some((tip) => tip.textContent?.includes('Wait for the current table refresh'))).toBe(true)
  })

  it('shows sort progress and explains why a second sort cannot start', () => {
    const onSort = vi.fn()
    const props = { label: 'Net Profit', field: 'net_profit', sort: 'created', order: 'desc' as const, onSort }
    const { rerender } = render(<table><thead><tr><SortHeader {...props} /></tr></thead></table>)

    fireEvent.click(screen.getByRole('button', { name: 'Net Profit' }))
    expect(onSort).toHaveBeenCalledWith('net_profit', 'desc')
    rerender(<table><thead><tr><SortHeader {...props} loading pending /></tr></thead></table>)

    expect(screen.getByRole('button', { name: 'Sorting Net Profit…' })).toBeDisabled()
    expect(document.querySelector('.button-spinner')).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent('Wait for the current table refresh to finish.')
  })
})
