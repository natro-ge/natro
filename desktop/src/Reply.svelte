<script lang="ts">
  // The reply box: Natro's answer as text in the top-right corner, in the dock's glass.
  // Rust (src-tauri/src/reply.rs) places the window and says what to show; this page
  // draws the box, slides it in and out, and says how tall it is, so the window is
  // never bigger than the box. A short reply fades by itself (not while the mouse is
  // over it); a longer one stays until its ✕. A message that came to the phone is a
  // card, with a box to answer it right there (the window takes the keyboard only
  // while he types in it) or in the chat.
  import { invoke } from '@tauri-apps/api/core'
  import { listen } from '@tauri-apps/api/event'
  import { onMount, tick } from 'svelte'
  import { format, isShort, readingMillis } from './format'

  // A message that came to the phone (reply.rs's Card); time: when it came, ms since 1970.
  type Card = { id: string; from: string; app: string; text: string; time: number; can_reply: boolean }
  type ReplyEvent = {
    action: 'show' | 'text' | 'hide' | 'gone'
    text: string
    done: boolean
    max_height: number
    message: Card | null
  }
  // Room around the box for its shadow (px); keep in step with reply.rs (MARGIN).
  const MARGIN = 20
  // The answer grows to this tall (about four lines), then scrolls.
  const TALLEST_ANSWER = 84
  // Apps' own colors for a card's picture, by name as the phone says it (lowercase); others get one from the name.
  const APP_COLORS: Record<string, string> = {
    whatsapp: 'linear-gradient(135deg, #128c4a 0%, #25d366 100%)',
    instagram: 'linear-gradient(135deg, #f58529 0%, #dd2a7b 50%, #8134af 100%)',
    messenger: 'linear-gradient(135deg, #0695ff 0%, #a334fa 60%, #ff6968 100%)',
    telegram: 'linear-gradient(135deg, #1c8adb 0%, #2aabee 100%)',
    discord: 'linear-gradient(135deg, #4752c4 0%, #5865f2 100%)',
    messages: 'linear-gradient(135deg, #1a62d6 0%, #4c8df6 100%)',
    viber: 'linear-gradient(135deg, #59267c 0%, #7360f2 100%)',
    signal: 'linear-gradient(135deg, #2c6bed 0%, #5a8ff5 100%)',
    gmail: 'linear-gradient(135deg, #c5221f 0%, #ea4335 100%)',
  }
  const ICONS = {
    send: '<path d="M10 15.5V5M5.6 9.2 10 4.8l4.4 4.4"/>',
    chat: '<path d="M10 3.5c-4 0-7 2.6-7 5.8 0 1.8.9 3.3 2.4 4.4-.2.9-.7 1.8-1.4 2.4 1.5 0 2.9-.5 3.9-1.3.7.2 1.4.3 2.1.3 4 0 7-2.6 7-5.8s-3-5.8-7-5.8z"/>',
  }

  let open = $state(false)
  let text = $state('')
  let done = $state(false)
  let maxHeight = $state(520)
  let hovering = $state(false)
  let message = $state<Card | null>(null)
  // His answers to the cards, by message: one he was writing stays if another message comes.
  let answers = $state<Record<string, string>>({})
  let answer = $derived(message ? (answers[message.id] ?? '') : '')
  // Whether he's typing in the card (its answer has the keyboard).
  let typing = $state(false)
  // Under the answer: "Sending…", why it didn't go, or that the message was read on the phone.
  let status = $state('')
  let sending = $state(false)
  let stage: HTMLElement | undefined = $state()
  let body: HTMLElement | undefined = $state()
  let field: HTMLTextAreaElement | undefined = $state()
  let html = $derived(format(text, !done))
  // Counts shows and hides, so a late timer of an old one does nothing.
  let turn = 0
  // Following the end while she writes, unless the owner scrolled up.
  let following = true

  function close() {
    invoke('reply_close')
  }

  function show(event: ReplyEvent) {
    turn++
    text = event.text
    done = event.done
    maxHeight = event.max_height
    setMessage(event.message)
    following = true
    if (body) body.scrollTop = 0
    if (!open) {
      // Let the closed box be drawn once, so the slide starts from there.
      requestAnimationFrame(() => requestAnimationFrame(() => (open = true)))
    }
  }

  function setMessage(card: Card | null) {
    if (card?.id !== message?.id) {
      status = ''
      sending = false
    }
    message = card
  }

  // A short reply, once it's whole, fades after the time to read it; a card not while he's answering it.
  $effect(() => {
    if (!open || !done || hovering || typing || answer.trim() || sending || !isShort(text)) return
    const mine = turn
    const timer = setTimeout(() => mine === turn && close(), readingMillis(text))
    return () => clearTimeout(timer)
  })

  // The answer grows with what's written, up to TALLEST_ANSWER.
  $effect(() => {
    void answer
    if (!field) return
    field.style.height = 'auto'
    field.style.height = `${Math.min(field.scrollHeight, TALLEST_ANSWER)}px`
  })

  /** A click into the answer: the window takes the keyboard (it never has it otherwise), then the answer. */
  async function takeKeyboard() {
    if (typing) return
    await invoke('reply_keyboard', { take: true })
    typing = true
    field?.focus()
  }

  /** Another app has the keyboard now: the window isn't one to take it any more. */
  function lostKeyboard() {
    if (!typing) return
    typing = false
    invoke('reply_keyboard', { take: false })
  }

  function write(event: Event) {
    if (message) answers[message.id] = (event.target as HTMLTextAreaElement).value
  }

  /** Enter or the arrow: the answer goes from the phone, in the message's app (reply.rs). */
  async function send() {
    const card = message
    const words = answer.trim()
    if (!card || !words || sending) return
    sending = true
    status = 'Sending…'
    if (await invoke<boolean>('reply_send', { id: card.id, text: words })) {
      answers[card.id] = ''
      typing = false
    } else {
      sending = false
      status = "Natro is offline, so it didn't go."
    }
  }

  function answerKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      send()
    }
  }

  /** The message is gone from the phone: the box lets go of it, unless he's answering it (that still goes).
   *  While his answer is on its way, that's the answer itself (sent through its notification): it says so soon. */
  function gone() {
    if (sending) return
    if (typing || answer.trim()) status = 'Read on your phone. Your answer still goes.'
    else close()
  }

  /** "Nino Kapanadze" -> "N"; a name without letters gets a dot. */
  function initial(name: string) {
    return [...name.trim()][0]?.toUpperCase() ?? '•'
  }

  function appColor(app: string) {
    const known = APP_COLORS[app.trim().toLowerCase()]
    if (known) return known
    let hue = 0
    for (const c of app) hue = (hue * 31 + c.charCodeAt(0)) % 360
    return `linear-gradient(135deg, hsl(${hue} 55% 38%) 0%, hsl(${hue} 60% 52%) 100%)`
  }

  function clock(time: number) {
    return time ? new Date(time).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''
  }

  // While she writes, keep the newest words in view (a reply that came whole starts at its top).
  $effect(() => {
    void html
    if (body && following && !done) requestAnimationFrame(() => body && (body.scrollTop = body.scrollHeight))
  })

  function scrolled() {
    if (body) following = body.scrollTop + body.clientHeight >= body.scrollHeight - 4
  }

  /** Links open in the browser (the box's window never navigates). */
  function clicked(event: MouseEvent) {
    const link = (event.target as HTMLElement).closest('a')
    if (!link) return
    event.preventDefault()
    invoke('reply_open', { url: link.getAttribute('href') })
  }

  onMount(() => {
    // The window is as tall as the box (and its shadow); Rust keeps its top-right corner in place.
    const sizes = new ResizeObserver(() => {
      if (stage) invoke('reply_size', { height: Math.ceil(stage.getBoundingClientRect().height) })
    })
    if (stage) sizes.observe(stage)
    const unlisten = [
      listen<ReplyEvent>('reply', ({ payload }) => {
        if (payload.action === 'hide') {
          turn++
          open = false
          typing = false // reply.rs gave the keyboard back
        } else if (payload.action === 'show') {
          show(payload)
        } else if (payload.action === 'gone') {
          gone()
        } else {
          text = payload.text
          done = payload.done
          setMessage(payload.message)
        }
      }),
    ]
    // The box may have opened while this page was still loading.
    Promise.all(unlisten).then(async () => {
      const showing = await invoke<ReplyEvent | null>('reply_state')
      if (showing) show(showing)
    })
    return () => {
      sizes.disconnect()
      unlisten.forEach((stop) => stop.then((f) => f()))
    }
  })
