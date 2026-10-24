import { Link, useParams } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Card } from '../../components/ui'
import { ApiError } from '../../lib/api/client'
import {
  associationStatusSchema,
  identifierUrl,
  taxonomyUrl,
  useEntity,
  type Edge,
  type EntityDetail,
  type EntitySummary,
} from '../../lib/api/entities'
import { formatDateTime } from '../../lib/format'

const NAMESPACE_LABEL: Record<string, string> = {
  ncbigene: 'NCBI Gene',
  uniprot: 'UniProtKB',
  ensembl: 'Ensembl',
  hgnc: 'HGNC',
  omim: 'OMIM',
  opentargets: 'Open Targets',
}

function ExternalId({ namespace, id }: { namespace: string; id: string }) {
  const url = identifierUrl(namespace, id)
  return url ? (
    <a href={url} target="_blank" rel="noreferrer" className="font-mono text-teal-700 hover:underline">
      {id}
    </a>
  ) : (
    <span className="font-mono">{id}</span>
  )
}

function EntityLink({ entity }: { entity: EntitySummary }) {
  return (
    <Link to={`/entities/${entity.id}`} className="font-medium text-teal-700 hover:underline">
      {entity.label}
    </Link>
  )
}

function Provenance({ entity }: { entity: EntityDetail }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-slate-600">
      <dt className="text-slate-400">Source</dt>
      <dd>
        {entity.provider} {entity.provider_release ?? '(release unknown)'}
      </dd>
      <dt className="text-slate-400">Retrieved</dt>
      <dd>{formatDateTime(entity.retrieved_at)}</dd>
      <dt className="text-slate-400">Raw record</dt>
      <dd className="font-mono">sha256 {entity.raw_payload_sha256.slice(0, 16)}…</dd>
    </dl>
  )
}

