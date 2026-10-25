import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, Field, TextInput } from '../../components/ui'
import {
  associationStatusSchema,
  identifierUrl,
  INPUT_KINDS,
  useAddProjectEntity,
  useOrganisms,
  useProjectEntities,
  useRemoveProjectEntity,
  useResolveEntity,
  type Candidate,
  type InputKind,
  type MatchType,
  type ProjectEntity,
  type Resolution,
} from '../../lib/api/entities'
import { plural } from '../../lib/format'

const KIND_LABEL: Record<'auto' | InputKind, string> = {
  auto: 'Auto-detect',
  symbol: 'Gene symbol / alias',
  ncbigene: 'NCBI Gene ID',
  ensembl: 'Ensembl gene ID',
  hgnc: 'HGNC ID',
  uniprot: 'UniProt accession',
}

const MATCH_LABEL: Record<MatchType, string> = {
  official_symbol: 'official symbol',
  alias: 'alias',
  identifier: 'NCBI Gene ID',
  replaced_identifier: 'replaced ID → current',
  cross_reference: 'cross-reference',
}

const STATUS_STYLE: Record<Resolution['status'], { label: string; className: string }> = {
  resolved: { label: 'Resolved', className: 'bg-emerald-50 text-emerald-800 border-emerald-200' },
  ambiguous: { label: 'Ambiguous: choose a gene', className: 'bg-amber-50 text-amber-800 border-amber-200' },
  not_found: { label: 'Not found', className: 'bg-slate-50 text-slate-700 border-slate-200' },
  taxon_mismatch: { label: 'Different organism', className: 'bg-rose-50 text-rose-800 border-rose-200' },
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

function CandidateRow({
  candidate,
  resolution,
  projectId,
  pinned,
}: {
  candidate: Candidate
  resolution: Resolution
  projectId: string
  pinned: boolean
}) {
  const add = useAddProjectEntity(projectId)
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-2">
      <div className="min-w-0">
        <p className="text-sm">
          <span className="font-semibold text-slate-900">{candidate.symbol}</span>{' '}
          <span className="text-slate-600">{candidate.name}</span>
        </p>
        <p className="text-xs text-slate-500">
          NCBI Gene <ExternalId namespace="ncbigene" id={candidate.gene_id} /> · <i>{candidate.organism}</i> (taxon{' '}
          {candidate.taxon_id}) · matched as {MATCH_LABEL[candidate.match_type]}
          {candidate.map_location ? ` · ${candidate.map_location}` : ''}
        </p>
        {candidate.aliases.length ? (
          <p className="text-xs text-slate-400">aliases: {candidate.aliases.join(', ')}</p>
        ) : null}
        {add.error ? <ErrorNotice error={add.error} title="Could not add gene" /> : null}
      </div>
      <Button
        variant="secondary"
        disabled={pinned || add.isPending}
        onClick={() =>
          add.mutate({
            gene_id: candidate.gene_id,
            taxon_id: candidate.taxon_id,
            query: resolution.query,
            match_type: candidate.match_type,
          })
        }
      >
        {pinned ? 'In project' : add.isPending ? 'Importing…' : 'Add to project'}
      </Button>
    </li>
  )
}

