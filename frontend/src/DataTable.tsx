import type { ReactNode } from 'react'
import { ActionButton, ActionProgress } from './ActionControls'

export type SortOrder = 'asc' | 'desc'

export function SortHeader({
  label,
  field,
  sort,
  order,
  onSort,
  loading = false,
  blockedReason = 'Wait for the current table refresh to finish.',
  pending = false,
}: {
  label: string
  field: string
  sort: string
  order: SortOrder
  onSort: (field: string, order: SortOrder) => void
  loading?: boolean
  blockedReason?: string
  pending?: boolean
}) {
  const active = sort === field
  const next: SortOrder = active && order === 'desc' ? 'asc' : 'desc'

  return (
    <th aria-sort={active ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <ActionButton
        type="button"
        className="sort-header"
        disabled={loading}
        blockedReason={blockedReason}
        onClick={() => {
          onSort(field, next)
        }}
      >
        <ActionProgress
          active={loading && pending}
          idle={<>{label}<span aria-hidden="true">{active ? (order === 'asc' ? ' ↑' : ' ↓') : ''}</span></>}
          pending={<span className="visually-hidden">Sorting {label}…</span>}
        />
      </ActionButton>
    </th>
  )
}

export function Pagination({
  page,
  pages,
  pageSize,
  total,
  onPage,
  onPageSize,
  loading = false,
  blockedReason = 'Wait for the current table refresh to finish.',
  pendingDirection = '',
}: {
  page: number
  pages: number
  pageSize: number
  total: number
  onPage: (page: number, direction: 'previous' | 'next') => void
  onPageSize: (size: number) => void
  loading?: boolean
  blockedReason?: string
  pendingDirection?: 'previous' | 'next' | ''
}) {
  const pageCount = Math.max(1, pages)
  const currentPage = Math.min(Math.max(1, page), pageCount)
  const start = total === 0
    ? 0
    : Math.min(total, (currentPage - 1) * pageSize + 1)
  const end = total === 0 ? 0 : Math.min(total, currentPage * pageSize)
  const range = total === 0
    ? 'Showing 0 of 0'
    : `Showing ${start}–${end} of ${total}`

  return (
    <div className="pagination-controls" aria-label="Pagination">
      <div className="pagination-left">
        <label className="pagination-page-size">
          <span>Rows per page:</span>
          <select
            aria-label="Rows per page"
            value={pageSize}
            onChange={(event) => onPageSize(Number(event.target.value))}
          >
            {[25, 50, 100].map((size) => (
              <option key={size} value={size}>{size}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="pagination-center">
        <ActionButton
          type="button"
          aria-label="Previous page"
          disabled={loading || currentPage <= 1}
          blockedReason={loading ? blockedReason : 'You are already on the first page.'}
          onClick={() => onPage(currentPage - 1, 'previous')}
        >
          <ActionProgress
            active={loading && pendingDirection === 'previous'}
            idle={<span aria-hidden="true">‹</span>}
            pending={<span className="visually-hidden">Loading previous page…</span>}
          />
        </ActionButton>
        <span className="pagination-page-count">{currentPage} / {pageCount}</span>
        <ActionButton
          type="button"
          aria-label="Next page"
          disabled={loading || currentPage >= pageCount}
          blockedReason={loading ? blockedReason : 'You are already on the last page.'}
          onClick={() => onPage(currentPage + 1, 'next')}
        >
          <ActionProgress
            active={loading && pendingDirection === 'next'}
            idle={<span aria-hidden="true">›</span>}
            pending={<span className="visually-hidden">Loading next page…</span>}
          />
        </ActionButton>
      </div>
      <div className="pagination-right">{range}</div>
    </div>
  )
}

export function TableState({
  loading,
  error,
  empty,
  children,
}: {
  loading: boolean
  error: string
  empty: boolean
  children: ReactNode
}) {
  if (loading) return <p role="status" className="loading">Loading…</p>
  if (error) return <p role="alert" className="error">{error}</p>
  if (empty) return <p className="empty-state">No rows match the current view.</p>
  return <>{children}</>
}
