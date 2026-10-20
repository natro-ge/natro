// A modifier tapped twice quickly, alone, as double_tap.rs sees it from other apps. While one
// of Natro's windows has the keyboard, Windows doesn't show its keys to that hook, so the page
// watches them: the chat's closes it, and Settings' chooses the modifier.

// A press shorter than this (ms) is a tap; the second press comes within BETWEEN of the first
// release. Keep in step with double_tap.rs.
const TAP = 300
const BETWEEN = 400

// As settings.json says them, by KeyboardEvent.key. AltGr (AltGraph) isn't one: it's another key.
const MODIFIERS: Record<string, string> = { Control: 'control', Alt: 'alt', Shift: 'shift' }

export class DoubleTap {
  // The modifier down alone, and since when; the last tap, and when it was let go.
  private down: { key: string; at: number } | null = null
  private tapped: { key: string; at: number } | null = null

  /** A key pressed. */
  press(event: KeyboardEvent) {
    const key = MODIFIERS[event.key]
    if (key && this.down?.key === key) return // it repeats while held
    if (key && !this.down) this.down = { key, at: event.timeStamp }
    else this.interrupt() // another key with it, or alone
  }

  /** A key let go: the modifier ("alt"), if that was its second tap. */
  release(event: KeyboardEvent): string | null {
    const key = MODIFIERS[event.key]
    const down = this.down
    if (!down || key !== down.key) return null // one pressed before it
    this.down = null
    const first = this.tapped
    this.tapped = null
    if (event.timeStamp - down.at >= TAP) return null
    if (first?.key === key && down.at - first.at <= BETWEEN) return key
    this.tapped = { key, at: event.timeStamp }
    return null
  }

  /** A click, or the window lost the keyboard: no tap until the next press. */
  interrupt() {
    this.down = null
    this.tapped = null
  }
}
