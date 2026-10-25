import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  App as AntApp,
  Button,
  Card,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { ReloadOutlined, SendOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAccount } from '../store'
import type { Friend, MessageTemplate } from '../types'

const TEMPLATE_KIND_TEXT: Record<string, string> = {
  text: '文字',
  image: '图片',
  sticker: '原生表情',
}

export default function Friends() {
  const { message } = AntApp.useApp()
  const { accountId } = useAccount()
  const [friends, setFriends] = useState<Friend[]>([])
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [groupFilter, setGroupFilter] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<number[]>([])

  // 直接发送
  const [templates, setTemplates] = useState<MessageTemplate[]>([])
  const [sendOpen, setSendOpen] = useState(false)
  const [sending, setSending] = useState(false)
  const [sendFriendIds, setSendFriendIds] = useState<number[]>([])
  const [sendTemplateIds, setSendTemplateIds] = useState<number[]>([])

  const load = useCallback(async () => {
    if (!accountId) return
    setLoading(true)
    try {
      const list = await api.friends(accountId)
      setFriends(list)
      setSelectedIds(list.filter((item) => item.selected).map((item) => item.id))
    } catch (error) {
      message.error(String(error))
    } finally {
      setLoading(false)
    }
  }, [accountId, message])

  useEffect(() => {
    void load()
  }, [load])

  const handleSync = async () => {
    if (!accountId) {
      message.warning('先在右上角选一个账号')
      return
    }
    setSyncing(true)
    const hide = message.loading('正在打开抖音私信页扫描好友与火花天数…', 0)
    try {
      const result = await api.syncFriends(accountId)
      hide()
      message.success(
        `共 ${result.total} 个会话，新增 ${result.added}，更新 ${result.updated}`,
      )
      await load()
    } catch (error) {
      hide()
      message.error(String(error))
    } finally {
      setSyncing(false)
    }
  }

  // 复选框 = 勾选状态（落库）。所有入口都同时维护 friends.selected 和 selectedIds，
  // 保证"已勾选 N/M"计数、行按钮文案和复选框三者一致。
  const applySelection = (nextIds: number[]) => {
    setSelectedIds(nextIds)
    setFriends((current) => current.map((item) => ({ ...item, selected: nextIds.includes(item.id) })))
  }

  const persistSelection = async (nextIds: number[]) => {
    if (!accountId) return
    const currentIds = friends.filter((item) => item.selected).map((item) => item.id)
    const added = nextIds.filter((id) => !currentIds.includes(id))
    const removed = currentIds.filter((id) => !nextIds.includes(id))
    try {
      if (added.length) await api.setSelection(accountId, added, true)
      if (removed.length) await api.setSelection(accountId, removed, false)
      applySelection(nextIds)
    } catch (error) {
      message.error(String(error))
    }
  }

  const patchFriend = async (friend: Friend, payload: Partial<Friend>) => {
    try {
      await api.updateFriend(friend.id, payload)
      setFriends((current) =>
        current.map((item) => (item.id === friend.id ? { ...item, ...payload } : item)),
      )
      const nextSelected = payload.selected
      if (nextSelected !== undefined) {
        setSelectedIds((current) =>
          nextSelected
            ? [...new Set([...current, friend.id])]
            : current.filter((id) => id !== friend.id),
        )
      }
    } catch (error) {
      message.error(String(error))
    }
  }

  const setAll = async (selected: boolean) => {
    if (!accountId) return
    const ids = filtered.map((item) => item.id)
    const currentIds = friends.filter((item) => item.selected).map((item) => item.id)
    try {
      await api.setSelection(accountId, ids, selected)
      applySelection(
        selected
          ? [...new Set([...currentIds, ...ids])]
          : currentIds.filter((id) => !ids.includes(id)),
      )
      message.success(selected ? `已勾选 ${ids.length} 个好友` : '已取消勾选')
    } catch (error) {
      message.error(String(error))
    }
  }

  const groups = useMemo(() => {
    const set = new Set<string>()
    friends.forEach((item) => {
      if (item.group_tag) set.add(item.group_tag)
    })
    return [...set]
  }, [friends])

  const selectedFriendCount = friends.filter((item) => item.selected).length

  const openSendModal = async () => {
    if (!accountId) {
      message.warning('先在右上角选一个账号')
      return
    }
    setSendFriendIds([])
    setSendTemplateIds([])
    setSendOpen(true)
    try {
      setTemplates(await api.templates(accountId))
    } catch (error) {
      message.error(String(error))
    }
  }

  const handleDirectSend = async () => {
    if (!accountId) return
    setSending(true)
    const hide = message.loading('正在发送…（每个好友之间会稍作停顿）', 0)
    try {
      const result = await api.sendNow(accountId, sendFriendIds, sendTemplateIds)
      hide()
      if (result.created === 0) {
        message.warning('没有新任务：目标好友今天都已经发过（或没有可用模板），详见任务记录')
      } else {
        message.success(`已生成 ${result.created} 个任务并立即执行，结果见「任务记录」页`)
      }
      setSendOpen(false)
      await load()
    } catch (error) {
      hide()
      message.error(String(error))
    } finally {
      setSending(false)
    }
  }


  const filtered = useMemo(() => {
    return friends.filter((item) => {
      if (groupFilter && item.group_tag !== groupFilter) return false
      if (keyword && !item.display_name.includes(keyword)) return false
      return true
    })
  }, [friends, groupFilter, keyword])

  return (
    <>
      <div className="page-header">
        <div>
          <h2 className="page-title">好友与火花</h2>
          <p className="page-subtitle">
            扫描抖音私信页左侧会话列表，读取每个好友的火花徽标；勾选要续火花的好友。
            橙色 = 平台上火花正常，灰色 = 平台上还没续上/临期（以最近一次扫描为准，在手机上互发后要点「刷新好友列表」才会变）
          </p>
        </div>
      </div>

      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
          <Input.Search
            placeholder="搜索好友"
            allowClear
            style={{ width: 200 }}
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <Select
            allowClear
            placeholder="全部分组"
            style={{ width: 160 }}
            value={groupFilter}
            onChange={setGroupFilter}
            options={groups.map((group) => ({ value: group, label: group }))}
          />
          <Button onClick={() => setAll(true)}>全选当前</Button>
          <Button onClick={() => setAll(false)}>取消全选</Button>
          <Typography.Text type="secondary">
            已勾选 <b>{friends.filter((item) => item.selected).length}</b> / {friends.length}
          </Typography.Text>
          <div style={{ marginLeft: 'auto' }}>
            <Space>
              <Button type="primary" icon={<SendOutlined />} onClick={openSendModal}>
                直接发送
              </Button>
              <Button icon={<ReloadOutlined />} loading={syncing} onClick={handleSync}>
                刷新好友列表
              </Button>
            </Space>
          </div>
        </div>
      </Card>

      <Card>
        <Table<Friend>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={filtered}
          pagination={{ pageSize: 20, showSizeChanger: true }}
          locale={{ emptyText: '还没有数据，点右上角「刷新好友列表」扫描一次' }}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => void persistSelection(keys as number[]),
          }}
          columns={[
            {
              title: '好友',
              dataIndex: 'display_name',
              width: 180,
              render: (value: string, row) => (
                <Space direction="vertical" size={0}>
                  <span>{value}</span>
                  {row.consecutive_failures > 0 && (
                    <Typography.Text type="danger" className="mono">
                      连续失败 {row.consecutive_failures} 次
                    </Typography.Text>
                  )}
                </Space>
              ),
            },
            {
              title: '火花',
              dataIndex: 'flame_days',
              width: 120,
              sorter: (a, b) => a.flame_days - b.flame_days,
              render: (_: unknown, row: Friend) => {
                const raw = (row.flame_text || '').trim()
                const numeric = raw.replace(/[\s🔥]/g, '')
                // 抖音原始文案里除了数字还有别的字（比如"还剩N天"）就原样照搬
                const label =
                  row.flame_days > 0 && /^\d+$/.test(numeric)
                    ? `🔥 ${row.flame_days}`
                    : raw || (row.flame_days > 0 ? `🔥 ${row.flame_days}` : '')
                if (!label) return <Typography.Text type="secondary">未读到</Typography.Text>
                const scannedAt = row.flame_updated_at
                  ? `（${row.flame_updated_at.replace('T', ' ').slice(0, 16)} 扫描）`
                  : ''
                return (
                  <Tooltip title={`${raw || label}${scannedAt}`}>
                    {row.flame_state === 'gray' ? (
                      <Tag style={{ opacity: 0.65 }}>{label}</Tag>
                    ) : (
                      <Tag color="orange">{label}</Tag>
                    )}
                  </Tooltip>
                )
              },
            },
            {
              title: '分组',
              dataIndex: 'group_tag',
              width: 150,
              render: (value: string, row) => (
                <Select
                  size="small"
                  allowClear
                  placeholder="未分组"
                  style={{ width: '100%' }}
                  value={value || undefined}
                  onChange={(next) => patchFriend(row, { group_tag: next ?? '' })}
                  options={groups.map((group) => ({ value: group, label: group }))}
                  mode="tags"
                  maxTagCount={1}
                />
              ),
            },
            {
              title: '备注',
              dataIndex: 'note',
              width: 160,
              render: (value: string, row) => (
                <Input
                  size="small"
                  placeholder="备注"
                  defaultValue={value}
                  onBlur={(event) => {
                    if (event.target.value !== value) {
                      void patchFriend(row, { note: event.target.value })
                    }
                  }}
                />
              ),
            },
            {
              title: '今天已发',
              width: 100,
              render: (_: unknown, row) =>
                row.sent_today ? (
                  <Tooltip title="这个工具今天成功发过一条（手动在抖音里发的不算）">
                    <Tag color="green">已发</Tag>
                  </Tooltip>
                ) : (
                  <Tag>未发</Tag>
                ),
            },
            {
              title: '操作',
              width: 120,
              render: (_: unknown, row) => (
                <Popconfirm
                  title="确认这个好友今天的火花已经续过了？"
                  description="勾上之后计划会自动跳过今天已发送的好友。"
                  onConfirm={() => patchFriend(row, { selected: !row.selected })}
                >
                  <Button size="small" type={row.selected ? 'default' : 'primary'}>
                    {row.selected ? '取消勾选' : '勾选续火花'}
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title="直接发送"
        open={sendOpen}
        onCancel={() => setSendOpen(false)}
        onOk={handleDirectSend}
        okText="立即发送"
        okButtonProps={{ danger: true, loading: sending }}
        width={560}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Typography.Text>
            立即给好友各发一条消息，不经过定时计划。默认发给勾选中的{' '}
            <b>{selectedFriendCount}</b> 个好友；今天已经发过的会被自动跳过。
          </Typography.Text>
          <Select
            mode="multiple"
            allowClear
            style={{ width: '100%' }}
            placeholder={`目标好友（留空 = 全部勾选中的 ${selectedFriendCount} 人）`}
            value={sendFriendIds}
            onChange={setSendFriendIds}
            optionFilterProp="label"
            options={friends.map((item) => ({
              value: item.id,
              label: `${item.display_name}${item.flame_days ? ` 🔥${item.flame_days}` : ''}`,
            }))}
          />
          <Select
            mode="multiple"
            allowClear
            style={{ width: '100%' }}
            placeholder="文案来源（留空 = 所有启用模板随机挑）"
            value={sendTemplateIds}
            onChange={setSendTemplateIds}
            optionFilterProp="label"
            options={templates.map((item) => ({
              value: item.id,
              label: `${item.name || '未命名'}（${TEMPLATE_KIND_TEXT[item.kind] ?? item.kind}）`,
            }))}
          />
          <Typography.Text type="secondary">
            执行明细（成功 / 失败 / 截图）在「任务记录」页查看。每个好友每天最多一条，
            当日总量上限同样生效。
          </Typography.Text>
        </Space>
      </Modal>
    </>
  )
}
