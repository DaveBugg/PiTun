import { useQuery } from '@tanstack/react-query'
import { Boxes } from 'lucide-react'
import { resourcesApi } from '@/api/client'
import { useT } from '@/hooks/useT'

// What each part does, for the "Description" column. Matched by name; naive
// sidecars are named per node, hence the prefix rule.
function describe(name: string, t: (en: string, ru: string) => string): string {
  if (name === 'pitun backend') return t('API and background jobs: subscriptions, checks, circles, statistics.', 'API и фоновые задачи: подписки, проверки, круги, статистика.')
  if (name === 'xray') return t('The core that routes traffic by your rules.', 'Ядро, которое маршрутизирует трафик по вашим правилам.')
  if (name === 'pitun-backend') return t('Container with the API and xray (both listed above).', 'Контейнер с API и xray (оба процесса выше).')
  if (name === 'pitun-nginx') return t('Front door: serves the web UI and proxies the API.', 'Входной прокси: отдаёт веб-интерфейс и проксирует API.')
  if (name === 'pitun-frontend') return t('The web UI files.', 'Файлы веб-интерфейса.')
  if (name === 'pitun-hostapd') return t('Wi-Fi access point (router mode).', 'Точка доступа Wi-Fi (режим роутера).')
  if (name === 'pitun-dnsmasq') return t('DHCP server for the LAN (router mode).', 'DHCP-сервер для LAN (режим роутера).')
  if (name.startsWith('pitun-naive')) return t('NaiveProxy sidecar for one node.', 'Сайдкар NaiveProxy для одной ноды.')
  return ''
}

// What PiTun itself costs the box: xray, the backend and each PiTun container.
// The host-wide gauges say the box is busy; this says by which part.
export function ProcessesCard() {
  const t = useT()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['resources', 'processes'],
    queryFn: () => resourcesApi.processes(),
    refetchInterval: 15_000,
    staleTime: 10_000,
  })

  if (isLoading) return <div className="h-24 rounded-xl border border-gray-800 bg-gray-900/50 animate-pulse" />
  if (isError || !data) return null

  const maxMem = Math.max(1, ...data.processes.map((p) => p.mem_mb))

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Boxes className="h-4 w-4 text-brand-400" />
          {t('What PiTun is using', 'Что использует PiTun')}
        </h2>
        <p className="text-[11px] text-gray-500 mt-0.5">
          {t(
            `CPU in % of one core (this box has ${data.host.cpu_count}); memory is the working set. The backend container includes xray and the backend process listed above it.`,
            `CPU в % от одного ядра (в этой системе ${data.host.cpu_count}); память — рабочий набор. Контейнер backend включает xray и процесс backend, перечисленные выше.`,
          )}
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-gray-500">
            <tr>
              <th className="px-4 py-2 text-left font-medium">{t('Part', 'Часть')}</th>
              <th className="px-3 py-2 text-left font-medium">{t('Description', 'Описание')}</th>
              <th className="px-3 py-2 text-right font-medium">CPU</th>
              <th className="px-3 py-2 text-left font-medium w-1/3">{t('Memory', 'Память')}</th>
              <th className="px-3 py-2 text-left font-medium">{t('Limit', 'Лимит')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {data.processes.map((p) => {
              const ofLimit = p.mem_limit_mb ? Math.min(100, Math.round((p.mem_mb / p.mem_limit_mb) * 100)) : null
              const width = Math.max(2, Math.round((p.mem_mb / maxMem) * 100))
              const hot = ofLimit !== null && ofLimit > 85
              return (
                <tr key={`${p.kind}:${p.name}`}>
                  <td className="px-4 py-1.5">
                    <div className="text-gray-100">{p.name}</div>
                    <div className="text-[11px] text-gray-500">{p.kind === 'container' ? t('container', 'контейнер') : t('process', 'процесс')} · {p.note}</div>
                  </td>
                  <td className="px-3 py-1.5 text-xs text-gray-400 max-w-[22rem]">{describe(p.name, t)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-gray-200">{p.cpu_percent.toFixed(1)}%</td>
                  <td className="px-3 py-1.5">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 flex-1 rounded-full bg-gray-800 overflow-hidden">
                        <div className={hot ? 'h-full bg-red-500' : 'h-full bg-brand-500'} style={{ width: `${width}%` }} />
                      </div>
                      <span className="tabular-nums text-gray-300 w-16 text-right">{p.mem_mb.toFixed(0)} MB</span>
                    </div>
                  </td>
                  <td className="px-3 py-1.5 text-xs text-gray-400 whitespace-nowrap">
                    {p.mem_limit_mb
                      ? <span className={hot ? 'text-red-400' : ''}>{p.mem_limit_mb.toFixed(0)} MB ({ofLimit}%)</span>
                      : t('none', 'нет')}
                  </td>
                </tr>
              )
            })}
            {data.processes.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-4 text-center text-gray-500">{t('Nothing to show yet.', 'Пока нечего показывать.')}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {data.docker_error && (
        <p className="px-4 py-2 text-[11px] text-amber-400 border-t border-gray-800">
          {t('Containers are not shown:', 'Контейнеры не показаны:')} {data.docker_error}.
        </p>
      )}
    </div>
  )
}
