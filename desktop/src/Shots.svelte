<script lang="ts">
  // A bubble's pictures in the chat (Chat.svelte): screenshots Natro took and ones he sent, by id. A
  // click opens one large; one the chat no longer keeps (it keeps the newest few) says so.
  let { ids, pictures, open }: { ids: string[]; pictures: Record<string, string>; open: (id: string) => void } =
    $props()
</script>

{#each ids as id (id)}
  {#if pictures[id]}
    <button class="shot" title="Open large" onclick={() => open(id)}>
      <img src={pictures[id]} alt="" />
    </button>
  {:else}
    <span class="gone">Picture no longer here</span>
  {/if}
{/each}

<style>
  .shot {
    display: block;
    padding: 0;
    border: none;
    border-radius: 12px;
    background: none;
    overflow: hidden;
    cursor: zoom-in;
  }
  .shot:focus-visible {
    outline: 2px solid var(--green-glow);
    outline-offset: 1px;
  }
  .shot img {
    display: block;
    max-width: 100%;
    max-height: 300px;
    border-radius: 12px;
  }
  .gone {
    padding: 4px 8px;
    font-size: 12px;
    color: var(--text-dim);
  }
</style>
