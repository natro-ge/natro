<script lang="ts" module>
  // What the chat shows, as chat.rs sends it.
  export type Step = { label: string; icon: string; image: string | null }
  // answer: empty while Natro waits; then "Yes", "No", or what was typed or said.
  // device: whose question, for one about a request made on the phone ("" for the PC's).
  export type Question = { id: string; text: string; answer: string; device: string }
  // asked: typed, or her English for what was said; heard: the Georgian, for a spoken request.
  export type Exchange = {
    id: string
    asked: string
    heard: string
    spoken: boolean
    reply: string
    done: boolean
    step: Step | null
    questions: Question[]
    problem: string
    // The device it was asked on, for one made on the phone ("" for the PC's).
    from: string
    pictures: string[]
    // Asked on that device while the PC was off, run here after.
    queued: string
    // A picture he sent, not a request.
    sent: boolean
    // A reminder that went off: her words alone, with no request.
    reminder: boolean
    // A background job's task: her work on it as it goes, then what she found.
    job: string
    // A routine's name, on the job that is its run (an older server leaves it out).
    routine?: string
    // Something a watcher saw: her words alone, as a reminder's.
    alert: boolean
  }
  // A request he made on the phone while the PC was off, to run or skip.
  export type Queued = { id: string; text: string; from: string; time: number }
</script>

<script lang="ts">
  // The chat's bubbles (Chat.svelte holds them and scrolls them): each exchange's request, her questions, her
  // reply or what she's doing, and its pictures (Shots.svelte); then what he asked on the phone while the PC was
  // off, to run or skip.
  import { scale } from 'svelte/transition'
  import { ICONS } from './chaticons'
  import { format } from './format'
  import { GLYPHS } from './glyphs'
  import Shots from './Shots.svelte'

  type Props = {
    exchanges: Exchange[]
    queued: Queued[]
    // Picture id -> data URL.
    pictures: Record<string, string>
    answer: (question: Question, yes: boolean) => void
    runQueued: (item: Queued, run: boolean) => void
    openPicture: (id: string) => void
  }
  let { exchanges, queued, pictures, answer, runQueued, openPicture }: Props = $props()

  function time(millis: number) {
    return new Date(millis).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  }
</script>

