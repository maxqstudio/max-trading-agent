import { describe, expect, it, vi } from 'vitest'
import { fetchOnnxWorkspace, parseOnnxWorkspace } from './onnxApi'

describe('ONNX read-only API contract', () => {
  it('rejects unknown backend status instead of inventing a frontend state', () => {
    expect(() => parseOnnxWorkspace({
      contract_version: '1.0',
      source: 'BACKEND_ONNX_01_SKELETON',
      operational_state: {
        status: 'READY',
        availability: 'NOT_IMPLEMENTED',
        persisted: false,
        cycle_id: null,
        reason: 'untrusted status',
      },
    })).toThrow(/invalid operational state/i)
  })

  it('uses only GET and preserves the application recovery response', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(
      JSON.stringify({ detail: 'RECOVERY_REQUIRED: application operations are disabled' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } },
    ))

    await expect(fetchOnnxWorkspace(undefined, fetcher))
      .rejects.toThrow(/application recovery is required/i)
    expect(fetcher).toHaveBeenCalledWith('/api/v1/onnx/workspace', expect.objectContaining({ method: 'GET' }))
  })
})
