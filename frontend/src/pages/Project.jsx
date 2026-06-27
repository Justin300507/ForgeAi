import { useState, useEffect, useRef } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import Navbar from '../components/Navbar'
import api, { buildWsUrl } from '../lib/api'

function StatusBadge({ status }) {
  if (status === 'done') return <span className="badge-green"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />Live</span>
  if (status === 'running' || status === 'pending') return <span className="badge-yellow"><span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />Building</span>
  if (status === 'error') return <span className="badge-red"><span className="w-1.5 h-1.5 rounded-full bg-red-400" />Failed</span>
  if (status === 'cancelled') return <span className="badge-red"><span className="w-1.5 h-1.5 rounded-full bg-gray-400" />Cancelled</span>
  return null
}

function DeployFailed({ label, hint }) {
  return (
    <div className="flex items-start gap-3 p-3.5 rounded-lg bg-gray-800/50 border border-red-500/20">
      <span className="text-red-400 text-sm shrink-0">✗</span>
      <div>
        <div className="text-sm text-gray-400">{label} <span className="text-red-400 text-xs ml-1">deployment failed</span></div>
        {hint && <div className="text-xs text-gray-600 mt-1 font-mono">{hint}</div>}
      </div>
    </div>
  )
}

function UrlCard({ label, url, color = 'text-violet-400' }) {
  if (!url) return null
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="flex items-center justify-between p-3.5 rounded-lg bg-gray-800 hover:bg-gray-700 transition-colors group"
    >
      <span className="text-sm text-gray-400">{label}</span>
      <span className={`text-xs ${color} group-hover:underline truncate max-w-[260px] ml-3`}>{url} ↗</span>
    </a>
  )
}

