import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp,
  Button,
  Card,
  Input,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { DeleteOutlined, LoginOutlined, LogoutOutlined, PlusOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAccount } from '../store'
import type { Account } from '../types'

const STATUS_META: Record<string, { color: string; text: string }> = {
  active: { color: 'green', text: '已登录' },
  expired: { color: 'red', text: '登录失效' },
  unknown: { color: 'default', text: '未检查' },
  busy: { color: 'blue', text: '使用中' },
}

export default function Accounts() {
  const { message, modal } = AntApp.useApp()
  const { accounts, reloadAccounts } = useAccount()
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)

  const refresh = useCallback(() => void reloadAccounts(), [reloadAccounts])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleCreate = async () => {
    if (!name.trim()) {
      message.warning('给账号起个名字')
      return
    }
    try {
      await api.createAccount(name.trim(), note.trim())
      setName('')
      setNote('')
      await refresh()
      message.success('账号已创建，接着点「扫码登录」')
    } catch (error) {
      message.error(String(error))
    }
  }

  const handleLogin = async (account: Account) => {
    setBusyId(account.id)
    const hide = message.loading(
      `正在打开浏览器，请扫码登录「${account.name}」…（最长 5 分钟）`,
      0,
    )
    try {
      const result = await api.login(account.id)
      hide()
      if (result.logged_in) {
        message.success('登录成功，登录态已保存到本地 profile')
      } else {
        message.warning('超时未完成登录，可以再试一次')
      }
      await refresh()
    } catch (error) {
      hide()
      message.error(String(error))
    } finally {
      setBusyId(null)
    }
  }

  const handleCheck = async (account: Account) => {
    try {
      const result = await api.loginStatus(account.id)
      message.info(result.logged_in ? '登录态有效' : `登录态已失效${result.error ? `：${result.error}` : ''}`)
      await refresh()
    } catch (error) {
      message.error(String(error))
    }
  }

  const handleDelete = async (account: Account) => {
    modal.confirm({
      title: `删除账号「${account.name}」？`,
      content: '该账号的好友、文案、计划和任务记录都会一起删除。浏览器 profile 目录会保留，如需彻底清理请手动删除 data/profiles 下的对应文件夹。',
      okButtonProps: { danger: true },
      onOk: async () => {
        await api.deleteAccount(account.id)
        await refresh()
        message.success('已删除')
      },
    })
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h2 className="page-title">账号</h2>
          <p className="page-subtitle">
            每个账号一个独立的浏览器 profile，登录态互不干扰，也避免抖音看到"一个浏览器多个号"
          </p>
        </div>
      </div>

      <Card title="新建账号" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input
            placeholder="账号名称，例如「我的主号」"
            value={name}
            onChange={(event) => setName(event.target.value)}
            style={{ width: 220 }}
          />
          <Input
            placeholder="备注（可选）"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            style={{ width: 220 }}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            创建
          </Button>
        </Space>
      </Card>

      <Card title="账号列表">
        <Table<Account>
          rowKey="id"
          pagination={false}
          dataSource={accounts}
          locale={{ emptyText: '还没有账号' }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 60 },
            { title: '名称', dataIndex: 'name' },
            { title: '备注', dataIndex: 'note', render: (value: string) => value || '—' },
            {
              title: '登录状态',
              dataIndex: 'status',
              width: 110,
              render: (value: string) => {
                const meta = STATUS_META[value] ?? STATUS_META.unknown
                return <Tag color={meta.color}>{meta.text}</Tag>
              },
            },
            {
              title: '最后登录',
              dataIndex: 'last_login_at',
              width: 170,
              render: (value: string | null) => (
                <Typography.Text type="secondary" className="mono">
                  {value ? value.replace('T', ' ').slice(0, 19) : '—'}
                </Typography.Text>
              ),
            },
            {
              title: '操作',
              width: 260,
              render: (_: unknown, row: Account) => (
                <Space>
                  <Button
                    type="primary"
                    size="small"
                    icon={<LoginOutlined />}
                    loading={busyId === row.id}
                    onClick={() => handleLogin(row)}
                  >
                    扫码登录
                  </Button>
                  <Button size="small" onClick={() => handleCheck(row)}>
                    检查
                  </Button>
                  <Popconfirm title="关闭浏览器会话（不影响登录态）" onConfirm={() => api.logout(row.id)}>
                    <Button size="small" icon={<LogoutOutlined />} />
                  </Popconfirm>
                  <Popconfirm title="删除这个账号？" onConfirm={() => handleDelete(row)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </>
  )
}
