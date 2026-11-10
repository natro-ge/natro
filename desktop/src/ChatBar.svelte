<script lang="ts" module>
  // Where Natro works for what's asked here (works_on.rs), and whether that device is online.
  export type WorksOn = { device: 'pc' | 'phone'; online: boolean }
  // A message from the phone that what's typed answers (the reply box's "answer in the chat"), quoted over the box.
  export type Replying = { id: string; from: string; app: string; text: string }
</script>

<script lang="ts">
  // The chat's input bar (Chat.svelte has the bubbles over it): the text box with what he typed before under Up
  // and Down, the + and its menu (ChatMenu.svelte), the switch for where Natro works, the microphone and send, the
  // message being answered quoted over it (ChatReplying.svelte) and the files for Natro over that (ChatFiles.svelte),
  // and a word over it for a moment.
  import { invoke } from '@tauri-apps/api/core'
  import { listen } from '@tauri-apps/api/event'
  import { onMount, tick } from 'svelte'
  import { fade } from 'svelte/transition'
  import ChatFiles from './ChatFiles.svelte'
  import { ICONS } from './chaticons'
  import ChatMenu from './ChatMenu.svelte'
  import ChatReplying from './ChatReplying.svelte'
  import { pictureData } from './pictures'

  // The text grows to this tall (about five lines), then scrolls.
  const TALLEST_TEXT = 110
  // A notice ("Natro is offline") shows this long.
  const NOTICE_MILLIS = 2600

  type Props = {
    replying: Replying | null
    // The names of the files for Natro, waiting for his next request.
    attached: string[]
    listening: boolean
    // The microphone's loudness, 0 to 1.
    level: number
    // What's typed answers Natro's question.
    answering: boolean
    // Something of his went into the chat: show the newest bubble.
    onsend: () => void
  }
  let { replying = $bindable(), attached, listening, level, answering, onsend }: Props = $props()

  // The + menu: a new chat, files to the phone, files for Natro, a picture, Settings.
  let menu = $state(false)
  let menuPart: ChatMenu | undefined = $state()
  let moreButton: HTMLButtonElement | undefined = $state()
  let picker: HTMLInputElement | undefined = $state()
  let draft = $state('')
  let notice = $state('')
  // The switch beside the +: where Natro works for what's asked here. The phone is green.
  let worksOn = $state<WorksOn>({ device: 'pc', online: true })
  let away = $derived(worksOn.device === 'phone')
  // How tall the quoted message and the files are: the files go above the message, and a notice above both.
  let quoteHeight = $state(0)
  let filesHeight = $state(0)
  let aboveQuote = $derived(replying ? quoteHeight + 6 : 0)
  let aboveFiles = $derived(aboveQuote + (attached.length ? filesHeight + 6 : 0))
  let field: HTMLTextAreaElement | undefined = $state()
  // What he typed to her before, oldest first (chat.rs keeps it), and where Up and Down are in it:
  // at typed.length, the box holds what he was writing (unsent).
  let typed: string[] = []
  let recalled = 0
  let unsent = ''
  let noticeTimer = 0

  export function focus() {
    focusField()
  }

  export function menuOpen() {
    return menu
  }

  /** Esc: the menu closes, or the message stops being answered. False if there was neither. */
  export function escape() {
    if (menu) closeMenu()
    else if (replying) stopReplying()
    else return false
    return true
  }

  /** The chat closed. */
  export function hidden() {
    menu = false
    notice = ''
  }

  function focusField() {
    requestAnimationFrame(() => field?.focus())
  }

  async function send() {
    const text = draft.trim()
    if (!text || listening) return
    draft = ''
    onsend()
    if (!(await invoke<boolean>('chat_send', { text }))) draft = text // offline: keep it
    await loadTyped()
    focusField()
  }

  async function loadTyped() {
    typed = await invoke<string[]>('chat_history')
    recalled = typed.length
    unsent = ''
  }

  /** Up and Down, as in a terminal: what he typed before, while the caret is on the box's first
   *  line (going down, its last). False when the caret just moves in the text instead. */
  function recall(step: -1 | 1) {
    if (!field || field.selectionStart !== field.selectionEnd) return false
    const caret = field.selectionStart
    const edge = step < 0 ? !draft.slice(0, caret).includes('\n') : !draft.slice(caret).includes('\n')
    const next = recalled + step
    if (!edge || next < 0 || next > typed.length) return false
    if (recalled === typed.length) unsent = draft
    recalled = next
    draft = next === typed.length ? unsent : typed[next]
    tick().then(() => field?.setSelectionRange(draft.length, draft.length))
    return true
  }

  function stopReplying() {
    replying = null
    invoke('chat_stop_replying')
    focusField()
  }

  async function sendPicture(file: Blob, name: string) {
    try {
      onsend()
      await invoke<boolean>('chat_picture', { data: await pictureData(file), name })
    } catch {
      showNotice("That picture couldn't be read")
    }
    focusField()
  }

  function picked() {
    for (const file of picker?.files ?? []) sendPicture(file, file.name)
    if (picker) picker.value = ''
  }

  /** A picture pasted into the box goes into the chat; text is pasted as usual. */
  function pasted(event: ClipboardEvent) {
    const item = [...(event.clipboardData?.items ?? [])].find((i) => i.type.startsWith('image/'))
    const file = item?.getAsFile()
    if (!file) return
    event.preventDefault()
    sendPicture(file, file.name || 'pasted picture.png')
  }

  function newChat() {
    invoke('chat_clear')
    focusField()
  }

  export function showNotice(text: string) {
    notice = text
    clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => (notice = ''), NOTICE_MILLIS)
  }

  /** The + opens the menu over it; opened from the keyboard, its first line takes the focus. */
  async function toggleMenu(event: MouseEvent) {
    menu = !menu
    if (!menu) return focusField()
    await tick()
    if (event.detail === 0) menuPart?.focusFirst()
  }

  export function closeMenu(refocus = true) {
    if (!menu) return
    menu = false
    if (refocus) focusField()
  }

  /** A press anywhere but the menu and its + closes the menu. */
  export function pressed(target: Node) {
    if (menu && !menuPart?.contains(target) && !moreButton?.contains(target)) closeMenu(false)
  }


  function fieldKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      send()
    }
    const alone = !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey && !event.isComposing
    const arrow = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0
    if (alone && arrow && recall(arrow)) event.preventDefault()
  }

  // The text box grows with what's written, up to TALLEST_TEXT.
  $effect(() => {
    void draft
    if (!field) return
    field.style.height = 'auto'
    field.style.height = `${Math.min(field.scrollHeight, TALLEST_TEXT)}px`
  })

  onMount(() => {
    const stop = listen<WorksOn>('works_on', ({ payload }) => (worksOn = payload))
    loadTyped()
    stop.then(async () => (worksOn = await invoke<WorksOn>('works_on_state')))
    return () => {
      clearTimeout(noticeTimer)
      stop.then((f) => f())
    }
  })
