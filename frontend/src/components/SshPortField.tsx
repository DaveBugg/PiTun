import { Dice5 } from 'lucide-react'
import { useT } from '@/hooks/useT'

/**
 * SSH port input + random-button (since v1.3.0-beta.7).
 *
 * Surfaces the install-time SSH port move that all three deploy
 * scripts (naive / wireguard / x-ui) support. Same control reused in
 * DeployModal and the manual-script modal so the UX stays uniform.
 *
 * Semantics:
 *   - Empty value → "leave SSH config untouched" (script no-op).
 *   - 22 → also no-op (script treats =22 as "don't move").
 *   - 1-65535 (except 22) → write a sshd_config drop-in with the new
 *     Port directive and restart sshd. Backend persists the value to
 *     `Server.port` on success so subsequent SSH calls go to the new
 *     port.
 *
 * The 🎲 button rolls a fresh 10000-65535 port — the upper half of the
 * port space avoids well-known + most distros' ephemeral-port range
 * (Linux default 32768-60999) bumping into our chosen port at runtime.
 */
export function SshPortField({
  value,
  onChange,
  serverPort,
}: {
  /** Current input value (string so empty = "no change"). */
  value: string
  onChange: (v: string) => void
  /** Currently-recorded SSH port for this server (Server.port). Used
   *  to hint the user when they're about to change away from it. */
  serverPort?: number
}) {
  const t = useT()
  const roll = () => {
    // 10000..65535 inclusive — span ~55k ports, picked to avoid the
    // 0-1023 well-known range and most of the Linux default ephemeral
    // range (32768-60999) by leaning into the upper half.
    const n = 10000 + Math.floor(Math.random() * (65535 - 10000 + 1))
    onChange(String(n))
  }
  const numeric = value.trim() === '' ? NaN : Number(value)
  const invalid = value.trim() !== '' && (
    Number.isNaN(numeric) || numeric < 1 || numeric > 65535
  )
  const same22 = numeric === 22
  return (
    <div>
      <div className="flex items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/[^\d]/g, ''))}
          placeholder={
            serverPort && serverPort !== 22
              ? `current: ${serverPort}`
              : t('leave blank — keep SSH on current port',
                  'оставьте пустым — текущий порт SSH не трогаем')
          }
          className={
            'flex-1 rounded-lg bg-gray-900 border px-3 py-2 text-sm text-gray-100 focus:outline-hidden ' +
            (invalid
              ? 'border-red-700 focus:border-red-500'
              : 'border-gray-800 focus:border-brand-500')
          }
        />
        <button
          type="button"
          onClick={roll}
          title={t('Roll a random port (10000-65535)', 'Случайный порт (10000-65535)')}
          className="rounded-lg border border-gray-700 hover:bg-gray-800 px-2.5 py-2 text-gray-300 hover:text-gray-100 transition-colors"
        >
          <Dice5 className="h-4 w-4" />
        </button>
      </div>
      {invalid && (
        <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">
          {t('Port must be 1-65535.', 'Порт должен быть 1-65535.')}
        </p>
      )}
      {same22 && (
        <p className="mt-1 text-[11px] text-gray-500">
          {t(
            '22 means "no change" — the script will leave sshd alone.',
            '22 = не менять SSH-конфиг на сервере.',
          )}
        </p>
      )}
    </div>
  )
}
