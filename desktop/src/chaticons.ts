// The chat's icons, for ChatBar.svelte, its parts and the bubbles: solid, on a 20 × 20 grid in the text's color.
export const ICONS = {
  more: '<path class="line thick" d="M10 4.5v11M4.5 10h11"/>',
  newChat:
    '<path fill-rule="evenodd" d="M10 2.5c-4.7 0-8.5 3.1-8.5 7 0 2.1 1.1 4 2.9 5.3-.2 1.1-.8 2.1-1.6 2.8 1.8 0 3.4-.6 4.6-1.5.8.2 1.7.3 2.6.3 4.7 0 8.5-3.1 8.5-7s-3.8-6.9-8.5-6.9zm-.8 3.7h1.6v2.5h2.5v1.6h-2.5v2.5H9.2v-2.5H6.7V8.7h2.5z"/>',
  file: '<path class="line" d="M5 2.5h6.5L15.5 6.5V17a.5.5 0 0 1-.5.5H5a.5.5 0 0 1-.5-.5V3a.5.5 0 0 1 .5-.5zM11.5 2.5v4h4"/>',
  attach:
    '<path class="line" d="M13.5 6.5 8 12a1.8 1.8 0 0 0 2.5 2.5l6-6a3.5 3.5 0 0 0-5-5l-6.2 6.2a5.2 5.2 0 0 0 7.4 7.4L17 12.8"/>',
  picture:
    '<path fill-rule="evenodd" d="M4.5 3h11A2.5 2.5 0 0 1 18 5.5v9a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 2 14.5v-9A2.5 2.5 0 0 1 4.5 3zm9 4.2a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM3.8 14.6l3.6-4.4a1 1 0 0 1 1.5 0l2.2 2.5 1.3-1.4a1 1 0 0 1 1.5 0l2.3 2.6v.6c0 .6-.4 1-1 1H4.8a1 1 0 0 1-1-.9z"/>',
  settings:
    '<path fill-rule="evenodd" d="M8.3 1.5h3.4l.5 2.4c.6.2 1.1.5 1.6.9l2.3-.8 1.7 2.9-1.8 1.6c.1.6.1 1.2 0 1.8l1.8 1.6-1.7 2.9-2.3-.8c-.5.4-1 .7-1.6.9l-.5 2.4H8.3l-.5-2.4c-.6-.2-1.1-.5-1.6-.9l-2.3.8-1.7-2.9 1.8-1.6a6 6 0 0 1 0-1.8L2.2 6.9l1.7-2.9 2.3.8c.5-.4 1-.7 1.6-.9zM10 12.8a2.8 2.8 0 1 0 0-5.6 2.8 2.8 0 0 0 0 5.6z"/>',
  mic: '<rect x="7" y="1.5" width="6" height="10.5" rx="3"/><path class="line" d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v3"/>',
  stop: '<rect x="5" y="5" width="10" height="10" rx="2.6"/>',
  send: '<path class="line thick" d="M10 15.5V5M5.6 9.2 10 4.8l4.4 4.4"/>',
  pc: '<rect class="line" x="2.5" y="3.5" width="15" height="10" rx="2"/><path class="line" d="M7 16.8h6M10 13.5v3.3"/>',
  phone: '<rect class="line" x="5.5" y="2" width="9" height="16" rx="2.4"/><path class="line" d="M8.8 15h2.4"/>',
}
