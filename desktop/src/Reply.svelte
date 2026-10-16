<script lang="ts">
  // The reply box: Natro's answer as text in the top-right corner, in the dock's glass.
  // Rust (src-tauri/src/reply.rs) places the window and says what to show; this page
  // draws the box, slides it in and out, and says how tall it is, so the window is
  // never bigger than the box. A short reply fades by itself (not while the mouse is
  // over it); a longer one stays until its ✕.
  import { invoke } from '@tauri-apps/api/core'
  import { listen } from '@tauri-apps/api/event'
  import { onMount } from 'svelte'
  import { format, isShort, readingMillis } from './format'

  type ReplyEvent = { action: 'show' | 'text' | 'hide'; text: string; done: boolean; max_height: number }
  // Room around the box for its shadow (px); keep in step with reply.rs (MARGIN).
  const MARGIN = 20

  let open = $state(false)
  let text = $state('')
  let done = $state(false)
  let maxHeight = $state(520)
  let hovering = $state(false)
  let stage: HTMLElement | undefined = $state()
  let body: HTMLElement | undefined = $state()
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
    following = true
    if (body) body.scrollTop = 0
    if (!open) {
      // Let the closed box be drawn once, so the slide starts from there.
      requestAnimationFrame(() => requestAnimationFrame(() => (open = true)))
    }
  }

  // A short reply, once it's whole, fades after the time to read it.
  $effect(() => {
    if (!open || !done || hovering || !isShort(text)) return
    const mine = turn
    const timer = setTimeout(() => mine === turn && close(), readingMillis(text))
    return () => clearTimeout(timer)
  })

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
        } else if (payload.action === 'show') {
          show(payload)
        } else {
          text = payload.text
          done = payload.done
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

<main class="stage" class:open bind:this={stage}>
  <!-- The window never takes the keyboard: the ✕ is for the mouse. -->
  <section
    class="box"
    style:max-height="{maxHeight - 2 * MARGIN}px"
    onmouseenter={() => (hovering = true)}
    onmouseleave={() => (hovering = false)}
    aria-label="Natro's reply"
    aria-live="polite"
  >
    <div class="sheen"></div>
    <header>
      <span class="orb" class:writing={!done}></span>
      <span class="name">Natro</span>
      <button class="close" aria-label="Close" onclick={close}>
        <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" /></svg>
      </button>
    </header>
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
    <div class="body" bind:this={body} onscroll={scrolled} onclick={clicked}>
      {@html html}
    </div>
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

  @media (prefers-reduced-motion: reduce) {
    .stage {
      --spring: ease-out;
    }
    .orb.writing::before,
    .body :global(.caret) {
      animation: none;
    }
  }
</style>