</script>

<svelte:window onblur={lostKeyboard} />

{#snippet closeButton()}
  <button class="close" aria-label="Close" onclick={close}>
    <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" /></svg>
  </button>
{/snippet}

<main class="stage" class:open bind:this={stage}>
  <!-- The window takes the keyboard only for a message's answer: the rest is for the mouse. -->
  <section
    class="box"
    class:card={message}
    style:max-height="{maxHeight - 2 * MARGIN}px"
    onmouseenter={() => (hovering = true)}
    onmouseleave={() => (hovering = false)}
    aria-label={message ? `Message from ${message.from} on ${message.app}` : "Natro's reply"}
    aria-live="polite"
  >
    <div class="sheen"></div>
    {#if message}
      <header class="from">
        <span class="avatar" style:background={appColor(message.app)} aria-hidden="true">{initial(message.from)}</span>
        <span class="who">
          <span class="sender">{message.from}</span>
          <span class="meta">{message.time ? `${message.app} · ${clock(message.time)}` : message.app}</span>
        </span>
        {@render closeButton()}
      </header>
      <div class="body words" bind:this={body}>{message.text}</div>
      {#if message.can_reply}
        <div class="answer" class:typing>
          <textarea
            bind:this={field}
            value={answer}
            rows="1"
            placeholder="Answer {message.from}…"
            aria-label="Your answer to {message.from}"
            disabled={sending}
            oninput={write}
            onpointerdown={takeKeyboard}
            onkeydown={answerKeydown}
          ></textarea>
          <button
            class="tool"
            title="Answer in the chat"
            aria-label="Answer in the chat"
            onclick={() => message && invoke('reply_open', { url: `natro:reply/${message.id}` })}
          >
            <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.chat}</svg>
          </button>
          <button class="send" title="Send" aria-label="Send" disabled={!answer.trim() || sending} onclick={send}>
            <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.send}</svg>
          </button>
        </div>
      {/if}
      {#if status}
        <p class="status" class:sending>{status}</p>
      {/if}
    {:else}
      <header>
        <span class="orb" class:writing={!done}></span>
        <span class="name">Natro</span>
        {@render closeButton()}
      </header>
      <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
      <div class="body" bind:this={body} onscroll={scrolled} onclick={clicked}>
        {@html html}
      </div>
    {/if}
  </section>
</main>

<style>
  /* The dock's glass (app.css, Dock.svelte), thicker: there's more to read. Keep MARGIN in step with reply.rs. */
  .stage {
    --spring: cubic-bezier(0.18, 1.25, 0.4, 1);
    --out: cubic-bezier(0.55, 0, 0.75, 0.2);
    box-sizing: border-box;
    padding: 20px;
  }

  .box {
    position: relative;
    display: flex;
    flex-direction: column;
    min-height: 0;
    border-radius: 20px;
    overflow: hidden;
    box-shadow: var(--shadow);
    transform: translateX(28px) scale(0.97);
    transform-origin: top right;
    opacity: 0;
    transition:
      transform 260ms var(--out),
      opacity 200ms ease-in;
  }
  .open .box {
    transform: none;
    opacity: 1;
    transition:
      transform 480ms var(--spring),
      opacity 180ms ease-out;
  }

  .sheen {
    position: absolute;
    inset: 0;
    box-sizing: border-box;
    border-radius: inherit;
    border: 1px solid var(--border);
    background: var(--glass-thick);
    box-shadow: inset 0 1px 0 0 var(--highlight);
    pointer-events: none;
  }

  header {
    position: relative;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 12px 4px 18px;
  }
  .name {
    flex: 1;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--text-dim);
  }

  /* A message from the phone: who it's from, in which app, when; its words; an answer box. */
  header.from {
    gap: 11px;
    padding: 14px 12px 6px 16px;
  }
  .avatar {
    flex: none;
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    color: #fff;
    font-size: 16px;
    font-weight: 600;
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.18);
  }
  .who {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .sender {
    overflow: hidden;
    font-size: 14px;
    font-weight: 600;
    line-height: 1.3;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .meta {
    font-size: 12px;
    color: var(--text-dim);
  }
  header.from .close {
    align-self: flex-start;
  }
  .body.words {
    padding: 2px 18px 12px 63px;
    white-space: pre-wrap;
  }
  .answer {
    position: relative;
    display: flex;
    align-items: flex-end;
    gap: 2px;
    margin: 0 12px 12px;
    padding: 4px;
    border: 1px solid var(--border);
    border-radius: 20px;
    background: var(--decline);
    transition: box-shadow 150ms ease;
  }
  .answer.typing {
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--green-glow) 45%, transparent);
  }
  .answer textarea {
    flex: 1;
    box-sizing: border-box;
    min-width: 0;
    height: 30px;
    margin: 0 2px 0 8px;
    padding: 5px 0;
    border: none;
    outline: none;
    resize: none;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 13.5px;
    line-height: 20px;
    overflow-y: auto;
    scrollbar-width: thin;
    user-select: text;
    cursor: text;
  }
  .answer textarea::placeholder {
    color: var(--text-dim);
  }
  .answer .tool,
  .answer .send {
    flex: none;
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    margin: 0;
    padding: 0;
    border: none;
    border-radius: 50%;
    cursor: pointer;
    transition:
      background 150ms ease,
      opacity 150ms ease,
      filter 150ms ease,
      scale 120ms ease;
  }
  .answer .tool {
    background: transparent;
    color: var(--text);
    opacity: 0.6;
  }
  .answer .tool:hover {
    background: var(--decline);
    opacity: 1;
  }
  .answer .send {
    background: var(--green);
    color: #fff;
  }
  .answer .send:hover:not(:disabled) {
    filter: brightness(1.1);
  }
  .answer .send:disabled {
    opacity: 0.32;
    cursor: default;
  }
  .answer button:active:not(:disabled) {
    scale: 0.9;
  }
  .answer svg {
    width: 17px;
    height: 17px;
  }
  .answer .tool svg {
    fill: currentColor;
  }
  .answer .send svg {
    fill: none;
    stroke: currentColor;
    stroke-width: 2.3;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .status {
    position: relative;
    margin: -6px 18px 12px;
    font-size: 12px;
    color: var(--text-dim);
  }
  .status.sending {
    animation: breathe 1.4s ease-in-out infinite;
  }

  /* The dock's dot: still once she's done, pulsing while she writes. */
  .orb {
    position: relative;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 4px color-mix(in srgb, var(--green-glow) 70%, transparent);
  }
  .orb.writing::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: var(--green-glow);
    animation: pulse 1.7s cubic-bezier(0.2, 0.6, 0.35, 1) infinite;
  }

  .close {
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    margin: 0;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: var(--decline);
    color: var(--text);
    cursor: pointer;
    transition:
      background 150ms ease,
      color 150ms ease;
  }
  .close:hover {
    background: #ff453a;
    color: #fff;
  }
  .close svg {
    width: 10px;
    height: 10px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.8;
    stroke-linecap: round;
  }

  .body {
    position: relative;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 2px 18px 16px;
    font-size: 14px;
    line-height: 1.5;
    color: var(--text);
    user-select: text;
    cursor: auto;
    overflow-wrap: anywhere;
    scrollbar-width: thin;
    scrollbar-color: var(--text-dim) transparent;
  }
  .body :global(p) {
    margin: 0 0 8px;
  }
  .body :global(p.heading) {
    font-weight: 600;
    margin-top: 4px;
  }
  .body :global(ul),
  .body :global(ol) {
    margin: 0 0 8px;
    padding-left: 20px;
  }
  .body :global(li) {
    margin: 2px 0;
  }
  .body :global(li::marker) {
    color: var(--text-dim);
  }
  .body :global(> :last-child) {
    margin-bottom: 0;
  }
  .body :global(strong) {
    font-weight: 600;
  }
  .body :global(code) {
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: 12.5px;
    padding: 1px 5px;
    border-radius: 6px;
    background: var(--decline);
  }
  .body :global(pre) {
    margin: 0 0 8px;
    padding: 8px 10px;
    border-radius: 10px;
    background: var(--decline);
    overflow-x: auto;
  }
  .body :global(pre code) {
    padding: 0;
    background: none;
  }
  .body :global(a) {
    color: var(--link);
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
  }
  /* Yes and No to Natro's question on the phone (reply.rs): buttons, as in the chat. */
  .body :global(a[href^='natro:answer/']) {
    display: inline-block;
    margin: 4px 6px 0 0;
    padding: 0 16px;
    line-height: 28px;
    border-radius: 14px;
    font-size: 13px;
    font-weight: 600;
    text-decoration: none;
    background: var(--decline);
    color: var(--text);
    transition:
      background 150ms ease,
      color 150ms ease,
      filter 150ms ease,
      scale 120ms ease;
  }
  .body :global(a[href^='natro:answer/']:active) {
    scale: 0.95;
  }
  .body :global(a[href^='natro:answer/'][href$='/yes']) {
    background: var(--green);
    color: #fff;
  }
  .body :global(a[href^='natro:answer/'][href$='/yes']:hover) {
    filter: brightness(1.1);
  }
  .body :global(a[href^='natro:answer/'][href$='/no']:hover) {
    background: #ff453a;
    color: #fff;
  }
  .body :global(.caret) {
    display: inline-block;
    width: 2px;
    height: 1.05em;
    margin-left: 2px;
    vertical-align: text-bottom;
    border-radius: 1px;
    background: var(--green-glow);
    animation: blink 1s steps(2, start) infinite;
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
  @keyframes blink {
    to {
      visibility: hidden;
    }
  }
  @keyframes breathe {
    50% {
      opacity: 0.45;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .stage {
      --spring: ease-out;
    }
    .orb.writing::before,
    .body :global(.caret),
    .status.sending {
      animation: none;
    }
  }
</style>
