import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cpu, Loader2, RotateCcw } from 'lucide-react'
import { xuiApi } from '@/api/client'
import { useConfirm } from '@/components/ConfirmModal'
import { apiErrorText } from '@/lib/apiError'
import { useT } from '@/hooks/useT'
import type { XuiXrayInfo, XuiXrayUpdateResult } from '@/types'

// The xray core running inside one x-ui panel, and a way to change it — the
// same call as the panel's own "switch xray version", with PiTun's guard rails
// (a panel older than 3.8.0 is not offered the 26.9+ cores).
export function PanelXrayCard({ serverId }: { serverId: number }) {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [picked, setPicked] = useState('')
  const [result, setResult] = useState<XuiXrayUpdateResult | null>(null)

  const { data, isLoading, error, refetch } = useQuery<XuiXrayInfo>({
    queryKey: ['xui', 'xray', serverId],
    queryFn: () => xuiApi.panelXray(serverId),
    refetchOnWindowFocus: false,
    retry: false,
  })

  const update = useMutation({
    mutationFn: (version: string) => xuiApi.updatePanelXray(serverId, version),
    onSuccess: (r) => {
      setResult(r)
      qc.invalidateQueries({ queryKey: ['xui', 'xray', serverId] })
    },
  })

  const install = async (version: string, rollback = false) => {
    const ok = await confirm({
      title: rollback
        ? t(`Roll back xray to ${version}?`, `Откатить xray до ${version}?`)
        : t(`Install xray ${version} on this panel?`, `Установить xray ${version} на эту панель?`),
      body: t(
        'The panel stops xray, downloads this release and starts it again. Connections through this panel drop for a few seconds.',
        'Панель остановит xray, скачает этот релиз и запустит его снова. Соединения через эту панель оборвутся на несколько секунд.',
      ),
      confirmLabel: t('Install', 'Установить'),
    })
    if (!ok) return
    setResult(null)
    update.mutate(version)
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-xs text-gray-500">
        <Loader2 className="h-3 w-3 animate-spin" /> {t('Reading xray version…', 'Читаю версию xray…')}
      </div>
    )
  }
  if (error || !data) {
    return (
      <div className="text-xs text-gray-500">
        {t('xray version unavailable', 'Версия xray недоступна')} · {apiErrorText(error, t('panel did not answer', 'панель не ответила'))}{' '}
        <button className="underline" onClick={() => refetch()}>{t('retry', 'повторить')}</button>
      </div>
    )
  }

  const running = data.xray_state === 'running'
  const choices = data.versions.filter((v) => !v.current)
  const selected = choices.find((v) => v.version === picked)

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-2 space-y-2">
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <Cpu className="h-3.5 w-3.5 text-gray-500" />
        <span className="text-gray-300">
          xray <span className="font-mono">{data.xray_version ?? '?'}</span>
        </span>
        <span className={running ? 'text-emerald-400' : 'text-red-400'}>
          ● {running ? t('running', 'работает') : (data.xray_state ?? t('unknown', 'неизвестно'))}
        </span>
        <span className="text-gray-600">·</span>
        <span className="text-gray-500">
          {t('panel', 'панель')} <span className="font-mono">{data.panel_version ?? '?'}</span>
        </span>
        <div className="ml-auto flex items-center gap-2">
          <select
            value={picked}
            onChange={(e) => setPicked(e.target.value)}
            className="rounded-sm bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100"
            aria-label={t('xray version', 'версия xray')}
          >
            <option value="">{t('Pick a version…', 'Выберите версию…')}</option>
            {choices.map((v) => (
              <option key={v.tag} value={v.version} disabled={!!v.blocked}>
                {v.version}{v.newer ? ` (${t('newer', 'новее')})` : ` (${t('older', 'старее')})`}{v.blocked ? ' ⛔' : ''}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={!selected || !!selected.blocked || update.isPending}
            onClick={() => selected && install(selected.version, !selected.newer)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1 text-xs font-medium text-white hover:bg-brand-500 disabled:opacity-50"
          >
            {update.isPending
              ? <Loader2 className="h-3 w-3 animate-spin" />
              : selected && !selected.newer ? <RotateCcw className="h-3 w-3" /> : null}
            {update.isPending ? t('Installing…', 'Устанавливаю…') : t('Install', 'Установить')}
          </button>
        </div>
      </div>

      {data.xray_error && <p className="text-xs text-red-400">{data.xray_error}</p>}
      {choices.some((v) => v.blocked) && (
        <p className="text-[11px] text-gray-500">
          ⛔ {choices.find((v) => v.blocked)?.blocked}
        </p>
      )}
      {update.isError && (
        <p className="text-xs text-red-400">{apiErrorText(update.error, t('Update failed', 'Не удалось обновить'))}</p>
      )}
      {result && (
        <div className={`text-xs ${result.ok ? 'text-emerald-400' : 'text-red-400'}`}>
          {result.message}
          {!result.ok && result.previous_version && (
            <button
              type="button"
              className="ml-2 underline"
              onClick={() => install(result.previous_version!, true)}
            >
              {t(`Roll back to ${result.previous_version}`, `Откатить до ${result.previous_version}`)}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
