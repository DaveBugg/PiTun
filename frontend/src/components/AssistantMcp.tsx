import { useState } from 'react'
import { Copy, KeyRound, Trash2 } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { assistantApi } from '@/api/client'
import type { AssistantSettingsPatch } from '@/api/client'
import { InfoTip } from '@/components/InfoTip'
import { useConfirm } from '@/components/ConfirmModal'
import { useT } from '@/hooks/useT'
import { apiErrorText } from '@/lib/apiError'

const INPUT = 'w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden'

function CopyBox({ text }: { text: string }) {
  const t = useT()
  const [done, setDone] = useState(false)
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-md bg-gray-950 border border-gray-800 p-2 pr-8 text-[11px] text-gray-300 whitespace-pre-wrap break-all">{text}</pre>
      <button type="button" aria-label={t('Copy', 'Копировать')}
              onClick={() => { void navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500) }}
              className="absolute right-1.5 top-1.5 text-gray-500 hover:text-gray-200">
        {done ? <span className="text-[10px] text-green-400">ok</span> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  )
}

/** Settings → Assistant → MCP: turn the endpoint on, issue and revoke tokens. */
export function AssistantMcp({ enabled, onToggle }: {
  enabled: boolean
  onToggle: (p: AssistantSettingsPatch) => void
}) {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [name, setName] = useState('')
  const [scope, setScope] = useState<'read' | 'plan'>('plan')
  const [fresh, setFresh] = useState<string | null>(null)
  const url = `${window.location.origin}/api/mcp`
  const tokens = useQuery({ queryKey: ['assistant', 'mcp-tokens'], queryFn: () => assistantApi.mcpTokens(), enabled })
  const refresh = () => void qc.invalidateQueries({ queryKey: ['assistant', 'mcp-tokens'] })
  const create = useMutation({
    mutationFn: () => assistantApi.createMcpToken(name.trim(), scope),
    onSuccess: (r) => { setFresh(r.token); setName(''); refresh() },
  })
  const revoke = useMutation({ mutationFn: (id: number) => assistantApi.revokeMcpToken(id), onSuccess: refresh })

  return (
    <div className="space-y-3 rounded-lg border border-gray-800 p-3">
      <label className="flex items-center gap-2 text-xs text-gray-300">
        <input type="checkbox" checked={enabled} onChange={(e) => onToggle({ mcp_enabled: e.target.checked })} />
        {t('MCP server for external agents', 'MCP-сервер для внешних агентов')}
        <InfoTip className="ml-0.5" text={t(
          'Claude Desktop, Claude Code or any MCP client can look things up and propose changes. Proposed changes wait here as plans (chat "MCP: <token>") until you approve them. Works without the built-in assistant\'s provider.',
          'Claude Desktop, Claude Code или любой MCP-клиент может смотреть состояние и предлагать изменения. Предложенное ждёт здесь планом (чат «MCP: <токен>»), пока вы его не подтвердите. Работает и без провайдера встроенного помощника.',
        )} />
      </label>
      {enabled && (
        <>
          <div className="text-xs text-gray-400">
            {t('Endpoint', 'Адрес')}: <span className="font-mono text-gray-200">{url}</span>
          </div>
          <div className="rounded-md border border-gray-800 divide-y divide-gray-800">
            {(tokens.data ?? []).length === 0 && (
              <div className="px-3 py-2 text-xs text-gray-600">{t('No tokens yet.', 'Токенов пока нет.')}</div>
            )}
            {(tokens.data ?? []).map((tk) => (
              <div key={tk.id} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                <KeyRound className="h-3.5 w-3.5 text-gray-500" />
                <span className="text-gray-200">{tk.name}</span>
                <span className="font-mono text-gray-600">{tk.prefix}…</span>
                <span className="text-gray-500">{tk.scope === 'plan' ? t('can propose plans', 'может предлагать планы') : t('read only', 'только чтение')}</span>
                <span className="ml-auto text-gray-600">
                  {tk.last_used_at ? new Date(tk.last_used_at).toLocaleString() : t('never used', 'не использовался')}
                </span>
                <button type="button" aria-label={t('Revoke', 'Отозвать')} className="text-gray-600 hover:text-red-400"
                        onClick={async () => {
                          if (await confirm({ title: t('Revoke this token?', 'Отозвать этот токен?'), danger: true })) revoke.mutate(tk.id)
                        }}>
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
          <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate() }}>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={60}
                   placeholder={t('token name, e.g. laptop', 'имя токена, например ноутбук')} className={`${INPUT} flex-1 min-w-40`} />
            <select value={scope} onChange={(e) => setScope(e.target.value as 'read' | 'plan')} className={`${INPUT} w-auto`}>
              <option value="plan">{t('can propose plans', 'может предлагать планы')}</option>
              <option value="read">{t('read only', 'только чтение')}</option>
            </select>
            <button type="submit" disabled={!name.trim() || create.isPending}
                    className="rounded-lg bg-gray-800 px-3 text-xs text-gray-200 hover:bg-gray-700 disabled:opacity-50">
              {t('Create token', 'Создать токен')}
            </button>
          </form>
          {create.error && <div className="text-xs text-red-400">{apiErrorText(create.error, 'error')}</div>}
          {fresh && (
            <div className="space-y-2 rounded-md border border-brand-800 bg-brand-900/20 p-2 text-xs">
              <div className="text-gray-200">
                {t('The token is shown only now. Copy it, or one of the commands below:',
                  'Токен показывается только сейчас. Скопируйте его или одну из команд ниже:')}
              </div>
              <CopyBox text={fresh} />
              <div className="text-gray-400">Claude Code:</div>
              <CopyBox text={`claude mcp add --transport http pitun ${url} --header "Authorization: Bearer ${fresh}"`} />
              <div className="text-gray-400">{t('JSON config (Claude Desktop and others):', 'JSON-конфиг (Claude Desktop и другие):')}</div>
              <CopyBox text={JSON.stringify({ mcpServers: { pitun: { type: 'http', url, headers: { Authorization: `Bearer ${fresh}` } } } }, null, 2)} />
              {window.location.protocol !== 'https:' && (
                <div className="text-yellow-400">
                  {t('Over plain HTTP the token travels unencrypted in the LAN; prefer the https:// address.',
                    'По обычному HTTP токен идёт по сети в открытом виде; лучше адрес https://.')}
                </div>
              )}
              <button type="button" onClick={() => setFresh(null)} className="text-gray-500 hover:text-gray-300">
                {t('Done, hide it', 'Готово, скрыть')}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
