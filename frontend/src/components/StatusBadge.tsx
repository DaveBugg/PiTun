import { clsx } from 'clsx'

interface Props {
  online?: boolean
  latency?: number
  className?: string
}

export function StatusBadge({ online, latency, className }: Props) {
  if (online === undefined) {
    return (
      <span className={clsx('inline-flex items-center gap-1 text-xs text-gray-500', className)}>
        <span className="h-2 w-2 rounded-full bg-gray-600" />
        Unknown
      </span>
    )
  }

  if (!online) {
    return (
      <span className={clsx('inline-flex items-center gap-1 text-xs text-red-600 dark:text-red-400', className)}>
        <span className="h-2 w-2 rounded-full bg-red-500" />
        Offline
      </span>
    )
  }

  const color =
    latency == null ? 'text-gray-400'
    : latency < 100 ? 'text-green-600 dark:text-green-400'
    : latency < 300 ? 'text-yellow-600 dark:text-yellow-400'
    : 'text-red-600 dark:text-red-400'

  const dot =
    latency == null ? 'bg-gray-500'
    : latency < 100 ? 'bg-green-500'
    : latency < 300 ? 'bg-yellow-500'
    : 'bg-red-500'

  return (
    <span className={clsx('inline-flex items-center gap-1 text-xs', color, className)}>
      <span className={clsx('h-2 w-2 rounded-full', dot)} />
      {latency != null ? `${latency} ms` : 'Online'}
    </span>
  )
}

interface ProtocolBadgeProps {
  protocol: string
  className?: string
}

const PROTO_COLORS: Record<string, string> = {
  vless:     'bg-violet-100 text-violet-700 dark:bg-violet-900/60 dark:text-violet-300',
  vmess:     'bg-blue-100 text-blue-700 dark:bg-blue-900/60 dark:text-blue-300',
  trojan:    'bg-orange-100 text-orange-700 dark:bg-orange-900/60 dark:text-orange-300',
  ss:        'bg-teal-100 text-teal-700 dark:bg-teal-900/60 dark:text-teal-300',
  wireguard: 'bg-green-100 text-green-700 dark:bg-green-900/60 dark:text-green-300',
  socks:     'bg-gray-700 text-gray-300',
  hy2:       'bg-pink-100 text-pink-700 dark:bg-pink-900/60 dark:text-pink-300',
}

export function ProtocolBadge({ protocol, className }: ProtocolBadgeProps) {
  const colors = PROTO_COLORS[protocol] ?? 'bg-gray-700 text-gray-300'
  return (
    <span
      className={clsx(
        'inline-block rounded-sm px-1.5 py-0.5 text-xs font-mono font-medium uppercase',
        colors,
        className,
      )}
    >
      {protocol}
    </span>
  )
}

interface ModeBadgeProps {
  mode: string
  className?: string
}

const MODE_COLORS: Record<string, string> = {
  global:  'bg-red-50 dark:bg-red-900/60 text-red-700 dark:text-red-300',
  rules:   'bg-blue-100 text-blue-700 dark:bg-blue-900/60 dark:text-blue-300',
  bypass:  'bg-gray-700 text-gray-300',
}

export function ModeBadge({ mode, className }: ModeBadgeProps) {
  const colors = MODE_COLORS[mode] ?? 'bg-gray-700 text-gray-300'
  return (
    <span
      // `block` instead of `inline-block` so flex parents align it on
      // their cross-axis (centered by default) rather than text-baseline,
      // which would otherwise pull the chip up by ~1-2 px next to taller
      // adjacent text (the "Running" label in the xray dashboard tile).
      className={clsx(
        'block rounded-sm px-2 py-0.5 text-xs font-medium capitalize',
        colors,
        className,
      )}
    >
      {mode}
    </span>
  )
}
