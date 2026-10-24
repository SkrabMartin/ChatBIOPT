import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { Candidate, Entity, EntityDetail, ProjectEntity, Resolution } from '../../lib/api/entities'
import { jsonResponse, mockApi, renderRoute } from '../../test/utils'
import { EntityPage } from './EntityPage'
import { ProjectEntities } from './ProjectEntities'

const now = '2026-10-25T10:00:00Z'
const organisms = [
  { taxon_id: 9606, scientific_name: 'Homo sapiens', common_name: 'human' },
  { taxon_id: 10090, scientific_name: 'Mus musculus', common_name: 'house mouse' },
]
const providers = [{ provider: 'NCBI Gene', release: 'Build261023-2230m.1' }]

function candidate(overrides: Partial<Candidate>): Candidate {
  return {
    gene_id: '5594',
    symbol: 'MAPK1',
    name: 'mitogen-activated protein kinase 1',
    taxon_id: 9606,
    organism: 'Homo sapiens',
    match_type: 'alias',
    aliases: ['ERK2', 'p40'],
    chromosome: '22',
    map_location: '22q11.22',
    official_nomenclature: true,
    ...overrides,
  }
}

function resolution(overrides: Partial<Resolution>): Resolution {
  return {
    query: 'p40',
    taxon_id: 9606,
    input_kind: 'symbol',
    status: 'ambiguous',
    candidates: [],
    found_elsewhere: [],
    warnings: [],
    providers,
    resolver_version: 'entity-resolver-v1',
    retrieved_at: now,
    ...overrides,
  }
}

function gene(overrides: Partial<Entity> = {}): Entity {
  return {
    id: 'e-tp63',
    kind: 'gene',
    namespace: 'ncbigene',
    accession: '8626',
    taxon_id: 9606,
    organism: 'Homo sapiens',
    label: 'TP63',
    name: 'tumor protein p63',
    provider: 'NCBI Gene',
    provider_release: 'Build261023-2230m.1',
    retrieved_at: now,
    xrefs: [],
    attributes: { disease_associations: { status: 'stored', total: 900, stored: 10, data_version: '26.09' } },
    raw_payload_sha256: 'a'.repeat(64),
    ...overrides,
  }
}

describe('ProjectEntities', () => {
  it('requires an explicit organism and pins only the candidate the user picks', async () => {
    let pinned: ProjectEntity[] = []
    const resolveBodies: unknown[] = []
    const addBodies: unknown[] = []
    mockApi({
      'GET /api/v1/entities/organisms': () => jsonResponse(organisms),
      'GET /api/v1/projects/p-1/entities': () => jsonResponse(pinned),
      'POST /api/v1/entities/resolve': (body) => {
        resolveBodies.push(body)
        return jsonResponse(
          resolution({
            candidates: [candidate({}), candidate({ gene_id: '8626', symbol: 'TP63', name: 'tumor protein p63' })],
          }),
        )
      },
      'POST /api/v1/projects/p-1/entities': (body) => {
        addBodies.push(body)
        pinned = [{ entity: gene(), query: 'p40', match_type: 'alias', warnings: [], added_at: now }]
        return jsonResponse(pinned[0], 201)
      },
    })
    const user = userEvent.setup()
    renderRoute(<ProjectEntities projectId="p-1" />, { path: '/projects/:projectId', url: '/projects/p-1' })

    await user.type(screen.getByLabelText('Gene or identifier'), 'p40')
    const submit = screen.getByRole('button', { name: 'Resolve' })
    expect(submit).toBeDisabled() // no organism chosen yet: nothing is assumed
    await screen.findByRole('option', { name: 'Homo sapiens (human)' })
    await user.selectOptions(screen.getByLabelText('Organism'), '9606')
    await user.click(submit)

    const result = await screen.findByLabelText('Resolution result')
    expect(resolveBodies).toEqual([{ query: 'p40', taxon_id: 9606, kind: 'auto' }])
    expect(within(result).getByText('Ambiguous: choose a gene')).toBeInTheDocument()
    expect(within(result).getAllByRole('button', { name: 'Add to project' })).toHaveLength(2)
    expect(addBodies).toEqual([])

    await user.click(within(result).getAllByRole('button', { name: 'Add to project' })[1])

    await waitFor(() =>
      expect(addBodies).toEqual([{ gene_id: '8626', taxon_id: 9606, query: 'p40', match_type: 'alias' }]),
    )
    expect(await screen.findByRole('link', { name: 'TP63' })).toHaveAttribute('href', '/entities/e-tp63')
    expect(within(result).getByRole('button', { name: 'In project' })).toBeDisabled()
    expect(screen.getByText(/10 disease associations stored of 900/)).toBeInTheDocument()
  })

  it('shows an identifier from another organism without offering to add it', async () => {
    mockApi({
      'GET /api/v1/entities/organisms': () => jsonResponse(organisms),
      'GET /api/v1/projects/p-1/entities': () => jsonResponse([]),
      'POST /api/v1/entities/resolve': () =>
        jsonResponse(
          resolution({
            query: '7157',
            taxon_id: 10090,
            input_kind: 'ncbigene',
            status: 'taxon_mismatch',
            found_elsewhere: [
              { namespace: 'ncbigene', identifier: '7157', label: 'TP53', taxon_id: 9606, organism: 'Homo sapiens' },
            ],
          }),
        ),
    })
    const user = userEvent.setup()
    renderRoute(<ProjectEntities projectId="p-1" />, { path: '/projects/:projectId', url: '/projects/p-1' })

    await user.type(screen.getByLabelText('Gene or identifier'), '7157')
    await screen.findByRole('option', { name: 'Mus musculus (house mouse)' })
    await user.selectOptions(screen.getByLabelText('Organism'), '10090')
    await user.click(screen.getByRole('button', { name: 'Resolve' }))

    const result = await screen.findByLabelText('Resolution result')
    expect(within(result).getByText('Different organism')).toBeInTheDocument()
    expect(within(result).getByText('This identifier belongs to:')).toBeInTheDocument()
    expect(within(result).getByRole('link', { name: '7157' })).toHaveAttribute(
      'href',
      'https://www.ncbi.nlm.nih.gov/gene/7157',
    )
    expect(within(result).queryByRole('button', { name: 'Add to project' })).not.toBeInTheDocument()
  })
})