function CrossReferences({ entity }: { entity: EntityDetail }) {
  if (!entity.xrefs.length) return null
  return (
    <Card>
      <h2 className="mb-2 text-lg font-semibold text-slate-900">Cross-references</h2>
      <table className="w-full text-left text-sm">
        <thead className="text-xs text-slate-500">
          <tr>
            <th className="py-1 font-medium">Database</th>
            <th className="py-1 font-medium">Identifier</th>
            <th className="py-1 font-medium">Asserted by</th>
            <th className="py-1 font-medium">Check</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {entity.xrefs.map((x) => (
            <tr key={`${x.namespace}:${x.id}`}>
              <td className="py-1">{NAMESPACE_LABEL[x.namespace] ?? x.namespace}</td>
              <td className="py-1">
                <ExternalId namespace={x.namespace} id={x.id} />
                {x.versions.length ? <span className="text-xs text-slate-400"> (v{x.versions.join(', v')})</span> : null}
              </td>
              <td className="py-1 text-xs text-slate-600">
                {x.source} {x.release ?? ''}
              </td>
              <td className="py-1 text-xs">
                {x.reciprocal === true ? (
                  <span className="text-emerald-700">reciprocal ({x.checked_against})</span>
                ) : x.reciprocal === false ? (
                  <span className="text-rose-700">not confirmed by {x.checked_against}: not used</span>
                ) : (
                  <span className="text-slate-400">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

function Proteins({ edges, title }: { edges: Edge[]; title: string }) {
  if (!edges.length) return null
  return (
    <Card>
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      <p className="mb-2 text-xs text-slate-500">
        Relation: <code>encodes</code>. Basis: the UniProtKB entry cross-references the NCBI Gene record in the same
        organism.
      </p>
      <ul className="divide-y divide-slate-100 text-sm">
        {edges.map((e) => (
          <li key={e.id} className="py-1.5">
            <EntityLink entity={e.neighbor} /> <span className="text-slate-600">{e.neighbor.name}</span>{' '}
            <span className="text-xs text-slate-500">
              · {NAMESPACE_LABEL[e.neighbor.namespace]}{' '}
              <ExternalId namespace={e.neighbor.namespace} id={e.neighbor.accession} />
              {e.neighbor.organism ? (
                <>
                  {' '}
                  · <i>{e.neighbor.organism}</i>
                </>
              ) : null}{' '}
              · {e.provider} {e.provider_release}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function datatypeScores(edge: Edge): [string, number][] {
  const scores = edge.evidence.datatype_scores
  if (!scores || typeof scores !== 'object') return []
  return Object.entries(scores as Record<string, unknown>)
    .filter((entry): entry is [string, number] => typeof entry[1] === 'number')
    .sort((a, b) => b[1] - a[1])
}

function Associations({ entity, edges }: { entity: EntityDetail; edges: Edge[] }) {
  const status = associationStatusSchema.safeParse(entity.attributes.disease_associations)
  const outgoing = edges.filter((e) => e.direction === 'out')
  if (entity.kind !== 'gene' && !edges.length) return null
  const note = edges.find((e) => typeof e.evidence.note === 'string')?.evidence.note as string | undefined
  return (
    <Card>
      <h2 className="text-lg font-semibold text-slate-900">
        {outgoing.length || entity.kind === 'gene' ? 'Disease associations' : 'Associated genes'}
      </h2>
      <p className="mb-2 text-xs text-slate-500">
        Relation: <code>associated_with_disease</code> (Open Targets).{' '}
        {note ?? 'Association scores aggregate evidence; they do not establish causality.'}
      </p>
      {status.success && status.data.status === 'not_covered' ? (
        <p className="text-sm text-slate-600">
          Not covered: Open Targets Platform contains human targets only, so no associations exist for{' '}
          <i>{entity.organism}</i>.
        </p>
      ) : null}
      {status.success && status.data.status === 'unavailable' ? (
        <p className="text-sm text-amber-800">
          Open Targets was unavailable at the last refresh; associations below are from the previous snapshot.
        </p>
      ) : null}
      {status.success && status.data.status === 'no_target' ? (
        <p className="text-sm text-slate-600">No confirmed Ensembl target: associations were not retrieved.</p>
      ) : null}
      {edges.length ? (
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-slate-500">
            <tr>
              <th className="py-1 font-medium">{outgoing.length ? 'Disease' : 'Gene'}</th>
              <th className="py-1 font-medium">Identifier</th>
              <th className="w-48 py-1 font-medium">Score</th>
              <th className="py-1 font-medium">Strongest evidence types</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {edges.map((e) => (
              <tr key={e.id}>
                <td className="py-1.5">
                  <EntityLink entity={e.neighbor} />
                </td>
                <td className="py-1.5 font-mono text-xs text-slate-600">{e.neighbor.accession}</td>
                <td className="py-1.5">
                  <div className="flex items-center gap-2">
                    <div className="h-2 w-28 rounded bg-slate-100" aria-hidden>
                      <div className="h-2 rounded bg-teal-600" style={{ width: `${Math.round((e.score ?? 0) * 100)}%` }} />
                    </div>
                    <span className="font-mono text-xs">{e.score?.toFixed(3)}</span>
                  </div>
                </td>
                <td className="py-1.5 text-xs text-slate-500">
                  {datatypeScores(e)
                    .slice(0, 3)
                    .map(([name, score]) => `${name.replaceAll('_', ' ')} ${score.toFixed(2)}`)
                    .join(' · ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {status.success && status.data.status === 'stored' ? (
        <p className="mt-2 text-xs text-slate-400">
          Top {status.data.stored} of {status.data.total} associations · Open Targets {status.data.data_version}
        </p>
      ) : null}
    </Card>
  )
}

function Attributes({ entity }: { entity: EntityDetail }) {
  const a = entity.attributes
  const rows: [string, string][] = []
  if (Array.isArray(a.aliases) && a.aliases.length) rows.push(['Aliases', a.aliases.join(', ')])
  if (typeof a.map_location === 'string') rows.push(['Location', a.map_location])
  if (typeof a.official_nomenclature === 'boolean')
    rows.push(['Nomenclature', a.official_nomenclature ? 'official' : 'not official (provisional or interim)'])
  if (typeof a.reviewed === 'boolean') rows.push(['UniProtKB', a.reviewed ? 'reviewed (Swiss-Prot)' : 'unreviewed'])
  if (typeof a.length === 'number') rows.push(['Sequence', `${a.length} aa, sequence version ${String(a.sequence_version)}`])
  if (Array.isArray(a.gene_symbols) && a.gene_symbols.length) rows.push(['Gene names', a.gene_symbols.join(', ')])
  if (!rows.length) return null
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-slate-500">{k}</dt>
          <dd className="text-slate-700">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

export function EntityPage() {
  const { entityId = '' } = useParams()
  const entity = useEntity(entityId)

  if (entity.isPending) return <p className="text-sm text-slate-500">Loading…</p>
  if (entity.error) {
    const missing = entity.error instanceof ApiError && (entity.error.status === 404 || entity.error.status === 422)
    return <ErrorNotice error={entity.error} title={missing ? 'Entity not found' : 'Could not load entity'} />
  }
  const e = entity.data
  const encodes = e.edges.filter((x) => x.predicate === 'encodes')
  const associations = e.edges.filter((x) => x.predicate === 'associated_with_disease')
  const ownXref = e.xrefs.find((x) => x.namespace === 'opentargets')

  return (
    <div className="space-y-6">
      <Card className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-3">
          <h1 className="text-2xl font-semibold text-slate-900">{e.label}</h1>
          <span className="rounded border border-slate-200 px-2 py-0.5 text-xs text-slate-600">{e.kind}</span>
          <span className="text-sm text-slate-500">
            {NAMESPACE_LABEL[e.namespace] ?? e.namespace}{' '}
            {ownXref ? <ExternalId namespace="opentargets" id={ownXref.id} /> : null}
            {!ownXref ? <ExternalId namespace={e.namespace} id={e.accession} /> : <span> ({e.accession})</span>}
          </span>
        </div>
        {e.name ? <p className="text-slate-700">{e.name}</p> : null}
        {e.taxon_id !== null ? (
          <p className="text-sm text-slate-600">
            Organism: <i>{e.organism}</i> ·{' '}
            <a href={taxonomyUrl(e.taxon_id)} target="_blank" rel="noreferrer" className="text-teal-700 hover:underline">
              taxon {e.taxon_id}
            </a>
          </p>
        ) : null}
        <Attributes entity={e} />
        <Provenance entity={e} />
      </Card>
      <CrossReferences entity={e} />
      <Proteins edges={encodes} title={encodes.some((x) => x.direction === 'out') ? 'Encoded proteins' : 'Encoded by'} />
      <Associations entity={e} edges={associations} />
    </div>
  )
}
