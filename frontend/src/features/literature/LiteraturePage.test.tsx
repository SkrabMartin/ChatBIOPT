import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import type { LiteratureRecord, LiteratureSearch } from '../../lib/api/literature'
import { jsonResponse, mockApi, renderRoute } from '../../test/utils'
import { LiteraturePage } from './LiteraturePage'

const record: LiteratureRecord = {
  source: 'pubmed',
  source_id: '18772890',
  pmid: '18772890',
  pmcid: 'PMC2671642',
  doi: '10.1038/nature07385',
  title: 'Comprehensive genomic characterization defines human glioblastoma genes and core pathways.',
  abstract: 'Abstract with <img src=x onerror=alert(1)> markup-looking text.',
  abstract_sections: [{ label: null, text: 'Abstract with <img src=x onerror=alert(1)> markup-looking text.' }],
  authors: ['Cancer Genome Atlas Research Network'],
  journal: 'Nature',
  published_year: 2008,
  published_date: '2008-10-23',
  publication_types: ['Journal Article'],
  source_url: 'https://pubmed.ncbi.nlm.nih.gov/18772890/',
  links: {
    pubmed: 'https://pubmed.ncbi.nlm.nih.gov/18772890/',
    doi: 'https://doi.org/10.1038/nature07385',
    pmc: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC2671642/',
  },
  retrieved_at: '2026-10-23T10:00:00Z',
  raw_payload_sha256: 'a'.repeat(64),
  normalizer_version: 'pubmed-v1',
}

function searchResponse(overrides: Partial<LiteratureSearch> = {}): LiteratureSearch {
  return {
    search_id: '7f1c2b9e-0000-0000-0000-000000000000',
    source: 'pubmed',
    query: 'TP53 glioblastoma',
    query_translation: '"tp53"[All Fields] AND "glioblastoma"[MeSH Terms]',
    total_count: 1093,
    limit: 20,
    offset: 0,
    retrieved_at: '2026-10-23T10:00:00Z',
    items: [record],
    ...overrides,
  }
}

const searchBody = z.object({ query: z.string(), limit: z.number(), offset: z.number() })

function renderPage(url = '/literature') {
  return renderRoute(<LiteraturePage />, { path: '/literature', url })
}

describe('LiteraturePage', () => {
  it('searches PubMed and links each record to its original identifiers', async () => {
    const fetchMock = mockApi({ 'POST /api/v1/literature/search': () => jsonResponse(searchResponse()) })
    const user = userEvent.setup()
    const { router } = renderPage()

    await user.type(screen.getByLabelText('PubMed query'), '  TP53 glioblastoma ')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    const card = (await screen.findByRole('heading', { name: record.title })).closest('article')!
    const links = within(card).getAllByRole('link', { name: /PMID|DOI|PMC/ })
    expect(links.map((a) => a.getAttribute('href'))).toEqual([record.links.pubmed, record.links.doi, record.links.pmc])
    for (const link of links) expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText(/1,093 records/)).toBeInTheDocument()
    expect(router.state.location.search).toBe('?q=TP53+glioblastoma')
    expect(searchBody.parse(JSON.parse(String(fetchMock.mock.calls[0][1]?.body)))).toEqual({
      query: 'TP53 glioblastoma',
      limit: 20,
      offset: 0,
    })
  })

  it('renders abstract text as text, never as HTML', async () => {
    mockApi({ 'POST /api/v1/literature/search': () => jsonResponse(searchResponse()) })
    const user = userEvent.setup()
    const { container } = renderPage('/literature?q=TP53')

    await user.click(await screen.findByRole('button', { name: 'Show abstract' }))

    expect(screen.getByText(/markup-looking text/)).toHaveTextContent('<img src=x onerror=alert(1)>')
    expect(container.querySelector('article img')).toBeNull()
  })

  it('distinguishes a provider timeout from an empty result', async () => {
    mockApi({
      'POST /api/v1/literature/search': () =>
        jsonResponse({ error: { code: 'upstream_timeout', message: 'PubMed did not respond in time', trace_id: 't' } }, 504),
    })

    renderPage('/literature?q=TP53')

    expect(await screen.findByRole('alert')).toHaveTextContent('PubMed did not respond in time')
    expect(screen.queryByText('No PubMed records match this query.')).not.toBeInTheDocument()
  })

  it('says so when PubMed has no matches', async () => {
    mockApi({
      'POST /api/v1/literature/search': () => jsonResponse(searchResponse({ total_count: 0, items: [], query_translation: null })),
    })

    renderPage('/literature?q=zzzz')

    expect(await screen.findByText('No PubMed records match this query.')).toBeInTheDocument()
  })

  it('pages through results via the URL offset', async () => {
    const fetchMock = mockApi({
      'POST /api/v1/literature/search': (body) => {
        const { offset } = searchBody.parse(body)
        return jsonResponse(searchResponse({ offset, items: [{ ...record, pmid: String(100 + offset) }] }))
      },
    })
    const user = userEvent.setup()
    const { router } = renderPage('/literature?q=TP53')

    await user.click(await screen.findByRole('button', { name: 'Next' }))

    expect(await screen.findByText(/showing 21–21/)).toBeInTheDocument()
    expect(router.state.location.search).toBe('?q=TP53&offset=20')
    expect(fetchMock.mock.calls.map(([, init]) => searchBody.parse(JSON.parse(String(init?.body))).offset)).toEqual([0, 20])
    expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled()
  })
})