</script>

{#snippet place(device: WorksOn['device'], label: string)}
  <button
    class="place"
    class:on={worksOn.device === device}
    class:offline={worksOn.device === device && !worksOn.online}
    role="radio"
    aria-checked={worksOn.device === device}
    aria-label={label}
    title={label}
    onclick={() => {
      invoke('works_on_set', { device })
      focusField()
    }}
  >
    <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS[device]}</svg>
  </button>
{/snippet}

<div class="bottom">
  <ChatReplying {replying} bind:height={quoteHeight} stop={stopReplying} />
  <ChatFiles
    {attached}
    above={aboveQuote + 6}
    bind:height={filesHeight}
    detach={(at) => {
      invoke('chat_detach', { at })
      focusField()
    }}
  />
  {#if notice}
    <div
      class="notice"
      style:bottom="calc(100% + {aboveFiles + 6}px)"
      transition:fade={{ duration: 180 }}
    >
      {notice}
    </div>
  {/if}
  <ChatMenu bind:this={menuPart} open={menu} close={() => closeMenu(false)} {newChat} pickPicture={() => picker?.click()} />
  <div class="bar" data-hit>
    <div class="sheen"></div>
    <span class="blob-dot"></span>
    <div class="row">
      <button
        class="tool more"
        class:on={menu}
        title={menu ? 'Close' : 'More'}
        aria-label="More: new chat, files, a picture, settings"
        aria-haspopup="menu"
        aria-expanded={menu}
        bind:this={moreButton}
        onclick={toggleMenu}
      >
        <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.more}</svg>
      </button>
      <div class="where" role="radiogroup" aria-label="Where Natro works">
        {@render place('pc', 'Work on this PC')}
        {@render place(
          'phone',
          away && !worksOn.online ? "Work on your phone (it's offline: Natro works here till it's back)" : 'Work on your phone',
        )}
      </div>
      <input class="picker" type="file" accept="image/*" multiple bind:this={picker} onchange={picked} />
      {#if listening}
        <div class="field heard" style:--level={level}><span class="live"></span>Listening…</div>
      {:else}
        <textarea
          bind:this={field}
          bind:value={draft}
          rows="1"
          placeholder={replying
            ? `Your answer to ${replying.from}`
            : answering
              ? 'Answer Natro: yes or no'
              : attached.length
                ? `What should Natro do with ${attached.length === 1 ? attached[0] : 'these'}?`
              : away
                ? worksOn.online
                  ? 'Ask Natro (on your phone)…'
                  : "Ask Natro (here: your phone's offline)…"
                : 'Ask Natro…'}
          aria-label="Message to Natro"
          onkeydown={fieldKeydown}
          onpaste={pasted}
        ></textarea>
      {/if}
      <button
        class="tool mic"
        class:on={listening}
        title={listening ? 'Send what you said' : 'Talk instead'}
        aria-label={listening ? 'Send what you said' : 'Talk instead'}
        onclick={() => invoke('chat_mic')}
      >
        <svg viewBox="0 0 20 20" aria-hidden="true">{@html listening ? ICONS.stop : ICONS.mic}</svg>
      </button>
      <button class="send" title="Send" aria-label="Send" disabled={!draft.trim() || listening} onclick={send}>
        <svg viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.send}</svg>
      </button>
    </div>
  </div>
