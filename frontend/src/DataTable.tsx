import type { ReactNode } from 'react'

export type SortOrder = 'asc' | 'desc'

export function SortHeader({
  label,
  field,
  sort,
  order,
  onSort,
}: {
  label: string
  field: string
  sort: string
  order: SortOrder
  onSort: (field: string, order: SortOrder) => void
}) {
  const active = sort === field
  const next: SortOrder = active && order === 'desc' ? 'asc' : 'desc'
  return (
    <th aria-sort={active ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button
        type="button"
        className="sort-header"
        onClick={() => onSort(field, next)}
      >
        {label}<span aria-hidden="true">{active ? (order === 'asc' ? ' ↑' : ' ↓') : ''}</span>
      </button>
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
}: {
  page: number
  pages: number
  pageSize: number
  total: number
  onPage: (page: number) => void
  onPageSize: (size: number) => void
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
        <button
          type="button"
          aria-label="Previous page"
          disabled={currentPage <= 1}
          onClick={() => onPage(currentPage - 1)}
        >
          <span aria-hidden="true">‹</span>
        </button>
        <span className="pagination-page-count">{currentPage} / {pageCount}</span>
        <button
          type="button"
          aria-label="Next page"
          disabled={currentPage >= pageCount}
          onClick={() => onPage(currentPage + 1)}
        >
          <span aria-hidden="true">›</span>
        </button>
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
