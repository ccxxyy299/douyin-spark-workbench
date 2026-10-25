import type {
  Account,
  Friend,
  MessageTemplate,
  ModuleSpec,
  SendJob,
  SendLog,
  SendPlan,
  Stats,
  StickerItem,
} from './types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* 响应不是 JSON，用状态码兜底 */
    }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) })

const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })

const del = <T>(path: string) => request<T>(path, { method: 'DELETE' })

export const api = {
  // 模块
  modules: () => request<ModuleSpec[]>('/modules'),

  // 账号
  accounts: () => request<Account[]>('/douyin/accounts'),
  createAccount: (name: string, note = '') =>
    post<Account>('/douyin/accounts', { name, note }),
  deleteAccount: (id: number) => del<{ ok: boolean }>(`/douyin/accounts/${id}`),
  loginStatus: (id: number) =>
    request<{ logged_in: boolean; error: string }>(`/douyin/accounts/${id}/login-status`),
  login: (id: number) => post<{ logged_in: boolean }>(`/douyin/accounts/${id}/login`),
  logout: (id: number) => post<{ ok: boolean }>(`/douyin/accounts/${id}/logout`),

  // 好友
  friends: (accountId: number) => request<Friend[]>(`/douyin/friends?account_id=${accountId}`),
  syncFriends: (accountId: number) =>
    post<{ total: number; added: number; updated: number; sent_today: number[] }>(
      `/douyin/friends/sync?account_id=${accountId}`,
    ),
  updateFriend: (id: number, payload: Partial<Friend>) =>
    patch<Friend>(`/douyin/friends/${id}`, payload),
  setSelection: (accountId: number, friendIds: number[], selected: boolean) =>
    post<{ updated: number }>('/douyin/friends/selection', {
      account_id: accountId,
      friend_ids: friendIds,
      selected,
    }),

  // 文案模板
  templates: (accountId: number) =>
    request<MessageTemplate[]>(`/douyin/templates?account_id=${accountId}`),
  createTemplate: (accountId: number, payload: Partial<MessageTemplate>) =>
    post<MessageTemplate>(`/douyin/templates?account_id=${accountId}`, payload),
  updateTemplate: (id: number, payload: Partial<MessageTemplate>) =>
    put<MessageTemplate>(`/douyin/templates/${id}`, payload),
  deleteTemplate: (id: number) => del<{ ok: boolean }>(`/douyin/templates/${id}`),

  // 计划
  plans: (accountId: number) => request<SendPlan[]>(`/douyin/plans?account_id=${accountId}`),
  createPlan: (accountId: number, payload: Partial<SendPlan>) =>
    post<SendPlan>(`/douyin/plans?account_id=${accountId}`, payload),
  updatePlan: (id: number, payload: Partial<SendPlan>) =>
    put<SendPlan>(`/douyin/plans/${id}`, payload),
  deletePlan: (id: number) => del<{ ok: boolean }>(`/douyin/plans/${id}`),
  generatePlanJobs: (id: number) =>
    post<{ created: number }>(`/douyin/plans/${id}/generate`),
  runPlanNow: (id: number) =>
    post<{ created: number; executed: number; plan: string }>(`/douyin/plans/${id}/run-now`),
  sendNow: (accountId: number, friendIds: number[], templateIds: number[]) =>
    post<{ created: number; executed: number }>('/douyin/send-now', {
      account_id: accountId,
      friend_ids: friendIds,
      template_ids: templateIds,
    }),

  // 表情
  collectStickers: (accountId: number) =>
    post<{ collected: number; new: number; items: StickerItem[] }>(
      `/douyin/stickers/collect?account_id=${accountId}`,
    ),
  stickers: (accountId: number) => request<StickerItem[]>(`/douyin/stickers?account_id=${accountId}`),

  // 任务与日志
  jobs: (accountId?: number, limit = 100) =>
    request<SendJob[]>(
      `/douyin/jobs?limit=${limit}${accountId ? `&account_id=${accountId}` : ''}`,
    ),
  logs: (accountId?: number, limit = 200) =>
    request<SendLog[]>(`/douyin/logs?limit=${limit}${accountId ? `&account_id=${accountId}` : ''}`),
  stats: (accountId?: number) =>
    request<Stats>(`/douyin/stats${accountId ? `?account_id=${accountId}` : ''}`),
}