export default function Project() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const [job, setJob] = useState(null)
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('overview')
  const [regenerating, setRegenerating] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const logsEndRef = useRef(null)
  const wsRef = useRef(null)

  useEffect(() => {
    fetchJob()
    return () => wsRef.current?.close()
  }, [jobId])

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  async function fetchJob() {
    try {
      const res = await api.get(`/jobs/${jobId}`)
      const data = res.data
      setJob(data)
      if (data.logs) setLogs(data.logs)

      if (data.status === 'running' || data.status === 'pending') {
        connectWs()
      }
    } catch {
      navigate('/dashboard')
    } finally {
      setLoading(false)
    }
  }

  async function handleCancel() {
    setCancelling(true)
    try {
      await api.post(`/jobs/${jobId}/cancel`)
      await fetchJob()
    } catch {
      // job may have already finished
    } finally {
      setCancelling(false)
    }
  }

  async function handleRetry() {
    setRegenerating(true)
    try {
      const res = await api.post(`/jobs/${jobId}/retry`)
      navigate(`/projects/${res.data.job_id}`)
    } catch {
      setRegenerating(false)
    }
  }

  async function handleRebuild() {
    setRegenerating(true)
    try {
      const res = await api.post('/jobs', {
        idea: job.idea,
        provider: job.provider || 'auto',
        deploy_to: job.deploy_to || 'none',
        frontend_target: job.frontend_target || 'web',
      })
      navigate(`/projects/${res.data.job_id}`)
    } catch {
      setRegenerating(false)
    }
  }

  function connectWs() {
    if (wsRef.current) wsRef.current.close()
    const ws = new WebSocket(buildWsUrl(jobId))
    wsRef.current = ws

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'log') {
        setLogs(prev => [...prev, msg.message])
      } else if (msg.type === 'done' || msg.type === 'error') {
        fetchJob()
        ws.close()
      }
    }
  }

  if (loading) return (
    <div className="min-h-screen">
      <Navbar />
      <div className="flex justify-center py-24">
        <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    </div>
  )

  if (!job) return null

  const isActive = job.status === 'running' || job.status === 'pending'
  const isCancelled = job.status === 'cancelled'
  const scoreColor = job.forge_score >= 80 ? 'text-emerald-400' : job.forge_score >= 60 ? 'text-amber-400' : 'text-red-400'
  const name = job.project_name || `Project ${jobId.slice(0, 8)}`

  return (
    <div className="min-h-screen">
      <Navbar />
      <div className="max-w-4xl mx-auto px-4 py-8">

        {/* Header */}
        <div className="flex items-start justify-between mb-6">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <StatusBadge status={job.status} />
              {job.forge_score != null && (
                <span className={`text-sm font-mono font-bold ${scoreColor}`}>
                  Score: {job.forge_score}/100
                </span>
              )}
            </div>
            <h1 className="text-2xl font-bold">{name}</h1>
            <p className="text-gray-400 text-sm mt-1 max-w-xl">{job.idea}</p>
          </div>
          <Link to="/generate" className="btn-primary shrink-0">+ New App</Link>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-800 mb-6">
          {['overview', 'logs'].map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 text-sm font-medium capitalize transition-colors border-b-2 -mb-px ${activeTab === tab ? 'border-violet-500 text-white' : 'border-transparent text-gray-500 hover:text-gray-300'}`}
            >
              {tab}
              {tab === 'logs' && logs.length > 0 && (
                <span className="ml-1.5 text-xs bg-gray-700 text-gray-400 px-1.5 py-0.5 rounded-full">{logs.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* Overview tab */}
        {activeTab === 'overview' && (
          <div className="space-y-4 animate-fade-in">
            {/* Building indicator */}
            {isActive && (
              <div className="card p-4 border-amber-500/30 flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="w-4 h-4 border-2 border-amber-400 border-t-transparent rounded-full animate-spin shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-amber-400">Building your app...</div>
                    <div className="text-xs text-gray-500">This takes 3–5 minutes. You can leave and come back.</div>
                  </div>
                </div>
                <button
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="shrink-0 text-xs px-3 py-1.5 rounded-lg border border-red-500/40 text-red-400 hover:bg-red-500/10 transition-colors disabled:opacity-50"
                >
                  {cancelling ? 'Stopping...' : 'Force Stop'}
                </button>
              </div>
            )}

            {/* Cancelled notice */}
            {isCancelled && (
              <div className="card p-4 border-gray-600/30 flex items-center gap-3">
                <span className="text-gray-400 text-lg">⏹</span>
                <div>
                  <div className="text-sm font-medium text-gray-400">Generation stopped</div>
                  <div className="text-xs text-gray-600">You cancelled this job. Use Fix & Retry or Rebuild from Scratch below.</div>
                </div>
              </div>
            )}

            {/* Deployment links */}
            {(job.deploy_to !== 'none' || job.frontend_url || job.backend_url || job.github_url) && job.status === 'done' && (
              <div className="card p-5">
                <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">Deployment</h2>
                <div className="space-y-2">
                  {/* GitHub — always shown if deploy was attempted */}
                  {job.github_url
                    ? <UrlCard label="GitHub" url={job.github_url} color="text-gray-400" />
                    : job.deploy_to !== 'none' && <DeployFailed label="GitHub" />}

                  {/* Backend */}
                  {(job.deploy_to === 'render' || job.deploy_to === 'both') && (
                    job.backend_url
                      ? <UrlCard label="Backend (Render)" url={job.backend_url} color="text-blue-400" />
                      : <DeployFailed label="Backend (Render)" />
                  )}

                  {/* Frontend */}
                  {(job.deploy_to === 'cloudflare' || job.deploy_to === 'both') && (
                    job.frontend_url
                      ? <UrlCard label="Frontend (Cloudflare)" url={job.frontend_url} color="text-violet-400" />
                      : <DeployFailed label="Frontend (Cloudflare)" hint="Wrangler failed — check logs, then run: wrangler pages deploy ./dist --project-name=<slug>" />
                  )}
                </div>
              </div>
            )}

            {/* Metadata */}
            <div className="card p-5">
              <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">Details</h2>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
                {[
                  { label: 'Status', value: job.status },
                  { label: 'Provider', value: job.provider || 'auto' },
                  { label: 'Deploy', value: job.deploy_to || 'none' },
                  { label: 'Target', value: job.frontend_target || 'web' },
                  { label: 'Created', value: job.created_at ? new Date(job.created_at).toLocaleString() : '—' },
                  { label: 'Completed', value: job.completed_at ? new Date(job.completed_at).toLocaleString() : '—' },
                ].map(({ label, value }) => (
                  <div key={label}>
                    <dt className="text-xs text-gray-500">{label}</dt>
                    <dd className="text-sm text-gray-200 font-mono mt-0.5">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>

            {/* Error */}
            {job.status === 'error' && job.error && (
              <div className="card p-5 border-red-500/30">
                <h2 className="text-sm font-medium text-red-400 mb-2">Error</h2>
                <pre className="text-xs text-gray-400 whitespace-pre-wrap font-mono">{job.error}</pre>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 flex-wrap">
              <button
                onClick={handleRetry}
                disabled={regenerating || isActive}
                className="btn-primary text-sm"
                title="Fix existing files and redeploy — no full rebuild"
              >
                {regenerating ? 'Starting...' : 'Fix & Retry'}
              </button>
              <button
                onClick={handleRebuild}
                disabled={regenerating || isActive}
                className="btn-ghost border border-gray-700 text-sm"
                title="Start fresh — regenerate the entire app from scratch"
              >
                Rebuild from Scratch
              </button>
              {isActive && (
                <button
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="btn-ghost border border-red-500/40 text-red-400 text-sm"
                >
                  {cancelling ? 'Stopping...' : 'Force Stop'}
                </button>
              )}
              <Link to="/dashboard" className="btn-ghost text-sm">
                Back to Dashboard
              </Link>
            </div>
          </div>
        )}

        {/* Logs tab */}
        {activeTab === 'logs' && (
          <div className="animate-fade-in">
            {logs.length === 0 ? (
              <div className="card p-12 text-center text-gray-600 font-mono text-sm">
                {isActive ? 'Waiting for logs...' : 'No logs captured'}
              </div>
            ) : (
              <div className="card p-4 bg-gray-950">
                <div className="font-mono text-xs text-gray-400 space-y-0.5 max-h-[600px] overflow-y-auto">
                  {logs.map((line, i) => (
                    <div key={i} className="leading-relaxed hover:text-gray-300 transition-colors">
                      <span className="text-gray-700 select-none mr-2">{String(i + 1).padStart(3, '0')}</span>
                      {line}
                    </div>
                  ))}
                  <div ref={logsEndRef} />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
