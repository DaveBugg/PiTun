import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw, Trash2 } from 'lucide-react'
import { clsx } from 'clsx'
import { balancersApi, endpointsApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import { useNodes } from '@/hooks/useNodes'
import { useSystemStatus } from '@/hooks/useSystem'
import { useRoutingSets } from '@/hooks/useRoutingSets'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import { SearchableSelect } from '@/components/SearchableSelect'
import { apiErrorText } from '@/lib/apiError'
import { loadHideUnavailable, saveHideUnavailable } from '@/lib/nodeFilters'
import type { ProxyEndpoint, ProxyEndpointMode } from '@/types'

const MAX_ENDPOINTS = 9
const inputCls =
  'w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden'

/**
 * Extra SOCKS5 proxy endpoints on the Dashboard — each is its own xray
 * process bound to an assigned node (or balancer) with optional
 * RoutingSet rules. The main flow stays untouched.
 */
export function ProxyEndpointsSection() {
  const t = useT()
  const confirm = useConfirm()
  const qc = useQueryClient()
  const [editing, setEditing] = useState<ProxyEndpoint | 'new' | null>(null)

  const { data: endpoints = [] } = useQuery({
    queryKey: ['endpoints'],
    queryFn: () => endpointsApi.list(),
    refetchInterval: 5000,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['endpoints'] })
  const toggle = useMutation({
    mutationFn: (ep: ProxyEndpoint) => endpointsApi.update(ep.id, { enabled: !ep.enabled }),
    onSuccess: invalidate,
  })
  const restart = useMutation({
    mutationFn: (id: number) => endpointsApi.restart(id),
    onSuccess: invalidate,
  })
  const remove = useMutation({
    mutationFn: (id: number) => endpointsApi.delete(id),
    onSuccess: invalidate,
  })

  return (
    <div className="mt-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500">
          {t('Extra SOCKS5 endpoints (own node per flow)', 'Доп. SOCKS5-эндпоинты (своя нода на поток)')}
          {' · '}{endpoints.length}/{MAX_ENDPOINTS}
        </span>
        <button
          onClick={() => setEditing('new')}
          disabled={endpoints.length >= MAX_ENDPOINTS}
          className="flex items-center gap-1 rounded-lg bg-gray-800 px-2.5 py-1.5 text-xs text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
          title={endpoints.length >= MAX_ENDPOINTS
            ? t(`Limit reached — ${MAX_ENDPOINTS} extra endpoints max (each is its own xray process)`, `Лимит — максимум ${MAX_ENDPOINTS} доп. эндпоинтов (каждый — свой процесс xray)`)
            : t('Add an endpoint with its own node/rules', 'Добавить эндпоинт со своей нодой/правилами')}
        >
          <Plus className="h-3.5 w-3.5" />
          {t('Add endpoint', 'Добавить эндпоинт')}
        </button>
      </div>

      {endpoints.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {endpoints.map((ep) => (
            <div key={ep.id} className="rounded-xl border border-gray-800 bg-gray-900/30 p-3">
              <div className="flex items-center justify-between mb-1">
                <div className="text-xs font-semibold text-cyan-600 dark:text-cyan-400 truncate">
                  {ep.name}
                </div>
                <span
                  className={clsx(
                    'h-2 w-2 rounded-full shrink-0',
                    !ep.enabled ? 'bg-gray-600'
                      : ep.running ? 'bg-green-500'
                      : 'bg-red-500',
                  )}
                  title={!ep.enabled
                    ? t('disabled', 'выключен')
                    : ep.running
                      ? t('running', 'работает')
                      : (ep.last_error ?? t('stopped', 'остановлен'))}
                />
              </div>
              <div className="font-mono text-gray-100 text-sm">:{ep.socks_port}</div>
              <div className="text-xs text-gray-600 mt-0.5 truncate">
                {ep.mode === 'routing_set'
                  ? t(`rules: ${ep.routing_set_name ?? '?'} → `, `правила: ${ep.routing_set_name ?? '?'} → `)
                  : t('global → ', 'глобально → ')}
                <span className="text-gray-400">
                  {ep.node_name ?? ep.balancer_name ?? '—'}
                </span>
              </div>
              {ep.last_error && ep.enabled && !ep.running && (
                <div className="text-[11px] text-red-400 mt-1 truncate" title={ep.last_error}>
                  {ep.last_error}
                </div>
              )}
              <div className="flex items-center gap-1.5 mt-2">
                <button
                  onClick={() => toggle.mutate(ep)}
                  disabled={toggle.isPending}
                  className="rounded bg-gray-800 px-2 py-1 text-[11px] text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
                >
                  {ep.enabled ? t('Disable', 'Выключить') : t('Enable', 'Включить')}
                </button>
                <button
                  onClick={() => setEditing(ep)}
                  className="rounded bg-gray-800 px-2 py-1 text-[11px] text-gray-300 hover:bg-gray-700 transition-colors"
                >
                  {t('Edit', 'Изменить')}
                </button>
                <button
                  onClick={() => restart.mutate(ep.id)}
                  disabled={restart.isPending || !ep.enabled}
                  className="rounded bg-gray-800 p-1.5 text-gray-400 hover:bg-gray-700 transition-colors disabled:opacity-50"
                  title={t('Restart', 'Перезапустить')}
                >
                  <RefreshCw className={clsx('h-3 w-3', restart.isPending && 'animate-spin')} />
                </button>
                <button
                  onClick={async () => {
                    const ok = await confirm({
                      title: t(`Delete endpoint "${ep.name}"?`, `Удалить эндпоинт «${ep.name}»?`),
                      body: t('Its xray process stops; the assigned node is freed for other flows.', 'Процесс xray остановится; назначенная нода освободится для других потоков.'),
                      confirmLabel: t('Delete', 'Удалить'),
                      danger: true,
                    })
                    if (ok) remove.mutate(ep.id)
                  }}
                  className="rounded bg-gray-800 p-1.5 text-red-400 hover:bg-gray-700 transition-colors"
                  title={t('Delete', 'Удалить')}
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing !== null && (
        <EndpointFormModal
          initial={editing === 'new' ? undefined : editing}
          endpoints={endpoints}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}


function EndpointFormModal({
  initial,
  endpoints,
  onClose,
}: {
  initial?: ProxyEndpoint
  endpoints: ProxyEndpoint[]
  onClose: () => void
}) {
  const t = useT()
  const qc = useQueryClient()
  const { data: nodes = [] } = useNodes()
  const { data: status } = useSystemStatus()
  const { data: routingSets = [] } = useRoutingSets()
  const { data: balancers = [] } = useQuery({
    queryKey: ['balancers'],
    queryFn: () => balancersApi.list(),
  })

  const [name, setName] = useState(initial?.name ?? '')
  const [port, setPort] = useState(initial?.socks_port?.toString() ?? '')
  const [mode, setMode] = useState<ProxyEndpointMode>(initial?.mode ?? 'global')
  const [egress, setEgress] = useState(
    initial?.node_id != null ? `node:${initial.node_id}`
      : initial?.balancer_id != null ? `balancer:${initial.balancer_id}`
      : '',
  )
  const [setId, setSetId] = useState(initial?.routing_set_id?.toString() ?? '')
  const [hideUnavailable, setHideUnavailable] = useState(() => loadHideUnavailable(true))
  const [error, setError] = useState('')

  // Nodes already driving a flow: the main active node + every other
  // endpoint's node. Balancers are exempt by design.
  const usedNodeIds = useMemo(() => {
    const used = new Set<number>()
    if (status?.active_node_id != null) used.add(status.active_node_id)
    for (const ep of endpoints) {
      if (ep.node_id != null && ep.id !== initial?.id) used.add(ep.node_id)
    }
    return used
  }, [status, endpoints, initial])

  const nodeOptions = useMemo(() => nodes
    .filter((n) => n.enabled && !usedNodeIds.has(n.id))
    .filter((n) => !hideUnavailable || n.is_online || `node:${n.id}` === egress)
    .map((n) => ({
      value: `node:${n.id}`,
      label: `${n.name}${n.is_online ? '' : ` (${t('offline', 'недоступна')})`}`,
      hint: `${n.protocol} · ${n.address}`,
    })), [nodes, usedNodeIds, hideUnavailable, egress, t])

  const balancerOptions = balancers
    .filter((b) => b.enabled)
    .map((b) => ({
      value: `balancer:${b.id}`,
      label: b.name,
      hint: `${b.node_ids.length} ${t('nodes', 'нод')} · ${b.strategy}`,
    }))

  const save = useMutation({
    mutationFn: async () => {
      const nodeId = egress.startsWith('node:') ? Number(egress.slice(5)) : null
      const balancerId = egress.startsWith('balancer:') ? Number(egress.slice(9)) : null
      const payload = {
        name: name.trim(),
        socks_port: port.trim() ? Number(port.trim()) : 0,
        mode,
        node_id: nodeId,
        balancer_id: balancerId,
        routing_set_id: mode === 'routing_set' && setId ? Number(setId) : null,
      }
      if (initial) return endpointsApi.update(initial.id, payload)
      return endpointsApi.create(payload)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['endpoints'] })
      onClose()
    },
    onError: (e) => setError(apiErrorText(e, 'save failed')),
  })

  const submit = () => {
    setError('')
    if (!name.trim()) {
      setError(t('Name is required', 'Укажите имя'))
      return
    }
    if (!egress) {
      setError(t('Pick a node or a balancer', 'Выберите ноду или балансер'))
      return
    }
    if (mode === 'routing_set' && !setId) {
      setError(t('Pick a routing set', 'Выберите набор правил'))
      return
    }
    save.mutate()
  }

  return (
    <ModalShell onClose={onClose} labelledBy="endpoint-form-title">
      <div className="w-[min(92vw,28rem)] rounded-2xl bg-gray-950 border border-gray-800 p-6 space-y-4">
        <h2 id="endpoint-form-title" className="text-base font-semibold text-gray-100">
          {initial ? t('Edit endpoint', 'Изменить эндпоинт') : t('Add endpoint', 'Новый эндпоинт')}
        </h2>

        {error && (
          <div className="rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 px-3 py-2 text-xs text-red-700 dark:text-red-300">
            {error}
          </div>
        )}

        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <label className="block text-xs font-medium text-gray-400 mb-1">{t('Name', 'Имя')}</label>
            <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} placeholder="work-laptop" autoFocus />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              {t('SOCKS port', 'SOCKS-порт')}
            </label>
            <input
              value={port}
              onChange={(e) => setPort(e.target.value)}
              className={inputCls}
              placeholder={t('auto (1081+)', 'авто (1081+)')}
              inputMode="numeric"
            />
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{t('Egress (unique per flow)', 'Выход (уникален на поток)')}</label>
          <SearchableSelect
            value={egress}
            onChange={setEgress}
            placeholder={t('Select node or balancer…', 'Выберите ноду или балансер…')}
            options={[
              ...(balancerOptions.length ? balancerOptions : []),
              ...nodeOptions,
            ]}
            footer={(
              <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={hideUnavailable}
                  onChange={(e) => {
                    setHideUnavailable(e.target.checked)
                    saveHideUnavailable(e.target.checked)
                  }}
                  className="h-3.5 w-3.5 accent-brand-500"
                />
                {t('Hide unreachable nodes', 'Скрывать недоступные ноды')}
              </label>
            )}
          />
          <p className="text-[11px] text-gray-600 mt-1">
            {t(
              'A node already assigned to the main flow or another endpoint is not listed. Balancers may repeat.',
              'Нода, уже назначенная на основной поток или другой эндпоинт, не показывается. Балансеры могут повторяться.',
            )}
          </p>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{t('Mode', 'Режим')}</label>
          <select value={mode} onChange={(e) => setMode(e.target.value as ProxyEndpointMode)} className={inputCls}>
            <option value="global">{t('Global — everything goes to the assigned egress', 'Глобально — всё в назначенный выход')}</option>
            <option value="routing_set">{t('Routing set — set rules first, egress is the catch-all', 'Набор правил — сначала правила, выход — по умолчанию')}</option>
          </select>
        </div>

        {mode === 'routing_set' && (
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">{t('Routing set', 'Набор правил')}</label>
            <select value={setId} onChange={(e) => setSetId(e.target.value)} className={inputCls}>
              <option value="">{t('Select…', 'Выберите…')}</option>
              {routingSets.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2 border-t border-gray-800">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors"
          >
            {t('Cancel', 'Отмена')}
          </button>
          <button
            onClick={submit}
            disabled={save.isPending}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors disabled:opacity-50"
          >
            {save.isPending ? t('Saving…', 'Сохраняю…') : t('Save', 'Сохранить')}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}
