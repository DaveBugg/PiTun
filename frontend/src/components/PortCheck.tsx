import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { AlertTriangle, Loader2, Plus, RefreshCw } from 'lucide-react'
import { xuiApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import type { XuiPortStatus } from '@/types'

/** Ports worth checking first — the usual Reality / TLS picks. */
export const POPULAR_PORTS = [443, 8443, 2443]

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return v
}

const validPort = (p: number) => Number.isInteger(p) && p >= 1 && p <= 65535

/**
 * Which ports are free on an x-ui panel's VPS: panel inbounds + one SSH
 * `ss`/`ufw` probe. Shows a chip per port; clicking a free one calls
 * `onPick`. `selected` (the port currently typed in the owning form) is
 * always checked and gets a one-line explanation under the chips.
 */
export function PortCheck({
  serverId,
  selected,
  onPick,
  direct = false,
  allowCustom = false,
  label,
  className,
}: {
  serverId: number | null | undefined
  selected?: number
  onPick?: (port: number) => void
  direct?: boolean
  allowCustom?: boolean
  label?: string
  className?: string
}) {
  const t = useT()
  const [custom, setCustom] = useState<number[]>([])
  const [draft, setDraft] = useState('')
  const typed = useDebounced(selected ?? 0, 500)

  const candidates = useMemo(() => {
    const out: number[] = []
    for (const p of [...POPULAR_PORTS, ...custom, typed]) {
      if (validPort(p) && !out.includes(p)) out.push(p)
    }
    return out
  }, [custom, typed])

  const { data, isFetching, error, refetch } = useQuery({
    queryKey: ['xui', 'ports', serverId, candidates.join(','), direct],
    queryFn: () => xuiApi.checkPorts(serverId!, candidates, direct),
    enabled: serverId != null,
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: false,
    placeholderData: (prev) => prev,
  })

  const byPort = new Map<number, XuiPortStatus>((data?.ports ?? []).map((r) => [r.port, r]))

  const addCustom = () => {
    const p = Number(draft)
    if (validPort(p) && !custom.includes(p)) setCustom((c) => [...c, p])
    setDraft('')
  }

  const ufwText = (u: XuiPortStatus['ufw']) => {
    switch (u) {
      case 'allow': return t('ufw: open', 'ufw: открыт')
      case 'closed': return t('ufw: closed (PiTun opens it on create)', 'ufw: закрыт (PiTun откроет при создании)')
      case 'inactive': return t('ufw: off', 'ufw выключен')
      case 'absent': return t('no ufw', 'ufw нет')
      default: return null
    }
  }

  const describe = (r: XuiPortStatus): string => {
    const parts: string[] = []
    if (r.status === 'free') parts.push(t('free', 'свободен'))
    else if (r.status === 'reserved') parts.push(t(`reserved: ${r.reason ?? ''}`, `зарезервирован: ${r.reason ?? ''}`))
    for (const ib of r.inbounds) {
      parts.push(
        t(`inbound #${ib.id} "${ib.remark}"`, `инбаунд #${ib.id} "${ib.remark}"`)
        + (ib.enabled ? '' : t(' (disabled)', ' (выключен)'))
        + (ib.known_here ? '' : t(' — not managed by this PiTun', ' — не из этого PiTun')),
      )
    }
    if (r.listeners?.length) parts.push(t(`listening: ${r.listeners.join(', ')}`, `слушает: ${r.listeners.join(', ')}`))
    if (r.status !== 'free' && !r.inbounds.length && !r.listeners?.length && r.reason) parts.push(r.reason)
    const u = ufwText(r.ufw)
    if (u) parts.push(u)
    return parts.join(' · ')
  }

  if (serverId == null) return null

  const sel = selected && validPort(selected) ? byPort.get(selected) : undefined

  return (
    <div className={clsx('space-y-1', className)}>
      <div className="flex items-center gap-1.5 flex-wrap">
        {label && <span className="text-[11px] text-gray-500">{label}</span>}
        {candidates.map((p) => {
          const r = byPort.get(p)
          const pickable = !!onPick && r?.status === 'free'
          return (
            <button
              key={p}
              type="button"
              onClick={() => pickable && onPick!(p)}
              title={r ? describe(r) : t('checking…', 'проверяю…')}
              className={clsx(
                'rounded-md border px-1.5 py-0.5 text-[11px] font-mono inline-flex items-center gap-1 transition-colors',
                !r && 'border-gray-700 text-gray-500',
                r?.status === 'free' && 'border-green-300 bg-green-50 text-green-700 dark:border-green-700/50 dark:bg-green-900/20 dark:text-green-300',
                r?.status === 'taken' && 'border-red-300 bg-red-50 text-red-700 dark:border-red-700/50 dark:bg-red-900/20 dark:text-red-300',
                r?.status === 'reserved' && 'border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700/50 dark:bg-amber-900/20 dark:text-amber-300',
                pickable ? 'hover:brightness-110 cursor-pointer' : 'cursor-default',
                selected === p && 'ring-1 ring-brand-500',
              )}
            >
              {!r && isFetching && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
              {p}
              {r && (r.status === 'free' ? ' ✓' : ' ✗')}
            </button>
          )
        })}
        {allowCustom && (
          // Not a <form>: this component sits inside other forms.
          <span className="inline-flex items-center gap-1">
            <input
              type="number"
              min={1}
              max={65535}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  addCustom()
                }
              }}
              placeholder={t('port', 'порт')}
              className="w-20 rounded-md bg-gray-900 border border-gray-800 px-1.5 py-0.5 text-[11px] text-gray-100 focus:border-brand-500 focus:outline-hidden"
            />
            <button
              type="button"
              onClick={addCustom}
              disabled={!validPort(Number(draft))}
              title={t('Check this port too', 'Проверить и этот порт')}
              className="rounded-md border border-gray-700 hover:bg-gray-800 disabled:opacity-40 p-1 text-gray-300"
            >
              <Plus className="h-3 w-3" />
            </button>
          </span>
        )}
        <button
          type="button"
          onClick={() => refetch()}
          disabled={isFetching}
          title={t('Re-check', 'Проверить заново')}
          className="text-gray-500 hover:text-gray-300 p-0.5 disabled:opacity-50"
        >
          <RefreshCw className={clsx('h-3 w-3', isFetching && 'animate-spin')} />
        </button>
      </div>
      {sel && (
        <div className={clsx(
          'text-[10px] leading-tight',
          sel.status === 'free' ? 'text-green-700 dark:text-green-400' : 'text-red-600 dark:text-red-400',
        )}>
          {sel.port}: {describe(sel)}
        </div>
      )}
      {error && (
        <div className="text-[10px] text-red-600 dark:text-red-400 flex items-center gap-1">
          <AlertTriangle className="h-3 w-3" />
          {t('Port check failed', 'Не удалось проверить порты')}
        </div>
      )}
      {data?.ssh_error && (
        <div className="text-[10px] text-amber-600 dark:text-amber-400 leading-tight">
          {t(
            `SSH probe skipped (${data.ssh_error}) — only panel inbounds were checked`,
            `SSH-проверка не выполнена (${data.ssh_error}) — учтены только инбаунды панели`,
          )}
        </div>
      )}
      {data?.panel_error && (
        <div className="text-[10px] text-amber-600 dark:text-amber-400 leading-tight">
          {t(`Panel unreachable (${data.panel_error})`, `Панель недоступна (${data.panel_error})`)}
        </div>
      )}
    </div>
  )
}
