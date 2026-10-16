<script lang="ts">
  // The dock: a glass pill that drops down from the top of the screen.
  // Rust (src-tauri/src/dock.rs) places and shows the window and says when to
  // slide in or out, and what to show; this page draws the pill and animates it.
  import { invoke } from '@tauri-apps/api/core'
  import { listen } from '@tauri-apps/api/event'
  import { onMount, tick } from 'svelte'
  import { fly, scale } from 'svelte/transition'
  import { GLYPHS } from './glyphs'

  // step: what Natro is doing, from the brain ("Opening Spotify"); asking: her question,
  // waiting for a yes or no; problem: "Natro is offline" and the like.
  type Status = 'listening' | 'thinking' | 'step' | 'done' | 'asking' | 'problem'
  type DockEvent = { action: 'show' } | { action: 'hide' }
  // A step has a kind of picture (icon), or the app's own icon (image, a data URL).
  type StatusEvent = { status: Status; label: string; icon: string | null; image: string | null }
  // Padding and border, and the gap after the icon (px); keep in step with the CSS below.
  const AROUND = 24 * 2 + 2 + 8
  // A long question is cut short at this width (px); the ✕ beside it takes this much more.
  const LONGEST_LABEL = 240
  const DECLINE = 8 + 20

  let open = $state(false)
  let status = $state<Status>('listening')
  let label = $state('Listening…')
  let icon = $state<string | null>(null)
  let image = $state<string | null>(null)
  let labelWidth = $state(0)
  // The dot is 8 px; the spinner and the check need a little more room, a step's picture a bit more.
  let iconWidth = $derived(status === 'step' ? 16 : status === 'thinking' || status === 'done' ? 14 : 8)
  let shownLabel = $derived(Math.min(labelWidth, LONGEST_LABEL))
  let width = $derived(AROUND + iconWidth + shownLabel + (status === 'asking' ? DECLINE : 0))
  // Rust needs it to know where clicks land (while Natro asks).
  $effect(() => {
    invoke('dock_size', { width })
  })

  /** While Natro asks: a click on the pill is yes, on its ✕ no. */
  function answer(yes: boolean) {
    if (status === 'asking') invoke('dock_answer', { yes })
  }

  /** A click on the pill: yes while she asks, else her last reply comes back (the reply box). */
  function clicked() {
    if (status === 'asking') answer(true)
    else invoke('reply_show')
  }
  // The microphone's loudness, 0 to 1, smoothed for the eye.
  let level = $state(0)
  let heard = 0
  let frame = 0
  // Counts shows and hides, so a late step of an old one does nothing.
  let turn = 0

  /** RMS to 0..1 on a decibel scale: a quiet room is about 0, loud speech 1. */
  function loudness(rms: number) {
    const db = 20 * Math.log10(Math.max(rms, 1e-5))
    return Math.min(1, Math.max(0, (db + 52) / 36))
  }

  function animateLevel() {
    const target = open && status === 'listening' ? heard : 0
    level += (target - level) * (target > level ? 0.4 : 0.12) // quick to rise, slow to fall
    frame = open || level > 0.002 ? requestAnimationFrame(animateLevel) : 0
  }

  async function show() {
    const mine = ++turn
    heard = 0
    await tick()
    // Let the closed pill be drawn once, so the slide starts from there.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        if (mine !== turn) return
        open = true
        if (!frame) frame = requestAnimationFrame(animateLevel)
      }),
    )
  }

  function showStatus(event: StatusEvent) {
    status = event.status
    label = event.label
    icon = event.icon
    image = event.image
  }

  onMount(() => {
    const unlisten = [
      listen<DockEvent>('dock', ({ payload }) => {
        if (payload.action === 'show') show()
        else {
          turn++
          open = false
        }
      }),
      listen<StatusEvent>('status', ({ payload }) => showStatus(payload)),
      listen<number>('level', ({ payload }) => (heard = loudness(payload))),
    ]
    // The dock may have opened while this page was still loading.
    Promise.all(unlisten).then(async () => {
      const showing = await invoke<StatusEvent | null>('dock_state')
      if (showing && !open) {
        showStatus(showing)
        show()
      }
    })
    return () => {
      cancelAnimationFrame(frame)
      unlisten.forEach((stop) => stop.then((f) => f()))
    }
  })
