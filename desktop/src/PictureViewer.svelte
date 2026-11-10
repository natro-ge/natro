<script lang="ts">
  // One of the chat's pictures, large, over the whole chat window (it covers the screen above the
  // taskbar; Chat.svelte draws the chat at its bottom). It opens fitted to the window, never larger
  // than 100%: a pixel of the picture to a pixel of the screen, as a screenshot was taken. The wheel
  // zooms around the mouse; Ctrl + and Ctrl − or the buttons around the middle. A drag moves it
  // while it's larger than the window, a double click goes between fitted and 100%, and Ctrl+0 or
  // the fit button puts it back. Esc (the chat's keys see it first), the ✕ or a click beside the
  // picture closes it; the last button opens it in Windows' own viewer instead.
  import { onMount } from 'svelte'
  import { fade } from 'svelte/transition'

  let { src, close, openOutside }: { src: string; close: () => void; openOutside: () => void } = $props()

  // A button's or key's zoom step; the wheel's, per pixel it turns (a notch is 100: about a fifth).
  const STEP = 1.5
  const WHEEL = 0.002
  // The most it zooms, in screen pixels per picture pixel; the least is half of fitted.
  const MOST = 8
  // Room around the fitted picture: the ✕ above it and the zoom bar below.
  const ROOM_X = 40
  const ROOM_Y = 76
  // A press that moves less than this is a click (beside the picture, it closes the viewer).
  const CLICK = 4

  const ICONS = {
    out: '<path class="line" d="M5 10h10"/>',
    in: '<path class="line" d="M10 5v10M5 10h10"/>',
    fit: '<path class="line" d="M3.5 7.5v-4h4m5 0h4v4m0 5v4h-4m-5 0h-4v-4"/>',
    outside:
      '<path class="line" d="M11.5 3.5h5v5m0-5-7 7M8.5 4.5h-3a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2v-3"/>',
    close: '<path class="line" d="m5.5 5.5 9 9m0-9-9 9"/>',
  }

  let root: HTMLElement | undefined = $state()
  let frame: HTMLElement | undefined = $state()
  // The window's size and the picture's (CSS pixels, picture pixels).
  let width = $state(0)
  let height = $state(0)
  let natural = $state({ w: 0, h: 0 })
  // CSS pixels per picture pixel, and where the picture's middle is from the window's.
  let zoom = $state(0)
  let x = $state(0)
  let y = $state(0)
  // Fitted until he zooms: it stays fitted if the window changes.
  let fitting = $state(true)
  // How the next change moves: smoothly (buttons, keys, the double click), quickly (the wheel), or at once (a drag).
  let motion = $state<'smooth' | 'quick' | 'none'>('smooth')
  let press: { id: number; x: number; y: number; from: [number, number]; moved: boolean; onPicture: boolean } | null =
    null
  let dragging = $state(false)

  const scale = window.devicePixelRatio || 1
  // 100%: a picture pixel per screen pixel.
  const actual = 1 / scale
  let fit = $derived(
    natural.w && width ? Math.min(actual, (width - 2 * ROOM_X) / natural.w, (height - 2 * ROOM_Y) / natural.h) : 0,
  )
  let least = $derived(fit / 2)
  let most = $derived(Math.max(MOST * actual, fit))
  let fitted = $derived(Math.abs(zoom - fit) < 1e-4 && !x && !y)
  let shown = $derived({ w: natural.w * zoom, h: natural.h * zoom })
  let movable = $derived(shown.w > width + 0.5 || shown.h > height + 0.5)
  // On whole screen pixels, so that at 100% it's as sharp as the screen was.
  let left = $derived(snap(width / 2 + x - shown.w / 2))
  let top = $derived(snap(height / 2 + y - shown.h / 2))

  $effect(() => {
    if (fitting && fit) zoom = fit
  })

  function snap(value: number) {
    return Math.round(value * scale) / scale
  }

  /** Where the middle may be: the picture's edges never come inside the window's while it's larger. */
  function within(z: number, nx: number, ny: number): [number, number] {
    const mx = Math.max(0, (natural.w * z - width) / 2)
    const my = Math.max(0, (natural.h * z - height) / 2)
    return [Math.min(mx, Math.max(-mx, nx)), Math.min(my, Math.max(-my, ny))]
  }

  /** Zoom to z, keeping the point (window pixels) where it is on the picture. */
  function zoomTo(z: number, how: 'smooth' | 'quick', px = width / 2, py = height / 2) {
    if (!zoom) return
    z = Math.min(most, Math.max(least, z))
    const [cx, cy] = [px - width / 2, py - height / 2]
    const grow = z / zoom
    ;[x, y] = within(z, cx - (cx - x) * grow, cy - (cy - y) * grow)
    zoom = z
    motion = how
    fitting = false
  }

  function toFit() {
    zoom = fit
    x = y = 0
    motion = 'smooth'
    fitting = true
  }

  /** Fitted and 100% (or, for a picture that fits at 100%, twice that), around the point. */
  function toggle(px?: number, py?: number) {
    if (!fitted) return toFit()
    zoomTo(Math.abs(fit - actual) < 1e-4 ? actual * 2 : actual, 'smooth', px, py)
  }

  function loaded(event: Event) {
    const img = event.currentTarget as HTMLImageElement
    natural = { w: img.naturalWidth, h: img.naturalHeight }
  }

  function onPicture(event: { clientX: number; clientY: number }) {
    const box = frame?.getBoundingClientRect()
    return !!box && event.clientX >= box.left && event.clientX < box.right && event.clientY >= box.top && event.clientY < box.bottom
  }

  function down(event: PointerEvent) {
    if (event.button !== 0 || (event.target as Element).closest('button')) return
    press = { id: event.pointerId, x: event.clientX, y: event.clientY, from: [x, y], moved: false, onPicture: onPicture(event) }
  }

  function move(event: PointerEvent) {
    if (!press || event.pointerId !== press.id) return
    const [dx, dy] = [event.clientX - press.x, event.clientY - press.y]
    if (!press.moved) {
      if (Math.hypot(dx, dy) < CLICK) return
      press.moved = true
      root?.setPointerCapture(event.pointerId)
    }
    if (!movable) return
    dragging = true
    motion = 'none'
    ;[x, y] = within(zoom, press.from[0] + dx, press.from[1] + dy)
  }

  function up(event: PointerEvent) {
    if (!press || event.pointerId !== press.id) return
    const was = press
    press = null
    dragging = false
    if (!was.moved && !was.onPicture && event.type === 'pointerup') close()
  }

  function doubleClick(event: MouseEvent) {
    if (onPicture(event)) toggle(event.clientX, event.clientY)
  }

  function wheel(event: WheelEvent) {
    event.preventDefault()
    const pixels = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? height : 1)
    zoomTo(zoom * Math.exp(-pixels * WHEEL), 'quick', event.clientX, event.clientY)
  }

  /** Ctrl + and Ctrl − (or without Ctrl), Ctrl+0, wherever the focus is. */
  function keydown(event: KeyboardEvent) {
    if (event.key === '+' || event.key === '=') zoomTo(zoom * STEP, 'smooth')
    else if (event.key === '-' || event.key === '_') zoomTo(zoom / STEP, 'smooth')
    else if (event.key === '0') toFit()
    else return
    event.preventDefault()
  }

  onMount(() => {
    root?.focus()
    // Not passive: the wheel zooms the picture, not the page.
    root?.addEventListener('wheel', wheel, { passive: false })
    return () => root?.removeEventListener('wheel', wheel)
  })
