import { App as AntdApp, ConfigProvider, Layout, Menu, Typography } from 'antd'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  AppstoreOutlined,
  BugOutlined,
  GlobalOutlined,
  KeyOutlined,
  LinkOutlined,
  MailOutlined,
  PhoneOutlined,
  PlayCircleOutlined,
  SettingOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons'
import zhCN from 'antd/locale/zh_CN'

import Pipelines from '@/pages/Pipelines'
import Jobs from '@/pages/Jobs'
import Accounts from '@/pages/Accounts'
import AccessTokens from '@/pages/AccessTokens'
import SubscriptionAccounts from '@/pages/SubscriptionAccounts'
import PaymentLinks from '@/pages/PaymentLinks'
import Emails from '@/pages/Emails'
import Proxies from '@/pages/Proxies'
import PayPalNumbers from '@/pages/PayPalNumbers'
import Pools from '@/pages/Pools'
import BrowserDebug from '@/pages/BrowserDebug'
import Settings from '@/pages/Settings'

import { lightTheme } from './theme'

const { Sider, Content, Header } = Layout

const MENU = [
  { key: '/pipelines', icon: <PlayCircleOutlined />, label: '任务队列' },
  { key: '/jobs', icon: <UnorderedListOutlined />, label: 'Jobs 追踪' },
  { key: '/pools', icon: <AppstoreOutlined />, label: '池子 / WorkPools' },
  { key: '/accounts', icon: <UserOutlined />, label: '账号' },
  { key: '/access-tokens', icon: <KeyOutlined />, label: 'Free 号池' },
  { key: '/subscription-accounts', icon: <LinkOutlined />, label: '订阅号池' },
  { key: '/payment-links', icon: <LinkOutlined />, label: '支付长链' },
  { key: '/emails', icon: <MailOutlined />, label: '邮箱' },
  { key: '/paypal-numbers', icon: <PhoneOutlined />, label: 'PayPal 号码' },
  { key: '/proxies', icon: <GlobalOutlined />, label: '代理' },
  { key: '/browser-debug', icon: <BugOutlined />, label: '浏览器调试' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
]

function Shell() {
  const navigate = useNavigate()
  const location = useLocation()
  const selectedKey = MENU.find((item) => location.pathname.startsWith(item.key))?.key || '/pipelines'

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
          items={MENU}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 0, padding: '6px 12px', background: 'transparent' }}
        />
      </Sider>
      <Layout style={{ background: 'transparent' }}>
        <Header className="app-header">
          <Typography.Title level={4} style={{ margin: 0, color: '#0f172a' }}>
            {MENU.find((item) => item.key === selectedKey)?.label || ''}
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
            <Route path="/subscription-accounts" element={<SubscriptionAccounts />} />
            <Route path="/payment-links" element={<PaymentLinks />} />
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
