<script lang="ts">
  // The message from the phone being answered in the chat, quoted over the box (ChatBar.svelte): who, the app,
  // its words, and a ✕ to stop answering it.
  import { fade } from 'svelte/transition'
  import type { Replying } from './ChatBar.svelte'

  // height: how tall it is, for what goes above it.
  let { replying, height = $bindable(0), stop }: { replying: Replying | null; height?: number; stop: () => void } =
    $props()
</script>


{#if replying}
  <div class="replying" data-hit bind:clientHeight={height} transition:fade={{ duration: 140 }}>
    <div class="quoted">
      <span class="whom">Answering <strong>{replying.from}</strong> · {replying.app}, from your phone</span>
      {#if replying.text}<span class="words">{replying.text}</span>{/if}
    </div>
    <button aria-label="Don't answer" title="Don't answer (Esc)" onclick={stop}>
      <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" /></svg>
    </button>
  </div>
{/if}

<style>
  /* Answering a message from the phone: it's quoted over the box (who, the app, its words), with a green edge
     as his own bubbles; a notice goes above it. */
  .replying {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 14px;
    right: 14px;
    display: flex;
    align-items: flex-start;
    gap: 6px;
    box-sizing: border-box;
    padding: 8px 6px 9px 14px;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: var(--glass-dense);
    box-shadow: var(--shadow);
    font-size: 13px;
    overflow: hidden;
  }
  .replying::before {
    content: '';
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 4px;
    background: var(--green);
  }
  .quoted {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .whom {
    overflow: hidden;
    color: var(--text-dim);
    font-size: 12px;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .whom strong {
    color: var(--text);
    font-weight: 600;
  }
  /* Its words: two lines at most. */
  .quoted .words {
    flex: none;
    max-height: 2.8em;
    overflow: hidden;
    line-height: 1.4;
    overflow-wrap: anywhere;
  }
  .replying button {
    flex: none;
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: inherit;
    cursor: pointer;
  }
  .replying button:hover {
    background: rgba(127, 127, 127, 0.2);
  }
  .replying svg {
    width: 10px;
    height: 10px;
    stroke: currentColor;
    stroke-width: 1.4;
    stroke-linecap: round;
  }
  button:focus-visible {
    outline: 2px solid var(--green-glow);
    outline-offset: 1px;
  }
</style>
