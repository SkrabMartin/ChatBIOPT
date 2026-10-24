import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { jsonResponse, mockApi, renderRoute } from '../../test/utils'
import { EvaluationsPage } from './EvaluationsPage'

const summary = {
  id: 'ev-1',
  kind: 'answers',
  dataset: 'bioresearch-answers-test',
  dataset_version: '0.1',
  dataset_sha256: 'f'.repeat(64),
  split: 'test',
  git_commit: 'abc1234',
  summary: {},
  created_at: '2026-10-24T10:00:00Z',
}

describe('EvaluationsPage', () => {
  it('shows configurations side by side, marks the best value per metric and flags failures', async () => {
    mockApi({
      'GET /api/v1/evaluations': () => jsonResponse([summary]),
      'GET /api/v1/evaluations/ev-1': () =>
        jsonResponse({
          ...summary,
          report: {
            evaluation_run: { judge: 'llm-judge-v1 · local-general', answer_model: 'local-general (qwen2.5:7b)' },
            results: {
              'no-rag': {
                metrics: {
                  answer_accuracy: { mean: 0.25, ci95_low: 0, ci95_high: 0.5, n: 8 },
                  false_abstention_rate: { mean: 0.0, ci95_low: 0, ci95_high: 0, n: 8 },
                },
                latency_ms: { p50: 4000, p95: 9000 },
                failed_runs: [],
              },
              hybrid: {
                metrics: {
                  answer_accuracy: { mean: 0.81, ci95_low: 0.6, ci95_high: 1, n: 8 },
                  false_abstention_rate: { mean: 0.125, ci95_low: 0, ci95_high: 0.38, n: 8 },
                  citation_validity: 1,
                },
                latency_ms: { p50: 6000, p95: 12000 },
                failed_runs: [{ id: 'a03', error_code: 'model_timeout' }],
              },
            },
            injection: { resistance: 1 },
          },
        }),
    })

    renderRoute(<EvaluationsPage />, { path: '/evaluations', url: '/evaluations' })

    const hybridRow = (await screen.findByText('hybrid')).closest('tr')!
    expect(within(hybridRow).getByText('0.810').closest('td')).toHaveClass('font-semibold')
    expect(within(hybridRow).getByText('1 failed run(s)')).toBeInTheDocument()
    const noRagRow = screen.getByText('no-rag').closest('tr')!
    // false_abstention_rate is lower-is-better: no-rag's 0.000 wins.
    expect(within(noRagRow).getByText('0.000').closest('td')).toHaveClass('font-semibold')
    expect(within(noRagRow).getAllByText('—')).toHaveLength(1)
    expect(screen.getByText(/automated, not an independent expert review/)).toBeInTheDocument()
    expect(screen.getByText('Prompt-injection resistance:')).toHaveTextContent('1')
    const order = screen.getAllByRole('row').slice(1).map((row) => row.querySelector('td')?.textContent)
    expect(order[0]).toMatch(/^no-rag/)
  })
})
