<script lang="ts">
  // Settings' keys, behind a PIN. Rust keeps them (src-tauri/src/keys.rs): the server's, through
  // Natro, and this PC's .env, one tab each. Keys are never shown whole, only their last four
  // characters; a new one is pasted in its place. Closing the window locks them. Looks:
  // settings.css.
  import { invoke } from '@tauri-apps/api/core'
  import { onMount, tick } from 'svelte'
  import { fade, slide } from 'svelte/transition'

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

  const ICONS = {
    lock: '<path fill-rule="evenodd" d="M10 1.5a4.25 4.25 0 0 0-4.25 4.25V8.5H5.5A1.75 1.75 0 0 0 3.75 10.25v6.5c0 .97.78 1.75 1.75 1.75h9c.97 0 1.75-.78 1.75-1.75v-6.5c0-.97-.78-1.75-1.75-1.75h-.25V5.75A4.25 4.25 0 0 0 10 1.5zm2.6 7V5.75a2.6 2.6 0 1 0-5.2 0V8.5z"/>',
    open: '<path fill-rule="evenodd" d="M13.75 1.5a4.25 4.25 0 0 0-4.25 4.25V8.5h-4A1.75 1.75 0 0 0 3.75 10.25v6.5c0 .97.78 1.75 1.75 1.75h9c.97 0 1.75-.78 1.75-1.75v-6.5c0-.97-.78-1.75-1.75-1.75h-3.4V5.75a2.6 2.6 0 1 1 5.2 0V7.5h1.65V5.75A4.25 4.25 0 0 0 13.75 1.5z"/>',
    eye: '<path fill-rule="evenodd" d="M10 4.5c-4 0-7 3.2-8 5.5 1 2.3 4 5.5 8 5.5s7-3.2 8-5.5c-1-2.3-4-5.5-8-5.5zm0 8.6a3.1 3.1 0 1 0 0-6.2 3.1 3.1 0 0 0 0 6.2z"/>',
  }

  // Whether there's a PIN yet (null until known), and what's typed for it.
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
  /** A key pressed or a click anywhere in the window (Settings.svelte says). */
  export function touched() {
    clearTimeout(idle)
    if (unlocked) idle = setTimeout(() => lock('Locked after 5 minutes.'), UNLOCKED_MILLIS)
  }

  /** Natro's connection, as Settings.svelte hears it: back after she was offline (or started again), her keys need
      the PIN again. */
  export function connection(online: boolean) {
    if (online && unlocked && !server.ok) lock('Natro is back: enter the PIN again for her keys.', true)
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

  // A wait after wrong PINs, counted down.
  $effect(() => {
    if (wait <= 0) return
    const timer = setInterval(() => {
      wait = Math.max(0, wait - 1)
      if (wait === 0) say('')
    }, 1000)
    return () => clearInterval(timer)
  })

  onMount(() => {
    invoke<{ pin: boolean }>('keys_status').then((status) => {
      hasPin = status.pin
      tick().then(() => pinField?.focus())
    })
    return () => clearTimeout(idle)
  })
</script>

<section class="wide">
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