</script>

<main class="stage {status}" class:open>
  <!-- Keyboard users answer with Ctrl+Alt+Y / Ctrl+Alt+N or Esc; the window never takes the keyboard. -->
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="pill" style:width="{width}px" title={status === 'asking' ? label : null} onclick={clicked}>
    <div class="sheen"></div>
    <div class="content">
      <span class="icon" style:--level={level}>
        <span class="orb"></span>
        <span class="spinner"></span>
        <svg class="check" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="8" />
          <path d="M4.6 8.3l2.2 2.2 4.6-4.8" />
        </svg>
        {#if status === 'step'}
          {#key image ?? icon}
            <span class="picture" in:scale={{ start: 0.5, duration: 280 }} out:scale={{ start: 0.5, duration: 160 }}>
              {#if image}
                <img src={image} alt="" />
              {:else}
                <svg viewBox="0 0 16 16" aria-hidden="true">{@html GLYPHS[icon ?? 'work'] ?? GLYPHS.work}</svg>
              {/if}
            </span>
          {/key}
        {/if}
      </span>
      <span class="labels" style:width="{shownLabel}px">
        {#key label}
          <span class="label" in:fly={{ y: 7, duration: 260, delay: 70 }} out:fly={{ y: -7, duration: 160 }}>
            {label}
          </span>
        {/key}
      </span>
      {#if status === 'asking'}
        <button
          class="decline"
          aria-label="No"
          transition:scale={{ start: 0.5, duration: 220 }}
          onclick={(event) => {
            event.stopPropagation()
            answer(false)
          }}
        >
          <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" /></svg>
        </button>
      {/if}
    </div>
  </div>
  <span class="label measure" aria-hidden="true" bind:clientWidth={labelWidth}>{label}</span>
</main>

<style>
  /* The look is the NatsarkekiaAI dock's (app.css); the motion is Natro's.
     Keep in step with dock.rs: STAGE 360 × 120, SLIDE_OUT 320 ms. */
  .stage {
    --out: cubic-bezier(0.55, 0, 0.75, 0.2);
    --spring: cubic-bezier(0.18, 1.25, 0.4, 1);
    --resize: cubic-bezier(0.2, 0.9, 0.3, 1);
    height: 100%;
  }

  .pill {
    position: relative;
    height: 45px;
    margin: 20px auto 0;
    border-radius: 20px;
    overflow: hidden;
    box-shadow: var(--shadow);
    transform: translateY(-84px) scale(0.94);
    opacity: 0;
    /* Closing: rise and fade. */
    transition:
      transform 320ms var(--out),
      opacity 220ms ease-in 100ms,
      width 420ms var(--resize);
  }
  .open .pill {
    transform: none;
    opacity: 1;
    /* Opening: drop with a little spring. */
    transition:
      transform 520ms var(--spring),
      opacity 200ms ease-out,
      width 420ms var(--resize),
      scale 120ms ease-out;
  }

  .sheen {
    position: absolute;
    inset: 0;
    box-sizing: border-box;
    border-radius: inherit;
    border: 1px solid var(--border);
    background: var(--glass);
    box-shadow: inset 0 1px 0 0 var(--highlight);
  }

  .content {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    height: 100%;
    padding: 0 24px;
  }

  .label {
    display: inline-block;
    font-size: 14px;
    font-weight: 500;
    line-height: 19px;
    white-space: nowrap;
  }
  .measure {
    position: absolute;
    visibility: hidden;
  }
  .labels {
    position: relative;
    height: 19px;
    transition: width 420ms var(--resize);
  }
  .labels .label {
    position: absolute;
    left: 0;
    top: 0;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* Natro asks: the pill is a button. Over it the dot becomes a green check (yes); the ✕ says no. */
  .asking .pill {
    cursor: pointer;
  }
  .asking .pill:hover:not(:has(.decline:hover)) .orb {
    opacity: 0;
  }
  .asking .pill:hover:not(:has(.decline:hover)) .check {
    opacity: 1;
    transform: none;
  }
  .asking .pill:hover:not(:has(.decline:hover)) .check path {
    stroke-dashoffset: 0;
    transition: stroke-dashoffset 260ms ease-out 60ms;
  }
  .asking .pill:active:not(:has(.decline:hover)) {
    scale: 0.97;
  }
  .decline {
    flex: none;
    display: grid;
    place-items: center;
    width: 20px;
    height: 20px;
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
  .decline:hover {
    background: #ff453a;
    color: #fff;
  }
  .decline svg {
    width: 10px;
    height: 10px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.6;
    stroke-linecap: round;
  }
  .thinking .labels .label {
    background: linear-gradient(90deg, var(--text) 30%, var(--text-dim) 50%, var(--text) 70%) 0 0 / 300% 100%;
    background-clip: text;
    color: transparent;
    animation: shimmer 1.5s linear infinite;
  }

  /* The dot: green and alive while listening, a spinner while thinking, a check when done. */
  .icon {
    position: relative;
    flex: none;
    width: 8px;
    height: 8px;
    transition:
      width 360ms var(--spring),
      height 360ms var(--spring);
  }
  .thinking .icon,
  .done .icon {
    width: 14px;
    height: 14px;
  }
  .step .icon {
    width: 16px;
    height: 16px;
  }
  .orb,
  .spinner,
  .check {
    position: absolute;
    inset: 0;
    transition:
      transform 360ms var(--spring),
      opacity 200ms ease;
  }
  .orb {
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 calc(2px + var(--level) * 12px) color-mix(in srgb, var(--green-glow) 70%, transparent);
    transform: scale(calc(1 + var(--level) * 0.6));
    transition: opacity 200ms ease;
  }
  .orb::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: var(--green-glow);
    opacity: 0;
  }
  .open.listening .orb::before,
  .open.asking .orb::before {
    animation: pulse 1.7s cubic-bezier(0.2, 0.6, 0.35, 1) infinite;
  }
  /* Waiting for a yes or no: amber. Something wrong: red, still. */
  .asking .orb {
    background: linear-gradient(135deg, #c27803 0%, #f5a623 100%);
  }
  .asking .orb::before {
    background: #f5a623;
  }
  .problem .orb {
    background: linear-gradient(135deg, #b3261e 0%, #ff453a 100%);
  }
  .spinner {
    border-radius: 50%;
    background: conic-gradient(from 0deg, transparent 10%, var(--text));
    mask: radial-gradient(farthest-side, transparent calc(100% - 2px), #000 calc(100% - 1.5px));
    opacity: 0;
    transform: scale(0.4);
  }
  .check {
    opacity: 0;
    transform: scale(0.4);
  }
  .check circle {
    fill: var(--green-glow);
  }
  .check path {
    fill: none;
    stroke: #fff;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-dasharray: 12;
    stroke-dashoffset: 12;
  }

  .thinking .orb,
  .step .orb,
  .done .orb {
    opacity: 0;
  }

  /* A step's picture: the app's own icon, or a small line drawing. */
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
  .thinking .spinner {
    opacity: 1;
    transform: none;
    animation: spin 0.8s linear infinite;
  }
  .done .check {
    opacity: 1;
    transform: none;
  }
  .done .check path {
    stroke-dashoffset: 0;
    transition: stroke-dashoffset 320ms ease-out 120ms;
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

  @media (prefers-reduced-motion: reduce) {
    .stage {
      --spring: ease-out;
    }
    .open.listening .orb::before,
    .open.asking .orb::before,
    .thinking .labels .label {
      animation: none;
    }
  }
</style>
