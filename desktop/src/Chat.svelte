<script lang="ts">
  // The chat: a glass box that rises from the bottom of the screen as a blob and widens,
  // with the conversation above it as bubbles. Rust (src-tauri/src/chat.rs) places the
  // window, gives it the keyboard and says what to show; this page draws and animates
  // it, and says where the box and the bubbles are, so that clicks anywhere else go
  // through the window to what's under it. The chat is shared with the phone: its
  // requests show too, labelled, and pictures (screenshots, ones he sends) in bubbles (ChatBubbles.svelte);
  // a click opens one large (PictureViewer.svelte). The window covers the screen above the taskbar: the
  // chat is drawn at its bottom, and an opened picture over all of it.
  import { invoke } from '@tauri-apps/api/core'
  import { listen } from '@tauri-apps/api/event'
  import { onMount, tick } from 'svelte'
  import ChatBar, { type Replying } from './ChatBar.svelte'
  import ChatBubbles, { type Exchange, type Queued, type Question } from './ChatBubbles.svelte'
  import PictureViewer from './PictureViewer.svelte'
  import { DoubleTap } from './twice'

  type Shown = {
    exchanges: Exchange[]
    listening: boolean
    pictures: Record<string, string>
    queued: Queued[]
    replying?: Replying | null
    attached?: string[]
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
    | { action: 'attached'; names: string[] }

  let open = $state(false)
  // The modifier tapped twice that closes the chat too ("alt"), as the keybind says when it opens.
  let twice: string | null = null
  const taps = new DoubleTap()
  let exchanges = $state<Exchange[]>([])
  // Picture id -> data URL, and the one opened large.
  let pictures = $state<Record<string, string>>({})
  let viewing = $state<string | null>(null)
  let queued = $state<Queued[]>([])
  let listening = $state(false)
  let replying = $state<Replying | null>(null)
  // The names of the files for Natro, waiting for his next request.
  let attached = $state<string[]>([])
  let bar: ChatBar | undefined = $state()
  // The microphone's loudness, 0 to 1, smoothed for the eye.
  let level = $state(0)
  let stage: HTMLElement | undefined = $state()
  let list: HTMLElement | undefined = $state()
  // Natro's question still waiting for a yes or no: what's typed (or said) answers it.
  let waiting = $derived(
    exchanges
      .filter((e) => !e.done && !e.from)
      .flatMap((e) => e.questions)
      .find((q) => !q.answer),
  )
  // Following the newest bubble, unless the owner scrolled up.
  let following = true
  let heard = 0
  let lastHits = ''

  function show(event: Shown) {
    taps.interrupt()
    invoke<string | null>('settings_twice').then((key) => (twice = key))
    exchanges = event.exchanges
    listening = event.listening
    pictures = event.pictures
    queued = event.queued
    replying = event.replying ?? null
    attached = event.attached ?? []
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
    bar?.focus()
  }

  async function scrollToEnd() {
    if (!following) return
    await tick()
    if (list) list.scrollTop = list.scrollHeight
  }

  function scrolled() {
    if (list) following = list.scrollTop + list.clientHeight >= list.scrollHeight - 8
  }

  function answer(question: Question, yes: boolean) {
    invoke('chat_answer', { question: question.id, yes, device: question.device || null })
    focusField()
  }

  function runQueued(item: Queued, run: boolean) {
    invoke('chat_queue', { id: item.id, run })
    focusField()
  }

  function pointerdown(event: PointerEvent) {
    taps.interrupt()
    bar?.pressed(event.target as Node)
  }

  function keydown(event: KeyboardEvent) {
    taps.press(event)
    // Alt alone would put the window in menu mode, which swallows the second tap's press.
    if (event.key === 'Alt' && twice === 'alt') event.preventDefault()
    if (event.key === 'Escape') {
      event.preventDefault()
      if (viewing) closePicture()
      else if (!bar?.escape()) invoke('chat_close')
    }
  }

  function openPicture(id: string) {
    bar?.closeMenu(false)
    viewing = id
  }

  function closePicture() {
    viewing = null
    focusField()
  }

  /** The double tap closes the chat, as it opened it (here, as the chat has the keyboard). */
  function keyup(event: KeyboardEvent) {
    if (event.key === 'Alt' && twice === 'alt') event.preventDefault()
    const key = taps.release(event)
    if (open && key && key === twice) invoke('chat_close', { keys: `${event.key} twice` })
  }

  /** Links in her replies open in the browser (the window never navigates). */
  function clicked(event: MouseEvent) {
    const link = (event.target as HTMLElement).closest('a')
    if (!link) return
    event.preventDefault()
    invoke('reply_open', { url: link.getAttribute('href') })
  }

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
            viewing = null
            replying = null
            listening = false
            bar?.hidden()
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
            return bar?.showNotice(payload.text)
          case 'replying':
            replying = payload.replying
            if (replying) focusField()
            return
          case 'attached':
            attached = payload.names
            focusField()
            return
        }
      }),
      listen<number>('level', ({ payload }) => (heard = loudness(payload))),
    ]
    // The chat may have opened while this page was still loading.
    Promise.all(unlisten).then(async () => {
      const showing = await invoke<ChatEvent | null>('chat_state')
      if (showing?.action === 'show') show(showing)
    })
    return () => {
      cancelAnimationFrame(frame)
      unlisten.forEach((stop) => stop.then((f) => f()))
    }
  })
</script>

<svelte:window
  onkeydown={keydown}
  onkeyup={keyup}
  onpointerdown={pointerdown}
  onfocus={() => open && !listening && !bar?.menuOpen() && !viewing && focusField()}
  onblur={() => {
    taps.interrupt()
    bar?.closeMenu(false)
  }}
/>

<main class="stage" class:open class:listening bind:this={stage}>
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="list" bind:this={list} onscroll={scrolled} onclick={clicked} aria-live="polite">
    <ChatBubbles {exchanges} {queued} {pictures} {answer} {runQueued} {openPicture} />
  </div>

  <ChatBar bind:this={bar} bind:replying {attached} {listening} {level} answering={!!waiting} onsend={() => (following = true)} />

  {#if viewing && pictures[viewing]}
    {@const id = viewing}
    <PictureViewer src={pictures[id]} close={closePicture} openOutside={() => invoke('chat_open_picture', { id })} />
  {/if}
</main>

<style>
  /* The dock's glass (app.css), dense: there's text to read over whatever is behind it.
     Keep in step with chat.rs: SLIDE_OUT 640 ms. */
  :global(#app) {
    height: 100%;
  }

  /* At the bottom middle of the window (the screen above the taskbar): 784 wide (the box 720 and
     32 on each side), three quarters of the window's height, at most 900. */
  .stage {
    --width: min(784px, 100vw);
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
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    width: var(--width);
    height: min(75%, 900px);
    margin: 0 auto;
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

  @media (prefers-reduced-motion: reduce) {
    .stage {
      --spring: ease-out;
      --widen: ease-out;
    }
  }
</style>
