import { useState } from 'react'
import { KeyRound, Loader2, Trash2 } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ModalShell } from '@/components/ModalShell'
import { useConfirm } from '@/components/ConfirmModal'
import { useT } from '@/hooks/useT'
import { apiErrorText } from '@/lib/apiError'
import { subsApi } from '@/api/client'
import type { LinkKeyKind } from '@/types'

/**
 * Keys for encrypted subscription links (happ://crypt…, v2raytun://crypt,
 * incy://crypt…). The built-in set covers today's clients; this adds keys a
 * client starts using later without waiting for a release. Values are
 * write-only: the API returns a fingerprint, never the key.
 */

const INPUT_CLASS =
  'w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm ' +
  'text-gray-100 focus:border-brand-500 focus:outline-hidden'

const KINDS: { kind: LinkKeyKind; label: string; name: string; value: string }[] = [
  { kind: 'happ_crypt5', label: 'Happ crypt5', name: '8-char marker, e.g. abcdwxyz', value: 'RSA private key (PEM or base64 DER)' },
  { kind: 'happ_rsa', label: 'Happ crypt…crypt4', name: 'crypt, crypt2, crypt3 or crypt4', value: 'RSA private key (PEM or base64 DER)' },
  { kind: 'v2raytun', label: 'v2RayTun crypt', name: 'any label', value: 'RSA private key (PEM or base64 DER)' },
  { kind: 'incy', label: 'INCY', name: 'link host, e.g. crypt2', value: '32-byte AES key (hex or base64)' },
  { kind: 'body_aes', label: 'Encrypted body (?key=)', name: 'key name from the URL, e.g. key91', value: '16-byte AES key (hex, base64 or 16 chars)' },
]

export function LinkKeysModal({ onClose }: { onClose: () => void }) {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [kind, setKind] = useState<LinkKeyKind>('happ_crypt5')
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)

  const { data, isLoading } = useQuery({ queryKey: ['link-keys'], queryFn: () => subsApi.linkKeys() })
  const add = useMutation({
    mutationFn: () => subsApi.addLinkKey({ kind, name: name.trim(), value: value.trim() }),
    onSuccess: () => {
      setName(''); setValue(''); setError(null)
      void qc.invalidateQueries({ queryKey: ['link-keys'] })
    },
    onError: (err) => setError(apiErrorText(err, t('Could not add the key', 'Не удалось добавить ключ'))),
  })
  const del = useMutation({
    mutationFn: (id: string) => subsApi.deleteLinkKey(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['link-keys'] }),
  })
  const meta = KINDS.find((k) => k.kind === kind) ?? KINDS[0]
  const labelOf = (k: string) => KINDS.find((x) => x.kind === k)?.label ?? k
  const b = data?.builtin

  return (
    <ModalShell onClose={onClose} labelledBy="link-keys-title" z={60}>
      <div className="w-full max-w-lg rounded-2xl bg-gray-950 border border-gray-800 p-6 space-y-4">
        <h2 id="link-keys-title" className="flex items-center gap-2 text-base font-semibold text-gray-100 pr-8">
          <KeyRound className="h-4 w-4 text-brand-400" />
          {t('Encrypted link keys', 'Ключи зашифрованных ссылок')}
        </h2>
        <p className="text-xs text-gray-500">
          {t(
            'Subscriptions accept happ://crypt…, v2raytun://crypt/ and incy:// links; the link is decoded on every refresh. Add a key here when a client starts using a new one.',
            'Подписки принимают ссылки happ://crypt…, v2raytun://crypt/ и incy://; ссылка расшифровывается при каждом обновлении. Добавьте ключ, если клиент начал использовать новый.',
          )}
        </p>
        {b && (
          <p className="text-[11px] text-gray-600">
            {t('Built in', 'Встроено')}: Happ crypt5 {b.happ_crypt5} · Happ crypt1–4 {b.happ_rsa} · v2RayTun {b.v2raytun} · INCY {b.incy} · {t('body', 'тело')} {b.body_aes}
          </p>
        )}

        <div className="space-y-1.5">
          {isLoading && <Loader2 className="h-4 w-4 animate-spin text-gray-500" />}
          {data && data.custom.length === 0 && (
            <div className="text-xs text-gray-600">{t('No keys added.', 'Своих ключей нет.')}</div>
          )}
          {data?.custom.map((k) => (
            <div key={k.id} className="flex items-center justify-between gap-2 rounded-lg border border-gray-800 bg-gray-900 px-3 py-2 text-xs">
              <span className="min-w-0 truncate">
                <span className="text-gray-300">{labelOf(k.kind)}</span>
                <span className="ml-2 font-mono text-gray-100">{k.name}</span>
                <span className="ml-2 font-mono text-gray-600">#{k.fingerprint}</span>
              </span>
              <button
                onClick={async () => {
                  const ok = await confirm({
                    title: t('Delete key?', 'Удалить ключ?'),
                    body: t(`Links that need "${k.name}" will stop decoding.`, `Ссылки, которым нужен «${k.name}», перестанут расшифровываться.`),
                    danger: true,
                  })
                  if (ok) del.mutate(k.id)
                }}
                aria-label={t('Delete key', 'Удалить ключ')}
                className="text-gray-500 hover:text-red-400"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>

        <form
          onSubmit={(e) => { e.preventDefault(); add.mutate() }}
          className="space-y-2 border-t border-gray-800 pt-4"
        >
          <div className="grid grid-cols-2 gap-2">
            <select value={kind} onChange={(e) => setKind(e.target.value as LinkKeyKind)} className={INPUT_CLASS}>
              {KINDS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
            </select>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={meta.name}
                   required className={INPUT_CLASS + ' font-mono'} />
          </div>
          <textarea value={value} onChange={(e) => setValue(e.target.value)} placeholder={meta.value}
                    required rows={4} spellCheck={false} className={INPUT_CLASS + ' font-mono text-xs'} />
          {error && <div className="text-xs text-red-400">{error}</div>}
          <div className="flex justify-end">
            <button type="submit" disabled={add.isPending || !name.trim() || !value.trim()}
                    className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50">
              {add.isPending ? t('Adding…', 'Добавляю…') : t('Add key', 'Добавить ключ')}
            </button>
          </div>
        </form>
      </div>
    </ModalShell>
  )
}
