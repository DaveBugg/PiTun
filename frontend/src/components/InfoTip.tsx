import { clsx } from 'clsx'

interface InfoTipProps {
  text: string
  className?: string
  /** 'top' (default) — tooltip appears above the icon. 'bottom' — appears below (use when the icon is near the top of the page). */
  position?: 'top' | 'bottom'
}

export function InfoTip({ text, className, position = 'top' }: InfoTipProps) {
  const isBottom = position === 'bottom'
  return (
    <span className={clsx('relative inline-flex group', className)}>
      <span className="cursor-help text-gray-600 hover:text-gray-400 text-xs select-none">ⓘ</span>
      <span
        className={clsx(
          'pointer-events-none absolute left-1/2 -translate-x-1/2 z-50',
          'w-72 rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-xs text-gray-300 leading-relaxed',
          'opacity-0 group-hover:opacity-100 transition-opacity duration-150 shadow-xl whitespace-pre-wrap',
          isBottom ? 'top-full mt-1.5' : 'bottom-full mb-1.5',
        )}
      >
        {text}
        <span
          className={clsx(
            'absolute left-1/2 -translate-x-1/2 border-4 border-transparent',
            isBottom ? 'bottom-full border-b-gray-700' : 'top-full border-t-gray-700',
          )}
        />
      </span>
    </span>
  )
}
