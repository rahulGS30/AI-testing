import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'

const API_BASE = 'http://127.0.0.1:8000'
const TERMINAL_STATUSES = new Set([
  'completed',
  'blocked',
  'incomplete',
  'max_steps',
  'failed',
])

type Persona = {
  persona_id: string
  name: string
  description: string
}

type TestCase = {
  test_case_id: string
  name: string
  website: string
  goal: string
}

type TokenTotals = {
  call_count: number
  usage_reported_count: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cached_input_tokens: number
  reasoning_tokens: number
}

type Evidence = {
  step: number
  stage: string
  source: string
  quote: string
}

type Finding = {
  observation: string
  persona_impact: string
  recommendation: string
  priority: string
  evidence: Evidence[]
}

type AnalysisReport = {
  status: string
  summary?: string
  strengths?: Finding[]
  improvements?: Finding[]
  not_assessed?: string[]
  evidence_scope?: string
  error?: string
}

type ArtifactUrls = {
  result: string
  action_history: string
  ux_analysis: string
  token_usage: string | null
  video: string | null
  screenshots: string[]
}

type RunRecord = {
  run_id: string
  batch_id?: string | null
  test_case_id: string
  name: string
  website: string
  persona_id: string
  persona_name: string
  goal: string
  status: string
  completed: boolean
  step_count: number
  started_at: string
  finished_at?: string | null
  duration_seconds?: number
  result?: string | null
  error?: string | null
  analysis: AnalysisReport
  token_usage?: TokenTotals
  artifact_urls: ArtifactUrls
}

type ActionRecord = {
  step: number
  decision?: {
    action?: string
    target?: string | null
    value?: string | null
    reason?: string
  }
  result?: string
  error?: string
  before?: { url?: string; title?: string }
  after?: { url?: string; title?: string }
}

type ActionHistory = {
  initial_observation?: { url?: string; title?: string }
  actions: ActionRecord[]
}

type TokenUsageFile = {
  calls: Array<{
    call_type: string
    usage_reported: boolean
    input_tokens: number | null
    output_tokens: number | null
    total_tokens: number | null
    recorded_at: string
  }>
  totals: TokenTotals
  by_call_type: Record<string, TokenTotals>
}

type View = 'run' | 'history' | 'reports' | 'profile'
type ApiState = 'loading' | 'connected' | 'unavailable'

type PendingBatch = {
  testCaseId: string
  runIds: string[]
}

