import { useEffect, useRef, useState } from 'react'
import {
  Bot, Check, ChevronDown, ImagePlus, Loader2, Mic, MicOff, Plus, Send, Sparkles, Trash2, Undo2, Wrench, X, Zap,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { assistantApi } from '@/api/client'
import type { AssistantEvent, AssistantImage, AssistantItem, AssistantJob, AssistantPlan } from '@/api/client'
import { MACRO_LABEL, MacroForm } from '@/components/AssistantMacroForm'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'
import { useVoiceInput, voiceUnavailable } from '@/hooks/useVoiceInput'
import { useAppStore } from '@/store'

/**
 * The assistant: a floating button and a slide-over chat. Changes come back
 * as plan cards; nothing runs until the user approves one.
 */

const EXAMPLES: { en: string; ru: string }[] = [
  { en: 'Route the living-room TV through the fastest German node', ru: 'Пусти телевизор в зале через самую быструю немецкую ноду' },
  { en: 'Build a "best" circle from my subscription without Russia and Belarus', ru: 'Собери циркл «best» из моей подписки без России и Беларуси' },
  { en: 'Send chatgpt.com through a Netherlands node', ru: 'Пусти chatgpt.com через ноду в Нидерландах' },
  { en: 'Which nodes are online and fastest right now?', ru: 'Какие ноды сейчас онлайн и самые быстрые?' },
  { en: 'Make youtube.com go direct', ru: 'Сделай youtube.com напрямую' },
  { en: 'Which devices are on the network?', ru: 'Какие устройства сейчас в сети?' },
  { en: 'Why does example.com not open on the TV?', ru: 'Почему на телевизоре не открывается example.com?' },
]

const TOOL_LABEL: Record<string, { en: string; ru: string }> = {
  search: { en: 'searched the API', ru: 'поиск в API' },
  read: { en: 'read', ru: 'прочитал' },
  act: { en: 'prepared a plan', ru: 'подготовил план' },
  find_nodes: { en: 'looked up nodes', ru: 'искал ноды' },
  find_devices: { en: 'looked up devices', ru: 'искал устройства' },
  route_device: { en: 'routing a device', ru: 'маршрут устройства' },
  build_circle: { en: 'building a circle', ru: 'сборка циркла' },
  add_site_rule: { en: 'rule for a site', ru: 'правило для сайта' },
  remember: { en: 'remembered', ru: 'запомнил' },
  forget: { en: 'forgot a fact', ru: 'забыл факт' },
  deploy_server: { en: 'install on a VPS', ru: 'установка на VPS' },
  issue_nodes: { en: 'issuing nodes', ru: 'выпуск нод' },
  diagnose: { en: 'diagnosing', ru: 'диагностика' },
  web_search: { en: 'searched the web', ru: 'поиск в интернете' },
  web_fetch: { en: 'opened a page', ru: 'открыл страницу' },
  router: { en: 'picking the task', ru: 'выбор задачи' },
  summary: { en: 'summarised the older messages', ru: 'сжал старые сообщения' },
}

const MAX_IMAGES = 4
const MAX_SIDE = 1600

/** Shrink a screenshot to at most MAX_SIDE px and encode it as JPEG. */
async function toImage(file: Blob): Promise<AssistantImage & { preview: string }> {
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error('not an image'))
      el.src = url
    })
    const k = Math.min(1, MAX_SIDE / Math.max(img.width, img.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(img.width * k)
    canvas.height = Math.round(img.height * k)
    canvas.getContext('2d')?.drawImage(img, 0, 0, canvas.width, canvas.height)
    const preview = canvas.toDataURL('image/jpeg', 0.85)
    return { media_type: 'image/jpeg', data: preview.split(',', 2)[1], preview }
  } finally {
    URL.revokeObjectURL(url)
  }
}

