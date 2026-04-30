import { useEffect, useRef, useState, useCallback } from 'react'
import { createLogSocket } from '@/api/client'

export interface LogLine {
  id: number
  text: string
  level: 'error' | 'warn' | 'info' | 'debug' | 'raw'
  ts: number
}

const MAX_LINES = 2000

function classifyLine(text: string): LogLine['level'] {
  const lower = text.toLowerCase()
  if (lower.includes('error') || lower.includes('failed') || lower.includes('fatal')) return 'error'
  if (lower.includes('warn') || lower.includes('warning')) return 'warn'
  if (lower.includes('info') || lower.includes('accepted') || lower.includes('connected')) return 'info'
  if (lower.includes('debug') || lower.includes('trace')) return 'debug'
  return 'raw'
}

let _lineId = 0

export function useLogs(enabled = true) {
  const [lines, setLines] = useState<LogLine[]>([])
  const [connected, setConnected] = useState(false)
  const [paused, setPaused] = useState(true)
  const pausedRef = useRef(false)
  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const aliveRef = useRef(true)
  const [filter, setFilter] = useState('')

  const clear = useCallback(() => setLines([]), [])

  useEffect(() => {
    pausedRef.current = paused
  }, [paused])

  useEffect(() => {
    if (!enabled) return

    const connect = () => {
      const ws = createLogSocket((text) => {
        // Drop incoming lines while paused — prevents unbounded state growth
        // and DOM reflows that crashed the browser under log spam.
        if (pausedRef.current) return
        setLines((prev) => {
          const next = [
            ...prev,
            { id: ++_lineId, text, level: classifyLine(text), ts: Date.now() },
          ]
          return next.length > MAX_LINES ? next.slice(-MAX_LINES) : next
        })
      })

      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (aliveRef.current) {
          timerRef.current = setTimeout(connect, 3000)
        }
      }
      ws.onerror = () => ws.close()
      wsRef.current = ws
    }

    connect()

    return () => {
      aliveRef.current = false
      if (timerRef.current) clearTimeout(timerRef.current)
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [enabled])

  const filtered = filter
    ? lines.filter((l) => l.text.toLowerCase().includes(filter.toLowerCase()))
    : lines

  return { lines: filtered, allLines: lines, connected, filter, setFilter, clear, paused, setPaused }
}
