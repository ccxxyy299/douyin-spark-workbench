import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from './api'
import type { Account } from './types'

interface AccountContextValue {
  accounts: Account[]
  accountId: number | null
  account: Account | null
  setAccountId: (id: number) => void
  reloadAccounts: () => Promise<void>
  loading: boolean
}

const AccountContext = createContext<AccountContextValue | null>(null)

export function AccountProvider({ children }: { children: ReactNode }) {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountId, setAccountId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  // 必须是稳定引用：页面里普遍用 useEffect(..., [refresh]) 触发首次加载，
  // 每次渲染都新建函数会让 effect 无限重跑，接口被反复请求。
  const reloadAccounts = useCallback(async () => {
    setLoading(true)
    try {
      const list = await api.accounts()
      setAccounts(list)
      setAccountId((current) => {
        if (current && list.some((item) => item.id === current)) return current
        return list[0]?.id ?? null
      })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reloadAccounts()
  }, [reloadAccounts])

  const value = useMemo<AccountContextValue>(
    () => ({
      accounts,
      accountId,
      account: accounts.find((item) => item.id === accountId) ?? null,
      setAccountId,
      reloadAccounts,
      loading,
    }),
    [accounts, accountId, loading, reloadAccounts],
  )

  return <AccountContext.Provider value={value}>{children}</AccountContext.Provider>
}

export function useAccount() {
  const value = useContext(AccountContext)
  if (!value) throw new Error('useAccount 必须在 AccountProvider 内使用')
  return value
}