async function requestJson<T>(
  path: string,
  options?: RequestInit,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...options, signal })
  if (!response.ok) {
    let message = `Request failed (${response.status}).`
    try {
      const body: { detail?: unknown } = await response.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (Array.isArray(body.detail)) {
        const validationIssue = body.detail.find(
          (item): item is { msg?: unknown } =>
            typeof item === 'object' && item !== null && 'msg' in item,
        )
        if (typeof validationIssue?.msg === 'string') message = validationIssue.msg
      }
    } catch {
      // The response may not contain JSON.
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

function mergeRuns(existing: RunRecord[], incoming: RunRecord[]): RunRecord[] {
  const byId = new Map(existing.map((run) => [run.run_id, run]))
  incoming.forEach((run) => byId.set(run.run_id, run))
  return [...byId.values()].sort((a, b) =>
    (b.started_at || '').localeCompare(a.started_at || ''),
  )
}

function formatNumber(value: number | undefined): string {
  return (value ?? 0).toLocaleString()
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function statusLabel(status: string): string {
  return status.replaceAll('_', ' ')
}

function websiteHost(website: string): string {
  try {
    return new URL(website).hostname
  } catch {
    return website || 'Unknown website'
  }
}

function App() {
  const [apiState, setApiState] = useState<ApiState>('loading')
  const [personas, setPersonas] = useState<Persona[]>([])
  const [testCases, setTestCases] = useState<TestCase[]>([])
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [activeView, setActiveView] = useState<View>('run')
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [showIndividualReport, setShowIndividualReport] = useState(false)
  const [url, setUrl] = useState('')
  const [task, setTask] = useState('')
  const [selectedPersonaIds, setSelectedPersonaIds] = useState<string[]>([])
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [pendingBatch, setPendingBatch] = useState<PendingBatch | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [showBrowser, setShowBrowser] = useState(false)
  const [actionHistory, setActionHistory] = useState<ActionHistory | null>(null)
  const [tokenDetails, setTokenDetails] = useState<TokenUsageFile | null>(null)
  const [artifactError, setArtifactError] = useState('')

  useEffect(() => {
    const controller = new AbortController()

    async function loadDashboard() {
      try {
        const [health, availablePersonas, availableCases, recentRuns] =
          await Promise.all([
            requestJson<{ status: string }>('/api/health', undefined, controller.signal),
            requestJson<Persona[]>('/api/personas', undefined, controller.signal),
            requestJson<TestCase[]>('/api/test-cases', undefined, controller.signal),
            requestJson<RunRecord[]>('/api/runs', undefined, controller.signal),
          ])
        if (health.status !== 'ok') throw new Error('Backend health check failed.')
        setPersonas(availablePersonas)
        setTestCases(availableCases)
        setRuns(recentRuns)
        setApiState('connected')
      } catch (loadError) {
        if (loadError instanceof DOMException && loadError.name === 'AbortError') return
        setError(loadError instanceof Error ? loadError.message : 'Could not load the dashboard.')
        setApiState('unavailable')
      }
    }

    void loadDashboard()
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!pendingBatch) return
    let cancelled = false
    let timer: number | undefined

    async function pollRunStatus() {
      try {
        const latestRuns = await requestJson<RunRecord[]>(
          `/api/test-cases/${encodeURIComponent(pendingBatch!.testCaseId)}/runs`,
        )
        if (cancelled) return
        const batchRuns = latestRuns.filter((run) =>
          pendingBatch!.runIds.includes(run.run_id),
        )
        setRuns((existing) => mergeRuns(existing, batchRuns))
        if (
          batchRuns.length === pendingBatch!.runIds.length
          && batchRuns.every((run) => TERMINAL_STATUSES.has(run.status))
        ) {
          setPendingBatch(null)
          setNotice('Run finished. Its report and artifacts are available below.')
          return
        }
      } catch (pollError) {
        if (!cancelled) {
          setError(pollError instanceof Error ? pollError.message : 'Could not refresh run status.')
        }
      }
      if (!cancelled) timer = window.setTimeout(pollRunStatus, 1800)
    }

    void pollRunStatus()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [pendingBatch])

  const selectedRun = useMemo(
    () => runs.find((run) => run.run_id === selectedRunId) ?? null,
    [runs, selectedRunId],
  )
  const selectedBatchRuns = useMemo(() => {
    if (!selectedRun?.batch_id) return selectedRun ? [selectedRun] : []
    const batchRuns = runs
      .filter(
        (run) =>
          run.batch_id === selectedRun.batch_id
          && run.test_case_id === selectedRun.test_case_id,
      )
      .sort((left, right) => left.persona_name.localeCompare(right.persona_name))
    return batchRuns.length > 0 ? batchRuns : [selectedRun]
  }, [runs, selectedRun])
  const totalTokens = runs.reduce(
    (total, run) => total + (run.token_usage?.total_tokens ?? 0),
    0,
  )
  const totalCalls = runs.reduce(
    (total, run) => total + (run.token_usage?.call_count ?? 0),
    0,
  )

  useEffect(() => {
    setActionHistory(null)
    setTokenDetails(null)
    setArtifactError('')
    if (!selectedRun || !TERMINAL_STATUSES.has(selectedRun.status)) return

    const controller = new AbortController()
    function reportArtifactError(loadError: unknown) {
      if (controller.signal.aborted) return
      setArtifactError(
        loadError instanceof Error
          ? loadError.message
          : 'A run artifact could not be loaded.',
      )
    }

    async function loadReportArtifacts() {
      const tasks: Promise<void>[] = [
        requestJson<ActionHistory>(
          selectedRun!.artifact_urls.action_history,
          undefined,
          controller.signal,
        )
          .then(setActionHistory)
          .catch((loadError: unknown) => {
            setActionHistory(null)
            reportArtifactError(loadError)
          }),
      ]
      if (selectedRun!.artifact_urls.token_usage) {
        tasks.push(
          requestJson<TokenUsageFile>(
            selectedRun!.artifact_urls.token_usage,
            undefined,
            controller.signal,
          )
            .then(setTokenDetails)
            .catch((loadError: unknown) => {
              setTokenDetails(null)
              reportArtifactError(loadError)
            }),
        )
      }
      await Promise.all(tasks)
    }

    void loadReportArtifacts()
    return () => controller.abort()
  }, [selectedRun?.run_id, selectedRun?.status, selectedRun?.artifact_urls])

  function togglePersona(personaId: string) {
    setSelectedPersonaIds((selected) =>
      selected.includes(personaId)
        ? selected.filter((id) => id !== personaId)
        : [...selected, personaId],
    )
  }

  function selectPreset(testCaseId: string) {
    const testCase = testCases.find((item) => item.test_case_id === testCaseId)
    if (!testCase) return
    setUrl(testCase.website)
    setTask(testCase.goal)
    setError('')
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (selectedPersonaIds.length === 0) {
      setError('Select at least one persona.')
      return
    }
    setIsSubmitting(true)
    setError('')
    setNotice('')
    try {
      const response = await requestJson<{
        test_case_id: string
        batch_id: string
        runs: RunRecord[]
      }>('/api/runs/custom', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          website: url,
          task,
          persona_ids: selectedPersonaIds,
          headed: showBrowser,
        }),
      })
      setRuns((existing) => mergeRuns(existing, response.runs))
      const runIds = response.runs.map((run) => run.run_id)
      setSelectedRunId(runIds[0] ?? null)
      setShowIndividualReport(false)
      setPendingBatch({ testCaseId: response.test_case_id, runIds })
      setActiveView('reports')
      setNotice(`Started ${response.runs.length} persona run${response.runs.length === 1 ? '' : 's'}.`)
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Could not start the run.')
    } finally {
      setIsSubmitting(false)
    }
  }

  async function refreshRuns() {
    setIsRefreshing(true)
    setError('')
    try {
      const recentRuns = await requestJson<RunRecord[]>('/api/runs')
      setRuns(recentRuns)
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : 'Could not refresh run history.')
    } finally {
      setIsRefreshing(false)
    }
  }

  function openReport(run: RunRecord) {
    setSelectedRunId(run.run_id)
    setShowIndividualReport(false)
    setActiveView('reports')
    setError('')
  }

  function openIndividualReport(run: RunRecord) {
    setSelectedRunId(run.run_id)
    setShowIndividualReport(true)
    setActiveView('reports')
  }

  const groupedRuns = runs.reduce<Record<string, RunRecord[]>>((groups, run) => {
    const hostname = websiteHost(run.website)
    groups[hostname] = [...(groups[hostname] ?? []), run]
    return groups
  }, {})

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Persona UX home">
          <span className="brand-mark" aria-hidden="true">P</span>
          <span>persona<span className="brand-light">lab</span></span>
        </a>
        <div className={`connection-status ${apiState}`} role="status">
          <span className="status-dot" aria-hidden="true" />
          {apiState === 'loading' ? 'Connecting' : apiState === 'connected' ? 'Backend connected' : 'Backend unavailable'}
        </div>
      </header>

      <div className="dashboard-layout">
        <aside className="sidebar">
          <div className="sidebar-label">WORKSPACE</div>
          <nav className="sidebar-nav" aria-label="Workspace navigation">
            <button className={`sidebar-link${activeView === 'run' ? ' active' : ''}`} onClick={() => setActiveView('run')} type="button">
              <span className="nav-icon" aria-hidden="true">＋</span><span>Run a test</span>
            </button>
            <button className={`sidebar-link${activeView === 'history' ? ' active' : ''}`} onClick={() => setActiveView('history')} type="button">
              <span className="nav-icon" aria-hidden="true">◷</span><span>History</span><span className="nav-count">{runs.length}</span>
            </button>
            <button className={`sidebar-link${activeView === 'reports' ? ' active' : ''}`} onClick={() => { setSelectedRunId(null); setShowIndividualReport(false); setActiveView('reports') }} type="button">
              <span className="nav-icon" aria-hidden="true">▤</span><span>Reports</span>
            </button>
            <button className={`sidebar-link${activeView === 'profile' ? ' active' : ''}`} onClick={() => setActiveView('profile')} type="button">
              <span className="nav-icon" aria-hidden="true">◎</span><span>Profile</span>
            </button>
          </nav>

          <section className="usage-card" aria-label="Token usage overview">
            <p className="sidebar-label">TOKEN USAGE</p>
            <strong>{formatNumber(totalTokens)}</strong>
            <span>reported tokens</span>
            <div className="usage-meta">{formatNumber(totalCalls)} model calls · {runs.length} runs</div>
          </section>

          <div className="sidebar-bottom">
            <div className="sidebar-user">
              <span className="user-avatar" aria-hidden="true">DU</span>
              <span className="user-details"><strong>Demo User</strong><span>Local workspace</span></span>
            </div>
            <button className="sidebar-link logout-link" type="button" onClick={() => setNotice('Logout is not connected because this workspace has no authentication yet.')}>
              <span className="nav-icon" aria-hidden="true">↪</span><span>Log out</span>
            </button>
          </div>
        </aside>

        <section className="workspace-content">
          {error && <div className="message error-message" role="alert">{error}</div>}
          {notice && <div className="message notice-message" role="status">{notice}</div>}

          {activeView === 'run' && (
            <>
              <section className="intro">
                <p className="eyebrow">AI PERSONA UX TESTING</p>
                <h1>Test a journey.<br /><span>See it through their eyes.</span></h1>
                <p className="intro-copy">Enter a public website and task, select personas, then inspect their recorded runs and evidence.</p>
              </section>

              <form className="run-form" onSubmit={handleSubmit}>
                <div className="section-heading">
                  <div><p className="eyebrow">01 / TEST SETUP</p><h2>Choose a website and task</h2></div>
                  {testCases.length > 0 && (
                    <label className="preset-picker">
                      <span>Load saved scenario</span>
                      <select defaultValue="" onChange={(event) => selectPreset(event.target.value)}>
                        <option value="">Choose a scenario</option>
                        {testCases.map((testCase) => <option key={testCase.test_case_id} value={testCase.test_case_id}>{testCase.test_case_id} · {testCase.name}</option>)}
                      </select>
                    </label>
                  )}
                </div>
                <label className="field-label" htmlFor="website-url">Website URL</label>
                <input id="website-url" className="text-input" type="url" placeholder="https://example.com" value={url} onChange={(event) => setUrl(event.target.value)} required />
                <label className="field-label task-label" htmlFor="test-task">What should the persona try?</label>
                <textarea id="test-task" className="text-input task-input" placeholder="For example: Find the help page and locate the return policy." value={task} onChange={(event) => setTask(event.target.value)} rows={3} maxLength={1000} required />
                <p className="form-hint">Only public HTTPS websites are accepted. Sign-in, checkout, purchases, and personal or payment information remain out of scope.</p>

                <section className="persona-section" aria-labelledby="personas-title">
                  <div className="section-heading">
                    <div><p className="eyebrow">02 / CHOOSE PERSPECTIVES</p><h2 id="personas-title">Select personas</h2></div>
                    <span className="persona-count">{selectedPersonaIds.length} selected</span>
                  </div>
                  <div className="persona-grid">
                    {personas.map((persona, index) => {
                      const selected = selectedPersonaIds.includes(persona.persona_id)
                      const id = `persona-${persona.persona_id}`
                      return (
                        <label className={`persona-card${selected ? ' selected' : ''}`} htmlFor={id} key={persona.persona_id}>
                          <input id={id} className="persona-checkbox" type="checkbox" checked={selected} onChange={() => togglePersona(persona.persona_id)} />
                          <span className={`persona-avatar avatar-${(index % 5) + 1}`} aria-hidden="true">{persona.name.slice(0, 1)}</span>
                          <span className="persona-name">{persona.name}</span>
                          <span className="persona-description">{persona.description}</span>
                        </label>
                      )
                    })}
                    {apiState === 'loading' && <p className="loading-state">Loading personas…</p>}
                  </div>
                </section>

                <label className="headed-toggle">
                  <input type="checkbox" checked={showBrowser} onChange={(event) => setShowBrowser(event.target.checked)} />
                  <span>Show the browser while the test runs</span>
                </label>
                <div className="form-actions">
                  <p>{pendingBatch ? 'A run is currently in progress.' : 'Runs stop on security checks and never proceed to checkout or purchase.'}</p>
                  <button className="run-button" type="submit" disabled={apiState !== 'connected' || isSubmitting || !!pendingBatch || selectedPersonaIds.length === 0}>
                    {isSubmitting ? 'Starting…' : pendingBatch ? 'Run in progress…' : 'Run test'}<span aria-hidden="true">→</span>
                  </button>
                </div>
              </form>
            </>
          )}

          {activeView === 'history' && (
            <section className="page-panel">
              <p className="eyebrow">YOUR ACTIVITY</p>
              <div className="page-panel-heading">
                <div><h1>Run history</h1><p>Runs grouped by website, newest first.</p></div>
                <div className="heading-actions"><span className="persona-count">{runs.length} runs</span><button className="text-button" type="button" onClick={() => void refreshRuns()} disabled={isRefreshing}>{isRefreshing ? 'Refreshing…' : 'Refresh'}</button></div>
              </div>
              {Object.keys(groupedRuns).length === 0 ? (
                <div className="empty-state"><span className="empty-icon">◷</span><strong>No runs yet</strong><p>Submitted runs will be saved here.</p></div>
              ) : (
                <div className="history-list">
                  {Object.entries(groupedRuns).map(([website, websiteRuns]) => (
                    <section className="history-group" key={website}>
                      <h2>{website}<span>{websiteRuns.length} run{websiteRuns.length === 1 ? '' : 's'}</span></h2>
                      {websiteRuns.map((run) => (
                        <article className="history-item" key={run.run_id}>
                          <div className="history-item-main">
                            <strong>{run.goal}</strong>
                            <span>{run.persona_name} · {formatDate(run.started_at)}</span>
                          </div>
                          <span className={`status-pill status-${run.status}`}>{statusLabel(run.status)}</span>
                          <span className="history-tokens">{formatNumber(run.token_usage?.total_tokens)} tokens</span>
                          <button className="text-button" type="button" onClick={() => openReport(run)}>View report →</button>
                        </article>
                      ))}
                    </section>
                  ))}
                </div>
              )}
            </section>
          )}

          {activeView === 'reports' && (
            selectedRun ? (
              selectedBatchRuns.length > 1 && !showIndividualReport ? (
                <PersonaBatchReport
                  runs={selectedBatchRuns}
                  onOpenRun={openIndividualReport}
                  onBack={() => { setSelectedRunId(null); setShowIndividualReport(false) }}
                />
              ) : (
                <RunReport
                  run={selectedRun}
                  actionHistory={actionHistory}
                  tokenDetails={tokenDetails}
                  artifactError={artifactError}
                  onBack={() => { setSelectedRunId(null); setShowIndividualReport(false) }}
                />
              )
            ) : (
              <section className="page-panel">
                <p className="eyebrow">EVIDENCE & FINDINGS</p>
                <div className="page-panel-heading"><div><h1>Reports</h1><p>Select a run to inspect its analysis and artifacts.</p></div><button className="text-button" type="button" onClick={() => void refreshRuns()} disabled={isRefreshing}>{isRefreshing ? 'Refreshing…' : 'Refresh'}</button></div>
                {runs.length === 0 ? (
                  <div className="empty-state"><span className="empty-icon">▤</span><strong>No reports yet</strong><p>Run a test to create a report.</p></div>
                ) : (
                  <div className="history-list">
                    {runs.map((run) => (
                      <article className="report-list-item" key={run.run_id}>
                        <div><strong>{run.name}</strong><span>{websiteHost(run.website)} · {run.persona_name}</span><p>{run.analysis?.summary || run.result || 'Analysis pending.'}</p></div>
                        <span className={`status-pill status-${run.status}`}>{statusLabel(run.status)}</span>
                        <button className="text-button" type="button" onClick={() => openReport(run)}>Open →</button>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            )
          )}

          {activeView === 'profile' && (
            <section className="page-panel">
              <p className="eyebrow">ACCOUNT</p>
              <div className="page-panel-heading"><div><h1>User profile</h1><p>Local preview account.</p></div></div>
              <div className="profile-card"><span className="profile-avatar">DU</span><div><h2>Demo User</h2><p>Authentication and profile settings are not connected yet.</p></div><span className="profile-badge">LOCAL</span></div>
            </section>
          )}
        </section>
      </div>

      <footer className="page-footer">
        <span>AI Persona UX Testing Agent</span>
        <span>Simulated personas support testing; they do not replace user research.</span>
      </footer>
    </main>
  )
}

function PersonaBatchReport({
  runs,
  onOpenRun,
  onBack,
}: {
  runs: RunRecord[]
  onOpenRun: (run: RunRecord) => void
  onBack: () => void
}) {
  const firstRun = runs[0]
  const finishedRuns = runs.filter((run) => TERMINAL_STATUSES.has(run.status)).length
  const issueCount = runs.reduce(
    (total, run) => total + (run.analysis?.improvements?.length ?? 0),
    0,
  )

  return (
    <section className="page-panel report-panel">
      <button className="back-button" onClick={onBack} type="button">← All reports</button>
      <p className="eyebrow">PERSONA COMPARISON · {firstRun.test_case_id}</p>
      <div className="report-heading">
        <div>
          <h1>{firstRun.name}</h1>
          <a href={firstRun.website} target="_blank" rel="noreferrer">{firstRun.website}</a>
        </div>
        <span className="persona-count">{runs.length} personas</span>
      </div>
      <p className="batch-report-intro">
        This report groups the selected personas’ separate test runs. Each issue below is tied to the
        persona whose run surfaced it; findings are based on observed evidence, not assumptions.
      </p>
      <div className="batch-report-metrics">
        <div><strong>{runs.length}</strong><span>personas selected</span></div>
        <div><strong>{finishedRuns} / {runs.length}</strong><span>runs finished</span></div>
        <div><strong>{issueCount}</strong><span>potential issues found</span></div>
      </div>

      <div className="persona-report-list">
        {runs.map((run) => (
          <article className="persona-report-card" key={run.run_id}>
            <div className="persona-report-heading">
              <div>
                <p className="eyebrow">PERSONA</p>
                <h2>{run.persona_name}</h2>
              </div>
              <span className={`status-pill status-${run.status}`}>{statusLabel(run.status)}</span>
            </div>
            {run.analysis?.summary && <p className="analysis-summary">{run.analysis.summary}</p>}
            {run.result && <p className="run-outcome">{run.result}</p>}
            {run.analysis?.status === 'completed' ? (
              run.analysis.improvements?.length ? (
                <FindingList
                  title={`Potential challenges for ${run.persona_name}`}
                  findings={run.analysis.improvements}
                  kind="improvement"
                />
              ) : (
                <p className="muted-copy">No persona-specific usability issues were identified in this run.</p>
              )
            ) : (
              <p className="muted-copy">
                {run.analysis?.error || 'This persona’s analysis is still pending.'}
              </p>
            )}
            <FindingList title="What worked for this persona" findings={run.analysis?.strengths ?? []} kind="strength" />
            {(run.analysis?.not_assessed?.length ?? 0) > 0 && (
              <div className="not-assessed">
                <h3>Not assessed in this run</h3>
                <ul>{run.analysis.not_assessed?.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
            )}
            <button className="text-button persona-evidence-button" onClick={() => onOpenRun(run)} type="button">
              View this persona’s screenshots and actions →
            </button>
          </article>
        ))}
      </div>
    </section>
  )
}

function RunReport({
  run,
  actionHistory,
  tokenDetails,
  artifactError,
  onBack,
}: {
  run: RunRecord
  actionHistory: ActionHistory | null
  tokenDetails: TokenUsageFile | null
  artifactError: string
  onBack: () => void
}) {
  const tokenUsage = run.token_usage
  return (
    <section className="page-panel report-panel">
      <button className="back-button" onClick={onBack} type="button">← All reports</button>
      <p className="eyebrow">RUN REPORT · {run.test_case_id}</p>
      <div className="report-heading">
        <div><h1>{run.name}</h1><a href={run.website} target="_blank" rel="noreferrer">{run.website}</a></div>
        <span className={`status-pill status-${run.status}`}>{statusLabel(run.status)}</span>
      </div>
      <div className="run-meta">
        <span><strong>Persona</strong>{run.persona_name}</span>
        <span><strong>Started</strong>{formatDate(run.started_at)}</span>
        <span><strong>Duration</strong>{run.duration_seconds === undefined ? '—' : `${run.duration_seconds}s`}</span>
        <span><strong>Steps</strong>{run.step_count}</span>
      </div>
      {(run.status === 'queued' || run.status === 'running') && <div className="progress-notice">The browser test is running. This report will refresh automatically.</div>}
      {run.error && <div className="message error-message">{run.error}</div>}
      {artifactError && <div className="message error-message" role="status">{artifactError}</div>}
      {run.result && <p className="run-outcome">{run.result}</p>}

      <section className="report-section">
        <div className="section-heading"><div><p className="eyebrow">AI REVIEW</p><h2>Usability report</h2></div><div className="heading-actions">{run.artifact_urls.ux_analysis && <a className="download-link" href={`${API_BASE}${run.artifact_urls.ux_analysis}`} target="_blank" rel="noreferrer">Open report JSON ↗</a>}<span className={`status-pill status-${run.analysis?.status || 'pending'}`}>{statusLabel(run.analysis?.status || 'pending')}</span></div></div>
        {run.analysis?.summary ? <p className="analysis-summary">{run.analysis.summary}</p> : <p className="muted-copy">{run.analysis?.error || 'The report will be available when analysis finishes.'}</p>}
        {run.analysis?.evidence_scope && <p className="evidence-scope"><strong>Evidence scope:</strong> {run.analysis.evidence_scope}</p>}
        <FindingList title="Strengths" findings={run.analysis?.strengths ?? []} kind="strength" />
        <FindingList title="Improvements" findings={run.analysis?.improvements ?? []} kind="improvement" />
        {(run.analysis?.not_assessed?.length ?? 0) > 0 && (
          <div className="not-assessed"><h3>Not assessed</h3><ul>{run.analysis.not_assessed?.map((item) => <li key={item}>{item}</li>)}</ul></div>
        )}
      </section>

      <section className="report-section">
        <div className="section-heading"><div><p className="eyebrow">MODEL USAGE</p><h2>Token usage</h2></div>{run.artifact_urls.token_usage && <a className="download-link" href={`${API_BASE}${run.artifact_urls.token_usage}`} target="_blank" rel="noreferrer">Open usage JSON ↗</a>}</div>
        <div className="metric-grid">
          <Metric label="Total tokens" value={tokenUsage?.total_tokens} />
          <Metric label="Input tokens" value={tokenUsage?.input_tokens} />
          <Metric label="Output tokens" value={tokenUsage?.output_tokens} />
          <Metric label="Model calls" value={tokenUsage?.call_count} />
        </div>
        {tokenDetails && <div className="usage-breakdown">{Object.entries(tokenDetails.by_call_type).map(([name, counts]) => <div key={name}><span>{statusLabel(name)}</span><strong>{formatNumber(counts.total_tokens)} tokens</strong><small>{counts.call_count} calls · {formatNumber(counts.input_tokens)} input · {formatNumber(counts.output_tokens)} output</small></div>)}</div>}
      </section>

      <section className="report-section">
        <div className="section-heading"><div><p className="eyebrow">RECORDED MEDIA</p><h2>Journey capture</h2></div><span className="persona-count">{run.artifact_urls.screenshots.length} screenshots</span></div>
        {run.artifact_urls.video ? <video className="journey-video" controls preload="metadata" src={`${API_BASE}${run.artifact_urls.video}`}>Your browser does not support WebM video.</video> : <p className="muted-copy">No video was saved for this run.</p>}
        {run.artifact_urls.screenshots.length > 0 ? (
          <div className="screenshot-grid">
            {run.artifact_urls.screenshots.map((screenshot, index) => (
              <a className="screenshot-card" href={`${API_BASE}${screenshot}`} target="_blank" rel="noreferrer" key={screenshot}>
                <img src={`${API_BASE}${screenshot}`} alt={`Browser screenshot at step ${index}`} loading="lazy" />
                <span>Step {String(index).padStart(2, '0')} · Open image ↗</span>
              </a>
            ))}
          </div>
        ) : <p className="muted-copy">No screenshots were saved for this run.</p>}
      </section>

      <section className="report-section">
        <div className="section-heading"><div><p className="eyebrow">EXECUTION LOG</p><h2>Browser actions</h2></div><span className="persona-count">{actionHistory?.actions.length ?? 0} actions</span></div>
        {actionHistory ? (
          <ol className="action-list">
            {actionHistory.actions.map((action) => (
              <li key={action.step}>
                <span className="action-step">{String(action.step).padStart(2, '0')}</span>
                <div><strong>{action.decision?.action || 'Action'}{action.decision?.target ? ` · ${action.decision.target}` : ''}</strong>{action.decision?.value && <span>Value: {action.decision.value}</span>}{action.error && <span className="action-error">{action.error}</span>}{action.result && <span>{action.result}</span>}{action.after?.url && <a href={action.after.url} target="_blank" rel="noreferrer">{action.after.url}</a>}</div>
              </li>
            ))}
          </ol>
        ) : <p className="muted-copy">Action history will appear when the run finishes.</p>}
      </section>
    </section>
  )
}

function FindingList({
  title,
  findings,
  kind,
}: {
  title: string
  findings: Finding[]
  kind: 'strength' | 'improvement'
}) {
  if (findings.length === 0) return null
  return (
    <div className={`finding-group ${kind}`}>
      <h3>{title}</h3>
      {findings.map((finding, index) => (
        <article className="finding-card" key={`${finding.observation}-${index}`}>
          <div className="finding-title"><strong>{finding.observation}</strong><span className={`priority priority-${finding.priority}`}>{finding.priority}</span></div>
          <p><strong>Persona impact:</strong> {finding.persona_impact}</p>
          <p><strong>Recommendation:</strong> {finding.recommendation}</p>
          {finding.evidence.length > 0 && <div className="evidence-quotes">{finding.evidence.map((item, evidenceIndex) => <blockquote key={`${item.step}-${evidenceIndex}`}><q>{item.quote}</q><cite>Step {item.step} · {item.stage} · {item.source}</cite></blockquote>)}</div>}
        </article>
      ))}
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number | undefined }) {
  return <div className="metric-card"><span>{label}</span><strong>{formatNumber(value)}</strong></div>
}

export default App
