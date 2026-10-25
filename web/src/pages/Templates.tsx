import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, ScissorOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAccount } from '../store'
import type { MessageTemplate, StickerItem, TemplateKind } from '../types'

const KIND_TEXT: Record<TemplateKind, string> = {
  text: '文字',
  image: '图片',
  sticker: '原生表情',
}

export default function Templates() {
  const { message } = AntApp.useApp()
  const { accountId } = useAccount()
  const [templates, setTemplates] = useState<MessageTemplate[]>([])
  const [stickers, setStickers] = useState<StickerItem[]>([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<MessageTemplate | null>(null)
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<TemplateKind>('text')
  const [form] = Form.useForm()

  // 表情采集
  const [pickerOpen, setPickerOpen] = useState(false)
  const [collecting, setCollecting] = useState(false)
  const [catalog, setCatalog] = useState<StickerItem[]>([])

  const load = useCallback(async () => {
    if (!accountId) return
    setLoading(true)
    try {
      const [list, stickerList] = await Promise.all([
        api.templates(accountId),
        api.stickers(accountId),
      ])
      setTemplates(list)
      setStickers(stickerList)
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
    setKind('text')
    form.resetFields()
    form.setFieldsValue({ kind: 'text', enabled: true, weight: 1 })
    setOpen(true)
  }

  const openEdit = (template: MessageTemplate) => {
    setEditing(template)
    setKind(template.kind)
    form.setFieldsValue({
      name: template.name,
      kind: template.kind,
      content: template.content,
      sticker_name: template.sticker_name,
      sticker_category: template.sticker_category,
      sticker_fallback_index: template.sticker_fallback_index ?? undefined,
      enabled: template.enabled,
      weight: template.weight,
    })
    setOpen(true)
  }

  const handleSave = async () => {
    if (!accountId) return
    const values = await form.validateFields()
    const payload = {
      name: values.name ?? '',
      kind: values.kind,
      content: values.content ?? '',
      sticker_name: values.sticker_name ?? '',
      sticker_category: values.sticker_category ?? '',
      sticker_fallback_index: values.sticker_fallback_index ?? null,
      enabled: values.enabled ?? true,
      weight: values.weight ?? 1,
    }
    try {
      if (editing) {
        await api.updateTemplate(editing.id, payload)
      } else {
        await api.createTemplate(accountId, payload)
      }
      setOpen(false)
      await load()
      message.success('已保存')
    } catch (error) {
      message.error(String(error))
    }
  }

  const handleCollect = async () => {
    if (!accountId) {
      message.warning('先在右上角选一个账号')
      return
    }
    setCollecting(true)
    const hide = message.loading('正在打开表情面板采集…', 0)
    try {
      const result = await api.collectStickers(accountId)
      hide()
      setCatalog(result.items)
      setPickerOpen(true)
      message.success(`采集到 ${result.collected} 个表情，其中 ${result.new} 个是新的`)
      await load()
    } catch (error) {
      hide()
      message.error(String(error))
    } finally {
      setCollecting(false)
    }
  }

  const pickSticker = (sticker: StickerItem) => {
    form.setFieldsValue({
      kind: 'sticker',
      sticker_name: sticker.name,
      sticker_category: sticker.category,
      sticker_fallback_index: sticker.fallback_index,
      content: sticker.name,
      name: form.getFieldValue('name') || sticker.name,
    })
    setKind('sticker')
    setPickerOpen(false)
    setOpen(true)
  }

  const uploadProps = {
    accept: 'image/png,image/jpeg,image/gif,image/webp',
    showUploadList: false,
    customRequest: async (options: any) => {
      const file = options.file as File
      const body = new FormData()
      body.append('file', file)
      try {
        const response = await fetch('/api/douyin/templates/upload-image', { method: 'POST', body })
        const result = await response.json()
        if (!response.ok) throw new Error(result?.detail ?? '上传失败')
        form.setFieldsValue({ content: result.path })
        options.onSuccess?.(result)
        message.success('图片已上传')
      } catch (error) {
        options.onError?.(error as Error)
        message.error(String(error))
      }
    },
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h2 className="page-title">文案与表情</h2>
          <p className="page-subtitle">
            计划从这里的模板池里随机挑一条。轮换机制会避免同一个好友连着收到同一条文案
          </p>
        </div>
        <Space>
          <Button icon={<ScissorOutlined />} loading={collecting} onClick={handleCollect}>
            采集抖音表情
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建模板
          </Button>
        </Space>
      </div>

      <Card>
        <Table<MessageTemplate>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={templates}
          pagination={false}
          locale={{ emptyText: '还没有模板，先建一条文字文案' }}
          columns={[
            { title: '名称', dataIndex: 'name', width: 180 },
            {
              title: '类型',
              dataIndex: 'kind',
              width: 100,
              render: (value: TemplateKind) => <Tag>{KIND_TEXT[value] ?? value}</Tag>,
            },
            {
              title: '内容',
              ellipsis: true,
              render: (_: unknown, row: MessageTemplate) => {
                if (row.kind === 'sticker') {
                  return (
                    <Space>
                      {row.sticker_preview && (
                        <img
                          src={row.sticker_preview}
                          alt=""
                          style={{ width: 28, height: 28, objectFit: 'contain' }}
                        />
                      )}
                      <span>{row.sticker_name}</span>
                      {row.sticker_category && (
                        <Typography.Text type="secondary">· {row.sticker_category}</Typography.Text>
                      )}
                    </Space>
                  )
                }
                if (row.kind === 'image') {
                  return <span className="mono">{row.content}</span>
                }
                return row.content
              },
            },
            {
              title: '权重',
              dataIndex: 'weight',
              width: 70,
            },
            {
              title: '启用',
              dataIndex: 'enabled',
              width: 70,
              render: (value: boolean) => (value ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
            },
            {
              title: '操作',
              width: 110,
              render: (_: unknown, row: MessageTemplate) => (
                <Space>
                  <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)} />
                  <Popconfirm
                    title="删除这个模板？"
                    onConfirm={async () => {
                      await api.deleteTemplate(row.id)
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

      {/* 模板编辑 */}
      <Modal
        title={editing ? '编辑模板' : '新建模板'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={handleSave}
        okText="保存"
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称">
            <Input placeholder="例如：日常问候" />
          </Form.Item>
          <Form.Item name="kind" label="类型">
            <Radio.Group
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              optionType="button"
              buttonStyle="solid"
              options={[
                { value: 'text', label: '文字' },
                { value: 'image', label: '图片' },
                { value: 'sticker', label: '原生表情' },
              ]}
            />
          </Form.Item>

          {kind === 'text' && (
            <Form.Item
              name="content"
              label="文字内容"
              extra="支持变量：{昵称} {日期} {星期} {时间}"
            >
              <Input.TextArea rows={4} placeholder="今天也要开心呀 {昵称}" />
            </Form.Item>
          )}

          {kind === 'image' && (
            <Form.Item name="content" label="图片路径" extra="图片会存到本地 data/artifacts/uploads">
              <Space.Compact style={{ width: '100%' }}>
                <Input placeholder="服务器上的绝对路径" />
                <Upload {...uploadProps}>
                  <Button>上传图片</Button>
                </Upload>
              </Space.Compact>
            </Form.Item>
          )}

          {kind === 'sticker' && (
            <>
              <Form.Item name="sticker_name" label="表情名称">
                <Input placeholder="表情在抖音面板里显示的名字" />
              </Form.Item>
              <Space>
                <Form.Item name="sticker_category" label="所在分类">
                  <Input placeholder="例如：常用" style={{ width: 180 }} />
                </Form.Item>
                <Form.Item name="sticker_fallback_index" label="分类内序号">
                  <InputNumber min={0} placeholder="找不到时的兜底" />
                </Form.Item>
              </Space>
              <Button
                icon={<ScissorOutlined />}
                loading={collecting}
                onClick={handleCollect}
                style={{ marginBottom: 12 }}
              >
                从抖音面板采集
              </Button>
              {stickers.length > 0 && (
                <div className="sticker-grid">
                  {stickers.slice(0, 60).map((sticker) => (
                    <div
                      key={sticker.id}
                      className="sticker-cell"
                      onClick={() => {
                        form.setFieldsValue({
                          sticker_name: sticker.name,
                          sticker_category: sticker.category,
                          sticker_fallback_index: sticker.fallback_index,
                          content: sticker.name,
                        })
                      }}
                    >
                      {sticker.preview ? (
                        <img src={sticker.preview} alt={sticker.name} />
                      ) : (
                        <div style={{ height: 52 }} />
                      )}
                      <div className="name">{sticker.name}</div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          <Space size="large">
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="weight" label="权重">
              <InputNumber min={1} max={100} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      {/* 表情采集结果 */}
      <Modal
        title="采集到的抖音表情"
        open={pickerOpen}
        onCancel={() => setPickerOpen(false)}
        footer={null}
        width={760}
      >
        {catalog.length === 0 ? (
          <div className="empty-hint">没有采集到表情。可能是抖音改版了面板结构，把日志级别调成 DEBUG 再试。</div>
        ) : (
          <div className="sticker-grid">
            {catalog.map((sticker, index) => (
              <div
                key={`${sticker.category}-${sticker.name}-${index}`}
                className="sticker-cell"
                onClick={() => pickSticker(sticker)}
              >
                {sticker.preview ? (
                  <img src={sticker.preview} alt={sticker.name} />
                ) : (
                  <div style={{ height: 52 }} />
                )}
                <div className="name">{sticker.name}</div>
                {sticker.category && (
                  <Tooltip title={sticker.category}>
                    <div className="name" style={{ color: '#bfbfbf' }}>
                      {sticker.category}
                    </div>
                  </Tooltip>
                )}
              </div>
            ))}
          </div>
        )}
      </Modal>
    </>
  )
}
