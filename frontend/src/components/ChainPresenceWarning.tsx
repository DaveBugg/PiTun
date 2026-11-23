import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { serversApi } from '@/api/client'
import { useT } from '@/hooks/useT'

/**
 * Warning shown before an x-ui reinstall/uninstall: the server's panel
 * may host chain inbounds — this install's (registered chains or
 * `[pitun:<our id>]` marks) or ANOTHER PiTun install's. Renders nothing
 * when the panel is clean or the check hasn't finished.
 */
export function ChainPresenceWarning({ serverId }: { serverId: number }) {
  const t = useT()
  const { data } = useQuery({
    queryKey: ['servers', serverId, 'chain-presence'],
    queryFn: () => serversApi.chainPresence(serverId),
    staleTime: 30_000,
    retry: false,
  })
  if (!data) return null

  const own = data.own_marked_inbounds ?? 0
  const foreign = data.foreign_marked_inbounds ?? 0
  const legacy = data.legacy_chain_inbounds ?? 0
  const parts: string[] = []
  if (data.local_chains > 0) {
    parts.push(t(
      `${data.local_chains} chain(s) registered in this PiTun`,
      `чейнов в этом PiTun: ${data.local_chains}`,
    ))
  }
  if (own > 0) {
    parts.push(t(
      `${own} live chain inbound(s) of this install on the panel`,
      `chain-инбаундов этой установки на панели: ${own}`,
    ))
  }
  if (foreign > 0) {
    parts.push(t(
      `${foreign} inbound(s) marked by ANOTHER PiTun install`,
      `инбаундов от ДРУГОГО PiTun: ${foreign}`,
    ))
  }
  if (legacy > 0) {
    parts.push(t(
      `${legacy} unmarked chain inbound(s) (created before ownership marks)`,
      `немаркированных chain-инбаундов (до маркировки): ${legacy}`,
    ))
  }
  if (!parts.length) return null

  return (
    <div className="rounded-lg border border-yellow-200 dark:border-yellow-700/40 bg-yellow-50 dark:bg-yellow-900/20 px-3 py-2 text-xs text-yellow-800 dark:text-yellow-200 flex items-start gap-2">
      <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
      <div>
        <div className="font-semibold">
          {t('This panel hosts proxy chains', 'На этой панели живут прокси-чейны')}
        </div>
        <div className="mt-0.5">{parts.join(' · ')}</div>
        <div className="mt-1 text-yellow-700 dark:text-yellow-300/80">
          {t(
            'A reinstall/uninstall wipes every inbound on the panel, including other installs’ chains. Delete the chains from their owner PiTun first, or re-register the panel afterwards and re-sync.',
            'Переустановка/удаление сносит ВСЕ инбаунды панели, включая чейны других установок. Сначала удалите чейны из их PiTun, либо после — перерегистрируйте панель и сделайте sync.',
          )}
        </div>
        {data.error && (
          <div className="mt-1 text-yellow-700 dark:text-yellow-300/60">
            {t('Live panel check failed (DB-only answer): ', 'Живая проверка панели не удалась (только данные БД): ')}
            {data.error}
          </div>
        )}
      </div>
    </div>
  )
}