function ResolutionResult({
  resolution,
  projectId,
  pinnedGeneIds,
}: {
  resolution: Resolution
  projectId: string
  pinnedGeneIds: Set<string>
}) {
  const style = STATUS_STYLE[resolution.status]
  return (
    <div className="space-y-2 rounded-lg border border-slate-200 p-3" aria-label="Resolution result">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded border px-2 py-0.5 text-xs font-medium ${style.className}`}>{style.label}</span>
        <span className="text-xs text-slate-500">
          “{resolution.query}” as {KIND_LABEL[resolution.input_kind].toLowerCase()} in taxon {resolution.taxon_id}
        </span>
      </div>
      {resolution.warnings.length ? (
        <ul className="list-disc pl-5 text-xs text-amber-800">
          {resolution.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
      {resolution.candidates.length ? (
        <ul className="divide-y divide-slate-100">
          {resolution.candidates.map((c) => (
            <CandidateRow
              key={c.gene_id}
              candidate={c}
              resolution={resolution}
              projectId={projectId}
              pinned={pinnedGeneIds.has(c.gene_id)}
            />
          ))}
        </ul>
      ) : null}
      {resolution.found_elsewhere.length ? (
        <div className="text-xs text-slate-600">
          <p className="font-medium">
            {resolution.status === 'taxon_mismatch' ? 'This identifier belongs to:' : 'Exists in other organisms:'}
          </p>
          <ul className="list-disc pl-5">
            {resolution.found_elsewhere.map((e) => (
              <li key={`${e.namespace}:${e.identifier}`}>
                {e.label} · <i>{e.organism}</i> (taxon {e.taxon_id}) ·{' '}
                <ExternalId namespace={e.namespace} id={e.identifier} />
              </li>
            ))}
          </ul>
          <p className="mt-1 text-slate-500">Not resolved: change the organism if this is the gene you mean.</p>
        </div>
      ) : null}
      <p className="text-xs text-slate-400">
        {resolution.providers.map((p) => `${p.provider} ${p.release ?? '(release unknown)'}`).join(' · ')} ·{' '}
        {resolution.resolver_version}
      </p>
    </div>
  )
}

function PinnedEntity({ item, projectId, editable }: { item: ProjectEntity; projectId: string; editable: boolean }) {
  const remove = useRemoveProjectEntity(projectId)
  const { entity } = item
  const associations = associationStatusSchema.safeParse(entity.attributes.disease_associations)
  const ensembl = entity.xrefs.filter((x) => x.namespace === 'ensembl')
  const proteins = entity.xrefs.filter((x) => x.namespace === 'uniprot')
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-2">
      <div className="min-w-0 text-sm">
        <Link to={`/entities/${entity.id}`} className="font-semibold text-teal-700 hover:underline">
          {entity.label}
        </Link>{' '}
        <span className="text-slate-600">{entity.name}</span>
        <p className="text-xs text-slate-500">
          <i>{entity.organism}</i> (taxon {entity.taxon_id}) · NCBI Gene{' '}
          <ExternalId namespace="ncbigene" id={entity.accession} />
          {proteins.length ? (
            <>
              {' '}
              · UniProt{' '}
              {proteins.map((p, i) => (
                <span key={p.id}>
                  {i ? ', ' : ''}
                  <ExternalId namespace="uniprot" id={p.id} />
                </span>
              ))}
            </>
          ) : null}
          {ensembl.map((x) => (
            <span key={x.id}>
              {' '}
              · <ExternalId namespace="ensembl" id={x.id} />
              {x.reciprocal ? '' : ' (unconfirmed)'}
            </span>
          ))}
        </p>
        <p className="text-xs text-slate-400">
          {associations.success && associations.data.status === 'stored'
            ? `${plural(associations.data.stored ?? 0, 'disease association')} stored of ${associations.data.total ?? '?'} (Open Targets ${associations.data.data_version ?? ''})`
            : associations.success && associations.data.status === 'not_covered'
              ? 'Disease associations: not covered (Open Targets is human-only)'
              : associations.success
                ? `Disease associations: ${associations.data.status.replace('_', ' ')}`
                : null}
          {item.query ? ` · resolved from “${item.query}”` : ''}
        </p>
        {item.warnings.length ? (
          <ul className="list-disc pl-5 text-xs text-amber-800">
            {item.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : null}
      </div>
      {editable ? (
        <Button variant="danger" onClick={() => remove.mutate(entity.id)} disabled={remove.isPending}>
          Remove
        </Button>
      ) : null}
    </li>
  )
}

/** Resolve genes within an explicitly chosen organism and pin them, with their typed relations, to the project. */
export function ProjectEntities({ projectId, editable }: { projectId: string; editable: boolean }) {
  const organisms = useOrganisms()
  const entities = useProjectEntities(projectId)
  const resolve = useResolveEntity()
  const [query, setQuery] = useState('')
  const [taxon, setTaxon] = useState('')
  const [kind, setKind] = useState<'auto' | InputKind>('auto')

  const pinnedGeneIds = new Set(
    (entities.data ?? []).filter((i) => i.entity.namespace === 'ncbigene').map((i) => i.entity.accession),
  )

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!query.trim() || !taxon) return
    resolve.mutate({ query: query.trim(), taxon_id: Number(taxon), kind })
  }

  return (
    <Card className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Genes</h2>
        <p className="text-sm text-slate-500">
          Resolve a gene symbol or identifier within an explicit organism (NCBI Gene, UniProtKB, Ensembl). Disease
          associations come from Open Targets and are available for human genes only.
        </p>
      </div>
      {editable ? (
        <>
          <form onSubmit={submit} className="grid gap-3 md:grid-cols-[1fr_14rem_11rem_auto] md:items-end">
            <Field label="Gene or identifier" htmlFor="entity-query">
              <TextInput
                id="entity-query"
                value={query}
                maxLength={64}
                placeholder="TP53, ENSG00000141510, HGNC:11998, P04637…"
                onChange={(e) => setQuery(e.target.value)}
              />
            </Field>
            <Field label="Organism" htmlFor="entity-organism">
              <select
                id="entity-organism"
                value={taxon}
                onChange={(e) => setTaxon(e.target.value)}
                className="w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm"
              >
                <option value="">Choose organism…</option>
                {organisms.data?.map((o) => (
                  <option key={o.taxon_id} value={o.taxon_id}>
                    {o.scientific_name} ({o.common_name})
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Input type" htmlFor="entity-kind">
              <select
                id="entity-kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as 'auto' | InputKind)}
                className="w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm"
              >
                {(['auto', ...INPUT_KINDS] as const).map((k) => (
                  <option key={k} value={k}>
                    {KIND_LABEL[k]}
                  </option>
                ))}
              </select>
            </Field>
            <Button type="submit" disabled={resolve.isPending || !query.trim() || !taxon}>
              {resolve.isPending ? 'Resolving…' : 'Resolve'}
            </Button>
          </form>
          {organisms.error ? <ErrorNotice error={organisms.error} title="Could not load organisms" /> : null}
          {resolve.error ? <ErrorNotice error={resolve.error} title="Could not resolve" /> : null}
          {resolve.data ? (
            <ResolutionResult resolution={resolve.data} projectId={projectId} pinnedGeneIds={pinnedGeneIds} />
          ) : null}
        </>
      ) : null}
      <div>
        <h3 className="text-sm font-semibold text-slate-700">In this project</h3>
        {entities.error ? <ErrorNotice error={entities.error} title="Could not load genes" /> : null}
        {entities.data?.length === 0 ? <p className="text-sm text-slate-500">No genes yet.</p> : null}
        <ul className="divide-y divide-slate-100">
          {entities.data?.map((item) => (
            <PinnedEntity key={item.entity.id} item={item} projectId={projectId} editable={editable} />
          ))}
        </ul>
      </div>
    </Card>
  )
}
