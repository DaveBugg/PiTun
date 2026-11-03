import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpCircle, Loader2 } from 'lucide-react'
import { xuiApi } from '@/api/client'
import { useConfirm } from '@/components/ConfirmModal'
import { apiErrorText } from '@/lib/apiError'
import { useT } from '@/hooks/useT'
import type { XuiPanelUpdateInfo, XuiPanelUpdateResult } from '@/types'

// Updates the x-ui panel to the release PiTun has tested (not "whatever is
// newest": a newer panel may change the API PiTun uses). Runs a pinned updater
// over SSH; PiTun copies the panel database first and does not start without it.
export function PanelUpdateCard({ serverId }: { serverId: number }) {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [result, setResult] = useState<XuiPanelUpdateResult | null>(null)

  const { data, isLoading } = useQuery<XuiPanelUpdateInfo>({
    queryKey: ['xui', 'panel-update', serverId],
    queryFn: () => xuiApi.panelUpdateInfo(serverId),
    refetchOnWindowFocus: false,
    retry: false,
  })

  const update = useMutation({
    mutationFn: () => xuiApi.updatePanel(serverId),
    onSuccess: (r) => {
      setResult(r)
      qc.invalidateQueries({ queryKey: ['xui', 'panel-update', serverId] })
      qc.invalidateQueries({ queryKey: ['xui', 'xray', serverId] })
    },
  })

  const start = async () => {
    if (!data) return
    const ok = await confirm({
      title: t(`Update the panel to ${data.latest}?`, `Обновить панель до ${data.latest}?`),
      body: t(
        'This is the release PiTun has tested. PiTun copies the panel database on the VPS first (and stops if it cannot). The update then runs the new version\'s database migrations once and cannot be undone: going back means restoring that copy. It also replaces xray with the one bundled in that release. The panel is unavailable for a minute or two.',
        'Это релиз, проверенный в PiTun. Сначала PiTun копирует базу панели на VPS (без копии обновление не начнётся). Затем обновление один раз выполняет миграции базы новой версии и необратимо: вернуться можно только восстановлением этой копии. Заодно xray заменится на тот, что входит в релиз. Панель будет недоступна минуту-две.',
      ),
      confirmLabel: t('Back up and update', 'Скопировать и обновить'),
    })
    if (!ok) return
    setResult(null)
    update.mutate()
  }

  if (isLoading || !data) return null
  if (!data.update_available && !result) return null

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-2 space-y-1.5">
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <ArrowUpCircle className="h-3.5 w-3.5 text-gray-500" />
        <span className="text-gray-300">
          {t('Panel', 'Панель')} <span className="font-mono">{data.current}</span>
          {data.update_available && (
            <> → <span className="font-mono text-emerald-400">{data.latest}</span> {t('(tested)', '(проверена)')}</>
          )}
        </span>
        {data.update_available && (
          <button
            type="button"
            disabled={update.isPending}
            onClick={start}
            className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1 text-xs font-medium text-white hover:bg-brand-500 disabled:opacity-50"
          >
            {update.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            {update.isPending ? t('Updating…', 'Обновляю…') : t('Update panel', 'Обновить панель')}
          </button>
        )}
      </div>
      {update.isError && (
        <p className="text-xs text-red-400">{apiErrorText(update.error, t('Update failed', 'Не удалось обновить'))}</p>
      )}
      {result && (
        <p className={`text-xs ${result.ok ? 'text-emerald-400' : 'text-red-400'}`}>{result.message}</p>
      )}
    </div>
  )
}
