import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Loader2, Radar, Sparkles, Trash2, XCircle } from 'lucide-react'
import { clsx } from 'clsx'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { assistantApi } from '@/api/client'
import type { AssistantProvider, AssistantSettings, AssistantSettingsPatch } from '@/api/client'
import { InfoTip } from '@/components/InfoTip'
import { AssistantMcp } from '@/components/AssistantMcp'
import { useT } from '@/hooks/useT'
import { apiErrorText } from '@/lib/apiError'

const INPUT =
  'w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 ' +
  'focus:border-brand-500 focus:outline-hidden'

const PROVIDER_LABEL: Record<AssistantProvider, string> = {
  openrouter: 'OpenRouter',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  local: 'Local model (LAN)',
}

function Facts() {
  const t = useT()
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const facts = useQuery({ queryKey: ['assistant', 'facts'], queryFn: () => assistantApi.facts() })
  const refresh = () => void qc.invalidateQueries({ queryKey: ['assistant', 'facts'] })
  const add = useMutation({
    mutationFn: (v: string) => assistantApi.addFact(v),
    onSuccess: () => { setText(''); refresh() },
  })
  const del = useMutation({ mutationFn: (id: number) => assistantApi.deleteFact(id), onSuccess: refresh })
  const ok = useMutation({ mutationFn: (id: number) => assistantApi.confirmFact(id), onSuccess: refresh })
  return (
    <div>
      <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
        {t('What the assistant remembers', 'Что помощник запомнил')}
        <InfoTip className="ml-0.5" text={t(
          'Short facts about this install, added in chat ("remember…") or here. They go with every request; delete what is wrong or out of date.',
          'Короткие факты об этой установке — из чата («запомни…») или отсюда. Они идут с каждым запросом; удаляйте неверные и устаревшие.',
        )} />
      </label>
      <div className="rounded-lg border border-gray-800 divide-y divide-gray-800">
        {(facts.data ?? []).length === 0 && (
          <div className="px-3 py-2 text-xs text-gray-600">{t('Nothing yet.', 'Пока ничего.')}</div>
        )}
        {(facts.data ?? []).map((f) => (
          <div key={f.id} className="flex items-start gap-2 px-3 py-1.5 text-xs text-gray-300">
            <span className="text-gray-600 font-mono">#{f.id}</span>
            <span className={clsx('flex-1', !f.confirmed && 'text-yellow-300')}>
              {f.text}
              {!f.confirmed && (
                <span className="ml-1.5 text-[10px] text-yellow-600">
                  {t('(proposed by the assistant, not used until you confirm)', '(предложил помощник, не используется, пока не подтвердите)')}
                </span>
              )}
            </span>
            {!f.confirmed && (
              <button onClick={() => ok.mutate(f.id)} className="text-yellow-400 hover:text-yellow-200">
                {t('Confirm', 'Подтвердить')}
              </button>
            )}
            <button onClick={() => del.mutate(f.id)} aria-label={t('Delete', 'Удалить')}
                    className="text-gray-600 hover:text-red-400"><Trash2 className="h-3.5 w-3.5" /></button>
          </div>
        ))}
      </div>
      <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) add.mutate(text) }}>
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={300}
               placeholder={t('e.g. The TV is the Samsung in the living room', 'например: телевизор — это Samsung в гостиной')}
               className={INPUT} />
        <button type="submit" disabled={!text.trim() || add.isPending}
                className="rounded-lg bg-gray-800 px-3 text-xs text-gray-200 hover:bg-gray-700 disabled:opacity-50">{t('Add', 'Добавить')}</button>
      </form>
      {add.error && <div className="mt-1 text-xs text-red-400">{apiErrorText(add.error, 'error')}</div>}
    </div>
  )
}