function json(v: unknown): string {
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

function PlanCard({ plan, job, onDecide, onUndo, busy }: {
  plan: AssistantPlan
  job?: AssistantJob
  onDecide: (d: 'approve' | 'reject') => void
  onUndo: () => void
  busy: boolean
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const status = {
    pending: { cls: 'border-brand-700 bg-brand-900/20', label: t('Waiting for your approval', 'Ждёт подтверждения') },
    running: { cls: 'border-gray-700', label: t('Running…', 'Выполняется…') },
    executed: { cls: 'border-green-800 bg-green-950/20', label: t('Done', 'Выполнено') },
    failed: { cls: 'border-red-800 bg-red-950/20', label: t('Failed', 'Ошибка') },
    rejected: { cls: 'border-gray-800 opacity-70', label: t('Rejected', 'Отклонено') },
    undone: { cls: 'border-gray-800 opacity-70', label: t('Undone', 'Отменено') },
  }[plan.status]
  const danger = plan.steps.some((s) => s.method === 'DELETE')
  return (
    <div className={clsx('rounded-lg border p-3 text-xs space-y-2', status.cls)}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium text-gray-100">{plan.title}</div>
          <div className="text-[11px] text-gray-500">#{plan.id} · {status.label}</div>
        </div>
        <button onClick={() => setOpen(!open)} className="text-gray-500 hover:text-gray-300 shrink-0"
                aria-label={t('Details', 'Подробнее')}>
          <ChevronDown className={clsx('h-4 w-4 transition-transform', open && 'rotate-180')} />
        </button>
      </div>
      {plan.summary && <p className="text-gray-300 whitespace-pre-wrap">{plan.summary}</p>}
      <ol className="space-y-1">
        {plan.steps.map((s, i) => {
          const r = plan.results[i]
          return (
            <li key={i} className="rounded-sm bg-gray-900/70 px-2 py-1">
              <div className="flex items-center gap-2">
                <span className={clsx('font-mono font-semibold',
                  s.method === 'DELETE' ? 'text-red-400' : s.method === 'POST' ? 'text-green-400' : 'text-yellow-400')}>
                  {s.method}
                </span>
                <span className="font-mono text-gray-300 truncate">{s.path}</span>
                {r && (
                  <span className={clsx('ml-auto font-mono', r.ok ? 'text-green-400' : 'text-red-400')}>{r.status}</span>
                )}
              </div>
              {s.note && <div className="text-gray-500 mt-0.5">{s.note}</div>}
              {open && (
                <div className="mt-1 space-y-1">
                  {s.body !== undefined && s.body !== null && (
                    <pre className="max-h-48 overflow-auto rounded-sm bg-gray-950 p-1.5 text-[10px] text-gray-300">{json(s.body)}</pre>
                  )}
                  {plan.before[i] != null && (
                    <details>
                      <summary className="cursor-pointer text-gray-500">{t('Current state', 'Текущее состояние')}</summary>
                      <pre className="max-h-48 overflow-auto rounded-sm bg-gray-950 p-1.5 text-[10px] text-gray-400">{json(plan.before[i])}</pre>
                    </details>
                  )}
                  {r && !r.ok && (
                    <pre className="max-h-32 overflow-auto rounded-sm bg-red-950/40 p-1.5 text-[10px] text-red-300">{json(r.body)}</pre>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ol>
      {job && (
        <div className="flex items-center gap-1.5 rounded-sm bg-gray-900/70 px-2 py-1 text-[11px]">
          {job.status === 'running'
            ? <Loader2 className="h-3 w-3 animate-spin text-gray-400 shrink-0" />
            : <Wrench className={clsx('h-3 w-3 shrink-0', job.status === 'succeeded' ? 'text-green-400' : 'text-red-400')} />}
          <span className="text-gray-400 shrink-0">
            {job.status === 'running' ? t('Installing…', 'Установка…') : job.status}
          </span>
          <span className="truncate font-mono text-gray-500">{job.line}</span>
        </div>
      )}
      {plan.can_undo && (
        <div className="pt-1 space-y-1">
          <button
            onClick={onUndo}
            disabled={busy}
            className="flex items-center gap-1 rounded-md bg-gray-800 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 disabled:opacity-50"
          >
            <Undo2 className="h-3.5 w-3.5" />{t('Undo', 'Отменить')}
          </button>
          {(plan.undo_skipped ?? []).length > 0 && (
            <div className="text-[11px] text-gray-500">
              {t('Undo will not revert:', 'Отмена не вернёт:')} {(plan.undo_skipped ?? []).join('; ')}
            </div>
          )}
        </div>
      )}
      {plan.status === 'pending' && (
        <div className="flex gap-2 pt-1">
          <button
            onClick={() => onDecide('approve')}
            disabled={busy}
            className={clsx('flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50',
              danger ? 'bg-red-600 hover:bg-red-500' : 'bg-brand-600 hover:bg-brand-500')}
          >
            <Check className="h-3.5 w-3.5" />{t('Apply', 'Применить')}
          </button>
          <button
            onClick={() => onDecide('reject')}
            disabled={busy}
            className="rounded-md bg-gray-800 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 disabled:opacity-50"
          >
            {t('Reject', 'Отклонить')}
          </button>
        </div>
      )}
    </div>
  )
}

export function AssistantPanel() {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [open, setOpen] = useState(false)
  const [cid, setCid] = useState<number | null>(null)
  const [items, setItems] = useState<AssistantItem[]>([])
  const [input, setInput] = useState('')
  const [images, setImages] = useState<(AssistantImage & { preview: string })[]>([])
  const [busy, setBusy] = useState(false)
  const [jobs, setJobs] = useState<Record<number, AssistantJob>>({})
  const [menu, setMenu] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const uiLang = useAppStore((st) => st.lang)
  const voiceBase = useRef('')
  const endRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const settings = useQuery({
    queryKey: ['assistant', 'settings'],
    queryFn: () => assistantApi.settings(),
    staleTime: 60_000,
    retry: false,
  })
  const macros = useQuery({
    queryKey: ['assistant', 'macros'],
    queryFn: () => assistantApi.macros(),
    enabled: open && !!settings.data?.enabled,
    staleTime: Infinity,
  })
  const convs = useQuery({
    queryKey: ['assistant', 'conversations'],
    queryFn: () => assistantApi.conversations(),
    enabled: open && !!settings.data?.enabled,
  })

  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [items, open])

  const load = async (id: number) => {
    setCid(id)
    const c = await assistantApi.conversation(id)
    setItems(c.items)
  }

  useEffect(() => {
    if (open && cid === null && convs.data && convs.data.length > 0) void load(convs.data[0].id)
  }, [open, cid, convs.data])

  const del = useMutation({
    mutationFn: (id: number) => assistantApi.deleteConversation(id),
    onSuccess: () => {
      setCid(null); setItems([])
      void qc.invalidateQueries({ queryKey: ['assistant', 'conversations'] })
    },
  })

  const voiceMode = settings.data?.voice_mode ?? 'off'
  const speechLang = settings.data?.voice_lang || (uiLang === 'ru' ? 'ru-RU' : 'en-US')
  const voice = useVoiceInput(voiceMode, speechLang, (text) => {
    const base = voiceBase.current
    setInput(base + (base && text ? ' ' : '') + text)
  })
  const noVoice = voiceUnavailable(voiceMode)
  const micTitle = noVoice === 'insecure'
    ? t('Voice needs the page over HTTPS (or localhost)', 'Голосу нужна страница по HTTPS (или localhost)')
    : noVoice === 'unsupported'
      ? t('This browser cannot do speech input', 'Этот браузер не умеет голосовой ввод')
      : voice.state === 'listening' ? t('Stop and keep the text', 'Стоп, оставить текст')
        : t('Speak (the text lands here for editing)', 'Сказать (текст появится здесь для правки)')

  const onEvent = (e: AssistantEvent) => {
    if (e.type === 'job') {
      const { type: _type, ...job } = e
      setJobs((p) => ({ ...p, [job.plan_id]: job }))
      return
    }
    setItems((prev) => {
      const next = [...prev]
      switch (e.type) {
        case 'message': next.push({ kind: 'assistant', text: e.text }); break
        case 'user_masked': {
          for (let i = next.length - 1; i >= 0; i--) {
            const it = next[i]
            if (it.kind === 'user') { next[i] = { ...it, text: e.text }; break }
          }
          break
        }
        case 'tool': next.push({ kind: 'tool', name: e.name, summary: e.summary }); break
        case 'tool_done': {
          for (let i = next.length - 1; i >= 0; i--) {
            const it = next[i]
            if (it.kind === 'tool' && it.name === e.name && it.ok === undefined) { next[i] = { ...it, ok: e.ok }; break }
          }
          break
        }
        case 'plan': {
          const idx = next.findIndex((it) => it.kind === 'plan' && it.plan.id === e.plan.id)
          if (idx >= 0) next[idx] = { kind: 'plan', plan: e.plan }
          else next.push({ kind: 'plan', plan: e.plan })
          break
        }
        case 'error': next.push({ kind: 'error', text: e.message }); break
        case 'form': next.push({ kind: 'form', macro: e.macro, args: e.args, reason: e.reason }); break
        default: break
      }
      return next
    })
    if (e.type === 'usage') void qc.invalidateQueries({ queryKey: ['assistant', 'settings'] })
  }

  const attach = async (files: Blob[]) => {
    const pics = files.filter((f) => f.type.startsWith('image/')).slice(0, MAX_IMAGES - images.length)
    const out: (AssistantImage & { preview: string })[] = []
    for (const f of pics) {
      try { out.push(await toImage(f)) } catch { /* not decodable */ }
    }
    if (out.length) setImages((prev) => [...prev, ...out].slice(0, MAX_IMAGES))
  }

  const send = async (text: string) => {
    const msg = text.trim()
    const pics = images
    if ((!msg && pics.length === 0) || busy) return
    setBusy(true)
    setInput('')
    setImages([])
    try {
      let id = cid
      if (id === null) {
        id = (await assistantApi.createConversation()).id
        setCid(id)
      }
      setItems((p) => [...p, { kind: 'user', text: msg, images: pics.map((im) => im.preview) }])
      abortRef.current = new AbortController()
      await assistantApi.send(id, msg, pics.map(({ media_type, data }) => ({ media_type, data })),
        onEvent, abortRef.current.signal)
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setItems((p) => [...p, { kind: 'error', text: (err as Error).message }])
      }
    } finally {
      setBusy(false)
      abortRef.current = null
      void qc.invalidateQueries({ queryKey: ['assistant', 'conversations'] })
    }
  }

  const ensureChat = async () => {
    if (cid !== null) return cid
    const id = (await assistantApi.createConversation()).id
    setCid(id)
    return id
  }

  const openForm = (name: string) => {
    setMenu(false)
    setItems((p) => [...p, { kind: 'form', macro: name, args: {} }])
  }

  const runForm = async (index: number, name: string, args: Record<string, unknown>) => {
    setBusy(true)
    setItems((p) => p.filter((_, k) => k !== index))
    try {
      const id = await ensureChat()
      await assistantApi.runMacro(id, name, args, onEvent)
    } catch (err) {
      setItems((p) => [...p, { kind: 'error', text: (err as Error).message }])
    } finally {
      setBusy(false)
      void qc.invalidateQueries({ queryKey: ['assistant', 'conversations'] })
    }
  }

  const decide = async (plan: AssistantPlan, d: 'approve' | 'reject') => {
    if (d === 'approve' && plan.steps.some((s) => /\/deploy$/.test(s.path))) {
      const ok = await confirm({
        title: t('Install on the VPS?', 'Устанавливать на VPS?'),
        body: t('This runs an install script on the server over SSH. It takes a few minutes and cannot be undone from here.',
          'На сервере по SSH запустится скрипт установки. Это займёт несколько минут, и отсюда это не отменить.'),
        danger: true,
      })
      if (!ok) return
    }
    if (d === 'approve' && plan.steps.some((s) => s.method === 'DELETE')) {
      const ok = await confirm({
        title: t('Apply a plan that deletes?', 'Применить план с удалением?'),
        body: t('This plan removes something. Undo re-creates it with a new id, links to it may be lost.',
          'План что-то удаляет. «Отменить» создаст это заново с новым id, связи могут потеряться.'),
        danger: true,
      })
      if (!ok) return
    }
    setBusy(true)
    try {
      await assistantApi.decide(plan.id, d, onEvent)
      // Applied changes touch other pages' data.
      void qc.invalidateQueries()
    } catch (err) {
      setItems((p) => [...p, { kind: 'error', text: (err as Error).message }])
    } finally {
      setBusy(false)
    }
  }

  const undo = async (plan: AssistantPlan) => {
    const ok = await confirm({
      title: t('Undo this plan?', 'Отменить этот план?'),
      body: t('Its changes are reverted. Changes made after it to the same things are overwritten.',
        'Его изменения будут возвращены. Более поздние правки тех же объектов перезапишутся.'),
    })
    if (!ok) return
    setBusy(true)
    try {
      const view = await assistantApi.undo(plan.id)
      onEvent({ type: 'plan', plan: view })
      if (view.undo_ok === false) {
        setItems((p) => [...p, { kind: 'error', text: t('Undo failed: ', 'Отмена не удалась: ') + json(view.undo_error) }])
      }
      void qc.invalidateQueries()
    } catch (err) {
      setItems((p) => [...p, { kind: 'error', text: (err as Error).message }])
    } finally {
      setBusy(false)
    }
  }

  if (!settings.data?.enabled) return null

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label={t('Assistant', 'Помощник')}
          title={t('Assistant', 'Помощник')}
          className="fixed bottom-5 right-5 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-brand-600 text-white shadow-lg hover:bg-brand-500 transition-colors"
        >
          <Sparkles className="h-5 w-5" />
        </button>
      )}
      {open && (
        <div
          className="fixed inset-y-0 right-0 z-50 flex w-full sm:w-[440px] flex-col border-l border-gray-800 bg-gray-950 shadow-2xl"
          onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) e.preventDefault() }}
          onDrop={(e) => { e.preventDefault(); void attach(Array.from(e.dataTransfer.files)) }}
        >
          <div className="flex items-center gap-2 border-b border-gray-800 px-3 py-2.5">
            <Bot className="h-5 w-5 text-brand-400 shrink-0" />
            <select
              value={cid ?? ''}
              onChange={(e) => { if (e.target.value) void load(Number(e.target.value)) }}
              className="min-w-0 flex-1 truncate rounded-sm bg-gray-900 border border-gray-800 px-2 py-1 text-sm text-gray-200"
            >
              {cid === null && <option value="">{t('New chat', 'Новый чат')}</option>}
              {(convs.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
            </select>
            <button onClick={() => { setCid(null); setItems([]) }} disabled={busy}
                    title={t('New chat', 'Новый чат')} className="text-gray-400 hover:text-gray-200 disabled:opacity-50">
              <Plus className="h-4 w-4" />
            </button>
            {cid !== null && (
              <button
                onClick={async () => {
                  if (await confirm({ title: t('Delete this chat?', 'Удалить этот чат?'), danger: true })) del.mutate(cid)
                }}
                disabled={busy}
                title={t('Delete chat', 'Удалить чат')}
                className="text-gray-500 hover:text-red-400 disabled:opacity-50"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            )}
            <button onClick={() => setOpen(false)} aria-label={t('Close', 'Закрыть')} className="text-gray-400 hover:text-gray-200">
              <X className="h-5 w-5" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
            {items.length === 0 && (
              <div className="space-y-3 pt-2">
                <p className="text-xs text-gray-500">
                  {t(
                    'I can look things up and prepare changes: routing devices and sites through nodes, building circles, installing x-ui on a VPS and issuing nodes, finding out what does not work. Every change comes as a plan you approve first.',
                    'Я могу искать информацию и готовить изменения: пускать устройства и сайты через ноды, собирать циркл, ставить x-ui на VPS и выпускать ноды, искать, что не работает. Любое изменение приходит планом, который вы сначала подтверждаете.',
                  )}
                </p>
                <p className="text-[11px] text-gray-600 whitespace-pre-line">
                  {t(
                    'To add a VPS, paste its access on separate lines: IP, password, then optionally user (root) and port (22). The box reads it itself; the password never goes to the model.',
                    'Чтобы добавить VPS, вставьте доступ построчно: IP, пароль, дальше по желанию пользователь (root) и порт (22). Бокс разберёт это сам, пароль модели не уходит.',
                  )}
                </p>
                <div className="space-y-1.5">
                  {EXAMPLES.map((ex) => {
                    const text = t(ex.en, ex.ru)
                    return (
                      <button key={ex.en} onClick={() => void send(text)} disabled={busy}
                              className="block w-full rounded-lg border border-gray-800 bg-gray-900/50 px-3 py-2 text-left text-xs text-gray-300 hover:border-gray-700 disabled:opacity-50">
                        {text}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
            {items.map((it, i) => {
              if (it.kind === 'user') {
                return (
                  <div key={i} className="ml-8 rounded-lg bg-brand-900/40 px-3 py-2 text-sm text-gray-100 whitespace-pre-wrap space-y-1.5">
                    {(it.images ?? []).length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {(it.images ?? []).map((src, j) => (
                          <a key={j} href={src} target="_blank" rel="noreferrer">
                            <img src={src} alt="" className="h-16 max-w-[120px] rounded-sm object-cover" />
                          </a>
                        ))}
                      </div>
                    )}
                    {it.text && it.text !== '(screenshot)' && <div>{it.text}</div>}
                  </div>
                )
              }
              if (it.kind === 'assistant') {
                return <div key={i} className="mr-4 rounded-lg bg-gray-900 px-3 py-2 text-sm text-gray-200 whitespace-pre-wrap">{it.text}</div>
              }
              if (it.kind === 'tool') {
                const label = TOOL_LABEL[it.name] ? t(TOOL_LABEL[it.name].en, TOOL_LABEL[it.name].ru) : it.name
                return (
                  <div key={i} className="flex items-center gap-1.5 text-[11px] text-gray-500">
                    {it.ok === undefined ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wrench className={clsx('h-3 w-3', it.ok === false && 'text-red-400')} />}
                    <span>{label}</span>
                    {it.summary && <span className="truncate text-gray-600">· {it.summary}</span>}
                  </div>
                )
              }
              if (it.kind === 'plan') {
                return <PlanCard key={i} plan={it.plan} job={jobs[it.plan.id]} busy={busy}
                                 onDecide={(d) => void decide(it.plan, d)} onUndo={() => void undo(it.plan)} />
              }
              if (it.kind === 'form') {
                const spec = (macros.data ?? []).find((m) => m.name === it.macro)
                if (!spec) return null
                return <MacroForm key={i} macro={spec} initial={it.args} reason={it.reason} busy={busy}
                                  onSubmit={(args) => void runForm(i, it.macro, args)}
                                  onClose={() => setItems((p) => p.filter((_, k) => k !== i))} />
              }
              if (it.kind === 'note') {
                return null  // what the model was told after a plan; the card already shows it
              }
              return <div key={i} className="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">{it.text}</div>
            })}
            {busy && (
              <div className="flex items-center gap-2 text-xs text-gray-500">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />{t('Thinking…', 'Думаю…')}
                <button onClick={() => abortRef.current?.abort()} className="ml-2 text-gray-600 hover:text-gray-300">
                  {t('Stop', 'Стоп')}
                </button>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {images.length > 0 && (
            <div className="flex flex-wrap gap-2 border-t border-gray-800 px-3 pt-2">
              {images.map((im, j) => (
                <div key={j} className="relative">
                  <img src={im.preview} alt="" className="h-14 w-20 rounded-sm object-cover" />
                  <button
                    onClick={() => setImages((p) => p.filter((_, k) => k !== j))}
                    aria-label={t('Remove', 'Убрать')}
                    className="absolute -right-1.5 -top-1.5 rounded-full bg-gray-800 p-0.5 text-gray-300 hover:bg-gray-700"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
          <form
            onSubmit={(e) => { e.preventDefault(); void send(input) }}
            className="flex items-end gap-2 border-t border-gray-800 p-3"
          >
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => { void attach(Array.from(e.target.files ?? [])); e.target.value = '' }}
            />
            <div className="relative">
              <button type="button" onClick={() => setMenu(!menu)} disabled={busy}
                      title={t('Quick actions (forms, no model needed)', 'Быстрые действия (формы, без модели)')}
                      className="flex h-9 w-9 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-900 hover:text-gray-200 disabled:opacity-50">
                <Zap className="h-4 w-4" />
              </button>
              {menu && (
                <div className="absolute bottom-10 left-0 z-10 w-60 rounded-lg border border-gray-800 bg-gray-900 py-1 shadow-xl">
                  {(macros.data ?? []).map((m) => (
                    <button key={m.name} type="button" onClick={() => openForm(m.name)}
                            className="block w-full px-3 py-1.5 text-left text-xs text-gray-200 hover:bg-gray-800">
                      {MACRO_LABEL[m.name] ? t(MACRO_LABEL[m.name].en, MACRO_LABEL[m.name].ru) : m.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button type="button" onClick={() => fileRef.current?.click()}
                    disabled={busy || images.length >= MAX_IMAGES}
                    title={t('Attach a screenshot (or paste / drop it)', 'Приложить скриншот (или вставить / перетащить)')}
                    className="flex h-9 w-9 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-900 hover:text-gray-200 disabled:opacity-50">
              <ImagePlus className="h-4 w-4" />
            </button>
            {noVoice !== 'off' && (
              <button type="button"
                      onClick={() => { if (voice.state === 'idle') voiceBase.current = input.trim(); voice.toggle() }}
                      disabled={busy || noVoice !== null || voice.state === 'transcribing'}
                      title={voice.error ? `${micTitle} — ${voice.error}` : micTitle}
                      className={clsx('flex h-9 w-9 items-center justify-center rounded-lg disabled:opacity-50',
                        voice.state === 'listening' ? 'bg-red-600/80 text-white animate-pulse'
                          : voice.error ? 'text-red-400 hover:bg-gray-900' : 'text-gray-400 hover:bg-gray-900 hover:text-gray-200')}>
                {voice.state === 'transcribing' ? <Loader2 className="h-4 w-4 animate-spin" />
                  : noVoice ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
              </button>
            )}
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPaste={(e) => {
                const files = Array.from(e.clipboardData.files)
                if (files.some((f) => f.type.startsWith('image/'))) { e.preventDefault(); void attach(files) }
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(input) }
              }}
              rows={2}
              placeholder={t('Ask or describe a change…', 'Спросите или опишите изменение…')}
              className="flex-1 resize-none rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
            />
            <button type="submit" disabled={busy || (!input.trim() && images.length === 0)} aria-label={t('Send', 'Отправить')}
                    className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white hover:bg-brand-500 disabled:opacity-50">
              <Send className="h-4 w-4" />
            </button>
          </form>
        </div>
      )}
    </>
  )
}
