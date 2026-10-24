import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card } from '../../components/ui'
import { useModels, type ModelProfile } from '../../lib/api/runs'
import { useModelPreference } from '../../lib/preferences'

const policyLabel: Record<string, string> = {
  local: 'Prompts stay on infrastructure you run',
  external: 'Prompts are sent to an external provider',
}

function ModelRow({ model, selected, onSelect }: { model: ModelProfile; selected: boolean; onSelect: () => void }) {
  const usable = model.available && !model.blocked_reason
  return (
    <li className="flex items-start justify-between gap-4 py-3">
      <div className="space-y-1">
        <p className="font-medium text-slate-900">
          {model.label}{' '}
          {model.is_default ? <span className="text-xs font-normal text-slate-400">(server default)</span> : null}
        </p>
        <p className="font-mono text-xs text-slate-500">
          {model.provider} · {model.model} · {model.context_window.toLocaleString()} tokens · {model.license}
        </p>
        <p className={`text-xs ${model.data_policy === 'external' ? 'text-amber-700' : 'text-slate-500'}`}>
          {model.deployment} · {policyLabel[model.data_policy] ?? model.data_policy}
          {model.price_input_per_mtok || model.price_output_per_mtok
            ? ` · $${model.price_input_per_mtok}/$${model.price_output_per_mtok} per 1M tokens in/out`
            : ' · no per-token cost'}
        </p>
        {model.notes ? <p className="text-xs text-slate-500">{model.notes}</p> : null}
        {usable ? (
          <p className="text-xs text-emerald-700">
            Available{model.revision ? <span className="font-mono"> · {model.revision.slice(0, 19)}</span> : null}
          </p>
        ) : (
          <p className="text-xs text-rose-700">{model.unavailable_reason ?? 'Unavailable'}</p>
        )}
      </div>
      <Button variant={selected ? 'primary' : 'secondary'} onClick={onSelect} disabled={!usable || selected}>
        {selected ? 'Selected' : 'Use'}
      </Button>
    </li>
  )
}

export function SettingsPage() {
  const models = useModels()
  const [preference, setPreference] = useModelPreference()

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Settings</h1>
        <p className="mt-1 text-sm text-slate-500">
          Model used for new answers in this browser. Models are defined in the server&apos;s{' '}
          <code className="font-mono">model_registry.yaml</code>.
        </p>
      </header>
      <Card>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">Models</h2>
          <Button variant="secondary" onClick={() => void models.refetch()} disabled={models.isFetching}>
            {models.isFetching ? 'Checking…' : 'Recheck availability'}
          </Button>
        </div>
        {models.isPending ? (
          <p className="text-sm text-slate-500">Checking models…</p>
        ) : models.error ? (
          <ErrorNotice error={models.error} title="Could not load models" />
        ) : (
          <>
            <ul className="divide-y divide-slate-100">
              {models.data.items.map((model) => (
                <ModelRow
                  key={model.id}
                  model={model}
                  selected={(preference ?? models.data.default_profile) === model.id}
                  onSelect={() => setPreference(model.is_default ? null : model.id)}
                />
              ))}
            </ul>
            <p className="mt-3 text-xs text-slate-500">
              External models are {models.data.allow_external_models ? 'allowed' : 'disabled'} on this server
              {models.data.allow_external_models ? '.' : ' (ALLOW_EXTERNAL_MODELS=false): no prompt leaves the host.'}
            </p>
          </>
        )}
      </Card>
    </div>
  )
}