const protein = {
  id: 'e-p04637',
  kind: 'protein' as const,
  namespace: 'uniprot',
  accession: 'P04637',
  taxon_id: 9606,
  organism: 'Homo sapiens',
  label: 'P53_HUMAN',
  name: 'Cellular tumor antigen p53',
  provider: 'UniProt',
  provider_release: '2026_03',
  retrieved_at: now,
}

function disease(id: string, curie: string, label: string) {
  return {
    id,
    kind: 'disease' as const,
    namespace: 'mondo',
    accession: curie,
    taxon_id: null,
    organism: null,
    label,
    name: null,
    provider: 'Open Targets',
    provider_release: '26.09',
    retrieved_at: now,
  }
}

const note = 'Open Targets association score (0-1): aggregated evidence. Not a measure of causality.'

const tp53: EntityDetail = {
  ...gene({
    id: 'e-tp53',
    accession: '7157',
    label: 'TP53',
    name: 'tumor protein p53',
    attributes: {
      aliases: ['P53', 'LFS1'],
      map_location: '17p13.1',
      official_nomenclature: true,
      disease_associations: { status: 'stored', total: 5198, stored: 2, data_version: '26.09' },
    },
    xrefs: [
      {
        namespace: 'ensembl',
        id: 'ENSG00000141510',
        source: 'UniProt',
        release: '2026_03',
        versions: ['20'],
        reciprocal: true,
        checked_against: 'Ensembl 116',
      },
      {
        namespace: 'hgnc',
        id: 'HGNC:11998',
        source: 'UniProt',
        release: '2026_03',
        versions: [],
        reciprocal: null,
        checked_against: null,
      },
    ],
  }),
  edges: [
    {
      id: 'x1',
      predicate: 'associated_with_disease',
      direction: 'out',
      neighbor: disease('d1', 'MONDO:0018875', 'Li-Fraumeni syndrome'),
      provider: 'Open Targets',
      provider_release: '26.09',
      score: 0.876,
      evidence: { rank: 1, note, datatype_scores: { genetic_literature: 0.98, literature: 0.9 } },
      retrieved_at: now,
    },
    {
      id: 'x2',
      predicate: 'associated_with_disease',
      direction: 'out',
      neighbor: disease('d2', 'MONDO:0007256', 'hepatocellular carcinoma'),
      provider: 'Open Targets',
      provider_release: '26.09',
      score: 0.797,
      evidence: { rank: 2, note, datatype_scores: { somatic_mutation: 0.7 } },
      retrieved_at: now,
    },
    {
      id: 'x3',
      predicate: 'encodes',
      direction: 'out',
      neighbor: protein,
      provider: 'UniProt',
      provider_release: '2026_03',
      score: null,
      evidence: { basis: 'UniProtKB P04637 cross-references NCBI Gene 7157' },
      retrieved_at: now,
    },
  ],
}

describe('EntityPage', () => {
  it('shows identifiers with who asserted them, typed relations and association scores', async () => {
    mockApi({ 'GET /api/v1/entities/e-tp53': () => jsonResponse(tp53) })

    renderRoute(<EntityPage />, { path: '/entities/:entityId', url: '/entities/e-tp53' })

    expect(await screen.findByRole('heading', { name: 'TP53' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'taxon 9606' })).toBeInTheDocument()
    expect(screen.getByText('NCBI Gene Build261023-2230m.1')).toBeInTheDocument()
    expect(screen.getByText('reciprocal (Ensembl 116)')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'ENSG00000141510' })).toHaveAttribute(
      'href',
      'https://www.ensembl.org/id/ENSG00000141510',
    )
    expect(screen.getByRole('link', { name: 'P53_HUMAN' })).toHaveAttribute('href', '/entities/e-p04637')

    const table = screen.getByRole('heading', { name: 'Disease associations' }).closest('section')!
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getAllByRole('cell')[0].textContent)).toEqual([
      'Li-Fraumeni syndrome',
      'hepatocellular carcinoma',
    ])
    expect(within(rows[0]).getByText('0.876')).toBeInTheDocument()
    expect(within(table).getByText(/Not a measure of causality/)).toBeInTheDocument()
    expect(within(table).getByText('Top 2 of 5198 associations · Open Targets 26.09')).toBeInTheDocument()
  })

  it('explains that non-human genes have no Open Targets coverage', async () => {
    const mouse: EntityDetail = {
      ...gene({
        id: 'e-trp53',
        accession: '22059',
        label: 'Trp53',
        taxon_id: 10090,
        organism: 'Mus musculus',
        attributes: { disease_associations: { status: 'not_covered' } },
      }),
      edges: [],
    }
    mockApi({ 'GET /api/v1/entities/e-trp53': () => jsonResponse(mouse) })

    renderRoute(<EntityPage />, { path: '/entities/:entityId', url: '/entities/e-trp53' })

    expect(await screen.findByText(/Open Targets Platform contains human targets only/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
