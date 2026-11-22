(() => {
  'use strict'
  // Poll the status page in the background instead of reloading the whole screen every few
  // seconds (which flashed the page and restarted every animation).
  const root = document.getElementById('atelier-wait')
  if (!root) return
  const stageText = document.getElementById('atelier-stage')
  const bar = document.getElementById('atelier-progress-bar')
  const elapsedText = document.getElementById('atelier-elapsed')
  const started = Number(root.dataset.started || 0) * 1000 || Date.now()
  const expected = Number(root.dataset.expected || 45)
  const stages = [
    'Reading your pose',
    'Fitting the gown to your shoulders',
    'Adding folds, sleeves and trim',
    'Putting back your own face',
    ...(root.dataset.hasCap ? ['Placing the cap'] : []),
    'Finishing the portrait',
  ]
  let stage = -1

  function tick() {
    const seconds = Math.max(0, (Date.now() - started) / 1000)
    // Eases towards 95% around the expected time and never claims to be finished.
    const progress = Math.min(95, 100 * (1 - Math.exp(-seconds / (expected * 0.6))))
    bar.style.width = `${progress.toFixed(1)}%`
    const whole = Math.floor(seconds)
    elapsedText.textContent = `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
    const next = Math.min(stages.length - 1, Math.floor((progress / 95) * stages.length))
    if (next !== stage) {
      stage = next
      stageText.classList.add('is-changing')
      window.setTimeout(() => { stageText.textContent = stages[stage]; stageText.classList.remove('is-changing') }, 300)
    }
  }

  async function poll() {
    try {
      const response = await fetch(root.dataset.statusUrl, { cache: 'no-store', credentials: 'same-origin' })
      if (response.redirected) { window.location.replace(response.url); return }
      const html = await response.text()
      // The status page drops the waiting panel once the job has failed: show that page.
      if (response.ok && !html.includes('id="atelier-wait"')) { window.location.reload(); return }
    } catch {
      // Network blip: keep waiting and try again.
    }
    window.setTimeout(poll, 2500)
  }

  tick()
  window.setInterval(tick, 1000)
  window.setTimeout(poll, 2500)
})()
