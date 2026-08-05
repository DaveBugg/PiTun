import { useEffect, useRef, useState } from 'react'
import { Wifi, WifiOff, Trash2, Pause, Play, Search } from 'lucide-react'
import { clsx } from 'clsx'
import { useLogs } from '@/hooks/useLogs'
import { useT } from '@/hooks/useT'

const LEVEL_COLORS = {
  error: 'text-red-600 dark:text-red-400',
  warn:  'text-yellow-600 dark:text-yellow-400',
  info:  'text-green-600 dark:text-green-400',
  debug: 'text-gray-500',
  raw:   'text-gray-300',
}

export function Logs() {
  const t = useT()
  const { lines, connected, filter, setFilter, clear, paused, setPaused } = useLogs(true)
  const bottomRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  useEffect(() => {
    if (autoScroll && !paused) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [lines, autoScroll, paused])

  return (
    <div className="flex flex-col h-full p-6 gap-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3 shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold text-gray-100">{t('Logs', 'Логи')}</h1>
          <div className="flex items-center gap-1.5 text-xs">
            {connected
              ? <><Wifi className="h-3.5 w-3.5 text-green-600 dark:text-green-400" /><span className="text-green-600 dark:text-green-400">{t('Connected', 'Подключено')}</span></>
              : <><WifiOff className="h-3.5 w-3.5 text-red-600 dark:text-red-400" /><span className="text-red-600 dark:text-red-400">{t('Disconnected', 'Отключено')}</span></>
            }
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Filter */}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-500" />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={t('Filter logs…', 'Фильтр логов…')}
              className="rounded-lg bg-gray-900 border border-gray-800 pl-8 pr-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden w-48"
            />
          </div>

          <button
            onClick={() => setPaused((v) => !v)}
            title={paused ? t('Resume', 'Продолжить') : t('Pause', 'Пауза')}
            className="rounded-lg bg-gray-800 p-2 text-gray-400 hover:text-gray-200 hover:bg-gray-700 transition-colors"
          >
            {paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
          </button>

          <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="rounded-sm border-gray-600 bg-gray-800"
            />
            {t('Auto-scroll', 'Автопрокрутка')}
          </label>

          <button
            onClick={clear}
            title={t('Clear', 'Очистить')}
            className="rounded-lg bg-gray-800 p-2 text-gray-400 hover:text-red-400 hover:bg-gray-700 transition-colors"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Log view */}
      <div className="flex-1 min-h-0 rounded-xl border border-gray-800 bg-gray-950 overflow-y-auto font-mono text-xs">
        {lines.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-gray-600">
            {paused
              ? <><Play className="h-6 w-6 text-gray-700" /><span>{t('Paused — press Play to start streaming', 'Пауза — нажмите Play для стрима')}</span></>
              : <span>{connected ? t('Waiting for log output…', 'Ожидание вывода логов…') : t('Connecting to log stream…', 'Подключение к потоку логов…')}</span>
            }
          </div>
        ) : (
          <div className="p-4 space-y-0.5">
            {lines.map((line) => (
              <div
                key={line.id}
                className={clsx(
                  'leading-5 whitespace-pre-wrap break-all',
                  paused ? 'opacity-70' : '',
                  LEVEL_COLORS[line.level],
                )}
              >
                {line.text}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="shrink-0 flex items-center justify-between text-xs text-gray-600">
        <span>{lines.length} {t('lines', 'строк')}{filter ? t(' (filtered)', ' (фильтр)') : ''}</span>
        {paused && lines.length > 0 && <span className="text-yellow-500">{t('⏸ Paused — new lines dropped', '⏸ Пауза — новые строки отбрасываются')}</span>}
      </div>
    </div>
  )
}