</script>

<svelte:window onkeydown={keydown} />

<div
  class="viewer"
  class:dragging
  role="dialog"
  aria-modal="true"
  aria-label="Picture"
  tabindex="-1"
  data-hit
  bind:this={root}
  bind:clientWidth={width}
  bind:clientHeight={height}
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  onpointercancel={up}
  ondblclick={doubleClick}
  in:fade={{ duration: 180 }}
  out:fade={{ duration: 140 }}
>
  <div
    class="frame {motion}"
    class:movable
    class:ready={zoom > 0}
    bind:this={frame}
    style:width="{shown.w}px"
    style:height="{shown.h}px"
    style:transform="translate({left}px, {top}px)"
  >
    <img {src} alt="" draggable="false" onload={loaded} />
  </div>

  <button class="close" title="Close (Esc)" aria-label="Close" onclick={close}>
    <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.close}</svg>
  </button>

  <div class="tools">
    <button title="Zoom out (Ctrl −)" aria-label="Zoom out" disabled={zoom <= least + 1e-4} onclick={() => zoomTo(zoom / STEP, 'smooth')}>
      <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.out}</svg>
    </button>
    <button class="percent" title={fitted ? 'Show at 100%' : 'Fit to the window'} onclick={() => toggle()}>
      {Math.round(zoom * scale * 100)}%
    </button>
    <button title="Zoom in (Ctrl +)" aria-label="Zoom in" disabled={zoom >= most - 1e-4} onclick={() => zoomTo(zoom * STEP, 'smooth')}>
      <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.in}</svg>
    </button>
    <span class="divider"></span>
    <button title="Fit to the window (Ctrl+0)" aria-label="Fit to the window" disabled={fitted} onclick={toFit}>
      <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.fit}</svg>
    </button>
    <button title="Open in Windows' viewer" aria-label="Open in Windows' viewer" onclick={openOutside}>
      <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.outside}</svg>
    </button>
  </div>
