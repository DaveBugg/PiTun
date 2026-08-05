import { clsx } from 'clsx'
import { Zap } from 'lucide-react'
import { useT } from '@/hooks/useT'

/**
 * "Direct connection" switch. Checked = the operation dials the server /
 * panel DIRECTLY (SO_MARK bypass), off the active node's tunnel; unchecked =
 * through the active node (default). Themed pill switch so it reads as a
 * deliberate on/off, not a stray checkbox.
 */
export function DirectToggle({
  checked,
  onChange,
  size = 'sm',
  className,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  size?: 'sm' | 'xs'
  className?: string
}) {
  const t = useT()
  const track = size === 'xs' ? 'h-4 w-7' : 'h-5 w-9'
  const knob = size === 'xs' ? 'h-3 w-3' : 'h-4 w-4'
  const shift = size === 'xs' ? 'translate-x-3' : 'translate-x-4'
  return (
    <label
      className={clsx(
        'inline-flex items-center gap-1.5 cursor-pointer select-none',
        size === 'xs' ? 'text-[11px]' : 'text-xs',
        className,
      )}
      title={t(
        'Connect directly, bypassing the active node — use when the VPN node is down',
        'Прямое подключение, минуя активную ноду — когда VPN-нода недоступна',
      )}
    >
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative shrink-0 rounded-full transition-colors ring-1',
          track,
          checked
            ? 'bg-brand-600 ring-brand-500/50'
            : 'bg-gray-700 ring-gray-600/50 hover:bg-gray-600',
        )}
      >
        <span
          className={clsx(
            'absolute top-0.5 left-0.5 rounded-full bg-white shadow-xs transition-transform',
            knob,
            checked && shift,
          )}
        />
      </button>
      <span
        className={clsx(
          'inline-flex items-center gap-1 transition-colors',
          checked ? 'text-brand-300' : 'text-gray-400',
        )}
      >
        <Zap className={clsx(size === 'xs' ? 'h-3 w-3' : 'h-3.5 w-3.5', checked && 'fill-brand-400/30')} />
        {t('Direct', 'Напрямую')}
      </span>
    </label>
  )
}
