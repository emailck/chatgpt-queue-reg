import { App as AntdApp, ConfigProvider, Layout, Menu, Typography } from 'antd'
import type { ItemType } from 'antd/es/menu/interface'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  AppstoreOutlined,
  BugOutlined,
  ClusterOutlined,
  GlobalOutlined,
  KeyOutlined,
  MailOutlined,
  PhoneOutlined,
  PlayCircleOutlined,
  SettingOutlined,
  TeamOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons'
import zhCN from 'antd/locale/zh_CN'

import Pipelines from '@/pages/Pipelines'
import Jobs from '@/pages/Jobs'
import Accounts from '@/pages/Accounts'
import AccessTokens from '@/pages/AccessTokens'
import Emails from '@/pages/Emails'
import Proxies from '@/pages/Proxies'
import PayPalNumbers from '@/pages/PayPalNumbers'
import Pools from '@/pages/Pools'
import BrowserDebug from '@/pages/BrowserDebug'
import Settings from '@/pages/Settings'

import { lightTheme } from './theme'

const { Sider, Content, Header } = Layout

const MENU: ItemType[] = [
  { key: '/pipelines', icon: <PlayCircleOutlined />, label: '任务队列' },
  { key: '/jobs', icon: <UnorderedListOutlined />, label: 'Jobs 追踪' },
  { key: '/pools', icon: <AppstoreOutlined />, label: 'WorkPools' },
  {
    key: 'account-pools',
    icon: <TeamOutlined />,
    label: '账号池',
    children: [
      { key: '/access-tokens', icon: <KeyOutlined />, label: 'Free 池（已注册 AT）' },
      { key: '/accounts', icon: <UserOutlined />, label: 'Plus 池（sub2api）' },
    ],
  },
  {
    key: 'resource-pools',
    icon: <ClusterOutlined />,
    label: '资源池',
    children: [
      { key: '/emails', icon: <MailOutlined />, label: '邮箱池' },
      { key: '/paypal-numbers', icon: <PhoneOutlined />, label: 'PayPal 号码池' },
      { key: '/proxies', icon: <GlobalOutlined />, label: '代理池' },
    ],
  },
  { key: '/browser-debug', icon: <BugOutlined />, label: '浏览器调试' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
]

const PAGE_TITLES: Record<string, string> = {
  '/pipelines': '任务队列',
  '/jobs': 'Jobs 追踪',
  '/access-tokens': 'Free 池（已注册 AT）',
  '/accounts': 'Plus 池（sub2api）',
  '/pools': 'WorkPools',
  '/emails': '邮箱池',
  '/paypal-numbers': 'PayPal 号码池',
  '/proxies': '代理池',
  '/browser-debug': '浏览器调试',
  '/settings': '设置',
}

const MENU_KEYS = Object.keys(PAGE_TITLES)

function Shell() {
  const navigate = useNavigate()
  const location = useLocation()
  const selectedKey = MENU_KEYS.find((key) => location.pathname.startsWith(key)) || '/pipelines'
  const openKeys = selectedKey === '/access-tokens' || selectedKey === '/accounts'
    ? ['account-pools']
    : ['/emails', '/paypal-numbers', '/proxies'].includes(selectedKey)
      ? ['resource-pools']
      : []

  return (
    <Layout className="app-shell">
      <Sider width={248} className="app-sider">
        <div className="app-brand">
          <AppstoreOutlined className="app-brand-icon" />
          ChatGPT Queue
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          defaultOpenKeys={openKeys}
          items={MENU}
          onClick={({ key }) => { if (String(key).startsWith('/')) navigate(key) }}
          style={{ borderInlineEnd: 0, padding: '6px 12px', background: 'transparent' }}
        />
      </Sider>
      <Layout style={{ background: 'transparent' }}>
        <Header className="app-header">
          <Typography.Title level={4} style={{ margin: 0, color: '#0f172a' }}>
            {PAGE_TITLES[selectedKey] || ''}
          </Typography.Title>
        </Header>
        <Content className="app-content">
          <Routes>
            <Route path="/" element={<Navigate to="/pipelines" replace />} />
            <Route path="/pipelines" element={<Pipelines />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/pools" element={<Pools />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/access-tokens" element={<AccessTokens />} />
            <Route path="/emails" element={<Emails />} />
            <Route path="/paypal-numbers" element={<PayPalNumbers />} />
            <Route path="/proxies" element={<Proxies />} />
            <Route path="/browser-debug" element={<BrowserDebug />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export default function App() {
  return (
    <ConfigProvider theme={lightTheme} locale={zhCN}>
      <AntdApp>
        <BrowserRouter>
          <Shell />
        </BrowserRouter>
      </AntdApp>
    </ConfigProvider>
  )
}
