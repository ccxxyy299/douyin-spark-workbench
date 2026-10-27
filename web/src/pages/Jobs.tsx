import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Segmented, Space, Table, Tabs, Tag, Tooltip, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAccount } from '../store'
import type { SendJob, SendLog } from '../types'

const STATE_COLOR: Record<string, string> = {
  sent: 'green',
  pending: 'blue',
  running: 'processing',
  failed: 'red',
  skipped: 'default',
  needs_human: 'orange',
}

const STATE_TEXT: Record<string, string> = {
  sent: '已发送',
  pending: '待发送',
  running: '发送中',
  failed: '失败',
  skipped: '已跳过',
  needs_human: '待人工',
}

const KIND_TEXT: Record<string, string> = {
  text: '文字',
  image: '图片',
  sticker: '表情',
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}

export default function Jobs() {
  const { accountId } = useAccount()
  const [jobs, setJobs] = useState<SendJob[]>([])
  const [logs, setLogs] = useState<SendLog[]>([])
  const [loading, setLoading] = useState(false)
  const [stateFilter, setStateFilter] = useState<string>('全部')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [jobList, logList] = await Promise.all([
        api.jobs(accountId ?? undefined, 300),
        api.logs(accountId ?? undefined, 300),
      ])
      setJobs(jobList)
      setLogs(logList)
    } catch (error) {
      console.error(error)
    } finally {
      setLoading(false)
    }
  }, [accountId])

  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(), 5000)
    return () => clearInterval(timer)
  }, [load])

  const filtered =
    stateFilter === '全部' ? jobs : jobs.filter((job) => job.state === stateFilter)

  const counts = (state: string) => jobs.filter((job) => job.state === state).length

  return (
    <>
      <div className="page-header">
        <div>
          <h2 className="page-title">任务记录</h2>
          <p className="page-subtitle">
            每 5 秒自动刷新。失败的任务会带截图，点开能看到当时页面是什么样子
          </p>
        </div>
        <Space>
          <Segmented
            value={stateFilter}
            onChange={(value) => setStateFilter(value as string)}
            options={[
              { label: `全部 ${jobs.length}`, value: '全部' },
              { label: `已发送 ${counts('sent')}`, value: 'sent' },
              { label: `待发送 ${counts('pending')}`, value: 'pending' },
              { label: `失败 ${counts('failed')}`, value: 'failed' },
              { label: `待人工 ${counts('needs_human')}`, value: 'needs_human' },
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      <Card>
        <Tabs
          items={[
            {
              key: 'jobs',
              label: '任务',
              children: (
                <Table<SendJob>
                  rowKey="id"
                  size="small"
                  loading={loading}
                  dataSource={filtered}
                  pagination={{ pageSize: 20, showSizeChanger: true }}
                  locale={{ emptyText: '还没有任务' }}
                  columns={[
                    { title: 'ID', dataIndex: 'id', width: 60 },
                    { title: '好友', dataIndex: 'friend_name', width: 120 },
                    {
                      title: '类型',
                      dataIndex: 'message_kind',
                      width: 70,
                      render: (value: string) => <Tag>{KIND_TEXT[value] ?? value}</Tag>,
                    },
                    {
                      title: '内容',
                      dataIndex: 'message_preview',
                      ellipsis: true,
                      render: (value: string) => value || '—',
                    },
                    {
                      title: '状态',
                      dataIndex: 'state',
                      width: 100,
                      render: (value: string) => (
                        <Tag color={STATE_COLOR[value] ?? 'default'}>{STATE_TEXT[value] ?? value}</Tag>
                      ),
                    },
                    {
                      title: '计划时间',
                      dataIndex: 'plan_at',
                      width: 165,
                      render: (value: string) => <span className="mono">{formatTime(value)}</span>,
                    },
                    {
                      title: '实际发送',
                      dataIndex: 'actual_at',
                      width: 165,
                      render: (value: string | null) => <span className="mono">{formatTime(value)}</span>,
                    },
                    {
                      title: '尝试',
                      dataIndex: 'attempt',
                      width: 60,
                    },
                    {
                      title: '备注',
                      dataIndex: 'error',
                      width: 200,
                      ellipsis: true,
                      render: (value: string, row: SendJob) =>
                        value ? (
                          <Tooltip title={value}>
                            <Typography.Text type="danger">{value}</Typography.Text>
                          </Tooltip>
                        ) : row.screenshot ? (
                          <a
                            href={`/api/douyin/artifacts/${row.screenshot.split(/[\\/]/).pop()}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            看截图
                          </a>
                        ) : (
                          '—'
                        ),
                    },
                  ]}
                />
              ),
            },
            {
              key: 'logs',
              label: '日志',
              children: (
                <Table<SendLog>
                  rowKey="id"
                  size="small"
                  loading={loading}
                  dataSource={logs}
                  pagination={{ pageSize: 20 }}
                  locale={{ emptyText: '还没有日志' }}
                  columns={[
                    {
                      title: '级别',
                      dataIndex: 'level',
                      width: 80,
                      render: (value: string) => (
                        <Tag color={value === 'error' ? 'red' : 'default'}>{value}</Tag>
                      ),
                    },
                    { title: '内容', dataIndex: 'message' },
                    {
                      title: '时间',
                      dataIndex: 'created_at',
                      width: 165,
                      render: (value: string) => <span className="mono">{formatTime(value)}</span>,
                    },
                  ]}
                />
              ),
            },
          ]}
        />
      </Card>
    </>
  )
}
