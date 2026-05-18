import { useEffect } from 'react'
import { App as AntdApp, ConfigProvider, Layout, Menu, Typography } from 'antd'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  AppstoreOutlined,
  BugOutlined,
  GlobalOutlined,
  KeyOutlined,
  LinkOutlined,
  MailOutlined,
  PlayCircleOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons'
import zhCN from 'antd/locale/zh_CN'

import Pipelines from '@/pages/Pipelines'
import Accounts from '@/pages/Accounts'
import AccessTokens from '@/pages/AccessTokens'
import SubscriptionAccounts from '@/pages/SubscriptionAccounts'
import PaymentLinks from '@/pages/PaymentLinks'
import Emails from '@/pages/Emails'
import Proxies from '@/pages/Proxies'
import Pools from '@/pages/Pools'
import BrowserDebug from '@/pages/BrowserDebug'
import Settings from '@/pages/Settings'

import { darkTheme } from './theme'

const { Sider, Content, Header } = Layout

const MENU = [
  { key: '/pipelines', icon: <PlayCircleOutlined />, label: '任务队列' },
  { key: '/pools', icon: <AppstoreOutlined />, label: '池子 / WorkPools' },
  { key: '/accounts', icon: <UserOutlined />, label: '账号' },
  { key: '/access-tokens', icon: <KeyOutlined />, label: 'Free 号池' },
  { key: '/subscription-accounts', icon: <LinkOutlined />, label: '订阅号池' },
  { key: '/payment-links', icon: <LinkOutlined />, label: '支付长链' },
  { key: '/emails', icon: <MailOutlined />, label: '邮箱' },
  { key: '/proxies', icon: <GlobalOutlined />, label: '代理' },
  { key: '/browser-debug', icon: <BugOutlined />, label: '浏览器调试' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
]

function Shell() {
  const navigate = useNavigate()
  const location = useLocation()
  useEffect(() => {
    document.documentElement.classList.add('dark')
  }, [])

  const selectedKey = MENU.find((item) => location.pathname.startsWith(item.key))?.key || '/pipelines'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={220} theme="dark">
        <div
          style={{
            height: 56,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            fontWeight: 600,
          }}
        >
          <AppstoreOutlined style={{ marginRight: 8 }} />
          ChatGPT Queue
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={MENU}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: 'transparent', padding: '0 24px' }}>
          <Typography.Title level={4} style={{ margin: '12px 0' }}>
            {MENU.find((item) => item.key === selectedKey)?.label || ''}
          </Typography.Title>
        </Header>
        <Content style={{ padding: 24 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/pipelines" replace />} />
            <Route path="/pipelines" element={<Pipelines />} />
            <Route path="/pools" element={<Pools />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/access-tokens" element={<AccessTokens />} />
            <Route path="/subscription-accounts" element={<SubscriptionAccounts />} />
            <Route path="/payment-links" element={<PaymentLinks />} />
            <Route path="/emails" element={<Emails />} />
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
    <ConfigProvider theme={darkTheme} locale={zhCN}>
      <AntdApp>
        <BrowserRouter>
          <Shell />
        </BrowserRouter>
      </AntdApp>
    </ConfigProvider>
  )
}
