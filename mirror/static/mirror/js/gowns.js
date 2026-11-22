(() => {
  'use strict'
  const screen = document.getElementById('gown-screen')
  if (!screen) return
  const slides = [...screen.querySelectorAll('.gown')]
  const thumbs = [...screen.querySelectorAll('.thumb')]
  if (!slides.length) return
  let index = Math.max(0, slides.findIndex(slide => !slide.classList.contains('hidden')))

  function show(next, direction = 0) {
    index = (next + slides.length) % slides.length
    slides.forEach((slide, current) => {
      const active = current === index
      slide.classList.toggle('hidden', !active)
      slide.classList.remove('is-in-next', 'is-in-prev')
      if (active && direction) {
        void slide.offsetWidth // restart the entrance animation
        slide.classList.add(direction > 0 ? 'is-in-next' : 'is-in-prev')
      }
    })
    thumbs.forEach((thumb, current) => {
      const active = current === index
      thumb.classList.toggle('is-active', active)
      thumb.querySelector('input').checked = active
      if (active) thumb.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
    })
  }

  document.getElementById('previous-gown')?.addEventListener('click', () => show(index - 1, -1))
  document.getElementById('next-gown')?.addEventListener('click', () => show(index + 1, 1))
  thumbs.forEach((thumb, current) => thumb.addEventListener('click', () => show(current, Math.sign(current - index))))

  // Swipe left/right on the gown to browse; vertical drags keep scrolling the page.
  const stage = slides[0].parentElement
  let startX = null
  let startY = null
  stage.addEventListener('pointerdown', event => {
    if (event.target.closest('button')) return
    startX = event.clientX
    startY = event.clientY
  })
  stage.addEventListener('pointerup', event => {
    if (startX === null) return
    const dx = event.clientX - startX
    const dy = event.clientY - startY
    startX = null
    if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.3) show(index + (dx < 0 ? 1 : -1), dx < 0 ? 1 : -1)
  })
  stage.addEventListener('pointercancel', () => { startX = null })

  // A focused radio already moves with the arrow keys; keep the picture in step with it.
  thumbs.forEach((thumb, current) => thumb.querySelector('input').addEventListener('change', () => {
    if (current !== index) show(current, Math.sign(current - index))
  }))
  document.addEventListener('keydown', event => {
    if (event.target.closest('input, textarea, select')) return
    if (event.key === 'ArrowRight') show(index + 1, 1)
    if (event.key === 'ArrowLeft') show(index - 1, -1)
  })
})()
