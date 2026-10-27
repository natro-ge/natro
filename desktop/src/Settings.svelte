<script lang="ts">
  // The Settings window: the tray's switches, the keybinds, and the keys behind a PIN. Rust
  // remembers the switches and the keybinds (src-tauri/src/settings.rs) and keeps the keys
  // (src-tauri/src/keys.rs): the server's, through Natro, and this PC's .env. Keys are never
  // shown whole, only their last four characters; a new one is pasted in its place. A click
  // on a keybind waits for the new keys (Esc keeps the old); on the chat's double tap, for a
  // modifier tapped twice (Backspace turns it off). The card opens from the middle of the
  // screen; Esc or the ✕ closes the window, and that locks the keys.
  import { invoke } from '@tauri-apps/api/core'
  import { listen } from '@tauri-apps/api/event'
  import { onMount, tick } from 'svelte'
  import { fade, slide } from 'svelte/transition'
  import { DoubleTap } from './twice'

  type Switches = { speak: boolean; replies: boolean; verbose: boolean; startup: boolean }
  type SettingsEvent = { action: 'switches'; switches: Switches } | { action: 'online'; online: boolean }
  // keys: "Ctrl+Alt+Space" (the double tap's: "Alt", or "" when it's off); default: the first
  // ones; taken: another program has them (the double tap: Windows didn't let Natro watch keys).
  type Keybind = { name: string; keys: string; default: boolean; taken: boolean }
  // A key's hint is "••••ab12"; choices: [value, name] for a list (the voice).
  type Field = {
    key: string
    label: string
    secret: boolean
    set: boolean
    value: string
    hint: string
    note: string
    choices?: [string, string][]
  }
  type ServerKeys = { ok: boolean; fields?: Field[]; message?: string; offline?: boolean }
  type Answer = {
    ok: boolean
    message?: string
    wait?: number
    locked?: boolean
    new_pin?: boolean
    pc?: Field[]
    server?: ServerKeys
    errors?: Record<string, string>
    restarting?: boolean
    fields?: Field[]
  }
  type Tab = 'server' | 'pc'

  // Unlocked keys lock again after this long without being touched (as in keys.rs).
  const UNLOCKED_MILLIS = 5 * 60 * 1000
  // Around the header and the content: the card's margins and border, and the space under the content.
  const ROOM = 2 * 12 + 2 + 20
  // The card closing (Esc or the ✕) before the window goes; keep in step with the CSS below.
  const CLOSING_MILLIS = 160

  const SWITCHES: { name: keyof Switches; label: string; note: string }[] = [
    { name: 'speak', label: 'Speak replies', note: 'Natro says her replies out loud on this PC; off, only what you ask her to say out loud' },
    { name: 'replies', label: 'Show replies as text', note: 'In a box by the clock, while the chat is closed' },
  ]

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

  const ICONS = {
    close: '<path class="line" d="m5.5 5.5 9 9m0-9-9 9"/>',
    lock: '<path fill-rule="evenodd" d="M10 1.5a4.25 4.25 0 0 0-4.25 4.25V8.5H5.5A1.75 1.75 0 0 0 3.75 10.25v6.5c0 .97.78 1.75 1.75 1.75h9c.97 0 1.75-.78 1.75-1.75v-6.5c0-.97-.78-1.75-1.75-1.75h-.25V5.75A4.25 4.25 0 0 0 10 1.5zm2.6 7V5.75a2.6 2.6 0 1 0-5.2 0V8.5z"/>',
    open: '<path fill-rule="evenodd" d="M13.75 1.5a4.25 4.25 0 0 0-4.25 4.25V8.5h-4A1.75 1.75 0 0 0 3.75 10.25v6.5c0 .97.78 1.75 1.75 1.75h9c.97 0 1.75-.78 1.75-1.75v-6.5c0-.97-.78-1.75-1.75-1.75h-3.4V5.75a2.6 2.6 0 1 1 5.2 0V7.5h1.65V5.75A4.25 4.25 0 0 0 13.75 1.5z"/>',
    eye: '<path fill-rule="evenodd" d="M10 4.5c-4 0-7 3.2-8 5.5 1 2.3 4 5.5 8 5.5s7-3.2 8-5.5c-1-2.3-4-5.5-8-5.5zm0 8.6a3.1 3.1 0 1 0 0-6.2 3.1 3.1 0 0 0 0 6.2z"/>',
  }

  let switches = $state<Switches>({ speak: true, replies: true, verbose: false, startup: false })
  let header: HTMLElement | undefined = $state()
  let content: HTMLElement | undefined = $state()
  let online = $state(false)
  // The card: hidden until the window fits it in the middle of the screen, then opening from there.
  let opened = $state(false)
  let closing = $state(false)
  let loaded = false
  let centered = false

  let keybinds = $state<Keybind[]>([])
  // The keybind waiting for new keys, the modifiers held for it so far, and why the last keys pressed
  // couldn't be a keybind's.
  let capturing = $state<string | null>(null)
  let held = $state<string[]>([])
  let keyProblem = $state<{ name: string; text: string } | null>(null)
  let catcher: HTMLElement | undefined = $state()
  // While the double tap waits: the modifiers tapped so far.
  const taps = new DoubleTap()

  // The keys: whether there's a PIN yet (null until known), and what's typed for it.
  let hasPin = $state<boolean | null>(null)
  let pin = $state('')
  let pinAgain = $state('')
  let pinField: HTMLInputElement | undefined = $state()
  let message = $state('')
  let good = $state(false)
  let wait = $state(0)
  let busy = $state(false)

  let unlocked = $state(false)
  let tab = $state<Tab>('server')
  let server = $state<ServerKeys>({ ok: false })
  let pc = $state<Field[]>([])
  // What he typed, by key; for a key, only once he chose to replace it.
  let edits = $state<Record<string, string>>({})
  let replacing = $state<Record<string, boolean>>({})
  let shown = $state<Record<string, boolean>>({})
  let errors = $state<Record<string, string>>({})
  // Under the fields, by Save: what happened ("good" or "bad"), or what saving does.
  let note = $state('')
  let noteKind = $state<'' | 'good' | 'bad'>('')
  let changingPin = $state(false)
  let newPin = $state('')
  let newPinAgain = $state('')

  let fields = $derived(tab === 'server' ? (server.fields ?? []) : pc)
  let changes = $derived(Object.fromEntries(fields.filter(changed).map((field) => [field.key, edits[field.key].trim()])))
  let changedCount = $derived(Object.keys(changes).length)

  function changed(field: Field) {
    const typed = edits[field.key]
    if (typed === undefined) return false
    return field.secret ? replacing[field.key] && typed.trim() !== '' : typed.trim() !== field.value
  }

  function say(text: string, ok = false) {
    message = text
    good = ok
  }

  function tell(text: string, kind: '' | 'good' | 'bad' = '') {
    note = text
    noteKind = kind
  }

  function digits(value: string) {
    return value.replace(/\D/g, '').slice(0, 12)
  }

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

  async function unlock() {
    if (busy || wait > 0) return
    if (hasPin === false) {
      if (!/^\d{4,12}$/.test(pin)) return say('A PIN is 4 to 12 digits.')
      if (pin !== pinAgain) return say("The two PINs aren't the same.")
    }
    busy = true
    say('')
    const answer = await invoke<Answer>('keys_unlock', { pin })
    busy = false
    if (!answer.ok) {
      say(answer.message ?? "Couldn't unlock.")
      if (answer.wait) wait = answer.wait
      pin = ''
      await tick()
      pinField?.focus()
      return
    }
    hasPin = true
    pin = pinAgain = ''
    pc = answer.pc ?? []
    server = answer.server ?? { ok: false }
    edits = {}
    replacing = {}
    errors = {}
    tab = server.ok || !server.offline ? 'server' : 'pc'
    unlocked = true
    tell(answer.new_pin ? 'Your PIN is set. You need it each time you open the keys.' : '', 'good')
    touched()
  }

  function lock(why = '', ok = false) {
    if (unlocked) invoke('keys_lock')
    unlocked = false
    changingPin = false
    edits = {}
    replacing = {}
    shown = {}
    errors = {}
    tell('')
    say(why, ok)
    tick().then(() => pinField?.focus())
  }

  // Unlocked keys lock again after a while without being touched.
  let idle: ReturnType<typeof setTimeout> | undefined
  function touched() {
    clearTimeout(idle)
    if (unlocked) idle = setTimeout(() => lock('Locked after 5 minutes.'), UNLOCKED_MILLIS)
  }

  function replace(field: Field) {
    replacing[field.key] = true
    edits[field.key] = ''
    tick().then(() => document.getElementById(`field-${field.key}`)?.focus())
  }

  function keep(field: Field) {
    replacing[field.key] = false
    delete edits[field.key]
    delete errors[field.key]
  }

  async function save() {
    if (busy || !changedCount) return
    busy = true
    errors = {}
    tell(tab === 'server' ? 'Checking the new keys with their providers…' : 'Saving…')
    const answer = await invoke<Answer>('keys_save', { target: tab, values: changes })
    busy = false
    if (answer.locked) return lock(answer.message ?? 'Locked: enter the PIN again.')
    if (!answer.ok) {
      errors = answer.errors ?? {}
      return tell(answer.message ?? "Couldn't save.", 'bad')
    }
    if (!answer.restarting) {
      edits = {}
      replacing = {}
      return tell('Nothing changed.')
    }
    if (tab === 'server') {
      lock('Saved. Natro is starting again with the new keys: about 10 seconds.', true)
    } else {
      pc = answer.fields ?? pc
      edits = {}
      replacing = {}
      tell('Saved. Natro starts again on this PC…', 'good')
    }
  }

  async function setPin() {
    if (!/^\d{4,12}$/.test(newPin)) return tell('A PIN is 4 to 12 digits.', 'bad')
    if (newPin !== newPinAgain) return tell("The two PINs aren't the same.", 'bad')
    busy = true
    const answer = await invoke<Answer>('keys_pin', { pin: newPin })
    busy = false
    if (answer.locked) return lock(answer.message)
    newPin = newPinAgain = ''
    changingPin = !answer.ok
    tell(answer.message ?? '', answer.ok ? 'good' : 'bad')
  }

  function keydown(event: KeyboardEvent) {
    if (capturing) return // the keybind's (captureKey)
    if (event.key === 'Escape') {
      event.preventDefault()
      close()
    }
    touched()
  }

  // A wait after wrong PINs, counted down.
  $effect(() => {
    if (wait <= 0) return
    const timer = setInterval(() => {
      wait = Math.max(0, wait - 1)
      if (wait === 0) say('')
    }, 1000)
    return () => clearInterval(timer)
  })

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
        // Back after she was offline (or started again): her keys need the PIN again.
        if (online && unlocked && !server.ok) lock('Natro is back: enter the PIN again for her keys.', true)
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
    invoke<{ pin: boolean }>('keys_status').then((status) => {
      hasPin = status.pin
      tick().then(() => pinField?.focus())
    })
    return () => {
      stop.then((unlisten) => unlisten())
      sizes.disconnect()
      clearTimeout(idle)
    }
  })
