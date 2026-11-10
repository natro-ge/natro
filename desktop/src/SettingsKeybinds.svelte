<script lang="ts" module>
  // keys: "Ctrl+Alt+Space" (the double tap's: "Alt", or "" when it's off); default: the first
  // ones; taken: another program has them (the double tap: Windows didn't let Natro watch keys).
  export type Keybind = { name: string; keys: string; default: boolean; taken: boolean }
</script>

<script lang="ts">
  // Settings' keybinds: Natro's keys for talking, the chat, and yes and no. Rust remembers them
  // (src-tauri/src/settings.rs). A click on one waits for the new keys (Esc keeps the old); on
  // the chat's double tap, for a modifier tapped twice (Backspace turns it off). Looks:
  // settings.css.
  import { invoke } from '@tauri-apps/api/core'
  import { tick } from 'svelte'
  import { DoubleTap } from './twice'

  // keybinds: as Rust sends them. capturing: the keybind waiting for new keys, if one is.
  let { keybinds = $bindable(), capturing = $bindable(null) }: { keybinds: Keybind[]; capturing?: string | null } =
    $props()

  // The keybinds Rust sends, by name (settings.rs's KEYBINDS, and the double tap).
  const KEYBINDS: Record<string, { label: string; note: string }> = {
    talk: { label: 'Talk to Natro', note: 'Hold it and talk, or tap it and talk hands-free' },
    chat: { label: 'Open the chat', note: 'Type to Natro; press it again to close the chat' },
    chat_twice: { label: 'Double tap for the chat', note: 'Tap the key twice quickly, from any window' },
    yes: { label: 'Answer yes', note: 'When Natro asks before something risky' },
    no: { label: 'Answer no', note: 'When Natro asks before something risky' },
  }
  // As the page shows a held modifier, by KeyboardEvent.key.
  const MODIFIERS: Record<string, string> = { Control: 'Ctrl', Alt: 'Alt', AltGraph: 'Alt', Shift: 'Shift', Meta: 'Win' }

  // The modifiers held so far for the keybind waiting, and why the last keys pressed couldn't be a
  // keybind's.
  let held = $state<string[]>([])
  let keyProblem = $state<{ name: string; text: string } | null>(null)
  let catcher: HTMLElement | undefined = $state()
  // While the double tap waits: the modifiers tapped so far.
  const taps = new DoubleTap()

  /** A click on a keybind: it waits for the new keys, and meanwhile Natro lets hers go, so they reach the page. */
  async function capture(name: string) {
    keyProblem = null
    held = []
    capturing = name
    taps.interrupt()
    invoke('settings_capture', { on: true })
    await tick()
    catcher?.focus()
  }

  /** Done waiting: new keys, Esc, or a click elsewhere. Natro takes her keys again. */
  function stopCapture(refocus: boolean) {
    const name = capturing
    if (!name) return
    capturing = null
    held = []
    invoke<Keybind[]>('settings_capture', { on: false }).then((list) => (keybinds = list))
    if (refocus) tick().then(() => document.getElementById(`keys-${name}`)?.focus())
  }

  function modifiers(event: KeyboardEvent) {
    return [event.ctrlKey && 'Ctrl', event.altKey && 'Alt', event.shiftKey && 'Shift', event.metaKey && 'Win'].filter(
      (part) => part !== false,
    )
  }

  /** While a keybind waits: modifiers show as they're held; the first other key, with them, is its new keys. */
  async function captureKey(event: KeyboardEvent) {
    event.preventDefault()
    event.stopPropagation()
    const name = capturing
    if (!name || event.repeat) return
    held = modifiers(event)
    if (event.key in MODIFIERS) return
    if (event.key === 'Escape' && !held.length) return stopCapture(true)
    // As the shortcut plugin reads them: "control+alt+KeyT" (the key where it is, whatever the layout).
    const mods = [event.shiftKey && 'shift', event.ctrlKey && 'control', event.altKey && 'alt', event.metaKey && 'super']
    setKeys(name, [...mods.filter((part) => part !== false), event.code].join('+'))
  }

  /** While the double tap waits: a modifier tapped twice is its new key; Backspace turns it off. */
  function captureTap(event: KeyboardEvent) {
    event.preventDefault()
    event.stopPropagation()
    if (capturing !== 'chat_twice') return
    taps.press(event)
    if (event.repeat) return
    if (event.key === 'Escape') stopCapture(true)
    else if (event.key === 'Backspace' || event.key === 'Delete') setKeys('chat_twice', 'off')
  }

  function captureTapUp(event: KeyboardEvent) {
    event.preventDefault()
    event.stopPropagation()
    const key = taps.release(event)
    if (key && capturing === 'chat_twice') setKeys('chat_twice', key)
  }

  async function setKeys(name: string, keys: string) {
    try {
      keybinds = await invoke<Keybind[]>('settings_keybind', { name, keys })
      keyProblem = null
      if (capturing === name) stopCapture(true)
    } catch (problem) {
      keyProblem = { name, text: String(problem) }
      held = []
    }
  }

  async function resetKeybinds() {
    keyProblem = null
    keybinds = await invoke<Keybind[]>('settings_keybinds_reset')
  }
