import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Layout, Menu, Select, Space, Tag, Typography } from 'antd'
import { FireOutlined } from '@ant-design/icons'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { AccountProvider, useAccount } from './store'
import { api } from './api'
import type { ModuleSpec } from './types'
import Dashboard from './pages/Dashboard'
import Accounts from './pages/Accounts'
import Friends from './pages/Friends'
import Templates from './pages/Templates'
import Plans from './pages/Plans'
import Jobs from './pages/Jobs'

const { Header, Sider, Content } = Layout

const MODULE_ICON: Record<string, ReactNode> = {
  'douyin-spark': <FireOutlined />,
}

type NavItem = {
  key: string
  icon: ReactNode
  label: string
  children?: { key: string; label: string }[]
}

function Shell() {
  const [modules, setModules] = useState<ModuleSpec[]>([])
  const { accounts, accountId, setAccountId, account } = useAccount()
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    api.modules().then(setModules).catch(() => setModules([]))
  }, [])

  // 模块列表是异步拿到的，defaultOpenKeys 只在挂载那一刻生效，
  // 所以这里用受控 openKeys，拿到模块后再展开。
  const [openKeys, setOpenKeys] = useState<string[]>([])
  useEffect(() => {
    setOpenKeys((prev) => [...new Set([...prev, ...modules.map((module) => module.route_prefix)])])
  }, [modules])

  const items: NavItem[] = [
    { key: '/', icon: <span>📊</span>, label: '工作台概览' },
    ...modules.map((module) => ({
      key: module.route_prefix,
      icon: MODULE_ICON[module.id] ?? <span>•</span>,
      label: module.name,
      children: module.nav.map((item) => ({
        key: `${module.route_prefix}/${item.key}`,
        label: item.label,
      })),
    })),
  ]

  const selected =
    location.pathname === '/'
      ? '/'
      : items
          .flatMap((item) => (item.children ? item.children : [item]))
          .find((item) => item.key === location.pathname)?.key ?? location.pathname

  return (
    <Layout className="app-shell">
      <Sider width={216} theme="dark">
        <div className="app-logo">
          <FireOutlined /> 个人工作台
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selected]}
          openKeys={openKeys}
          onOpenChange={(keys) => setOpenKeys(keys)}
          items={items}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff',
            padding: '0 20px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #f0f0f0',
          }}
        >
          <Typography.Text type="secondary">当前账号</Typography.Text>
          <Space size="middle">
            <Select
              style={{ minWidth: 200 }}
              placeholder="选择抖音账号"
              value={accountId ?? undefined}
              onChange={(value) => setAccountId(value)}
              options={accounts.map((item) => ({
                value: item.id,
                label: (
                  <Space size={6}>
                    <span>{item.name}</span>
                    <Tag
                      color={
                        item.status === 'active'
                          ? 'green'
                          : item.status === 'expired'
                            ? 'red'
                            : 'default'
                      }
                    >
                      {item.status}
                    </Tag>
                  </Space>
                ),
              }))}
              notFoundContent="还没有账号，去「账号」页新建"
            />
            {account?.status === 'expired' && <Tag color="red">登录态已失效</Tag>}
          </Space>
        </Header>
        <Content className="app-content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/douyin/accounts" element={<Accounts />} />
            <Route path="/douyin/friends" element={<Friends />} />
            <Route path="/douyin/templates" element={<Templates />} />
            <Route path="/douyin/plans" element={<Plans />} />
            <Route path="/douyin/jobs" element={<Jobs />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export default function App() {
  return (
    <AccountProvider>
      <Shell />
    </AccountProvider>
  )
}
