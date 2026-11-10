<script lang="ts">
  // The chat's + menu (ChatBar.svelte has the + under it): a new chat, files to the phone, files for Natro, a
  // picture, Settings. Up and down move between its lines; a line closes it, then does its thing.
  import { invoke } from '@tauri-apps/api/core'
  import { cubicIn } from 'svelte/easing'
  import { scale } from 'svelte/transition'
  import { ICONS } from './chaticons'

  type Props = {
    open: boolean
    // It closes (the bar keeps the focus where it is: the line's thing may move it).
    close: () => void
    newChat: () => void
    // The bar's file picker, for a picture.
    pickPicture: () => void
  }
  let { open, close, newChat, pickPicture }: Props = $props()

  let menuBox: HTMLElement | undefined = $state()

  /** Opened from the keyboard: its first line takes the focus. */
  export function focusFirst() {
    menuBox?.querySelector('button')?.focus()
  }

  export function contains(target: Node) {
    return !!menuBox?.contains(target)
  }

  /** A line of the menu: it closes, then does its thing. */
  function choose(action: () => void) {
    close()
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
</script>


{#snippet line(at: number, label: string, hint: string, icon: string, action: () => void)}
  <button class="item" role="menuitem" style:--at={at} onclick={() => choose(action)}>
    <svg viewBox="0 0 20 20" aria-hidden="true">{@html icon}</svg>
    <span>{label}</span>
    {#if hint}<span class="hint">{hint}</span>{/if}
  </button>
{/snippet}

{#if open}
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
    {@render line(2, 'Files for Natro', 'to convert or edit', ICONS.file, () => invoke('chat_for_natro'))}
    {@render line(3, 'Send a picture', 'or paste one', ICONS.picture, pickPicture)}
    <span class="divider" style:--at={4}></span>
    {@render line(4, 'Settings', '', ICONS.settings, () => invoke('settings_open'))}
  </div>
{/if}

<style>
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
  button:focus-visible {
    outline: 2px solid var(--green-glow);
    outline-offset: 1px;
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

  @media (prefers-reduced-motion: reduce) {
    .menu,
    .item,
    .divider {
      animation: none;
    }
  }
</style>
