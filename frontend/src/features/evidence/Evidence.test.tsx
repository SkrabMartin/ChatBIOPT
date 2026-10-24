import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import type { DocumentSummary, IngestionJob, Library } from '../../lib/api/evidence'
import { jsonResponse, mockApi, renderRoute } from '../../test/utils'
import { AddToProject } from './AddToProject'
import { DocumentPage } from './DocumentPage'
import { EvidenceLibrary } from './EvidenceLibrary'

const now = '2026-10-24T10:00:00Z'
const P = 'p-1'

function job(overrides: Partial<IngestionJob> = {}): IngestionJob {
  return {
    id: 'j-1',
    project_id: P,
    source_type: 'pubmed',
    external_id: '29416795',
    source_id: 's-1',
    status: 'succeeded',
    attempts: 1,
    max_attempts: 3,
    run_after: now,
    steps: [],
    error_code: null,
    error_message: null,
    created_at: now,
    started_at: now,
    finished_at: now,
    ...overrides,
  }
}

function doc(overrides: Partial<DocumentSummary> = {}): DocumentSummary {
  return {
    id: 'd-1',
    source_id: 's-1',
    kind: 'abstract',
    version: 1,
    access: 'metadata',
    license: null,
    license_url: null,
    origin: 'pubmed',
    origin_url: 'https://pubmed.ncbi.nlm.nih.gov/29416795/',
    text_sha256: 'a'.repeat(64),
    text_length: 100,
    raw_sha256: 'b'.repeat(64),
    parser_version: 'pubmed-abstract-v1',
    retrieved_at: now,
    created_at: now,
    ...overrides,
  }
}

const source = {
  id: 's-1',
  source_type: 'pubmed',
  external_id: '29416795',
  title: 'Treatment-associated TP53 mutations in secondary gliosarcoma.',
  url: 'https://pubmed.ncbi.nlm.nih.gov/29416795/',
  doi: '10.18632/oncotarget.23517',
  pmcid: 'PMC5788663',
  journal: 'Oncotarget',
  published_year: 2018,
}

function library(items: Library['items'], pending: IngestionJob[] = []): Library {
  return { items, pending_jobs: pending }
}

describe('EvidenceLibrary', () => {
  it('lists stored document versions with license and chunk counts, and why full text is missing', async () => {
    mockApi({
      [`GET /api/v1/projects/${P}/sources`]: () =>
        jsonResponse(
          library([
            {
              source,
              added_at: now,
              documents: [doc(), doc({ id: 'd-2', kind: 'fulltext', access: 'open_access', license: 'CC-BY' })],
              chunk_counts: { 'd-1': 2, 'd-2': 41 },
              latest_job: job(),
            },
            {
              source: { ...source, id: 's-2', external_id: '18772890', title: 'TCGA glioblastoma' },
              added_at: now,
              documents: [doc({ id: 'd-3', source_id: 's-2' })],
              chunk_counts: { 'd-3': 1 },
              latest_job: job({
                id: 'j-2',
                external_id: '18772890',
                steps: [{ step: 'fulltext', status: 'skipped', detail: 'Full text not stored: no explicit open license', at: now }],
              }),
            },
          ]),
        ),
    })

    renderRoute(<EvidenceLibrary projectId={P} />, { path: '/projects/:projectId', url: `/projects/${P}` })

    expect(await screen.findByRole('link', { name: 'Full text v1 · CC-BY · 41 chunks' })).toHaveAttribute(
      'href',
      '/documents/d-2',
    )
    expect(screen.getByRole('link', { name: 'Abstract v1 · 2 chunks' })).toHaveAttribute('href', '/documents/d-1')
    expect(screen.getByText('Full text not stored: no explicit open license')).toBeInTheDocument()
  })

  it('polls while a job is active and shows the outcome', async () => {
    let calls = 0
    mockApi({
      [`GET /api/v1/projects/${P}/sources`]: () => {
        calls += 1
        return calls === 1
          ? jsonResponse(library([], [job({ status: 'queued', source_id: null, attempts: 0 })]))
          : jsonResponse(library([], [job({ status: 'failed', error_code: 'not_found', error_message: 'PubMed has no record with PMID 29416795' })]))
      },
    })

    renderRoute(<EvidenceLibrary projectId={P} />, { path: '/projects/:projectId', url: `/projects/${P}` })

    expect(await screen.findByText('queued')).toBeInTheDocument()
    expect(await screen.findByText('PubMed has no record with PMID 29416795', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
  })
})

describe('AddToProject', () => {
  it('queues the record into the chosen project', async () => {
    const fetchMock = mockApi({
      'GET /api/v1/projects': () =>
        jsonResponse({
          items: [
            { id: 'p-1', title: 'First', description: null, conversation_count: 0, created_at: now, updated_at: now },
            { id: 'p-2', title: 'Second', description: null, conversation_count: 0, created_at: now, updated_at: now },
          ],
          total: 2,
          limit: 200,
          offset: 0,
        }),
      'POST /api/v1/projects/p-2/sources': () => jsonResponse({ job: job({ project_id: 'p-2', status: 'queued' }), created: true }, 202),
    })
    const user = userEvent.setup()
    renderRoute(<AddToProject pmid="29416795" />, { path: '/', url: '/' })

    await user.selectOptions(await screen.findByLabelText('Project'), 'p-2')
    await user.click(screen.getByRole('button', { name: 'Add to project' }))

    expect(await screen.findByText(/Queued for ingestion/)).toBeInTheDocument()
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(z.object({ identifier: z.string() }).parse(JSON.parse(String(post?.[1]?.body))).identifier).toBe('29416795')
  })
})

describe('DocumentPage', () => {
  it('marks superseded versions, links all versions and renders chunk text as plain text', async () => {
    mockApi({
      'GET /api/v1/documents/d-1': () =>
        jsonResponse({
          ...doc(),
          text: 'Title\n\nAbstract\n\nText with <b>markup</b>.',
          sections: [{ kind: 'abstract', title: 'Abstract', start_offset: 7, end_offset: 41 }],
          chunker_version: 'section-sentence-v1',
          chunks: [
            {
              id: 'c-1',
              document_id: 'd-1',
              chunker_version: 'section-sentence-v1',
              ordinal: 0,
              section: 'abstract',
              section_title: 'Abstract',
              start_offset: 17,
              end_offset: 41,
              text: 'Text with <b>markup</b>.',
              token_estimate: 6,
            },
          ],
          is_latest: false,
          source,
        }),
      'GET /api/v1/sources/s-1/documents': () => jsonResponse([doc({ id: 'd-9', version: 2 }), doc()]),
    })

    renderRoute(<DocumentPage />, { path: '/documents/:documentId', url: '/documents/d-1' })

    expect(await screen.findByText('Superseded version')).toBeInTheDocument()
    const chunk = screen.getByText('Text with <b>markup</b>.')
    expect(chunk.querySelector('b')).toBeNull()
    await waitFor(() => expect(screen.getAllByRole('link', { name: /^v\d/ })).toHaveLength(2))
    const current = screen.getByRole('link', { name: /^v1/ })
    expect(current).toHaveAttribute('aria-current', 'page')
    expect(within(current.parentElement!).getByRole('link', { name: /^v2/ })).toHaveAttribute('href', '/documents/d-9')
  })
})