</div>

<style>
  /* Dark behind the picture whatever the theme, as photo viewers are; the buttons in the chat's glass. */
  .viewer {
    --spring: cubic-bezier(0.18, 1.25, 0.4, 1);
    position: fixed;
    inset: 0;
    z-index: 10;
    overflow: hidden;
    background: rgba(12, 12, 14, 0.78);
    outline: none;
    touch-action: none;
  }
  .viewer.dragging,
  .viewer.dragging .frame {
    cursor: grabbing;
  }

  .frame {
    position: absolute;
    left: 0;
    top: 0;
    visibility: hidden;
    cursor: zoom-in;
  }
  .frame.ready {
    visibility: visible;
  }
  .frame.movable {
    cursor: grab;
  }
  .frame.smooth {
    transition:
      transform 260ms var(--spring),
      width 260ms var(--spring),
      height 260ms var(--spring);
  }
  .frame.quick {
    transition:
      transform 90ms ease-out,
      width 90ms ease-out,
      height 90ms ease-out;
  }
  .frame img {
    display: block;
    width: 100%;
    height: 100%;
    border-radius: 6px;
    box-shadow: 0 12px 48px rgba(0, 0, 0, 0.45);
    user-select: none;
    -webkit-user-drag: none;
    animation: open 380ms var(--spring) both;
  }

  .tools,
  .close {
    position: absolute;
    border: 1px solid var(--border);
    background: var(--glass-dense);
    box-shadow:
      inset 0 1px 0 0 var(--highlight),
      0 6px 24px rgba(0, 0, 0, 0.3);
    color: var(--text);
  }
  .tools {
    left: 50%;
    bottom: 18px;
    translate: -50% 0;
    display: flex;
    align-items: center;
    gap: 2px;
    padding: 5px;
    border-radius: 24px;
    animation: rise 420ms var(--spring) 60ms both;
  }
  .close {
    top: 18px;
    right: 18px;
    display: grid;
    place-items: center;
    width: 40px;
    height: 40px;
    padding: 0;
    border-radius: 50%;
    cursor: pointer;
    transition:
      filter 150ms ease,
      scale 120ms ease;
  }
  .close:hover {
    filter: brightness(1.15);
  }
  .close:active {
    scale: 0.92;
  }
  .tools button {
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    margin: 0;
    padding: 0;
    border: none;
    border-radius: 18px;
    background: transparent;
    color: var(--text);
    font: inherit;
    cursor: pointer;
    opacity: 0.78;
    transition:
      background 150ms ease,
      opacity 150ms ease,
      scale 120ms ease;
  }
  .tools button:hover:not(:disabled) {
    background: var(--decline);
    opacity: 1;
  }
  .tools button:active:not(:disabled) {
    scale: 0.9;
  }
  .tools button:disabled {
    opacity: 0.3;
    cursor: default;
  }
  .tools .percent {
    width: auto;
    min-width: 58px;
    padding: 0 8px;
    font-size: 13px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    opacity: 1;
  }
  .divider {
    width: 1px;
    height: 20px;
    margin: 0 4px;
    background: var(--decline);
  }
  svg {
    width: 20px;
    height: 20px;
  }
  svg :global(.line) {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.9;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  button:focus-visible {
    outline: 2px solid var(--green-glow);
    outline-offset: 1px;
  }

  @keyframes open {
    from {
      opacity: 0;
      transform: scale(0.92);
    }
  }
  @keyframes rise {
    from {
      opacity: 0;
      transform: translateY(16px);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .frame.smooth,
    .frame.quick {
      transition: none;
    }
    .frame img,
    .tools {
      animation: none;
    }
  }
</style>
