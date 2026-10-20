// Natro's reply as HTML for the reply box. She's asked to write plain spoken sentences
// (agent/identity/NATRO.md), so mostly this keeps paragraphs and line breaks; when she
// does use Markdown it shows lists, **bold**, *italics*, `code`, headings and links.
// Everything is escaped first: nothing in a reply becomes HTML of its own.

const ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

function escape(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ESCAPES[c])
}

/** Bold, italics, code and links in one line (already split from the others). */
function inline(text: string): string {
  // Code first, so what's inside it stays as written.
  return escape(text)
    .split(/(`[^`]+`)/)
    .map((part) => {
      if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) return `<code>${part.slice(1, -1)}</code>`
      return part
        // Web links; natro:open/3 or natro:show/3 for a file that came from the phone (files.rs), a reminder's
        // natro:snooze/<its id>, natro:answer/phone/<its id>/yes or /no to a question on the phone, and
        // natro:reply/<its id> to a message that came to the phone (reply.rs).
        .replace(
          /\[([^\]]+)\]\((https?:\/\/[^\s)]+|natro:(?:(?:open|show)\/\d+|snooze\/[\w-]+|reply\/[\w-]+|answer\/[\w-]+\/[\w-]+\/(?:yes|no)))\)/g,
          '<a href="$2">$1</a>',
        )
        .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,!?;:])/g, '$1<a href="$2">$2</a>')
        .replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, (_, a, b) => `<strong>${a ?? b}</strong>`)
        .replace(/(^|[^*\w])\*([^*\s][^*]*)\*(?!\*)/g, '$1<em>$2</em>')
    })
    .join('')
}

const BULLET = /^\s*[-*•]\s+(.*)$/
const NUMBERED = /^\s*(\d+)[.)]\s+(.*)$/
const HEADING = /^\s*#{1,6}\s+(.*)$/

/** The reply as HTML; with `caret`, a blinking caret after its last word (she's still writing). */
export function format(text: string, caret = false): string {
  const lines = text.replace(/\r\n?/g, '\n').trim().split('\n')
  const blocks: string[] = []
  let paragraph: string[] = []
  // (Typed with `as`: TypeScript doesn't see the helpers below change them.)
  let list = null as { tag: 'ul' | 'ol'; items: string[]; start: number } | null
  let code = null as string[] | null

  const endParagraph = () => {
    if (paragraph.length) blocks.push(`<p>${paragraph.map(inline).join('<br>')}</p>`)
    paragraph = []
  }
  const endList = () => {
    if (list) {
      const start = list.tag === 'ol' && list.start !== 1 ? ` start="${list.start}"` : ''
      blocks.push(`<${list.tag}${start}>${list.items.map((item) => `<li>${inline(item)}</li>`).join('')}</${list.tag}>`)
    }
    list = null
  }

  for (const line of lines) {
    if (code) {
      if (line.trim().startsWith('```')) {
        blocks.push(`<pre><code>${escape(code.join('\n'))}</code></pre>`)
        code = null
      } else {
        code.push(line)
      }
      continue
    }
    if (line.trim().startsWith('```')) {
      endParagraph()
      endList()
      code = []
      continue
    }
    const bullet = line.match(BULLET)
    const numbered = line.match(NUMBERED)
    const heading = line.match(HEADING)
    if (bullet || numbered) {
      endParagraph()
      const tag = bullet ? 'ul' : 'ol'
      if (list?.tag !== tag) {
        endList()
        list = { tag, items: [], start: numbered ? Number(numbered[1]) : 1 }
      }
      list!.items.push(bullet ? bullet[1] : numbered![2])
    } else if (heading) {
      endParagraph()
      endList()
      blocks.push(`<p class="heading">${inline(heading[1])}</p>`)
    } else if (!line.trim()) {
      endParagraph()
      endList()
    } else if (list && /^\s{2,}\S/.test(line)) {
      list.items[list.items.length - 1] += ' ' + line.trim() // a list item going on on the next line
    } else {
      endList()
      paragraph.push(line.trim())
    }
  }
  if (code) blocks.push(`<pre><code>${escape((code as string[]).join('\n'))}</code></pre>`)
  endParagraph()
  endList()

  let html = blocks.join('')
  if (caret) {
    const mark = '<span class="caret"></span>'
    // Inside the last paragraph or item, so it sits after the last word.
    html = /<\/(p|li)>(<\/[ou]l>)?$/.test(html)
      ? html.replace(/<\/(p|li)>((<\/[ou]l>)?)$/, `${mark}</$1>$2`)
      : html + mark
  }
  return html
}

/** A short reply (one line) fades by itself; anything longer stays until it's closed. */
export function isShort(text: string): boolean {
  const flat = text.trim()
  return flat.length <= 140 && !flat.includes('\n')
}

/** How long a short reply stays: time to read it, between 4 and 12 seconds. */
export function readingMillis(text: string): number {
  return Math.min(12_000, 4_000 + 60 * text.trim().length)
}
