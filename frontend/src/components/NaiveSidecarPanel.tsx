import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RotateCw, FileText, X } from 'lucide-react'
import { clsx } from 'clsx'
import { nodesApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import { ModalShell } from '@/components/ModalShell'

interface Props {
  nodeId: number
  nodeName: string
}

export function NaiveSidecarPanel({ nodeId, nodeName }: Props) {
  const t = useT()
  const qc = useQueryClient()
  const [logsOpen, setLogsOpen] = useState(false)

  const { data: status } = useQuery({
    queryKey: ['naive-sidecar', nodeId],
    queryFn: () => nodesApi.sidecarStatus(nodeId),
    refetchInterval: 5000,
  })

  const restart = useMutation({
    mutationFn: () => nodesApi.sidecarRestart(nodeId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['naive-sidecar', nodeId] }),
  })

  const dot =
    !status || !status.exists ? 'bg-gray-600'
    : status.running ? 'bg-green-500'
    : 'bg-red-500'

  const label =
    !status ? '…'
    : !status.exists ? t('not deployed', 'не развёрнут')
    : status.running ? `${t('running', 'работает')} · :${status.internal_port ?? '?'}`
    : `${t('stopped', 'остановлен')} (${status.status})`

  return (
    <>
      <div className="mt-1 ml-4 flex items-center gap-2 text-xs">
        <span className="text-gray-500">sidecar:</span>
        <span className={clsx('h-2 w-2 rounded-full', dot)} />
        <span className="text-gray-400">{label}</span>
        {status && status.restart_count > 0 && (
          <span className="text-gray-600">· restarts: {status.restart_count}</span>
        )}
        <button
          onClick={() => restart.mutate()}
          disabled={restart.isPending}
          className="ml-2 flex items-center gap-1 rounded bg-gray-800 px-2 py-0.5 text-gray-400 hover:bg-gray-700 hover:text-gray-200 disabled:opacity-50"
          title={t('Restart sidecar', 'Перезапустить sidecar')}
        >
          <RotateCw className={clsx('h-3 w-3', restart.isPending && 'animate-spin')} />
          {t('Restart', 'Перезапуск')}
        </button>
        <button
          onClick={() => setLogsOpen(true)}
          className="flex items-center gap-1 rounded bg-gray-800 px-2 py-0.5 text-gray-400 hover:bg-gray-700 hover:text-gray-200"
        >
          <FileText className="h-3 w-3" />
          {t('Logs', 'Логи')}
        </button>
      </div>

      {logsOpen && (
        <LogsModal nodeId={nodeId} nodeName={nodeName} onClose={() => setLogsOpen(false)} />
      )}
    </>
  )
}

function LogsModal({ nodeId, nodeName, onClose }: { nodeId: number; nodeName: string; onClose: () => void }) {
  const t = useT()
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['naive-sidecar-logs', nodeId],
    queryFn: () => nodesApi.sidecarLogs(nodeId, 500),
    refetchInterval: 3000,
  })

  return (
    <ModalShell onClose={onClose} labelledBy="naive-logs-title">
      <div className="w-full max-w-3xl max-h-[85vh] flex flex-col rounded-2xl bg-gray-950 border border-gray-800">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <h2 id="naive-logs-title" className="text-sm font-semibold text-gray-100">
            {t('Sidecar logs', 'Логи sidecar')} · {nodeName}
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="rounded bg-gray-800 px-2 py-1 text-xs text-gray-300 hover:bg-gray-700 disabled:opacity-50"
            >
              {isFetching ? '…' : t('Refresh', 'Обновить')}
            </button>
            <button
              onClick={onClose}
              className="rounded p-1 text-gray-400 hover:text-gray-100 hover:bg-gray-800"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        <pre className="flex-1 overflow-auto p-4 text-xs text-gray-300 font-mono whitespace-pre-wrap">
          {isLoading ? t('Loading…', 'Загрузка…') : (data?.logs || t('(no logs)', '(логов нет)'))}
        </pre>
      </div>
    </ModalShell>
  )
}
