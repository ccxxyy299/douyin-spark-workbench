export interface Account {
  id: number
  name: string
  note: string
  profile_dir: string
  status: 'unknown' | 'active' | 'expired' | 'busy'
  last_login_at: string | null
  last_check_at: string | null
  created_at: string
}

export interface Friend {
  id: number
  account_id: number
  display_name: string
  sec_uid: string
  nickname: string
  flame_days: number
  flame_text: string
  /** 平台徽标状态：normal=彩色（续上了）/ gray=灰色（还没续或临期）/ ''=没读到。扫描时的快照 */
  flame_state: 'normal' | 'gray' | ''
  flame_updated_at: string | null
  selected: boolean
  selected_order: number | null
  group_tag: string
  note: string
  has_conversation: boolean
  consecutive_failures: number
  last_error: string
  /** 今天是否已经成功发送过（由后端按发送记录算，不看火花天数） */
  sent_today: boolean
}

export type TemplateKind = 'text' | 'image' | 'sticker'

export interface MessageTemplate {
  id: number
  account_id: number
  name: string
  kind: TemplateKind
  content: string
  sticker_name: string
  sticker_category: string
  sticker_fallback_index: number | null
  sticker_preview: string
  enabled: boolean
  weight: number
}

export interface SendPlan {
  id: number
  account_id: number
  name: string
  target_friend_ids: string
  template_ids: string
  daily_time: string
  jitter_minutes: number
  days_of_week: string
  date_from: string
  date_to: string
  enabled: boolean
  last_plan_date: string
}

export type JobState =
  | 'pending'
  | 'running'
  | 'sent'
  | 'failed'
  | 'skipped'
  | 'needs_human'

export interface SendJob {
  id: number
  plan_id: number
  account_id: number
  friend_id: number
  friend_name: string
  template_id: number | null
  message_kind: TemplateKind
  message_payload: string
  message_preview: string
  plan_date: string
  plan_at: string
  actual_at: string | null
  state: JobState
  attempt: number
  error: string
  screenshot: string
}

export interface SendLog {
  id: number
  job_id: number | null
  account_id: number | null
  level: string
  message: string
  created_at: string
}

export interface StickerItem {
  id: number
  account_id: number
  category: string
  name: string
  fallback_index: number
  preview: string
  resource_key: string
}

export interface Stats {
  today: string
  total_jobs_today: number
  by_state: Record<string, number>
  sent_today: number
  friends_total: number
  friends_selected: number
  friends_with_flame: number
  daily_cap: number
}

export interface ModuleSpec {
  id: string
  name: string
  description: string
  icon: string
  prefix: string
  route_prefix: string
  nav: { key: string; label: string }[]
}