/** Settings → Assistant: provider, key, model, egress, limit, connection test, facts. */
export function AssistantSection() {
  const t = useT()
  const qc = useQueryClient()
  const { data: s, isLoading } = useQuery({
    queryKey: ['assistant', 'settings'],
    queryFn: () => assistantApi.settings(),
  })
  const [key, setKey] = useState('')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [limit, setLimit] = useState('0')
  const [filter, setFilter] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!s) return
    setModel(s.model)
    setBaseUrl(s.base_url)
    setLimit(String(s.monthly_token_limit))
  }, [s?.provider, s?.model, s?.base_url, s?.monthly_token_limit])  // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: (p: AssistantSettingsPatch) => assistantApi.patchSettings(p),
    onSuccess: (data) => {
      qc.setQueryData(['assistant', 'settings'], data)
      setKey('')
      setError(null)
      void qc.invalidateQueries({ queryKey: ['assistant', 'models'] })
    },
    onError: (err) => setError(apiErrorText(err, t('Could not save', 'Не удалось сохранить'))),
  })
  const test = useMutation({ mutationFn: () => assistantApi.test() })
  const discover = useMutation({ mutationFn: () => assistantApi.discover() })
  const canList = !!s && (s.provider === 'local' ? !!s.base_url : s.has_key[s.provider])
  const models = useQuery({
    queryKey: ['assistant', 'models', s?.provider],
    queryFn: () => assistantApi.models(),
    enabled: canList,
    retry: false,
    staleTime: 10 * 60_000,
  })
  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase()
    return (models.data ?? []).filter((m) => !f || m.id.toLowerCase().includes(f) || m.name.toLowerCase().includes(f)).slice(0, 200)
  }, [models.data, filter])

  if (isLoading || !s) return null
  const p = s.provider

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-5 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-200">
          <Sparkles className="h-4 w-4 text-brand-400" />
          {t('Assistant', 'Помощник')}
          <span className="rounded-sm bg-yellow-900/40 px-1.5 py-0.5 text-[10px] font-medium text-yellow-400">beta</span>
        </h2>
        <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
          <input type="checkbox" checked={s.enabled} onChange={(e) => save.mutate({ enabled: e.target.checked })} />
          {t('Enabled', 'Включён')}
        </label>
      </div>
      <p className="text-xs text-gray-500">
        {t(
          'A chat that turns requests into PiTun changes. Every change is shown as a plan and runs only after you approve it. Keys, passwords and server addresses are hidden from the model.',
          'Чат, который превращает просьбы в изменения в PiTun. Любое изменение показывается планом и выполняется только после подтверждения. Ключи, пароли и адреса серверов скрыты от модели.',
        )}
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{t('Provider', 'Провайдер')}</label>
          <select value={p} onChange={(e) => save.mutate({ provider: e.target.value as AssistantProvider })} className={INPUT}>
            {s.providers.map((x) => <option key={x} value={x}>{PROVIDER_LABEL[x]}</option>)}
          </select>
        </div>
        <div>
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
            {t('Requests to the provider go', 'Запросы к провайдеру идут')}
            <InfoTip className="ml-0.5" text={t(
              'Some providers refuse requests from some countries; through the active node avoids that. A local model is always reached directly.',
              'Часть провайдеров отказывает запросам из некоторых стран; через активную ноду это обходится. Локальная модель всегда напрямую.',
            )} />
          </label>
          <select value={s.egress} disabled={p === 'local'}
                  onChange={(e) => save.mutate({ egress: e.target.value as 'active' | 'direct' })}
                  className={clsx(INPUT, 'disabled:opacity-50')}>
            <option value="active">{t('through the active node', 'через активную ноду')}</option>
            <option value="direct">{t('directly', 'напрямую')}</option>
          </select>
        </div>
      </div>

      {(p === 'local' || p === 'openai') && (
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">
            {p === 'local' ? t('Server URL (OpenAI-compatible)', 'Адрес сервера (OpenAI-совместимый)') : t('API URL (optional)', 'Адрес API (необязательно)')}
          </label>
          <div className="flex gap-2">
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} spellCheck={false}
                   placeholder={p === 'local' ? 'http://<host>:11434/v1' : s.default_base_url}
                   className={clsx(INPUT, 'font-mono')} />
            <button onClick={() => save.mutate({ base_url: baseUrl })}
                    className="rounded-lg bg-gray-800 px-3 text-xs text-gray-200 hover:bg-gray-700">{t('Save', 'Сохранить')}</button>
          </div>
          {p === 'local' && (
            <div className="mt-2 space-y-1.5">
              <button onClick={() => discover.mutate()} disabled={discover.isPending}
                      className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-700 disabled:opacity-50">
                {discover.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Radar className="h-3.5 w-3.5" />}
                {t('Find in LAN', 'Найти в сети')}
              </button>
              {discover.data && discover.data.length === 0 && (
                <div className="text-xs text-gray-500">
                  {t('No model servers answered on the usual ports.', 'На обычных портах ни один сервер моделей не ответил.')}
                </div>
              )}
              {(discover.data ?? []).map((srv) => (
                <div key={srv.base_url} className="rounded-lg border border-gray-800 px-3 py-1.5 text-xs">
                  <div className="flex items-center gap-2">
                    <span className="text-gray-200">{srv.kind}</span>
                    <span className="font-mono text-gray-400">{srv.base_url}</span>
                    <button onClick={() => { setBaseUrl(srv.base_url); save.mutate({ base_url: srv.base_url }) }}
                            className="ml-auto text-brand-400 hover:text-brand-300">{t('Use', 'Выбрать')}</button>
                  </div>
                  {srv.models.length > 0 && (
                    <div className="mt-0.5 truncate text-[11px] text-gray-500">{srv.models.slice(0, 8).join(', ')}</div>
                  )}
                </div>
              ))}
              {discover.error && <div className="text-xs text-red-400">{apiErrorText(discover.error, 'error')}</div>}
            </div>
          )}
          {p === 'local' && (
            <p className="mt-1.5 text-[11px] text-gray-500">
              {t(
                'Ollama :11434, LM Studio :1234, llama.cpp :8080, vLLM :8000 — all with /v1. Use an instruct model with native tool calling: 8B is the minimum for single tasks, 14–32B is comfortable; 16k+ context. Rough VRAM at Q4: 8B ≈ 6 GB, 14B ≈ 10 GB, 32B ≈ 20 GB. Do not run the model on the Pi itself.',
                'Ollama :11434, LM Studio :1234, llama.cpp :8080, vLLM :8000 — везде с /v1. Нужна instruct-модель с нативным вызовом инструментов: 8B — минимум для простых задач, 14–32B — комфортно; контекст от 16k. Память при Q4: 8B ≈ 6 ГБ, 14B ≈ 10 ГБ, 32B ≈ 20 ГБ. Не запускайте модель на самой Pi.',
              )}
            </p>
          )}
        </div>
      )}

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">
          {t('API key', 'API-ключ')}{p === 'local' && ` (${t('if the server needs one', 'если серверу нужен')})`}
        </label>
        <div className="flex gap-2">
          <input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off"
                 placeholder={s.has_key[p] ? t('saved — type to replace', 'сохранён — введите, чтобы заменить') : ''}
                 className={clsx(INPUT, 'font-mono')} />
          <button onClick={() => save.mutate({ api_key: key })} disabled={!key.trim()}
                  className="rounded-lg bg-gray-800 px-3 text-xs text-gray-200 hover:bg-gray-700 disabled:opacity-50">{t('Save', 'Сохранить')}</button>
          {s.has_key[p] && (
            <button onClick={() => save.mutate({ api_key: '' })}
                    className="rounded-lg px-2 text-xs text-gray-500 hover:text-red-400">{t('Remove', 'Удалить')}</button>
          )}
        </div>
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">{t('Model', 'Модель')}</label>
        <div className="flex gap-2">
          <input value={model} onChange={(e) => setModel(e.target.value)} spellCheck={false}
                 placeholder={p === 'anthropic' ? 'claude-opus-5-5' : p === 'openrouter' ? 'vendor/model' : ''}
                 className={clsx(INPUT, 'font-mono')} />
          <button onClick={() => save.mutate({ model })} disabled={model === s.model}
                  className="rounded-lg bg-gray-800 px-3 text-xs text-gray-200 hover:bg-gray-700 disabled:opacity-50">{t('Save', 'Сохранить')}</button>
        </div>
        {canList && (
          <div className="mt-2 rounded-lg border border-gray-800">
            <input value={filter} onChange={(e) => setFilter(e.target.value)}
                   placeholder={t('Filter the provider’s models…', 'Фильтр моделей провайдера…')}
                   className="w-full rounded-t-lg bg-gray-900 border-b border-gray-800 px-3 py-1.5 text-xs text-gray-200 focus:outline-hidden" />
            <div className="max-h-48 overflow-y-auto">
              {models.isLoading && <div className="p-2 text-xs text-gray-500"><Loader2 className="inline h-3 w-3 animate-spin" /></div>}
              {models.error && <div className="p-2 text-xs text-red-400">{apiErrorText(models.error, 'error')}</div>}
              {shown.map((m) => (
                <button key={m.id} onClick={() => { setModel(m.id); save.mutate({ model: m.id }) }}
                        className={clsx('flex w-full items-center gap-2 px-3 py-1 text-left text-xs hover:bg-gray-800',
                          m.id === s.model ? 'text-brand-300' : 'text-gray-300')}>
                  <span className="font-mono truncate">{m.id}</span>
                  {m.tools === false && <span className="text-[10px] text-red-400">{t('no tools', 'без инструментов')}</span>}
                  {m.vision && <span className="text-[10px] text-gray-500">vision</span>}
                  {m.prompt_price != null && (
                    <span className="ml-auto shrink-0 text-[10px] text-gray-500">${m.prompt_price}/${m.completion_price} /M</span>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div>
        <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
          {t('Small-model mode', 'Режим малой модели')}
          <InfoTip className="ml-0.5" text={t(
            'For small home models: only ready-made tasks (route a device, build a circle, a site rule) with plain-word arguments, a short prompt and short history. Auto = on for local models.',
            'Для небольших домашних моделей: только готовые задачи (маршрут устройства, циркл, правило для сайта) с аргументами простыми словами, короткий промпт и короткая история. Авто = включён для локальных моделей.',
          )} />
        </label>
        <div className="flex items-center gap-3">
          <select value={s.small_mode} onChange={(e) => save.mutate({ small_mode: e.target.value as 'auto' | 'on' | 'off' })}
                  className={clsx(INPUT, 'sm:w-60')}>
            <option value="auto">{t('auto', 'авто')}</option>
            <option value="on">{t('on', 'включён')}</option>
            <option value="off">{t('off', 'выключен')}</option>
          </select>
          <span className="text-xs text-gray-500">
            {s.small_active ? t('active now', 'сейчас действует') : t('not active', 'не действует')}
          </span>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
            {t('Voice input', 'Голосовой ввод')}
            <InfoTip className="ml-0.5" text={t(
              'Browser: the browser recognises speech (audio goes to the browser vendor). Provider: the provider\'s speech-to-text (OpenAI, or a local server that has it). Speech server: an OpenAI-compatible whisper server in the LAN. The mic needs the page over HTTPS or localhost.',
              'Браузер: речь распознаёт сам браузер (звук уходит его разработчику). Провайдер: распознавание у провайдера (OpenAI или локальный сервер, если умеет). Сервер речи: OpenAI-совместимый whisper-сервер в сети. Микрофону нужна страница по HTTPS или localhost.',
            )} />
          </label>
          <select value={s.voice_mode} onChange={(e) => save.mutate({ voice_mode: e.target.value as AssistantSettings['voice_mode'] })}
                  className={INPUT}>
            <option value="browser">{t('browser', 'браузер')}</option>
            <option value="provider">{t('provider', 'провайдер')}</option>
            <option value="server">{t('speech server in LAN', 'сервер речи в сети')}</option>
            <option value="off">{t('off', 'выключен')}</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{t('Speech language', 'Язык речи')}</label>
          <input defaultValue={s.voice_lang} key={s.voice_lang} maxLength={5}
                 placeholder={t('auto (UI language)', 'авто (язык интерфейса)')}
                 onBlur={(e) => { if (e.target.value.trim().toLowerCase() !== s.voice_lang) save.mutate({ voice_lang: e.target.value }) }}
                 className={INPUT} />
        </div>
        {s.voice_mode !== 'browser' && s.voice_mode !== 'off' && (
          <>
            {s.voice_mode === 'server' && (
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1">{t('Speech server URL', 'Адрес сервера речи')}</label>
                <input defaultValue={s.stt_url} key={s.stt_url} placeholder="http://<host>:8000/v1"
                       onBlur={(e) => { if (e.target.value.trim() !== s.stt_url) save.mutate({ stt_url: e.target.value }) }}
                       className={INPUT} />
              </div>
            )}
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">{t('Speech model', 'Модель речи')}</label>
              <input defaultValue={s.stt_model} key={s.stt_model} placeholder="whisper-1"
                     onBlur={(e) => { if (e.target.value.trim() !== s.stt_model) save.mutate({ stt_model: e.target.value }) }}
                     className={INPUT} />
            </div>
          </>
        )}
      </div>

      <label className="flex items-center gap-2 text-xs text-gray-300">
        <input type="checkbox" checked={s.web_search} onChange={(e) => save.mutate({ web_search: e.target.checked })} />
        {t('Web search', 'Поиск в интернете')}
        <InfoTip className="ml-0.5" text={t(
          'Lets the assistant search the web and open public pages (e.g. to find a service\'s domains). Requests go out through the same route as the provider.',
          'Помощник может искать в интернете и открывать публичные страницы (например, чтобы найти домены сервиса). Запросы идут тем же маршрутом, что и к провайдеру.',
        )} />
      </label>

      <div className="grid gap-3 sm:grid-cols-2 items-end">
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{t('Monthly token limit (0 = none)', 'Лимит токенов в месяц (0 = нет)')}</label>
          <div className="flex gap-2">
            <input type="number" min={0} value={limit} onChange={(e) => setLimit(e.target.value)} className={INPUT} />
            <button onClick={() => save.mutate({ monthly_token_limit: Math.max(0, parseInt(limit, 10) || 0) })}
                    className="rounded-lg bg-gray-800 px-3 text-xs text-gray-200 hover:bg-gray-700">{t('Save', 'Сохранить')}</button>
          </div>
        </div>
        <div className="text-xs text-gray-500">
          {t('This month', 'В этом месяце')}: {(s.usage.input + s.usage.output).toLocaleString()} {t('tokens', 'токенов')}
          {s.usage.cost > 0 && ` · $${s.usage.cost.toFixed(4)}`} · {s.usage.requests} {t('requests', 'запросов')}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button onClick={() => test.mutate()} disabled={test.isPending}
                className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-500 disabled:opacity-50">
          {test.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {t('Test connection', 'Проверить подключение')}
        </button>
        {test.data && (
          <span className={clsx('flex items-center gap-1 text-xs', test.data.ok && test.data.tool_calling ? 'text-green-400' : test.data.ok ? 'text-yellow-400' : 'text-red-400')}>
            {test.data.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
            {test.data.ok
              ? (test.data.tool_calling ? t('Works, tool calling OK', 'Работает, вызов инструментов OK') : test.data.note)
              : test.data.error}
          </span>
        )}
        {test.error && <span className="text-xs text-red-400">{apiErrorText(test.error, 'error')}</span>}
      </div>
      {error && <div className="text-xs text-red-400">{error}</div>}

      <Facts />

      <AssistantMcp enabled={s.mcp_enabled} onToggle={(p) => save.mutate(p)} />
    </div>
  )
}
