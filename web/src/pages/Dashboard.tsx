import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Space, Statistic, Table, Tag, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAccount } from '../store'
import type { SendJob, Stats } from '../types'

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

export default function Dashboard() {
  const { accountId, accounts } = useAccount()
  const [stats, setStats] = useState<Stats | null>(null)
  const [jobs, setJobs] = useState<SendJob[]>([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [nextStats, nextJobs] = await Promise.all([
        api.stats(accountId ?? undefined),
        api.jobs(accountId ?? undefined, 12),
      ])
      setStats(nextStats)
      setJobs(nextJobs)
    } catch (error) {
      console.error(error)
    } finally {
      setLoading(false)
    }
  }, [accountId])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <>
      <div className="page-header">
        <div>
          <h2 className="page-title">工作台概览</h2>
          <p className="page-subtitle">
            {accounts.length ? `共 ${accounts.length} 个抖音账号` : '还没有账号，先去「账号」页新建一个'}
          </p>
        </div>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
          刷新
        </Button>
      </div>

      {accounts.length === 0 && (
        <Alert
          type="info"
          showIcon
          message="第一步：新建一个抖音账号"
          description={
            <Space direction="vertical">
              <span>在「账号」页新建账号后点「扫码登录」，浏览器会弹出来，扫码一次即可。</span>
              <span>
                登录态保存在本地浏览器 profile 里，<b>登录地和发送地是同一台机器</b>，
                这是规避风控最有效的一条。
              </span>
            </Space>
          }
          style={{ marginBottom: 16 }}
        />
      )}

      <div className="stat-row">
        <Card>
          <Statistic title="今日任务" value={stats?.total_jobs_today ?? 0} />
        </Card>
        <Card>
          <Statistic title="今日已发送" value={stats?.sent_today ?? 0} />
        </Card>
        <Card>
          <Statistic title="好友总数" value={stats?.friends_total ?? 0} />
        </Card>
        <Card>
          <Statistic title="已勾选" value={stats?.friends_selected ?? 0} />
        </Card>
        <Card>
          <Statistic title="有火花的" value={stats?.friends_with_flame ?? 0} />
        </Card>
        <Card>
          <Statistic title="当日发送上限" value={stats?.daily_cap ?? 0} />
        </Card>
      </div>

      <Card title="最近任务" extra={<Link to="/douyin/jobs">查看全部</Link>}>
        <Table<SendJob>
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={jobs}
          locale={{ emptyText: '还没有任务' }}
          columns={[
            { title: '好友', dataIndex: 'friend_name', width: 140 },
            {
              title: '内容',
              dataIndex: 'message_preview',
              ellipsis: true,
              render: (value: string, row) => (
                <span>
                  <Tag>{row.message_kind === 'text' ? '文字' : row.message_kind === 'image' ? '图片' : '表情'}</Tag>
                  {value}
                </span>
              ),
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
              title: '时间',
              dataIndex: 'actual_at',
              width: 170,
              render: (value: string | null, row) => (
                <Typography.Text type="secondary" className="mono">
                  {value ? value.replace('T', ' ').slice(0, 19) : row.plan_at.replace('T', ' ').slice(0, 19)}
                </Typography.Text>
              ),
            },
          ]}
        />
      </Card>
    </>
  )
}
