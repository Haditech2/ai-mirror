(() => {
  'use strict'

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => undefined))
  }

  const kiosk = document.getElementById('guest-kiosk')
  if (kiosk) {
    const offline = document.getElementById('offline-banner')
    const updateNetwork = () => {
      if (!offline) return
      offline.classList.toggle('hidden', navigator.onLine)
      offline.classList.toggle('flex', !navigator.onLine)
    }
    window.addEventListener('online', updateNetwork)
    window.addEventListener('offline', updateNetwork)
    updateNetwork()
    document.addEventListener('contextmenu', event => event.preventDefault())

    window.history.pushState(null, '', window.location.href)
    window.addEventListener('popstate', () => window.history.pushState(null, '', window.location.href))

    if (kiosk.dataset.inactivity === 'true') {
      const timeout = Math.max(45, Number(kiosk.dataset.timeout || 120))
      const warning = document.getElementById('timeout-warning')
      const secondsNode = document.getElementById('timeout-seconds')
      const continueButton = document.getElementById('timeout-continue')
      const timeoutForm = document.getElementById('inactivity-form')
      let deadline = Date.now() + timeout * 1000
      let submitted = false
      const reset = () => {
        deadline = Date.now() + timeout * 1000
        warning?.classList.add('hidden')
        warning?.classList.remove('grid')
      }
      ;['pointerdown', 'pointermove', 'touchstart', 'keydown'].forEach(name => {
        window.addEventListener(name, reset, { passive: true })
      })
      continueButton?.addEventListener('click', reset)
      window.setInterval(() => {
        const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
        if (secondsNode) secondsNode.textContent = String(remaining)
        if (remaining > 0 && remaining <= 15) {
          warning?.classList.remove('hidden')
          warning?.classList.add('grid')
        }
        if (remaining === 0 && !submitted) {
          submitted = true
          timeoutForm?.requestSubmit()
        }
      }, 1000)
    }
  }

  const completion = document.getElementById('completion-countdown')
  if (completion) {
    let seconds = 7
    window.setInterval(() => {
      seconds = Math.max(0, seconds - 1)
      completion.textContent = String(seconds)
    }, 1000)
  }

  const sidebar = document.getElementById('admin-sidebar')
  const overlay = document.getElementById('admin-nav-overlay')
  const openNav = () => {
    sidebar?.classList.remove('-translate-x-full')
    overlay?.classList.remove('hidden')
  }
  const closeNav = () => {
    sidebar?.classList.add('-translate-x-full')
    overlay?.classList.add('hidden')
  }
  document.getElementById('admin-nav-open')?.addEventListener('click', openNav)
  document.getElementById('admin-nav-close')?.addEventListener('click', closeNav)
  overlay?.addEventListener('click', closeNav)

  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm || 'Continue?')) event.preventDefault()
    })
  })

  const particles = document.querySelectorAll('.gold-particles')
  const points = [[9,14,4,0],[21,74,2,1.2],[37,25,3,2.4],[52,84,4,.8],[68,18,2,3.1],[82,61,3,1.8],[94,32,2,.4],[74,92,2,2.8],[14,48,2,3.5],[46,58,2,1.4],[89,81,4,2],[61,43,2,.2]]
  particles.forEach(container => points.forEach(([left, top, size, delay], index) => {
    const dot = document.createElement('span')
    dot.style.left = `${left}%`
    dot.style.top = `${top}%`
    dot.style.width = `${size}px`
    dot.style.height = `${size}px`
    dot.style.setProperty('--delay', `${delay}s`)
    dot.style.setProperty('--duration', `${4.5 + (index % 4)}s`)
    container.appendChild(dot)
  }))
})()
