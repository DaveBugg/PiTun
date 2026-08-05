/**
 * Shared explainer for the TWO host-DNS knobs that confusingly live on
 * two different pages:
 *
 *   - Settings → Host network   (gateway + DNS)  — the box's PRIMARY DNS
 *   - DNS      → Host resolver   (fallback only)  — additive BACKUP DNS
 *
 * Both configure DNS for the PiTun box ITSELF (its own outbound lookups:
 * subscriptions, geo files, x-ui panels, health checks) — neither touches
 * what LAN clients resolve (that's xray's DNS rules + primary upstream).
 * Operators kept asking "what's the difference / why are there two?", so
 * this one component is dropped on BOTH pages with `highlight` pointing at
 * whichever block is local to the current page.
 *
 * Click-anchored popover modelled on VersionPopover: portal into
 * document.body (escape any backdrop-blur-sm stacking context), position via
 * getBoundingClientRect, click-outside + Escape to close.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Info, ArrowDown } from 'lucide-react'
import { clsx } from 'clsx'
import { useT } from '@/hooks/useT'

interface Props {
  /** Emphasise the block that belongs to the page this help is shown on. */
  highlight?: 'network' | 'resolver'
  className?: string
}

const POP_W = 344

export function HostDnsHelp({ highlight, className }: Props) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [coords, setCoords] = useState<{ left: number; top: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)

  // Anchor below the trigger, left-aligned but clamped into the viewport
  // so the 344px card never spills off the right edge on a narrow screen.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - POP_W - 12))
    setCoords({ left, top: rect.bottom + 6 })
  }, [open])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (popRef.current?.contains(e.target as Node) ||
          triggerRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(v => !v)}
        className={clsx(
          'inline-flex items-center gap-1 rounded-sm border border-gray-700 px-1.5 py-0.5',
          'text-[10px] text-gray-400 hover:text-brand-700 dark:hover:text-brand-300 hover:border-brand-400/50 transition-colors',
          open && 'text-brand-300 border-brand-700/50',
          className,
        )}
        aria-label={t('What is this? Host DNS vs host network',
                      'Что это? Host DNS и host-сеть')}
      >
        <Info className="h-3 w-3" />
        {t('What is this?', 'Что это?')}
      </button>

      {open && coords && createPortal(
        <div
          ref={popRef}
          role="dialog"
          aria-label={t('Host DNS help', 'Справка по host DNS')}
          style={{ position: 'fixed', left: coords.left, top: coords.top, width: POP_W }}
          className="z-9999 rounded-lg border border-gray-700 bg-gray-900 p-3 shadow-2xl
                     text-[11px] leading-relaxed text-gray-300 space-y-2.5"
        >
          <div className="text-xs font-semibold text-gray-100">
            {t('Two host-DNS knobs — what differs?',
               'Две host-DNS настройки — в чём разница?')}
          </div>

          <p className="text-gray-400">
            {t(
              'Both set DNS for the PiTun box ITSELF (its own lookups: subscriptions, geo files, x-ui panels, health checks). Neither changes what your LAN devices resolve — that is the DNS Rules + Primary upstream above, handled by xray.',
              'Обе настраивают DNS для самой машины PiTun (её собственные запросы: подписки, гео-файлы, x-ui панели, health-проверки). Ни одна не меняет то, что резолвят твои LAN-устройства — это DNS-правила + Primary upstream выше, через xray.',
            )}
          </p>

          <Block
            emphasised={highlight === 'network'}
            where={t('Settings → Host network', 'Настройки → Host network')}
            tag={t('gateway + DNS', 'gateway + DNS')}
            role={t('PRIMARY', 'ОСНОВНОЙ')}
            roleColor="text-emerald-700 dark:text-emerald-300"
            lines={[
              t("The box's main network identity: default gateway + its main DNS servers.",
                'Главная сетевая идентичность машины: default gateway + основные DNS-серверы.'),
              t('Tried FIRST. Also sets where the box sends its outbound traffic.',
                'Пробуется ПЕРВЫМ. Ещё задаёт, куда машина шлёт исходящий трафик.'),
              t('Reconfigures host networking (brief blip possible). Has rollback backups.',
                'Переконфигурирует host-сеть (возможно короткое моргание). Есть бэкапы для отката.'),
            ]}
          />

          <div className="flex justify-center text-gray-600">
            <ArrowDown className="h-3 w-3" />
            <span className="ml-1 text-[9px] text-gray-600">
              {t('falls back to', 'откатывается на')}
            </span>
          </div>

          <Block
            emphasised={highlight === 'resolver'}
            where={t('DNS → Host resolver', 'DNS → Резолвер хоста')}
            tag={t('this box only · fallback', 'только эта машина · fallback')}
            role={t('BACKUP', 'ЗАПАСНОЙ')}
            roleColor="text-amber-700 dark:text-amber-300"
            lines={[
              t('ONLY extra fallback DNS, added on top of the primary.',
                'ТОЛЬКО дополнительный fallback-DNS, добавляется поверх основного.'),
              t("Used only if the primary can't resolve (router DNS down / routed through a dead VPN).",
                'Используется, только если основной не отвечает (DNS роутера лёг / завернулся в мёртвый VPN).'),
              t("Doesn't touch gateway or IP — non-disruptive, idempotent, survives reboot.",
                'Не трогает gateway или IP — без разрывов, идемпотентно, переживает reboot.'),
            ]}
          />

          <p className="text-gray-500 border-t border-gray-800 pt-2">
            {t(
              'Rule of thumb: set your real gateway + DNS in Settings; add public fallbacks (1.1.1.1 / 8.8.8.8) in DNS as a safety net. The same servers in both is harmless but redundant.',
              'Правило: реальные gateway + DNS задавай в Настройках; публичные fallback (1.1.1.1 / 8.8.8.8) добавляй в DNS как подстраховку. Одинаковые серверы в обоих местах не вредят, но избыточны.',
            )}
          </p>
        </div>,
        document.body,
      )}
    </>
  )
}

function Block({
  emphasised, where, tag, role, roleColor, lines,
}: {
  emphasised?: boolean
  where: string
  tag: string
  role: string
  roleColor: string
  lines: string[]
}) {
  return (
    <div className={clsx(
      'rounded-md border p-2 space-y-1',
      emphasised ? 'border-brand-700/50 bg-brand-900/15' : 'border-gray-800 bg-gray-950/40',
    )}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] text-gray-200">{where}</span>
        <span className={clsx('text-[9px] font-semibold uppercase tracking-wider', roleColor)}>
          {role}
        </span>
      </div>
      <div className="text-[10px] text-gray-500">{tag}</div>
      <ul className="space-y-0.5 text-gray-400">
        {lines.map((l, i) => <li key={i}>• {l}</li>)}
      </ul>
    </div>
  )
}
