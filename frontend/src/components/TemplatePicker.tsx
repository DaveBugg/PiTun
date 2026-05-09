import { useQuery } from '@tanstack/react-query'
import { Loader2, Globe, FileCode2, Briefcase, Newspaper, BookOpen, Wrench } from 'lucide-react'

import { templatesApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import type { DecoyTemplate } from '@/types'

/**
 * Decoy-site template picker (since v1.3.0-beta.6).
 *
 * Renders a small card gallery so the user can choose what
 * non-authenticated visitors see at the proxy's domain when they
 * arrive without a valid Proxy-Authorization header. The default
 * (Pac-Man via daleharvey/pacman) is fine but doesn't always
 * match the domain — a corporate-y domain looks more plausible
 * with the "corporate" landing, a personal-blog domain with the
 * blog template, etc.
 *
 * No "None" option: the script needs SOMETHING to serve at the
 * root, otherwise visitors get the default Caddy page which
 * obviously screams "I am a proxy". The script-side `DECOY_REPO=
 * none` escape hatch stays available via direct env var override
 * for power users; the UI deliberately doesn't surface it.
 *
 * Used in two places:
 *   - DeployModal (auto-deploy via SSH)
 *   - ManualScriptModal (download .sh)
 * Both pass the picked id into `naive_install_script` env. */
export function TemplatePicker({
  value,
  onChange,
}: {
  value: string | undefined
  onChange: (id: string) => void
}) {
  const t = useT()
  // List endpoint is small + static; cache it for the modal session
  // and don't refetch on focus (the gallery doesn't change while
  // the user has the form open).
  const { data: templates, isLoading, error } = useQuery({
    queryKey: ['templates'],
    queryFn: () => templatesApi.list(),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })

  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-4 flex items-center gap-2 text-xs text-gray-500">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {t('Loading templates…', 'Загрузка шаблонов…')}
      </div>
    )
  }
  if (error || !templates) {
    return (
      <div className="rounded-lg border border-red-800/40 bg-red-950/20 px-3 py-2 text-xs text-red-300">
        {t('Failed to load templates', 'Не удалось загрузить шаблоны')}
      </div>
    )
  }

  // Default to the first template if nothing selected — keeps the
  // "I never touched this" path identical to the script's own
  // built-in default (which currently is also pacman, position #0).
  const selected = value ?? templates[0]?.id

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
      {templates.map((tpl) => (
        <TemplateCard
          key={tpl.id}
          template={tpl}
          active={tpl.id === selected}
          onClick={() => onChange(tpl.id)}
        />
      ))}
    </div>
  )
}


function TemplateCard({
  template, active, onClick,
}: {
  template: DecoyTemplate
  active: boolean
  onClick: () => void
}) {
  const Icon = ICON_BY_ID[template.id] ?? FileCode2
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        'rounded-lg border px-3 py-2 text-left transition-colors flex items-start gap-2 ' +
        (active
          ? 'border-brand-500/60 bg-brand-600/10 text-brand-200'
          : 'border-gray-800 bg-gray-900/40 text-gray-400 hover:border-gray-700 hover:text-gray-200')
      }
    >
      <div className={active ? 'text-brand-400 mt-0.5' : 'text-gray-500 mt-0.5'}>
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0">
        <div className="text-sm font-medium flex items-center gap-1.5">
          {template.label}
          <span className="text-[10px] uppercase tracking-wider text-gray-600 font-mono">
            {template.kind === 'git_repo' ? 'git' : 'html'}
          </span>
        </div>
        <div className="text-[11px] text-gray-500 mt-0.5 leading-snug">
          {template.description}
        </div>
      </div>
    </button>
  )
}


// Lucide icon per template id — purely cosmetic, falls back to
// FileCode2 if unknown so adding a new template doesn't require
// editing this map.
const ICON_BY_ID: Record<string, React.ComponentType<{ className?: string }>> = {
  pacman: Globe,
  corporate: Briefcase,
  blog: Newspaper,
  docs: BookOpen,
  maintenance: Wrench,
}
