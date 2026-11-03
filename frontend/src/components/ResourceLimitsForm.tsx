import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { resourcesApi } from '@/api/client'
import { apiErrorText } from '@/lib/apiError'
import { useT } from '@/hooks/useT'
import type { ResourceLimits } from '@/types'

type Draft = Record<string, { mem_mb: string; cpus: string }> & { xray: { mem_mb: string; cpus: string } }
const TARGETS = ['backend', 'frontend', 'nginx', 'naive'] as const

function toDraft(l: ResourceLimits): Draft {
  const d: Record<string, { mem_mb: string; cpus: string }> = {}
  for (const k of TARGETS) d[k] = { mem_mb: l[k].mem_mb?.toString() ?? '', cpus: l[k].cpus?.toString() ?? '' }
  d.xray = { mem_mb: l.xray_mem_mb?.toString() ?? '', cpus: '' }
  return d as Draft
}

// Optional ceilings for PiTun's own parts. Nothing is limited unless a value is
// typed here; empty means "no limit".
export function ResourceLimitsForm() {
  const t = useT()
  const qc = useQueryClient()
  const { data, isLoading, isError } = useQuery({ queryKey: ['resources', 'limits'], queryFn: () => resourcesApi.limits() })
  const [draft, setDraft] = useState<Draft | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => { if (data && !draft) setDraft(toDraft(data.limits)) }, [data, draft])

  const save = useMutation({
    mutationFn: (d: Draft) => resourcesApi.saveLimits({
      backend: { mem_mb: Number(d.backend.mem_mb) || null, cpus: Number(d.backend.cpus) || null },
      frontend: { mem_mb: Number(d.frontend.mem_mb) || null, cpus: Number(d.frontend.cpus) || null },
      nginx: { mem_mb: Number(d.nginx.mem_mb) || null, cpus: Number(d.nginx.cpus) || null },
      naive: { mem_mb: Number(d.naive.mem_mb) || null, cpus: Number(d.naive.cpus) || null },
      xray_mem_mb: Number(d.xray.mem_mb) || null,
    }),
    onSuccess: (r) => {
      const failed = r.results.filter((x) => !x.ok)
      setNotice(
        r.docker_error
          ? `${t('Saved, but not applied:', 'Сохранено, но не применено:')} ${r.docker_error}`
          : failed.length
            ? `${t('Applied, except:', 'Применено, кроме:')} ${failed.map((f) => `${f.container} — ${f.error}`).join('; ')}`
            : t('Applied.', 'Применено.')
              + (r.xray_restart_needed ? ' ' + t('The xray cap takes effect the next time xray restarts.', 'Лимит xray вступит в силу при следующем перезапуске xray.') : ''),
      )
      setDraft(toDraft(r.limits))
      qc.invalidateQueries({ queryKey: ['resources'] })
    },
  })

  if (isLoading) return <div className="h-24 animate-pulse rounded-lg bg-gray-900/50" />
  if (isError || !data || !draft) return <p className="text-xs text-gray-500">{t('Limits are unavailable.', 'Лимиты недоступны.')}</p>

  const set = (k: string, f: 'mem_mb' | 'cpus', v: string) => setDraft({ ...draft, [k]: { ...draft[k], [f]: v } })
  const input = 'w-24 rounded-sm bg-gray-950 border border-gray-800 px-2 py-1 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden'
  const effective = (k: (typeof TARGETS)[number]) => {
    const rows = data.effective[k] ?? []
    if (!rows.length) return '—'
    return rows.map((r) => `${r.mem_mb ? r.mem_mb + ' MB' : t('no memory cap', 'память без лимита')}, ${r.cpus ? r.cpus + ' CPU' : t('no CPU cap', 'CPU без лимита')}`).join(' · ')
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-400 max-w-3xl">
        {t(
          'Off by default: nothing is limited. A cap keeps one part of PiTun from starving the rest of a small box, at the price that the part is killed (memory) or slowed (CPU) when it reaches it — so leave room above what it normally uses. Empty = no limit. Limits are applied to the running containers and put back after a restart.',
          'По умолчанию выключено: ничего не ограничено. Лимит не даёт одной части PiTun задушить остальное на слабой коробке, но часть будет убита (память) или замедлена (CPU) при достижении лимита, поэтому оставляйте запас над обычным потреблением. Пусто = без лимита. Лимиты применяются к работающим контейнерам и возвращаются после перезапуска.',
        )}
      </p>
      <div className="overflow-x-auto">
        <table className="text-sm">
          <thead className="text-xs text-gray-500">
            <tr>
              <th className="pr-4 py-1 text-left font-medium">{t('Part', 'Часть')}</th>
              <th className="pr-3 py-1 text-left font-medium">{t('Memory, MB', 'Память, МБ')}</th>
              <th className="pr-3 py-1 text-left font-medium">CPU</th>
              <th className="py-1 text-left font-medium">{t('Now', 'Сейчас')}</th>
            </tr>
          </thead>
          <tbody>
            {TARGETS.map((k) => (
              <tr key={k}>
                <td className="pr-4 py-1 text-gray-200">{data.targets[k].label}</td>
                <td className="pr-3 py-1">
                  <input type="number" min={data.targets[k].min_mb} max={data.host.mem_mb} className={input} placeholder={t('none', 'нет')}
                    value={draft[k].mem_mb} onChange={(e) => set(k, 'mem_mb', e.target.value)} aria-label={`${data.targets[k].label} memory`} />
                </td>
                <td className="pr-3 py-1">
                  <input type="number" min={0.1} max={data.host.cpus} step={0.1} className={input} placeholder={t('none', 'нет')}
                    value={draft[k].cpus} onChange={(e) => set(k, 'cpus', e.target.value)} aria-label={`${data.targets[k].label} CPUs`} />
                </td>
                <td className="py-1 text-[11px] text-gray-500">{effective(k)}</td>
              </tr>
            ))}
            <tr>
              <td className="pr-4 py-1 text-gray-200">{t('xray soft memory cap', 'Мягкий лимит памяти xray')}</td>
              <td className="pr-3 py-1">
                <input type="number" min={data.xray_min_mb} max={data.host.mem_mb} className={input} placeholder={t('none', 'нет')}
                  value={draft.xray.mem_mb} onChange={(e) => set('xray', 'mem_mb', e.target.value)} aria-label="xray memory" />
              </td>
              <td />
              <td className="py-1 text-[11px] text-gray-500">{t('the garbage collector works harder near it; applies at the next xray restart', 'сборщик мусора работает активнее рядом с ним; вступает при следующем перезапуске xray')}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-gray-600">
        {t(`This box: ${data.host.mem_mb} MB, ${data.host.cpus} CPU. Docker cannot lift a memory limit on a running container, so “empty” sets it to the box's whole memory.`,
           `Эта система: ${data.host.mem_mb} МБ, ${data.host.cpus} CPU. Docker не умеет снимать лимит памяти у работающего контейнера, поэтому «пусто» ставит всю память системы.`)}
      </p>
      {data.docker_error && <p className="text-xs text-amber-400">{data.docker_error}</p>}
      {save.isError && <p className="text-xs text-red-400">{apiErrorText(save.error, t('Could not save', 'Не удалось сохранить'))}</p>}
      {notice && <p className="text-xs text-emerald-400">{notice}</p>}
      <div className="flex gap-2">
        <button type="button" disabled={save.isPending} onClick={() => save.mutate(draft)}
          className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50">
          {save.isPending ? t('Applying…', 'Применяю…') : t('Apply', 'Применить')}
        </button>
        <button type="button" disabled={save.isPending}
          onClick={() => { const blank = toDraft({ backend: {}, frontend: {}, nginx: {}, naive: {}, xray_mem_mb: null } as unknown as ResourceLimits); setDraft(blank); save.mutate(blank) }}
          className="rounded-lg bg-gray-800 px-4 py-1.5 text-sm text-gray-300 hover:bg-gray-700 disabled:opacity-50">
          {t('Remove all limits', 'Снять все лимиты')}
        </button>
      </div>
    </div>
  )
}
