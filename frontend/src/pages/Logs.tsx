import { useEffect, useRef, useState } from 'react'
import { Wifi, WifiOff, Trash2, Pause, Play, Search } from 'lucide-react'
import { clsx } from 'clsx'
import { useLogs } from '@/hooks/useLogs'

const LEVEL_COLORS = {
  error: 'text-red-400',
  warn:  'text-yellow-400',
  info:  'text-green-400',
  debug: 'text-gray-500',
  raw:   'text-gray-300',
}

export function Logs() {
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
      <div className="flex items-center justify-between flex-wrap gap-3 flex-shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold text-gray-100">Logs</h1>
          <div className="flex items-center gap-1.5 text-xs">
            {connected
              ? <><Wifi className="h-3.5 w-3.5 text-green-400" /><span className="text-green-400">Connected</span></>
              : <><WifiOff className="h-3.5 w-3.5 text-red-400" /><span className="text-red-400">Disconnected</span></>
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
              placeholder="Filter logs…"
              className="rounded-lg bg-gray-900 border border-gray-800 pl-8 pr-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none w-48"
            />
          </div>

          <button
            onClick={() => setPaused((v) => !v)}
            title={paused ? 'Resume' : 'Pause'}
            className="rounded-lg bg-gray-800 p-2 text-gray-400 hover:text-gray-200 hover:bg-gray-700 transition-colors"
          >
            {paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
          </button>

          <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="rounded border-gray-600 bg-gray-800"
            />
            Auto-scroll
          </label>

          <button
            onClick={clear}
            title="Clear"
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
              ? <><Play className="h-6 w-6 text-gray-700" /><span>Paused — press Play to start streaming</span></>
              : <span>{connected ? 'Waiting for log output…' : 'Connecting to log stream…'}</span>
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
      <div className="flex-shrink-0 flex items-center justify-between text-xs text-gray-600">
        <span>{lines.length} lines{filter ? ` (filtered)` : ''}</span>
        {paused && lines.length > 0 && <span className="text-yellow-500">⏸ Paused — new lines dropped</span>}
      </div>
    </div>
  )
}
