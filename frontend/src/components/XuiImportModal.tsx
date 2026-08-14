import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link2, Loader2 } from 'lucide-react'
import { clsx } from 'clsx'

import { serversApi, xuiApi } from '@/api/client'
import { ModalShell } from '@/components/ModalShell'
import { useT } from '@/hooks/useT'
import { apiErrorText } from '@/lib/apiError'

/**
 * Register an x-ui panel that already exists.
 *
 * Deploying through PiTun registers the panel by itself, so this covers
 * everything else: a panel installed by hand, one inherited with a server,
 * or one whose `xui://` line was lost. Without it the X-ui page stayed empty
 * for a box that plainly had a working panel on it, and the only way out was
 * to reinstall over a running install.
 *
 * Two ways in, because operators arrive with different things in hand: the
 * `xui://` line if they kept it, or the details they log into the panel with.
 * The API token is deliberately NOT asked for — the backend logs in and
 * fetches it, since almost nobody has that value lying around.
 */
export function XuiImportModal({ onClose }: { onClose: () => void }) {
  const t = useT()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'uri' | 'fields'>('uri')
  const [uri, setUri] = useState('')
  const [serverId, setServerId] = useState<number | ''>('')
  const [port, setPort] = useState('')
  const [basepath, setBasepath] = useState('/')
  const [user, setUser] = useState('')
  const [pass, setPass] = useState('')
  const [domain, setDomain] = useState('')
  const [error, setError] = useState('')

  const { data: servers = [] } = useQuery({
    queryKey: ['servers'],
    queryFn: () => serversApi.list(),
  })

  const importMut = useMutation({
    mutationFn: () =>
      xuiApi.importServer(
        tab === 'uri'
          ? { server_id: Number(serverId), uri: uri.trim() }
          : {
              server_id: Number(serverId),
              panel_port: Number(port),
              panel_basepath: basepath.trim() || '/',
              panel_user: user,
              panel_pass: pass,
              ...(domain.trim()
                ? { domain: domain.trim(), mode: 'xui-pro' as const }
                : { mode: 'bare' as const }),
            },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['xui', 'servers'] })
      onClose()
    },
    onError: (err) => setError(apiErrorText(err, 'Import failed')),
  })

  const ready = serverId !== '' && (
    tab === 'uri'
      ? uri.trim().startsWith('xui://')
      : Boolean(port && user && pass)
  )

  const field = (
    label: string, value: string, set: (v: string) => void,
    opts: { type?: string; placeholder?: string; hint?: string } = {},
  ) => (
    <div>
      <label className="block text-[11px] font-medium text-gray-400 mb-1">{label}</label>
      <input
        type={opts.type || 'text'}
        value={value}
        onChange={(e) => set(e.target.value)}
        placeholder={opts.placeholder}
        className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
      />
      {opts.hint && <p className="text-[10px] text-gray-600 mt-1">{opts.hint}</p>}
    </div>
  )

  return (
    <ModalShell onClose={onClose} labelledBy="xui-import-title">
      <div className="w-[min(94vw,34rem)] max-h-[90vh] overflow-y-auto rounded-2xl bg-gray-950 border border-gray-800 shadow-2xl p-4 space-y-3">
        <h2
          id="xui-import-title"
          className="text-sm font-semibold text-gray-100 flex items-center gap-2"
        >
          <Link2 className="h-4 w-4 text-brand-400" />
          {t('Connect an existing panel', 'Подключить установленную панель')}
        </h2>
        <p className="text-xs text-gray-500 leading-relaxed">
          {t(
            'For a panel PiTun did not install: one set up by hand, inherited with the server, or whose install line was lost. Nothing is reinstalled — the panel is only registered.',
            'Для панели, которую PiTun не устанавливал: поднятой вручную, доставшейся вместе с сервером или потерявшей строку установки. Ничего не переустанавливается — панель только регистрируется.',
          )}
        </p>

        <div>
          <label className="block text-[11px] font-medium text-gray-400 mb-1">
            {t('Server', 'Сервер')}
          </label>
          <select
            value={serverId}
            onChange={(e) => setServerId(e.target.value ? Number(e.target.value) : '')}
            className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
          >
            <option value="">{t('— choose —', '— выберите —')}</option>
            {servers.map((s) => (
              <option key={s.id} value={s.id}>{s.name || s.host} ({s.host})</option>
            ))}
          </select>
          <p className="text-[10px] text-gray-600 mt-1">
            {t(
              'The host the panel runs on. Add it under Servers first if it is not listed.',
              'Хост, на котором работает панель. Если его нет в списке — сначала добавьте на странице Servers.',
            )}
          </p>
        </div>

        <div className="flex gap-1 rounded-lg bg-gray-950 border border-gray-800 p-1">
          {(['uri', 'fields'] as const).map((k) => (
            <button
              key={k}
              onClick={() => { setTab(k); setError('') }}
              className={clsx(
                'flex-1 rounded-md px-3 py-1.5 text-xs transition-colors',
                tab === k ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-gray-200',
              )}
            >
              {k === 'uri'
                ? t('I have the xui:// line', 'Есть строка xui://')
                : t('I know the panel login', 'Знаю логин от панели')}
            </button>
          ))}
        </div>

        {tab === 'uri' ? (
          <div>
            <label className="block text-[11px] font-medium text-gray-400 mb-1">
              {t('Install line', 'Строка установки')}
            </label>
            <textarea
              value={uri}
              onChange={(e) => setUri(e.target.value)}
              rows={3}
              placeholder="xui://token@host:port/basepath?..."
              className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-xs font-mono text-gray-100 focus:outline-hidden focus:border-gray-600"
            />
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              {field(t('Panel port', 'Порт панели'), port, setPort,
                { type: 'number', placeholder: '2053' })}
              {field(t('Base path', 'Базовый путь'), basepath, setBasepath,
                { placeholder: '/', hint: t('The random path from the panel URL',
                                            'Случайный путь из адреса панели') })}
            </div>
            <div className="grid grid-cols-2 gap-3">
              {field(t('Panel username', 'Логин панели'), user, setUser)}
              {field(t('Panel password', 'Пароль панели'), pass, setPass, { type: 'password' })}
            </div>
            {field(t('Domain (only for x-ui-pro over HTTPS)',
                     'Домен (только для x-ui-pro по HTTPS)'), domain, setDomain,
                   { placeholder: t('leave empty for a plain panel',
                                    'оставьте пустым для обычной панели') })}
            <p className="text-[10px] text-gray-600">
              {t(
                "The API token isn't asked for — PiTun logs in with these and takes it from the panel.",
                'API-токен спрашивать не нужно — PiTun войдёт с этими данными и возьмёт его сам.',
              )}
            </p>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={onClose}
            className="rounded-lg border border-gray-800 px-3 py-2 text-sm text-gray-400 hover:text-gray-200"
          >
            {t('Cancel', 'Отмена')}
          </button>
          <button
            onClick={() => { setError(''); importMut.mutate() }}
            disabled={!ready || importMut.isPending}
            className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50"
          >
            {importMut.isPending
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Link2 className="h-4 w-4" />}
            {t('Connect', 'Подключить')}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}
