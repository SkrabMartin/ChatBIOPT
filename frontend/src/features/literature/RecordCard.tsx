import { useState } from 'react'
import { Link } from 'react-router'
import type { LiteratureRecord } from '../../lib/api/literature'
import { AddToProject } from '../evidence/AddToProject'

function formatAuthors(authors: string[], max = 3): string {
  if (authors.length === 0) return 'No authors listed'
  return authors.length > max ? `${authors.slice(0, max).join(', ')}, et al.` : authors.join(', ')
}

/** Outbound link to the original record. Hrefs come only from server-validated identifiers. */
export function ExternalLink({ href, children }: { href: string; children: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="rounded border border-slate-200 px-1.5 py-0.5 font-mono text-xs text-teal-800 hover:bg-teal-50"
    >
      {children}
    </a>
  )
}

export function IdentifierLinks({ record }: { record: LiteratureRecord }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <ExternalLink href={record.links.pubmed}>{`PMID ${record.pmid}`}</ExternalLink>
      {record.doi && record.links.doi ? <ExternalLink href={record.links.doi}>{`DOI ${record.doi}`}</ExternalLink> : null}
      {record.pmcid && record.links.pmc ? (
        <ExternalLink href={record.links.pmc}>{`${record.pmcid} (free full text)`}</ExternalLink>
      ) : null}
    </div>
  )
}

export function RecordCard({ record }: { record: LiteratureRecord }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <article className="space-y-2 py-4">
      <h3 className="font-medium text-slate-900">
        <Link to={`/literature/pubmed/${record.pmid}`} className="hover:text-teal-800 hover:underline">
          {record.title}
        </Link>
      </h3>
      <p className="text-sm text-slate-600">{formatAuthors(record.authors)}</p>
      <p className="text-xs text-slate-500">
        {[record.journal, record.published_date ?? record.published_year?.toString(), ...record.publication_types]
          .filter(Boolean)
          .join(' · ')}
      </p>
      <IdentifierLinks record={record} />
      <AddToProject pmid={record.pmid} />
      {record.abstract ? (
        <div>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className="text-xs font-medium text-teal-700 hover:underline"
          >
            {expanded ? 'Hide abstract' : 'Show abstract'}
          </button>
          {expanded ? <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">{record.abstract}</p> : null}
        </div>
      ) : (
        <p className="text-xs text-slate-400">No abstract in PubMed record.</p>
      )}
    </article>
  )
}
