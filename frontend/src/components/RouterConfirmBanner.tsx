import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldAlert, Loader2 } from 'lucide-react'

import { networkApi } from '@/api/client'
import { useT } from '@/hooks/useT'

/**
 * Commit-confirm banner for router mode.
 *
 * Router mode has no fallback — PiTun *is* the router — so an apply that
 * breaks the network would otherwise leave nobody able to undo it. The backend
 * reverts to gateway unless a human confirms, and this is where they do it.
 *
 * Rendered app-wide rather than on the Settings page: whoever needs to confirm
 * may well have reloaded the panel, landed on the dashboard, and would never
 * see a banner tucked inside a settings section. It polls fast, because the
 * number it shows is a countdown to the network changing under the user.
 */
export default function RouterConfirmBanner() {
  const t = useT()
  const qc = useQueryClient()
  const [now, setNow] = useState(Date.now())

  const { data } = useQuery({
    queryKey: ['router-mode-pending'],
    queryFn: () => networkApi.routerModePending(),
    refetchInterval: 5_000,
  })

  // Local ticking so the countdown moves smoothly between polls instead of
  // jumping in five-second steps.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const confirm = useMutation({
    mutationFn: () => networkApi.confirmRouterMode(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['router-mode-pending'] }),
  })

  if (!data?.pending) return null

  const deadline = data.deadline ? new Date(data.deadline).getTime() : 0
  const left = deadline ? Math.max(0, Math.round((deadline - now) / 1000)) : data.seconds_left
  const mm = String(Math.floor(left / 60)).padStart(2, '0')
  const ss = String(left % 60).padStart(2, '0')

  return (
    <div className="sticky top-0 z-50 border-b border-amber-300 dark:border-amber-800/60 bg-amber-50 dark:bg-amber-950/60 px-4 py-2.5">
      <div className="flex items-center gap-3 flex-wrap">
        <ShieldAlert className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="flex-1 min-w-[16rem] text-[12px] text-amber-900 dark:text-amber-200">
          <span className="font-medium">
            {t('Router mode is on trial.', 'Режим роутера на испытании.')}
          </span>{' '}
          {t(
            'If your network still works, confirm it. Otherwise PiTun switches back to gateway mode by itself — nothing you configured is lost.',
            'Если сеть работает — подтвердите. Иначе PiTun сам вернётся в режим шлюза, ничего из настроенного не потеряется.',
          )}
        </div>
        <span className="font-mono text-[13px] tabular-nums text-amber-900 dark:text-amber-200">
          {mm}:{ss}
        </span>
        <button
          type="button"
          onClick={() => confirm.mutate()}
          disabled={confirm.isPending}
          className="inline-flex items-center gap-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-50 px-3 py-1.5 text-[12px] font-medium text-white"
        >
          {confirm.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {t('Everything works — keep it', 'Всё работает — оставить')}
        </button>
      </div>
    </div>
  )
}
