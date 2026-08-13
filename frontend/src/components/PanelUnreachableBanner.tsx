import { useEffect, useState } from 'react'
import { PlugZap } from 'lucide-react'

import { useT } from '@/hooks/useT'
import { lanPanelUrl, subscribeReachability } from '@/lib/panelReachability'

/**
 * Shown when the box has stopped answering at the address this page is open on.
 *
 * The case worth explaining is router mode: the WAN port refuses new
 * connections by design, and the person who switched it is looking at the
 * panel on exactly that port. The bundle stays in the browser's cache, so the
 * UI keeps rendering and only the console shows anything wrong. Naming the LAN
 * address turns a mystery into one click.
 */
export default function PanelUnreachableBanner() {
  const t = useT()
  const [down, setDown] = useState(false)

  useEffect(() => subscribeReachability(setDown), [])

  if (!down) return null
  const lan = lanPanelUrl()

  return (
    <div className="sticky top-0 z-50 border-b border-red-300 dark:border-red-800/60 bg-red-50 dark:bg-red-950/60 px-4 py-2.5">
      <div className="flex items-center gap-3 flex-wrap">
        <PlugZap className="h-4 w-4 shrink-0 text-red-600 dark:text-red-400" />
        <span className="text-xs text-red-800 dark:text-red-200">
          {t(
            'PiTun is not answering at this address.',
            'PiTun не отвечает по этому адресу.',
          )}{' '}
          {lan
            ? t(
                'If you just switched it to router mode, this address is on the uplink side and no longer serves the panel — the LAN side does.',
                'Если вы только что перевели его в режим роутера, этот адрес со стороны аплинка и панель больше не обслуживает — обслуживает сторона LAN.',
              )
            : t(
                'The box may be restarting, or this address is on the uplink side after a switch to router mode.',
                'Коробка может перезапускаться — либо этот адрес со стороны аплинка после перехода в режим роутера.',
              )}
        </span>
        {lan && (
          <a
            href={lan}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-500 transition-colors"
          >
            {t('Open', 'Открыть')} {lan}
          </a>
        )}
      </div>
    </div>
  )
}
