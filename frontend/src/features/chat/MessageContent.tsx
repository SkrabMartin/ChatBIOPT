import Markdown, { defaultUrlTransform, type Components } from 'react-markdown'
import { Link } from 'react-router'
import remarkGfm from 'remark-gfm'
import type { RunEvidence } from '../../lib/api/runs'

const CITE = 'cite:'
const MARKER = /\[\s*(E\d+(?:\s*,\s*E\d+)*)\s*\]/g

/** Turn server-validated `[E1, E2]` markers into `cite:` links the renderer shows as citation chips. */
function linkCitations(text: string, labels: ReadonlySet<string>): string {
  return text.replace(MARKER, (marker, group: string) => {
    const known = group.split(',').map((part) => part.trim()).filter((lab) => labels.has(lab))
    return known.length ? known.map((lab) => `[${lab}](${CITE}${lab})`).join(' ') : marker
  })
}

function CitationChip({ label, evidence }: { label: string; evidence: RunEvidence | undefined }) {
  if (!evidence) return <span className="font-mono text-xs text-slate-400">[{label}]</span>
  const where = evidence.section_title ?? evidence.section
  return (
    <Link
      to={`/documents/${evidence.document_id}#chunk-${evidence.chunk_id}`}
      title={`${evidence.source_title} · PMID ${evidence.external_id} · ${where}\n\n${evidence.text.slice(0, 400)}`}
      className="mx-0.5 rounded bg-teal-50 px-1 font-mono text-xs text-teal-800 no-underline hover:bg-teal-100"
    >
      {label}
    </Link>
  )
}

/**
 * Model output as Markdown. Raw HTML in the text is never rendered (skipHtml), images are dropped, and ordinary
 * links are shown as inert text: an unverified URL from a model must not look like a citation. Only citation
 * labels the server validated for this run become links, and they point at stored passages.
 */
export function MarkdownContent({ text, evidence }: { text: string; evidence?: RunEvidence[] }) {
  const byLabel = new Map((evidence ?? []).map((e) => [e.label, e]))
  const components: Components = {
    a: ({ children, href }) =>
      href?.startsWith(CITE) ? (
        <CitationChip label={href.slice(CITE.length)} evidence={byLabel.get(href.slice(CITE.length))} />
      ) : (
        <span className="underline decoration-dotted" title={`Unverified link from model output: ${href ?? ''}`}>
          {children}
        </span>
      ),
    img: () => null,
    p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>,
    ul: ({ children }) => <ul className="my-1.5 list-disc pl-5">{children}</ul>,
    ol: ({ children }) => <ol className="my-1.5 list-decimal pl-5">{children}</ol>,
    code: ({ children }) => <code className="rounded bg-slate-100 px-1 font-mono text-xs">{children}</code>,
    pre: ({ children }) => <pre className="my-2 overflow-x-auto rounded bg-slate-100 p-2 text-xs">{children}</pre>,
    table: ({ children }) => <table className="my-2 border-collapse text-xs">{children}</table>,
    th: ({ children }) => <th className="border border-slate-200 px-2 py-1 text-left">{children}</th>,
    td: ({ children }) => <td className="border border-slate-200 px-2 py-1">{children}</td>,
  }
  const source = evidence ? linkCitations(text, new Set(byLabel.keys())) : text
  return (
    <div className="break-words text-sm">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={components}
        skipHtml
        urlTransform={(url) => (url.startsWith(CITE) ? url : defaultUrlTransform(url))}
      >
        {source}
      </Markdown>
    </div>
  )
}
