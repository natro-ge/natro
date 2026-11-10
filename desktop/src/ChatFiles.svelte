<script lang="ts">
  // The files for Natro, over the chat's box (ChatBar.svelte) until his next request, each with its ✕.
  import { fade } from 'svelte/transition'
  import { ICONS } from './chaticons'

  type Props = {
    // Their names.
    attached: string[]
    // How far over the box (pixels), clear of the message being answered.
    above: number
    // How tall they are, for what goes above them.
    height?: number
    detach: (at: number) => void
  }
  let { attached, above, height = $bindable(0), detach }: Props = $props()
</script>


{#if attached.length}
  <div
    class="attached"
    data-hit
    bind:clientHeight={height}
    style:bottom="calc(100% + {above}px)"
    transition:fade={{ duration: 140 }}
  >
    {#each attached as name, at (name)}
      <span class="file" title={name}>
        <svg class="icon" viewBox="0 0 20 20" aria-hidden="true">{@html ICONS.file}</svg>
        <span class="name">{name}</span>
        <button
          aria-label={`Take ${name} off`}
          title="Take it off"
          onclick={() => detach(at)}
        >
          <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 2.5l5 5M7.5 2.5l-5 5" /></svg>
        </button>
      </span>
    {/each}
  </div>
{/if}

<style>
  /* The files for Natro: over the box, each with its ✕, until his next request. */
  .attached {
    position: absolute;
    left: 14px;
    right: 14px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    box-sizing: border-box;
    max-height: 104px;
    overflow-y: auto;
    scrollbar-width: thin;
    padding: 6px;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: var(--glass-dense);
    box-shadow: var(--shadow);
    font-size: 13px;
  }
  .file {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    max-width: 100%;
    padding: 3px 3px 3px 9px;
    border-radius: 11px;
    background: var(--decline);
  }
  .file .icon {
    flex: none;
    width: 14px;
    height: 14px;
    opacity: 0.78;
  }
  .file .icon :global(.line) {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.9;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .file .name {
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .file button {
    flex: none;
    display: grid;
    place-items: center;
    width: 20px;
    height: 20px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: inherit;
    cursor: pointer;
  }
  .file button:hover {
    background: rgba(127, 127, 127, 0.2);
  }
  .file button svg {
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
