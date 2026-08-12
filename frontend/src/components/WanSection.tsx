import { Globe, AlertTriangle } from 'lucide-react'

import { useT } from '@/hooks/useT'

/**
 * How the box gets its uplink (router mode, phase 2).
 *
 * The modes exist because a line needing PPPoE, a VLAN tag or a cloned MAC
 * looks exactly like a dead line until it's configured — the operator has no
 * way to tell from the symptom, so the options have to be discoverable.
 */
export default function WanSection({
  wan, values, onChange,
}: {
  wan: string
  values: {
    mode: string
    vlanId: string
    macClone: string
    address: string
    gateway: string
    dns: string
    pppoeUser: string
    pppoePassword: string
  }
  onChange: (key: string, value: string | number) => void
}) {
  const t = useT()
  if (!wan) return null

  const effective = values.mode === 'pppoe'
    ? 'ppp0'
    : Number(values.vlanId) > 0 ? `${wan}.${values.vlanId}` : wan

  const input = (key: string, value: string, placeholder = '', mono = true, type = 'text') => (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(key, e.target.value)}
      placeholder={placeholder}
      className={
        'w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600 ' +
        (mono ? 'font-mono' : '')
      }
    />
  )

  return (
    <div className="mt-3 space-y-3">
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-gray-500">
        <Globe className="h-3.5 w-3.5 text-brand-400" />
        {t('Internet connection', 'Подключение к интернету')}
      </div>

      <div className="grid sm:grid-cols-3 gap-3">
        <div>
          <label className="block text-[11px] font-medium text-gray-400 mb-1">
            {t('Connection type', 'Тип подключения')}
          </label>
          <select
            value={values.mode}
            onChange={(e) => onChange('wan_mode', e.target.value)}
            className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
          >
            <option value="dhcp">{t('Automatic (DHCP)', 'Автоматически (DHCP)')}</option>
            <option value="static">{t('Static address', 'Статический адрес')}</option>
            <option value="pppoe">{t('PPPoE (login required)', 'PPPoE (с логином)')}</option>
          </select>
        </div>
        <div>
          <label className="block text-[11px] font-medium text-gray-400 mb-1">
            {t('VLAN tag', 'VLAN-тег')}
          </label>
          {input('wan_vlan_id', values.vlanId, t('0 = none', '0 = нет'))}
        </div>
        <div>
          <label className="block text-[11px] font-medium text-gray-400 mb-1">
            {t('Clone MAC address', 'Подменить MAC')}
          </label>
          {input('wan_mac_clone', values.macClone, t('optional', 'необязательно'))}
        </div>
      </div>

      {values.mode === 'static' && (
        <div className="grid sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-[11px] font-medium text-gray-400 mb-1">
              {t('Address with prefix', 'Адрес с префиксом')}
            </label>
            {input('wan_static_address', values.address, '203.0.113.7/24')}
          </div>
          <div>
            <label className="block text-[11px] font-medium text-gray-400 mb-1">
              {t('Gateway', 'Шлюз')}
            </label>
            {input('wan_static_gateway', values.gateway, '203.0.113.1')}
          </div>
          <div>
            <label className="block text-[11px] font-medium text-gray-400 mb-1">
              {t('DNS servers', 'DNS-серверы')}
            </label>
            {input('wan_static_dns', values.dns, '1.1.1.1,8.8.8.8')}
          </div>
        </div>
      )}

      {values.mode === 'pppoe' && (
        <div className="grid sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-[11px] font-medium text-gray-400 mb-1">
              {t('PPPoE username', 'Логин PPPoE')}
            </label>
            {input('wan_pppoe_user', values.pppoeUser, t('issued by your ISP', 'выдан провайдером'), false)}
          </div>
          <div>
            <label className="block text-[11px] font-medium text-gray-400 mb-1">
              {t('PPPoE password', 'Пароль PPPoE')}
            </label>
            {input('wan_pppoe_password', values.pppoePassword, t('unchanged', 'без изменений'), false, 'password')}
          </div>
        </div>
      )}

      {(values.mode === 'pppoe' || Number(values.vlanId) > 0) && (
        <div className="flex items-start gap-2 rounded-md border border-gray-800 bg-gray-950/40 p-2 text-[11px] text-gray-500">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          {t(
            `Traffic will leave on ${effective} rather than ${wan} — NAT and the firewall follow it automatically.`,
            `Трафик будет уходить через ${effective}, а не через ${wan} — NAT и файрвол переключатся туда автоматически.`,
          )}
        </div>
      )}
    </div>
  )
}
