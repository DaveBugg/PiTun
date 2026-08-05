import { FormEvent, useState } from 'react'
import { X } from 'lucide-react'

import { ModalShell } from '@/components/ModalShell'
import { useT } from '@/hooks/useT'
import type { Server, ServerCreate, ServerUpdate } from '@/types'

interface Props {
  initial?: Server | null
  onClose: () => void
  onSubmit: (data: ServerCreate | ServerUpdate) => Promise<void> | void
}

/**
 * Add / Edit form for a Server.
 *
 * Three-state secret semantics on edit (matches the backend ServerUpdate
 * contract):
 *   - secret field left untouched in the form  → not sent  → unchanged
 *   - secret field shown as empty placeholder  → empty str → unchanged
 *     (existing value indicated by the "set" badge next to the label)
 *   - "Clear" checkbox + empty field           → null      → cleared
 *   - typed value                              → string    → replaced
 *
 * On create, the form sends concrete values (or omits empty fields).
 */
export function ServerForm({ initial, onClose, onSubmit }: Props) {
  const t = useT()
  const editing = !!initial

  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [host, setHost] = useState(initial?.host ?? '')
  const [port, setPort] = useState<number>(initial?.port ?? 22)
  const [user, setUser] = useState(initial?.user ?? 'root')
  const [authType, setAuthType] = useState<'password' | 'key'>(
    initial?.auth_type ?? 'password',
  )

  const [password, setPassword] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [passphrase, setPassphrase] = useState('')

  // "Clear" checkboxes — three-state implementation. Checked + empty
  // input → send `null` to clear the stored secret.
  const [clearPassword, setClearPassword] = useState(false)
  const [clearPrivateKey, setClearPrivateKey] = useState(false)
  const [clearPassphrase, setClearPassphrase] = useState(false)

  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    if (!name.trim() || !host.trim()) {
      setError(t('Name and host are required', 'Name и host обязательны'))
      return
    }

    const payload: ServerCreate | ServerUpdate = {
      name: name.trim(),
      description: description.trim() || null,
      host: host.trim(),
      port,
      user: user.trim() || 'root',
      auth_type: authType,
    }

    // Three-state secret handling — only different on edit.
    const setSecret = (
      key: 'password' | 'private_key' | 'passphrase',
      value: string,
      clear: boolean,
    ) => {
      if (!editing) {
        // On create: include only if user typed something.
        if (value) (payload as Record<string, unknown>)[key] = value
        return
      }
      if (clear) (payload as Record<string, unknown>)[key] = null
      else if (value) (payload as Record<string, unknown>)[key] = value
      // else: don't touch the field → backend leaves existing value.
    }

    setSecret('password', password, clearPassword)
    setSecret('private_key', privateKey, clearPrivateKey)
    setSecret('passphrase', passphrase, clearPassphrase)

    setSubmitting(true)
    try {
      await onSubmit(payload)
      onClose()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to save'
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalShell onClose={onClose} labelledBy="server-form-title">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-xl rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto"
      >
        <div className="flex items-center justify-between mb-4">
          <h2
            id="server-form-title"
            className="text-lg font-semibold text-gray-100"
          >
            {editing ? t('Edit server', 'Редактировать сервер') : t('Add server', 'Добавить сервер')}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300 transition-colors"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {error && (
          <div className="mb-3 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 px-3 py-2 text-sm text-red-700 dark:text-red-300">
            {error}
          </div>
        )}

        <div className="space-y-3">
          <Field label={t('Name', 'Название')}>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Hetzner FRA-1"
              className={inputCls}
              autoFocus
              required
            />
          </Field>

          <Field label={t('Description', 'Описание')} hint={t('Optional notes', 'Необязательные заметки')}>
            <input
              type="text"
              value={description ?? ''}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="naive endpoint, 4 vCPU, 16 EUR/mo"
              className={inputCls}
            />
          </Field>

          <div className="grid grid-cols-12 gap-2">
            <div className="col-span-7">
              <Field label={t('Host', 'Хост')}>
                <input
                  type="text"
                  value={host}
                  onChange={(e) => setHost(e.target.value)}
                  placeholder="1.2.3.4 or host.example.com"
                  className={inputCls}
                  required
                />
              </Field>
            </div>
            <div className="col-span-2">
              <Field label={t('Port', 'Порт')}>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={port}
                  onChange={(e) => setPort(Number(e.target.value) || 22)}
                  className={inputCls}
                />
              </Field>
            </div>
            <div className="col-span-3">
              <Field label={t('User', 'Юзер')}>
                <input
                  type="text"
                  value={user}
                  onChange={(e) => setUser(e.target.value)}
                  placeholder="root"
                  className={inputCls}
                />
              </Field>
            </div>
          </div>

          <Field label={t('Auth method', 'Метод авторизации')}>
            <div className="flex gap-2">
              {(['password', 'key'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setAuthType(m)}
                  className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                    authType === m
                      ? 'bg-brand-50 dark:bg-brand-600/20 text-brand-300 border border-brand-400/40'
                      : 'bg-gray-900 border border-gray-800 text-gray-400 hover:bg-gray-800'
                  }`}
                >
                  {m === 'password' ? t('Password', 'Пароль') : t('Private key', 'Приватный ключ')}
                </button>
              ))}
            </div>
          </Field>

          {authType === 'password' && (
            <SecretField
              label={t('Password', 'Пароль')}
              isSet={!!initial?.has_password}
              clear={clearPassword}
              onClearChange={setClearPassword}
              editing={editing}
            >
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={initial?.has_password ? '••••••• (' + t('leave blank to keep existing', 'оставьте пустым, чтобы не менять') + ')' : ''}
                className={inputCls}
                autoComplete="new-password"
                disabled={clearPassword}
              />
            </SecretField>
          )}

          {authType === 'key' && (
            <>
              <SecretField
                label={t('Private key (PEM)', 'Приватный ключ (PEM)')}
                isSet={!!initial?.has_private_key}
                clear={clearPrivateKey}
                onClearChange={setClearPrivateKey}
                editing={editing}
              >
                <textarea
                  value={privateKey}
                  onChange={(e) => setPrivateKey(e.target.value)}
                  placeholder={
                    initial?.has_private_key
                      ? '••••••• (' + t('leave blank to keep existing', 'оставьте пустым, чтобы не менять') + ')'
                      : '-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----'
                  }
                  rows={6}
                  className={`${inputCls} font-mono text-xs`}
                  disabled={clearPrivateKey}
                />
              </SecretField>
              <SecretField
                label={t('Passphrase', 'Пароль ключа')}
                isSet={!!initial?.has_passphrase}
                clear={clearPassphrase}
                onClearChange={setClearPassphrase}
                editing={editing}
                hint={t('Empty if the key is not encrypted', 'Пусто, если ключ без пароля')}
              >
                <input
                  type="password"
                  value={passphrase}
                  onChange={(e) => setPassphrase(e.target.value)}
                  className={inputCls}
                  autoComplete="new-password"
                  disabled={clearPassphrase}
                />
              </SecretField>
            </>
          )}
        </div>

        <div className="flex gap-2 pt-5">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-400 hover:bg-gray-800 transition-colors"
          >
            {t('Cancel', 'Отмена')}
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="flex-1 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
          >
            {submitting ? t('Saving…', 'Сохранение…') : editing ? t('Save', 'Сохранить') : t('Create', 'Создать')}
          </button>
        </div>
      </form>
    </ModalShell>
  )
}

// ── Building blocks ─────────────────────────────────────────────────────────

const inputCls =
  'w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden disabled:opacity-50'

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        <span className="text-gray-500">{label}</span>
        {hint && <span className="text-gray-600">— {hint}</span>}
      </div>
      {children}
    </label>
  )
}

function SecretField({
  label,
  isSet,
  clear,
  onClearChange,
  editing,
  hint,
  children,
}: {
  label: string
  isSet: boolean
  clear: boolean
  onClearChange: (v: boolean) => void
  editing: boolean
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="block">
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        <span className="text-gray-500">{label}</span>
        {isSet && (
          <span className="rounded-sm bg-green-50 dark:bg-green-900/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-green-600 dark:text-green-400">
            set
          </span>
        )}
        {hint && <span className="text-gray-600">— {hint}</span>}
        {editing && isSet && (
          <label className="ml-auto flex items-center gap-1.5 text-[11px] text-gray-500 cursor-pointer">
            <input
              type="checkbox"
              checked={clear}
              onChange={(e) => onClearChange(e.target.checked)}
              className="h-3 w-3"
            />
            clear
          </label>
        )}
      </div>
      {children}
    </div>
  )
}
