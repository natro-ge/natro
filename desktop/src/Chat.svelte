<script lang="ts">
  // The chat: a glass box that rises from the bottom of the screen as a blob and widens,
  // with the conversation above it as bubbles. Rust (src-tauri/src/chat.rs) places the
  // window, gives it the keyboard and says what to show; this page draws and animates
  // it, and says where the box and the bubbles are, so that clicks anywhere else go
  // through the window to what's under it. The chat is shared with the phone: its
  // requests show too, labelled, and pictures (screenshots, ones he sends) in bubbles.
  import { invoke } from '@tauri-apps/api/core'
  import { listen } from '@tauri-apps/api/event'
  import { onMount, tick } from 'svelte'
  import { cubicIn } from 'svelte/easing'
  import { fade, scale } from 'svelte/transition'
  import { format } from './format'
  import { GLYPHS } from './glyphs'
  import { DoubleTap } from './twice'

  type Step = { label: string; icon: string; image: string | null }
  // answer: empty while Natro waits; then "Yes", "No", or what was typed or said.
  // device: whose question, for one about a request made on the phone ("" for the PC's).
  type Question = { id: string; text: string; answer: string; device: string }
  // asked: typed, or her English for what was said; heard: the Georgian, for a spoken request.
  type Exchange = {
    id: string
    asked: string
    heard: string
    spoken: boolean
    reply: string
    done: boolean
    step: Step | null
    questions: Question[]
    problem: string
    // The device it was asked on, for one made on the phone ("" for the PC's).
    from: string
    pictures: string[]
    // Asked on that device while the PC was off, run here after.
    queued: string
    // A picture he sent, not a request.
    sent: boolean
    // A reminder that went off: her words alone, with no request.
    reminder: boolean
    // A background job's task: her work on it as it goes, then what she found.
    job: string
    // A routine's name, on the job that is its run (an older server leaves it out).
    routine?: string
    // Something a watcher saw: her words alone, as a reminder's.
    alert: boolean
  }
  // A request he made on the phone while the PC was off, to run or skip.
  type Queued = { id: string; text: string; from: string; time: number }
  // A message from the phone that what's typed answers (the reply box's "answer in the chat"), quoted over the box.
  type Replying = { id: string; from: string; app: string; text: string }
  type Shown = {
    exchanges: Exchange[]
    listening: boolean
    pictures: Record<string, string>
    queued: Queued[]
    replying?: Replying | null
  }
  type ChatEvent =
    | ({ action: 'show' } & Shown)
    | { action: 'hide' }
    | { action: 'exchange'; exchange: Exchange }
    | { action: 'clear' }
    | { action: 'replace'; exchanges: Exchange[] }
    | { action: 'picture'; id: string; url: string }
    | { action: 'queued'; items: Queued[] }
    | { action: 'listening'; on: boolean }
    | { action: 'notice'; text: string }
    | { action: 'replying'; replying: Replying | null }

  // The text grows to this tall (about five lines), then scrolls.
  const TALLEST_TEXT = 110
  // A notice ("Natro is offline") shows this long.
  const NOTICE_MILLIS = 2600
  // Pictures he sends: the longest side at most this, as JPEG.
  const PICTURE_PIXELS = 2048

  // Solid icons, on a 20 × 20 grid in the text's color.
  const ICONS = {
    more: '<path class="line thick" d="M10 4.5v11M4.5 10h11"/>',
    newChat:
      '<path fill-rule="evenodd" d="M10 2.5c-4.7 0-8.5 3.1-8.5 7 0 2.1 1.1 4 2.9 5.3-.2 1.1-.8 2.1-1.6 2.8 1.8 0 3.4-.6 4.6-1.5.8.2 1.7.3 2.6.3 4.7 0 8.5-3.1 8.5-7s-3.8-6.9-8.5-6.9zm-.8 3.7h1.6v2.5h2.5v1.6h-2.5v2.5H9.2v-2.5H6.7V8.7h2.5z"/>',
    attach:
      '<path class="line" d="M13.5 6.5 8 12a1.8 1.8 0 0 0 2.5 2.5l6-6a3.5 3.5 0 0 0-5-5l-6.2 6.2a5.2 5.2 0 0 0 7.4 7.4L17 12.8"/>',
    picture:
      '<path fill-rule="evenodd" d="M4.5 3h11A2.5 2.5 0 0 1 18 5.5v9a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 2 14.5v-9A2.5 2.5 0 0 1 4.5 3zm9 4.2a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM3.8 14.6l3.6-4.4a1 1 0 0 1 1.5 0l2.2 2.5 1.3-1.4a1 1 0 0 1 1.5 0l2.3 2.6v.6c0 .6-.4 1-1 1H4.8a1 1 0 0 1-1-.9z"/>',
    settings:
      '<path fill-rule="evenodd" d="M8.3 1.5h3.4l.5 2.4c.6.2 1.1.5 1.6.9l2.3-.8 1.7 2.9-1.8 1.6c.1.6.1 1.2 0 1.8l1.8 1.6-1.7 2.9-2.3-.8c-.5.4-1 .7-1.6.9l-.5 2.4H8.3l-.5-2.4c-.6-.2-1.1-.5-1.6-.9l-2.3.8-1.7-2.9 1.8-1.6a6 6 0 0 1 0-1.8L2.2 6.9l1.7-2.9 2.3.8c.5-.4 1-.7 1.6-.9zM10 12.8a2.8 2.8 0 1 0 0-5.6 2.8 2.8 0 0 0 0 5.6z"/>',
    mic: '<rect x="7" y="1.5" width="6" height="10.5" rx="3"/><path class="line" d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v3"/>',
    stop: '<rect x="5" y="5" width="10" height="10" rx="2.6"/>',
    send: '<path class="line thick" d="M10 15.5V5M5.6 9.2 10 4.8l4.4 4.4"/>',
  }

  let open = $state(false)
  // The modifier tapped twice that closes the chat too ("alt"), as the keybind says when it opens.
  let twice: string | null = null
  const taps = new DoubleTap()
  // The + menu: a new chat, files to the phone, a picture, Settings.
  let menu = $state(false)
  let menuBox: HTMLElement | undefined = $state()
  let moreButton: HTMLButtonElement | undefined = $state()
  let exchanges = $state<Exchange[]>([])
  // Picture id -> data URL.
  let pictures = $state<Record<string, string>>({})
  let queued = $state<Queued[]>([])
  let picker: HTMLInputElement | undefined = $state()
  let listening = $state(false)
  let draft = $state('')
  let notice = $state('')
  let replying = $state<Replying | null>(null)
  // How tall the quoted message is: a notice goes above it.
  let quoteHeight = $state(0)
  // The microphone's loudness, 0 to 1, smoothed for the eye.
  let level = $state(0)
  let stage: HTMLElement | undefined = $state()
  let list: HTMLElement | undefined = $state()
  let field: HTMLTextAreaElement | undefined = $state()
  // Natro's question still waiting for a yes or no: what's typed (or said) answers it.
  let waiting = $derived(
    exchanges
      .filter((e) => !e.done && !e.from)
      .flatMap((e) => e.questions)
      .find((q) => !q.answer),
  )
  // What he typed to her before, oldest first (chat.rs keeps it), and where Up and Down are in it:
  // at typed.length, the box holds what he was writing (unsent).
  let typed: string[] = []
  let recalled = 0
  let unsent = ''
  // Following the newest bubble, unless the owner scrolled up.
  let following = true
  let heard = 0
  let noticeTimer = 0
  let lastHits = ''

  function show(event: Shown) {
    taps.interrupt()
    invoke<string | null>('settings_twice').then((key) => (twice = key))
    exchanges = event.exchanges
    listening = event.listening
    pictures = event.pictures
    queued = event.queued
    replying = event.replying ?? null
    following = true
    if (!open) {
      // Let the closed blob be drawn once, so it rises from there.
      requestAnimationFrame(() => requestAnimationFrame(() => (open = true)))
    }
    scrollToEnd()
    focusField()
  }

  function update(exchange: Exchange) {
    const at = exchanges.findIndex((e) => e.id === exchange.id)
    if (at >= 0) exchanges[at] = exchange
    else {
      exchanges.push(exchange)
      if (!exchange.spoken || exchange.asked === '') following = true // the owner's own: show it
    }
    scrollToEnd()
  }

  function focusField() {
    requestAnimationFrame(() => field?.focus())
  }

  async function scrollToEnd() {
    if (!following) return
    await tick()
    if (list) list.scrollTop = list.scrollHeight
  }

  function scrolled() {
    if (list) following = list.scrollTop + list.clientHeight >= list.scrollHeight - 8
  }

  async function send() {
    const text = draft.trim()
    if (!text || listening) return
    draft = ''
    following = true
    if (!(await invoke<boolean>('chat_send', { text }))) draft = text // offline: keep it
    await loadTyped()
    focusField()
  }

  async function loadTyped() {
    typed = await invoke<string[]>('chat_history')
    recalled = typed.length
    unsent = ''
  }

  /** Up and Down, as in a terminal: what he typed before, while the caret is on the box's first
   *  line (going down, its last). False when the caret just moves in the text instead. */
  function recall(step: -1 | 1) {
    if (!field || field.selectionStart !== field.selectionEnd) return false
    const caret = field.selectionStart
    const edge = step < 0 ? !draft.slice(0, caret).includes('\n') : !draft.slice(caret).includes('\n')
    const next = recalled + step
    if (!edge || next < 0 || next > typed.length) return false
    if (recalled === typed.length) unsent = draft
    recalled = next
    draft = next === typed.length ? unsent : typed[next]
    tick().then(() => field?.setSelectionRange(draft.length, draft.length))
    return true
  }

  function stopReplying() {
    replying = null
    invoke('chat_stop_replying')
    focusField()
  }

  function answer(question: Question, yes: boolean) {
    invoke('chat_answer', { question: question.id, yes, device: question.device || null })
    focusField()
  }

  function runQueued(item: Queued, run: boolean) {
    invoke('chat_queue', { id: item.id, run })
    focusField()
  }

  /** A picture, made small enough to send quickly, as a JPEG data URL. */
  async function pictureData(file: Blob) {
    const bitmap = await createImageBitmap(file)
    const scale = Math.min(1, PICTURE_PIXELS / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    const context = canvas.getContext('2d')!
    context.fillStyle = '#fff' // a transparent picture on white, not black
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    bitmap.close()
    return canvas.toDataURL('image/jpeg', 0.85)
  }

  async function sendPicture(file: Blob, name: string) {
    try {
      following = true
      await invoke<boolean>('chat_picture', { data: await pictureData(file), name })
    } catch {
      showNotice("That picture couldn't be read")
    }
    focusField()
  }

  function picked() {
    for (const file of picker?.files ?? []) sendPicture(file, file.name)
    if (picker) picker.value = ''
  }

  /** A picture pasted into the box goes into the chat; text is pasted as usual. */
  function pasted(event: ClipboardEvent) {
    const item = [...(event.clipboardData?.items ?? [])].find((i) => i.type.startsWith('image/'))
    const file = item?.getAsFile()
    if (!file) return
    event.preventDefault()
    sendPicture(file, file.name || 'pasted picture.png')
  }

  function time(millis: number) {
    return new Date(millis).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  }

  function newChat() {
    invoke('chat_clear')
    focusField()
  }

  function showNotice(text: string) {
    notice = text
    clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => (notice = ''), NOTICE_MILLIS)
  }

  /** The + opens the menu over it; opened from the keyboard, its first line takes the focus. */
  async function toggleMenu(event: MouseEvent) {
    menu = !menu
    if (!menu) return focusField()
    await tick()
    if (event.detail === 0) menuBox?.querySelector('button')?.focus()
  }

  function closeMenu(refocus = true) {
    if (!menu) return
    menu = false
    if (refocus) focusField()
  }

  /** A line of the menu: it closes, then does its thing. */
  function choose(action: () => void) {
    closeMenu(false)
    action()
  }

  /** Up and down move between the menu's lines. */
  function menuKeydown(event: KeyboardEvent) {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    const lines = [...(menuBox?.querySelectorAll('button') ?? [])]
    const at = lines.indexOf(document.activeElement as HTMLButtonElement)
    const step = event.key === 'ArrowDown' ? 1 : -1
    lines[(at + step + lines.length) % lines.length]?.focus()
  }

  /** A press anywhere but the menu and its + closes the menu. */
  function pointerdown(event: PointerEvent) {
    taps.interrupt()
    const target = event.target as Node
    if (menu && !menuBox?.contains(target) && !moreButton?.contains(target)) closeMenu(false)
  }

  function keydown(event: KeyboardEvent) {
    taps.press(event)
    // Alt alone would put the window in menu mode, which swallows the second tap's press.
    if (event.key === 'Alt' && twice === 'alt') event.preventDefault()
    if (event.key === 'Escape') {
      event.preventDefault()
      if (menu) closeMenu()
      else if (replying) stopReplying()
      else invoke('chat_close')
    }
  }

  /** The double tap closes the chat, as it opened it (here, as the chat has the keyboard). */
  function keyup(event: KeyboardEvent) {
    if (event.key === 'Alt' && twice === 'alt') event.preventDefault()
    const key = taps.release(event)
    if (open && key && key === twice) invoke('chat_close', { keys: `${event.key} twice` })
  }

  function fieldKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      send()
    }
    const alone = !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey && !event.isComposing
    const arrow = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0
    if (alone && arrow && recall(arrow)) event.preventDefault()
  }

  /** Links in her replies open in the browser (the window never navigates). */
  function clicked(event: MouseEvent) {
    const link = (event.target as HTMLElement).closest('a')
    if (!link) return
    event.preventDefault()
    invoke('reply_open', { url: link.getAttribute('href') })
  }

  // The text box grows with what's written, up to TALLEST_TEXT.
  $effect(() => {
    void draft
    if (!field) return
    field.style.height = 'auto'
    field.style.height = `${Math.min(field.scrollHeight, TALLEST_TEXT)}px`
  })

  /** RMS to 0..1 on a decibel scale: a quiet room is about 0, loud speech 1 (as the dock's). */
  function loudness(rms: number) {
    const db = 20 * Math.log10(Math.max(rms, 1e-5))
    return Math.min(1, Math.max(0, (db + 52) / 36))
  }

  /** Where the box and the bubbles are (window pixels), for Rust: the window takes the mouse only there. */
  function reportHits() {
    const rects: number[][] = []
    if (open && stage) {
      const view = list?.getBoundingClientRect()
      for (const element of stage.querySelectorAll<HTMLElement>('[data-hit]')) {
        const box = element.getBoundingClientRect()
        let [top, bottom] = [box.top, box.bottom]
        // A bubble scrolled partly out of sight takes the mouse only where it shows.
        if (view && list?.contains(element)) [top, bottom] = [Math.max(top, view.top), Math.min(bottom, view.bottom)]
        if (bottom - top >= 1 && box.width >= 1)
          rects.push([Math.floor(box.left), Math.floor(top), Math.ceil(box.width), Math.ceil(bottom - top)])
      }
    }
    const key = rects.join(';')
    if (key !== lastHits) {
      lastHits = key
      invoke('chat_hits', { rects })
    }
  }

  onMount(() => {
    let frame = 0
    const everyFrame = () => {
      const target = listening ? heard : 0
      const next = level + (target - level) * (target > level ? 0.4 : 0.12) // quick to rise, slow to fall
      if (Math.abs(next - level) > 0.001) level = next
      reportHits()
      frame = requestAnimationFrame(everyFrame)
    }
    frame = requestAnimationFrame(everyFrame)
    const unlisten = [
      listen<ChatEvent>('chat', ({ payload }) => {
        switch (payload.action) {
          case 'show':
            return show(payload)
          case 'hide':
            open = false
            menu = false
            replying = null
            listening = false
            notice = ''
            return
          case 'exchange':
            return update(payload.exchange)
          case 'clear':
            exchanges = []
            pictures = {}
            return
          case 'replace':
            exchanges = payload.exchanges
            return scrollToEnd()
          case 'picture':
            pictures[payload.id] = payload.url
            return scrollToEnd()
          case 'queued':
            queued = payload.items
            return scrollToEnd()
          case 'listening':
            listening = payload.on
            heard = 0
            if (!payload.on) tick().then(focusField)
            return
          case 'notice':
            return showNotice(payload.text)
          case 'replying':
            replying = payload.replying
            if (replying) focusField()
            return
        }
      }),
      listen<number>('level', ({ payload }) => (heard = loudness(payload))),
    ]
    loadTyped()
    // The chat may have opened while this page was still loading.
    Promise.all(unlisten).then(async () => {
      const showing = await invoke<ChatEvent | null>('chat_state')
      if (showing?.action === 'show') show(showing)
    })
    return () => {
      cancelAnimationFrame(frame)
      clearTimeout(noticeTimer)
      unlisten.forEach((stop) => stop.then((f) => f()))
    }
  })