</script>

<svelte:window onblur={() => stopCapture(false)} />

<section class="wide">
  <h2>
    Keybinds
    {#if keybinds.some((keybind) => !keybind.default)}
      <span class="links">
        <button class="link" onclick={resetKeybinds}>Reset</button>
      </span>
    {/if}
  </h2>
  {#each keybinds as keybind (keybind.name)}
    {@render keys(keybind)}
  {/each}
  <p class="fixed">Esc closes the dock, the chat and the reply box.</p>
</section>

{#snippet keys(keybind: Keybind)}
  {@const about = KEYBINDS[keybind.name]}
  {@const twice = keybind.name === 'chat_twice'}
  <div class="row keybind">
    <span class="words">
      <span class="label">{about?.label ?? keybind.name}</span>
      {#if keyProblem?.name === keybind.name}
        <span class="detail bad">{keyProblem.text}</span>
      {:else if capturing === keybind.name && twice}
        <span class="detail">Tap Ctrl, Alt or Shift twice; Backspace turns it off, Esc keeps it</span>
      {:else if capturing === keybind.name}
        <span class="detail">Press the new keys, or Esc to keep these</span>
      {:else if keybind.taken && twice}
        <span class="detail bad">Windows didn't let Natro watch the keys: restart Natro</span>
      {:else if keybind.taken}
        <span class="detail bad">Another program has these keys: choose others</span>
      {:else if twice && !keybind.keys}
        <span class="detail">Off: click it to choose Ctrl, Alt or Shift</span>
      {:else}
        <span class="detail">{about?.note}</span>
      {/if}
    </span>
    {#if capturing === keybind.name && twice}
      <div
        class="keycaps waiting"
        role="textbox"
        aria-readonly="true"
        aria-label="Tap the new key twice for {about?.label}"
        tabindex="-1"
        bind:this={catcher}
        onkeydown={captureTap}
        onkeyup={captureTapUp}
        onblur={() => stopCapture(false)}
      >
        <span class="prompt">Tap twice</span>
      </div>
    {:else if capturing === keybind.name}
      <div
        class="keycaps waiting"
        role="textbox"
        aria-readonly="true"
        aria-label="New keys for {about?.label ?? keybind.name}"
        tabindex="-1"
        bind:this={catcher}
        onkeydown={captureKey}
        onkeyup={(event) => (held = modifiers(event))}
        onblur={() => stopCapture(false)}
      >
        {#each held as part (part)}
          <kbd>{part}</kbd>
        {/each}
        <span class="prompt">{held.length ? '+ …' : 'Press keys'}</span>
      </div>
    {:else if twice}
      <button
        id="keys-{keybind.name}"
        class="keycaps"
        class:taken={keybind.taken}
        title="Change"
        aria-label="{about?.label}: {keybind.keys ? `${keybind.keys} twice` : 'off'}. Change"
        onclick={() => capture(keybind.name)}
      >
        {#if keybind.keys}
          <kbd>{keybind.keys}</kbd>
          <span class="times">×2</span>
        {:else}
          <span class="prompt">Off</span>
        {/if}
      </button>
    {:else}
      <button
        id="keys-{keybind.name}"
        class="keycaps"
        class:taken={keybind.taken}
        title="Change"
        aria-label="{about?.label ?? keybind.name}: {keybind.keys}. Change"
        onclick={() => capture(keybind.name)}
      >
        {#each keybind.keys.split('+') as part, at (at)}
          <kbd>{part}</kbd>
        {/each}
      </button>
    {/if}
  </div>
{/snippet}
