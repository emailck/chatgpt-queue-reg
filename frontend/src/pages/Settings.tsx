import { Button, Space, Typography } from 'antd'
import { AppstoreOutlined, MailOutlined, PlayCircleOutlined, SettingOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { ActionCard, EntityCard, KeyValue, KeyValueGrid, PageScaffold, SummaryGrid, StatCard } from '@/components/ui/CardPrimitives'

const { Text } = Typography

export default function Settings() {
  const navigate = useNavigate()

  return (
    <PageScaffold
      title="设置"
      description="这里不再堆 WorkPool / ResourcePool 表单；设置页只说明边界并提供跳转，具体配置跟随对应池子卡片。"
      actions={<Button icon={<AppstoreOutlined />} type="primary" onClick={() => navigate('/pools')}>去池子控制台</Button>}
    >
      <SummaryGrid>
        <StatCard label="Pipeline" value="orchestration" hint="count / full_chain / stop_after" tone="primary" />
        <StatCard label="WorkPool" value="stage config" hint="每个 stage 自己维护默认行为" tone="info" />
        <StatCard label="ResourcePool" value="resource config" hint="邮箱/代理/卡/短信资源边界" tone="success" />
        <StatCard label="Popup" value="card modal" hint="统一居中弹出卡片" tone="warning" />
      </SummaryGrid>

      <ActionCard
        title="配置入口收敛"
        description="任务创建页面不能传支付代理 region、OAuth 接码、注册代理等模块参数；这些参数都从对应 WorkPool / ResourcePool 卡片进入配置。"
        actions={<Button icon={<SettingOutlined />} onClick={() => navigate('/pools')}>打开池子配置</Button>}
      />

      <div className="entity-grid">
        <EntityCard
          title="Pipeline creation boundary"
          subtitle="任务创建只表达编排意图"
          tone="primary"
          status={<PlayCircleOutlined />}
          actions={<Button size="small" onClick={() => navigate('/pipelines')}>创建任务</Button>}
        >
          <KeyValueGrid>
            <KeyValue label="允许" value={<Text>count / preset: full_chain / stop_after</Text>} />
            <KeyValue label="禁止" value={<Text>proxy region / sms project / payment card config</Text>} />
          </KeyValueGrid>
        </EntityCard>

        <EntityCard
          title="WorkPool config boundary"
          subtitle="register / payment_link / payment / oauth_codex / rt_keepalive"
          tone="info"
          status={<SettingOutlined />}
          actions={<Button size="small" onClick={() => navigate('/pools')}>配置 WorkPool</Button>}
        >
          <KeyValueGrid>
            <KeyValue label="register" value="注册代理 region、注册并发" />
            <KeyValue label="payment" value="支付代理 region、PayPal 账号、PayPal 号码池、Stripe/Captcha 参数" />
            <KeyValue label="oauth_codex" value="add-phone 接码配置" />
            <KeyValue label="rt_keepalive" value="sub2api 上传/状态同步配置" />
          </KeyValueGrid>
        </EntityCard>

        <EntityCard
          title="ResourcePool data boundary"
          subtitle="资源数据不放到 /settings"
          tone="success"
          status={<AppstoreOutlined />}
          actions={<Space><Button size="small" onClick={() => navigate('/emails')}>邮箱</Button><Button size="small" onClick={() => navigate('/paypal-numbers')}>PayPal 号码</Button><Button size="small" onClick={() => navigate('/proxies')}>代理</Button></Space>}
        >
          <KeyValueGrid>
            <KeyValue label="email_pool" value="邮箱账号与邮件记录" />
            <KeyValue label="proxy_pool" value="代理 URL、region、启用状态" />
            <KeyValue label="card_pool" value="付款卡资源状态" />
            <KeyValue label="paypal_number_pool" value="PayPal 手机号与 smsurl 一次性资源" />
            <KeyValue label="sms_pool" value="短信项目与 provider 凭据" />
          </KeyValueGrid>
        </EntityCard>

        <EntityCard
          title="Debug and evidence"
          subtitle="浏览器调试与 HAR 采集"
          tone="warning"
          status={<MailOutlined />}
          actions={<Button size="small" onClick={() => navigate('/browser-debug')}>调试工作台</Button>}
        >
          <KeyValueGrid>
            <KeyValue label="身份注入" value="cookies / localStorage / UA / fingerprint" />
            <KeyValue label="日志" value="原始 transcript 弹出卡片" />
          </KeyValueGrid>
        </EntityCard>
      </div>
    </PageScaffold>
  )
}
