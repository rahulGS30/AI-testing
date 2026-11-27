import { useEffect, useState } from 'react'
import './App.css'

type Persona = {
  persona_id: string
  name: string
  description: string
}

type ApiState = 'loading' | 'connected' | 'unavailable'

const testCases = [
  {
    id: 'TC-002',
    name: 'Search for iPhone 17 on Amazon',
    steps: ['Open Amazon', 'Search “iPhone 17”', 'Add a result to cart'],
  },
  {
    id: 'TC-003',
    name: 'Find an Argentina jacket',
    steps: [
      'Open Thayyil Sports',
      'Search “jacket argentina”',
      'Add the first result to cart',
    ],
  },
]

function App() {
  const [apiState, setApiState] = useState<ApiState>('loading')
  const [personas, setPersonas] = useState<Persona[]>([])

  useEffect(() => {
    const controller = new AbortController()

    async function loadFoundationStatus() {
      try {
        const [healthResponse, personasResponse] = await Promise.all([
          fetch('http://127.0.0.1:8000/api/health', {
            signal: controller.signal,
          }),
          fetch('http://127.0.0.1:8000/api/personas', {
            signal: controller.signal,
          }),
        ])

        if (!healthResponse.ok || !personasResponse.ok) {
          throw new Error('The backend returned an unsuccessful response.')
        }

        const availablePersonas: Persona[] = await personasResponse.json()
        setPersonas(availablePersonas)
        setApiState('connected')
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        setApiState('unavailable')
      }
    }

    void loadFoundationStatus()
    return () => controller.abort()
  }, [])

  const apiLabel = {
    loading: 'Checking backend',
    connected: 'Backend connected',
    unavailable: 'Backend unavailable',
  }[apiState]

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Persona UX home">
          <span className="brand-mark" aria-hidden="true">P</span>
          <span>persona<span className="brand-light">lab</span></span>
        </a>
        <div className={`connection-status ${apiState}`} role="status">
          <span className="status-dot" aria-hidden="true" />
          {apiLabel}
        </div>
      </header>

      <section className="intro">
        <p className="eyebrow">AI PERSONA UX TESTING</p>
        <h1>Test a journey.<br /><span>See it through their eyes.</span></h1>
        <p className="intro-copy">
          Simulate real-world perspectives, capture the journey, and uncover
          usability friction with evidence.
        </p>
      </section>

      <section className="scenarios-section" aria-labelledby="scenarios-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">READY TO RUN FROM THE TERMINAL</p>
            <h2 id="scenarios-title">Test cases</h2>
          </div>
          <span className="persona-count">{testCases.length} available</span>
        </div>

        <div className="scenario-grid">
          {testCases.map((testCase) => (
            <article className="journey-card" key={testCase.id}>
              <div className="card-heading">
                <div>
                  <p className="eyebrow">TEST CASE {testCase.id}</p>
                  <h3>{testCase.name}</h3>
                </div>
                <span className="phase-tag">CLI READY</span>
              </div>

              <div className="journey-flow" aria-label={`${testCase.id} steps`}>
                {testCase.steps.map((step, index) => (
                  <div className="journey-step-wrap" key={step}>
                    {index > 0 && <span className="step-arrow" aria-hidden="true">→</span>}
                    <div className="journey-step">
                      <span className="step-number">{String(index + 1).padStart(2, '0')}</span>
                      <span>{step}</span>
                    </div>
                  </div>
                ))}
              </div>

              <div className="safety-note">
                <span className="shield-icon" aria-hidden="true">✓</span>
                <p>Stops at cart confirmation. No sign-in, checkout, or purchase.</p>
              </div>

              <div className="run-footer">
                <span>Run from the workspace root:</span>
                <code>.\.venv\Scripts\python.exe backend\run_test.py {testCase.id}</code>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="personas-section" aria-labelledby="personas-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">READY TO SIMULATE</p>
            <h2 id="personas-title">Personas</h2>
          </div>
          <span className="persona-count">
            {personas.length ? `${personas.length} available` : 'Loading'}
          </span>
        </div>
        {apiState === 'unavailable' ? (
          <p className="empty-state">
            Start the backend to load personas. See <code>docs/setup.md</code>.
          </p>
        ) : (
          <div className="persona-grid">
            {personas.map((persona, index) => (
              <article className="persona-card" key={persona.persona_id}>
                <span className={`persona-avatar avatar-${index + 1}`} aria-hidden="true">
                  {persona.name.slice(0, 1)}
                </span>
                <h3>{persona.name}</h3>
                <p>{persona.description}</p>
              </article>
            ))}
            {apiState === 'loading' && (
              <p className="loading-state">Connecting to the local API…</p>
            )}
          </div>
        )}
      </section>

      <footer className="page-footer">
        <span>AI Persona UX Testing Agent</span>
        <span>Simulated personas support testing; they do not replace user research.</span>
      </footer>
    </main>
  )
}

export default App