</script>

<svelte:window
  onkeydown={keydown}
  onkeyup={keyup}
  onpointerdown={pointerdown}
  onfocus={() => open && !listening && !menu && focusField()}
  onblur={() => {
    taps.interrupt()
    closeMenu(false)
  }}
/>

<main class="stage" class:open class:listening bind:this={stage}>
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="list" bind:this={list} onscroll={scrolled} onclick={clicked} aria-live="polite">
    {#each exchanges as exchange (exchange.id)}
      {#if exchange.reminder}
        <span class="from reminder">Reminder</span>
      {:else if exchange.alert}
        <span class="from reminder">Heads-up</span>
      {:else if exchange.job}
        <span class="from reminder job" title={exchange.job}>{exchange.routine ? `Routine: ${exchange.routine}` : `In the background: ${exchange.job}`}</span>
      {:else if exchange.from || exchange.queued}
        <span class="from">
          {exchange.queued ? `Asked on your ${exchange.queued} while the PC was off` : `On your ${exchange.from}`}
        </span>
      {/if}
      {#if exchange.sent}
        <div class="bubble you shots" class:elsewhere={exchange.from} data-hit>
          {#each exchange.pictures as id (id)}
            {@render shot(id)}
          {/each}
        </div>
      {:else if exchange.asked || exchange.spoken}
        <div class="bubble you" class:elsewhere={exchange.from} data-hit title={exchange.heard || null}>
          {#if exchange.spoken}
            <svg class="said" viewBox="0 0 20 20" aria-label="Said">{@html ICONS.mic}</svg>
          {/if}
          {#if exchange.asked}
            {exchange.asked}
          {:else}
            <span class="dots" aria-label="Working out what you said"><i></i><i></i><i></i></span>
          {/if}
        </div>
      {/if}
      {#each exchange.questions as question (question.id)}
        <div class="bubble natro question" class:waiting={!question.answer} data-hit>
          <p><span class="orb"></span><span>{question.text}</span></p>
          {#if question.answer}
            <span class="answer">Your answer: {question.answer}</span>
          {:else}
            <div class="choices">
              <button class="yes" onclick={() => answer(question, true)}>Yes</button>
              <button class="no" onclick={() => answer(question, false)}>No</button>
            </div>
          {/if}
        </div>
      {/each}
      {#if exchange.reply || exchange.problem || (!exchange.done && (exchange.asked || exchange.job) && !exchange.questions.some((q) => !q.answer))}
        <div class="bubble natro" class:problem={!exchange.reply && exchange.problem} data-hit>
          {#if exchange.reply}
            <div class="text">{@html format(exchange.reply, !exchange.done)}</div>
          {:else if exchange.problem}
            <span class="status"><span class="orb"></span>{exchange.problem}</span>
          {:else}
            <span class="status">
              <span class="icon">
                {#if exchange.step}
                  {#key exchange.step.image ?? exchange.step.icon}
                    <span class="picture" in:scale={{ start: 0.5, duration: 280 }}>
                      {#if exchange.step.image}
                        <img src={exchange.step.image} alt="" />
                      {:else}
                        <svg viewBox="0 0 16 16" aria-hidden="true">{@html GLYPHS[exchange.step.icon] ?? GLYPHS.work}</svg>
                      {/if}
                    </span>
                  {/key}
                {:else}
                  <span class="spinner"></span>
                {/if}
              </span>
              <span class="label" class:shimmer={!exchange.step}>{exchange.step?.label ?? 'Thinking…'}</span>
            </span>
          {/if}
        </div>
      {/if}
      {#if !exchange.sent && exchange.pictures.length}
        <div class="bubble natro shots" data-hit>
          {#each exchange.pictures as id (id)}
            {@render shot(id)}
          {/each}
        </div>
      {/if}
    {/each}
    {#each queued as item (item.id)}
      <div class="bubble natro question waiting queued" data-hit>
        <p><span class="orb"></span><span>While the PC was off, you asked on your {item.from} at {time(item.time)}:</span></p>
        <span class="kept">{item.text}</span>
        <div class="choices">
          <button class="yes" onclick={() => runQueued(item, true)}>Run</button>
          <button class="no" onclick={() => runQueued(item, false)}>Skip</button>
        </div>
      </div>
    {/each}
  </div>

  {#snippet line(at: number, label: string, hint: string, icon: string, action: () => void)}
    <button class="item" role="menuitem" style:--at={at} onclick={() => choose(action)}>
      <svg viewBox="0 0 20 20" aria-hidden="true">{@html icon}</svg>
      <span>{label}</span>
      {#if hint}<span class="hint">{hint}</span>{/if}
    </button>
  {/snippet}

  {#snippet shot(id: string)}
    {#if pictures[id]}
      <button class="shot" title="Open full size" onclick={() => invoke('chat_open_picture', { id })}>
        <img src={pictures[id]} alt="" />
      </button>
    {:else}
      <span class="gone">Picture no longer here</span>
    {/if}
  {/snippet}

  <div class="bottom">
    {#if replying}
      <div class="replying" data-hit bind:clientHeight={quoteHeight} transition:fade={{ duration: 140 }}>
        <div class="quoted">
          <span class="whom">Answering <strong>{replying.from}</strong> · {replying.app}, from your phone</span>
          {#if replying.text}<span class="words">{replying.text}</span>{/if}
        </div>
        <button aria-label="Don't answer" title="Don't answer (Esc)" onclick={stopReplying}>
          <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" /></svg>
        </button>
      </div>
    {/if}
    {#if notice}
      <div
        class="notice"
        style:bottom={replying ? `calc(100% + ${quoteHeight + 12}px)` : null}
        transition:fade={{ duration: 180 }}
      >
        {notice}
      </div>
    {/if}
    {#if menu}
      <!-- svelte-ignore a11y_interactive_supports_focus -->
      <div
        class="menu"
        role="menu"
        tabindex="-1"
        data-hit
        bind:this={menuBox}
        onkeydown={menuKeydown}
        out:scale={{ start: 0.94, duration: 140, easing: cubicIn }}
      >
        <div class="sheen"></div>
        {@render line(0, 'New chat', '', ICONS.newChat, newChat)}
        {@render line(1, 'Send files to the phone', '', ICONS.attach, () => invoke('chat_attach'))}
        {@render line(2, 'Send a picture', 'or paste one', ICONS.picture, () => picker?.click())}
        <span class="divider" style:--at={3}></span>
        {@render line(3, 'Settings', '', ICONS.settings, () => invoke('settings_open'))}
      </div>
    {/if}
    <div class="bar" data-hit>
      <div class="sheen"></div>
      <span class="blob-dot"></span>
      <div class="row">
        <button
          class="tool more"
          class:on={menu}
          title={menu ? 'Close' : 'More'}
          aria-label="More: new chat, files, a picture, settings"
          aria-haspopup="menu"
          aria-expanded={menu}
          bind:this={moreButton}
          onclick={toggleMenu}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.more}</svg>
        </button>
        <input class="picker" type="file" accept="image/*" multiple bind:this={picker} onchange={picked} />
        {#if listening}
          <div class="field heard" style:--level={level}><span class="live"></span>Listening…</div>
        {:else}
          <textarea
            bind:this={field}
            bind:value={draft}
            rows="1"
            placeholder={replying ? `Your answer to ${replying.from}` : waiting ? 'Answer Natro: yes or no' : 'Ask Natro…'}
            aria-label="Message to Natro"
            onkeydown={fieldKeydown}
            onpaste={pasted}
          ></textarea>
        {/if}
        <button
          class="tool mic"
          class:on={listening}
          title={listening ? 'Send what you said' : 'Talk instead'}
          aria-label={listening ? 'Send what you said' : 'Talk instead'}
          onclick={() => invoke('chat_mic')}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">{@html listening ? ICONS.stop : ICONS.mic}</svg>
        </button>
        <button class="send" title="Send" aria-label="Send" disabled={!draft.trim() || listening} onclick={send}>
          <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.send}</svg>
        </button>
      </div>
    </div>
  </div>
</main>

<style>
  /* The dock's glass (app.css), dense: there's text to read over whatever is behind it.
     Keep in step with chat.rs: WIDTH 784 (the box 720 and 32 on each side), SLIDE_OUT 640 ms. */
  :global(#app) {
    height: 100%;
  }

  .stage {
    --spring: cubic-bezier(0.18, 1.25, 0.4, 1);
    --widen: cubic-bezier(0.22, 1.12, 0.36, 1);
    --narrow: cubic-bezier(0.5, 0, 0.75, 0.3);
    --out: cubic-bezier(0.55, 0, 0.75, 0.2);
    --amber: linear-gradient(135deg, #c27803 0%, #f5a623 100%);
    --red: linear-gradient(135deg, #b3261e 0%, #ff453a 100%);
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    height: 100%;
    padding: 0 32px 24px;
  }

  /* The conversation: newest at the bottom, fading out at the top. */
  .list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-height: 0;
    margin: 0 -16px;
    padding: 36px 16px 14px;
    overflow-y: auto;
    overscroll-behavior: contain;
    scrollbar-width: none;
    mask-image: linear-gradient(to bottom, transparent 0, #000 40px);
    opacity: 0;
    transform: translateY(20px);
    transition:
      opacity 200ms ease-in,
      transform 260ms var(--out);
  }
  .open .list {
    opacity: 1;
    transform: none;
    transition:
      opacity 280ms ease-out 300ms,
      transform 560ms var(--spring) 260ms;
  }

  .bubble {
    position: relative;
    flex: none;
    box-sizing: border-box;
    max-width: 78%;
    padding: 9px 14px;
    border-radius: 18px;
    font-size: 14px;
    line-height: 1.45;
    overflow-wrap: anywhere;
    user-select: text;
    cursor: auto;
    animation: pop 420ms var(--spring) both;
  }
  /* The owner's: on the right, in Natro's green. */
  .you {
    align-self: flex-end;
    border-bottom-right-radius: 6px;
    background: var(--green);
    color: #fff;
    box-shadow: 0 4px 16px color-mix(in srgb, var(--green-glow) 22%, transparent);
    transform-origin: bottom right;
    white-space: pre-wrap;
  }
  /* The phone's requests: the same green, a shade quieter, with where they were asked above. */
  .you.elsewhere {
    background: linear-gradient(135deg, #2f6b2d 0%, #3d8f3e 100%);
    box-shadow: none;
  }
  .from {
    align-self: flex-end;
    margin: 4px 6px -4px;
    font-size: 11px;
    font-weight: 500;
    color: var(--text-dim);
    user-select: none;
  }
  /* A reminder is her words alone: the label sits on her side. */
  .from.reminder {
    align-self: flex-start;
  }
  /* A background job's label holds its task: one line, the whole of it on hover. */
  .from.job {
    max-width: 70%;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  /* Pictures: screenshots and ones he sent; a click opens one full size. */
  .shots {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 6px;
    background: var(--glass-dense);
  }
  .you.shots {
    background: var(--glass-dense);
    border: 1px solid var(--border);
  }
  .shot {
    display: block;
    padding: 0;
    border: none;
    border-radius: 12px;
    background: none;
    overflow: hidden;
    cursor: zoom-in;
  }
  .shot img {
    display: block;
    max-width: 100%;
    max-height: 300px;
    border-radius: 12px;
  }
  .gone {
    padding: 4px 8px;
    font-size: 12px;
    color: var(--text-dim);
  }
  .queued .kept {
    display: block;
    margin: 6px 0 0 16px;
    font-weight: 600;
  }
  .picker {
    display: none;
  }
  .said {
    width: 12px;
    height: 12px;
    margin: 0 6px -1px 0;
    fill: currentColor;
    opacity: 0.8;
  }
  .said :global(.line) {
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
  }
  /* Hers: on the left, in glass. */
  .natro {
    align-self: flex-start;
    border: 1px solid var(--border);
    border-bottom-left-radius: 6px;
    background: var(--glass-dense);
    box-shadow:
      inset 0 1px 0 0 var(--highlight),
      0 4px 16px rgba(0, 0, 0, 0.1);
    color: var(--text);
    transform-origin: bottom left;
  }

  /* Waiting for what was said: three dots. */
  .dots {
    display: inline-flex;
    gap: 4px;
    padding: 4px 0;
  }
  .dots i {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
    animation: bounce 1.2s ease-in-out infinite;
  }
  .dots i:nth-child(2) {
    animation-delay: 150ms;
  }
  .dots i:nth-child(3) {
    animation-delay: 300ms;
  }

  /* Her reply: the reply box's text (Reply.svelte). */
  .text :global(p) {
    margin: 0 0 8px;
  }
  .text :global(p.heading) {
    font-weight: 600;
    margin-top: 4px;
  }
  .text :global(ul),
  .text :global(ol) {
    margin: 0 0 8px;
    padding-left: 20px;
  }
  .text :global(li) {
    margin: 2px 0;
  }
  .text :global(li::marker) {
    color: var(--text-dim);
  }
  .text :global(> :last-child) {
    margin-bottom: 0;
  }
  .text :global(strong) {
    font-weight: 600;
  }
  .text :global(code) {
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: 12.5px;
    padding: 1px 5px;
    border-radius: 6px;
    background: var(--decline);
  }
  .text :global(pre) {
    margin: 0 0 8px;
    padding: 8px 10px;
    border-radius: 10px;
    background: var(--decline);
    overflow-x: auto;
  }
  .text :global(pre code) {
    padding: 0;
    background: none;
  }
  .text :global(a) {
    color: var(--link);
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
  }
  .text :global(.caret) {
    display: inline-block;
    width: 2px;
    height: 1.05em;
    margin-left: 2px;
    vertical-align: text-bottom;
    border-radius: 1px;
    background: var(--green-glow);
    animation: blink 1s steps(2, start) infinite;
  }

  /* What she's doing: the dock's spinner or the step's picture, and its words. */
  .status {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 500;
    user-select: none;
  }
  .icon {
    position: relative;
    flex: none;
    width: 16px;
    height: 16px;
  }
  .spinner {
    position: absolute;
    inset: 1px;
    border-radius: 50%;
    background: conic-gradient(from 0deg, transparent 10%, var(--text));
    mask: radial-gradient(farthest-side, transparent calc(100% - 2px), #000 calc(100% - 1.5px));
    animation: spin 0.8s linear infinite;
  }
  .picture {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
  }
  .picture img {
    width: 16px;
    height: 16px;
    border-radius: 3px;
  }
  .picture svg {
    width: 16px;
    height: 16px;
    fill: none;
    stroke: var(--text);
    stroke-width: 1.4;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .shimmer {
    background: linear-gradient(90deg, var(--text) 30%, var(--text-dim) 50%, var(--text) 70%) 0 0 / 300% 100%;
    background-clip: text;
    color: transparent;
    animation: shimmer 1.5s linear infinite;
  }

  /* The dock's dot: amber while she asks, red when something went wrong. */
  .orb {
    position: relative;
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--amber);
  }
  .problem .orb {
    background: var(--red);
  }
  .question.waiting .orb::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: #f5a623;
    animation: pulse 1.7s cubic-bezier(0.2, 0.6, 0.35, 1) infinite;
  }
  .question p {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin: 0;
  }
  .question .orb {
    transform: translateY(-1px);
  }
  .choices {
    display: flex;
    gap: 6px;
    margin: 9px 0 1px 16px;
  }
  .choices button {
    height: 28px;
    padding: 0 16px;
    border: none;
    border-radius: 14px;
    font: inherit;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition:
      background 150ms ease,
      color 150ms ease,
      filter 150ms ease,
      scale 120ms ease;
  }
  .choices button:active {
    scale: 0.95;
  }
  .yes {
    background: var(--green);
    color: #fff;
  }
  .yes:hover {
    filter: brightness(1.1);
  }
  .no {
    background: var(--decline);
    color: var(--text);
  }
  .no:hover {
    background: #ff453a;
    color: #fff;
  }
  .answer {
    display: block;
    margin: 4px 0 0 16px;
    font-size: 12px;
    color: var(--text-dim);
  }

  /* The box: a blob that rises from below the screen, then widens. */
  .bottom {
    position: relative;
    flex: none;
    display: flex;
    justify-content: center;
    padding-top: 6px;
  }
  .bar {
    position: relative;
    display: flex;
    justify-content: center;
    width: 52px;
    min-height: 52px;
    border-radius: 26px;
    overflow: hidden;
    box-shadow: var(--shadow);
    transform: translateY(100px) scale(0.6);
    opacity: 0;
    /* Closing: narrow back into the blob, then drop. */
    transition:
      width 320ms var(--narrow),
      transform 340ms var(--out) 260ms,
      opacity 180ms ease-in 420ms;
  }
  .open .bar {
    width: 100%;
    transform: none;
    opacity: 1;
    /* Opening: rise with a little spring, then widen. */
    transition:
      transform 540ms var(--spring),
      opacity 160ms ease-out,
      width 640ms var(--widen) 230ms;
  }
  .sheen {
    position: absolute;
    inset: 0;
    box-sizing: border-box;
    border-radius: inherit;
    border: 1px solid var(--border);
    background: var(--glass-dense);
    box-shadow: inset 0 1px 0 0 var(--highlight);
    pointer-events: none;
  }
  /* The blob's green dot, gone once it widens. */
  .blob-dot {
    position: absolute;
    left: 50%;
    top: 21px;
    width: 10px;
    height: 10px;
    margin-left: -5px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px color-mix(in srgb, var(--green-glow) 60%, transparent);
    transition: opacity 160ms ease 160ms;
  }
  .open .blob-dot {
    opacity: 0;
    transition: opacity 140ms ease 260ms;
  }
  /* As wide as the open box from the start, centered: widening the box uncovers it. */
  .row {
    position: relative;
    flex: none;
    box-sizing: border-box;
    display: flex;
    align-items: flex-end;
    gap: 2px;
    width: calc(100vw - 64px);
    padding: 8px;
    opacity: 0;
    transition: opacity 120ms ease-in;
  }
  .open .row {
    opacity: 1;
    transition: opacity 280ms ease-out 420ms;
  }

  textarea {
    flex: 1;
    box-sizing: border-box;
    min-width: 0;
    height: 36px;
    max-height: 110px;
    margin: 0 4px;
    padding: 8px 4px;
    border: none;
    outline: none;
    resize: none;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 14.5px;
    line-height: 20px;
    overflow-y: auto;
    scrollbar-width: thin;
    user-select: text;
    cursor: text;
  }
  textarea::placeholder {
    color: var(--text-dim);
  }

  /* Listening: the dock's live green dot instead of the text. */
  .heard {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 10px;
    height: 36px;
    margin: 0 4px;
    padding: 0 4px;
    font-size: 14.5px;
    font-weight: 500;
  }
  .live {
    position: relative;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 calc(2px + var(--level) * 12px) color-mix(in srgb, var(--green-glow) 70%, transparent);
    transform: scale(calc(1 + var(--level) * 0.6));
  }
  .live::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: var(--green-glow);
    animation: pulse 1.7s cubic-bezier(0.2, 0.6, 0.35, 1) infinite;
  }

  .tool,
  .send {
    flex: none;
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    margin: 0;
    padding: 0;
    border: none;
    border-radius: 50%;
    cursor: pointer;
    transition:
      background 150ms ease,
      color 150ms ease,
      opacity 150ms ease,
      filter 150ms ease,
      box-shadow 150ms ease,
      scale 120ms ease;
  }
  .tool {
    background: transparent;
    color: var(--text);
    opacity: 0.72;
  }
  .tool:hover {
    background: var(--decline);
    opacity: 1;
  }
  .tool:active,
  .send:active:not(:disabled) {
    scale: 0.9;
  }
  .tool svg,
  .send svg {
    width: 20px;
    height: 20px;
    fill: currentColor;
  }
  .tool svg :global(.line),
  .send svg :global(.line) {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.9;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .send svg :global(.thick) {
    stroke-width: 2.3;
  }
  .mic.on {
    background: var(--green);
    color: #fff;
    opacity: 1;
  }
  .send {
    background: var(--green);
    color: #fff;
    box-shadow: 0 2px 10px color-mix(in srgb, var(--green-glow) 40%, transparent);
  }
  .send:hover:not(:disabled) {
    filter: brightness(1.1);
  }
  .send:disabled {
    opacity: 0.32;
    box-shadow: none;
    cursor: default;
  }
  button:focus-visible {
    outline: 2px solid var(--green-glow);
    outline-offset: 1px;
  }

  /* The +: turns into a ✕ while its menu is open. */
  .tool svg :global(.thick) {
    stroke-width: 2.2;
  }
  .more svg {
    transition: rotate 360ms var(--spring);
  }
  .more.on {
    background: var(--decline);
    opacity: 1;
  }
  .more.on svg {
    rotate: 45deg;
  }

  /* Its menu: the box's glass, over the box's left end, growing out of the + with a little spring.
     Its corner sits over the + (the box's padding 8, the button 36, the 2 + 6 above the box). */
  .menu {
    position: absolute;
    z-index: 1; /* over the bubbles */
    left: 0;
    bottom: calc(100% + 2px);
    display: flex;
    flex-direction: column;
    min-width: 240px;
    padding: 6px;
    border-radius: 18px;
    box-shadow: var(--shadow);
    outline: none;
    transform-origin: 26px calc(100% + 34px);
    animation: menu-in 420ms var(--spring) both;
  }
  .item {
    position: relative;
    display: flex;
    align-items: center;
    gap: 11px;
    height: 38px;
    margin: 0;
    padding: 0 12px 0 10px;
    border: none;
    border-radius: 12px;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 13.5px;
    font-weight: 500;
    text-align: left;
    white-space: nowrap;
    cursor: pointer;
    animation: item-in 340ms var(--spring) both;
    animation-delay: calc(50ms + var(--at) * 35ms);
    transition:
      background 120ms ease,
      scale 120ms ease;
  }
  .item:hover,
  .item:focus-visible {
    background: var(--decline);
    outline: none;
  }
  .item:active {
    scale: 0.98;
  }
  .item svg {
    flex: none;
    width: 18px;
    height: 18px;
    fill: currentColor;
    opacity: 0.78;
  }
  .item svg :global(.line) {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.9;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .item .hint {
    margin-left: auto;
    padding-left: 14px;
    font-size: 12px;
    font-weight: 400;
    color: var(--text-dim);
  }
  .divider {
    position: relative;
    height: 1px;
    margin: 4px 10px;
    background: var(--decline);
    animation: item-in 340ms var(--spring) both;
    animation-delay: calc(50ms + var(--at) * 35ms);
  }

  /* Answering a message from the phone: it's quoted over the box (who, the app, its words), with a green edge
     as his own bubbles; a notice goes above it. */
  .replying {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 14px;
    right: 14px;
    display: flex;
    align-items: flex-start;
    gap: 6px;
    box-sizing: border-box;
    padding: 8px 6px 9px 14px;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: var(--glass-dense);
    box-shadow: var(--shadow);
    font-size: 13px;
    overflow: hidden;
  }
  .replying::before {
    content: '';
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 4px;
    background: var(--green);
  }
  .quoted {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .whom {
    overflow: hidden;
    color: var(--text-dim);
    font-size: 12px;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .whom strong {
    color: var(--text);
    font-weight: 600;
  }
  /* Its words: two lines at most. */
  .quoted .words {
    flex: none;
    max-height: 2.8em;
    overflow: hidden;
    line-height: 1.4;
    overflow-wrap: anywhere;
  }
  .replying button {
    flex: none;
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: inherit;
    cursor: pointer;
  }
  .replying button:hover {
    background: rgba(127, 127, 127, 0.2);
  }
  .replying svg {
    width: 10px;
    height: 10px;
    stroke: currentColor;
    stroke-width: 1.4;
    stroke-linecap: round;
  }

  /* A word over the box for a moment ("Natro is offline"). */
  .notice {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 50%;
    translate: -50% 0;
    padding: 6px 14px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--glass-dense);
    box-shadow: var(--shadow);
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
  }

  @keyframes pop {
    from {
      opacity: 0;
      transform: translateY(10px) scale(0.92);
    }
  }
  @keyframes menu-in {
    from {
      opacity: 0;
      transform: scale(0.6);
    }
    40% {
      opacity: 1;
    }
  }
  @keyframes item-in {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
  }
  @keyframes bounce {
    0%,
    60%,
    100% {
      transform: none;
      opacity: 0.6;
    }
    30% {
      transform: translateY(-3px);
      opacity: 1;
    }
  }
  @keyframes pulse {
    from {
      transform: scale(1);
      opacity: 0.55;
    }
    to {
      transform: scale(3);
      opacity: 0;
    }
  }
  @keyframes spin {
    to {
      rotate: 1turn;
    }
  }
  @keyframes shimmer {
    from {
      background-position: 100% 0;
    }
    to {
      background-position: 0 0;
    }
  }
  @keyframes blink {
    to {
      visibility: hidden;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .stage {
      --spring: ease-out;
      --widen: ease-out;
    }
    .bubble,
    .dots i,
    .live::before,
    .question.waiting .orb::before,
    .shimmer,
    .text :global(.caret),
    .menu,
    .item,
    .divider {
      animation: none;
    }
  }
</style>
