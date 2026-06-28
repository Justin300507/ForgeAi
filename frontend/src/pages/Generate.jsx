import { useState, useRef, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import Navbar from '../components/Navbar'
import api, { buildWsUrl } from '../lib/api'

const MODELS = [
  { id: 'auto', label: 'Auto', desc: 'Best available' },
  { id: 'cerebras', label: 'Cerebras', desc: 'Fast & free' },
  { id: 'gemini', label: 'Gemini', desc: 'Google AI' },
  { id: 'groq', label: 'Groq', desc: 'Ultra fast' },
]

const DEPLOY_OPTIONS = [
  {
    id: 'none',
    label: 'Download Only',
    desc: 'Get a zip of the generated code',
    icon: '📁',
    requires: [],
  },
  {
    id: 'cloudflare',
    label: 'Frontend Only',
    desc: 'Deploy to Cloudflare Pages',
    icon: '🌐',
    requires: ['github', 'cloudflare'],
  },
  {
    id: 'both',
    label: 'Full Stack',
    desc: 'GitHub + Cloudflare + Railway',
    icon: '🚀',
    requires: ['github', 'cloudflare', 'railway'],
  },
]

const SERVICE_META = {
  github:     { label: 'GitHub',     icon: '🐙' },
  cloudflare: { label: 'Cloudflare', icon: '☁️' },
  railway:    { label: 'Railway',    icon: '🚂' },
}

const PIPELINE_STAGES = [
  { key: 'planner', label: 'Planner', keywords: ['Planning', 'plann', 'Step 1'] },
  { key: 'architect', label: 'Architect', keywords: ['Architect', 'architect', 'design'] },
  { key: 'backend', label: 'Backend', keywords: ['backend', 'Backend', 'Python', 'FastAPI'] },
  { key: 'frontend', label: 'Frontend', keywords: ['frontend', 'Frontend', 'React', 'JSX'] },
  { key: 'validation', label: 'Validation', keywords: ['validat', 'Validat', 'fix', 'Fix'] },
  { key: 'github', label: 'GitHub Push', keywords: ['GitHub', 'github', 'push', 'repo'] },
  { key: 'railway', label: 'Railway Deploy', keywords: ['Railway', 'railway', 'backend deploy'] },
  { key: 'cloudflare', label: 'Cloudflare', keywords: ['Cloudflare', 'cloudflare', 'pages', 'wrangler'] },
  { key: 'health', label: 'Health Check', keywords: ['health', 'Health', 'live', 'done'] },
]

function detectStage(log) {
  for (const stage of PIPELINE_STAGES) {
    if (stage.keywords.some(kw => log.includes(kw))) return stage.key
  }
  return null
}

function StageRow({ stage, status }) {
  const icon = status === 'done' ? '✓' : status === 'running' ? '●' : status === 'error' ? '✗' : '○'
  const color = status === 'done' ? 'text-emerald-400' : status === 'running' ? 'text-violet-400' : status === 'error' ? 'text-red-400' : 'text-gray-600'
  const barColor = status === 'done' ? 'bg-emerald-500' : status === 'running' ? 'bg-violet-500' : 'bg-gray-700'
  const barWidth = status === 'done' ? 'w-full' : status === 'running' ? 'w-2/3' : 'w-0'

  return (
    <div className="flex items-center gap-3">
      <span className={`text-xs font-mono w-3 text-center ${color}`}>{icon}</span>
      <span className={`text-xs w-24 shrink-0 ${status === 'pending' ? 'text-gray-600' : 'text-gray-300'}`}>
        {stage.label}
      </span>
      <div className="flex-1 bg-gray-800 rounded-full h-1.5 overflow-hidden">
        <div className={`h-full ${barWidth} ${barColor} rounded-full transition-all duration-700 ${status === 'running' ? 'animate-pulse-slow' : ''}`} />
      </div>
    </div>
  )
}

const EXAMPLE_IDEAS = [
  'A habit tracker with streaks, gamification, and dark mode',
  'An expense tracker with categories, monthly budgets, and charts',
  'A CRM with contacts, deals, and pipeline view',
  'A task manager with projects, due dates, and priorities',
  'An inventory system with stock alerts and order tracking',
]

export default function Generate() {
  const navigate = useNavigate()
  const [idea, setIdea] = useState('')
  const [provider, setProvider] = useState('auto')
  const [deployTo, setDeployTo] = useState('none')
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState(null)
  const [logs, setLogs] = useState([])
  const [stages, setStages] = useState({})
  const [currentStage, setCurrentStage] = useState(null)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [connStatus, setConnStatus] = useState(null)
  const logsEndRef = useRef(null)
  const wsRef = useRef(null)
  const currentStageRef = useRef(null)

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  useEffect(() => {
    return () => wsRef.current?.close()
  }, [])

  useEffect(() => {
    api.get('/credentials/status').then(r => setConnStatus(r.data)).catch(() => {})
  }, [])

  function connectWs(jid) {
    const ws = new WebSocket(buildWsUrl(jid))
    wsRef.current = ws

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data)

      if (msg.type === 'log') {
        const line = msg.message
        setLogs(prev => [...prev, line])

        const detected = detectStage(line)
        if (detected) {
          setCurrentStage(detected)
          currentStageRef.current = detected
          setStages(prev => {
            const next = { ...prev }
            // Mark previous stages done
            let found = false
            for (const s of PIPELINE_STAGES) {
              if (found) break
              if (s.key === detected) { found = true; next[s.key] = 'running' }
              else if (!next[s.key] || next[s.key] === 'pending') next[s.key] = 'pending'
              else if (next[s.key] === 'running') next[s.key] = 'done'
            }
            return next
          })
        }
      } else if (msg.type === 'done') {
        setStages(prev => {
          const next = { ...prev }
          PIPELINE_STAGES.forEach(s => { if (next[s.key] !== 'error') next[s.key] = 'done' })
          return next
        })
        setDone(true)
        setResult(msg.result)
        setSubmitting(false)
      } else if (msg.type === 'cancelled') {
        setError('')
        setDone(true)
        setSubmitting(false)
        navigate(`/projects/${jid}`)
      } else if (msg.type === 'error') {
        setError(msg.message || 'Generation failed')
        const failedStage = currentStageRef.current
        if (failedStage) setStages(prev => ({ ...prev, [failedStage]: 'error' }))
        setDone(true)
        setSubmitting(false)
      }
    }

    ws.onerror = () => {
      setError('Connection lost. Check the project page for results.')
      setDone(true)
      setSubmitting(false)
    }
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!idea.trim()) return
    setSubmitting(true)
    setLogs([])
    setStages({})
    setDone(false)
    setError('')
    setResult(null)

    // Mark first stage as running immediately
    setStages({ planner: 'running' })
    setCurrentStage('planner')
    currentStageRef.current = 'planner'

    try {
      const res = await api.post('/jobs', { idea: idea.trim(), provider, deploy_to: deployTo })
      const jid = res.data.job_id
      setJobId(jid)
      connectWs(jid)
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to start generation')
      setSubmitting(false)
      setStages({})
    }
  }

  const report = result?.report || {}
  const isBuilding = submitting && !done

  const selectedOption = DEPLOY_OPTIONS.find(o => o.id === deployTo)
  const requiredServices = selectedOption?.requires ?? []
  const allConnected = requiredServices.every(svc => connStatus?.[svc]?.connected === true)
  const canSubmit = !isBuilding && !!idea.trim() && (requiredServices.length === 0 || allConnected)

  return (
    <div className="min-h-screen">
      <Navbar />
      <div className="max-w-5xl mx-auto px-4 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold">Generate App</h1>
          <p className="text-gray-400 text-sm mt-1">Describe your idea — AI builds the full stack and deploys it</p>
        </div>

        <div className="grid lg:grid-cols-2 gap-6">
          {/* Left — form */}
          <div className="space-y-5">
            <form onSubmit={handleSubmit} className="space-y-5">
              {/* Idea textarea */}
              <div>
                <label className="label">What do you want to build?</label>
                <textarea
                  className="input min-h-[140px] resize-none"
                  placeholder="A habit tracker with streaks, badges, dark mode, and weekly reports..."
                  value={idea}
                  onChange={e => setIdea(e.target.value)}
                  disabled={isBuilding}
                  required
                />
                {/* Quick examples */}
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {EXAMPLE_IDEAS.slice(0, 3).map(ex => (
                    <button
                      key={ex}
                      type="button"
                      onClick={() => setIdea(ex)}
                      disabled={isBuilding}
                      className="text-xs px-2.5 py-1 rounded-full border border-gray-700 text-gray-400 hover:border-violet-500/50 hover:text-violet-300 transition-colors disabled:opacity-50"
                    >
                      {ex.split(' ').slice(0, 4).join(' ')}...
                    </button>
                  ))}
                </div>
              </div>

              {/* Model */}
              <div>
                <label className="label">Model</label>
                <div className="grid grid-cols-2 gap-2">
                  {MODELS.map(m => (
                    <label
                      key={m.id}
                      className={`flex items-center gap-2.5 p-3 rounded-lg border cursor-pointer transition-all ${provider === m.id ? 'border-violet-500 bg-violet-500/10' : 'border-gray-700 hover:border-gray-600'} ${isBuilding ? 'opacity-50 cursor-not-allowed' : ''}`}
                    >
                      <input
                        type="radio"
                        name="provider"
                        value={m.id}
                        checked={provider === m.id}
                        onChange={() => setProvider(m.id)}
                        disabled={isBuilding}
                        className="accent-violet-500"
                      />
                      <div>
                        <div className="text-sm font-medium">{m.label}</div>
                        <div className="text-xs text-gray-500">{m.desc}</div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

              {/* Deploy */}
              <div>
                <label className="label">Deployment</label>
                <div className="space-y-2">
                  {DEPLOY_OPTIONS.map(opt => (
                    <label
                      key={opt.id}
                      className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-all ${deployTo === opt.id ? 'border-violet-500 bg-violet-500/10' : 'border-gray-700 hover:border-gray-600'} ${isBuilding ? 'opacity-50 cursor-not-allowed' : ''}`}
                    >
                      <input
                        type="radio"
                        name="deploy_to"
                        value={opt.id}
                        checked={deployTo === opt.id}
                        onChange={() => setDeployTo(opt.id)}
                        disabled={isBuilding}
                        className="accent-violet-500"
                      />
                      <span className="text-lg">{opt.icon}</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium">{opt.label}</div>
                        <div className="text-xs text-gray-500">{opt.desc}</div>
                      </div>
                      {opt.id !== 'none' && deployTo === opt.id && (
                        <span className={`text-xs font-medium shrink-0 ${allConnected ? 'text-emerald-400' : 'text-amber-400'}`}>
                          {allConnected ? 'Ready' : 'Setup needed'}
                        </span>
                      )}
                    </label>
                  ))}
                </div>

                {/* Required accounts panel */}
                {requiredServices.length > 0 && (
                  <div className="mt-3 rounded-lg border border-gray-700 p-3 space-y-2">
                    <div className="text-xs text-gray-500 uppercase tracking-wide font-mono mb-1">Required accounts</div>
                    {requiredServices.map(svc => {
                      const meta = SERVICE_META[svc]
                      const st = connStatus?.[svc]
                      const connected = st?.connected === true
                      const label = connected
                        ? (st.login || st.name || st.email || 'Connected')
                        : null
                      return (
                        <div key={svc} className="flex items-center gap-2.5">
                          <span className="text-base w-5 text-center">{meta.icon}</span>
                          <span className="text-sm text-gray-300 w-20 shrink-0">{meta.label}</span>
                          {connected ? (
                            <span className="text-xs text-emerald-400 flex items-center gap-1">
                              <span>✓</span>
                              <span className="truncate max-w-[120px]">{label}</span>
                            </span>
                          ) : (
                            <Link
                              to="/credentials"
                              className="text-xs text-violet-400 hover:text-violet-300 underline underline-offset-2"
                            >
                              Connect →
                            </Link>
                          )}
                        </div>
                      )
                    })}
                    {!allConnected && (
                      <p className="text-xs text-amber-400/80 mt-1 pt-1 border-t border-gray-700/50">
                        Connect all accounts to enable deployment.
                      </p>
                    )}
                  </div>
                )}
              </div>

              <div className="flex gap-2">
                <button
                  type="submit"
                  className="btn-primary flex-1 py-3 text-base"
                  disabled={!canSubmit}
                  title={!allConnected && requiredServices.length > 0 ? 'Connect all required accounts first' : undefined}
                >
                  {isBuilding ? (
                    <span className="flex items-center justify-center gap-2">
                      <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Building your app...
                    </span>
                  ) : deployTo === 'none' ? 'Generate App' : 'Generate & Deploy'}
                </button>
                {isBuilding && jobId && (
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        await api.post(`/jobs/${jobId}/cancel`)
                      } catch {}
                    }}
                    className="px-4 py-3 rounded-lg border border-red-500/40 text-red-400 text-sm hover:bg-red-500/10 transition-colors"
                  >
                    Stop
                  </button>
                )}
              </div>
            </form>
          </div>

          {/* Right — progress */}
          <div className="space-y-4">
            {/* Pipeline stages */}
            {(isBuilding || done) && (
              <div className="card p-5 animate-fade-in">
                <div className="text-xs font-mono text-gray-500 uppercase tracking-wide mb-4">Pipeline</div>
                <div className="space-y-2.5">
                  {PIPELINE_STAGES.map(stage => (
                    <StageRow
                      key={stage.key}
                      stage={stage}
                      status={stages[stage.key] || 'pending'}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Result panel */}
            {done && !error && report.status && (
              <div className="card p-5 border-emerald-500/30 animate-fade-in">
                <div className="flex items-center gap-2 mb-4">
                  <span className="w-2 h-2 rounded-full bg-emerald-400" />
                  <span className="text-sm font-medium text-emerald-400">
                    {report.status === 'deployed' ? 'Deployed Successfully' : 'Generation Complete'}
                  </span>
                  {report.forge_score && (
                    <span className="ml-auto text-sm font-mono font-bold text-violet-400">
                      Score: {report.forge_score}/100
                    </span>
                  )}
                </div>

                <div className="space-y-2 mb-4">
                  {report.frontend_url && (
                    <a href={report.frontend_url} target="_blank" rel="noreferrer"
                      className="flex items-center justify-between p-2.5 rounded-lg bg-gray-800 hover:bg-gray-700 transition-colors">
                      <span className="text-sm text-gray-300">Frontend</span>
                      <span className="text-xs text-violet-400 truncate max-w-[200px]">{report.frontend_url} ↗</span>
                    </a>
                  )}
                  {report.backend_url && (
                    <a href={report.backend_url} target="_blank" rel="noreferrer"
                      className="flex items-center justify-between p-2.5 rounded-lg bg-gray-800 hover:bg-gray-700 transition-colors">
                      <span className="text-sm text-gray-300">Backend</span>
                      <span className="text-xs text-blue-400 truncate max-w-[200px]">{report.backend_url} ↗</span>
                    </a>
                  )}
                  {report.github_url && (
                    <a href={report.github_url} target="_blank" rel="noreferrer"
                      className="flex items-center justify-between p-2.5 rounded-lg bg-gray-800 hover:bg-gray-700 transition-colors">
                      <span className="text-sm text-gray-300">GitHub</span>
                      <span className="text-xs text-gray-400 truncate max-w-[200px]">{report.github_url} ↗</span>
                    </a>
                  )}
                </div>

                {jobId && (
                  <Link to={`/projects/${jobId}`} className="btn-primary w-full text-center block py-2.5">
                    View Project Details
                  </Link>
                )}
              </div>
            )}

            {/* Error panel */}
            {done && error && (
              <div className="card p-5 border-red-500/30 animate-fade-in">
                <div className="flex items-center gap-2 mb-2">
                  <span className="w-2 h-2 rounded-full bg-red-400" />
                  <span className="text-sm font-medium text-red-400">Generation Failed</span>
                </div>
                <p className="text-gray-400 text-xs mb-4">{error}</p>
                {jobId && (
                  <Link to={`/projects/${jobId}`} className="text-sm text-violet-400 hover:text-violet-300">
                    View logs →
                  </Link>
                )}
              </div>
            )}

            {/* Log terminal */}
            {(isBuilding || done) && logs.length > 0 && (
              <div className="card p-4 animate-fade-in">
                <div className="text-xs font-mono text-gray-500 uppercase tracking-wide mb-3">Live Logs</div>
                <div className="bg-gray-950 rounded-lg p-3 max-h-56 overflow-y-auto font-mono text-xs text-gray-400 space-y-0.5">
                  {logs.map((line, i) => (
                    <div key={i} className="leading-relaxed">
                      <span className="text-gray-600 select-none mr-2">&gt;</span>
                      {line}
                    </div>
                  ))}
                  <div ref={logsEndRef} />
                </div>
              </div>
            )}

            {/* Idle state */}
            {!isBuilding && !done && (
              <div className="card p-8 text-center text-gray-600">
                <div className="text-4xl mb-3">⚡</div>
                <p className="text-sm">Describe your app and click Generate.<br />The full pipeline runs in 3–5 minutes.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
