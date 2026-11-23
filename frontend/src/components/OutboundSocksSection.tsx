import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { serversApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import { apiErrorText } from '@/lib/apiError'

const inputCls =
  'w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden'

/**
 * Outbound-SOCKS5 mode for an already-installed WireGuard server:
 * peer egress via an upstream proxy (sing-box on the VPS) instead of
 * plain MASQUERADE. Synchronous apply over SSH (~seconds).
 */
export function OutboundSocksSection({ serverId }: { serverId: number }) {
  const t = useT()
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['servers', serverId, 'outbound-socks'],
    queryFn: () => serversApi.getOutboundSocks(serverId),
  })

  const [editing, setEditing] = useState(false)
  const [host, setHost] = useState('')
  const [port, setPort] = useState('1080')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [udp, setUdp] = useState(false)
  const [error, setError] = useState('')

  const save = useMutation({
    mutationFn: (body: Parameters<typeof serversApi.setOutboundSocks>[1]) =>
      serversApi.setOutboundSocks(serverId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['servers', serverId, 'outbound-socks'] })
      setEditing(false)
      setError('')
    },
    onError: (err) => setError(apiErrorText(err, 'apply failed')),
  })

  const startEdit = () => {
    setHost(data?.socks?.host ?? '')
    setPort(data?.socks?.port?.toString() ?? '1080')
    setUsername(data?.socks?.username ?? '')
    setPassword(data?.socks?.password ?? '')
    setUdp(!!data?.socks?.udp)
    setError('')
    setEditing(true)
  }

  const apply = () => {
    const p = Number(port)
    if (!host.trim()) {
      setError(t('Host is required', 'Укажите хост'))
      return
    }
    if (Number.isNaN(p) || p < 1 || p > 65535) {
      setError(t('Port must be 1–65535', 'Порт должен быть 1–65535'))
      return
    }
    save.mutate({
      enabled: true,
      socks: {
        host: host.trim(),
        port: p,
        username: username.trim() || undefined,
        password: password || undefined,
        udp,
      },
    })
  }

  return (
    <div className="mt-4 pt-4 border-t border-gray-800">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          {t('Outbound SOCKS5', 'Исходящий SOCKS5')}
        </h3>
        {data?.enabled && !editing && (
          <span className="text-[11px] text-green-400">
            {t('active', 'активен')}: {data.socks?.host}:{data.socks?.port}
            {data.socks?.udp ? ' · udp' : ''}
          </span>
        )}
      </div>
      <p className="text-[11px] text-gray-500 mb-3">
        {t(
          'Route all WireGuard peer traffic through an upstream SOCKS5 proxy instead of the VPS’s own IP. SSH and other services on the VPS are unaffected; DNS is resolved through the proxy.',
          'Завернуть весь трафик WG-пиров в upstream SOCKS5-прокси вместо IP самого VPS. SSH и другие сервисы сервера не затрагиваются; DNS резолвится через прокси.',
        )}
      </p>

      {error && (
        <div className="mb-2 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 px-3 py-2 text-xs text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {!editing ? (
        <div className="flex gap-2">
          <button
            onClick={startEdit}
            disabled={save.isPending}
            className="rounded-lg bg-gray-800 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            {data?.enabled
              ? t('Change proxy…', 'Изменить прокси…')
              : t('Enable…', 'Включить…')}
          </button>
          {data?.enabled && (
            <button
              onClick={() => save.mutate({ enabled: false })}
              disabled={save.isPending}
              className="rounded-lg bg-gray-800 px-3 py-1.5 text-xs text-red-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
            >
              {save.isPending ? t('Applying…', 'Применяю…') : t('Disable', 'Выключить')}
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          <div className="grid grid-cols-3 gap-2">
            <input
              type="text"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder={t('host, e.g. 127.0.0.1', 'хост, напр. 127.0.0.1')}
              className={`${inputCls} col-span-2`}
            />
            <input
              type="number"
              min={1}
              max={65535}
              value={port}
              onChange={(e) => setPort(e.target.value)}
              className={inputCls}
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder={t('username (optional)', 'логин (необязательно)')}
              className={inputCls}
              autoComplete="off"
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t('password (optional)', 'пароль (необязательно)')}
              className={inputCls}
              autoComplete="new-password"
            />
          </div>
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={udp}
              onChange={(e) => setUdp(e.target.checked)}
              className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
            />
            {t('Proxy supports UDP (UDP ASSOCIATE)', 'Прокси поддерживает UDP (UDP ASSOCIATE)')}
          </label>
          <div className="flex gap-2 pt-1">
            <button
              onClick={apply}
              disabled={save.isPending}
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-500 transition-colors disabled:opacity-50"
            >
              {save.isPending ? t('Applying…', 'Применяю…') : t('Apply on server', 'Применить на сервере')}
            </button>
            <button
              onClick={() => setEditing(false)}
              disabled={save.isPending}
              className="rounded-lg bg-gray-800 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
            >
              {t('Cancel', 'Отмена')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
