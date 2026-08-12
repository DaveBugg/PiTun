import { useQuery } from '@tanstack/react-query'
import { Cable, Loader2, AlertTriangle, CheckCircle2 } from 'lucide-react'
import { clsx } from 'clsx'

import { networkApi } from '@/api/client'
import { useT } from '@/hooks/useT'

/**
 * Operating mode + physical NIC inventory (router mode, phase 0).
 *
 * "Router" is only offered when the box actually has two or more real NICs —
 * it needs one facing the ISP and one facing the LAN. The backend enforces the
 * same rule; this is the explanation, not the gate.
 *
 * Phase 0 records the choice and changes nothing else: DHCP, NAT and WAN
 * handling arrive in later phases.
 */
export default function OperatingModeSection({
  value, onChange,
}: {
  value: string
  onChange: (mode: string) => void
}) {
  const t = useT()
  const { data, isLoading } = useQuery({
    queryKey: ['network-interfaces'],
    queryFn: () => networkApi.interfaces(),
    refetchInterval: 30_000,
  })

  const capable = data?.router_capable ?? false
  const nics = data?.items ?? []

  return (
    <div className="space-y-3">
      {/* NIC inventory */}
      <div>
        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1.5">
          {t('Network ports', 'Сетевые порты')}
          {data && <span className="ml-1.5 text-gray-600 normal-case">({data.count})</span>}
        </div>
        {isLoading ? (
          <div className="flex items-center gap-2 text-[12px] text-gray-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t('Detecting…', 'Определяем…')}
          </div>
        ) : nics.length === 0 ? (
          <div className="text-[12px] text-gray-500">
            {t('No physical interfaces detected.', 'Физические интерфейсы не найдены.')}
          </div>
        ) : (
          <div className="space-y-1">
            {nics.map((n) => (
              <div
                key={n.name}
                className="flex items-center gap-2 rounded-md border border-gray-800 bg-gray-950/40 px-2.5 py-1.5 text-[12px]"
              >
                <Cable className={clsx('h-3.5 w-3.5 shrink-0', n.carrier ? 'text-emerald-500' : 'text-gray-600')} />
                <span className="font-mono text-gray-200">{n.name}</span>
                <span className="font-mono text-gray-600 text-[11px]">{n.mac}</span>
                <span className="flex-1" />
                {n.ipv4 && (
                  <span className="font-mono text-gray-400">
                    {n.ipv4}{n.cidr != null ? `/${n.cidr}` : ''}
                  </span>
                )}
                {n.is_default_route && (
                  <span className="rounded-sm border border-brand-200 bg-brand-50 text-brand-700 dark:border-brand-800/50 dark:bg-brand-950/30 dark:text-brand-300 px-1.5 py-0.5 text-[10px]">
                    {t('uplink', 'аплинк')}
                  </span>
                )}
                <span className={clsx('text-[10px]', n.carrier ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-600')}>
                  {n.carrier ? t('link up', 'линк есть') : t('no link', 'нет линка')}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Mode choice */}
      <div>
        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1.5">
          {t('Operating mode', 'Режим работы')}
        </div>
        <div className="space-y-2">
          <label className="flex items-start gap-2 rounded-lg border border-gray-800 bg-gray-950/40 p-2.5 cursor-pointer hover:bg-gray-900/60">
            <input
              type="radio"
              className="mt-0.5"
              checked={value !== 'router'}
              onChange={() => onChange('gateway')}
            />
            <div>
              <div className="text-[12px] text-gray-200">
                {t('Gateway — beside your router', 'Шлюз — рядом с роутером')}
                <span className="ml-2 text-[10px] text-gray-500">{t('default', 'по умолчанию')}</span>
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                {t(
                  'Devices point their gateway at PiTun; your existing router keeps doing DHCP and NAT. Works on any hardware, including a single port.',
                  'Устройства указывают шлюзом PiTun; DHCP и NAT остаются на вашем роутере. Работает на любом железе, в том числе с одним портом.',
                )}
              </div>
            </div>
          </label>

          <label
            className={clsx(
              'flex items-start gap-2 rounded-lg border p-2.5',
              capable
                ? 'border-gray-800 bg-gray-950/40 cursor-pointer hover:bg-gray-900/60'
                : 'border-gray-800 bg-gray-950/20 opacity-60 cursor-not-allowed',
            )}
          >
            <input
              type="radio"
              className="mt-0.5"
              disabled={!capable}
              checked={value === 'router'}
              onChange={() => onChange('router')}
            />
            <div>
              <div className="text-[12px] text-gray-200">
                {t('Router — PiTun is the router', 'Роутер — PiTun и есть роутер')}
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                {t(
                  'PiTun owns the ISP uplink, hands out addresses on the LAN and does NAT. Needs two or more ports.',
                  'PiTun берёт на себя аплинк провайдера, раздаёт адреса в LAN и делает NAT. Нужно два и более порта.',
                )}
              </div>
            </div>
          </label>
        </div>

        {!capable && !isLoading && (
          <div className="mt-2 flex items-start gap-2 rounded-md border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/30 p-2 text-[11px] text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            {t(
              `Router mode needs at least 2 physical ports — this box has ${data?.count ?? 0}.`,
              `Для режима роутера нужно минимум 2 физических порта — на этой машине ${data?.count ?? 0}.`,
            )}
          </div>
        )}

        {value === 'router' && (
          <div className="mt-2 flex items-start gap-2 rounded-md border border-brand-200 dark:border-brand-800/50 bg-brand-50 dark:bg-brand-950/30 p-2 text-[11px] text-brand-800 dark:text-brand-300">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            {t(
              'Recorded. Port roles, DHCP, NAT and WAN setup arrive in the next releases — nothing has changed on the network yet.',
              'Записано. Роли портов, DHCP, NAT и настройка WAN появятся в следующих релизах — в сети пока ничего не изменилось.',
            )}
          </div>
        )}
      </div>
    </div>
  )
}