</div>

<style>
  .picker {
    display: none;
  }

  /* The box: a blob that rises from below the screen, then widens. */
  .bottom {
    position: relative;
    flex: none;
    display: flex;
    justify-content: center;
    padding-top: 6px;
  }
  .bar {
    position: relative;
    display: flex;
    justify-content: center;
    width: 52px;
    min-height: 52px;
    border-radius: 26px;
    overflow: hidden;
    box-shadow: var(--shadow);
    transform: translateY(100px) scale(0.6);
    opacity: 0;
    /* Closing: narrow back into the blob, then drop. */
    transition:
      width 320ms var(--narrow),
      transform 340ms var(--out) 260ms,
      opacity 180ms ease-in 420ms;
  }
  :global(.open) .bar {
    width: 100%;
    transform: none;
    opacity: 1;
    /* Opening: rise with a little spring, then widen. */
    transition:
      transform 540ms var(--spring),
      opacity 160ms ease-out,
      width 640ms var(--widen) 230ms;
  }
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
  /* The blob's green dot, gone once it widens. */
  .blob-dot {
    position: absolute;
    left: 50%;
    top: 21px;
    width: 10px;
    height: 10px;
    margin-left: -5px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px color-mix(in srgb, var(--green-glow) 60%, transparent);
    transition: opacity 160ms ease 160ms;
  }
  :global(.open) .blob-dot {
    opacity: 0;
    transition: opacity 140ms ease 260ms;
  }
  /* As wide as the open box from the start, centered: widening the box uncovers it. */
  .row {
    position: relative;
    flex: none;
    box-sizing: border-box;
    display: flex;
    align-items: flex-end;
    gap: 2px;
    width: calc(var(--width) - 64px);
    padding: 8px;
    opacity: 0;
    transition: opacity 120ms ease-in;
  }
  :global(.open) .row {
    opacity: 1;
    transition: opacity 280ms ease-out 420ms;
  }

  textarea {
    flex: 1;
    box-sizing: border-box;
    min-width: 0;
    height: 36px;
    max-height: 110px;
    margin: 0 4px;
    padding: 8px 4px;
    border: none;
    outline: none;
    resize: none;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 14.5px;
    line-height: 20px;
    overflow-y: auto;
    scrollbar-width: thin;
    user-select: text;
    cursor: text;
  }
  textarea::placeholder {
    color: var(--text-dim);
  }

  /* Listening: the dock's live green dot instead of the text. */
  .heard {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 10px;
    height: 36px;
    margin: 0 4px;
    padding: 0 4px;
    font-size: 14.5px;
    font-weight: 500;
  }
  .live {
    position: relative;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 calc(2px + var(--level) * 12px) color-mix(in srgb, var(--green-glow) 70%, transparent);
    transform: scale(calc(1 + var(--level) * 0.6));
  }
  .live::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: var(--green-glow);
    animation: pulse 1.7s cubic-bezier(0.2, 0.6, 0.35, 1) infinite;
  }

  .tool,
  .send {
    flex: none;
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    margin: 0;
    padding: 0;
    border: none;
    border-radius: 50%;
    cursor: pointer;
    transition:
      background 150ms ease,
      color 150ms ease,
      opacity 150ms ease,
      filter 150ms ease,
      box-shadow 150ms ease,
      scale 120ms ease;
  }
  .tool {
    background: transparent;
    color: var(--text);
    opacity: 0.72;
  }
  .tool:hover {
    background: var(--decline);
    opacity: 1;
  }
  .tool:active,
  .send:active:not(:disabled) {
    scale: 0.9;
  }
  .tool svg,
  .send svg {
    width: 20px;
    height: 20px;
    fill: currentColor;
  }
  .tool svg :global(.line),
  .send svg :global(.line) {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.9;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .send svg :global(.thick) {
    stroke-width: 2.3;
  }
  .mic.on {
    background: var(--green);
    color: #fff;
    opacity: 1;
  }
  .send {
    background: var(--green);
    color: #fff;
    box-shadow: 0 2px 10px color-mix(in srgb, var(--green-glow) 40%, transparent);
  }
  .send:hover:not(:disabled) {
    filter: brightness(1.1);
  }
  .send:disabled {
    opacity: 0.32;
    box-shadow: none;
    cursor: default;
  }
  button:focus-visible {
    outline: 2px solid var(--green-glow);
    outline-offset: 1px;
  }

  /* Where Natro works: two small round buttons in a groove. This PC, when on, is only lit; the phone
     is green, and dimmed while it's offline (she works here then). */
  .where {
    flex: none;
    display: flex;
    gap: 2px;
    height: 36px;
    box-sizing: border-box;
    padding: 3px;
    margin: 0 2px;
    border-radius: 18px;
    background: var(--decline);
  }
  .place {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    margin: 0;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: transparent;
    color: var(--text);
    opacity: 0.5;
    cursor: pointer;
    transition:
      background 150ms ease,
      color 150ms ease,
      opacity 150ms ease,
      scale 120ms ease;
  }
  .place:hover {
    opacity: 0.85;
  }
  .place:active {
    scale: 0.9;
  }
  .place.on {
    background: var(--glass-dense);
    opacity: 1;
    cursor: default;
  }
  .place.on:last-child {
    background: var(--green);
    color: #fff;
    box-shadow: 0 2px 8px color-mix(in srgb, var(--green-glow) 35%, transparent);
  }
  .place.offline {
    opacity: 0.5;
  }
  .place svg {
    width: 17px;
    height: 17px;
  }
  .place svg :global(.line) {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.7;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  /* The +: turns into a ✕ while its menu is open. */
  .tool svg :global(.thick) {
    stroke-width: 2.2;
  }
  .more svg {
    transition: rotate 360ms var(--spring);
  }
  .more.on {
    background: var(--decline);
    opacity: 1;
  }
  .more.on svg {
    rotate: 45deg;
  }

  /* A word over the box for a moment ("Natro is offline"). */
  .notice {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 50%;
    translate: -50% 0;
    padding: 6px 14px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--glass-dense);
    box-shadow: var(--shadow);
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
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

  @media (prefers-reduced-motion: reduce) {
    .live::before {
      animation: none;
    }
  }
</style>
