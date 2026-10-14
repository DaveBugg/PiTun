import { Gauge, Square } from 'lucide-react'
import { clsx } from 'clsx'
import { useT } from '@/hooks/useT'

interface Props {
  sweeping: boolean
  starting: boolean
  done: number
  total: number
  cancelling: boolean
  stopping: boolean
  onRun: () => void
  onStop: () => void
}

// "Speed All" while idle; once a sweep is running the same slot becomes "Stop".
export function SpeedAllButton({ sweeping, starting, done, total, cancelling, stopping, onRun, onStop }: Props) {
  const t = useT()
  if (sweeping) {
    return (
      <button
        onClick={onStop}
        disabled={stopping || cancelling}
        title={t('Stop after the node being tested finishes; results so far are kept', 'Остановить после текущей ноды; уже полученные результаты сохранятся')}
        className="flex items-center gap-1.5 rounded-lg bg-red-900/40 px-3 py-2 text-sm text-red-300 hover:bg-red-900/60 transition-colors disabled:opacity-50"
      >
        <Square className="h-3.5 w-3.5" />
        {cancelling
          ? t('Stopping…', 'Останавливаю…')
          : total > 0
            ? t(`Stop ${done}/${total}`, `Стоп ${done}/${total}`)
            : t('Stop', 'Стоп')}
      </button>
    )
  }
  return (
    <button
      onClick={onRun}
      disabled={starting}
      className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
    >
      <Gauge className={clsx('h-4 w-4', starting && 'animate-spin')} />
      {starting ? t('Starting…', 'Запуск…') : t('Speed All', 'Скорость всех')}
    </button>
  )
}