{#each exchanges as exchange (exchange.id)}
  {#if exchange.reminder}
    <span class="from reminder">Reminder</span>
  {:else if exchange.alert}
    <span class="from reminder">Heads-up</span>
  {:else if exchange.job}
    <span class="from reminder job" title={exchange.job}>{exchange.routine ? `Routine: ${exchange.routine}` : `In the background: ${exchange.job}`}</span>
  {:else if exchange.from || exchange.queued}
    <span class="from">
      {exchange.queued ? `Asked on your ${exchange.queued} while the PC was off` : `On your ${exchange.from}`}
    </span>
  {/if}
  {#if exchange.sent}
    <div class="bubble you shots" class:elsewhere={exchange.from} data-hit>
      <Shots ids={exchange.pictures} {pictures} open={openPicture} />
    </div>
  {:else if exchange.asked || exchange.spoken}
    <div class="bubble you" class:elsewhere={exchange.from} data-hit title={exchange.heard || null}>
      {#if exchange.spoken}
        <svg class="said" viewBox="0 0 20 20" aria-label="Said">{@html ICONS.mic}</svg>
      {/if}
      {#if exchange.asked}
        {exchange.asked}
      {:else}
        <span class="dots" aria-label="Working out what you said"><i></i><i></i><i></i></span>
      {/if}
    </div>
  {/if}
  {#each exchange.questions as question (question.id)}
    <div class="bubble natro question" class:waiting={!question.answer} data-hit>
      <p><span class="orb"></span><span>{question.text}</span></p>
      {#if question.answer}
        <span class="answer">Your answer: {question.answer}</span>
      {:else}
        <div class="choices">
          <button class="yes" onclick={() => answer(question, true)}>Yes</button>
          <button class="no" onclick={() => answer(question, false)}>No</button>
        </div>
      {/if}
    </div>
  {/each}
  {#if exchange.reply || exchange.problem || (!exchange.done && (exchange.asked || exchange.job) && !exchange.questions.some((q) => !q.answer))}
    <div class="bubble natro" class:problem={!exchange.reply && exchange.problem} data-hit>
      {#if exchange.reply}
        <div class="text">{@html format(exchange.reply, !exchange.done)}</div>
      {:else if exchange.problem}
        <span class="status"><span class="orb"></span>{exchange.problem}</span>
      {:else}
        <span class="status">
          <span class="icon">
            {#if exchange.step}
              {#key exchange.step.image ?? exchange.step.icon}
                <span class="picture" in:scale={{ start: 0.5, duration: 280 }}>
                  {#if exchange.step.image}
                    <img src={exchange.step.image} alt="" />
                  {:else}
                    <svg viewBox="0 0 16 16" aria-hidden="true">{@html GLYPHS[exchange.step.icon] ?? GLYPHS.work}</svg>
                  {/if}
                </span>
              {/key}
            {:else}
              <span class="spinner"></span>
            {/if}
          </span>
          <span class="label" class:shimmer={!exchange.step}>{exchange.step?.label ?? 'Thinking…'}</span>
        </span>
      {/if}
    </div>
  {/if}
  {#if !exchange.sent && exchange.pictures.length}
    <div class="bubble natro shots" data-hit>
      <Shots ids={exchange.pictures} {pictures} open={openPicture} />
    </div>
  {/if}
{/each}
{#each queued as item (item.id)}
  <div class="bubble natro question waiting queued" data-hit>
    <p><span class="orb"></span><span>While the PC was off, you asked on your {item.from} at {time(item.time)}:</span></p>
    <span class="kept">{item.text}</span>
    <div class="choices">
      <button class="yes" onclick={() => runQueued(item, true)}>Run</button>
      <button class="no" onclick={() => runQueued(item, false)}>Skip</button>
    </div>
  </div>
{/each}

<style>
  .bubble {
    position: relative;
    flex: none;
    box-sizing: border-box;
    max-width: 78%;
    padding: 9px 14px;
    border-radius: 18px;
    font-size: 14px;
    line-height: 1.45;
    overflow-wrap: anywhere;
    user-select: text;
    cursor: auto;
    animation: pop 420ms var(--spring) both;
  }
  /* The owner's: on the right, in Natro's green. */
  .you {
    align-self: flex-end;
    border-bottom-right-radius: 6px;
    background: var(--green);
    color: #fff;
    box-shadow: 0 4px 16px color-mix(in srgb, var(--green-glow) 22%, transparent);
    transform-origin: bottom right;
    white-space: pre-wrap;
  }
  /* The phone's requests: the same green, a shade quieter, with where they were asked above. */
  .you.elsewhere {
    background: linear-gradient(135deg, #2f6b2d 0%, #3d8f3e 100%);
    box-shadow: none;
  }
  .from {
    align-self: flex-end;
    margin: 4px 6px -4px;
    font-size: 11px;
    font-weight: 500;
    color: var(--text-dim);
    user-select: none;
  }
  /* A reminder is her words alone: the label sits on her side. */
  .from.reminder {
    align-self: flex-start;
  }
  /* A background job's label holds its task: one line, the whole of it on hover. */
  .from.job {
    max-width: 70%;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  /* Pictures: screenshots and ones he sent (Shots.svelte). */
  .shots {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 6px;
    background: var(--glass-dense);
  }
  .you.shots {
    background: var(--glass-dense);
    border: 1px solid var(--border);
  }
  .queued .kept {
    display: block;
    margin: 6px 0 0 16px;
    font-weight: 600;
  }
  .said {
    width: 12px;
    height: 12px;
    margin: 0 6px -1px 0;
    fill: currentColor;
    opacity: 0.8;
  }
  .said :global(.line) {
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
  }
  /* Hers: on the left, in glass. */
  .natro {
    align-self: flex-start;
    border: 1px solid var(--border);
    border-bottom-left-radius: 6px;
    background: var(--glass-dense);
    box-shadow:
      inset 0 1px 0 0 var(--highlight),
      0 4px 16px rgba(0, 0, 0, 0.1);
    color: var(--text);
    transform-origin: bottom left;
  }

  /* Waiting for what was said: three dots. */
  .dots {
    display: inline-flex;
    gap: 4px;
    padding: 4px 0;
  }
  .dots i {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
    animation: bounce 1.2s ease-in-out infinite;
  }
  .dots i:nth-child(2) {
    animation-delay: 150ms;
  }
  .dots i:nth-child(3) {
    animation-delay: 300ms;
  }

  /* Her reply: the reply box's text (Reply.svelte). */
  .text :global(p) {
    margin: 0 0 8px;
  }
  .text :global(p.heading) {
    font-weight: 600;
    margin-top: 4px;
  }
  .text :global(ul),
  .text :global(ol) {
    margin: 0 0 8px;
    padding-left: 20px;
  }
  .text :global(li) {
    margin: 2px 0;
  }
  .text :global(li::marker) {
    color: var(--text-dim);
  }
  .text :global(> :last-child) {
    margin-bottom: 0;
  }
  .text :global(strong) {
    font-weight: 600;
  }
  .text :global(code) {
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: 12.5px;
    padding: 1px 5px;
    border-radius: 6px;
    background: var(--decline);
  }
  .text :global(pre) {
    margin: 0 0 8px;
    padding: 8px 10px;
    border-radius: 10px;
    background: var(--decline);
    overflow-x: auto;
  }
  .text :global(pre code) {
    padding: 0;
    background: none;
  }
  .text :global(a) {
    color: var(--link);
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
  }
  .text :global(.caret) {
    display: inline-block;
    width: 2px;
    height: 1.05em;
    margin-left: 2px;
    vertical-align: text-bottom;
    border-radius: 1px;
    background: var(--green-glow);
    animation: blink 1s steps(2, start) infinite;
  }

  /* What she's doing: the dock's spinner or the step's picture, and its words. */
  .status {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 500;
    user-select: none;
  }
  .icon {
    position: relative;
    flex: none;
    width: 16px;
    height: 16px;
  }
  .spinner {
    position: absolute;
    inset: 1px;
    border-radius: 50%;
    background: conic-gradient(from 0deg, transparent 10%, var(--text));
    mask: radial-gradient(farthest-side, transparent calc(100% - 2px), #000 calc(100% - 1.5px));
    animation: spin 0.8s linear infinite;
  }
  .picture {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
  }
  .picture img {
    width: 16px;
    height: 16px;
    border-radius: 3px;
  }
  .picture svg {
    width: 16px;
    height: 16px;
    fill: none;
    stroke: var(--text);
    stroke-width: 1.4;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .shimmer {
    background: linear-gradient(90deg, var(--text) 30%, var(--text-dim) 50%, var(--text) 70%) 0 0 / 300% 100%;
    background-clip: text;
    color: transparent;
    animation: shimmer 1.5s linear infinite;
  }

  /* The dock's dot: amber while she asks, red when something went wrong. */
  .orb {
    position: relative;
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--amber);
  }
  .problem .orb {
    background: var(--red);
  }
  .question.waiting .orb::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: #f5a623;
    animation: pulse 1.7s cubic-bezier(0.2, 0.6, 0.35, 1) infinite;
  }
  .question p {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin: 0;
  }
  .question .orb {
    transform: translateY(-1px);
  }
  .choices {
    display: flex;
    gap: 6px;
    margin: 9px 0 1px 16px;
  }
  .choices button {
    height: 28px;
    padding: 0 16px;
    border: none;
    border-radius: 14px;
    font: inherit;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition:
      background 150ms ease,
      color 150ms ease,
      filter 150ms ease,
      scale 120ms ease;
  }
  .choices button:active {
    scale: 0.95;
  }
  .yes {
    background: var(--green);
    color: #fff;
  }
  .yes:hover {
    filter: brightness(1.1);
  }
  .no {
    background: var(--decline);
    color: var(--text);
  }
  .no:hover {
    background: #ff453a;
    color: #fff;
  }
  .answer {
    display: block;
    margin: 4px 0 0 16px;
    font-size: 12px;
    color: var(--text-dim);
  }

  button:focus-visible {
    outline: 2px solid var(--green-glow);
    outline-offset: 1px;
  }

  @keyframes pop {
    from {
      opacity: 0;
      transform: translateY(10px) scale(0.92);
    }
  }
  @keyframes bounce {
    0%,
    60%,
    100% {
      transform: none;
      opacity: 0.6;
    }
    30% {
      transform: translateY(-3px);
      opacity: 1;
    }
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
  @keyframes spin {
    to {
      rotate: 1turn;
    }
  }
  @keyframes shimmer {
    from {
      background-position: 100% 0;
    }
    to {
      background-position: 0 0;
    }
  }
  @keyframes blink {
    to {
      visibility: hidden;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .bubble,
    .dots i,
    .question.waiting .orb::before,
    .shimmer,
    .text :global(.caret) {
      animation: none;
    }
  }
</style>