</script>

<svelte:window onkeydown={keydown} onpointerdown={touched} onblur={() => stopCapture(false)} />

<div class="card" class:opened class:closing>
  <header bind:this={header} data-tauri-drag-region>
    <span class="dot" class:off={!online} title={online ? 'Natro is online' : 'Natro is offline'}></span>
    <h1 data-tauri-drag-region>Settings</h1>
    <button class="icon-button" title="Close (Esc)" aria-label="Close" onclick={close}>
      <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.close}</svg>
    </button>
  </header>

  <div class="scroll">
    <div class="content" bind:this={content}>
      <section>
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

      <section>
        <h2>Testing</h2>
        {@render toggle('verbose', 'Verbose', 'Keep every recording, and what happened to it, in logs/verbose')}
      </section>

      <section class="keys">
        <h2>
          Keys
          <svg class="padlock" class:unlocked viewBox="0 0 20 20" aria-hidden="true">
            {@html unlocked ? ICONS.open : ICONS.lock}
          </svg>
          {#if unlocked}
            <span class="links">
              <button class="link" onclick={() => ((changingPin = !changingPin), tell(''))}>Change PIN</button>
              <button class="link" onclick={() => lock()}>Lock</button>
            </span>
          {/if}
        </h2>

        {#if !unlocked}
          <form
            class="unlock"
            onsubmit={(event) => {
              event.preventDefault()
              unlock()
            }}
          >
            <div class="pin-row">
              <input
                bind:this={pinField}
                class="pin"
                type="password"
                inputmode="numeric"
                autocomplete="off"
                placeholder={hasPin === false ? 'New PIN (4 to 12 digits)' : 'PIN'}
                aria-label="PIN"
                value={pin}
                oninput={(event) => (pin = event.currentTarget.value = digits(event.currentTarget.value))}
                disabled={wait > 0}
              />
              {#if hasPin === false}
                <input
                  class="pin"
                  type="password"
                  inputmode="numeric"
                  autocomplete="off"
                  placeholder="Again"
                  aria-label="The PIN again"
                  value={pinAgain}
                  oninput={(event) => (pinAgain = event.currentTarget.value = digits(event.currentTarget.value))}
                />
              {/if}
              <button class="primary" type="submit" disabled={busy || wait > 0 || pin.length < 4 || hasPin === null}>
                {#if busy}<span class="spinner"></span>{/if}
                {hasPin === false ? 'Set PIN' : 'Unlock'}
              </button>
            </div>
            {#if message || wait > 0}
              <p class="message" class:good transition:fade={{ duration: 150 }}>
                {wait > 0 ? `Too many wrong tries: wait ${wait} s.` : message}
              </p>
            {/if}
          </form>
        {:else}
          <div transition:slide={{ duration: 220 }}>
            {#if changingPin}
              <form
                class="pin-row change"
                transition:slide={{ duration: 180 }}
                onsubmit={(event) => {
                  event.preventDefault()
                  setPin()
                }}
              >
                <input
                  class="pin"
                  type="password"
                  inputmode="numeric"
                  autocomplete="off"
                  placeholder="New PIN"
                  aria-label="New PIN"
                  value={newPin}
                  oninput={(event) => (newPin = event.currentTarget.value = digits(event.currentTarget.value))}
                />
                <input
                  class="pin"
                  type="password"
                  inputmode="numeric"
                  autocomplete="off"
                  placeholder="Again"
                  aria-label="The new PIN again"
                  value={newPinAgain}
                  oninput={(event) => (newPinAgain = event.currentTarget.value = digits(event.currentTarget.value))}
                />
                <button class="primary" type="submit" disabled={busy || newPin.length < 4}>Set</button>
              </form>
            {/if}

            <div class="tabs" role="tablist">
              <button role="tab" aria-selected={tab === 'server'} class:on={tab === 'server'} onclick={() => ((tab = 'server'), tell(''))}>
                Natro's server
              </button>
              <button role="tab" aria-selected={tab === 'pc'} class:on={tab === 'pc'} onclick={() => ((tab = 'pc'), tell(''))}>
                This PC
              </button>
            </div>

            {#if tab === 'server' && !server.ok}
              <p class="message">{server.message ?? "Natro's keys aren't open."}</p>
            {:else}
              <div class="fields">
                {#each fields as field (field.key)}
                  {@render row(field)}
                {/each}
              </div>
              <div class="save">
                <p class="note" class:good={noteKind === 'good'} class:bad={noteKind === 'bad'}>
                  {#if note}
                    {note}
                  {:else if tab === 'server'}
                    New keys are checked first. Saving starts Natro again.
                  {:else}
                    Saving starts Natro again on this PC.
                  {/if}
                </p>
                <button class="primary" disabled={busy || !changedCount} onclick={save}>
                  {#if busy}<span class="spinner"></span>{/if}
                  Save
                </button>
              </div>
            {/if}
          </div>
        {/if}
      </section>
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

{#snippet row(field: Field)}
  <div class="field" class:wrong={errors[field.key]}>
    <label class="words" for="field-{field.key}">
      <span class="label">{field.label}</span>
      {#if field.note}<span class="detail">{field.note}</span>{/if}
    </label>
    {#if field.secret && !replacing[field.key]}
      <div class="current">
        <span class="hint" class:unset={!field.set}>{field.set ? field.hint : 'Not set'}</span>
        <button class="small" onclick={() => replace(field)}>{field.set ? 'Replace' : 'Add'}</button>
      </div>
    {:else if field.secret}
      <div class="current">
        <input
          id="field-{field.key}"
          class="text"
          type={shown[field.key] ? 'text' : 'password'}
          autocomplete="off"
          spellcheck="false"
          placeholder="Paste the new key"
          bind:value={edits[field.key]}
        />
        <button
          class="icon-button"
          class:on={shown[field.key]}
          title={shown[field.key] ? 'Hide' : 'Show'}
          aria-label={shown[field.key] ? 'Hide the new key' : 'Show the new key'}
          onclick={() => (shown[field.key] = !shown[field.key])}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.eye}</svg>
        </button>
        <button class="small" onclick={() => keep(field)}>Keep</button>
      </div>
    {:else if field.choices}
      <select
        id="field-{field.key}"
        class="text"
        value={edits[field.key] ?? field.value}
        onchange={(event) => (edits[field.key] = event.currentTarget.value)}
      >
        {#each field.choices as [value, name] (value)}
          <option {value}>{name}</option>
        {/each}
      </select>
    {:else}
      <input
        id="field-{field.key}"
        class="text"
        type="text"
        autocomplete="off"
        spellcheck="false"
        value={edits[field.key] ?? field.value}
        oninput={(event) => (edits[field.key] = event.currentTarget.value)}
      />
    {/if}
    {#if errors[field.key]}
      <p class="error" transition:slide={{ duration: 150 }}>{errors[field.key]}</p>
    {/if}
  </div>
{/snippet}

<style>
  /* The dock's look (app.css), as a solid card with room for its shadow: unlike the chat it sits
     over other windows for a while, with fields to read. Keep in step with settings.rs: the
     window is 480 wide and as tall as the card (ROOM: its margins and border). It opens from
     the middle of the screen: settings.rs moves the window there before it shows. */
  :global(#app) {
    height: 100%;
  }
  :global(input),
  :global(select),
  :global(button) {
    font: inherit;
  }

  .card {
    --panel: #fafafa;
    --red: #e5484d;
    --field: rgba(0, 0, 0, 0.05);
    --field-line: rgba(0, 0, 0, 0.1);
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    height: calc(100% - 24px);
    margin: 12px;
    border: 1px solid var(--border);
    border-radius: 20px;
    background: var(--panel);
    box-shadow:
      inset 0 1px 0 0 var(--highlight),
      0 6px 20px rgba(0, 0, 0, 0.16);
    overflow: hidden;
    font-size: 13.5px;
    opacity: 0;
    transform: scale(0.82);
  }
  /* Opening: grows out of its middle with a little spring. */
  .card.opened {
    opacity: 1;
    transform: none;
    transition:
      transform 480ms cubic-bezier(0.18, 1.25, 0.4, 1),
      opacity 200ms ease-out;
  }
  /* Closing (Esc or the ✕): back into its middle, quickly (CLOSING_MILLIS). */
  .card.opened.closing {
    opacity: 0;
    transform: scale(0.9);
    transition:
      transform 160ms cubic-bezier(0.55, 0, 0.75, 0.2),
      opacity 140ms ease-in;
  }
  @media (prefers-reduced-motion: reduce) {
    .card,
    .card.opened.closing {
      transform: none;
    }
  }
  @media (prefers-color-scheme: dark) {
    .card {
      --panel: #222224;
      --field: rgba(255, 255, 255, 0.07);
      --field-line: rgba(255, 255, 255, 0.12);
    }
  }

  header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 12px 10px 20px;
  }
  h1 {
    flex: 1;
    margin: 0;
    font-size: 16px;
    font-weight: 600;
  }
  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px color-mix(in srgb, var(--green-glow) 60%, transparent);
    transition: background 200ms ease;
  }
  .dot.off {
    background: var(--text-dim);
    box-shadow: none;
  }

  .scroll {
    flex: 1;
    min-height: 0;
    padding: 0 20px 20px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--field-line) transparent;
  }

  /* Measured for the window's height: its sections' margins count too. */
  .content {
    display: flow-root;
  }
  section {
    margin-top: 14px;
  }
  h2 {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 0 0 6px;
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-dim);
  }
  .padlock {
    width: 13px;
    height: 13px;
    fill: currentColor;
  }
  .padlock.unlocked {
    fill: var(--green-glow);
  }
  .links {
    display: flex;
    gap: 12px;
    margin-left: auto;
    text-transform: none;
    letter-spacing: normal;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 9px 0;
    border-bottom: 1px solid var(--field-line);
    cursor: pointer;
  }
  section > .row:last-child {
    border-bottom: none;
  }
  .words {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .label {
    font-weight: 500;
  }
  .detail {
    font-size: 12px;
    color: var(--text-dim);
  }

  /* A switch: the dock's green when on. */
  .switch {
    appearance: none;
    flex: none;
    position: relative;
    width: 38px;
    height: 22px;
    margin: 0;
    border-radius: 11px;
    background: var(--decline);
    cursor: pointer;
    transition: background 180ms ease;
  }
  .switch::after {
    content: '';
    position: absolute;
    top: 2px;
    left: 2px;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: #fff;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
    transition: transform 220ms cubic-bezier(0.18, 1.25, 0.4, 1);
  }
  .switch:checked {
    background: var(--green);
  }
  .switch:checked::after {
    transform: translateX(16px);
  }
  .switch:focus-visible,
  .text:focus-visible,
  .pin:focus-visible {
    outline: 2px solid color-mix(in srgb, var(--green-glow) 60%, transparent);
    outline-offset: 1px;
  }

  .unlock {
    margin-top: 8px;
  }
  .pin-row {
    display: flex;
    gap: 8px;
  }
  .pin-row.change {
    margin: 2px 0 12px;
  }
  .pin,
  .text {
    box-sizing: border-box;
    min-width: 0;
    height: 34px;
    padding: 0 12px;
    border: 1px solid var(--field-line);
    border-radius: 10px;
    background: var(--field);
    color: var(--text);
    outline: none;
    user-select: text;
  }
  .pin {
    flex: 1;
    letter-spacing: 0.2em;
  }
  .pin::placeholder,
  .text::placeholder {
    letter-spacing: normal;
    color: var(--text-dim);
  }
  .text {
    width: 100%;
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: 12.5px;
  }
  select.text {
    font-family: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  select.text option {
    background: var(--panel);
    color: var(--text);
  }

  button {
    border: none;
    cursor: pointer;
  }
  .primary {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    flex: none;
    height: 34px;
    padding: 0 18px;
    border-radius: 17px;
    background: var(--green);
    color: #fff;
    font-weight: 600;
    transition:
      filter 150ms ease,
      opacity 150ms ease,
      scale 120ms ease;
  }
  .primary:hover:not(:disabled) {
    filter: brightness(1.1);
  }
  .primary:active:not(:disabled) {
    scale: 0.96;
  }
  .primary:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .small {
    flex: none;
    height: 28px;
    padding: 0 12px;
    border-radius: 14px;
    background: var(--decline);
    color: var(--text);
    font-size: 12.5px;
    font-weight: 500;
  }
  .small:hover {
    background: color-mix(in srgb, var(--decline) 100%, var(--text) 8%);
  }
  .link {
    padding: 0;
    background: none;
    color: var(--link);
    font-size: 12px;
    font-weight: 500;
  }
  .link:hover {
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .icon-button {
    display: grid;
    place-items: center;
    flex: none;
    width: 30px;
    height: 30px;
    padding: 0;
    border-radius: 50%;
    background: transparent;
    color: var(--text);
    opacity: 0.7;
    transition:
      background 150ms ease,
      opacity 150ms ease;
  }
  .icon-button:hover,
  .icon-button.on {
    background: var(--decline);
    opacity: 1;
  }
  .icon-button svg {
    width: 18px;
    height: 18px;
    fill: currentColor;
  }
  .icon-button svg :global(.line) {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.8;
    stroke-linecap: round;
  }
  .message,
  .note,
  .error {
    margin: 8px 0 0;
    font-size: 12.5px;
    line-height: 1.4;
    color: var(--red);
  }
  .message.good,
  .note.good {
    color: var(--link);
  }
  .note {
    flex: 1;
    margin: 0;
    color: var(--text-dim);
  }
  .note.bad {
    color: var(--red);
  }

  .tabs {
    display: flex;
    gap: 2px;
    margin: 6px 0 4px;
    padding: 3px;
    border-radius: 12px;
    background: var(--field);
  }
  .tabs button {
    flex: 1;
    height: 28px;
    border-radius: 9px;
    background: transparent;
    color: var(--text-dim);
    font-weight: 500;
    transition:
      background 150ms ease,
      color 150ms ease;
  }
  .tabs button.on {
    background: var(--panel);
    color: var(--text);
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
  }

  .fields {
    display: flex;
    flex-direction: column;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 7px;
    padding: 10px 0;
    border-bottom: 1px solid var(--field-line);
  }
  .field.wrong .text {
    border-color: var(--red);
  }
  .current {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .hint {
    flex: 1;
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: 12.5px;
    letter-spacing: 0.04em;
  }
  .hint.unset {
    font-family: inherit;
    letter-spacing: normal;
    color: var(--text-dim);
  }
  .error {
    margin: 0;
  }

  .save {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 14px;
  }

  .spinner {
    width: 12px;
    height: 12px;
    border: 2px solid rgba(255, 255, 255, 0.4);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 700ms linear infinite;
  }

  /* A keybind: its keys as keycaps; a click waits for new ones, in the dock's green. */
  .keybind {
    cursor: default;
  }
  .detail.bad {
    color: var(--red);
  }
  .keycaps {
    flex: none;
    display: flex;
    align-items: center;
    gap: 4px;
    min-height: 32px;
    padding: 0 6px;
    border-radius: 10px;
    background: transparent;
    color: var(--text);
    outline: none;
    transition:
      background 150ms ease,
      box-shadow 150ms ease;
  }
  .keycaps:hover,
  .keycaps:focus-visible {
    background: var(--field);
  }
  .keycaps:focus-visible {
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--green-glow) 60%, transparent);
  }
  .keycaps.taken kbd {
    border-color: color-mix(in srgb, var(--red) 55%, transparent);
    color: var(--red);
  }
  .keycaps.waiting {
    min-width: 116px;
    justify-content: center;
    background: color-mix(in srgb, var(--green) 10%, transparent);
    box-shadow: 0 0 0 1.5px var(--green-glow);
    animation: waiting 1.4s ease-in-out infinite;
  }
  kbd {
    display: inline-grid;
    place-items: center;
    box-sizing: border-box;
    min-width: 24px;
    height: 24px;
    padding: 0 7px;
    border: 1px solid var(--field-line);
    border-bottom-width: 2px;
    border-radius: 6px;
    background: var(--field);
    font: inherit;
    font-size: 12px;
    font-weight: 600;
    animation: key-in 220ms cubic-bezier(0.18, 1.25, 0.4, 1) both;
  }
  .prompt {
    font-size: 12.5px;
    font-weight: 500;
    color: var(--text-dim);
  }
  /* The double tap: its key, then how many times. */
  .times {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-dim);
  }
  .fixed {
    margin: 8px 0 0;
    font-size: 12px;
    color: var(--text-dim);
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @keyframes waiting {
    50% {
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--green-glow) 35%, transparent);
    }
  }
  @keyframes key-in {
    from {
      opacity: 0;
      transform: scale(0.7);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .keycaps.waiting,
    kbd {
      animation: none;
    }
  }
</style>
