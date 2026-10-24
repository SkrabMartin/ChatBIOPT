import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Model output as Markdown. Raw HTML in the text is never rendered (skipHtml), images are dropped, and links are
 * shown as inert text: an unverified URL from a model must not look like a citation.
 */
const components: Components = {
  a: ({ children, href }) => (
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

export function MarkdownContent({ text }: { text: string }) {
  return (
    <div className="break-words text-sm">
      <Markdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {text}
      </Markdown>
    </div>
  )
}
