<script lang="ts">
  // The Settings window: the tray's switches, the keybinds (SettingsKeybinds.svelte), and the keys
  // behind a PIN (SettingsKeys.svelte). Rust remembers the switches (src-tauri/src/settings.rs).
  // The card opens from the middle of the screen; Esc or the ✕ closes the window, and that locks
  // the keys. Its look is settings.css.
  import { invoke } from '@tauri-apps/api/core'
  import { listen } from '@tauri-apps/api/event'
  import { onMount, tick } from 'svelte'
  import SettingsKeybinds, { type Keybind } from './SettingsKeybinds.svelte'
  import SettingsKeys from './SettingsKeys.svelte'

  type Switches = { speak: boolean; replies: boolean; keep_listening: boolean; verbose: boolean; startup: boolean }
  type SettingsEvent = { action: 'switches'; switches: Switches } | { action: 'online'; online: boolean }

  // Around the header and the content: the card's margins and border, and the space under the content.
  const ROOM = 2 * 12 + 2 + 20
  // The card closing (Esc or the ✕) before the window goes; keep in step with settings.css.
  const CLOSING_MILLIS = 160

  const SWITCHES: { name: keyof Switches; label: string; note: string }[] = [
    { name: 'speak', label: 'Speak replies', note: 'Natro says her replies out loud on this PC; off, only what you ask her to say out loud' },
    { name: 'replies', label: 'Show replies as text', note: 'In a box by the clock, while the chat is closed' },
    { name: 'keep_listening', label: 'Keep listening after a reply', note: 'A few seconds after she answers something you said, you can go on by just talking' },
  ]

  const CLOSE = '<path class="line" d="m5.5 5.5 9 9m0-9-9 9"/>'

  let switches = $state<Switches>({ speak: true, replies: true, keep_listening: true, verbose: false, startup: false })
  let header: HTMLElement | undefined = $state()
  let content: HTMLElement | undefined = $state()
  let online = $state(false)
  // The card: hidden until the window fits it in the middle of the screen, then opening from there.
  let opened = $state(false)
  let closing = $state(false)
  let loaded = false
  let centered = false

  let keybinds = $state<Keybind[]>([])
  // The keybind waiting for new keys, if one is (SettingsKeybinds.svelte).
  let capturing = $state<string | null>(null)
  let keys: SettingsKeys | undefined = $state()

  function switchTo(name: keyof Switches, on: boolean) {
    switches[name] = on
    invoke('settings_switch', { name, on })
  }

  function close() {
    if (closing) return
    closing = true
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches
    setTimeout(() => invoke('settings_close'), still ? 0 : CLOSING_MILLIS)
  }

  function keydown(event: KeyboardEvent) {
    if (capturing) return // the keybind's (SettingsKeybinds.svelte)
    if (event.key === 'Escape') {
      event.preventDefault()
      close()
    }
    keys?.touched()
  }

  // The window is as tall as what's in it (Rust keeps it within the screen's reach), so there's no
  // empty glass under the locked keys; unlocked, it grows and the fields scroll. The first time,
  // once the switches and keybinds are known, it moves to the middle of the screen, and the card
  // opens (not waiting for the keys: asking Natro whether there's a PIN can take seconds).
  function fit() {
    if (!loaded || !header || !content) return
    const center = !centered
    centered = true
    invoke('settings_size', { height: header.offsetHeight + content.offsetHeight + ROOM, center }).finally(() => {
      // Drawn once hidden where it now is, so it opens from there.
      if (center) requestAnimationFrame(() => requestAnimationFrame(() => (opened = true)))
    })
  }

  onMount(() => {
    const sizes = new ResizeObserver(fit)
    if (content) sizes.observe(content)
    const stop = listen<SettingsEvent>('settings', ({ payload }) => {
      if (payload.action === 'switches') switches = payload.switches
      else {
        online = payload.online
        keys?.connection(online)
      }
    })
    invoke<{ switches: Switches; keybinds: Keybind[]; online: boolean }>('settings_state')
      .then((state) => {
        switches = state.switches
        keybinds = state.keybinds
        online = state.online
      })
      .finally(async () => {
        loaded = true
        await tick()
        fit()
      })
    return () => {
      stop.then((unlisten) => unlisten())
      sizes.disconnect()
    }
  })
</script>

<svelte:window onkeydown={keydown} onpointerdown={() => keys?.touched()} />

<div class="card" class:opened class:closing>
  <header bind:this={header} data-tauri-drag-region>
    <span class="dot" class:off={!online} title={online ? 'Natro is online' : 'Natro is offline'}></span>
    <h1 data-tauri-drag-region>Settings</h1>
    <button class="icon-button" title="Close (Esc)" aria-label="Close" onclick={close}>
      <svg viewBox="0 0 20 20" aria-hidden="true">{@html CLOSE}</svg>
    </button>
  </header>

  <div class="scroll">
    <div class="content" bind:this={content}>
      <section class="replies">
        <h2>Replies</h2>
        {#each SWITCHES as item (item.name)}
          {@render toggle(item.name, item.label, item.note)}
        {/each}
      </section>

      <section>
        <h2>Startup</h2>
        {@render toggle('startup', 'Start with Windows', 'Natro starts by the clock when you sign in')}
      </section>

      <section>
        <h2>Testing</h2>
        {@render toggle('verbose', 'Verbose', 'Keep every recording, and what happened to it, in logs/verbose')}
      </section>

      <SettingsKeybinds bind:keybinds bind:capturing />

      <SettingsKeys bind:this={keys} />
    </div>
  </div>
</div>

{#snippet toggle(name: keyof Switches, label: string, detail: string)}
  <label class="row">
    <span class="words">
      <span class="label">{label}</span>
      <span class="detail">{detail}</span>
    </span>
    <input
      class="switch"
      type="checkbox"
      role="switch"
      checked={switches[name]}
      onchange={(event) => switchTo(name, event.currentTarget.checked)}
    />
  </label>
{/snippet}
