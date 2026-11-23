(() => {
  'use strict'
  const screen = document.getElementById('camera-screen')
  if (!screen) return

  const video = document.getElementById('camera-video')
  const preview = document.getElementById('camera-preview')
  const loading = document.getElementById('camera-loading')
  const errorPanel = document.getElementById('camera-error')
  const errorMessage = document.getElementById('camera-error-message')
  const countdown = document.getElementById('camera-countdown')
  const countdownNumber = document.getElementById('camera-countdown-number')
  const capturedLabel = document.getElementById('captured-label')
  const captureControls = document.getElementById('capture-controls')
  const previewControls = document.getElementById('preview-controls')
  const captureButton = document.getElementById('capture-button')
  const cameraOptions = document.getElementById('camera-options')
  const cameraPicker = document.getElementById('camera-picker')
  const cameraDevice = document.getElementById('camera-device')
  const choosePhoto = document.getElementById('camera-upload')
  const retryButton = document.getElementById('camera-retry')
  const retakeButton = document.getElementById('retake-button')
  const acceptButton = document.getElementById('accept-photo-button')
  const uploadForm = document.getElementById('photo-upload-form')
  const uploadInput = document.getElementById('photo-upload-input')
  const csrf = document.querySelector('#heartbeat-form input[name="csrfmiddlewaretoken"]')?.value
  let stream = null
  let photoBlob = null
  let previewUrl = null
  let busy = false

  const wait = milliseconds => new Promise(resolve => window.setTimeout(resolve, milliseconds))
  const show = (element, display = 'grid') => { element?.classList.remove('hidden'); element?.classList.add(display) }
  const hide = (element, display = 'grid') => { element?.classList.add('hidden'); element?.classList.remove(display) }

  function report(status) {
    if (!csrf) return
    const body = new URLSearchParams({ status, csrfmiddlewaretoken: csrf })
    fetch(screen.dataset.heartbeatUrl, {
      method: 'POST', body, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' },
    }).catch(() => undefined)
  }

  function stop() {
    stream?.getTracks().forEach(track => track.stop())
    stream = null
    video.srcObject = null
  }

  async function refreshCameras() {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices()
      const cameras = devices.filter(device => device.kind === 'videoinput' && device.deviceId)
      const selected = cameraDevice.value
      cameraDevice.replaceChildren(new Option('System default', ''))
      cameras.forEach((camera, index) => {
        cameraDevice.add(new Option(camera.label || `Camera ${index + 1}`, camera.deviceId))
      })
      cameraDevice.value = cameras.some(camera => camera.deviceId === selected) ? selected : ''
      cameraPicker.classList.toggle('hidden', cameras.length === 0)
    } catch {
      // The camera can still work if the browser does not expose a device list.
    }
  }

  async function start() {
    stop()
    hide(errorPanel)
    show(loading)
    captureButton.disabled = true
    report('requesting')
    if (!navigator.mediaDevices?.getUserMedia) {
      return fail('unavailable', 'Live camera access requires a supported browser over HTTPS. You can choose a photo below.')
    }
    cameraDevice.disabled = true
    retryButton.disabled = true
    try {
      const deviceId = cameraDevice.value
      stream = await navigator.mediaDevices.getUserMedia({
        video: deviceId ? { deviceId: { exact: deviceId } } : true,
        audio: false,
      })
      video.srcObject = stream
      await video.play()
      hide(loading)
      captureButton.disabled = false
      report('ready')
    } catch (error) {
      const denied = error?.name === 'NotAllowedError' || error?.name === 'SecurityError'
      const unavailable = error?.name === 'NotFoundError' || error?.name === 'OverconstrainedError'
      fail(
        denied ? 'denied' : unavailable ? 'unavailable' : 'error',
        denied
          ? 'Camera access is blocked. Allow the camera in the browser, then try again.'
          : unavailable
            ? 'The browser could not find a usable camera. Reconnect the camera, try again, or choose a photo below.'
            : 'The camera could not be started. Check the connection and try again.',
      )
    } finally {
      await refreshCameras()
      cameraDevice.disabled = false
      retryButton.disabled = false
    }
  }

  function fail(status, message) {
    busy = false
    stop()
    hide(countdown)
    hide(loading)
    errorMessage.textContent = message
    show(errorPanel)
    captureButton.disabled = true
    cameraOptions.classList.remove('hidden')
    report(status)
  }

  async function takePhoto() {
    if (!stream || busy || !video.videoWidth) return
    busy = true
    captureButton.disabled = true
    cameraOptions.classList.add('hidden')
    show(countdown)
    for (let number = 3; number >= 1; number -= 1) {
      countdownNumber.textContent = String(number)
      await wait(850)
    }
    countdownNumber.textContent = ''
    await wait(180)
    // Capture exactly what the guest sees: the preview is object-fit: cover, so crop the
    // camera frame to the preview's aspect ratio (centred). A wide 16:9 webcam frame would
    // otherwise include areas the guest never saw and be squashed by the try-on model.
    const frameW = video.videoWidth
    const frameH = video.videoHeight
    const viewAspect = (video.clientWidth / video.clientHeight) || (frameW / frameH)
    let cropW = frameW
    let cropH = frameH
    if (frameW / frameH > viewAspect) cropW = Math.round(frameH * viewAspect)
    else cropH = Math.round(frameW / viewAspect)
    const cropX = Math.round((frameW - cropW) / 2)
    const cropY = Math.round((frameH - cropH) / 2)
    const scale = Math.min(1, 1600 / Math.max(cropW, cropH))
    const width = Math.round(cropW * scale)
    const height = Math.round(cropH * scale)
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d', { alpha: false })
    context.translate(width, 0)
    context.scale(-1, 1)
    context.drawImage(video, cropX, cropY, cropW, cropH, 0, 0, width, height)
    photoBlob = await new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('Capture failed')), 'image/jpeg', .93))
    previewUrl = URL.createObjectURL(photoBlob)
    preview.src = previewUrl
    preview.classList.remove('hidden')
    video.classList.add('hidden')
    hide(countdown)
    show(capturedLabel, 'block')
    captureControls.classList.add('hidden')
    previewControls.classList.remove('hidden')
    stop()
    busy = false
  }

  function retake() {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    previewUrl = null
    photoBlob = null
    preview.removeAttribute('src')
    preview.classList.add('hidden')
    video.classList.remove('hidden')
    hide(capturedLabel, 'block')
    captureControls.classList.remove('hidden')
    cameraOptions.classList.remove('hidden')
    previewControls.classList.add('hidden')
    start()
  }

  function accept() {
    if (!photoBlob || busy) return
    busy = true
    acceptButton.disabled = true
    acceptButton.textContent = 'Saving your photo…'
    const transfer = new DataTransfer()
    transfer.items.add(new File([photoBlob], `capture-${Date.now()}.jpg`, { type: 'image/jpeg' }))
    uploadInput.files = transfer.files
    uploadForm.requestSubmit()
  }

  captureButton.addEventListener('click', () => takePhoto().catch(() => fail('error', 'We could not capture that frame. Please hold still and try once more.')))
  cameraDevice.addEventListener('change', start)
  choosePhoto.addEventListener('click', () => uploadInput.click())
  uploadInput.addEventListener('change', () => {
    if (uploadInput.files.length) uploadForm.requestSubmit()
  })
  navigator.mediaDevices?.addEventListener?.('devicechange', refreshCameras)
  retryButton.addEventListener('click', start)
  retakeButton.addEventListener('click', retake)
  acceptButton.addEventListener('click', accept)
  window.addEventListener('pagehide', stop)
  start()
})()
