import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Pagination } from './DataTable'

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
})
