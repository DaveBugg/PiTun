import { useState, useRef, useCallback } from 'react'
import { BookOpen, ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import { useAppStore } from '@/store'

type Lang = 'en' | 'ru'

/* ------------------------------------------------------------------ */
/*  Section component                                                  */
/* ------------------------------------------------------------------ */

function Section({
  id,
  title,
  children,
  open,
  onToggle,
}: {
  id: string
  title: string
  children: React.ReactNode
  open: boolean
  onToggle: () => void
}) {
  return (
    <div id={id} className="rounded-xl border border-gray-800 bg-gray-900">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between p-4 text-left"
      >
        <h3 className="text-sm font-semibold text-gray-100">{title}</h3>
        <ChevronDown
          className={clsx(
            'h-4 w-4 text-gray-500 transition-transform',
            open && 'rotate-180',
          )}
        />
      </button>
      {open && (
        <div className="px-4 pb-4 text-sm text-gray-300 leading-relaxed space-y-3">
          {children}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Reusable mini-components                                           */
/* ------------------------------------------------------------------ */

function Code({ children }: { children: React.ReactNode }) {
  return (
    <pre className="rounded-lg bg-gray-950 border border-gray-800 p-3 text-xs font-mono text-gray-400 overflow-x-auto">
      {children}
    </pre>
  )
}

function P({ children }: { children: React.ReactNode }) {
  return <p>{children}</p>
}

function B({ children }: { children: React.ReactNode }) {
  return <strong className="text-gray-100 font-medium">{children}</strong>
}

function Ul({ children }: { children: React.ReactNode }) {
  return <ul className="list-disc list-inside space-y-1">{children}</ul>
}

/* ------------------------------------------------------------------ */
/*  Section definitions                                                */
/* ------------------------------------------------------------------ */

interface SectionDef {
  id: string
  title: Record<Lang, string>
  content: Record<Lang, React.ReactNode>
}

const SECTIONS: SectionDef[] = [
  /* 1. Getting Started */
  {
    id: 'getting-started',
    title: { en: 'Getting Started', ru: 'Начало работы' },
    content: {
      en: (
        <>
          <P>
            <B>PiTun</B> is a self-hosted transparent proxy manager for Raspberry Pi 4/5.
            It sits on your LAN alongside the router, intercepts traffic from devices that set
            their gateway to the RPi, and routes it through VPN nodes (xray-core) based on
            configurable rules.
          </P>
          <Code>{`Devices (gateway=192.168.1.100)
  |
RPi4 (192.168.1.100)
  |
nftables TPROXY -> xray-core -> routing rules
  |                               |
  |- geoip:ru, bypass -> direct -> router -> internet
  |- geosite:ads      -> block
  '- everything else  -> VLESS/VMess/Trojan -> VPN server -> internet`}</Code>
          <Ul>
            <li>No client-side configuration needed — devices just change their gateway IP</li>
            <li>IoT, phones, consoles, PCs — everything works transparently</li>
            <li>Three simultaneous proxy endpoints: TPROXY, SOCKS5, HTTP</li>
            <li>Default credentials: <code className="text-gray-200">admin / password</code> — change after first login</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>
            <B>PiTun</B> — self-hosted менеджер прозрачного прокси для Raspberry Pi 4/5.
            Устанавливается в локальную сеть рядом с роутером, перехватывает трафик устройств,
            у которых шлюз указан на RPi, и маршрутизирует его через VPN-ноды (xray-core)
            по настраиваемым правилам.
          </P>
          <Code>{`Устройства (шлюз=192.168.1.100)
  |
RPi4 (192.168.1.100)
  |
nftables TPROXY -> xray-core -> правила маршрутизации
  |                               |
  |- geoip:ru, bypass -> напрямую -> роутер -> интернет
  |- geosite:ads      -> блок
  '- всё остальное    -> VLESS/VMess/Trojan -> VPN сервер -> интернет`}</Code>
          <Ul>
            <li>Не нужно настраивать клиенты — устройства просто меняют шлюз</li>
            <li>IoT, телефоны, консоли, ПК — всё работает прозрачно</li>
            <li>Три одновременных прокси-эндпоинта: TPROXY, SOCKS5, HTTP</li>
            <li>Логин по умолчанию: <code className="text-gray-200">admin / password</code> — смените после первого входа</li>
          </Ul>
        </>
      ),
    },
  },

  /* 2. Network Modes */
  {
    id: 'network-modes',
    title: { en: 'Network Modes', ru: 'Сетевые режимы' },
    content: {
      en: (
        <>
          <P>PiTun supports three inbound (network) modes for intercepting traffic:</P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Mode</th>
                <th className="py-2 pr-4">How it works</th>
                <th className="py-2">When to use</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">TPROXY</td><td className="py-2 pr-4">nftables + dokodemo-door. Kernel-level transparent proxy.</td><td className="py-2">Default, recommended. Devices set gateway=RPi.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">TUN</td><td className="py-2 pr-4">Virtual tun0 interface. xray routes traffic internally.</td><td className="py-2">When TPROXY unavailable. Requires xray-core &ge; 1.8.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Both</td><td className="py-2 pr-4">TPROXY + TUN simultaneously.</td><td className="py-2">Rarely needed, specific compatibility scenarios.</td></tr>
            </tbody>
          </table>
          <P>Additionally, two explicit proxy inbounds run on the LAN:</P>
          <Ul>
            <li><B>SOCKS5 :1080</B> — configure in browser/app proxy settings, host = RPi IP</li>
            <li><B>HTTP :8080</B> — for apps that only support HTTP proxy</li>
          </Ul>
          <P>All three inbounds (TPROXY/TUN + SOCKS5 + HTTP) share the same outbound nodes and routing rules.</P>
        </>
      ),
      ru: (
        <>
          <P>PiTun поддерживает три входящих (сетевых) режима перехвата трафика:</P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Режим</th>
                <th className="py-2 pr-4">Как работает</th>
                <th className="py-2">Когда использовать</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">TPROXY</td><td className="py-2 pr-4">nftables + dokodemo-door. Прозрачный прокси на уровне ядра.</td><td className="py-2">По умолчанию. Устройства ставят шлюз=RPi.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">TUN</td><td className="py-2 pr-4">Виртуальный интерфейс tun0. xray маршрутизирует трафик.</td><td className="py-2">Когда TPROXY недоступен. Требуется xray &ge; 1.8.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Both</td><td className="py-2 pr-4">TPROXY + TUN одновременно.</td><td className="py-2">Редко нужно, для специфических сценариев.</td></tr>
            </tbody>
          </table>
          <P>Дополнительно на LAN работают два явных прокси:</P>
          <Ul>
            <li><B>SOCKS5 :1080</B> — настройте в браузере/приложении, хост = IP RPi</li>
            <li><B>HTTP :8080</B> — для приложений без поддержки SOCKS5</li>
          </Ul>
          <P>Все три входа (TPROXY/TUN + SOCKS5 + HTTP) используют общие исходящие ноды и правила маршрутизации.</P>
        </>
      ),
    },
  },

  /* 3. Proxy Modes */
  {
    id: 'proxy-modes',
    title: { en: 'Proxy Modes', ru: 'Режимы прокси' },
    content: {
      en: (
        <>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Mode</th>
                <th className="py-2 pr-4">Behavior</th>
                <th className="py-2">Use case</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">Global</td><td className="py-2 pr-4">All traffic goes through the active VPN node</td><td className="py-2">Full VPN, privacy, all traffic encrypted</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Rules</td><td className="py-2 pr-4">Traffic routed based on configured rules (domain, IP, geoip, etc.)</td><td className="py-2">Selective routing — bypass local, proxy blocked sites</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Bypass</td><td className="py-2 pr-4">All traffic goes direct, proxy inactive</td><td className="py-2">Temporarily disable proxy without stopping xray</td></tr>
            </tbody>
          </table>
          <P>Switch modes from the Dashboard. The change takes effect immediately (xray config is regenerated and reloaded).</P>
        </>
      ),
      ru: (
        <>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Режим</th>
                <th className="py-2 pr-4">Поведение</th>
                <th className="py-2">Когда</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">Global</td><td className="py-2 pr-4">Весь трафик через активную VPN-ноду</td><td className="py-2">Полный VPN, приватность</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Rules</td><td className="py-2 pr-4">Трафик маршрутизируется по правилам (домен, IP, geoip и др.)</td><td className="py-2">Селективная маршрутизация</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Bypass</td><td className="py-2 pr-4">Весь трафик напрямую, прокси неактивен</td><td className="py-2">Временное отключение без остановки xray</td></tr>
            </tbody>
          </table>
          <P>Переключение на Dashboard. Изменение применяется мгновенно (конфиг xray пересоздаётся и перезагружается).</P>
        </>
      ),
    },
  },

  /* 4. Protocols */
  {
    id: 'protocols',
    title: { en: 'Protocols', ru: 'Протоколы' },
    content: {
      en: (
        <>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Protocol</th>
                <th className="py-2 pr-4">Transports</th>
                <th className="py-2">TLS</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">VLESS</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">none, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">VMess</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">none, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Trojan</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">none, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Shadowsocks</td><td className="py-2 pr-4">TCP</td><td className="py-2">-</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">WireGuard</td><td className="py-2 pr-4">native</td><td className="py-2">native</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">SOCKS5</td><td className="py-2 pr-4">TCP</td><td className="py-2">-</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Hysteria2</td><td className="py-2 pr-4">QUIC</td><td className="py-2">native</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">NaiveProxy</td><td className="py-2 pr-4">HTTPS (Caddy + forwardproxy)</td><td className="py-2">native (sidecar)</td></tr>
            </tbody>
          </table>
          <P>URI import formats: <code className="text-gray-200">vless://</code> <code className="text-gray-200">vmess://</code> <code className="text-gray-200">trojan://</code> <code className="text-gray-200">ss://</code> <code className="text-gray-200">wg://</code> <code className="text-gray-200">socks5://</code> <code className="text-gray-200">hy2://</code> <code className="text-gray-200">naive+https://</code></P>
          <P>Clash YAML import is also supported.</P>
        </>
      ),
      ru: (
        <>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Протокол</th>
                <th className="py-2 pr-4">Транспорт</th>
                <th className="py-2">TLS</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">VLESS</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">нет, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">VMess</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">нет, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Trojan</td><td className="py-2 pr-4">TCP, WS, gRPC, H2, XHTTP, HTTPUpgrade, KCP, QUIC</td><td className="py-2">нет, TLS, Reality</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Shadowsocks</td><td className="py-2 pr-4">TCP</td><td className="py-2">-</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">WireGuard</td><td className="py-2 pr-4">native</td><td className="py-2">native</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">SOCKS5</td><td className="py-2 pr-4">TCP</td><td className="py-2">-</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Hysteria2</td><td className="py-2 pr-4">QUIC</td><td className="py-2">native</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">NaiveProxy</td><td className="py-2 pr-4">HTTPS (Caddy + forwardproxy)</td><td className="py-2">native (sidecar)</td></tr>
            </tbody>
          </table>
          <P>Форматы URI-импорта: <code className="text-gray-200">vless://</code> <code className="text-gray-200">vmess://</code> <code className="text-gray-200">trojan://</code> <code className="text-gray-200">ss://</code> <code className="text-gray-200">wg://</code> <code className="text-gray-200">socks5://</code> <code className="text-gray-200">hy2://</code> <code className="text-gray-200">naive+https://</code></P>
          <P>Также поддерживается импорт из Clash YAML.</P>
        </>
      ),
    },
  },

  /* 4b. NaiveProxy */
  {
    id: 'naiveproxy',
    title: { en: 'NaiveProxy (sidecar)', ru: 'NaiveProxy (sidecar)' },
    content: {
      en: (
        <>
          <P>
            <B>NaiveProxy</B> (by klzgrad) is an HTTPS forward-proxy client that masquerades its traffic as normal Chrome-to-Caddy HTTPS. This makes it highly resistant to DPI — the traffic is literally the same handshake, TLS fingerprint, and HTTP/2 behavior as Chrome.
          </P>
          <P>
            Unlike other protocols, NaiveProxy is <B>not built into xray-core</B>. PiTun runs it as a <B>Docker sidecar</B> — one small container per naive node, bound to <code className="text-gray-200">127.0.0.1:&lt;internal_port&gt;</code>. xray routes outbound traffic through it as a local SOCKS5 outbound.
          </P>
          <P><B>How it works:</B></P>
          <Ul>
            <li>You add a naive node → backend allocates a free loopback port (20800–20899)</li>
            <li>A container <code className="text-gray-200">pitun-naive-&lt;id&gt;</code> starts with <code className="text-gray-200">network_mode: host</code> (loopback only)</li>
            <li>xray outbound: <code className="text-gray-200">socks → 127.0.0.1:&lt;port&gt;</code> → naive sidecar → HTTPS to your server</li>
            <li>Sidecar auto-restarts on node edit; sync happens on backend startup</li>
          </Ul>
          <P><B>Server side:</B> you need a Caddy server with the <code className="text-gray-200">forwardproxy</code> plugin and a real TLS certificate. A helper script <code className="text-gray-200">scripts/setup-naive-server.sh</code> is provided for VPS setup.</P>
          <P><B>URI format:</B></P>
          <Code>naive+https://user:pass@example.com:443/?padding=1#MyNaive</Code>
          <P>
            <B>Requirements:</B> Address <B>must be a real domain</B> (not an IP) with a valid TLS certificate — otherwise the disguise fails and the connection is easily fingerprinted. Padding (HTTP/2 frame padding) is enabled by default and recommended.
          </P>
          <P><B>Security hardening of the sidecar:</B></P>
          <Ul>
            <li>read-only filesystem, all capabilities dropped, no-new-privileges</li>
            <li>64 MB memory limit, JSON log rotation (10 MB × 3)</li>
            <li>Docker API access via tecnativa/docker-socket-proxy (restricted to containers/images/networks only, bound to 127.0.0.1:2375)</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>
            <B>NaiveProxy</B> (автор — klzgrad) — это HTTPS forward-proxy клиент, который маскирует свой трафик под обычное HTTPS-соединение Chrome → Caddy. Это делает его крайне устойчивым к DPI: трафик имеет тот же TLS-handshake, fingerprint и поведение HTTP/2, что и у Chrome.
          </P>
          <P>
            В отличие от остальных протоколов, NaiveProxy <B>не встроен в xray-core</B>. PiTun запускает его как <B>Docker sidecar</B> — по одному небольшому контейнеру на naive-нод, слушающему на <code className="text-gray-200">127.0.0.1:&lt;internal_port&gt;</code>. xray маршрутизирует исходящий трафик через него как обычный SOCKS5-outbound.
          </P>
          <P><B>Как это работает:</B></P>
          <Ul>
            <li>Добавляете naive-нод → бэкенд выделяет свободный loopback-порт (20800–20899)</li>
            <li>Запускается контейнер <code className="text-gray-200">pitun-naive-&lt;id&gt;</code> с <code className="text-gray-200">network_mode: host</code> (только loopback)</li>
            <li>xray outbound: <code className="text-gray-200">socks → 127.0.0.1:&lt;port&gt;</code> → naive sidecar → HTTPS до сервера</li>
            <li>Sidecar автоматически перезапускается при редактировании нода; синхронизация — на старте бэкенда</li>
          </Ul>
          <P><B>На сервере:</B> нужен Caddy с плагином <code className="text-gray-200">forwardproxy</code> и валидным TLS-сертификатом. Скрипт <code className="text-gray-200">scripts/setup-naive-server.sh</code> автоматизирует развёртывание на VPS.</P>
          <P><B>Формат URI:</B></P>
          <Code>naive+https://user:pass@example.com:443/?padding=1#MyNaive</Code>
          <P>
            <B>Требования:</B> адрес <B>обязательно реальный домен</B> (не IP) с валидным TLS-сертификатом — иначе маскировка не работает и соединение легко фингерпринтится. Padding (HTTP/2 frame padding) включён по умолчанию и рекомендуется.
          </P>
          <P><B>Усиление безопасности sidecar:</B></P>
          <Ul>
            <li>read-only файловая система, сброс всех capabilities, no-new-privileges</li>
            <li>лимит памяти 64 МБ, ротация JSON-логов (10 МБ × 3)</li>
            <li>доступ к Docker API через tecnativa/docker-socket-proxy (только containers/images/networks, привязан к 127.0.0.1:2375)</li>
          </Ul>
        </>
      ),
    },
  },

  /* 5. Routing Rules */
  {
    id: 'routing-rules',
    title: { en: 'Routing Rules', ru: 'Правила маршрутизации' },
    content: {
      en: (
        <>
          <P>Rules determine how traffic is routed in <B>Rules</B> mode. They are evaluated top to bottom by priority.</P>
          <P><B>Rule types:</B></P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Type</th>
                <th className="py-2">Example</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">mac</td><td className="py-2"><code className="text-gray-400">AA:BB:CC:DD:EE:FF</code> — match by device MAC address</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">src_ip</td><td className="py-2"><code className="text-gray-400">192.168.1.50/32</code> — match by source IP/CIDR</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">dst_ip</td><td className="py-2"><code className="text-gray-400">10.0.0.0/8</code> — match by destination IP/CIDR</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">domain</td><td className="py-2"><code className="text-gray-400">google.com</code>, <code className="text-gray-400">geosite:category-ads</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">port</td><td className="py-2"><code className="text-gray-400">443</code>, <code className="text-gray-400">80,443</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">protocol</td><td className="py-2"><code className="text-gray-400">bittorrent</code>, <code className="text-gray-400">http</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">geoip</td><td className="py-2"><code className="text-gray-400">geoip:ru</code>, <code className="text-gray-400">geoip:cn</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">geosite</td><td className="py-2"><code className="text-gray-400">geosite:google</code>, <code className="text-gray-400">geosite:netflix</code></td></tr>
            </tbody>
          </table>
          <P><B>Actions:</B></P>
          <Ul>
            <li><B>proxy</B> — send through active VPN node</li>
            <li><B>direct</B> — connect directly (bypass VPN)</li>
            <li><B>block</B> — drop the connection</li>
            <li><B>node:&lt;id&gt;</B> — route through a specific node</li>
            <li><B>balancer:&lt;id&gt;</B> — route through a balancer group</li>
          </Ul>
          <P><B>Features:</B> drag-and-drop reorder, bulk import (paste domains/IPs one per line), Quick Add presets, full v2ray JSON import/export.</P>
          <P><B>Quick Add presets:</B></P>
          <Ul>
            <li><B>Bypass RU / CN</B> — adds <code className="text-gray-400">geoip:ru</code> / <code className="text-gray-400">geoip:cn</code> + <code className="text-gray-400">geosite:ru</code> / <code className="text-gray-400">geosite:cn</code> &rarr; direct, so local sites stay on the home ISP.</li>
            <li><B>Block ads</B> — adds <code className="text-gray-400">geosite:category-ads-all</code> &rarr; block.</li>
            <li><B>Proxy streaming</B> — comprehensive 50-entry list (Netflix, Disney+, HBO, Hulu, Spotify, YouTube Premium, Twitch, Steam, Epic Games, BBC iPlayer, ChatGPT/OpenAI, Anthropic Claude, etc.) routed through the active proxy.</li>
          </Ul>
          <P><B>Domain entry shorthand:</B> the backend auto-prefixes bare entries (e.g. <code className="text-gray-400">netflix.com</code>) with <code className="text-gray-400">domain:</code> on save, so you don't have to type the prefix every time.</P>
          <P><B>v2ray JSON Import/Export:</B> Routing page exposes Export/Import buttons that round-trip the rule set as v2ray-style routing JSON — useful for backups and for migrating a curated rule set between PiTun instances.</P>
        </>
      ),
      ru: (
        <>
          <P>Правила определяют маршрутизацию трафика в режиме <B>Rules</B>. Проверяются сверху вниз по приоритету.</P>
          <P><B>Типы правил:</B></P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Тип</th>
                <th className="py-2">Пример</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">mac</td><td className="py-2"><code className="text-gray-400">AA:BB:CC:DD:EE:FF</code> — по MAC-адресу устройства</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">src_ip</td><td className="py-2"><code className="text-gray-400">192.168.1.50/32</code> — по IP-источнику</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">dst_ip</td><td className="py-2"><code className="text-gray-400">10.0.0.0/8</code> — по IP-назначению</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">domain</td><td className="py-2"><code className="text-gray-400">google.com</code>, <code className="text-gray-400">geosite:category-ads</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">port</td><td className="py-2"><code className="text-gray-400">443</code>, <code className="text-gray-400">80,443</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">protocol</td><td className="py-2"><code className="text-gray-400">bittorrent</code>, <code className="text-gray-400">http</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">geoip</td><td className="py-2"><code className="text-gray-400">geoip:ru</code>, <code className="text-gray-400">geoip:cn</code></td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">geosite</td><td className="py-2"><code className="text-gray-400">geosite:google</code>, <code className="text-gray-400">geosite:netflix</code></td></tr>
            </tbody>
          </table>
          <P><B>Действия:</B></P>
          <Ul>
            <li><B>proxy</B> — через активную VPN-ноду</li>
            <li><B>direct</B> — напрямую (мимо VPN)</li>
            <li><B>block</B> — сбросить соединение</li>
            <li><B>node:&lt;id&gt;</B> — через конкретную ноду</li>
            <li><B>balancer:&lt;id&gt;</B> — через группу балансировки</li>
          </Ul>
          <P><B>Возможности:</B> drag-and-drop сортировка, массовый импорт (домены/IP построчно), пресеты Quick Add, полный импорт/экспорт v2ray JSON.</P>
          <P><B>Пресеты Quick Add:</B></P>
          <Ul>
            <li><B>Bypass RU / CN</B> — добавляет <code className="text-gray-400">geoip:ru</code> / <code className="text-gray-400">geoip:cn</code> + <code className="text-gray-400">geosite:ru</code> / <code className="text-gray-400">geosite:cn</code> &rarr; direct, чтобы локальные сайты шли через домашнего провайдера.</li>
            <li><B>Block ads</B> — добавляет <code className="text-gray-400">geosite:category-ads-all</code> &rarr; block.</li>
            <li><B>Proxy streaming</B> — расширенный список из 50 записей (Netflix, Disney+, HBO, Hulu, Spotify, YouTube Premium, Twitch, Steam, Epic Games, BBC iPlayer, ChatGPT/OpenAI, Anthropic Claude и т.д.) — всё через активный прокси.</li>
          </Ul>
          <P><B>Сокращённый ввод доменов:</B> бэкенд автоматически добавляет префикс <code className="text-gray-400">domain:</code> к «голым» записям (напр. <code className="text-gray-400">netflix.com</code>) при сохранении, чтобы не писать префикс каждый раз.</P>
          <P><B>Импорт/Экспорт v2ray JSON:</B> на странице Routing есть кнопки Export/Import — туда-обратно гонят набор правил в формате routing JSON (v2ray). Удобно для бэкапа и переноса между инстансами PiTun.</P>
        </>
      ),
    },
  },

  /* 5b. Routing Sets (per-device-group rules) */
  {
    id: 'routing-sets',
    title: { en: 'Routing Sets (per-device groups)', ru: 'Наборы правил (группы устройств)' },
    content: {
      en: (
        <>
          <P>
            By default every device on your LAN shares the <B>same</B> routing rules.
            A <B>Routing Set</B> lets you apply a different list of rules to a selected
            group of devices — a "Kids" set that blocks gambling + forces a parental-
            control node, a "Work" set that routes corporate domains direct, etc.
          </P>
          <P><B>How it works:</B></P>
          <Ul>
            <li>Create a named set on the Routing page (the <code className="text-gray-400">+ New set</code> button in the sub-tab bar).</li>
            <li>Add rules while that set's tab is active — they apply <B>only</B> to devices in the set.</li>
            <li>Assign devices on the Devices page: per-row <B>Set</B> dropdown, or select several + <B>Move to set</B> bulk action.</li>
            <li>Rules in the <B>Global</B> tab (or with no set) still apply to <B>every</B> device, including set members — per-set rules are matched first, then traffic falls through to globals.</li>
          </Ul>
          <P><B>Under the hood:</B> each set gets a dedicated loopback TPROXY port (65500–65535).
            nftables matches the device MAC in PREROUTING and redirects its traffic into the set's
            xray inbound; xray's router then matches the <code className="text-gray-400">inboundTag</code> to
            decide which rules apply. This is <B>DHCP-resistant</B> — membership is tied to the MAC, not
            the IP, so it survives lease changes without any reconfiguration.</P>
          <Ul>
            <li><B>Limit:</B> 36 sets max (one per port in the reserved range). The "+ New set" button disables at the limit.</li>
            <li><B>One device → one set</B> in this version. A device's traffic is evaluated against its set's rules + globals.</li>
            <li><B>Per-set node:</B> use a <code className="text-gray-400">node:&lt;id&gt;</code> action inside the set's rules to force that group through a specific VPN node.</li>
            <li><B>Deleting a set</B> moves its devices and rules back to Global (their set assignment becomes null) — reversible.</li>
            <li><B>Excluded devices</B> (Devices → policy "exclude") bypass TPROXY entirely, so their set assignment has no effect — the Set dropdown is greyed out for them.</li>
          </Ul>
          <P><B>Tip:</B> for the most reliable matching, give set-member devices a static DHCP
            reservation on your router. PiTun handles IP changes gracefully (MAC-based), but a fixed
            IP keeps diagnostics and logs easy to read.</P>
        </>
      ),
      ru: (
        <>
          <P>
            По умолчанию все устройства в LAN используют <B>одни и те же</B> правила
            маршрутизации. <B>Набор правил (Routing Set)</B> позволяет применить отдельный
            список правил к выбранной группе устройств — набор «Дети» блокирует азартные
            игры и гонит трафик через родительскую ноду, набор «Работа» отправляет
            корпоративные домены напрямую, и т.д.
          </P>
          <P><B>Как это работает:</B></P>
          <Ul>
            <li>Создай именованный набор на странице Routing (кнопка <code className="text-gray-400">+ Новый набор</code> в строке вкладок).</li>
            <li>Добавляй правила, когда активна вкладка набора — они применятся <B>только</B> к устройствам этого набора.</li>
            <li>Назначай устройства на странице Devices: выпадающий список <B>Набор</B> в строке, или выдели несколько + действие <B>Переместить в набор</B>.</li>
            <li>Правила во вкладке <B>Global</B> (или без набора) по-прежнему применяются ко <B>всем</B> устройствам, включая участников наборов — правила набора проверяются первыми, затем трафик проваливается на глобальные.</li>
          </Ul>
          <P><B>Под капотом:</B> каждый набор получает выделенный loopback-порт TPROXY (65500–65535).
            nftables сопоставляет MAC устройства в PREROUTING и перенаправляет его трафик в xray-inbound
            набора; роутер xray затем матчит <code className="text-gray-400">inboundTag</code>, чтобы решить какие
            правила применить. Это <B>устойчиво к DHCP</B> — членство привязано к MAC, а не к IP, поэтому
            смена аренды IP не требует переконфигурации.</P>
          <Ul>
            <li><B>Лимит:</B> максимум 36 наборов (по одному на порт в зарезервированном диапазоне). Кнопка «+ Новый набор» блокируется на лимите.</li>
            <li><B>Одно устройство → один набор</B> в этой версии. Трафик устройства проверяется против правил его набора + глобальных.</li>
            <li><B>Нода для набора:</B> используй действие <code className="text-gray-400">node:&lt;id&gt;</code> в правилах набора, чтобы гнать группу через конкретную VPN-ноду.</li>
            <li><B>Удаление набора</B> возвращает его устройства и правила в Global (их привязка к набору становится null) — обратимо.</li>
            <li><B>Исключённые устройства</B> (Devices → политика «exclude») полностью минуют TPROXY, поэтому привязка к набору на них не влияет — выпадающий список Набор для них заблокирован.</li>
          </Ul>
          <P><B>Совет:</B> для самого надёжного срабатывания дай устройствам-участникам набора
            статическую DHCP-резервацию на роутере. PiTun корректно обрабатывает смену IP (по MAC),
            но фиксированный IP упрощает чтение диагностики и логов.</P>
        </>
      ),
    },
  },

  /* 6. DNS Management */
  {
    id: 'dns',
    title: { en: 'DNS Management', ru: 'Управление DNS' },
    content: {
      en: (
        <>
          <P>PiTun manages DNS through xray's built-in DNS module. DNS queries from LAN devices flow through xray, which resolves them using configurable servers and per-domain rules.</P>
          <P><B>Server types:</B></P>
          <Ul>
            <li><B>Plain</B> — standard UDP DNS (e.g. <code className="text-gray-400">8.8.8.8</code>)</li>
            <li><B>DoH</B> — DNS over HTTPS (e.g. <code className="text-gray-400">https://dns.google/dns-query</code>)</li>
            <li><B>DoT</B> — labelled "DNS-over-TCP (not encrypted)" in the UI. xray-core doesn't support native DoT (see <a href="https://github.com/XTLS/Xray-core/issues/786" className="text-brand-400 underline">issue #786</a>), so this mode falls back to plaintext DNS-over-TCP on port 53 (<code className="text-gray-400">tcp://host:53</code>). Use DoH if you need encryption.</li>
          </Ul>
          <P><B>Per-domain DNS rules:</B> The DNS Rules table lets you assign a specific DNS server to specific domains. The domain match field accepts comma-separated entries using xray domain syntax:</P>
          <Ul>
            <li><code className="text-gray-400">domain:youtube.com</code> — youtube.com and all subdomains</li>
            <li><code className="text-gray-400">domain:ru</code> — the .ru TLD and all subdomains (note: <code className="text-gray-400">domain:.ru</code> with a leading dot is invalid in xray)</li>
            <li><code className="text-gray-400">geosite:category-ads</code> — a geo category</li>
          </Ul>
          <P><B>Disable DNS fallback (recommended ON):</B> When ON, each DNS server is used strictly for its configured domains — rule-specific servers are never queried for unmatched domains. When OFF, all servers are queried simultaneously for every domain, so rule-specific servers (e.g. 94.140.14.14 for a YouTube rule) will appear in the query log for unrelated domains too.</P>
          <P><B>Bypass CN/RU DNS:</B> Routes .cn/.ru TLD domains through the plain Primary/Secondary servers, bypassing DoH/DoT, to reduce latency for local domains. Internally uses <code className="text-gray-400">domain:cn</code> / <code className="text-gray-400">domain:ru</code> matching.</P>
          <P><B>FakeDNS:</B> Returns synthetic IPs to capture the real domain name before routing. Required for accurate domain-based routing when traffic arrives as raw IP addresses.</P>
          <P><B>DNS sniffing:</B> Extracts the real domain from TLS SNI / HTTP Host headers to improve routing accuracy without FakeDNS.</P>
          <P><B>DNS Query Log:</B> Found on the DNS page (not the Logs page). Records every DNS query resolved by xray: domain, server used, resolved IPs, latency, and whether it was a cache hit. Filterable by domain or server. Enable with the toggle on the DNS page — takes effect after xray restarts.</P>
          <P><B>DNS Test Tool:</B> Also on the DNS page — enter any domain to test resolution. "Via xray" mode shows which DNS server xray actually used (respecting your rules), compared to direct resolution from the RPi itself.</P>
        </>
      ),
      ru: (
        <>
          <P>PiTun управляет DNS через встроенный DNS-модуль xray. DNS-запросы устройств в LAN идут через xray, который резолвит их через настраиваемые серверы и правила per-domain.</P>
          <P><B>Типы серверов:</B></P>
          <Ul>
            <li><B>Plain</B> — обычный UDP DNS (напр. <code className="text-gray-400">8.8.8.8</code>)</li>
            <li><B>DoH</B> — DNS over HTTPS (напр. <code className="text-gray-400">https://dns.google/dns-query</code>)</li>
            <li><B>DoT</B> — в UI помечен как «DNS-over-TCP (not encrypted)». xray-core не поддерживает нативный DoT (см. <a href="https://github.com/XTLS/Xray-core/issues/786" className="text-brand-400 underline">issue #786</a>), поэтому режим падает до plaintext DNS-over-TCP на порту 53 (<code className="text-gray-400">tcp://host:53</code>). Для шифрования используй DoH.</li>
          </Ul>
          <P><B>Правила DNS per-domain:</B> В таблице DNS Rules можно назначить конкретный DNS-сервер для конкретных доменов. Поле domain match принимает значения через запятую в синтаксисе xray:</P>
          <Ul>
            <li><code className="text-gray-400">domain:youtube.com</code> — youtube.com и все поддомены</li>
            <li><code className="text-gray-400">domain:ru</code> — домены .ru и все поддомены (важно: <code className="text-gray-400">domain:.ru</code> с точкой — неверный синтаксис в xray)</li>
            <li><code className="text-gray-400">geosite:category-ads</code> — geo-категория</li>
          </Ul>
          <P><B>Отключить DNS fallback (рекомендуется включить):</B> При включении каждый DNS-сервер используется строго для своих доменов — rule-specific серверы не запрашиваются для остальных доменов. При выключении все серверы опрашиваются одновременно для каждого домена, и специфические серверы (напр. 94.140.14.14 для YouTube) будут появляться в логе для несвязанных доменов.</P>
          <P><B>Bypass CN/RU DNS:</B> Домены .cn/.ru резолвятся через Plain-серверы (Primary/Secondary), минуя DoH/DoT, для снижения задержки. Внутри используется синтаксис <code className="text-gray-400">domain:cn</code> / <code className="text-gray-400">domain:ru</code>.</P>
          <P><B>FakeDNS:</B> Возвращает синтетические IP для захвата реального доменного имени до маршрутизации. Необходим для точной маршрутизации по домену когда трафик поступает как «голые» IP-адреса.</P>
          <P><B>DNS sniffing:</B> Извлекает домен из TLS SNI / HTTP Host заголовков для улучшения точности маршрутизации без FakeDNS.</P>
          <P><B>DNS Query Log:</B> Находится на странице DNS (не Logs). Фиксирует каждый DNS-запрос через xray: домен, использованный сервер, полученные IP, задержку и кэш-хит. Фильтруется по домену или серверу. Включается переключателем на странице DNS — вступает в силу после перезапуска xray.</P>
          <P><B>Инструмент тестирования DNS:</B> Тоже на странице DNS — введите любой домен для проверки. Режим «Via xray» показывает какой DNS-сервер реально использовал xray (с учётом правил), в отличие от прямого резолва с RPi.</P>
        </>
      ),
    },
  },

  /* 6b. Host Network (gateway / DNS / self-loop) */
  {
    id: 'host-network',
    title: { en: 'Host Network (gateway / DNS)', ru: 'Host-сеть (gateway / DNS)' },
    content: {
      en: (
        <>
          <P><B>Settings &rarr; Network</B> changes the PiTun box's OWN gateway and DNS — the host's default route and <code className="text-gray-400">/etc/resolv.conf</code> — without SSHing in. This is distinct from the routing rules, which decide where <em>LAN clients'</em> traffic exits.</P>
          <P><B>Auto-backup + rollback.</B> Every apply snapshots the current config first; a one-click rollback (last 10 kept) restores both the persistent manager config (so it survives a reboot) and the live route via <code className="text-gray-400">ip route replace</code>.</P>
          <P><B>Self-loop guard.</B> A fresh box left on DHCP in "gateway mode" can receive its OWN IP as the default gateway — every off-LAN packet is handed back to itself and dies. The page flags this in red ("gateway is THIS PiTun's own IP — a routing self-loop") and refuses to apply a gateway equal to the box's own address. Set it to your ISP router (usually <code className="text-gray-400">192.168.x.1</code>); if the box was on DHCP, give it a static IP too.</P>
          <P><B>Double-hop warning.</B> If the upstream gateway is <em>another</em> PiTun (it matches PiTun's <code className="text-gray-400">/health</code> fingerprint), the page warns — your traffic would go you &rarr; this PiTun &rarr; other PiTun &rarr; router.</P>
          <P><B>Probe before apply.</B> A candidate gateway is pinged (ICMP, then ARP) and must be on the host's subnet; the box's own address is rejected outright.</P>
          <P><B>Installer note.</B> On install/deploy PiTun removes the native <code className="text-gray-400">systemd-resolved</code> and makes <code className="text-gray-400">/etc/resolv.conf</code> a static file it owns, so the box's own name resolution stays reliable (it frees port 53; avahi is disabled to free port 5353 for xray's DNS).</P>
        </>
      ),
      ru: (
        <>
          <P><B>Settings &rarr; Network</B> меняет СОБСТВЕННЫЙ gateway и DNS бокса PiTun — default route хоста и <code className="text-gray-400">/etc/resolv.conf</code> — без захода по SSH. Это отдельно от правил маршрутизации, которые решают, куда выходит трафик <em>LAN-клиентов</em>.</P>
          <P><B>Авто-бэкап + откат.</B> Каждый apply сначала снимает снэпшот текущей конфигурации; откат в один клик (хранятся последние 10) восстанавливает и постоянный конфиг менеджера (переживает ребут), и живой маршрут через <code className="text-gray-400">ip route replace</code>.</P>
          <P><B>Защита от self-loop.</B> Свежий бокс, оставленный на DHCP в «gateway mode», может получить СВОЙ IP как default gateway — каждый пакет за пределы LAN возвращается ему самому и умирает. Страница помечает это красным («gateway is THIS PiTun's own IP — routing self-loop») и отказывается применять gateway, равный собственному адресу бокса. Укажи ISP-роутер (обычно <code className="text-gray-400">192.168.x.1</code>); если бокс был на DHCP, дай ему и статический IP.</P>
          <P><B>Предупреждение о double-hop.</B> Если upstream-gateway — это <em>другой</em> PiTun (совпадает fingerprint <code className="text-gray-400">/health</code>), страница предупреждает — трафик пошёл бы ты &rarr; этот PiTun &rarr; другой PiTun &rarr; роутер.</P>
          <P><B>Проба перед apply.</B> Кандидат-gateway пингуется (ICMP, затем ARP) и должен быть в подсети хоста; собственный адрес бокса отклоняется сразу.</P>
          <P><B>Про установщик.</B> При установке/деплое PiTun снимает нативный <code className="text-gray-400">systemd-resolved</code> и делает <code className="text-gray-400">/etc/resolv.conf</code> статическим файлом под своим контролем, чтобы резолвинг имён самим боксом был стабильным (освобождает порт 53; avahi отключается ради порта 5353 для DNS xray).</P>
        </>
      ),
    },
  },

  /* 7. Balancer Groups */
  {
    id: 'balancers',
    title: { en: 'Balancer Groups', ru: 'Группы балансировки' },
    content: {
      en: (
        <>
          <P>Balancers distribute traffic across multiple nodes using xray's built-in balancer.</P>
          <P><B>Strategies:</B></P>
          <Ul>
            <li><B>leastPing</B> — route to the node with lowest latency (measured by health checks)</li>
            <li><B>random</B> — randomly pick a node from the group</li>
          </Ul>
          <P><B>How to use:</B></P>
          <Ul>
            <li>Create a balancer group in the Balancers page — add nodes to it</li>
            <li>In routing rules, set action to <code className="text-gray-400">balancer:&lt;id&gt;</code></li>
            <li>Traffic matching that rule will be distributed across the group's nodes</li>
            <li>If a node in the group goes offline, traffic automatically routes to remaining nodes</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>Балансировщики распределяют трафик между несколькими нодами через встроенный балансировщик xray.</P>
          <P><B>Стратегии:</B></P>
          <Ul>
            <li><B>leastPing</B> — направлять на ноду с наименьшей задержкой</li>
            <li><B>random</B> — случайный выбор ноды из группы</li>
          </Ul>
          <P><B>Как использовать:</B></P>
          <Ul>
            <li>Создайте группу на странице Balancers — добавьте ноды</li>
            <li>В правилах маршрутизации укажите действие <code className="text-gray-400">balancer:&lt;id&gt;</code></li>
            <li>Трафик по этому правилу распределяется между нодами группы</li>
            <li>Если нода уходит в офлайн, трафик автоматически идёт на оставшиеся</li>
          </Ul>
        </>
      ),
    },
  },

  /* 8. Node Circles (Rotation) */
  {
    id: 'node-circles',
    title: { en: 'Node Circles (Rotation)', ru: 'Node Circles (Ротация нод)' },
    content: {
      en: (
        <>
          <P>Node Circles automatically rotate the active proxy node on a schedule — without restarting xray or dropping active connections.</P>
          <P><B>How it works:</B></P>
          <Ul>
            <li>Create a circle with a list of nodes and a rotation interval</li>
            <li>PiTun uses xray's gRPC API to add the new outbound and remove the old one</li>
            <li>Existing connections finish naturally — no disconnects</li>
            <li>If the gRPC API is unavailable, falls back to a full xray restart</li>
          </Ul>
          <P><B>Modes:</B></P>
          <Ul>
            <li><B>Sequential</B> — rotates nodes in order (1 &rarr; 2 &rarr; 3 &rarr; 1 &rarr; ...)</li>
            <li><B>Random</B> — picks a random node from the circle each time</li>
            <li><B>Best</B> — picks the fastest eligible candidate from real speed data (lowest latency first, then highest measured speed), after applying the quality filters below</li>
          </Ul>
          <P><B>Interval:</B> set min/max minutes. In sequential mode, rotates every <code className="text-gray-400">interval_min</code> minutes. In random mode, picks a random interval between min and max.</P>
          <P><B>Quality filters (best mode):</B> <code className="text-gray-400">max_latency_ms</code> drops candidates whose RTT is above a ceiling; <code className="text-gray-400">min_speed_mbps</code> drops any whose last speed reading is below a floor (a never-tested node gets the benefit of the doubt). These read the per-node speed history kept fresh by <B>Auto-checks</B> — see the <em>Speed Tests &amp; Node Health</em> section.</P>
          <P><B>Smart-skip:</B> a scheduled rotation won't move off a node that's already healthy and low-latency — no point swapping a good exit for a random one. A manual "rotate now" always rotates regardless.</P>
          <P><B>Pre-ping with retry:</B> Before switching to a candidate, PiTun probes it via TCP to <code className="text-gray-400">address:port</code> using SO_MARK=0xFF (so the probe bypasses the TPROXY layer). Each candidate gets <B>2 attempts</B> with a short delay between them — this absorbs transient SYN drops without getting stuck on a truly dead node. Disabled or removed nodes are skipped automatically. If every candidate fails after retries, the rotation aborts and the active node stays put.</P>
          <P><B>Failover &harr; Circle integration:</B> When the active node fails its health checks repeatedly AND it belongs to an enabled circle, the failover handler delegates recovery to the circle by triggering an immediate <code className="text-gray-400">rotate_circle()</code> instead of using the legacy fallback list. If the circle has no live siblings, PiTun falls through to the Tier-2 fallback list configured in the NodeCircles page (Auto-failover toggle).</P>
          <P><B>Auto-failover toggle (NodeCircles page):</B> Globally enables/disables the failover behavior. When ON you can also pick a <B>fallback nodes list</B> — used only when the failed node is NOT in any circle (or all siblings are dead). Leave the list empty if you only use circles.</P>
          <P><B>Concurrency safety:</B> Each circle has its own <code className="text-gray-400">asyncio.Lock</code> so the scheduler tick and a manual "rotate now" can never race. A 20-second hard deadline prevents pathological probe times from blocking API responses.</P>
          <P><B>Use cases:</B></P>
          <Ul>
            <li>Distribute load across multiple VPN servers</li>
            <li>Avoid IP-based blocking by rotating exit IPs</li>
            <li>Automatic failover with skip-dead-candidates behavior</li>
          </Ul>
          <P>You can also manually trigger rotation from the NodeCircles page with the rotate button.</P>
        </>
      ),
      ru: (
        <>
          <P>Node Circles автоматически ротируют активную прокси-ноду по расписанию — без перезапуска xray и без обрыва активных соединений.</P>
          <P><B>Как работает:</B></P>
          <Ul>
            <li>Создайте circle со списком нод и интервалом ротации</li>
            <li>PiTun использует gRPC API xray для добавления нового outbound и удаления старого</li>
            <li>Существующие соединения завершаются штатно — без обрывов</li>
            <li>Если gRPC API недоступен, происходит полный перезапуск xray</li>
          </Ul>
          <P><B>Режимы:</B></P>
          <Ul>
            <li><B>Sequential</B> — ротация по порядку (1 &rarr; 2 &rarr; 3 &rarr; 1 &rarr; ...)</li>
            <li><B>Random</B> — случайный выбор ноды из круга каждый раз</li>
            <li><B>Best</B> — выбирает самого быстрого подходящего кандидата по реальным данным скорости (сначала минимальный пинг, затем максимальная измеренная скорость), после применения фильтров качества ниже</li>
          </Ul>
          <P><B>Интервал:</B> задайте мин/макс минуты. В sequential режиме ротация каждые <code className="text-gray-400">interval_min</code> минут. В random режиме — случайный интервал между min и max.</P>
          <P><B>Фильтры качества (режим best):</B> <code className="text-gray-400">max_latency_ms</code> отсекает кандидатов с RTT выше потолка; <code className="text-gray-400">min_speed_mbps</code> отсекает тех, чей последний замер скорости ниже порога (не тестированная нода получает презумпцию невиновности). Они читают историю скорости по нодам, которую держат свежей <B>Авто-проверки</B> — см. раздел <em>Speed-тесты и здоровье нод</em>.</P>
          <P><B>Smart-skip:</B> плановая ротация не уйдёт со здоровой ноды с низким пингом — нет смысла менять хороший выход на случайный. Ручная «rotate now» крутит всегда.</P>
          <P><B>Pre-ping с повтором:</B> Перед переключением на кандидата PiTun проверяет TCP-доступность <code className="text-gray-400">address:port</code> с SO_MARK=0xFF (зонд минует TPROXY). На каждого кандидата даётся <B>2 попытки</B> с короткой паузой — это нивелирует случайные пропуски SYN, но не позволит зависнуть на реально мёртвой ноде. Отключённые или удалённые ноды пропускаются. Если все кандидаты падают — ротация отменяется, активная нода остаётся прежней.</P>
          <P><B>Связь Failover &harr; Circle:</B> Когда активная нода стабильно падает по health check И она входит в активный circle, обработчик failover делегирует восстановление кругу — запускается немедленный <code className="text-gray-400">rotate_circle()</code> вместо старого списка fallback. Если в круге нет живых соседей, PiTun переходит к Tier-2 fallback-списку со страницы NodeCircles (тогл Auto-failover).</P>
          <P><B>Тогл Auto-failover (страница NodeCircles):</B> Глобально включает/выключает поведение failover. При включении можно выбрать <B>список fallback-нод</B> — используется только когда упавшая нода НЕ входит ни в один круг (или все соседи мертвы). Если используете только круги — оставьте список пустым.</P>
          <P><B>Безопасность параллелизма:</B> У каждого круга свой <code className="text-gray-400">asyncio.Lock</code>, чтобы тик планировщика и ручной "rotate now" не пересекались. Жёсткий дедлайн 20с защищает от зависших проверок.</P>
          <P><B>Случаи использования:</B></P>
          <Ul>
            <li>Распределение нагрузки между VPN-серверами</li>
            <li>Избежание блокировки по IP через ротацию выходных IP</li>
            <li>Автоматическое переключение со skip-dead-candidates</li>
          </Ul>
          <P>Также можно вручную запустить ротацию на странице NodeCircles кнопкой rotate.</P>
        </>
      ),
    },
  },

  /* 8c. Speed Tests & Node Health */
  {
    id: 'speed-tests',
    title: { en: 'Speed Tests & Node Health', ru: 'Speed-тесты и здоровье нод' },
    content: {
      en: (
        <>
          <P>Every node carries a live speed reading and a reachability status, measured through the real tunnel — the same data the <em>best</em> NodeCircle mode filters on.</P>
          <P><B>Unified speed test.</B> One measurement path backs the per-node button, "Speed all", the live stream and the auto-check. It <B>gates on reachability first</B> — two 204 endpoints (Google, Cloudflare) with a retry — so a dead node fails in ~1s instead of grinding every download fallback. The number is the <B>average after a warm-up plus the peak</B> steady window; both are saved.</P>
          <P><B>Live streaming.</B> The per-node test streams Mbps as it runs (<code className="text-gray-400">cachefly · 45.2 Mbps</code>), survives navigating away and pagination (state lives in the query cache), and persists — a reading older than 6h is flagged so a stale number never reads as current.</P>
          <P><B>Reachability check.</B> One tap confirms the node actually carries traffic to the internet (204 through the live tunnel), separate from raw link speed.</P>
          <P><B>Auto-checks (background sweep).</B> <B>Nodes &rarr; Auto-checks</B> speed-tests a chosen scope — <B>all / a subscription / a group / specific nodes</B> — on an interval, so <code className="text-gray-400">best</code> / <code className="text-gray-400">min_speed</code> and the UI stay fresh without manual testing. Sequential (a speed test saturates the uplink), with a per-node staleness guard and per-node error isolation — one bad node never aborts the sweep. Newest nodes are checked first, and a manual run stamps the schedule so a manual and a scheduled sweep never collide.</P>
          <P><B>SNI / REALITY-dest scanner.</B> In the node form, probe a candidate host for TLS 1.3 + HTTP/2 (routed through the active node) before saving it as the REALITY masquerade target.</P>
          <P><B>Single-node URI export.</B> Copy a node's <code className="text-gray-400">vless://</code> (etc.) share link straight from its card.</P>
        </>
      ),
      ru: (
        <>
          <P>У каждой ноды есть живой замер скорости и статус достижимости, измеренные через реальный туннель — это те же данные, по которым фильтрует режим <em>best</em> в NodeCircle.</P>
          <P><B>Единый speed-тест.</B> Один путь измерения стоит за кнопкой на ноде, «Speed all», live-стримом и авто-проверкой. Сначала <B>гейтит по достижимости</B> — два 204-эндпоинта (Google, Cloudflare) с повтором — так что мёртвая нода падает за ~1с вместо перебора всех fallback. Число — это <B>среднее после прогрева плюс пик</B> устойчивого окна; сохраняются оба.</P>
          <P><B>Live-стриминг.</B> Тест ноды стримит Mbps по ходу (<code className="text-gray-400">cachefly · 45.2 Mbps</code>), переживает уход со страницы и пагинацию (состояние в query-кэше) и сохраняется — замер старше 6ч помечается, чтобы устаревшее число не читалось как актуальное.</P>
          <P><B>Проверка достижимости.</B> Один тап подтверждает, что нода реально проносит трафик в интернет (204 через живой туннель), отдельно от сырой скорости.</P>
          <P><B>Авто-проверки (фоновый прогон).</B> <B>Nodes &rarr; Auto-checks</B> тестирует скорость выбранного scope — <B>все / подписка / группа / конкретные ноды</B> — по интервалу, чтобы <code className="text-gray-400">best</code> / <code className="text-gray-400">min_speed</code> и UI были свежими без ручного теста. Последовательно (speed-тест забивает аплинк), со staleness-guard и изоляцией ошибок по каждой ноде — одна плохая нода не рвёт прогон. Новые ноды проверяются первыми, а ручной запуск сдвигает расписание, так что ручной и плановый прогоны не сталкиваются.</P>
          <P><B>SNI / REALITY-dest сканер.</B> В форме ноды проверь хост-кандидат на TLS 1.3 + HTTP/2 (через активную ноду) перед сохранением его целью маскировки REALITY.</P>
          <P><B>Экспорт URI одной ноды.</B> Скопируй share-ссылку <code className="text-gray-400">vless://</code> (и т.п.) прямо с карточки ноды.</P>
        </>
      ),
    },
  },

  /* 9. Chain Tunnel */
  {
    id: 'chain-tunnel',
    title: { en: 'Chain Tunnel — single Node', ru: 'Chain Tunnel — одиночная нода' },
    content: {
      en: (
        <>
          <P><em>For the multi-channel chain orchestrator that wires two x-ui panels together (added in v1.3.0-beta.7), see the next section "Proxy Chains (two x-ui panels)".</em></P>
          <P>Chain tunneling nests one protocol inside another using xray's <code className="text-gray-400">proxySettings.transportLayer</code>.</P>
          <P><B>Example:</B> WireGuard wrapped inside VLESS+Reality</P>
          <Code>{`Your device -> RPi (xray)
  -> VLESS+Reality (outer, to CDN/edge server)
    -> WireGuard (inner, to final VPN server)
      -> internet`}</Code>
          <P><B>What the network sees:</B> only VLESS+Reality traffic to the outer server. The WireGuard tunnel is invisible.</P>
          <P><B>How to configure:</B></P>
          <Ul>
            <li>Create both nodes (outer VLESS and inner WireGuard)</li>
            <li>On the inner node (WireGuard), set <B>Chain Node</B> to the outer node (VLESS)</li>
            <li>Use the inner node as your active node or in routing rules</li>
          </Ul>
          <P><B>Use cases:</B> bypass DPI that blocks WireGuard, add an extra layer of encryption, use Reality fingerprinting to hide VPN traffic.</P>
        </>
      ),
      ru: (
        <>
          <P><em>Multi-channel chain orchestrator на двух x-ui панелях (добавлен в v1.3.0-beta.7) описан в следующем разделе «Proxy Chains (две x-ui панели)».</em></P>
          <P>Chain tunnel вкладывает один протокол в другой через <code className="text-gray-400">proxySettings.transportLayer</code> xray.</P>
          <P><B>Пример:</B> WireGuard внутри VLESS+Reality</P>
          <Code>{`Устройство -> RPi (xray)
  -> VLESS+Reality (внешний, до CDN/edge-сервера)
    -> WireGuard (внутренний, до финального VPN)
      -> интернет`}</Code>
          <P><B>Что видит сеть:</B> только VLESS+Reality трафик до внешнего сервера. WireGuard-туннель невидим.</P>
          <P><B>Как настроить:</B></P>
          <Ul>
            <li>Создайте обе ноды (внешний VLESS и внутренний WireGuard)</li>
            <li>На внутренней ноде (WG) укажите <B>Chain Node</B> = внешняя нода (VLESS)</li>
            <li>Используйте внутреннюю ноду как активную или в правилах</li>
          </Ul>
          <P><B>Случаи:</B> обход DPI, блокирующего WireGuard; дополнительный слой шифрования; маскировка VPN через Reality.</P>
        </>
      ),
    },
  },

  /* 8b. Proxy Chains (two x-ui panels — beta.7+) */
  {
    id: 'proxy-chains',
    title: { en: 'Proxy Chains (two x-ui panels)', ru: 'Proxy Chains (две x-ui панели)' },
    content: {
      en: (
        <>
          <P>Since <B>v1.3.0-beta.7</B> PiTun can orchestrate a two-hop VLESS+Reality chain across two x-ui panels — the <B>relay</B> (what the client connects to) and the <B>exit</B> (what reaches the internet). Each chain can carry multiple independent <B>channels</B> with their own SNI cover and Reality keys; one client gets one URL per channel.</P>
          <P><B>Pre-requisites:</B></P>
          <Ul>
            <li>Two x-ui panels registered on the <em>Servers</em> tab (deploy via "Install x-ui" or import via <code className="text-gray-400">xui://</code> URI).</li>
            <li>At least one panel is in <em>xui-pro</em> mode (with domain + LE cert). Bare-mode panels work for the relay side too if the cover SNI is reachable.</li>
            <li>An "Exit SNI" — a domain the relay→exit hop pretends to dial (usually <code className="text-gray-400">www.google.com</code> / a CDN edge). Universal-reachability matters more than specific brand.</li>
          </Ul>
          <P><B>Create flow (Chains page → "Создать цепочку"):</B></P>
          <Ul>
            <li>Pick the exit panel and the relay panel.</li>
            <li>Add one or more channels. Each channel: name, Client SNI (cover the client→relay handshake hides behind), relay/exit port (leave empty → auto-pick non-conflicting), optional xhttp path.</li>
            <li>Backend orchestrator (`orchestrate_create`) creates per-channel exit + relay inbounds via the panel API, generates per-channel Reality keypair, opens UFW ports on both VPSes, pushes a combined <code className="text-gray-400">xrayTemplateConfig</code> to the relay so traffic from each relay-inbound routes to the matching exit-outbound, restarts Xray on the relay.</li>
          </Ul>
          <P><B>Channels and clients:</B></P>
          <Ul>
            <li>Each <em>channel</em> = one independent VLESS+Reality slot end-to-end (its own keys, port, SNI).</li>
            <li>Each <em>chain client</em> = a user with one UUID per channel. Adding a client adds N panel-side clients in lock-step (one per channel) so all the user's URLs share an identity but route over different cover SNIs.</li>
            <li>Export to Nodes: each chain-client URL becomes a separate routable Node row that the rest of PiTun (routing rules, balancer, circle, speedtest) treats like any other VLESS outbound.</li>
          </Ul>
          <P><B>Healthcheck (Chains tab → "Проверка"):</B> multi-layer probe — both panels reachable, xray running, inbound presence, relay's running xray actually has the chain outbound + matching routing rule (the silent-blackhole case we hit several times in beta.7), plus a live <code className="text-gray-400">testOutbound</code> probe of the relay→exit hop.</P>
          <P><B>Per-channel delete:</B> trash icon on each channel card removes just that channel (inbounds on both panels + UFW close + relay template rebuild + cascade-delete exported Node rows). Deleting the last channel folds the chain row away too.</P>
          <P><B>Gotchas baked into the orchestrator:</B></P>
          <Ul>
            <li>Two channels in one chain can't claim the same relay or exit port — pre-check refuses the draft. Auto-pick (port=0) lands in safe ranges.</li>
            <li>x-ui-pro panels reserve <code className="text-gray-400">:443</code> + <code className="text-gray-400">:80</code> for nginx — the port allocator skips them automatically; user-supplied 443 on an xui-pro relay is rejected with a clear error.</li>
            <li>Last chain on a relay = full <code className="text-gray-400">xrayTemplateConfig</code> wipe over SSH so the panel falls back to its auto-routing (pushing an empty template via API doesn't actually reset Xray's running config).</li>
            <li>Chain-tagged inbounds are <em>read-only</em> on the X-ui Panels page (delete + add-client buttons disabled, export-to-Node replaced with a "via Chains" hint) — chain clients are managed exclusively from the Chains tab so cascade-delete + sync stay consistent.</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>С <B>v1.3.0-beta.7</B> PiTun умеет собирать двухзвенную VLESS+Reality цепочку из двух x-ui панелей: <B>relay</B> (куда подключается клиент) и <B>exit</B> (что выходит в интернет). Каждая цепочка может содержать несколько независимых <B>каналов</B> со своими SNI-обложками и Reality-ключами; один клиент получает по одной ссылке на канал.</P>
          <P><B>Что нужно:</B></P>
          <Ul>
            <li>Две x-ui панели зарегистрированы на вкладке <em>Servers</em> (через «Install x-ui» или импорт по <code className="text-gray-400">xui://</code>).</li>
            <li>Хотя бы одна панель — в режиме <em>xui-pro</em> (с доменом и LE-сертом). Bare-режим тоже годится для relay, если SNI-обложка достижима.</li>
            <li>«Exit SNI» — домен, куда relay делает вид, что соединяется (обычно <code className="text-gray-400">www.google.com</code> или CDN edge). Универсальная доступность важнее конкретного бренда.</li>
          </Ul>
          <P><B>Создание (Chains → «Создать цепочку»):</B></P>
          <Ul>
            <li>Выберите exit-панель и relay-панель.</li>
            <li>Добавьте один или больше каналов. У каждого: имя, Client SNI (обложка handshake клиент→relay), relay/exit порт (пусто → авто-выбор без коллизий), опциональный xhttp path.</li>
            <li>Backend (`orchestrate_create`) создаёт exit + relay inbounds через панельный API, генерит Reality-ключи на канал, открывает UFW-порты на обоих VPS, пушит общий <code className="text-gray-400">xrayTemplateConfig</code> на relay так, чтобы трафик с каждого relay-inbound уходил в свой exit-outbound, перезапускает Xray на relay.</li>
          </Ul>
          <P><B>Каналы и клиенты:</B></P>
          <Ul>
            <li>Канал = один независимый VLESS+Reality слот end-to-end (свои ключи, порт, SNI).</li>
            <li>Chain client = пользователь с одним UUID на канал. Добавление клиента кладёт N панельных клиентов в lock-step — все URL'ы юзера разделяют identity, но идут по разным SNI-обложкам.</li>
            <li>Export to Nodes: каждый URL chain-клиента становится отдельной маршрутизируемой Node-строкой, которую остальной PiTun (правила, balancer, circle, speedtest) видит как обычный VLESS-outbound.</li>
          </Ul>
          <P><B>Healthcheck (Chains → «Проверка»):</B> многослойная проверка — обе панели достижимы, xray работает, inbounds на месте, у relay в running xray реально есть chain-outbound + routing rule (silent-blackhole кейс мы ловили несколько раз в beta.7), плюс живая <code className="text-gray-400">testOutbound</code> проверка relay→exit.</P>
          <P><B>Per-channel delete:</B> иконка корзины на карточке канала удаляет только этот канал (inbounds на обеих панелях + UFW close + перепуш relay-template + cascade-delete экспортированных Node-строк). Удаление последнего канала сворачивает всю цепочку.</P>
          <P><B>Gotchas, защищённые оркестратором:</B></P>
          <Ul>
            <li>Два канала в одной цепочке не могут забрать один и тот же relay/exit порт — pre-check отклоняет draft. Авто-выбор (port=0) попадает в безопасные диапазоны.</li>
            <li>xui-pro панели резервируют <code className="text-gray-400">:443</code> + <code className="text-gray-400">:80</code> под nginx — аллокатор их пропускает; пользовательский 443 на xui-pro relay отклоняется явной ошибкой.</li>
            <li>Последняя цепочка на relay → полный wipe <code className="text-gray-400">xrayTemplateConfig</code> по SSH, чтобы панель вернулась к auto-routing (пуш пустого шаблона через API не сбрасывает running config Xray).</li>
            <li>Chain-инбаунды <em>read-only</em> на странице X-ui Panels (кнопки delete + add-client дисейблены, export-to-Node заменён подсказкой «через Цепочки») — клиенты цепочки управляются только со вкладки Chains, чтобы cascade-delete и sync оставались консистентными.</li>
          </Ul>
        </>
      ),
    },
  },

  /* 9. Kill Switch */
  {
    id: 'kill-switch',
    title: { en: 'Kill Switch', ru: 'Kill Switch' },
    content: {
      en: (
        <>
          <P>When enabled, the kill switch blocks ALL internet traffic if xray stops or crashes, preventing traffic leaks.</P>
          <P><B>How it works:</B></P>
          <Ul>
            <li>Uses nftables DROP rules to block all outgoing traffic</li>
            <li>LAN traffic (192.168.0.0/16, 10.0.0.0/8) remains accessible</li>
            <li>VPN server IPs are whitelisted so xray can reconnect</li>
            <li>Activates on: manual stop, xray crash, unexpected exit</li>
          </Ul>
          <P><B>When to enable:</B> if you need to guarantee that no traffic leaks direct (unencrypted) when the proxy is down.</P>
          <P><B>When to disable:</B> if you want internet to work normally when proxy is off (e.g., during maintenance).</P>
        </>
      ),
      ru: (
        <>
          <P>Kill switch блокирует ВЕСЬ интернет-трафик, если xray останавливается или падает, предотвращая утечки.</P>
          <P><B>Как работает:</B></P>
          <Ul>
            <li>Использует правила nftables DROP для блокировки исходящего трафика</li>
            <li>LAN (192.168.0.0/16, 10.0.0.0/8) остаётся доступным</li>
            <li>IP VPN-серверов в белом списке — xray может переподключиться</li>
            <li>Срабатывает при: ручной остановке, краше xray, неожиданном завершении</li>
          </Ul>
          <P><B>Когда включать:</B> если нужна гарантия, что трафик не пойдёт напрямую при падении прокси.</P>
          <P><B>Когда выключать:</B> если интернет должен работать без прокси (напр. при обслуживании).</P>
        </>
      ),
    },
  },

  /* 10. Device Management */
  {
    id: 'device-management',
    title: { en: 'Device Management', ru: 'Управление устройствами' },
    content: {
      en: (
        <>
          <P>PiTun automatically discovers and manages LAN devices, giving you per-device control over proxy routing.</P>
          <P><B>Device discovery:</B></P>
          <Ul>
            <li>Background scanner runs every 60s using a fallback chain: <code className="text-gray-400">arp-scan</code> &rarr; <code className="text-gray-400">ip neigh</code> &rarr; <code className="text-gray-400">/proc/net/arp</code></li>
            <li>New devices are automatically added with <code className="text-gray-400">default</code> routing policy</li>
            <li>Devices not seen on the network are marked offline</li>
            <li>Manual scan available via the "Scan LAN" button</li>
          </Ul>
          <P><B>Device routing modes</B> (set in the Devices page header):</P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Mode</th>
                <th className="py-2 pr-4">Behavior</th>
                <th className="py-2">Use case</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">All devices</td><td className="py-2 pr-4">All traffic from all devices is proxied</td><td className="py-2">Default. Proxy the entire LAN.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Include only</td><td className="py-2 pr-4">Only devices marked "include" are proxied</td><td className="py-2">Whitelist: only specific devices use VPN</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Exclude list</td><td className="py-2 pr-4">All devices proxied except those marked "exclude"</td><td className="py-2">Blacklist: exclude specific devices from VPN</td></tr>
            </tbody>
          </table>
          <P><B>Per-device routing policy:</B></P>
          <Ul>
            <li><B>Default</B> — follows the global device routing mode</li>
            <li><B>Include</B> — device is explicitly included (used in "include_only" mode)</li>
            <li><B>Exclude</B> — device is explicitly excluded (used in "exclude_list" mode)</li>
          </Ul>
          <P>Click the policy badge on any device row to cycle through policies. Use checkboxes for bulk policy changes.</P>
          <P><B>How it works technically:</B></P>
          <P>Device filtering is a <B>pre-filter layer at the nftables level</B>, before traffic reaches xray:</P>
          <Code>{`Packet arrives at RPi
  -> [nftables] check MAC against include/exclude set
  -> If device should NOT be proxied -> return (direct to router)
  -> [nftables TPROXY] -> xray -> [Routing Rules] -> proxy/direct/block`}</Code>
          <P>This means routing rules (domain, geoip, etc.) only apply to traffic from allowed devices.</P>
          <P><B>UI features:</B></P>
          <Ul>
            <li>Inline rename — click pencil icon, type name, press Enter</li>
            <li>Filters — search by MAC/IP/name/hostname/vendor, filter by online/offline, filter by policy</li>
            <li>Bulk actions — select multiple devices with checkboxes, apply policy to all at once</li>
            <li>"Reset All" button — resets all devices to "default" policy</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>PiTun автоматически обнаруживает и управляет устройствами в LAN, давая контроль маршрутизации на уровне каждого устройства.</P>
          <P><B>Обнаружение устройств:</B></P>
          <Ul>
            <li>Фоновый сканер каждые 60с по цепочке: <code className="text-gray-400">arp-scan</code> &rarr; <code className="text-gray-400">ip neigh</code> &rarr; <code className="text-gray-400">/proc/net/arp</code></li>
            <li>Новые устройства добавляются с политикой <code className="text-gray-400">default</code></li>
            <li>Устройства, не замеченные в сети, помечаются как offline</li>
            <li>Ручное сканирование — кнопка "Scan LAN"</li>
          </Ul>
          <P><B>Режимы маршрутизации устройств</B> (задаётся в шапке страницы Devices):</P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Режим</th>
                <th className="py-2 pr-4">Поведение</th>
                <th className="py-2">Случай</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">All devices</td><td className="py-2 pr-4">Весь трафик от всех устройств проксируется</td><td className="py-2">По умолчанию. Проксировать всю LAN.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Include only</td><td className="py-2 pr-4">Проксируются только устройства с пометкой "include"</td><td className="py-2">Белый список: только определённые устройства используют VPN</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Exclude list</td><td className="py-2 pr-4">Все устройства проксируются кроме помеченных "exclude"</td><td className="py-2">Чёрный список: исключить конкретные устройства из VPN</td></tr>
            </tbody>
          </table>
          <P><B>Политика маршрутизации устройства:</B></P>
          <Ul>
            <li><B>Default</B> — следует глобальному режиму маршрутизации устройств</li>
            <li><B>Include</B> — устройство явно включено (используется в режиме "include_only")</li>
            <li><B>Exclude</B> — устройство явно исключено (используется в режиме "exclude_list")</li>
          </Ul>
          <P>Кликните на бейдж политики в строке устройства для переключения. Используйте чекбоксы для массового изменения политики.</P>
          <P><B>Как это работает технически:</B></P>
          <P>Фильтрация устройств — <B>pre-filter слой на уровне nftables</B>, до передачи трафика в xray:</P>
          <Code>{`Пакет приходит на RPi
  -> [nftables] проверяет MAC по include/exclude множеству
  -> Если устройство НЕ должно проксироваться -> return (напрямую на роутер)
  -> [nftables TPROXY] -> xray -> [Правила маршрутизации] -> proxy/direct/block`}</Code>
          <P>Это значит, что правила маршрутизации (домен, geoip и т.д.) применяются только к трафику от разрешённых устройств.</P>
          <P><B>Функции UI:</B></P>
          <Ul>
            <li>Inline rename — нажмите карандаш, введите имя, нажмите Enter</li>
            <li>Фильтры — поиск по MAC/IP/имени/hostname/vendor, фильтр по online/offline, фильтр по политике</li>
            <li>Массовые действия — выберите устройства чекбоксами, примените политику ко всем сразу</li>
            <li>Кнопка "Reset All" — сброс всех устройств на политику "default"</li>
          </Ul>
        </>
      ),
    },
  },

  /* 11. Subscriptions */
  {
    id: 'subscriptions',
    title: { en: 'Subscriptions', ru: 'Подписки' },
    content: {
      en: (
        <>
          <P>Import proxy nodes from subscription URLs (providers, self-hosted panels).</P>
          <P><B>Supported formats:</B> Clash YAML, base64-encoded URI list, plain URI list</P>
          <P><B>Features:</B></P>
          <Ul>
            <li><B>Auto-update</B> — set an interval (e.g. every 6h), nodes refresh automatically</li>
            <li><B>User-Agent templates</B> — an editable table (<B>Subscriptions &rarr; UA templates</B>) replaces the old hardcoded presets. Each row has a name, key, UA string and description; bumping Happ's app version or a Chrome build when a panel starts rejecting a stale fingerprint no longer needs a redeploy. A subscription references a template by key, and an unknown key falls back to the built-in map rather than breaking a refresh.</li>
            <li><B>Custom request headers</B> — a template can declare extra headers sent with its User-Agent (for panels that also gate on an API key, a <code className="text-gray-400">Referer</code>, or a device fingerprint). An empty value <em>removes</em> that header instead of sending it blank — that's how you drop <code className="text-gray-400">Accept-Encoding</code> for panels that mishandle gzip. CR/LF and non-ASCII values are rejected on save.</li>
            <li><B>Export / import UA catalogue</B> — download the whole template set as JSON and restore it on another install. Import is additive; matching keys are skipped unless you overwrite in place (which keeps the row id, so subscriptions stay attached).</li>
            <li><B>Regex filter</B> — only import nodes whose names match a pattern (e.g. <code className="text-gray-400">US|UK|DE</code>)</li>
            <li><B>GeoIP country flags (opt-in)</B> — imported node names can be prefixed with a country flag (<code className="text-gray-400">🇳🇱 vless-nl</code>). Licence-clean: nothing is shipped or downloaded — drop a MaxMind <code className="text-gray-400">GeoLite2-Country.mmdb</code> next to the geoip/geosite data and it lights up; absent it's a silent no-op.</li>
            <li>Subscription nodes are tagged and can be bulk-deleted when the subscription is removed</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>Импорт прокси-нод из URL подписок (провайдеры, self-hosted панели).</P>
          <P><B>Поддерживаемые форматы:</B> Clash YAML, base64 URI, обычный список URI</P>
          <P><B>Возможности:</B></P>
          <Ul>
            <li><B>Автообновление</B> — задайте интервал (напр. каждые 6ч), ноды обновятся автоматически</li>
            <li><B>User-Agent шаблоны</B> — редактируемая таблица (<B>Subscriptions &rarr; UA templates</B>) вместо старых захардкоженных пресетов. У каждой строки имя, ключ, UA-строка и описание; поднять версию Happ или билд Chrome, когда панель начинает отбраковывать устаревший fingerprint, теперь можно без редеплоя. Подписка ссылается на шаблон по ключу, а неизвестный ключ откатывается на встроенную карту, а не ломает refresh.</li>
            <li><B>Кастомные request-заголовки</B> — шаблон может объявить доп. заголовки, отправляемые вместе с User-Agent (для панелей, которые проверяют ещё и API-ключ, <code className="text-gray-400">Referer</code> или device fingerprint). Пустое значение <em>удаляет</em> заголовок, а не шлёт пустым — так убирают <code className="text-gray-400">Accept-Encoding</code> для панелей, ломающихся на gzip. CR/LF и не-ASCII значения отклоняются при сохранении.</li>
            <li><B>Экспорт / импорт каталога UA</B> — выгрузить весь набор шаблонов в JSON и восстановить на другой установке. Импорт аддитивный; совпадающие ключи пропускаются, если не выбрать перезапись на месте (id строки сохраняется, подписки остаются привязанными).</li>
            <li><B>Regex-фильтр</B> — импортировать только ноды с именами по паттерну (напр. <code className="text-gray-400">US|UK|DE</code>)</li>
            <li><B>Флаги стран GeoIP (opt-in)</B> — имена импортируемых нод можно префиксить флагом страны (<code className="text-gray-400">🇳🇱 vless-nl</code>). Чисто по лицензии: ничего не поставляется и не качается — положи MaxMind <code className="text-gray-400">GeoLite2-Country.mmdb</code> рядом с geoip/geosite, и оно заработает; без него — тихий no-op.</li>
            <li>Ноды подписки отмечены тегом и удаляются массово при удалении подписки</li>
          </Ul>
        </>
      ),
    },
  },

  /* 11b. Servers (managed VPS inventory) */
  {
    id: 'servers',
    title: { en: 'Servers (VPS Inventory)', ru: 'Серверы (инвентарь VPS)' },
    content: {
      en: (
        <>
          <P>The <B>Servers</B> page is a managed inventory of remote VPS hosts that PiTun knows how to talk to. It is separate from <em>Nodes</em>: a Node is a runtime proxy outbound, a Server is the VPS box that may host one (or several) of those outbounds.</P>
          <P><B>Each server stores:</B></P>
          <Ul>
            <li><B>Connection</B> — name, host (IP/domain), SSH port, username, key path or password</li>
            <li><B>Provider metadata</B> — optional notes (provider, region, plan) just for human bookkeeping</li>
            <li><B>Tags</B> — free-form labels for filtering</li>
          </Ul>
          <P><B>Deployments tab:</B> shows which protocols are currently set up on a server (e.g. VLESS+Reality on :443, NaiveProxy on :443, Hysteria2 on :8443). PiTun keeps these deployment records so you can re-issue clients without remembering which port belongs to what.</P>
          <P><B>Manual scripts:</B> a server row can launch helper scripts (provision Caddy + naive, install xray, harden SSH, etc.) over its SSH connection. This is opt-in — nothing runs automatically.</P>
          <P><B>JSON Export/Import:</B> Servers page has dedicated Export/Import JSON buttons. The bundle envelope is <code className="text-gray-400">{`{kind: "pitun-servers-export", version: 1, ...}`}</code>. Export defaults to <em>without secrets</em> (passwords and SSH key contents redacted); a checkbox lets you include them when migrating between trusted hosts. Dedup on import is keyed on <code className="text-gray-400">(name, host, port)</code>.</P>
        </>
      ),
      ru: (
        <>
          <P>Страница <B>Servers</B> — управляемый инвентарь удалённых VPS, с которыми PiTun умеет общаться. Это <em>не</em> то же, что <em>Nodes</em>: Node — это runtime-outbound прокси, Server — это VPS-машина, на которой может хоститься один (или несколько) таких outbound.</P>
          <P><B>В записи сервера хранится:</B></P>
          <Ul>
            <li><B>Подключение</B> — имя, host (IP/домен), SSH-порт, пользователь, путь к ключу или пароль</li>
            <li><B>Метаданные провайдера</B> — опционально: провайдер, регион, план — чисто для человека</li>
            <li><B>Теги</B> — произвольные метки для фильтрации</li>
          </Ul>
          <P><B>Вкладка Deployments:</B> показывает, какие протоколы сейчас развернуты на сервере (напр. VLESS+Reality на :443, NaiveProxy на :443, Hysteria2 на :8443). Чтобы не помнить, какой порт чему принадлежит — PiTun хранит эти записи и помогает переиздавать конфиги клиентов.</P>
          <P><B>Manual scripts:</B> со строкой сервера можно запускать вспомогательные скрипты (поднять Caddy+naive, поставить xray, харднить SSH и т.п.) по его SSH-подключению. Только вручную — ничего не запускается само.</P>
          <P><B>JSON Export/Import:</B> на странице Servers есть отдельные кнопки Export/Import JSON. Конверт пакета — <code className="text-gray-400">{`{kind: "pitun-servers-export", version: 1, ...}`}</code>. Экспорт по умолчанию <em>без секретов</em> (пароли и содержимое ключей редактируются); чекбокс позволяет включить их для переноса между доверенными хостами. Дедуп при импорте по <code className="text-gray-400">(name, host, port)</code>.</P>
        </>
      ),
    },
  },

  /* 11b-2. Installing a proxy node on a Server (auto-deploy, v1.3.0+) */
  {
    id: 'install-on-server',
    title: {
      en: 'Installing a proxy node on a Server',
      ru: 'Установка прокси на Server',
    },
    content: {
      en: (
        <>
          <P>
            Since <B>v1.3.0-beta.1</B>, PiTun can run an install script on a registered{' '}
            <B>Server</B> over SSH and automatically create a Node from the
            resulting URI. Supported protocols by beta.8:{' '}
            <B>NaiveProxy</B> (Caddy+forwardproxy on :443),{' '}
            <B>WireGuard</B> (multi-client; manage from the Servers tab),{' '}
            <B>x-ui</B> (full 3x-ui / x-ui-pro panel, manages many VLESS / Trojan / SOCKS
            inbounds — see <em>X-ui Panels</em> + <em>Proxy Chains</em> sections).
          </P>

          <P><B>Prerequisites:</B></P>
          <Ul>
            <li>The Server is registered on the <em>Servers</em> page (host, port, user, password OR private key)</li>
            <li>The SSH probe (Activity icon) succeeded at least once — auto-deploy is disabled for offline servers</li>
            <li>For <em>naive</em>: a domain whose <code className="text-gray-400">A</code> record points at the VPS, plus an email address (Let's Encrypt registration)</li>
            <li>The VPS is a fresh Debian 11+ / Ubuntu 22+ box with port 80 + 443 reachable from the Internet</li>
          </Ul>

          <P><B>Flow (per click on the Rocket button on a Server row):</B></P>
          <Ul>
            <li>Modal opens — fill in <code className="text-gray-400">domain</code> / <code className="text-gray-400">email</code> (auto-prefilled if you already saved a deployment plan via "Configure & download"); username defaults to <code className="text-gray-400">pitun</code>; leave password blank to have the VPS auto-generate one</li>
            <li>Click <B>Run install</B> → PiTun POSTs to <code className="text-gray-400">/api/servers/{'{id}'}/deploy</code>, which spawns a background <B>Job</B> and returns <code className="text-gray-400">202 + job_id</code> immediately</li>
            <li>The modal switches to a live log panel — every stdout/stderr line from the remote script streams in via WebSocket, with stderr highlighted red and auto-scroll</li>
            <li>On success: the Caddy + naive_forwardproxy bootstrap finishes, the script prints <code className="text-gray-400">URI=naive+https://...</code>, PiTun parses it and creates a <B>Node</B> automatically. The status banner flips green and a <em>Open Nodes</em> link appears.</li>
            <li>The corresponding <em>ServerDeployment</em> row is upserted with <code className="text-gray-400">status=deployed</code> + <code className="text-gray-400">last_node_id</code> so the badge under the server name shows "Naive deployed · linked to Node #N"</li>
          </Ul>

          <P><B>Slot lock:</B> only one deploy per <code className="text-gray-400">(server_id, protocol)</code> can run at once. Clicking <em>Install</em> twice on the same Server while the first is still running returns HTTP 409 with a clear message. Different protocols on the same Server (or the same protocol on a different Server) run in parallel — the lock is per-pair.</P>

          <P><B>Cancel:</B> while the job is running you can press <em>Cancel</em>. Important caveat: this stops PiTun pumping the remote output into the live log buffer locally — the install script keeps running on the VPS to avoid a half-installed Caddy / certbot. If you really need to abort, SSH in and kill the process; otherwise let it finish and re-run the deploy if needed (it's idempotent).</P>

          <P><B>Hide vs Close:</B> while running, the modal's footer button reads <em>Hide (keeps running)</em> — closing the modal does <em>not</em> cancel; the job keeps streaming, you can find it on the <code className="text-gray-400">/server-tasks</code> page (the "Tasks" link in the Servers page header).</P>

          <P><B>Server-tasks page (<code className="text-gray-400">/server-tasks</code>):</B></P>
          <Ul>
            <li><B>Filter pills</B> across the top: by Server, by status (running / succeeded / failed / cancelled). Filter state lives in the URL — bookmark or share a link and the view restores</li>
            <li><B>Master/detail layout</B>: list on the left (newest first, polls every 5s), detail panel on the right with the live WS log for running jobs and the captured <code className="text-gray-400">log_tail</code> for finalized ones</li>
            <li><B>Cancel</B> button on the detail header for running jobs (same caveat as above)</li>
            <li>Job rows older than 30 days are auto-pruned, and the table is capped at 500 rows total — no maintenance needed</li>
          </Ul>

          <P><B>Discoverability:</B> the page is intentionally <em>not</em> in the main sidebar — it's a Servers-page concern, not a top-level concept. Get there via the <em>Tasks</em> link in the Servers header (visible once you've registered at least one server) or the "All tasks" link at the top of the live log inside DeployModal.</P>

          <P><B>Edge cases &amp; failure modes:</B></P>
          <Ul>
            <li>
              <B>Status = <code className="text-gray-400">deployed_no_uri</code></B>: script exited 0 but didn't print the
              <code className="text-gray-400"> URI=</code> contract line. The script may be from an older PiTun release, or got truncated.
              No Node is created automatically; check the captured log and add the Node manually with whatever credentials the script printed.
            </li>
            <li>
              <B>Backend restarted mid-deploy</B>: any Job stuck in <code className="text-gray-400">running</code> for more than 1 hour is healed to <code className="text-gray-400">failed</code> on the next backend boot with the message "backend restarted during deploy". The remote script may have completed regardless — check the VPS state, then re-run if needed.
            </li>
            <li>
              <B>Slot conflict on retry after cancel</B>: cancellation only stops local streaming, so the slot is freed when the remote script eventually finishes. If you cancelled and the slot is still showing busy, wait until the original install times out (10 min hard cap) or reboot the VPS first.
            </li>
            <li>
              <B>Naive sidecar (PiTun side)</B>: once the Node is created, PiTun automatically launches a <code className="text-gray-400">pitun-naive-{'{id}'}</code> Docker container that does the local naive client wiring + nftables bypass mark. If it doesn't come up, the Node row will show offline; check Logs and Diagnostics.
            </li>
          </Ul>

          <P><B>Manual fallback:</B> the <em>Download .sh</em> button on the same row generates the same install script as a downloadable bash bootstrap — for users who don't want to give PiTun their SSH key, or for bulk provisioning via Ansible / cloud-init. The script prints the same <code className="text-gray-400">URI=</code> line; paste it into <em>Nodes → Import URI</em>.</P>
        </>
      ),
      ru: (
        <>
          <P>
            Начиная с <B>v1.3.0-beta.1</B>, PiTun умеет запускать установочный скрипт на зарегистрированном{' '}
            <B>Server</B> по SSH и автоматически создавать Node из полученного URI. К beta.8 поддерживаются:{' '}
            <B>NaiveProxy</B> (Caddy+forwardproxy на :443), <B>WireGuard</B> (мульти-клиент; управление с
            вкладки Servers), <B>x-ui</B> (полная панель 3x-ui / x-ui-pro для управления множеством VLESS /
            Trojan / SOCKS инбаундов — см. разделы <em>X-ui Panels</em> + <em>Proxy Chains</em>).
          </P>

          <P><B>Что нужно заранее:</B></P>
          <Ul>
            <li>Сервер добавлен на странице <em>Servers</em> (host, port, user, пароль ИЛИ приватный ключ)</li>
            <li>SSH-проба (иконка Activity) хотя бы раз была успешной — для offline-серверов авто-деплой задизейблен</li>
            <li>Для <em>naive</em>: домен с <code className="text-gray-400">A</code>-записью на VPS и email (для регистрации Let's Encrypt)</li>
            <li>VPS — свежий Debian 11+ / Ubuntu 22+ с открытыми из интернета портами 80 и 443</li>
          </Ul>

          <P><B>Что происходит по клику на Rocket-иконке в строке сервера:</B></P>
          <Ul>
            <li>Открывается модалка — заполняешь <code className="text-gray-400">domain</code> / <code className="text-gray-400">email</code> (предзаполнятся, если ты уже сохранял план через «Configure &amp; download»); username по умолчанию <code className="text-gray-400">pitun</code>; пароль можно оставить пустым — VPS сгенерирует сам</li>
            <li>Жмёшь <B>Run install</B> → фронт POST-ит на <code className="text-gray-400">/api/servers/{'{id}'}/deploy</code>, бэкенд порождает фоновый <B>Job</B> и сразу отвечает <code className="text-gray-400">202 + job_id</code></li>
            <li>Модалка переключается в live-лог: каждая строка stdout/stderr со скрипта приходит по WebSocket, stderr подсвечен красным, автоскролл</li>
            <li>На успехе: bootstrap Caddy + naive_forwardproxy завершается, скрипт печатает <code className="text-gray-400">URI=naive+https://...</code>, PiTun парсит его и создаёт <B>Node</B> автоматически. Status-баннер становится зелёным, появляется ссылка <em>Open Nodes</em>.</li>
            <li>Соответствующая запись <em>ServerDeployment</em> upsert-ится со <code className="text-gray-400">status=deployed</code> + <code className="text-gray-400">last_node_id</code>, поэтому под именем сервера появится бейдж «Naive развернут · привязан к Node #N»</li>
          </Ul>

          <P><B>Slot-lock:</B> на одну пару <code className="text-gray-400">(server_id, protocol)</code> может бежать только одна установка. Двойной клик на <em>Install</em> на одном и том же сервере, пока первый ещё идёт, отдаст HTTP 409 с понятным сообщением. Разные протоколы на одном сервере (или один протокол на разных серверах) идут параллельно — лок попарный.</P>

          <P><B>Cancel:</B> пока джоб бежит — есть кнопка <em>Cancel</em>. Важный нюанс: это останавливает только локальный поток вывода в PiTun. Скрипт на VPS продолжит работать, чтобы не оставить Caddy / certbot в полу-установленном состоянии. Если нужно действительно прервать — заходи по SSH и убивай процесс; иначе дай скрипту доработать и перезапусти деплой при необходимости (он идемпотентен).</P>

          <P><B>Hide vs Close:</B> пока идёт установка, кнопка в футере модалки читается как <em>Hide (keeps running)</em> — закрытие модалки <em>не</em> отменяет джоб; он продолжит стримиться, а найти его можно на странице <code className="text-gray-400">/server-tasks</code> (ссылка «Tasks» в шапке страницы Servers).</P>

          <P><B>Страница server-tasks (<code className="text-gray-400">/server-tasks</code>):</B></P>
          <Ul>
            <li><B>Фильтры</B> в виде «таблеток» сверху: по серверу, по статусу (running / succeeded / failed / cancelled). Состояние фильтров живёт в URL — закладка / шеринг ссылки восстановит вид</li>
            <li><B>Master/detail</B>: слева список (новые сверху, опрос каждые 5с), справа панель деталей с live WS-логом для running и захваченным <code className="text-gray-400">log_tail</code> для завершённых</li>
            <li><B>Cancel</B> на шапке детали — для running джобов (с тем же нюансом)</li>
            <li>Записи старше 30 дней автоматически удаляются, плюс кэп 500 строк всего — обслуживания не нужно</li>
          </Ul>

          <P><B>Discoverability:</B> страница умышленно <em>не</em> в основном сайдбаре — это концерн страницы Servers, а не глобальная сущность. Попасть туда можно через ссылку <em>Tasks</em> в шапке страницы Servers (появляется, как только зарегистрирован хотя бы один сервер) либо через ссылку «All tasks» в шапке live-лога DeployModal.</P>

          <P><B>Граничные случаи и failure-режимы:</B></P>
          <Ul>
            <li>
              <B>Status = <code className="text-gray-400">deployed_no_uri</code></B>: скрипт завершился с кодом 0, но не напечатал контрактную строку
              <code className="text-gray-400"> URI=</code>. Возможно, скрипт от старого релиза PiTun или вывод был обрезан.
              Node автоматически не создаётся — посмотри лог и добавь Node вручную с теми credential-ами, что скрипт показал.
            </li>
            <li>
              <B>Бэкенд перезапустился во время установки</B>: любой Job, провисевший в <code className="text-gray-400">running</code> дольше 1 часа, на следующем старте бэкенда лечится в <code className="text-gray-400">failed</code> с сообщением «backend restarted during deploy». Скрипт на VPS мог при этом успеть отработать — проверь VPS и запусти заново при необходимости.
            </li>
            <li>
              <B>Конфликт slot после Cancel</B>: отмена останавливает только локальный стриминг, так что слот освобождается, когда удалённый скрипт реально допишет работу. Если ты отменил, а слот всё ещё «занят» — дождись хард-таймаута (10 мин) или ребутни VPS.
            </li>
            <li>
              <B>Naive sidecar (на стороне PiTun)</B>: после создания Node PiTun автоматически поднимет Docker-контейнер <code className="text-gray-400">pitun-naive-{'{id}'}</code>, который делает локальную обвязку naive-клиента + bypass-метку nftables. Если он не поднимется, Node будет показан как offline — смотри Logs и Diagnostics.
            </li>
          </Ul>

          <P><B>Ручной fallback:</B> кнопка <em>Download .sh</em> в той же строке генерирует тот же установочный скрипт в виде скачиваемого bash-bootstrap — для тех, кто не хочет давать PiTun SSH-ключ, или для массового provisioning через Ansible / cloud-init. Скрипт печатает ту же строку <code className="text-gray-400">URI=</code>; вставляй её в <em>Nodes → Import URI</em>.</P>
        </>
      ),
    },
  },

  /* 11b-3. X-ui Panels (manage VLESS / Trojan / SOCKS inbounds, since v1.3.0-beta.7) */
  {
    id: 'xui-panels',
    title: { en: 'X-ui Panels', ru: 'Панели X-ui' },
    content: {
      en: (
        <>
          <P>Once an x-ui panel is registered (via "Install x-ui" on a Server, or by pasting an <code className="text-gray-400">xui://</code> URI), it shows up on the dedicated <B>Панели X-ui</B> page. From there you can manage every inbound and client on the panel without leaving PiTun.</P>
          <P><B>Panel modes:</B></P>
          <Ul>
            <li><B>xui-pro</B> — full stack: nginx on :443 with Let's Encrypt + fakesite + the panel + xray inbounds fronted via <code className="text-gray-400">externalProxy</code>. Recommended for production / domain-mode flows.</li>
            <li><B>bare</B> — vanilla 3x-ui only. Panel on a random high port, self-signed cert (or HTTP for the localhost-bound API path). Coexists with NaiveProxy on the same VPS — only xui-pro shares :443.</li>
          </Ul>
          <P><B>Inbound presets:</B> the <em>Add inbound</em> dialog ships 6 wired-in templates: VLESS+TCP+Reality+xtls-rprx-vision, VLESS+xhttp+Reality, VLESS+WS over TLS, VLESS+xhttp over TLS, Trojan+gRPC over TLS, SOCKS5 (user/pass). Domain-mode presets require an xui-pro panel; Reality presets work on bare too.</P>
          <P><B>Per-inbound clients:</B> expand any inbound card to see its client list. Each client row shows email/UUID, flow, and either a green <em>Node #N</em> badge (if already exported) or an <em>Экспорт в Nodes</em> button. Export creates a Node row idempotently — re-exporting the same client reuses the existing Node and refreshes its fields if the inbound was edited on the panel side.</P>
          <P><B>Chain-managed inbounds:</B> inbounds whose tag starts with <code className="text-gray-400">chain-</code> are read-only on this page — Add Client / Delete + per-client Export are replaced with a "via Chains" hint. Manage them from the <em>Chains</em> tab so the chain orchestrator's bookkeeping stays consistent.</P>
          <P><B>Healthcheck (since v1.3.0-beta.8):</B> the «Проверить» button opens a modal that runs a multi-layer probe — panel API reachable, xray running, system snapshot (cpu / mem / uptime / disk), and over SSH: nginx state (xui-pro only), <code className="text-gray-400">ufw</code> active, TLS cert expiry, free disk %, free memory %, unzip availability. Each check is ok / warn / fail with a one-line detail.</P>
          <P><B>Sync (since v1.3.0-beta.8):</B> the «Синхронизация» button reconciles PiTun's local <code className="text-gray-400">XuiClient</code> cache with the panel's live state. Detects clients added/removed via the panel UI directly, inserts missing cache rows, drops vanished ones, and cascade-cleans Node rows whose backing client is gone. Result toast shows the counts (added / updated / removed / chain inbounds skipped / orphan Nodes cleaned). Chain-tagged inbounds are skipped — PiTun is the source of truth there.</P>
          <P><B>Fakesite rotation (xui-pro only, since v1.3.0-beta.8):</B> the «Ротация фейк-сайта» button picks a random template from the bundled <code className="text-gray-400">/root/randomfakehtml-master/</code> archive and copies it into <code className="text-gray-400">/var/www/html</code>; «Загрузить ZIP» lets you upload a custom site (≤100 MB, must contain index.html). Both fire <code className="text-gray-400">chmod</code> / <code className="text-gray-400">chown</code> + <code className="text-gray-400">nginx -s reload</code> automatically.</P>
        </>
      ),
      ru: (
        <>
          <P>Зарегистрированная x-ui панель (через «Install x-ui» на Server или импорт <code className="text-gray-400">xui://</code>) отображается на отдельной странице <B>Панели X-ui</B>. Оттуда можно управлять всеми инбаундами и клиентами панели не покидая PiTun.</P>
          <P><B>Режимы панели:</B></P>
          <Ul>
            <li><B>xui-pro</B> — полный стек: nginx на :443 с Let's Encrypt + fakesite + панель + xray-инбаунды через <code className="text-gray-400">externalProxy</code>. Рекомендуется для production / domain-flow.</li>
            <li><B>bare</B> — голый 3x-ui. Панель на случайном порту, self-signed cert (или HTTP на localhost-API). Сосуществует с NaiveProxy на одном VPS — :443 делит только xui-pro.</li>
          </Ul>
          <P><B>Пресеты инбаунда:</B> диалог <em>Добавить инбаунд</em> содержит 6 шаблонов: VLESS+TCP+Reality+xtls-rprx-vision, VLESS+xhttp+Reality, VLESS+WS over TLS, VLESS+xhttp over TLS, Trojan+gRPC over TLS, SOCKS5 (user/pass). Domain-пресеты требуют xui-pro; Reality-пресеты работают и на bare.</P>
          <P><B>Клиенты в инбаунде:</B> разверните карточку инбаунда чтобы увидеть список клиентов. Каждая строка: email/UUID, flow, и либо зелёный <em>Node #N</em> бэйдж (если экспортирован), либо кнопка <em>Экспорт в Nodes</em>. Экспорт создаёт Node идемпотентно — повторный экспорт того же клиента переиспользует существующую Node и обновляет её поля.</P>
          <P><B>Chain-инбаунды:</B> инбаунды с тегом, начинающимся на <code className="text-gray-400">chain-</code>, на этой странице read-only — Add Client / Delete + per-client Export заменены на подсказку «через Цепочки». Управляйте ими через вкладку <em>Chains</em>, чтобы bookkeeping chain-оркестратора оставался консистентным.</P>
          <P><B>Healthcheck (с v1.3.0-beta.8):</B> кнопка «Проверить» открывает модалку с многослойной проверкой — API панели достижим, xray работает, системный снапшот (cpu / mem / uptime / disk), плюс по SSH: nginx (только для xui-pro), <code className="text-gray-400">ufw</code> активный, срок TLS-сертификата, % свободного диска, % свободной памяти, наличие unzip. Каждая проверка — ok / warn / fail с однострочным detail.</P>
          <P><B>Sync (с v1.3.0-beta.8):</B> кнопка «Синхронизация» сверяет локальный кеш <code className="text-gray-400">XuiClient</code> с реальным состоянием панели. Замечает клиентов добавленных/удалённых прямо через UI панели, вставляет недостающие cache-строки, удаляет исчезнувшие, каскадно чистит Node-строки чей клиент исчез. Тост-результат: добавлено / обновлено / удалено / пропущено chain-инбаундов / убрано осиротевших Node. Chain-инбаунды пропускаются — PiTun для них source of truth.</P>
          <P><B>Ротация фейк-сайта (только xui-pro, с v1.3.0-beta.8):</B> кнопка «Ротация фейк-сайта» выбирает случайный шаблон из встроенного архива <code className="text-gray-400">/root/randomfakehtml-master/</code> и копирует в <code className="text-gray-400">/var/www/html</code>; «Загрузить ZIP» позволяет залить свой сайт (≤100 MB, нужен index.html). Обе операции делают <code className="text-gray-400">chmod</code> / <code className="text-gray-400">chown</code> + <code className="text-gray-400">nginx -s reload</code> автоматически.</P>
        </>
      ),
    },
  },

  /* 11b-4. Direct Connection switch */
  {
    id: 'direct-connection',
    title: { en: 'Direct Connection switch', ru: 'Переключатель Direct' },
    content: {
      en: (
        <>
          <P>By default every SSH / panel operation — server test, deploy, uninstall, WireGuard clients, x-ui sync / healthcheck / inbounds / clients, chain create / healthcheck / clients / export — dials <B>through the active node</B>, the same tunnel the LAN uses.</P>
          <P><B>Why:</B> when your ISP throttles or blocks a panel / VPS, routing the management traffic through the tunnel reaches it anyway.</P>
          <P><B>The Direct toggle</B> in each page header (Servers, X-ui, Chains) and in the Deploy modal flips a single operation back to a straight dial off the host (SO_MARK bypass) — for reaching a box <em>while the active node is down</em>. The backend honours <code className="text-gray-400">?direct=</code> on every <code className="text-gray-400">/servers</code> and <code className="text-gray-400">/xui</code> route, so the choice is per-operation, never a global mode.</P>
        </>
      ),
      ru: (
        <>
          <P>По умолчанию любая SSH / панельная операция — тест сервера, деплой, удаление, клиенты WireGuard, x-ui sync / healthcheck / inbounds / клиенты, создание цепочки / healthcheck / клиенты / экспорт — идёт <B>через активную ноду</B>, тот же туннель, что и LAN.</P>
          <P><B>Зачем:</B> когда провайдер режет или блокирует панель / VPS, прогон управляющего трафика через туннель всё равно до неё достучится.</P>
          <P><B>Тумблер Direct</B> в шапке каждой страницы (Servers, X-ui, Chains) и в модалке Deploy возвращает одну операцию на прямой дозвон с хоста (обход через SO_MARK) — чтобы достучаться до бокса, <em>пока активная нода лежит</em>. Бэкенд понимает <code className="text-gray-400">?direct=</code> на каждом роуте <code className="text-gray-400">/servers</code> и <code className="text-gray-400">/xui</code>, так что выбор — на каждую операцию, а не глобальный режим.</P>
        </>
      ),
    },
  },

  /* 11c. Backup & Restore (JSON Export/Import) */
  {
    id: 'backup-restore',
    title: { en: 'Backup & Restore (JSON Export/Import)', ru: 'Бэкап и восстановление (JSON Export/Import)' },
    content: {
      en: (
        <>
          <P>PiTun supports full-fidelity JSON export/import for three independent areas:</P>
          <Ul>
            <li><B>Nodes</B> — all proxy outbounds with their protocol-specific fields (UUID, transport, TLS, WireGuard keys, chain links, etc.).</li>
            <li><B>Servers</B> — the VPS inventory described above.</li>
            <li><B>Routing rules</B> — round-tripped as v2ray-style routing JSON (covered in the Routing Rules section).</li>
          </Ul>
          <P><B>Bundle envelope:</B> every export wraps its items in a typed envelope:</P>
          <Code>{`{
  "kind": "pitun-nodes-export" | "pitun-servers-export",
  "version": 1,
  "exported_at": "2026-05-04T12:34:56Z",
  "pitun_version": "1.2.3",
  "count": 42,
  "items": [ ... ]
}`}</Code>
          <P>The <code className="text-gray-400">kind</code> + <code className="text-gray-400">version</code> pair lets future versions migrate or politely refuse incompatible files.</P>
          <P><B>Import modes:</B></P>
          <Ul>
            <li><B>Append (default)</B> — duplicates are skipped using a natural key (Nodes: <code className="text-gray-400">protocol+address+port+uuid</code>; Servers: <code className="text-gray-400">name+host+port</code>).</li>
            <li><B>Replace</B> — passing <code className="text-gray-400">replace=true</code> wipes the existing collection first, then inserts the bundle. Use only for migrations between fresh instances.</li>
          </Ul>
          <P><B>Distinct from URI/subscription import:</B> URI-based import (<code className="text-gray-400">vless://</code>, Clash YAML, etc.) only carries protocol details for one node at a time. JSON Export/Import is a true backup format that preserves PiTun-specific metadata such as ordering, enabled flags, chain links, and friendly names.</P>
          <P><B>Plain-text URI export (since v1.3.0-beta.8):</B> a sibling endpoint <code className="text-gray-400">GET /api/nodes/export-uris</code> emits a <code className="text-gray-400">.txt</code> file with one VPN URI per line (<code className="text-gray-400">vless://</code>, <code className="text-gray-400">vmess://</code>, <code className="text-gray-400">trojan://</code>, <code className="text-gray-400">ss://</code>, <code className="text-gray-400">hysteria2://</code>, <code className="text-gray-400">socks://</code>). WireGuard nodes are skipped (no canonical URI form). Useful for sharing your node list with another v2rayN-compatible client or for a quick offline copy.</P>
          <P><B>Unified Import dialog (since v1.3.0-beta.8):</B> the Nodes-page toolbar collapses three buttons into one <em>Import</em> (paste OR drop a file — format auto-detected as JSON bundle vs URI list) and one <em>Export ▾</em> dropdown that lets you pick between URI list (.txt) and JSON bundle (.json).</P>
        </>
      ),
      ru: (
        <>
          <P>PiTun поддерживает полноформатный JSON Export/Import для трёх независимых областей:</P>
          <Ul>
            <li><B>Nodes</B> — все outbound-ы со всеми протокол-специфичными полями (UUID, транспорт, TLS, WireGuard ключи, chain-связки и т.д.).</li>
            <li><B>Servers</B> — инвентарь VPS, описан выше.</li>
            <li><B>Routing rules</B> — туда-обратно через v2ray routing JSON (см. раздел Routing Rules).</li>
          </Ul>
          <P><B>Конверт пакета:</B> экспорт всегда оборачивает items в типизированный конверт:</P>
          <Code>{`{
  "kind": "pitun-nodes-export" | "pitun-servers-export",
  "version": 1,
  "exported_at": "2026-05-04T12:34:56Z",
  "pitun_version": "1.2.3",
  "count": 42,
  "items": [ ... ]
}`}</Code>
          <P>Пара <code className="text-gray-400">kind</code> + <code className="text-gray-400">version</code> позволяет будущим версиям мигрировать или вежливо отклонить несовместимый файл.</P>
          <P><B>Режимы импорта:</B></P>
          <Ul>
            <li><B>Append (по умолчанию)</B> — дубли пропускаются по естественному ключу (Nodes: <code className="text-gray-400">protocol+address+port+uuid</code>; Servers: <code className="text-gray-400">name+host+port</code>).</li>
            <li><B>Replace</B> — параметр <code className="text-gray-400">replace=true</code> сначала очищает коллекцию, затем вставляет пакет. Для миграций между чистыми инстансами.</li>
          </Ul>
          <P><B>Отличие от URI/подписок:</B> URI-импорт (<code className="text-gray-400">vless://</code>, Clash YAML и т.п.) переносит только протокольные поля одной ноды. JSON Export/Import — настоящий формат бэкапа: сохраняет PiTun-специфичные данные (порядок, флаг enabled, chain-связки, читаемые имена).</P>
          <P><B>Plain-text URI экспорт (с v1.3.0-beta.8):</B> соседний endpoint <code className="text-gray-400">GET /api/nodes/export-uris</code> отдаёт <code className="text-gray-400">.txt</code>-файл с одним VPN URI на строку (<code className="text-gray-400">vless://</code>, <code className="text-gray-400">vmess://</code>, <code className="text-gray-400">trojan://</code>, <code className="text-gray-400">ss://</code>, <code className="text-gray-400">hysteria2://</code>, <code className="text-gray-400">socks://</code>). WireGuard ноды пропускаются (нет канонической URI-формы). Удобно расшарить список нод в другой v2rayN-совместимый клиент или быстро взять offline-копию.</P>
          <P><B>Унифицированный Import-диалог (с v1.3.0-beta.8):</B> тулбар Nodes-страницы объединил три кнопки в две: одна <em>Import</em> (вставить или загрузить файл — формат определяется автоматически: JSON-bundle vs URI-список) и одна <em>Export ▾</em> с dropdown-выбором URI-список (.txt) или JSON-bundle (.json).</P>
        </>
      ),
    },
  },

  /* 11d. Geo Data Profiles */
  {
    id: 'geodata',
    title: { en: 'Geo Data Profiles', ru: 'Geo-профили' },
    content: {
      en: (
        <>
          <P>Geo data files (<code className="text-gray-400">geoip.dat</code> + <code className="text-gray-400">geosite.dat</code>) power <code className="text-gray-400">geoip:*</code> and <code className="text-gray-400">geosite:*</code> rules. PiTun ships with three switchable upstream profiles — pick the one that best matches your routing intent.</P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Profile</th>
                <th className="py-2 pr-4">Source</th>
                <th className="py-2">Best for</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">Loyalsoldier</td><td className="py-2 pr-4">github.com/Loyalsoldier/v2ray-rules-dat</td><td className="py-2">CN bypass, the de-facto Mainland-China focused community list. Largest <code className="text-gray-400">geosite:cn</code> coverage.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">runetfreedom</td><td className="py-2 pr-4">github.com/runetfreedom/russia-blocked-geosite</td><td className="py-2">Russian-internet focus. Curated <code className="text-gray-400">geosite:ru-blocked</code> for sites blocked inside RU and a clean <code className="text-gray-400">geosite:ru</code> list for bypass.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">v2fly</td><td className="py-2 pr-4">github.com/v2fly/domain-list-community + geoip.dat</td><td className="py-2">Upstream "vanilla" v2fly data — neutral, broadest coverage, recommended baseline.</td></tr>
            </tbody>
          </table>
          <P><B>How it works:</B> the GeoData page lets you select a profile and click <em>Update</em>. PiTun fetches the new <code className="text-gray-400">.dat</code> files into the xray asset directory and triggers an xray reload. Update history (last fetched, file sizes, source URL) is shown on the page.</P>
          <P><B>Mixing rule sources:</B> you can only have one active profile at a time — the loaded <code className="text-gray-400">.dat</code> files define the namespace for <em>all</em> <code className="text-gray-400">geosite:*</code> tags in your routing rules. Switching profiles may invalidate tags that don't exist in the new source.</P>
        </>
      ),
      ru: (
        <>
          <P>Geo-данные (<code className="text-gray-400">geoip.dat</code> + <code className="text-gray-400">geosite.dat</code>) — это база для правил <code className="text-gray-400">geoip:*</code> и <code className="text-gray-400">geosite:*</code>. PiTun поставляется с тремя переключаемыми upstream-профилями — выбирайте по задаче.</P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Профиль</th>
                <th className="py-2 pr-4">Источник</th>
                <th className="py-2">Когда использовать</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">Loyalsoldier</td><td className="py-2 pr-4">github.com/Loyalsoldier/v2ray-rules-dat</td><td className="py-2">Bypass CN — де-факто стандартный community-список с фокусом на материковый Китай. Самый широкий <code className="text-gray-400">geosite:cn</code>.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">runetfreedom</td><td className="py-2 pr-4">github.com/runetfreedom/russia-blocked-geosite</td><td className="py-2">Фокус на рунет. Курируемый <code className="text-gray-400">geosite:ru-blocked</code> (заблокированные внутри РФ) и чистый <code className="text-gray-400">geosite:ru</code> для bypass.</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">v2fly</td><td className="py-2 pr-4">github.com/v2fly/domain-list-community + geoip.dat</td><td className="py-2">Upstream «vanilla» v2fly — нейтральный, максимально широкий, разумный базовый выбор.</td></tr>
            </tbody>
          </table>
          <P><B>Как работает:</B> на странице GeoData выберите профиль и нажмите <em>Update</em>. PiTun скачает новые <code className="text-gray-400">.dat</code> файлы в asset-директорию xray и перезагрузит xray. История обновлений (когда забиралось, размеры, источник) отображается на странице.</P>
          <P><B>Смешивание источников:</B> активным может быть только один профиль — загруженные <code className="text-gray-400">.dat</code> определяют пространство имён для <em>всех</em> тегов <code className="text-gray-400">geosite:*</code> в правилах. При смене профиля теги, которых нет в новом источнике, перестанут срабатывать.</P>
        </>
      ),
    },
  },

  /* 11. Health Checks & Failover */
  {
    id: 'health-checks',
    title: { en: 'Health Checks & Failover', ru: 'Health Checks и Failover' },
    content: {
      en: (
        <>
          <P>PiTun performs background TCP health checks on all enabled nodes every 30 seconds.</P>
          <Ul>
            <li>Probes connect with SO_MARK=0xFF so they bypass the TPROXY interception layer</li>
            <li>Nodes are marked online/offline based on TCP connectivity and measured latency</li>
            <li>Latency is displayed on the Dashboard and Nodes page</li>
            <li>Health check results feed into the <code className="text-gray-400">leastPing</code> balancer strategy</li>
          </Ul>
          <P><B>Two-tier automatic failover:</B> when the active node fails health checks repeatedly, PiTun decides recovery in this order:</P>
          <Ul>
            <li><B>Tier 1 — Circle-aware:</B> if the failed node belongs to an <em>enabled</em> NodeCircle, the failover handler delegates to <code className="text-gray-400">circle_scheduler.rotate_circle()</code>. This reuses the circle's pre-ping + retry logic so dead siblings are skipped automatically. Emits a <code className="text-gray-400">failover.via_circle</code> event.</li>
            <li><B>Tier 2 — Fallback list:</B> if no circle owns the node (or the circle has no live siblings), PiTun probes the <em>fallback nodes</em> list configured on the NodeCircles page in order; the first alive one becomes active.</li>
          </Ul>
          <P><B>Master toggle:</B> the <em>Auto-failover</em> switch on the NodeCircles page enables/disables the entire failover machinery.</P>
        </>
      ),
      ru: (
        <>
          <P>PiTun выполняет фоновые TCP health checks всех включённых нод каждые 30 секунд.</P>
          <Ul>
            <li>Зонды соединяются с SO_MARK=0xFF, чтобы обходить TPROXY-слой</li>
            <li>Ноды помечаются online/offline по TCP-подключению и замеренной задержке</li>
            <li>Задержка отображается на Dashboard и странице Nodes</li>
            <li>Результаты проверок используются стратегией <code className="text-gray-400">leastPing</code> балансировщика</li>
          </Ul>
          <P><B>Двухуровневый автоматический failover:</B> когда активная нода стабильно падает по health check, PiTun выбирает восстановление в таком порядке:</P>
          <Ul>
            <li><B>Tier 1 — Circle-aware:</B> если упавшая нода входит во <em>включённый</em> NodeCircle, failover делегирует восстановление в <code className="text-gray-400">circle_scheduler.rotate_circle()</code>. Это переиспользует pre-ping + retry круга — мёртвые соседи пропускаются автоматически. Записывается событие <code className="text-gray-400">failover.via_circle</code>.</li>
            <li><B>Tier 2 — Fallback-список:</B> если никакой круг не «владеет» нодой (или в круге нет живых), PiTun по порядку зондирует список <em>fallback nodes</em> со страницы NodeCircles; первая живая становится активной.</li>
          </Ul>
          <P><B>Главный тогл:</B> переключатель <em>Auto-failover</em> на странице NodeCircles включает/выключает весь механизм failover.</P>
        </>
      ),
    },
  },

  /* 11e. Updates (in-UI self-update) */
  {
    id: 'updates',
    title: { en: 'Updates (self-update)', ru: 'Обновления (self-update)' },
    content: {
      en: (
        <>
          <P><B>Settings &rarr; Updates</B> checks GitHub, shows what's new and applies it with live progress.</P>
          <P><B>How it works:</B> the backend deliberately can't update itself — doing so restarts the very container serving the request — so it writes a request file on the shared volume and a host-side systemd path unit (<code className="text-gray-400">pitun-update.sh --agent</code>) does the work. Progress travels back the same way, which is why the panel keeps reporting correctly straight through the backend restart. Endpoints: <code className="text-gray-400">/api/system/update/check|status|start</code>.</P>
          <P><B>Fetched through the active node</B> — a throttled direct route to GitHub isn't a blocker. The reply names the route that answered (active node / direct / unreachable), so "couldn't reach GitHub" never renders as "you're up to date".</P>
          <P><B>Safety:</B> a downgrade below 1.4.8 removes the Updates panel and is called out first (with the shell command to come back). After a verified-healthy update, superseded Docker images are dropped and only the 3 most recent DB snapshots are kept — neither runs on failure, which is exactly when the old artefacts are worth having.</P>
          <P><B>Unattended:</B> <code className="text-gray-400">scripts/pitun-update.sh --install-timer</code> adds a daily systemd timer that reports by default and only applies with <code className="text-gray-400">--apply</code>.</P>
        </>
      ),
      ru: (
        <>
          <P><B>Settings &rarr; Updates</B> проверяет GitHub, показывает что нового и применяет с живым прогрессом.</P>
          <P><B>Как это работает:</B> бэкенд намеренно не может обновить сам себя — это перезапустило бы тот самый контейнер, что обслуживает запрос — поэтому он пишет файл-запрос на общий том, а host-side systemd path-unit (<code className="text-gray-400">pitun-update.sh --agent</code>) делает работу. Прогресс возвращается тем же путём, поэтому панель продолжает корректно отчитываться прямо через рестарт бэкенда. Эндпоинты: <code className="text-gray-400">/api/system/update/check|status|start</code>.</P>
          <P><B>Тянется через активную ноду</B> — урезанный прямой маршрут к GitHub не помеха. Ответ называет маршрут, который ответил (active node / direct / unreachable), так что «не достучались до GitHub» никогда не отрисуется как «у вас всё актуально».</P>
          <P><B>Безопасность:</B> откат ниже 1.4.8 убирает панель Updates и предупреждает об этом заранее (с shell-командой, чтобы вернуться). После проверенно-здорового обновления удаляются устаревшие Docker-образы и хранятся только 3 последних снэпшота БД — ни то, ни другое не запускается при неудаче, когда старые артефакты как раз и нужны.</P>
          <P><B>Без участия:</B> <code className="text-gray-400">scripts/pitun-update.sh --install-timer</code> добавляет ежедневный systemd-таймер, который по умолчанию только отчитывается и применяет только с <code className="text-gray-400">--apply</code>.</P>
        </>
      ),
    },
  },

  /* 12. Security */
  {
    id: 'security',
    title: { en: 'Security', ru: 'Безопасность' },
    content: {
      en: (
        <>
          <Ul>
            <li><B>JWT authentication</B> — HS256, 24h token lifetime. All API endpoints protected except <code className="text-gray-400">/health</code> and <code className="text-gray-400">/auth/login</code></li>
            <li><B>Login lockout</B> — after 5 consecutive failed logins the account is locked for 15 minutes (HTTP 429 + <code className="text-gray-400">Retry-After</code>); a successful login resets the counter. PiTun is LAN-only with no captcha, so this is the primary brute-force guard.</li>
            <li><B>WebSocket auth</B> — log stream requires JWT token via <code className="text-gray-400">?token=</code> query param</li>
            <li><B>Password</B> — bcrypt hashing, minimum 8 characters, changeable via UI (sidebar key icon)</li>
            <li><B>CLI reset</B> — <code className="text-gray-400">docker exec pitun-backend bash /app/scripts/reset-password.sh newpassword</code></li>
            <li><B>SSRF protection</B> — subscription URLs are validated: hostname is resolved via DNS and all resolved IPs are checked against private/loopback/link-local ranges</li>
            <li><B>nftables sanitization</B> — MAC/CIDR inputs validated with regex before passing to nft</li>
            <li><B>No shell injection</B> — subprocess_exec with stdin pipe for nft commands</li>
            <li><B>xray checksum</B> — SHA256 verification of xray binary during installation</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <Ul>
            <li><B>JWT-аутентификация</B> — HS256, время жизни токена 24ч. Все API-эндпоинты защищены кроме <code className="text-gray-400">/health</code> и <code className="text-gray-400">/auth/login</code></li>
            <li><B>Блокировка входа</B> — после 5 подряд неудачных логинов аккаунт блокируется на 15 минут (HTTP 429 + <code className="text-gray-400">Retry-After</code>); успешный вход сбрасывает счётчик. PiTun работает только в LAN и без капчи, так что это основной защитник от перебора.</li>
            <li><B>WebSocket-авторизация</B> — поток логов требует JWT через параметр <code className="text-gray-400">?token=</code></li>
            <li><B>Пароль</B> — bcrypt-хеширование, минимум 8 символов, можно сменить через UI (иконка ключа)</li>
            <li><B>CLI-сброс</B> — <code className="text-gray-400">docker exec pitun-backend bash /app/scripts/reset-password.sh newpassword</code></li>
            <li><B>SSRF-защита</B> — URL подписок проверяется: hostname резолвится через DNS и все полученные IP проверяются на приватность/loopback/link-local</li>
            <li><B>Санитизация nftables</B> — MAC/CIDR проверяются regex перед передачей в nft</li>
            <li><B>Нет shell injection</B> — subprocess_exec с stdin pipe для nft-команд</li>
            <li><B>Контрольная сумма xray</B> — SHA256 верификация бинарника при установке</li>
          </Ul>
        </>
      ),
    },
  },

  /* 13. QUIC Blocking */
  {
    id: 'quic-blocking',
    title: { en: 'QUIC Blocking', ru: 'Блокировка QUIC' },
    content: {
      en: (
        <>
          <P><B>Problem:</B> QUIC (HTTP/3) is UDP-based. TPROXY intercepts it but the IP path changes, breaking connections.</P>
          <P><B>Solution:</B> PiTun blocks UDP port 443 via nftables, forcing browsers to fall back to TCP/443 (HTTP/2) which TPROXY handles correctly.</P>
          <Ul>
            <li>Only affects traffic routed through the proxy</li>
            <li>Bypassed destinations (direct rules) keep QUIC working</li>
            <li>Toggle on Dashboard: <B>Block QUIC (UDP/443)</B> checkbox</li>
            <li>Only shown when inbound mode is TPROXY or Both</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P><B>Проблема:</B> QUIC (HTTP/3) работает по UDP. TPROXY перехватывает его, но IP-путь меняется, ломая соединения.</P>
          <P><B>Решение:</B> PiTun блокирует UDP порт 443 через nftables, заставляя браузеры откатиться на TCP/443 (HTTP/2), который TPROXY обрабатывает корректно.</P>
          <Ul>
            <li>Затрагивает только трафик, идущий через прокси</li>
            <li>Обходимые направления (direct-правила) сохраняют QUIC</li>
            <li>Переключатель на Dashboard: <B>Block QUIC (UDP/443)</B></li>
            <li>Отображается только в режиме TPROXY или Both</li>
          </Ul>
        </>
      ),
    },
  },

  /* 13b. TLS Fragment (anti-DPI) */
  {
    id: 'tls-fragment',
    title: { en: 'TLS Fragment (anti-DPI)', ru: 'TLS Fragment (anti-DPI)' },
    content: {
      en: (
        <>
          <P><B>Settings &rarr; TLS Fragment</B> splits the outgoing TLS ClientHello across several packets, so a DPI box can't match the SNI in a single read.</P>
          <P>Entirely client-side — the server is unaware and reassembles the stream normally. Off by default. When on, only proxy <em>entry</em> hops are routed through a <code className="text-gray-400">fragment</code> freedom outbound; chain relay hops and the <code className="text-gray-400">freedom</code> / <code className="text-gray-400">blackhole</code> / <code className="text-gray-400">dns</code> / reserved tags are never touched.</P>
          <P><B>Tunables:</B> packet mode (e.g. <code className="text-gray-400">tlshello</code>), length range, interval range. Needs a bundled xray 26.x. Complements QUIC blocking — force TCP first, then fragment the ClientHello that rides on it.</P>
        </>
      ),
      ru: (
        <>
          <P><B>Settings &rarr; TLS Fragment</B> бьёт исходящий TLS ClientHello на несколько пакетов, чтобы DPI не поймал SNI за одно чтение.</P>
          <P>Полностью на стороне клиента — сервер об этом не знает и штатно собирает поток. По умолчанию выключено. При включении через <code className="text-gray-400">fragment</code> freedom-outbound идут только <em>входные</em> прокси-хопы; relay-хопы цепочек и теги <code className="text-gray-400">freedom</code> / <code className="text-gray-400">blackhole</code> / <code className="text-gray-400">dns</code> / зарезервированные не трогаются.</P>
          <P><B>Настройки:</B> режим пакетов (напр. <code className="text-gray-400">tlshello</code>), диапазон длины, диапазон интервала. Нужен bundled xray 26.x. Дополняет блокировку QUIC — сначала форсируем TCP, затем фрагментируем ClientHello, который по нему едет.</P>
        </>
      ),
    },
  },

  /* 14. Traffic Stats */
  {
    id: 'traffic-stats',
    title: { en: 'Traffic Stats', ru: 'Статистика трафика' },
    content: {
      en: (
        <>
          <P>PiTun reads per-node traffic statistics from the xray stats API.</P>
          <Ul>
            <li>Uplink and downlink bytes per node, updated every 5 seconds on the Dashboard</li>
            <li>Stats are collected while xray is running and reset on restart</li>
            <li>Each node's outbound is tagged as <code className="text-gray-400">node-&lt;id&gt;</code> for stats tracking</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P>PiTun читает посистемную статистику трафика из xray stats API.</P>
          <Ul>
            <li>Uplink и downlink в байтах по каждой ноде, обновляется каждые 5 секунд на Dashboard</li>
            <li>Статистика собирается пока xray работает и сбрасывается при перезапуске</li>
            <li>Каждый outbound помечен как <code className="text-gray-400">node-&lt;id&gt;</code> для отслеживания</li>
          </Ul>
        </>
      ),
    },
  },

  /* 15. HomeProxy Integration */
  {
    id: 'homeproxy',
    title: { en: 'HomeProxy Integration', ru: 'Интеграция с HomeProxy' },
    content: {
      en: (
        <>
          <P>PiTun works alongside OpenWrt HomeProxy for routers with limited resources.</P>
          <P><B>Setup:</B></P>
          <Ul>
            <li>On your OpenWrt router, install HomeProxy</li>
            <li>Add a SOCKS5 node pointing to <code className="text-gray-400">RPi_IP:1080</code> (PiTun's SOCKS5 inbound)</li>
            <li>Route traffic through that SOCKS5 node in HomeProxy</li>
            <li>The router sends traffic to RPi, which applies all routing rules and forwards through VPN</li>
          </Ul>
          <P>This approach offloads heavy crypto and routing to the RPi while the router just forwards.</P>
        </>
      ),
      ru: (
        <>
          <P>PiTun работает совместно с OpenWrt HomeProxy для роутеров с ограниченными ресурсами.</P>
          <P><B>Настройка:</B></P>
          <Ul>
            <li>На роутере OpenWrt установите HomeProxy</li>
            <li>Добавьте SOCKS5-ноду, указывающую на <code className="text-gray-400">RPi_IP:1080</code> (SOCKS5-вход PiTun)</li>
            <li>Маршрутизируйте трафик через эту SOCKS5-ноду в HomeProxy</li>
            <li>Роутер отправляет трафик на RPi, который применяет правила и пересылает через VPN</li>
          </Ul>
          <P>Такой подход переносит тяжёлую криптографию и маршрутизацию на RPi, а роутер только форвардит.</P>
        </>
      ),
    },
  },

  /* 16. Router Setup Guide */
  {
    id: 'router-setup',
    title: { en: 'Router Setup Guide', ru: 'Настройка роутера' },
    content: {
      en: (
        <>
          <P>How to route your entire network through PiTun:</P>
          <P><B>Option 1: Change DHCP Gateway (all devices)</B></P>
          <P>In your router's admin panel, change the DHCP settings so the default gateway points to RPi4's IP address. This routes ALL devices on the network through PiTun automatically.</P>
          <Ul>
            <li>Xiaomi/Redmi: Settings &rarr; LAN &rarr; DHCP Server &rarr; Gateway = 192.168.1.109</li>
            <li>TP-Link: Advanced &rarr; Network &rarr; DHCP Server &rarr; Default Gateway = 192.168.1.109</li>
            <li>ASUS: LAN &rarr; DHCP Server &rarr; Default Gateway = 192.168.1.109</li>
            <li>Keenetic: Home Network &rarr; DHCP &rarr; Gateway = 192.168.1.109</li>
            <li>OpenWrt: Network &rarr; Interfaces &rarr; LAN &rarr; DHCP &rarr; Advanced &rarr; Gateway = 192.168.1.109</li>
            <li>MikroTik: IP &rarr; DHCP Server &rarr; Networks &rarr; Gateway = 192.168.1.109</li>
          </Ul>
          <P>After changing, all devices that renew their DHCP lease will route through RPi4.</P>
          <P><B>Option 2: Static route on specific device</B></P>
          <P>On a phone/PC, set the gateway manually:</P>
          <Ul>
            <li>Windows: Network Settings &rarr; IPv4 &rarr; Gateway = 192.168.1.109</li>
            <li>macOS: System Settings &rarr; Network &rarr; Wi-Fi &rarr; Details &rarr; TCP/IP &rarr; Router = 192.168.1.109</li>
            <li>iOS: Wi-Fi &rarr; (i) &rarr; Configure IP &rarr; Manual &rarr; Router = 192.168.1.109</li>
            <li>Android: Wi-Fi &rarr; Long press &rarr; Modify &rarr; Advanced &rarr; Gateway = 192.168.1.109</li>
          </Ul>
          <P><B>Option 3: SOCKS5/HTTP Proxy (apps only)</B></P>
          <P>Configure in browser or app settings:</P>
          <Ul>
            <li>SOCKS5: <code className="text-gray-200">192.168.1.109:1080</code></li>
            <li>HTTP: <code className="text-gray-200">192.168.1.109:8080</code></li>
          </Ul>
          <P>No gateway change needed — only the configured app uses PiTun.</P>
          <P><B>Important:</B></P>
          <Ul>
            <li>RPi4 must have a static IP (use DHCP reservation in router)</li>
            <li>RPi4's own gateway must point to the real router (192.168.1.1)</li>
            <li>Enable IP forwarding on RPi4 (done by setup script)</li>
          </Ul>
          <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/40 px-3 py-2 text-xs text-red-700 dark:text-red-300 mt-2">
            <B>Warning: RPi4 must NOT use itself as gateway!</B> If RPi4 gets its gateway via DHCP (like other devices), and you set DHCP gateway=192.168.1.109, RPi4 will route its own traffic to itself — infinite loop, network dies. RPi4 must have a <B>static configuration</B> with gateway=192.168.1.1 (your real router). The setup script does this automatically. nftables also marks RPi4's own traffic with mark=255 to skip TPROXY interception.
          </div>
          <Code>{`# RPi4 static config (done by setup script):
nmcli con mod "Wired connection 1" \\
  ipv4.addresses 192.168.1.109/24 \\
  ipv4.gateway 192.168.1.1 \\
  ipv4.method manual`}</Code>
        </>
      ),
      ru: (
        <>
          <P>Как направить всю домашнюю сеть через PiTun:</P>
          <P><B>Вариант 1: Изменить шлюз в DHCP (все устройства)</B></P>
          <P>В панели администрирования роутера измените настройки DHCP так, чтобы шлюз по умолчанию указывал на IP-адрес RPi4. Это автоматически направит ВСЕ устройства в сети через PiTun.</P>
          <Ul>
            <li>Xiaomi/Redmi: Настройки &rarr; LAN &rarr; DHCP-сервер &rarr; Шлюз = 192.168.1.109</li>
            <li>TP-Link: Дополнительно &rarr; Сеть &rarr; DHCP-сервер &rarr; Шлюз по умолчанию = 192.168.1.109</li>
            <li>ASUS: LAN &rarr; DHCP-сервер &rarr; Шлюз по умолчанию = 192.168.1.109</li>
            <li>Keenetic: Домашняя сеть &rarr; DHCP &rarr; Шлюз = 192.168.1.109</li>
            <li>OpenWrt: Network &rarr; Interfaces &rarr; LAN &rarr; DHCP &rarr; Advanced &rarr; Gateway = 192.168.1.109</li>
            <li>MikroTik: IP &rarr; DHCP Server &rarr; Networks &rarr; Gateway = 192.168.1.109</li>
          </Ul>
          <P>После изменения все устройства, обновившие DHCP-аренду, будут маршрутизироваться через RPi4.</P>
          <P><B>Вариант 2: Статический маршрут на конкретном устройстве</B></P>
          <P>На телефоне/ПК задайте шлюз вручную:</P>
          <Ul>
            <li>Windows: Настройки сети &rarr; IPv4 &rarr; Шлюз = 192.168.1.109</li>
            <li>macOS: Системные настройки &rarr; Сеть &rarr; Wi-Fi &rarr; Подробнее &rarr; TCP/IP &rarr; Маршрутизатор = 192.168.1.109</li>
            <li>iOS: Wi-Fi &rarr; (i) &rarr; Настройка IP &rarr; Вручную &rarr; Маршрутизатор = 192.168.1.109</li>
            <li>Android: Wi-Fi &rarr; Долгое нажатие &rarr; Изменить &rarr; Дополнительно &rarr; Шлюз = 192.168.1.109</li>
          </Ul>
          <P><B>Вариант 3: SOCKS5/HTTP прокси (только приложения)</B></P>
          <P>Настройте в браузере или приложении:</P>
          <Ul>
            <li>SOCKS5: <code className="text-gray-200">192.168.1.109:1080</code></li>
            <li>HTTP: <code className="text-gray-200">192.168.1.109:8080</code></li>
          </Ul>
          <P>Менять шлюз не нужно — через PiTun пойдёт только настроенное приложение.</P>
          <P><B>Важно:</B></P>
          <Ul>
            <li>RPi4 должен иметь статический IP (используйте резервирование DHCP в роутере)</li>
            <li>Шлюз самого RPi4 должен указывать на реальный роутер (192.168.1.1)</li>
            <li>IP-форвардинг на RPi4 должен быть включён (делается скриптом установки)</li>
          </Ul>
          <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/40 px-3 py-2 text-xs text-red-700 dark:text-red-300 mt-2">
            <B>Внимание: RPi4 НЕ должен использовать себя как шлюз!</B> Если RPi4 получает шлюз через DHCP (как остальные устройства), и вы поставите DHCP gateway=192.168.1.109, RPi4 будет маршрутизировать свой трафик на себя — бесконечная петля, сеть ляжет. RPi4 должен иметь <B>статическую конфигурацию</B> с gateway=192.168.1.1 (ваш реальный роутер). Скрипт установки делает это автоматически. nftables также помечает собственный трафик RPi4 меткой mark=255 чтобы пропускать его мимо TPROXY.
          </div>
          <Code>{`# Статическая конфигурация RPi4 (делается скриптом установки):
nmcli con mod "Wired connection 1" \\
  ipv4.addresses 192.168.1.109/24 \\
  ipv4.gateway 192.168.1.1 \\
  ipv4.method manual`}</Code>
        </>
      ),
    },
  },

  /* 17. TPROXY vs TUN Comparison */
  {
    id: 'tproxy-vs-tun',
    title: { en: 'TPROXY vs TUN Comparison', ru: 'Сравнение TPROXY и TUN' },
    content: {
      en: (
        <>
          <P>Both modes intercept traffic and apply the same routing rules. The difference is HOW they intercept.</P>
          <P><B>TPROXY (Recommended for gateway)</B></P>
          <Ul>
            <li>Uses Linux kernel nftables to intercept packets</li>
            <li>Works at network layer — transparent to all devices</li>
            <li>Supports MAC-based bypass (skip specific devices)</li>
            <li>Kill switch via nftables DROP rules</li>
            <li>QUIC blocking via nftables (forces TCP fallback)</li>
            <li>Slightly faster — kernel-level packet handling</li>
            <li>Requires nftables support (all modern Linux)</li>
          </Ul>
          <P><B>TUN Mode</B></P>
          <Ul>
            <li>xray creates a virtual tun0 interface</li>
            <li>xray's autoRoute adds system routes</li>
            <li>No nftables needed for routing (xray handles it)</li>
            <li>No MAC-based bypass (no nftables layer)</li>
            <li>Kill switch: fallback nftables rule blocks traffic if tun0 dies</li>
            <li>QUIC: xray can sniff QUIC natively (destOverride: ["quic"])</li>
            <li>Slightly slower — userspace packet processing</li>
          </Ul>
          <P><B>When to use what:</B></P>
          <Ul>
            <li>RPi4 as LAN gateway &rarr; TPROXY (best performance, full feature set)</li>
            <li>RPi4 as standalone device &rarr; TUN works fine</li>
            <li>nftables not available &rarr; TUN is the only option</li>
            <li>Need MAC bypass &rarr; must use TPROXY</li>
          </Ul>
          <P><B>Feature comparison table:</B></P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Feature</th>
                <th className="py-2 pr-4">TPROXY</th>
                <th className="py-2">TUN</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">Routing rules</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Domain sniffing</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">GeoIP/GeoSite</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">MAC bypass</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10007;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Kill switch</td><td className="py-2 pr-4">Native</td><td className="py-2">Fallback</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">QUIC handling</td><td className="py-2 pr-4">Block (TCP fallback)</td><td className="py-2">Sniff natively</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Performance</td><td className="py-2 pr-4">Faster (kernel)</td><td className="py-2">Good (userspace)</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Setup complexity</td><td className="py-2 pr-4">nftables required</td><td className="py-2">Simpler</td></tr>
            </tbody>
          </table>
        </>
      ),
      ru: (
        <>
          <P>Оба режима перехватывают трафик и применяют одни и те же правила маршрутизации. Разница в том, КАК они перехватывают.</P>
          <P><B>TPROXY (рекомендуется для шлюза)</B></P>
          <Ul>
            <li>Использует nftables ядра Linux для перехвата пакетов</li>
            <li>Работает на сетевом уровне — прозрачно для всех устройств</li>
            <li>Поддерживает обход по MAC-адресу (пропуск конкретных устройств)</li>
            <li>Kill switch через правила nftables DROP</li>
            <li>Блокировка QUIC через nftables (принудительный откат на TCP)</li>
            <li>Чуть быстрее — обработка пакетов на уровне ядра</li>
            <li>Требуется поддержка nftables (все современные Linux)</li>
          </Ul>
          <P><B>Режим TUN</B></P>
          <Ul>
            <li>xray создаёт виртуальный интерфейс tun0</li>
            <li>autoRoute xray добавляет системные маршруты</li>
            <li>nftables не нужен для маршрутизации (xray справляется сам)</li>
            <li>Нет обхода по MAC (нет слоя nftables)</li>
            <li>Kill switch: резервное правило nftables блокирует трафик при падении tun0</li>
            <li>QUIC: xray может нативно анализировать QUIC (destOverride: ["quic"])</li>
            <li>Чуть медленнее — обработка пакетов в пространстве пользователя</li>
          </Ul>
          <P><B>Когда что использовать:</B></P>
          <Ul>
            <li>RPi4 как шлюз LAN &rarr; TPROXY (лучшая производительность, полный набор функций)</li>
            <li>RPi4 как отдельное устройство &rarr; TUN подходит</li>
            <li>nftables недоступен &rarr; TUN — единственный вариант</li>
            <li>Нужен обход по MAC &rarr; только TPROXY</li>
          </Ul>
          <P><B>Сравнительная таблица:</B></P>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-2 pr-4">Функция</th>
                <th className="py-2 pr-4">TPROXY</th>
                <th className="py-2">TUN</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              <tr><td className="py-2 pr-4 text-gray-200">Правила маршрутизации</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Анализ доменов</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">GeoIP/GeoSite</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10003;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Обход по MAC</td><td className="py-2 pr-4">&#10003;</td><td className="py-2">&#10007;</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Kill switch</td><td className="py-2 pr-4">Нативный</td><td className="py-2">Резервный</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Обработка QUIC</td><td className="py-2 pr-4">Блокировка (откат на TCP)</td><td className="py-2">Нативный анализ</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Производительность</td><td className="py-2 pr-4">Быстрее (ядро)</td><td className="py-2">Хорошо (userspace)</td></tr>
              <tr><td className="py-2 pr-4 text-gray-200">Сложность настройки</td><td className="py-2 pr-4">Нужен nftables</td><td className="py-2">Проще</td></tr>
            </tbody>
          </table>
        </>
      ),
    },
  },

  /* 18. Network Architecture */
  {
    id: 'network-architecture',
    title: { en: 'Network Architecture', ru: 'Сетевая архитектура' },
    content: {
      en: (
        <>
          <P><B>Typical home network with PiTun:</B></P>
          <Code>{`Internet
  |
Router (192.168.1.1)
  |
LAN Switch
  |-------- RPi4 (192.168.1.109) — PiTun
  |-------- PC (gateway=192.168.1.109)
  |-------- Phone (gateway=192.168.1.109)
  |-------- Smart TV (gateway=192.168.1.1 — direct)`}</Code>
          <P><B>Traffic flow:</B></P>
          <Ul>
            <li>1. Device sends packet (dst=youtube.com)</li>
            <li>2. Packet arrives at RPi4 (device's gateway)</li>
            <li>3. nftables TPROXY intercepts &rarr; sends to xray</li>
            <li>4. xray sniffs TLS SNI &rarr; sees "youtube.com"</li>
            <li>5. Routing rule: youtube.com &rarr; proxy</li>
            <li>6. xray encrypts and sends through VPN to server</li>
            <li>7. VPN server forwards to youtube.com</li>
            <li>8. Response comes back through VPN &rarr; xray &rarr; device</li>
          </Ul>
          <P><B>Direct traffic (bypassed):</B></P>
          <Ul>
            <li>1. Device sends packet (dst=local-service.ru)</li>
            <li>2. RPi4 &rarr; xray &rarr; routing rule: geoip:ru &rarr; direct</li>
            <li>3. xray sends through "freedom" outbound &rarr; router &rarr; internet</li>
            <li>4. No VPN, no extra latency</li>
          </Ul>
          <P><B>Three proxy endpoints (all share same rules):</B></P>
          <Ul>
            <li>TPROXY :7893 — transparent (change gateway)</li>
            <li>SOCKS5 :1080 — explicit proxy (configure in app)</li>
            <li>HTTP :8080 — for apps without SOCKS5</li>
          </Ul>
          <P><B>RPi4 requirements:</B></P>
          <Ul>
            <li>Static IP (DHCP reservation recommended)</li>
            <li>IP forwarding enabled (<code className="text-gray-400">net.ipv4.ip_forward=1</code>)</li>
            <li>Docker running (containers: backend, frontend, nginx)</li>
            <li>xray-core installed (<code className="text-gray-400">/usr/local/bin/xray</code>)</li>
          </Ul>
        </>
      ),
      ru: (
        <>
          <P><B>Типичная домашняя сеть с PiTun:</B></P>
          <Code>{`Интернет
  |
Роутер (192.168.1.1)
  |
LAN-коммутатор
  |-------- RPi4 (192.168.1.109) — PiTun
  |-------- ПК (шлюз=192.168.1.109)
  |-------- Телефон (шлюз=192.168.1.109)
  |-------- Smart TV (шлюз=192.168.1.1 — напрямую)`}</Code>
          <P><B>Путь трафика:</B></P>
          <Ul>
            <li>1. Устройство отправляет пакет (dst=youtube.com)</li>
            <li>2. Пакет приходит на RPi4 (шлюз устройства)</li>
            <li>3. nftables TPROXY перехватывает &rarr; передаёт в xray</li>
            <li>4. xray анализирует TLS SNI &rarr; видит "youtube.com"</li>
            <li>5. Правило маршрутизации: youtube.com &rarr; proxy</li>
            <li>6. xray шифрует и отправляет через VPN на сервер</li>
            <li>7. VPN-сервер пересылает на youtube.com</li>
            <li>8. Ответ возвращается через VPN &rarr; xray &rarr; устройство</li>
          </Ul>
          <P><B>Прямой трафик (обход):</B></P>
          <Ul>
            <li>1. Устройство отправляет пакет (dst=local-service.ru)</li>
            <li>2. RPi4 &rarr; xray &rarr; правило: geoip:ru &rarr; direct</li>
            <li>3. xray отправляет через "freedom" outbound &rarr; роутер &rarr; интернет</li>
            <li>4. Без VPN, без дополнительной задержки</li>
          </Ul>
          <P><B>Три прокси-эндпоинта (общие правила):</B></P>
          <Ul>
            <li>TPROXY :7893 — прозрачный (смена шлюза)</li>
            <li>SOCKS5 :1080 — явный прокси (настройка в приложении)</li>
            <li>HTTP :8080 — для приложений без поддержки SOCKS5</li>
          </Ul>
          <P><B>Требования к RPi4:</B></P>
          <Ul>
            <li>Статический IP (рекомендуется резервирование DHCP)</li>
            <li>IP-форвардинг включён (<code className="text-gray-400">net.ipv4.ip_forward=1</code>)</li>
            <li>Docker запущен (контейнеры: backend, frontend, nginx)</li>
            <li>xray-core установлен (<code className="text-gray-400">/usr/local/bin/xray</code>)</li>
          </Ul>
        </>
      ),
    },
  },

  /* 19. CLI Commands */
  {
    id: 'cli-commands',
    title: { en: 'CLI Commands', ru: 'CLI-команды' },
    content: {
      en: (
        <>
          <P><B>Password reset:</B></P>
          <Code>docker exec pitun-backend bash /app/scripts/reset-password.sh newpassword</Code>
          <P><B>Docker commands:</B></P>
          <Code>{`# Start
docker compose up -d

# Stop
docker compose down

# Rebuild after code changes
docker compose up --build -d

# View backend logs
docker compose logs -f backend

# View xray logs
docker compose exec backend cat /tmp/xray.log`}</Code>
          <P><B>Debugging:</B></P>
          <Code>{`# Check nftables rules
docker compose exec backend nft list ruleset

# Test node connectivity
docker compose exec backend curl -x socks5://127.0.0.1:1080 https://ifconfig.me

# Check xray process
docker compose exec backend ps aux | grep xray`}</Code>
        </>
      ),
      ru: (
        <>
          <P><B>Сброс пароля:</B></P>
          <Code>docker exec pitun-backend bash /app/scripts/reset-password.sh newpassword</Code>
          <P><B>Docker-команды:</B></P>
          <Code>{`# Запуск
docker compose up -d

# Остановка
docker compose down

# Пересборка после изменений
docker compose up --build -d

# Логи бэкенда
docker compose logs -f backend

# Логи xray
docker compose exec backend cat /tmp/xray.log`}</Code>
          <P><B>Отладка:</B></P>
          <Code>{`# Проверить правила nftables
docker compose exec backend nft list ruleset

# Тест подключения через ноду
docker compose exec backend curl -x socks5://127.0.0.1:1080 https://ifconfig.me

# Проверить процесс xray
docker compose exec backend ps aux | grep xray`}</Code>
        </>
      ),
    },
  },
]

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function KnowledgeBase() {
  const lang = useAppStore((s) => s.lang)
  const [openSections, setOpenSections] = useState<Set<string>>(
    () => new Set(['getting-started']),
  )

  const mainRef = useRef<HTMLDivElement>(null)

  const toggle = useCallback((id: string) => {
    setOpenSections((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const scrollTo = useCallback(
    (id: string) => {
      // make sure section is open
      setOpenSections((prev) => {
        const next = new Set(prev)
        next.add(id)
        return next
      })
      // scroll after state update
      requestAnimationFrame(() => {
        const el = document.getElementById(id)
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    },
    [],
  )

  const expandAll = useCallback(() => {
    setOpenSections(new Set(SECTIONS.map((s) => s.id)))
  }, [])

  const collapseAll = useCallback(() => {
    setOpenSections(new Set())
  }, [])

  return (
    <div className="flex h-full">
      {/* Sidebar TOC */}
      <aside className="hidden lg:flex w-56 flex-col border-r border-gray-800 bg-gray-900/50 overflow-y-auto sticky top-0 h-full shrink-0">
        <div className="px-4 py-4 border-b border-gray-800">
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-100">
            <BookOpen className="h-4 w-4 text-brand-400" />
            {lang === 'en' ? 'Contents' : 'Содержание'}
          </div>
        </div>
        <nav className="flex-1 px-2 py-2 space-y-0.5 overflow-y-auto">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              onClick={() => scrollTo(s.id)}
              className={clsx(
                'w-full text-left rounded-lg px-3 py-1.5 text-xs transition-colors truncate',
                openSections.has(s.id)
                  ? 'text-brand-400 bg-brand-50 dark:bg-brand-500/12'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800',
              )}
            >
              {s.title[lang]}
            </button>
          ))}
        </nav>
        <div className="px-3 py-3 border-t border-gray-800 space-y-1">
          <button
            onClick={expandAll}
            className="w-full text-left rounded-lg px-3 py-1.5 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
          >
            {lang === 'en' ? 'Expand all' : 'Развернуть все'}
          </button>
          <button
            onClick={collapseAll}
            className="w-full text-left rounded-lg px-3 py-1.5 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
          >
            {lang === 'en' ? 'Collapse all' : 'Свернуть все'}
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div ref={mainRef} className="flex-1 overflow-y-auto">
        <div className="p-6 space-y-4">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <BookOpen className="h-5 w-5 text-brand-400" />
              <h1 className="text-xl font-bold text-gray-100">
                {lang === 'en' ? 'Knowledge Base' : 'База знаний'}
              </h1>
            </div>

          </div>

          <p className="text-sm text-gray-500">
            {lang === 'en'
              ? 'Reference documentation for PiTun transparent proxy manager.'
              : 'Справочная документация по менеджеру прозрачного прокси PiTun.'}
          </p>

          {/* Sections */}
          {SECTIONS.map((s) => (
            <Section
              key={s.id}
              id={s.id}
              title={s.title[lang]}
              open={openSections.has(s.id)}
              onToggle={() => toggle(s.id)}
            >
              {s.content[lang]}
            </Section>
          ))}
        </div>
      </div>
    </div>
  )
}
