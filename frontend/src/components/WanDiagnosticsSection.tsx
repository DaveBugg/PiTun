import { useQuery } from '@tanstack/react-query'
import { Activity, CheckCircle2, AlertTriangle, XCircle, Loader2 } from 'lucide-react'
import { clsx } from 'clsx'

import { networkApi } from '@/api/client'
import { useT } from '@/hooks/useT'

/**
 * Uplink diagnosis (router mode).
 *
 * Exists because the two rules the WAN depends on fail silently: without DHCP
 * replies the uplink never gets an address, and without ICMP coming back a
 * path-MTU problem makes large transfers hang while DNS and ping look fine.
 * Neither logs anything, so the counters attached to those rules are the only
 * way to tell them apart without packet captures.
 */
export default function WanDiagnosticsSection() {
  const t = useT()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['wan-diagnose'],
    queryFn: () => networkApi.diagnoseWan(),
    refetchInterval: 30_000,
  })

  const icon = (level: string) =>
    level === 'ok' ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 mt-0.5 text-emerald-500" />
      : level === 'warn' ? <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5 text-amber-500" />
      : <XCircle className="h-3.5 w-3.5 shrink-0 mt-0.5 text-red-500" />

  return (
    <div className="mt-3">
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-gray-500 mb-1.5">
        <Activity className="h-3.5 w-3.5 text-brand-400" />
        {t('Uplink diagnosis', 'Диагностика аплинка')}
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-[12px] text-gray-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {t('Reading counters…', 'Читаем счётчики…')}
        </div>
      ) : isError ? (
        <div className="text-[12px] text-gray-500">
          {t('Could not read the uplink counters.', 'Не удалось прочитать счётчики аплинка.')}
        </div>
      ) : (
        <div className="space-y-1.5">
          {data?.findings.map((f, i) => (
            <div
              key={i}
              className={clsx(
                'flex items-start gap-2 rounded-md border p-2 text-[11px]',
                f.level === 'warn'
                  ? 'border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/30 text-amber-800 dark:text-amber-300'
                  : f.level === 'error'
                  ? 'border-red-200 dark:border-red-800/50 bg-red-50 dark:bg-red-950/30 text-red-800 dark:text-red-300'
                  : 'border-gray-800 bg-gray-950/40 text-gray-400',
              )}
            >
              {icon(f.level)}
              <div>
                <div className="font-medium">{f.title}</div>
                <div className="mt-0.5 opacity-90">{f.detail}</div>
                {f.hint && <div className="mt-0.5 opacity-70">{f.hint}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
