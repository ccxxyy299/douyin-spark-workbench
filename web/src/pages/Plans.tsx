import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp,
  Button,
  Card,
  Checkbox,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  TimePicker,
  Typography,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlayCircleOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api } from '../api'
import { useAccount } from '../store'
import type { Friend, MessageTemplate, SendPlan } from '../types'

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

function parseDays(value: string): number[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item !== '')
    .map(Number)
    .filter((item) => !Number.isNaN(item))
}

export default function Plans() {
  const { message } = AntApp.useApp()
  const { accountId } = useAccount()
  const [plans, setPlans] = useState<SendPlan[]>([])
  const [friends, setFriends] = useState<Friend[]>([])
  const [templates, setTemplates] = useState<MessageTemplate[]>([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<SendPlan | null>(null)
  const [open, setOpen] = useState(false)
  const [runningId, setRunningId] = useState<number | null>(null)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    if (!accountId) return
    setLoading(true)
    try {
      const [planList, friendList, templateList] = await Promise.all([
        api.plans(accountId),
        api.friends(accountId),
        api.templates(accountId),
      ])
      setPlans(planList)
      setFriends(friendList)
      setTemplates(templateList)
    } catch (error) {
      message.error(String(error))
    } finally {
      setLoading(false)
    }
  }, [accountId, message])

  useEffect(() => {
    void load()
  }, [load])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({
      name: '',
      daily_time: dayjs().hour(9).minute(30),
      jitter_minutes: 15,
      days_of_week: [0, 1, 2, 3, 4, 5, 6],
      enabled: true,
    })
    setOpen(true)
  }

  const openEdit = (plan: SendPlan) => {
    setEditing(plan)
    const [hour, minute] = plan.daily_time.split(':').map(Number)
    form.setFieldsValue({
      name: plan.name,
      daily_time: dayjs().hour(hour ?? 9).minute(minute ?? 30),
      jitter_minutes: plan.jitter_minutes,
      days_of_week: parseDays(plan.days_of_week),
      target_friend_ids: JSON.parse(plan.target_friend_ids || '[]'),
      template_ids: JSON.parse(plan.template_ids || '[]'),
      date_from: plan.date_from ? dayjs(plan.date_from) : null,
      date_to: plan.date_to ? dayjs(plan.date_to) : null,
      enabled: plan.enabled,
    })
    setOpen(true)
  }

  const handleSave = async () => {
    if (!accountId) return
    const values = await form.validateFields()
    const payload = {
      name: values.name ?? '',
      daily_time: values.daily_time ? values.daily_time.format('HH:mm') : '09:30',
      jitter_minutes: values.jitter_minutes ?? 0,
      days_of_week: (values.days_of_week ?? [0, 1, 2, 3, 4, 5, 6]).join(','),
      target_friend_ids: values.target_friend_ids ?? [],
      template_ids: values.template_ids ?? [],
      date_from: values.date_from ? values.date_from.format('YYYY-MM-DD') : '',
      date_to: values.date_to ? values.date_to.format('YYYY-MM-DD') : '',
      enabled: values.enabled ?? true,
    }
    try {
      if (editing) {
        await api.updatePlan(editing.id, payload)
      } else {
        await api.createPlan(accountId, payload)
      }
      setOpen(false)
      await load()
      message.success('已保存')
    } catch (error) {
      message.error(String(error))
    }
  }

  const handleRunNow = async (plan: SendPlan) => {
    setRunningId(plan.id)
    const hide = message.loading(`正在执行「${plan.name || '未命名计划'}」…`, 0)
    try {
      const result = await api.runPlanNow(plan.id)
      hide()
      message.success(`生成 ${result.created} 个任务，执行了 ${result.executed} 个`)
      await load()
    } catch (error) {
      hide()
      message.error(String(error))
    } finally {
      setRunningId(null)
    }
  }

  const selectedFriends = friends.filter((item) => item.selected)

  return (
    <>
      <div className="page-header">
        <div>
          <h2 className="page-title">发送计划</h2>
          <p className="page-subtitle">
            设定发送时间、随机抖动、目标好友和文案来源。抖动是默认值 —— 每天卡着整点发是最明显的自动化特征
          </p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建计划
        </Button>
      </div>

      {friends.length > 0 && selectedFriends.length === 0 && (
        <Typography.Paragraph type="warning">
          还没有勾选任何好友。计划默认对「好友与火花」页里勾选中的好友生效。
        </Typography.Paragraph>
      )}

      <Card>
        <Table<SendPlan>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={plans}
          pagination={false}
          locale={{ emptyText: '还没有计划' }}
          columns={[
            { title: '名称', dataIndex: 'name', width: 160, render: (v: string) => v || '未命名' },
            {
              title: '发送时间',
              dataIndex: 'daily_time',
              width: 150,
              render: (value: string, row) => (
                <span className="mono">
                  {value}
                  {row.jitter_minutes > 0 && (
                    <Typography.Text type="secondary"> +0~{row.jitter_minutes}分</Typography.Text>
                  )}
                </span>
              ),
            },
            {
              title: '周期',
              dataIndex: 'days_of_week',
              width: 130,
              render: (value: string) => {
                const days = parseDays(value)
                if (days.length === 7) return '每天'
                return days.map((day) => WEEKDAYS[day]).join('、')
              },
            },
            {
              title: '目标',
              width: 110,
              render: (_: unknown, row: SendPlan) => {
                const ids = JSON.parse(row.target_friend_ids || '[]')
                return ids.length ? <Tag>{ids.length} 个指定</Tag> : <Tag color="blue">全部勾选</Tag>
              },
            },
            {
              title: '文案',
              width: 110,
              render: (_: unknown, row: SendPlan) => {
                const ids = JSON.parse(row.template_ids || '[]')
                return ids.length ? <Tag>{ids.length} 条指定</Tag> : <Tag color="blue">全部模板</Tag>
              },
            },
            {
              title: '启用',
              dataIndex: 'enabled',
              width: 70,
              render: (value: boolean) => (value ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
            },
            {
              title: '最近生成',
              dataIndex: 'last_plan_date',
              width: 120,
              render: (value: string) => <span className="mono">{value || '—'}</span>,
            },
            {
              title: '操作',
              width: 170,
              render: (_: unknown, row: SendPlan) => (
                <Space>
                  <Popconfirm
                    title="立即执行会真实发出私信"
                    description="确认现在就给目标好友发送？"
                    onConfirm={() => handleRunNow(row)}
                  >
                    <Button
                      size="small"
                      type="primary"
                      icon={<PlayCircleOutlined />}
                      loading={runningId === row.id}
                    >
                      立即执行
                    </Button>
                  </Popconfirm>
                  <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)} />
                  <Popconfirm
                    title="删除这个计划？"
                    onConfirm={async () => {
                      await api.deletePlan(row.id)
                      await load()
                    }}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={editing ? '编辑计划' : '新建计划'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={handleSave}
        okText="保存"
        width={620}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="计划名称">
            <Input placeholder="例如：每天早上续火花" />
          </Form.Item>
          <Space size="large" align="start">
            <Form.Item name="daily_time" label="发送时间">
              <TimePicker format="HH:mm" minuteStep={5} style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="jitter_minutes" label="随机抖动（分钟）" extra="实际时间会晚 0~N 分钟">
              <InputNumber min={0} max={240} style={{ width: 120 }} />
            </Form.Item>
          </Space>
          <Form.Item name="days_of_week" label="重复">
            <Checkbox.Group options={WEEKDAYS.map((label, index) => ({ label, value: index }))} />
          </Form.Item>
          <Form.Item
            name="target_friend_ids"
            label="目标好友"
            extra="留空 = 对「好友与火花」页里勾选中的所有好友生效"
          >
            <Select
              mode="multiple"
              allowClear
              placeholder="全部勾选好友"
              style={{ width: '100%' }}
              optionFilterProp="label"
              options={friends.map((item) => ({
                value: item.id,
                label: `${item.display_name}${item.flame_days ? ` 🔥${item.flame_days}` : ''}`,
              }))}
            />
          </Form.Item>
          <Form.Item name="template_ids" label="文案来源" extra="留空 = 从所有启用的模板里随机挑">
            <Select
              mode="multiple"
              allowClear
              placeholder="全部模板"
              style={{ width: '100%' }}
              options={templates.map((item) => ({ value: item.id, label: item.name || `模板 ${item.id}` }))}
            />
          </Form.Item>
          <Space size="large">
            <Form.Item name="date_from" label="生效起">
              <DatePicker />
            </Form.Item>
            <Form.Item name="date_to" label="生效止">
              <DatePicker />
            </Form.Item>
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </>
  )
}
