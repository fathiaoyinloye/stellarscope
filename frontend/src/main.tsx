import React, {useEffect, useState} from 'react'
import axios from 'axios'
import { createRoot } from 'react-dom/client'
import App from './App'
interface Transaction {
  id: string
  transaction_hash: string
  network: string
  monitoring_status: string
  rpc_status: string
  ledger: number | null
  created_at: string
  updated_at: string | null
  is_terminal: boolean
  is_active: boolean
}

export default function TransactionListComponent() {
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(20)
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(0)

  useEffect(() => {
    async function fetchData() {
      try {
        const response = await axios.get(`/api/v1/transactions/`, {
          params: {page, per_page: perPage},
          withCredentials: true,
        })
        setTransactions(response.data.items)
        setTotal(response.data.total)
        setTotalPages(response.data.total_pages)
      } catch (error) {
        console.error('Failed to fetch transactions:', error)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [page, perPage])

  if (loading) {
    return <div>Loading transactions...</div>
  }

  return (
    <div className="p-4">
      <h1 className="text-2xl font-bold mb-4">Monitored Transactions</h1>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse border border-gray-300">
          <thead>
            <tr className="bg-gray-100">
              <th className="border padding px-4 py-2">Hash</th>
              <th className="border padding px-4 py-2">Network</th>
              <th className="border padding px-4 py-2">Status</th>
              <th className="border padding px-4 py-2">Ledger</th>
              <th className="border padding px-4 py-2">Created</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((tx) => (
              <tr key={tx.id} className="border-b">
                <td className="border padding px-4 py-2">
                  <a href={`/api/v1/transactions/${tx.transaction_hash}`} target="_blank" rel="noopener noreferrer">
                    {tx.transaction_hash}
                  </a>
                </td>
                <td className="border padding px-4 py-2">{tx.network}</td>
                <td className="border padding px-4 py-2">
                  <span className={`font-medium ${tx.monitoring_status === 'COMPLETED' ? 'text-green-600' : tx.monitoring_status === 'ACTIVE' ? 'text-yellow-600' : 'text-gray-600'}`}>
                    {tx.monitoring_status}
                  </span>
                </td>
                <td className="border padding px-4 py-2">{tx.ledger ?? '—'}</td>
                <td className="border padding px-4 py-2">{tx.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex justify-between items-center">
        <div className="text-sm text-gray-600">
          Showing {transactions.length} of {total} transactions
        </div>
        <div>
          <button
            onClick={() => setPage(Math.max(1, page - 1))}
            className="mr-2 px-3 py-1 rounded hover:bg-gray-200"
            disabled={page === 1}
          >
            Prev
          </button>
          <span>{page} of {totalPages}</span>
          <button
            onClick={() => setPage(page + 1)}
            className="ml-2 px-3 py-1 rounded hover:bg-gray-200"
            disabled={page === totalPages}
          >
            Next
          </button>
        </div>
      </div>

      <div className="mt-4">
        <button
          onClick={() => setPerPage(perPage === 20 ? 50 : 20)}
          className="mr-2 px-3 py-1 rounded hover:bg-gray-200"
        >
          {perPage === 20 ? 'Show 50' : 'Show 20'}
        </button>
        <span className="ml-2 text-sm text-gray-600">per page</span>
      </div>
    </div>
  )
}

// Mount the React application into the #root DOM element
const container = document.getElementById('root')
if (container) {
  const root = createRoot(container)
  root.render(<App />)
}